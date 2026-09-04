import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import authproxy, config, scanner
from .api import router
from .authproxy import RemoteUserAuthMiddleware
from .config import WEB_DIST, ensure_dirs
from .db import init_db


log = logging.getLogger("fermata.startup")


def _start_mcp_server(app: FastAPI):
    """Start the Model Context Protocol server (issue #31), or don't.

    THE IMPORT IS INSIDE THE `if` ON PURPOSE, and it is the whole reason this
    is a function rather than three lines in the lifespan. With FERMATA_MCP
    unset, `fermata.mcp_server` - and through it the protocol library - is
    never imported, so a deployment that turned this feature off does not
    need the optional dependency installed at all, and `import fermata.main`
    keeps working without it. server/tests/test_mcp_server.py pins that in
    both directions.

    The tools are generated from `app.openapi()`: the same document the
    application serves at `/openapi.json`, read here at startup, so a tool
    schema cannot drift from the route it wraps. Note the direction - this
    reads the document, it does not add to it. Turning the flag on does not
    change a single line of the API's own contract.
    """
    if not config.MCP_ENABLED:
        return None
    from . import mcp_server

    return mcp_server.start(
        app.openapi(),
        host=config.MCP_HOST,
        port=config.MCP_PORT,
        api_base_url=config.MCP_API_URL,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp_listener = None
    try:
        ensure_dirs()
        # Parses FERMATA_TRUSTED_PROXIES - not done at import time (see
        # config.py's comment on AUTH_TRUSTED_NETWORKS) specifically so a bad
        # CIDR lands here, in the same friendly-message-before-traceback path
        # as every other startup misconfiguration, rather than crashing the
        # process before this try/except even exists to catch it.
        config.load_auth_trusted_networks()
        # Fatal: refuses to start rather than serve reverse-proxy auth that
        # uvicorn's own X-Forwarded-For handling can make forgeable - see
        # authproxy.py's module docstring for the exact mechanism. Must run
        # AFTER load_auth_trusted_networks (nothing here depends on it, but
        # both are startup-safety checks and belong together) and BEFORE
        # init_db, so a dangerous launch never even opens the database.
        authproxy.check_proxy_header_safety()
        # Also fatal, same bucket: FERMATA_TRUSTED_PROXIES naming 0.0.0.0/0
        # or ::/0 is never a real proxy's address, and with auth on it means
        # the running server authenticates any direct request as any claimed
        # username - strictly worse than auth being off, and invisible under
        # `docker compose up -d` (the container reports healthy while doing
        # it). See authproxy.check_trusted_proxies_are_not_everyone's own
        # docstring for why this is fatal and a genuinely broad-but-real
        # subnet is not.
        authproxy.check_trusted_proxies_are_not_everyone()
        # Parses FERMATA_MCP_PORT when, and only when, FERMATA_MCP is on -
        # in here, not at import time, for exactly the same reason
        # load_auth_trusted_networks is (see config.py's comment on
        # MCP_PORT).
        config.load_mcp_settings()
        init_db()
        mcp_listener = _start_mcp_server(app)
    except RuntimeError as exc:
        # Said plainly BEFORE the exception propagates, because of what happens
        # next in the real world. Uvicorn prints a traceback for anything raised
        # in here, and a traceback above the explanation means the first thing a
        # worried operator sees is a stack trace - at which point the obvious
        # move is to put the previous image tag back. That is the single action
        # that destroys their practice history (see db.SCHEMA_VERSION), so the
        # readable sentence has to come first and the traceback second.
        #
        # Only RuntimeError, which is the type this application raises for the
        # conditions it refuses to start under, each carrying a message written
        # for a person: a library folder that is not there, a config folder it
        # cannot write to, a database from a newer release, a trusted-proxies
        # entry that isn't a real IP or CIDR, reverse-proxy auth turned on
        # without confirming uvicorn won't undermine it, or a trusted-proxies
        # list that trusts literally everyone. Anything else is a bug and
        # should arrive as the traceback it is.
        log.error("%s", exc)
        raise
    # Non-fatal: warns (loudly, at error level) about the one remaining way
    # to configure reverse-proxy auth into silently doing nothing - see
    # authproxy.check_auth_configuration_sanity's own docstring. Never
    # raises, so it cannot block startup the way the checks above do; it
    # runs after them so a genuinely fatal misconfiguration is reported as
    # that, not buried under a warning about a different one.
    authproxy.check_auth_configuration_sanity()
    scanner.start_scan()
    try:
        yield
    finally:
        if mcp_listener is not None:
            mcp_listener.stop()


app = FastAPI(title="Fermata", lifespan=lifespan)
# Wraps every request - the SPA's static files and /docs/openapi.json
# included, not just api.router's routes below - see authproxy.py's module
# docstring for why this has to be middleware rather than a route
# dependency, and for what stays exempt even when it is turned on.
app.add_middleware(RemoteUserAuthMiddleware)
app.include_router(router)

if WEB_DIST and Path(WEB_DIST).is_dir():
    dist = Path(WEB_DIST)
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    @app.head("/{full_path:path}")
    def spa(full_path: str):
        candidate = dist / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        # Only routes fall through to the app shell. Serving index.html for a
        # missing asset hands HTML to whatever expected a font or soundfont,
        # which surfaces as a parse error instead of a plain 404.
        if Path(full_path).suffix:
            raise HTTPException(404, "not found")
        return FileResponse(dist / "index.html")
