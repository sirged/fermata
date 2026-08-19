import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import scanner
from .config import FILE_TYPES, LIBRARY_DIR
from .db import connect, tx
from .tabextract import analyze as analyze_pdf, extract as extract_pdf
from .thumbs import thumb_path

router = APIRouter(prefix="/api")

VALID_KINDS = {"notation", "tab", "both", "unknown"}
VALID_PRACTICED = {"recent", "neglected"}
MAX_TRANSCRIPTION_CHARS = 2_000_000


def _score_row(conn, score_id: int):
    row = conn.execute("SELECT * FROM scores WHERE id = ?", (score_id,)).fetchone()
    if not row:
        raise HTTPException(404, "score not found")
    return row


def _with_tags(conn, rows):
    ids = [r["id"] for r in rows]
    tag_map: dict[int, list[str]] = {i: [] for i in ids}
    practice_map: dict[int, dict] = {}
    transcribed_ids: set[int] = set()
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
        for r in conn.execute(
            f"""SELECT DISTINCT score_id FROM transcriptions WHERE score_id IN ({placeholders})""",
            ids,
        ):
            transcribed_ids.add(r["score_id"])
    out = []
    for r in rows:
        d = dict(r)
        d["favorite"] = bool(d["favorite"])
        d["tags"] = tag_map.get(r["id"], [])
        d["has_transcription"] = r["id"] in transcribed_ids
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


def _transcription_row(conn, score_id: int):
    """Edited beats extracted when both exist - see db.py's schema comment
    for why they're kept as separate rows instead of one mutated in place."""
    return conn.execute(
        """SELECT * FROM transcriptions WHERE score_id = ?
           ORDER BY CASE source WHEN 'edited' THEN 0 ELSE 1 END LIMIT 1""",
        (score_id,),
    ).fetchone()


def _transcription_dict(row) -> dict:
    d = dict(row)
    if d.get("confidence"):
        try:
            d["confidence"] = json.loads(d["confidence"])
        except (TypeError, ValueError):
            pass
    return d


@router.get("/scores/{score_id}/transcription")
def get_transcription(score_id: int):
    conn = connect()
    _score_row(conn, score_id)
    row = _transcription_row(conn, score_id)
    if not row:
        raise HTTPException(404, "no transcription for this score")
    return _transcription_dict(row)


@router.get("/scores/{score_id}/transcription/analysis")
def get_transcription_analysis(score_id: int):
    conn = connect()
    row = _score_row(conn, score_id)
    if row["file_type"] != "pdf":
        return {
            "extractable": False,
            "reason": "transcription is only supported for pdf scores",
            "vector": False,
            "tab_staff_count": 0,
            "standard_staff_count": 0,
            "page_count": 0,
        }
    path = LIBRARY_DIR / row["path"]
    if not path.is_file():
        raise HTTPException(404, "file missing from library")
    return analyze_pdf(path)


class TranscribeIn(BaseModel):
    time_signature: tuple[int, int] | None = None


# alphaTab accepts a \ts numerator/denominator of 1-32; the denominator must
# also be a power of two to mean anything as a note-duration unit.
_VALID_TS_DENOMINATORS = {1, 2, 4, 8, 16, 32}


def _validate_time_signature(ts: tuple[int, int]) -> None:
    num, den = ts
    if not 1 <= num <= 32:
        raise HTTPException(422, "time_signature numerator must be between 1 and 32")
    if den not in _VALID_TS_DENOMINATORS:
        raise HTTPException(422, f"time_signature denominator must be one of {sorted(_VALID_TS_DENOMINATORS)}")


@router.post("/scores/{score_id}/transcribe")
def transcribe(score_id: int, body: TranscribeIn | None = Body(default=None)):
    conn = connect()
    row = _score_row(conn, score_id)
    if row["file_type"] != "pdf":
        raise HTTPException(422, "transcription is only supported for pdf scores")
    path = LIBRARY_DIR / row["path"]
    if not path.is_file():
        raise HTTPException(404, "file missing from library")

    ts = tuple(body.time_signature) if body and body.time_signature else None
    if ts is not None:
        _validate_time_signature(ts)
    result = extract_pdf(path, time_signature=ts)
    if not result.extractable:
        raise HTTPException(422, result.reason or "pdf is not extractable")

    # Only ever writes the source='extracted' row (see unique index on
    # (score_id, source) in db.py) - a source='edited' row is untouched.
    confidence_json = json.dumps({"warnings": result.warnings, "confidence": result.confidence})
    with tx() as tx_conn:
        tx_conn.execute(
            """INSERT INTO transcriptions(score_id, format, content, source, confidence, updated_at)
               VALUES (?, 'alphatex', ?, 'extracted', ?, datetime('now'))
               ON CONFLICT(score_id, source) DO UPDATE SET
                   content = excluded.content, confidence = excluded.confidence,
                   updated_at = datetime('now')""",
            (score_id, result.alphatex, confidence_json),
        )

    conn = connect()
    saved = conn.execute(
        "SELECT * FROM transcriptions WHERE score_id = ? AND source = 'extracted'", (score_id,)
    ).fetchone()
    d = _transcription_dict(saved)
    d["warnings"] = result.warnings
    d["bars"] = result.bars
    d["beats"] = result.beats
    d["notes"] = result.notes
    d["tempo"] = result.tempo
    d["tuning"] = result.tuning
    d["tuning_label"] = result.tuning_label
    d["time_signature"] = list(result.time_signature) if result.time_signature else None
    d["time_signature_source"] = result.time_signature_source
    return d


class TranscriptionEditIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_TRANSCRIPTION_CHARS)


@router.put("/scores/{score_id}/transcription")
def save_transcription(score_id: int, body: TranscriptionEditIn):
    with tx() as conn:
        _score_row(conn, score_id)
        conn.execute(
            """INSERT INTO transcriptions(score_id, format, content, source, updated_at)
               VALUES (?, 'alphatex', ?, 'edited', datetime('now'))
               ON CONFLICT(score_id, source) DO UPDATE SET
                   content = excluded.content, updated_at = datetime('now')""",
            (score_id, body.content),
        )
    conn = connect()
    row = conn.execute(
        "SELECT * FROM transcriptions WHERE score_id = ? AND source = 'edited'", (score_id,)
    ).fetchone()
    return _transcription_dict(row)


@router.delete("/scores/{score_id}/transcription")
def delete_transcription(score_id: int):
    """Discard a hand edit, reverting to the extracted transcription.

    Deletes only the source='edited' row - the source='extracted' row (if
    any) is left untouched, mirroring transcribe()'s promise that it never
    touches an edited row. Returns whatever transcription remains, or 404
    if there's none. Deleting an already-gone edited row is a harmless
    no-op, not an error - only "no transcription at all" is a 404.
    """
    with tx() as conn:
        _score_row(conn, score_id)
        conn.execute(
            "DELETE FROM transcriptions WHERE score_id = ? AND source = 'edited'", (score_id,)
        )
    conn = connect()
    row = _transcription_row(conn, score_id)
    if not row:
        raise HTTPException(404, "no transcription for this score")
    return _transcription_dict(row)


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
