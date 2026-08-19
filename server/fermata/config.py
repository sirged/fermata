import os
from pathlib import Path

LIBRARY_DIR = Path(os.environ.get("FERMATA_LIBRARY", "./library")).resolve()
CONFIG_DIR = Path(os.environ.get("FERMATA_CONFIG", "./config")).resolve()
WEB_DIST = os.environ.get("FERMATA_WEB_DIST", "")

CACHE_DIR = CONFIG_DIR / "cache"
DB_PATH = CONFIG_DIR / "fermata.db"

# File extensions the scanner picks up, mapped to a broad type used by the UI
# to pick a viewer.
FILE_TYPES = {
    ".pdf": "pdf",
    ".musicxml": "musicxml",
    ".mxl": "musicxml",
    ".gp": "gp",
    ".gp3": "gp",
    ".gp4": "gp",
    ".gp5": "gp",
    ".gpx": "gp",
}


def ensure_dirs() -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
