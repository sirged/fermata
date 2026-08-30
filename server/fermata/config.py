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
    """Create what is ours to create, and refuse to invent what is not.

    THE LIBRARY FOLDER IS NOT CREATED HERE, and that is the whole point of this
    function having a docstring. It used to be, with mkdir(exist_ok=True), and
    the consequence was the worst kind of failure this application can have.

    A library folder that is not there is almost never a first run - it is a
    bind mount that did not appear. The host path was renamed, an external
    drive did not come back, the container started before the mount was ready.
    Creating the folder in that situation does not recover anything; it
    manufactures an empty library, and the startup scan then reads that empty
    library as the truth and reconciles the database down to match it. That is
    how a drive failing to mount came to destroy practice history (#95).

    So the absence is reported as the configuration error it is, and nothing
    starts. That is a loud, obvious, harmless failure, and it is strictly
    better than a quiet destructive one: under `restart: unless-stopped` a
    container that refuses to start simply keeps trying, and recovers by itself
    the moment the mount appears - whereas one that started with an empty
    library has already done the damage by then.

    The config folder IS created, and the difference is not arbitrary. That
    folder is Fermata's own storage - nobody mounts anything into it that
    Fermata did not put there, an empty one is a genuine first run, and there
    is no data it could be shadowing. The library folder is the user's.

    THAT DISTINCTION SURVIVED FERMATA LEARNING TO WRITE TO THE LIBRARY (#56),
    and this paragraph exists because the sentence it replaces - "Fermata only
    ever reads it" - stopped being true. Fermata now moves, renames and deletes
    files in there when a person asks it to, which makes the reasoning above
    stronger rather than weaker: an invented empty library is now somewhere a
    reorganisation could be applied, not merely somewhere an index could be
    reconciled away. What is still true, and is the rule those operations are
    written to, is that Fermata never CREATES the library folder and never
    writes outside it.
    """
    if not LIBRARY_DIR.is_dir():
        what = "is a file, not a folder" if LIBRARY_DIR.exists() else "is not there"
        raise RuntimeError(
            f"Fermata cannot start: its library folder {LIBRARY_DIR} {what}.\n"
            "\n"
            "Fermata will not create this folder, on purpose. A library folder that is "
            "missing is usually a mount that did not appear - a renamed host folder, a "
            "drive that did not come back, a container that started before its volume was "
            "ready. Creating an empty one in that situation would look like a library with "
            "nothing in it, and your index would be reconciled down to match.\n"
            "\n"
            "Check that the folder exists and is readable by the user Fermata runs as. In "
            "Docker that is the volume mapped to /data/library; see docs/deployment.md. "
            "Running from source, FERMATA_LIBRARY names it.\n"
            "\n"
            "Nothing has been changed. Your sheet music and your practice history are both "
            "as they were."
        )
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
