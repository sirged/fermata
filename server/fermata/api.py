import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import scanner
from .config import FILE_TYPES, LIBRARY_DIR
from .db import connect, tx
from .thumbs import thumb_path

router = APIRouter(prefix="/api")

VALID_KINDS = {"notation", "tab", "both", "unknown"}
VALID_PRACTICED = {"recent", "neglected"}


def _score_row(conn, score_id: int):
    row = conn.execute("SELECT * FROM scores WHERE id = ?", (score_id,)).fetchone()
    if not row:
        raise HTTPException(404, "score not found")
    return row


def _with_tags(conn, rows):
    ids = [r["id"] for r in rows]
    tag_map: dict[int, list[str]] = {i: [] for i in ids}
    practice_map: dict[int, dict] = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        for r in conn.execute(
            f"""SELECT st.score_id, t.name FROM score_tags st
                JOIN tags t ON t.id = st.tag_id
                WHERE st.score_id IN ({placeholders}) ORDER BY t.name""",
            ids,
        ):
            tag_map[r["score_id"]].append(r["name"])
        for r in conn.execute(
            f"""SELECT score_id, SUM(seconds) AS practice_seconds, MAX(started_at) AS last_practiced
                FROM practice_sessions WHERE score_id IN ({placeholders}) GROUP BY score_id""",
            ids,
        ):
            practice_map[r["score_id"]] = {
                "practice_seconds": r["practice_seconds"],
                "last_practiced": r["last_practiced"],
            }
    out = []
    for r in rows:
        d = dict(r)
        d["favorite"] = bool(d["favorite"])
        d["tags"] = tag_map.get(r["id"], [])
        d.update(practice_map.get(r["id"], {"practice_seconds": 0, "last_practiced": None}))
        out.append(d)
    return out


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/scores")
def list_scores(
    search: str = "",
    collection: str = "",
    kind: str = "",
    tag: str = "",
    favorite: bool = False,
    practiced: str = "",
):
    if practiced and practiced not in VALID_PRACTICED:
        raise HTTPException(422, f"practiced must be one of {sorted(VALID_PRACTICED)}")
    conn = connect()
    sql = "SELECT DISTINCT s.* FROM scores s"
    where, params = [], []
    if tag:
        sql += " JOIN score_tags st ON st.score_id = s.id JOIN tags t ON t.id = st.tag_id"
        where.append("t.name = ?")
        params.append(tag)
    if search:
        where.append("(s.title LIKE ? OR s.composer LIKE ? OR s.source LIKE ? OR s.series LIKE ?)")
        params.extend([f"%{search}%"] * 4)
    if collection:
        where.append("s.collection = ?")
        params.append(collection)
    if kind:
        where.append("s.content_kind = ?")
        params.append(kind)
    if favorite:
        where.append("s.favorite = 1")
    if practiced == "recent":
        where.append(
            """s.id IN (SELECT score_id FROM practice_sessions
                        GROUP BY score_id HAVING MAX(started_at) >= datetime('now', '-14 days'))"""
        )
    elif practiced == "neglected":
        where.append(
            """(s.id NOT IN (SELECT score_id FROM practice_sessions)
                OR s.id IN (SELECT score_id FROM practice_sessions
                            GROUP BY score_id HAVING MAX(started_at) < datetime('now', '-30 days')))"""
        )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.title COLLATE NOCASE"
    rows = conn.execute(sql, params).fetchall()
    return _with_tags(conn, rows)


@router.get("/duplicates")
def list_duplicates():
    conn = connect()
    dupes = conn.execute(
        """SELECT hash, COUNT(*) AS count FROM scores
           GROUP BY hash HAVING COUNT(*) > 1
           ORDER BY count DESC, hash"""
    ).fetchall()
    groups = []
    for d in dupes:
        rows = conn.execute(
            "SELECT * FROM scores WHERE hash = ? ORDER BY path", (d["hash"],)
        ).fetchall()
        groups.append({"hash": d["hash"], "count": d["count"], "scores": _with_tags(conn, rows)})
    return groups


