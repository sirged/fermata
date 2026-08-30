"""Reverse-proxy authentication (issue #16).

Fermata has no accounts, no login screen, and no plan to grow either - see
docs/deployment.md's "Current limitations" section. What self-hosters
actually run instead is an authenticating reverse proxy (Caddy with
forward_auth, Authelia, authentik, ...) in front of the app, and the
standard way such a proxy hands off "who is this" to the app behind it is a
plain HTTP header - `X-Remote-User` throughout this codebase and its docs;
see docs/deployment.md's "Reverse proxy authentication" section for why the
header name has to be exactly one name, everywhere, rather than "whichever
of a couple of conventional names you like".

Trusting that header is exactly one footgun away from a spoofing hole: any
client that can reach Fermata directly (the LAN, a misconfigured proxy pass,
a port left open) can set that same header itself and walk in as anyone. So
this module trusts the header only when THREE things are all true: reverse
proxy auth was actually turned on (`config.AUTH_HEADER` is non-empty - see
config.py), the request's own TCP peer address is inside
`config.AUTH_TRUSTED_NETWORKS`, the operator-configured allowlist of proxy
addresses, and the header appears exactly once (see `dispatch`'s duplicate
check - a proxy that APPENDS rather than replaces the header can leave a
client's own forged copy sitting in front of the proxy's real one). A
request that fails any of those has the header stripped of its authority
entirely - it is simply never read.

READ THIS BEFORE TRUSTING "the request's own TCP peer" AS UNSPOOFABLE - IT
ISN'T, UNCONDITIONALLY. `request.client` is populated by uvicorn from the
real accepted socket UNLESS uvicorn's own `ProxyHeadersMiddleware` is
active, which it is BY DEFAULT (`--proxy-headers`, the default, with
`--forwarded-allow-ips` defaulting to `127.0.0.1`). That middleware runs
OUTSIDE this ASGI app, before a single line of Fermata's own code sees the
request, and REWRITES `scope["client"]` from a client-supplied
`X-Forwarded-For` header whenever the connection's real peer is itself
inside `forwarded_allow_ips` - which, for anything bound to loopback or
reachable through Docker's default networking, is very often true for every
request. Concretely: a request straight from an attacker, carrying both a
forged `X-Remote-User` AND a forged `X-Forwarded-For` naming an address on
Fermata's OWN trusted-proxy list, gets `request.client` REWRITTEN to that
forged address before `is_trusted_proxy` ever runs - full authentication
bypass, no matter how correct the logic below is. This is not
hypothetical - it was found in review and reproduced against a real
`uvicorn` process before being closed.

The fix is `--no-proxy-headers` on uvicorn's own command line - present in
the Dockerfile's `CMD` and README's dev-run command, both changed by the
same commit that added this warning - which disables `ProxyHeadersMiddleware`
entirely, so `scope["client"]` is always the real accepted socket peer and
never something a header can rewrite. `check_proxy_header_safety` below is
the belt-and-suspenders backstop: Fermata cannot control how an operator
actually launches uvicorn, so it refuses to START with reverse-proxy
authentication turned on unless it can confirm that flag is present, rather
than silently serving requests uvicorn might be rewriting out from under it.

This is middleware, not a dependency on individual routes, on purpose: it
has to cover the SPA's static files and the API's docs/openapi.json exactly
the same as every /api/* route, and none of those go through api.py's
router. See RemoteUserAuthMiddleware.dispatch for exactly what is and is not
exempt, and docs/deployment.md for the worked Caddy/Authelia examples.
"""

import logging
import os
import sys
from ipaddress import IPv6Address, ip_address, ip_network

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
#
# CAVEAT, noted rather than fixed: this is compared against
# `request.url.path` as a literal string, which does not account for
# uvicorn's `--root-path` (serving the app under a sub-path prefix another
# layer adds). Nothing in this project's documented deployment story uses
# `--root-path`, so this stays a plain string match - but under one, this
# exemption would stop matching and GET /api/health would require the header
# like everything else. That is a fail-CLOSED surprise (health checks start
# failing, loudly, rather than auth silently not applying) rather than a
# security hole, which is why it is a caveat here and not a blocker.
EXEMPT_PATHS = {"/api/health"}


