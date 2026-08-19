import hashlib
import threading
import time

from .config import FILE_TYPES, LIBRARY_DIR
from .db import connect
from .metadata import parse_path
from .thumbs import generate_pdf_thumb, pdf_info

_state = {
    "scanning": False,
    "total": 0,
    "processed": 0,
    "added": 0,
    "updated": 0,
    "removed": 0,
    "started_at": None,
    "finished_at": None,
}
_state_lock = threading.Lock()


def scan_status() -> dict:
    with _state_lock:
        return dict(_state)


def _hash_file(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _scan() -> None:
    conn = connect()
    files = [
        p
        for p in LIBRARY_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in FILE_TYPES
    ]
    with _state_lock:
        _state.update(total=len(files), processed=0, added=0, updated=0, removed=0)

    seen_paths = set()
    for path in files:
        rel = path.relative_to(LIBRARY_DIR).as_posix()
        seen_paths.add(rel)
        stat = path.stat()
        row = conn.execute(
            "SELECT id, size, mtime FROM scores WHERE path = ?", (rel,)
        ).fetchone()
        if row and row["size"] == stat.st_size and row["mtime"] == stat.st_mtime:
            with _state_lock:
                _state["processed"] += 1
            continue

        file_type = FILE_TYPES[path.suffix.lower()]
        file_hash = _hash_file(path)
        pages, pdf_title, pdf_creator = (None, None, None)
        if file_type == "pdf":
            pages, pdf_title, pdf_creator = pdf_info(path)
            generate_pdf_thumb(path, file_hash)
        meta = parse_path(rel, pdf_title, pdf_creator)

        if row:
            conn.execute(
                """UPDATE scores SET hash=?, size=?, mtime=?, pages=? WHERE id=?""",
                (file_hash, stat.st_size, stat.st_mtime, pages, row["id"]),
            )
            with _state_lock:
                _state["updated"] += 1
        else:
            conn.execute(
                """INSERT INTO scores
                   (title, composer, collection, series, source, path,
                    file_type, content_kind, pages, hash, size, mtime)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    meta.title,
                    meta.composer,
                    meta.collection,
                    meta.series,
                    meta.source,
                    rel,
                    file_type,
                    meta.content_kind,
                    pages,
                    file_hash,
                    stat.st_size,
                    stat.st_mtime,
                ),
            )
            with _state_lock:
                _state["added"] += 1
        conn.commit()
        with _state_lock:
            _state["processed"] += 1

    # Drop rows whose files disappeared from the library.
    existing = conn.execute("SELECT id, path FROM scores").fetchall()
    for row in existing:
        if row["path"] not in seen_paths:
            conn.execute("DELETE FROM scores WHERE id = ?", (row["id"],))
            with _state_lock:
                _state["removed"] += 1
    conn.commit()


def start_scan() -> bool:
    with _state_lock:
        if _state["scanning"]:
            return False
        _state["scanning"] = True
        _state["started_at"] = time.time()
        _state["finished_at"] = None

    def run():
        try:
            _scan()
        finally:
            with _state_lock:
                _state["scanning"] = False
                _state["finished_at"] = time.time()

    threading.Thread(target=run, name="fermata-scan", daemon=True).start()
    return True