@router.get("/collections")
def list_collections():
    conn = connect()
    rows = conn.execute(
        """SELECT collection, COUNT(*) AS count FROM scores
           WHERE collection IS NOT NULL GROUP BY collection ORDER BY collection"""
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/tags")
def list_tags():
    conn = connect()
    rows = conn.execute(
        """SELECT t.name, COUNT(st.score_id) AS count FROM tags t
           LEFT JOIN score_tags st ON st.tag_id = t.id
           GROUP BY t.id ORDER BY t.name"""
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/scores/{score_id}")
def get_score(score_id: int):
    conn = connect()
    return _with_tags(conn, [_score_row(conn, score_id)])[0]


class ScorePatch(BaseModel):
    title: str | None = None
    composer: str | None = None
    source: str | None = None
    content_kind: str | None = None
    favorite: bool | None = None
    last_page: int | None = None
    tags: list[str] | None = None


@router.patch("/scores/{score_id}")
def patch_score(score_id: int, patch: ScorePatch):
    if patch.content_kind is not None and patch.content_kind not in VALID_KINDS:
        raise HTTPException(422, f"content_kind must be one of {sorted(VALID_KINDS)}")
    with tx() as conn:
        _score_row(conn, score_id)
        fields = {
            k: v
            for k, v in patch.model_dump(exclude_none=True).items()
            if k != "tags"
        }
        if "favorite" in fields:
            fields["favorite"] = int(fields["favorite"])
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE scores SET {sets} WHERE id = ?", [*fields.values(), score_id]
            )
        if patch.tags is not None:
            conn.execute("DELETE FROM score_tags WHERE score_id = ?", (score_id,))
            for name in {t.strip() for t in patch.tags if t.strip()}:
                conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
                conn.execute(
                    """INSERT OR IGNORE INTO score_tags(score_id, tag_id)
                       SELECT ?, id FROM tags WHERE name = ?""",
                    (score_id, name),
                )
    conn = connect()
    return _with_tags(conn, [_score_row(conn, score_id)])[0]


class PracticeIn(BaseModel):
    seconds: int
    note: str | None = Field(default=None, max_length=2000)


def _practice_totals(conn, score_id: int):
    row = conn.execute(
        """SELECT COUNT(*) AS session_count, COALESCE(SUM(seconds), 0) AS practice_seconds,
                  MAX(started_at) AS last_practiced
           FROM practice_sessions WHERE score_id = ?""",
        (score_id,),
    ).fetchone()
    return dict(row)


@router.post("/scores/{score_id}/practice")
def log_practice(score_id: int, body: PracticeIn):
    if not 0 < body.seconds <= 86400:
        raise HTTPException(422, "seconds must be between 1 and 86400")
    with tx() as conn:
        _score_row(conn, score_id)
        conn.execute(
            "INSERT INTO practice_sessions(score_id, started_at, seconds, note) VALUES (?, datetime('now'), ?, ?)",
            (score_id, body.seconds, body.note),
        )
    return get_practice(score_id)


@router.get("/scores/{score_id}/practice")
def get_practice(score_id: int):
    conn = connect()
    _score_row(conn, score_id)
    sessions = conn.execute(
        "SELECT * FROM practice_sessions WHERE score_id = ? ORDER BY started_at DESC, id DESC LIMIT 50",
        (score_id,),
    ).fetchall()
    return {"sessions": [dict(r) for r in sessions], **_practice_totals(conn, score_id)}


@router.get("/practice/summary")
def practice_summary():
    conn = connect()
    week = conn.execute(
        """SELECT COALESCE(SUM(seconds), 0) AS total_seconds, COUNT(*) AS session_count
           FROM practice_sessions WHERE started_at >= datetime('now', '-7 days')"""
    ).fetchone()
    top = conn.execute(
        """SELECT s.id, s.title, SUM(p.seconds) AS practice_seconds
           FROM practice_sessions p JOIN scores s ON s.id = p.score_id
           WHERE p.started_at >= datetime('now', '-7 days')
           GROUP BY p.score_id ORDER BY practice_seconds DESC LIMIT 5"""
    ).fetchall()
    return {
        "week_seconds": week["total_seconds"],
        "week_sessions": week["session_count"],
        "top_scores": [dict(r) for r in top],
    }


@router.get("/scores/{score_id}/file")
def get_file(score_id: int):
    conn = connect()
    row = _score_row(conn, score_id)
    path = LIBRARY_DIR / row["path"]
    if not path.is_file():
        raise HTTPException(404, "file missing from library")
    media = "application/pdf" if row["file_type"] == "pdf" else "application/octet-stream"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/scores/{score_id}/thumb")
def get_thumb(score_id: int):
    conn = connect()
    row = _score_row(conn, score_id)
    path = thumb_path(row["hash"])
    if not path.is_file():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(path, media_type="image/png")


@router.post("/scan")
def trigger_scan():
    started = scanner.start_scan()
    return {"started": started, **scanner.scan_status()}


@router.get("/scan/status")
def get_scan_status():
    return scanner.scan_status()


_SAFE_SEGMENT = re.compile(r"^[^/\\]+$")


@router.post("/upload")
async def upload(file: UploadFile, folder: str = "Uploads"):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in FILE_TYPES:
        raise HTTPException(422, f"unsupported file type {suffix!r}")
    parts = [p for p in folder.replace("\\", "/").split("/") if p and p != ".."]
    if not all(_SAFE_SEGMENT.match(p) for p in parts):
        raise HTTPException(422, "invalid folder")
    name = Path(file.filename).name
    dest_dir = LIBRARY_DIR.joinpath(*parts)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    scanner.start_scan()
    return {"saved": str(dest.relative_to(LIBRARY_DIR))}