def _normalize_peer(addr):
    """An IPv4-mapped IPv6 address (`::ffff:203.0.113.9`) - what a dual-stack
    listener can hand back for what is, on the wire, an ordinary IPv4
    connection - parses as a distinct `IPv6Address` that will never test as
    `in` an IPv4 network someone wrote into FERMATA_TRUSTED_PROXIES as
    `203.0.113.9/32`. That is fail-CLOSED (the request is refused, never
    wrongly trusted), but it is a silent lockout of a correctly-configured
    proxy for a reason nothing in this module's own logging would explain -
    so the embedded IPv4 address is unwrapped before the trust check runs,
    the same address either form was always naming."""
    if isinstance(addr, IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def is_trusted_proxy(peer_ip: str | None) -> bool:
    """Whether `peer_ip` is inside one of config.AUTH_TRUSTED_NETWORKS.

    `peer_ip` is trustworthy input to this function ONLY when uvicorn's own
    X-Forwarded-For rewriting is confirmed off - see this module's docstring
    and check_proxy_header_safety. This function itself has no way to know
    whether that precondition holds; it is pure address arithmetic over
    whatever `peer_ip` the caller (RemoteUserAuthMiddleware.dispatch, reading
    `request.client.host`) hands it.

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
    addr = _normalize_peer(addr)
    return any(addr in network for network in config.AUTH_TRUSTED_NETWORKS)


# The "all addresses" networks - IPv4 and IPv6's equivalent of "trust
# everyone". A FERMATA_TRUSTED_PROXIES containing either is almost always a
# typo (0.0.0.0/0 instead of a real subnet, or a copy-pasted example) rather
# than an intentional choice, and it silently defeats the entire feature -
# every direct request is "from a trusted proxy" - so check_auth_configuration
# _sanity warns loudly about it rather than accepting it quietly. Warns
# rather than refuses to start: unlike the proxy-headers case, there is no
# way to be SURE this is a mistake (an operator could, however unusually,
# mean it), so this is not put in the same fail-shut bucket as
# check_proxy_header_safety.
_TRUST_EVERYONE_NETWORKS = {ip_network("0.0.0.0/0"), ip_network("::/0")}


def check_auth_configuration_sanity() -> None:
    """Two ways to configure this feature into looking secure while actually
    doing nothing (or the opposite) - both silent otherwise, both found in
    review. Called once from main.py's lifespan after config has loaded;
    never raises - these are warnings, not startup failures, because unlike
    check_proxy_header_safety neither is an active bypass by itself."""
    if config.AUTH_TRUSTED_NETWORKS and not config.AUTH_HEADER:
        log.error(
            "FERMATA_TRUSTED_PROXIES is set (%r) but FERMATA_AUTH_HEADER is not (or is "
            "empty) - reverse-proxy authentication is OFF. Every request is served "
            "unauthenticated regardless of the trusted-proxy list. This is almost always "
            "a typo in FERMATA_AUTH_HEADER's name rather than an intentional choice - see "
            "docs/deployment.md's 'Reverse proxy authentication' section.",
            config.AUTH_TRUSTED_PROXIES_RAW,
        )
    if config.AUTH_HEADER and any(
        net in _TRUST_EVERYONE_NETWORKS for net in config.AUTH_TRUSTED_NETWORKS
    ):
        log.error(
            "FERMATA_TRUSTED_PROXIES (%r) includes 0.0.0.0/0 or ::/0 - this trusts EVERY "
            "direct request's %s header, from anywhere, which defeats the entire point of "
            "reverse-proxy authentication (the spoofing hole issue #16 exists to close). "
            "Name your actual proxy's address or subnet instead - see "
            "docs/deployment.md's 'Reverse proxy authentication' section.",
            config.AUTH_TRUSTED_PROXIES_RAW, config.AUTH_HEADER,
        )


def _proxy_headers_confirmed_off() -> bool:
    """Best-effort confirmation that THIS process was launched with
    uvicorn's `--no-proxy-headers` - the one thing that actually stops
    `scope["client"]` from being rewritten from X-Forwarded-For before this
    middleware ever runs (see the module docstring).

    Reliable exactly when uvicorn IS the process this interpreter is running
    as - true for the Dockerfile's CMD and the plain `uvicorn
    fermata.main:app ...` / `python -m uvicorn fermata.main:app ...` this
    project documents, in both of which `sys.argv` is uvicorn's own command
    line (with `-m uvicorn` invocation, `-m` and the module name are
    consumed by the interpreter and never appear in `sys.argv` themselves,
    so this check is the same either way). NOT reliable if Fermata is ever
    run under a different process manager (gunicorn workers, a WSGI-to-ASGI
    shim, uvicorn.Server() embedded in a larger program) that does not carry
    uvicorn's own flags in this process's argv - hence "confirmed off" and
    not "is off": absence of the flag here does not prove proxy headers are
    on, only that this check cannot prove they are off, which is exactly why
    check_proxy_header_safety treats that as unsafe."""
    return "--no-proxy-headers" in sys.argv


def check_proxy_header_safety() -> None:
    """Refuses to start (raises RuntimeError, caught by main.py's lifespan
    the same way ensure_dirs's library-folder check is) when reverse-proxy
    authentication is turned on and this process cannot confirm uvicorn will
    leave `request.client` alone - see the module docstring for exactly what
    goes wrong if it doesn't. A no-op when FERMATA_AUTH_HEADER is unset: this
    whole class of risk is specific to trusting a peer address for
    authentication, which off means Fermata never does."""
    if not config.AUTH_HEADER:
        return
    problems = []
    if not _proxy_headers_confirmed_off():
        problems.append(
            "uvicorn's own X-Forwarded-For trust could not be confirmed off (no "
            "--no-proxy-headers seen on this process's own command line, which was: "
            f"{sys.argv!r}). Without it, uvicorn rewrites the request's peer address from "
            "a client-supplied X-Forwarded-For header BEFORE Fermata's own trusted-proxy "
            "check ever runs - which lets anyone who can reach Fermata at all impersonate "
            "your reverse proxy and forge the identity header this feature exists to "
            "protect, no matter how FERMATA_TRUSTED_PROXIES is set."
        )
    forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS")
    if forwarded_allow_ips:
        problems.append(
            f"the FORWARDED_ALLOW_IPS environment variable is set (to {forwarded_allow_ips!r}) "
            "- this is the exact setting that widens which peer addresses uvicorn will "
            "rewrite scope[\"client\"] for from X-Forwarded-For, and it has no safe value "
            "while reverse-proxy authentication is on."
        )
    if problems:
        raise RuntimeError(
            "Fermata cannot start: FERMATA_AUTH_HEADER is set, turning on reverse-proxy "
            "authentication, but this process's own launch configuration makes that "
            "authentication forgeable.\n\n"
            + "\n\n".join(problems)
            + "\n\nStart uvicorn with --no-proxy-headers (the Dockerfile's CMD and README's "
            "own run command both already do this) and do not set FORWARDED_ALLOW_IPS. See "
            "docs/deployment.md's 'Reverse proxy authentication' section for why."
        )


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

        # More than one occurrence of the configured header is refused
        # outright, never merged or picked-from. A proxy that correctly
        # REPLACES the header on every request (the only supported
        # configuration - see docs/deployment.md) never produces two: its own
        # HTTP client sets the header once. Two occurrences means either a
        # proxy misconfigured to APPEND rather than replace (leaving a
        # client-supplied copy sitting alongside its own real one - and
        # Starlette's Headers.get() returns the FIRST occurrence, which
        # would be the client's forged one if the client's copy arrives
        # first in the request, exactly the shape a forgery attempt would
        # take) or a client trying that forgery directly against a proxy
        # that does replace but was reached with two headers anyway. Neither
        # is a value this code should ever guess between.
        values = request.headers.getlist(config.AUTH_HEADER)
        if len(values) > 1:
            log.warning(
                "rejected %s %s: %s header sent more than once (%d occurrences) - your "
                "proxy must REPLACE this header, never append to it",
                request.method, request.url.path, config.AUTH_HEADER, len(values),
            )
            return _unauthorized(
                f"the {config.AUTH_HEADER} header was sent more than once - your reverse "
                "proxy must replace it, not append to it"
            )

        username = (values[0] if values else "").strip()
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
