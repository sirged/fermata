"""Reverse-proxy authentication (issue #16).

Fermata has no accounts, no login screen, and no plan to grow either - see
docs/deployment.md's "Current limitations" section. What self-hosters
actually run instead is an authenticating reverse proxy (Caddy with
forward_auth, Authelia, authentik, ...) in front of the app, and the
standard way such a proxy hands off "who is this" to the app behind it is a
plain HTTP header - conventionally `Remote-User` or `X-Remote-User` - set to
the logged-in username.

Trusting that header is exactly one footgun away from a spoofing hole: any
client that can reach Fermata directly (the LAN, a misconfigured proxy pass,
a port left open) can set that same header itself and walk in as anyone. So
this module trusts the header only when TWO things are both true: reverse
proxy auth was actually turned on (`config.AUTH_HEADER` is non-empty - see
config.py), AND the request's own TCP peer address is inside
`config.AUTH_TRUSTED_NETWORKS`, the operator-configured allowlist of proxy
addresses. A request from anywhere else has the header stripped of its
authority entirely - it is simply never read - so a request an operator
never told Fermata to trust cannot authenticate no matter what headers it
sends.

This is middleware, not a dependency on individual routes, on purpose: it
has to cover the SPA's static files and the API's docs/openapi.json exactly
the same as every /api/* route, and none of those go through api.py's
router. See RemoteUserAuthMiddleware.dispatch for exactly what is and is not
exempt, and docs/deployment.md for the worked Caddy/Authelia examples.
"""

import logging
from ipaddress import ip_address

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config

log = logging.getLogger("fermata.authproxy")

# The one path exempt from auth even when it is switched on. Docker's own
# HEALTHCHECK (see the Dockerfile) polls this from inside the container with
# no headers at all; requiring auth here would flip a correctly-running,
# correctly-configured container to "unhealthy" and into Compose's restart
# loop the moment reverse-proxy auth was turned on. Nothing this reports
# ("the process is up and answering requests at all" - see api.health's
# docstring) is sensitive enough to be worth that outage.
#
# GET /docs, /openapi.json and /redoc are deliberately NOT exempt: they are
# part of "every route" the way this feature is documented to cover, and the
# alternative - special-casing them open - would mean the one config knob
# that is supposed to lock the app down leaves its own API surface
# published. See docs/deployment.md's "Reverse proxy authentication"
# section, which states this explicitly for anyone who only reads the docs.
EXEMPT_PATHS = {"/api/health"}


def is_trusted_proxy(peer_ip: str | None) -> bool:
    """Whether `peer_ip` - the request's actual TCP peer, never a header a
    client could forge - is inside one of config.AUTH_TRUSTED_NETWORKS.

    An empty AUTH_TRUSTED_NETWORKS (the default, even with AUTH_HEADER set)
    trusts nothing: this always returns False, which is what turns "header
    name configured but no proxy list" into every request being refused
    rather than every request being trusted. Fail closed, not fail open."""
    if not peer_ip:
        return False
    try:
        addr = ip_address(peer_ip)
    except ValueError:
        return False
    return any(addr in network for network in config.AUTH_TRUSTED_NETWORKS)


def _unauthorized(reason: str) -> JSONResponse:
    # Same {"detail": ...} shape FastAPI's own HTTPException produces (see
    # api.py's `raise HTTPException(404, "score not found")` and its kin) -
    # a client or the SPA's own error handling sees one consistent error
    # shape everywhere, not a bespoke one for auth alone.
    return JSONResponse({"detail": reason}, status_code=401)


class RemoteUserAuthMiddleware(BaseHTTPMiddleware):
    """Wraps the whole app - see the module docstring for why middleware
    rather than a route dependency. Added to `app` in main.py."""

    async def dispatch(self, request: Request, call_next):
        # The off switch. AUTH_HEADER empty is the out-of-the-box state and
        # every existing deployment's state after an upgrade that never
        # touched its environment - this branch has to be a complete no-op,
        # not merely "usually harmless", for that promise to hold.
        if not config.AUTH_HEADER:
            request.state.fermata_username = None
            return await call_next(request)

        if request.url.path in EXEMPT_PATHS:
            request.state.fermata_username = None
            return await call_next(request)

        peer = request.client.host if request.client else None
        if not is_trusted_proxy(peer):
            log.warning(
                "rejected %s %s: request did not come from a trusted proxy (peer=%s) - "
                "see FERMATA_TRUSTED_PROXIES in docs/deployment.md",
                request.method, request.url.path, peer,
            )
            return _unauthorized(
                "this request did not come from a trusted reverse proxy - see "
                "docs/deployment.md's 'Reverse proxy authentication' section"
            )

        username = request.headers.get(config.AUTH_HEADER, "").strip()
        if not username:
            log.warning(
                "rejected %s %s: trusted proxy %s sent no %s header",
                request.method, request.url.path, peer, config.AUTH_HEADER,
            )
            return _unauthorized(
                f"missing the {config.AUTH_HEADER} header - your reverse proxy must set it "
                "on every request when reverse-proxy authentication is turned on"
            )

        request.state.fermata_username = username
        log.info(
            "authenticated %s %s as %r via proxy %s", request.method, request.url.path,
            username, peer,
        )
        return await call_next(request)
