from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scanner
from .api import router
from .config import WEB_DIST, ensure_dirs
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    scanner.start_scan()
    yield


app = FastAPI(title="Fermata", lifespan=lifespan)
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
