import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scanner
from .api import router
from .authproxy import RemoteUserAuthMiddleware
from .config import WEB_DIST, ensure_dirs
from .db import init_db


log = logging.getLogger("fermata.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_dirs()
        init_db()
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
        # cannot write to, a database from a newer release. Anything else is a
        # bug and should arrive as the traceback it is.
        log.error("%s", exc)
        raise
    scanner.start_scan()
    yield


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
