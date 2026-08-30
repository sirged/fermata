import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Path as PathParam,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictInt

from . import instruments, practice, scanner
from . import version as version_info
from .api_models import (
    ByActivityOut,
    ByScoreOut,
    CollectionOut,
    CurrentGoalOut,
    DuplicateGroupOut,
    FolderCreateOut,
    FolderOut,
    FolderRenameOut,
    GoalDeleteOut,
    GoalListOut,
    GoalOut,
    HealthOut,
    InstrumentDeleteOut,
    InstrumentOut,
    InstrumentPresetOut,
    LibraryMoveOut,
    LogPracticeOut,
    PracticeHistoryOut,
    PracticeReviewOut,
    PracticeSessionOut,
    PracticeSummaryOut,
    ScanStatusOut,
    ScanTriggerOut,
    ScoreDeleteOut,
    ScoreMoveOut,
    ScoreOut,
    ScorePracticeOut,
    ScorePurgeOut,
    ScoreRestoreOut,
    SessionDeleteOut,
    SessionListOut,
    SettingsOut,
    TagOut,
    TranscribeResultOut,
    TranscriptionAnalysisOut,
    TranscriptionOut,
    UploadOut,
    VersionOut,
)
from .config import FILE_TYPES, LIBRARY_DIR
from .db import DEFAULT_OWNER, connect, tx, write_tx
from .glyph_rhythm import VALID_TS_DENOMINATORS
from .metadata import parse_path
from .tabextract import analyze as analyze_pdf, extract as extract_pdf
from .thumbs import thumb_path

router = APIRouter(prefix="/api")

# Used by the library-management routes at the bottom of this file, for the one
# case where a filesystem operation fails in a way a person cannot see: a file
# that could not be put back after a failed move. Everything else these routes
# have to say, they say in the response.
log = logging.getLogger("fermata.api")

# Every route below carries a `tags=[...]` entry grouping it in /docs, and a
# `response_model=` pinning its shape - see api_models.py for what each model
# documents and why it lives apart from the routes. TAG_* names are declared
# once here so a route and /docs agree on the exact string.
TAG_SYSTEM = "system"
TAG_SETTINGS = "settings"
TAG_INSTRUMENTS = "instruments"
TAG_LIBRARY = "library"
TAG_PRACTICE = "practice"
TAG_TRANSCRIPTION = "transcription"
TAG_SCAN = "scan"

# SQLite's INTEGER is 64-bit, and handing the driver anything wider raises
# OverflowError from inside the query - a 500 for what is only ever a row that
# cannot exist. Bounded in the signature so an impossible id is refused before
# it reaches a database at all. `ge=1` because rowids start at 1, so 0 and
# negatives are equally impossible.
SQLITE_MAX_INTEGER = 2**63 - 1
RowId = Annotated[int, PathParam(ge=1, le=SQLITE_MAX_INTEGER)]

# A whole number and nothing that merely converts to one. Pydantic's default
# mode coerces before any validator sees the value, so `true` arrived as 1 -
# and {"target_days": true} set a one-day goal, cheerfully, past a guard
# written specifically to reject bools. Every numeric field a client sends
# below uses this.
Count = Annotated[StrictInt, Field()]

VALID_KINDS = {"notation", "tab", "both", "unknown"}
VALID_PRACTICED = {"recent", "neglected"}

# A user setting, not a per-score one - kept server-side (not browser storage)
# so it follows a person between devices. Defaults are what a fresh install
# with nothing stored behaves as. `SETTINGS_CHOICES` is optional per key: a
# key with no entry there accepts any string value.
#
# staff_theme's choices must be kept in sync with SCORE_THEMES in
# web/src/lib/score-render.js - test_settings_api.py's
# test_staff_theme_choices_match_the_frontends_score_themes parses that file
# and fails if the two ever disagree.
#
# week_starts_on decides which seven days "this week" means when a goal is set
# without naming its period, and which weeks a review walks back through. It is
# a preference and not a fact - half the world starts a week on Sunday - and a
# goal counted over the wrong seven days is counted against days its owner did
# not think were part of the week. It only ever decides a DEFAULT: a goal
# stores the two dates it was actually set for, so changing this never moves a
# goal that already exists.
SETTINGS_DEFAULTS = {"staff_theme": "parchment", "week_starts_on": practice.DEFAULT_WEEK_START}
SETTINGS_CHOICES = {
    "staff_theme": {"parchment", "noir", "print"},
    "week_starts_on": set(practice.WEEK_STARTS),
}
# MusicXML is a good deal more verbose than alphaTex for the same music: across
# the sampled library it runs about 44x the characters, and the longest score
# comes out around 660 KB. This leaves room for a compilation several times
# longer than anything in that sample.
MAX_TRANSCRIPTION_CHARS = 8_000_000

# The format a new extraction is stored in. Rows carry their own format, so
# this changing does not invalidate what is already stored - see transcribe().
TRANSCRIPTION_FORMAT = "musicxml"
VALID_TRANSCRIPTION_FORMATS = {"musicxml", "alphatex"}


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
        # last_practiced is the practice DAY (YYYY-MM-DD), not the timestamp it
        # used to be. The library's "practised 3 days ago" is a calendar
        # statement, and reading it off a UTC timestamp put an evening's
        # practice on the wrong day for anyone west of Greenwich - the same
        # reason the practice day is stored at all. Compared as text, which is
        # chronological for this format.
        for r in conn.execute(
            f"""SELECT p.score_id, SUM(p.seconds) AS practice_seconds,
                       MAX({practice.LOCAL_DATE_SQL}) AS last_practiced
                FROM practice_sessions p WHERE p.score_id IN ({placeholders})
                GROUP BY p.score_id""",
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


@router.get("/health", tags=[TAG_SYSTEM], response_model=HealthOut)
def health():
    """Whether the process is up and answering requests at all."""
    return {"status": "ok"}


@router.get("/version", tags=[TAG_SYSTEM], response_model=VersionOut)
def get_version():
    """What build is actually running - see fermata/version.py."""
    return version_info.info()


@router.get("/settings", tags=[TAG_SETTINGS], response_model=SettingsOut)
def get_settings():
    """Every user preference, defaulted for anything never written - see
    SETTINGS_DEFAULTS."""
    conn = connect()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE owner = ?", (DEFAULT_OWNER,)
    ).fetchall()
    result = dict(SETTINGS_DEFAULTS)
    for row in rows:
        key, value = row["key"], row["value"]
        if key not in SETTINGS_DEFAULTS:
            continue  # a setting since retired - ignore rather than surface it
        choices = SETTINGS_CHOICES.get(key)
        if choices and value not in choices:
            # A value that was valid when stored but isn't any more - this PR
            # itself renames the theme 'slate' to 'noir', so an existing
            # install can hold staff_theme='slate'. put_settings() can never
            # write this today, but a stored value outliving the choices it
            # was written under is exactly the kind of drift a rename causes,
            # so fall back to the default rather than hand back a value
            # nothing can render (the picker would then highlight no card at
            # all, and the renderer would quietly fall back on its own).
            continue
        result[key] = value
    return result


@router.put("/settings", tags=[TAG_SETTINGS], response_model=SettingsOut)
def put_settings(patch: dict[str, str] = Body(...)):
    """Write one or more preferences and return the settings as they now
    stand. A key not already known, or a value not among its choices, fails
    the whole call - see the module comment on SETTINGS_CHOICES."""
    # A settings store that takes arbitrary keys becomes a junk drawer - only
    # known keys are accepted, and a key with choices in SETTINGS_CHOICES must
    # be one of them.
    unknown = set(patch) - set(SETTINGS_DEFAULTS)
    if unknown:
        raise HTTPException(422, f"unknown setting(s): {sorted(unknown)}")
    for key, value in patch.items():
        choices = SETTINGS_CHOICES.get(key)
        if choices and value not in choices:
            raise HTTPException(422, f"{key} must be one of {sorted(choices)}")
    with tx() as conn:
        for key, value in patch.items():
            conn.execute(
                """INSERT INTO settings(owner, key, value) VALUES (?, ?, ?)
                   ON CONFLICT(owner, key) DO UPDATE SET value = excluded.value""",
                (DEFAULT_OWNER, key, value),
            )
    return get_settings()


class InstrumentIn(BaseModel):
    """A whole definition. Updates replace rather than patch: a tuning is only
    coherent as a set (string_count and string_pitches have to agree, and
    fret_count only exists at all when fretted), so accepting half of one and
    keeping half of the old is how a five-string bass ends up with four
    pitches."""

    # Defaulted, so today's clients need not send it and tomorrow's kinds do not
    # change the shape of this request. Only "string" is implemented - see
    # instruments.VALID_KINDS.
    kind: str = instruments.DEFAULT_KIND
    # The raw ceiling, not the name rule: instruments.normalise strips control
    # characters and collapses whitespace first, then applies MAX_NAME_CHARS to
    # what will actually be stored.
    name: str = Field(max_length=instruments.MAX_RAW_NAME_CHARS)
    fretted: bool = True
    string_count: int
    string_pitches: list[str]
    fret_count: int | None = None
    capo: int | None = None
    reference_pitch: float = instruments.DEFAULT_REFERENCE_HZ


def _instrument_dict(row) -> dict:
    d = dict(row)
    d["fretted"] = bool(d["fretted"])
    d["string_pitches"] = json.loads(d["string_pitches"])
    # Derived, not stored: each string's nominal tuning and what it actually
    # sounds under the capo. Sent so a client displaying a SAVED instrument has
    # no reason to compute any of it - the browser's own copy of the arithmetic
    # exists only for a draft the server has never seen. Unrounded, so exactly
    # one place decides how a frequency is written.
    d["strings"] = instruments.string_details(
        d["string_pitches"], d["reference_pitch"], d["capo"]
    )
    return d


def _instrument_row(conn, instrument_id: int):
    row = conn.execute(
        "SELECT * FROM instruments WHERE id = ? AND owner = ?",
        (instrument_id, DEFAULT_OWNER),
    ).fetchone()
    if not row:
        raise HTTPException(404, "instrument not found")
    return row


def _normalise_instrument(body: InstrumentIn) -> dict:
    try:
        return instruments.normalise(
            kind=body.kind,
            name=body.name,
            fretted=body.fretted,
            string_count=body.string_count,
            string_pitches=body.string_pitches,
            fret_count=body.fret_count,
            capo=body.capo,
            reference_pitch=body.reference_pitch,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/instruments", tags=[TAG_INSTRUMENTS], response_model=list[InstrumentOut])
def list_instruments():
    """Every instrument the player has defined, in name order."""
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM instruments WHERE owner = ? ORDER BY name COLLATE NOCASE, id",
        (DEFAULT_OWNER,),
    ).fetchall()
    return [_instrument_dict(r) for r in rows]


# Declared before /instruments/{instrument_id}: FastAPI matches in declaration
# order, and the other way round "presets" would be tried as an int path
# parameter and answered with a 422 about parsing rather than with the presets.
@router.get(
    "/instruments/presets", tags=[TAG_INSTRUMENTS], response_model=list[InstrumentPresetOut]
)
def list_instrument_presets():
    """Built-in tunings a player can start a definition from - see
    instruments.PRESETS."""
    return instruments.presets()


@router.post("/instruments", tags=[TAG_INSTRUMENTS], response_model=InstrumentOut)
def create_instrument(body: InstrumentIn):
    """Save a new instrument definition."""
    fields = _normalise_instrument(body)
    with write_tx() as conn:
        cur = conn.execute(
            """INSERT INTO instruments(owner, kind, name, fretted, string_count, string_pitches,
                                       fret_count, capo, reference_pitch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                DEFAULT_OWNER,
                fields["kind"],
                fields["name"],
                int(fields["fretted"]),
                fields["string_count"],
                json.dumps(fields["string_pitches"]),
                fields["fret_count"],
                fields["capo"],
                fields["reference_pitch"],
            ),
        )
        instrument_id = cur.lastrowid
    return get_instrument(instrument_id)


@router.get("/instruments/{instrument_id}", tags=[TAG_INSTRUMENTS], response_model=InstrumentOut)
def get_instrument(instrument_id: RowId):
    """One instrument definition."""
    conn = connect()
    return _instrument_dict(_instrument_row(conn, instrument_id))


@router.put("/instruments/{instrument_id}", tags=[TAG_INSTRUMENTS], response_model=InstrumentOut)
def update_instrument(instrument_id: RowId, body: InstrumentIn):
    """Replace a definition in place.

    NOTE FOR WHATEVER FIRST CONSUMES scores.instrument_id: nothing here
    revalidates the scores pointing at this instrument, so an edit can leave
    one referring to something it no longer fits - a seven-string retuned down
    to one string keeps every reference intact. That is harmless while the
    reference is only stored, and a trap the moment anything derives positions,
    tablature or playback from it: such a consumer must treat the instrument as
    possibly having changed under the score since it was chosen, and check the
    string count and range itself rather than trusting that the reference was
    valid when it was made.
    """
    fields = _normalise_instrument(body)
    with write_tx() as conn:
        _instrument_row(conn, instrument_id)
        conn.execute(
            """UPDATE instruments SET kind = ?, name = ?, fretted = ?, string_count = ?,
                   string_pitches = ?, fret_count = ?, capo = ?, reference_pitch = ?,
                   updated_at = datetime('now')
               WHERE id = ? AND owner = ?""",
            (
                fields["kind"],
                fields["name"],
                int(fields["fretted"]),
                fields["string_count"],
                json.dumps(fields["string_pitches"]),
                fields["fret_count"],
                fields["capo"],
                fields["reference_pitch"],
                instrument_id,
                DEFAULT_OWNER,
            ),
        )
    return get_instrument(instrument_id)


@router.delete(
    "/instruments/{instrument_id}", tags=[TAG_INSTRUMENTS], response_model=InstrumentDeleteOut
)
def delete_instrument(instrument_id: RowId):
    """Forget an instrument the player no longer has. Scores that referenced it
    keep their own rows - scores.instrument_id is ON DELETE SET NULL, so they
    revert to naming no instrument rather than disappearing with it.

    `scores_unlinked` is counted before the delete so a caller can say how many
    scores stopped naming an instrument, rather than doing it silently.
    """
    with write_tx() as conn:
        _instrument_row(conn, instrument_id)
        unlinked = conn.execute(
            "SELECT COUNT(*) AS n FROM scores WHERE instrument_id = ?", (instrument_id,)
        ).fetchone()["n"]
        conn.execute(
            "DELETE FROM instruments WHERE id = ? AND owner = ?",
            (instrument_id, DEFAULT_OWNER),
        )
    return {"deleted": instrument_id, "scores_unlinked": unlinked}


@router.get("/scores", tags=[TAG_LIBRARY], response_model=list[ScoreOut])
def list_scores(
    search: str = "",
    collection: str = "",
    kind: str = "",
    tag: str = "",
    favorite: bool = False,
    practiced: str = "",
):
    """The library, filtered and searched. `search` matches title, composer,
    source or series; `practiced` is 'recent' (practised in the last 14 days)
    or 'neglected' (present on disk, and either never practised or not
    practised in 30 days) - see the query's own comments for why those two
    views disagree about a score whose file has gone missing.

    A DELETED SCORE IS NEVER HERE, under any filter - it is in GET /api/trash
    until it is restored or destroyed. A score whose FILE has gone missing is a
    different thing and is still here, carrying `missing_since`: Fermata not
    being able to find a file is not the same statement as somebody having
    thrown it away."""
    if practiced and practiced not in VALID_PRACTICED:
        raise HTTPException(422, f"practiced must be one of {sorted(VALID_PRACTICED)}")
    conn = connect()
    sql = "SELECT DISTINCT s.* FROM scores s"
    # A deleted score is not in the library, and no filter brings it back here -
    # it is in the trash, which has its own endpoint (#56). This is deliberately
    # unconditional rather than a `deleted` parameter: every view built on this
    # one (the grid, the collection counts, "needs attention") would otherwise
    # have to remember to pass it, and forgetting once puts a score somebody
    # deleted back in front of them.
    where, params = ["s.deleted_at IS NULL"], []
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
    # Windowed on the practice DAY, not on the UTC timestamp. These two views
    # and the practice page have to agree about when a piece was last worked
    # on, and the library is the view a person sees first - so "practised
    # recently" here and "practised on Tuesday" there cannot be answered from
    # different clocks. A back-dated session counts from the day it says it
    # happened, which is the whole point of being able to enter one.
    if practiced == "recent":
        where.append(
            f"""s.id IN (SELECT p.score_id FROM practice_sessions p
                         GROUP BY p.score_id
                         HAVING MAX({practice.LOCAL_DATE_SQL}) >= date('now', '-14 days'))"""
        )
    elif practiced == "neglected":
        # A score whose file is not there is excluded from this one view, and
        # only this one. "Needs attention" answers "what should I work on next",
        # and it sorts unpractised scores first - so a score that was deleted
        # months ago arrives at the top of the list and STAYS there for ever,
        # because it cannot be opened, so it cannot be practised, so it never
        # stops being neglected. The view whose whole job is to suggest the next
        # thing to play was headed permanently by the one row that cannot be
        # played.
        #
        # "Recently practiced" deliberately keeps them: that is a record of what
        # happened, and practice that happened does not stop having happened
        # because the file moved. Same reasoning as the sessions themselves
        # outliving their score.
        where.append("s.missing_since IS NULL")
        where.append(
            # NOT EXISTS, not NOT IN, and this is not a style preference. A
            # session can now have no score at all - a trainer, or a piece
            # since deleted - and SQL's NOT IN against a set containing NULL
            # is never true, for any row. So the version of this that read
            # `s.id NOT IN (SELECT score_id FROM practice_sessions)` returned
            # NOTHING the moment one score-less session existed anywhere,
            # which is precisely what the nullable column was added for:
            # "needs attention" went from listing every unpractised score to
            # listing none, with nothing failing. NOT EXISTS asks the question
            # this filter means and is unaffected by NULLs.
            #
            # Worth knowing as a pattern rather than an instance: nothing about
            # that query was wrong when it was written. It rested on an
            # assumption the schema had been guaranteeing, and this change
            # stopped guaranteeing it.
            f"""(NOT EXISTS (SELECT 1 FROM practice_sessions p WHERE p.score_id = s.id)
                 OR s.id IN (SELECT p.score_id FROM practice_sessions p
                             GROUP BY p.score_id
                             HAVING MAX({practice.LOCAL_DATE_SQL}) < date('now', '-30 days')))"""
        )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.title COLLATE NOCASE"
    rows = conn.execute(sql, params).fetchall()
    return _with_tags(conn, rows)


@router.get("/duplicates", tags=[TAG_LIBRARY], response_model=list[DuplicateGroupOut])
def list_duplicates():
    """Copies of the same content that are BOTH ON DISK.

    Rows whose file is missing are excluded, and that is not the same choice the
    library grid makes. This view answers a question about files - "am I storing
    the same arrangement twice, and can I delete one" - so a row with no file
    behind it is not a copy of anything. Including them made this actively
    wrong: two identical files in a folder that gets renamed leave two marked
    rows plus two new ones, and this reported four copies of a hash that two
    files on disk share, half of them failing to open when clicked.
    """
    conn = connect()
    dupes = conn.execute(
        """SELECT hash, COUNT(*) AS count FROM scores
           WHERE missing_since IS NULL AND deleted_at IS NULL
           GROUP BY hash HAVING COUNT(*) > 1
           ORDER BY count DESC, hash"""
    ).fetchall()
    groups = []
    for d in dupes:
        rows = conn.execute(
            "SELECT * FROM scores WHERE hash = ? AND missing_since IS NULL"
            " AND deleted_at IS NULL ORDER BY path",
            (d["hash"],),
        ).fetchall()
        groups.append({"hash": d["hash"], "count": d["count"], "scores": _with_tags(conn, rows)})
    return groups


@router.get("/collections", tags=[TAG_LIBRARY], response_model=list[CollectionOut])
def list_collections():
    """Every collection, with how many of its scores are there and how many are not.

    `count` is files actually on disk, and `missing` is the rest. Counting them
    together produced a straightforwardly false sidebar: rename a folder and its
    scores are marked missing under the old name while new rows appear under the
    new one, so the old name went on standing in the sidebar with a full count
    beside it - a collection that does not exist, which a person has no way to
    remove and no reason to distrust.

    A collection with nothing left on disk is still listed, with `count` zero,
    rather than dropped. Dropping it would be the same mistake in the other
    direction: those rows hold practice history and tags, they are still
    reachable, and a name quietly disappearing from the sidebar is how somebody
    concludes their library ate itself. Listed with a zero says what happened.
    """
    conn = connect()
    rows = conn.execute(
        """SELECT collection,
                  SUM(CASE WHEN missing_since IS NULL THEN 1 ELSE 0 END) AS count,
                  SUM(CASE WHEN missing_since IS NULL THEN 0 ELSE 1 END) AS missing
             FROM scores
            WHERE collection IS NOT NULL AND deleted_at IS NULL
         GROUP BY collection ORDER BY collection"""
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/tags", tags=[TAG_LIBRARY], response_model=list[TagOut])
def list_tags():
    """Every tag in use, and how many scores carry it."""
    conn = connect()
    rows = conn.execute(
        """SELECT t.name, COUNT(s.id) AS count FROM tags t
           LEFT JOIN score_tags st ON st.tag_id = t.id
           LEFT JOIN scores s ON s.id = st.score_id AND s.deleted_at IS NULL
           GROUP BY t.id ORDER BY t.name"""
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/scores/{score_id}", tags=[TAG_LIBRARY], response_model=ScoreOut)
def get_score(score_id: RowId):
    """One score, with its tags, transcription flag and practice totals."""
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
    # Which of the player's instruments this score is for. Explicit null clears
    # it - see patch_score.
    instrument_id: int | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)


@router.patch("/scores/{score_id}", tags=[TAG_LIBRARY], response_model=ScoreOut)
def patch_score(score_id: RowId, patch: ScorePatch):
    """Change one or more fields on a score. `tags` replaces the whole tag
    set when present; an explicit `instrument_id: null` clears it, which is
    different from omitting the field entirely."""
    if patch.content_kind is not None and patch.content_kind not in VALID_KINDS:
        raise HTTPException(422, f"content_kind must be one of {sorted(VALID_KINDS)}")
    with write_tx() as conn:
        _score_row(conn, score_id)
        if patch.instrument_id is not None:
            _instrument_row(conn, patch.instrument_id)
        fields = {
            k: v
            for k, v in patch.model_dump(exclude_none=True).items()
            if k not in ("tags", "instrument_id")
        }
        # instrument_id is the one field where an explicit null is a request
        # rather than an omission: "this score is for no particular instrument"
        # has to be sayable, and the omit-nulls rule the other fields need
        # (a title cannot be cleared - the column is NOT NULL) would swallow it.
        if "instrument_id" in patch.model_fields_set:
            fields["instrument_id"] = patch.instrument_id
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


# ---------------------------------------------------------------------------
# Practice: sessions (what happened) and goals (what was intended).
#
# The rules live in fermata/practice.py, including why a session records the
# practiser's own calendar day and why nothing stores whether a goal was met.
# This layer does routing, ownership and 404s, and nothing else - so a session
# logged from the per-score path and one logged from the general path are the
# same row obeying the same rules.
# ---------------------------------------------------------------------------


def _server_today():
    """Today as the server sees it, in UTC.

    UTC and not the host's local time, because started_at is UTC and the two
    disagreeing would mean a session recorded 'today' landing outside the
    period a goal was told today was in. Every endpoint that needs a date
    accepts `today` to override this - see _today().
    """
    return datetime.now(timezone.utc).date()


def _today(today: str | None):
    """The date an endpoint should treat as today.

    Passed in by a client that knows its own timezone, because the server's UTC
    date is not the practiser's date - and whether a period is still running is
    exactly the sort of thing that must not be an hour-of-day accident. A
    client that sends nothing gets the server's UTC date.

    Bounded to the same window a practice day may name - see the comment on
    practice.MAX_BACKDATE_DAYS. Unbounded, a read endpoint answered
    `today=2099-01-01` with a plausible-looking empty week rather than an error,
    which is the worst kind of wrong answer: nothing in the response marks it as
    suspect. Writes were already bounded; reads were not.
    """
    if not today:
        return _server_today()
    try:
        day = practice.parse_day(today, "today")
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    server_day = _server_today()
    earliest = server_day - timedelta(days=practice.MAX_BACKDATE_DAYS)
    latest = server_day + timedelta(days=practice.MAX_FUTURE_DAYS)
    if not earliest <= day <= latest:
        raise HTTPException(
            422,
            f"today must be between {earliest.isoformat()} and {latest.isoformat()} - "
            "no practice this instance could hold falls outside that",
        )
    return day


def _week_starts_on() -> str:
    return get_settings()["week_starts_on"]


def _practice_totals(conn, score_id: int):
    row = conn.execute(
        f"""SELECT COUNT(*) AS session_count, COALESCE(SUM(p.seconds), 0) AS practice_seconds,
                   MAX({practice.LOCAL_DATE_SQL}) AS last_practiced
            FROM practice_sessions p WHERE p.score_id = ?""",
        (score_id,),
    ).fetchone()
    return dict(row)


class PracticeIn(BaseModel):
    """A session as logged against a score whose id is already in the path.

    Everything but `seconds` is optional, and that is deliberate: the timer can
    stop and store the one thing it knows, and the detail - how it felt, at
    what tempo, which bars - can arrive afterwards through the PATCH below
    rather than standing between a player and a stopped clock.
    """

    seconds: Count
    activity: str | None = None
    mode: str | None = None
    # The practiser's own calendar day. Omitted means "attribute this to the
    # UTC day", which is what a client that does not know its timezone should
    # say rather than guess.
    local_date: str | None = None
    from_bar: Count | None = None
    to_bar: Count | None = None
    from_page: Count | None = None
    to_page: Count | None = None
    tempo_bpm: Count | None = None
    target_tempo_bpm: Count | None = None
    rating: Count | None = None
    note: str | None = Field(default=None, max_length=practice.MAX_NOTE_CHARS)


class SessionIn(PracticeIn):
    """A session that names its own score, or names none at all."""

    score_id: Count | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)


class SessionPatch(BaseModel):
    """Detail added to, or corrected on, a session already stored.

    Every field including `seconds` is optional, and an explicit null CLEARS
    the field rather than being ignored - a rating entered by mistake has to be
    removable. The merged record is then re-checked in full, so a patch cannot
    reach a state a fresh log would have been refused.
    """

    seconds: Count | None = None
    activity: str | None = None
    mode: str | None = None
    local_date: str | None = None
    from_bar: Count | None = None
    to_bar: Count | None = None
    from_page: Count | None = None
    to_page: Count | None = None
    tempo_bpm: Count | None = None
    target_tempo_bpm: Count | None = None
    rating: Count | None = None
    note: str | None = Field(default=None, max_length=practice.MAX_NOTE_CHARS)


_SESSION_COLUMNS = (
    "score_id",
    "activity",
    "mode",
    "seconds",
    "local_date",
    "from_bar",
    "to_bar",
    "from_page",
    "to_page",
    "tempo_bpm",
    "target_tempo_bpm",
    "rating",
    "note",
)


def _normalise_session(
    conn, fields: dict, allow_missing_score: bool = False, check_day_window: bool = True
) -> dict:
    if fields.get("score_id") is not None:
        _score_row(conn, fields["score_id"])
    try:
        return practice.normalise_session(
            recorded_on=_server_today(),
            allow_missing_score=allow_missing_score,
            check_day_window=check_day_window,
            **fields,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


def _insert_session(conn, values: dict):
    """Store a session and return the stored row.

    Returned rather than looked up again afterwards: reading it back on a
    second connection - or after the transaction closed - lets a delete land in
    the gap and turn a successful write into a 404 about a row this call had
    just created.
    """
    columns = ", ".join(_SESSION_COLUMNS)
    placeholders = ", ".join("?" * len(_SESSION_COLUMNS))
    return conn.execute(
        f"""INSERT INTO practice_sessions(owner, started_at, {columns})
            VALUES (?, datetime('now'), {placeholders})
            RETURNING *""",
        [DEFAULT_OWNER, *(values[c] for c in _SESSION_COLUMNS)],
    ).fetchone()


def _session_row(conn, session_id: int):
    row = conn.execute(
        "SELECT * FROM practice_sessions WHERE id = ? AND owner = ?",
        (session_id, DEFAULT_OWNER),
    ).fetchone()
    if not row:
        raise HTTPException(404, "practice session not found")
    return row


@router.post(
    "/scores/{score_id}/practice", tags=[TAG_PRACTICE], response_model=LogPracticeOut
)
def log_practice(score_id: RowId, body: PracticeIn):
    """Log a practice session against this score, and return it alongside
    the score's recent sessions and totals."""
    with write_tx() as conn:
        _score_row(conn, score_id)
        fields = body.model_dump()
        fields["score_id"] = score_id
        values = _normalise_session(conn, fields)
        # The stored row, not the request echoed: the id is what a client needs
        # to add detail to what it just logged, and the row is the only place
        # the attributed practice day exists once the server has decided it.
        session = practice.session_dict(_insert_session(conn, values))
        totals = {
            "sessions": [
                practice.session_dict(r) for r in _recent_sessions(conn, score_id)
            ],
            **_practice_totals(conn, score_id),
        }
    return {"session": session, **totals}


def _recent_sessions(conn, score_id: int):
    return conn.execute(
        """SELECT * FROM practice_sessions WHERE score_id = ?
        ORDER BY started_at DESC, id DESC LIMIT 50""",
        (score_id,),
    ).fetchall()


@router.get(
    "/scores/{score_id}/practice", tags=[TAG_PRACTICE], response_model=ScorePracticeOut
)
def get_practice(score_id: RowId):
    """This score's recent sessions (up to 50) and its all-time totals."""
    conn = connect()
    _score_row(conn, score_id)
    return {
        "sessions": [practice.session_dict(r) for r in _recent_sessions(conn, score_id)],
        **_practice_totals(conn, score_id),
    }


@router.post("/practice/sessions", tags=[TAG_PRACTICE], response_model=PracticeSessionOut)
def log_session(body: SessionIn):
    """Log practice that is not necessarily against a piece.

    The general form of the per-score endpoint above, and the one an exercise
    or a stretch of unstructured playing uses: `score_id` may be omitted for
    every activity except 'piece', which is defined by having one.
    """
    with write_tx() as conn:
        values = _normalise_session(conn, body.model_dump())
        return practice.session_dict(_insert_session(conn, values))


@router.patch(
    "/practice/sessions/{session_id}", tags=[TAG_PRACTICE], response_model=PracticeSessionOut
)
def patch_session(session_id: RowId, patch: SessionPatch):
    """Add or correct detail on a session already logged. An explicit null on
    any field clears it; the whole record is re-validated, not just the
    changed fields - see the function's own comments for why."""
    with write_tx() as conn:
        row = _session_row(conn, session_id)
        # The whole record is rebuilt and re-checked, not just the changed
        # fields: `to_bar` alone is meaningless without the `from_bar` already
        # stored, and validating a fragment would let a patch produce a row a
        # fresh log could not have created.
        fields = {c: row[c] for c in _SESSION_COLUMNS}
        for name in patch.model_fields_set:
            fields[name] = getattr(patch, name)
        # A session whose score has since been deleted is still a true record
        # and has to stay editable - somebody adding a note to it is not making
        # a claim about a piece, they are annotating practice that happened.
        # The allowance is granted from the STORED row, so a patch cannot use
        # it to create that state deliberately.
        values = _normalise_session(
            conn,
            fields,
            allow_missing_score=practice.is_orphaned(row["activity"], row["score_id"]),
            # How far back a practice day may be is a rule about what somebody
            # may claim NOW. Re-applying it to the date already stored made a
            # session permanently uneditable once it was old enough - so a
            # rating on last year's practice could not be corrected, for a
            # reason that has nothing to do with the rating. Checked only when
            # the date is what is being written.
            check_day_window="local_date" in patch.model_fields_set,
        )
        assignments = ", ".join(f"{c} = ?" for c in _SESSION_COLUMNS)
        updated = conn.execute(
            f"""UPDATE practice_sessions SET {assignments}
                 WHERE id = ? AND owner = ? RETURNING *""",
            [*(values[c] for c in _SESSION_COLUMNS), session_id, DEFAULT_OWNER],
        ).fetchone()
        return practice.session_dict(updated)


@router.delete(
    "/practice/sessions/{session_id}", tags=[TAG_PRACTICE], response_model=SessionDeleteOut
)
def delete_session(session_id: RowId):
    """Remove a session that should not be in the record.

    Practice history cannot be regenerated from anything on disk, so this is
    the one destructive operation here and it exists only because a timer left
    running by accident is otherwise permanent - and a record with an invented
    two-hour session in it is not an honest record either.
    """
    with write_tx() as conn:
        _session_row(conn, session_id)
        conn.execute(
            "DELETE FROM practice_sessions WHERE id = ? AND owner = ?",
            (session_id, DEFAULT_OWNER),
        )
    return {"deleted": session_id}


@router.get("/practice/sessions", tags=[TAG_PRACTICE], response_model=SessionListOut)
def list_sessions(
    start: str = "",
    end: str = "",
    score_id: Annotated[int | None, Query(ge=1, le=SQLITE_MAX_INTEGER)] = None,
    activity: str = "",
    limit: int = practice.DEFAULT_SESSION_LIMIT,
):
    """Sessions themselves, across every piece and every kind of practice.

    The raw record, filtered by practice day: this is what answers "what did I
    actually do this week" without a reader having to reconstruct it from
    per-score endpoints one score at a time.
    """
    if not 1 <= limit <= practice.MAX_SESSION_LIMIT:
        raise HTTPException(422, f"limit must be between 1 and {practice.MAX_SESSION_LIMIT}")
    if activity and activity not in practice.ACTIVITIES:
        raise HTTPException(422, f"activity must be one of {sorted(practice.ACTIVITIES)}")
    where = ["p.owner = ?"]
    params: list = [DEFAULT_OWNER]
    for value, field, comparison in ((start, "start", ">="), (end, "end", "<=")):
        if value:
            try:
                day = practice.parse_day(value, field)
            except ValueError as e:
                raise HTTPException(422, str(e)) from None
            where.append(f"{practice.LOCAL_DATE_SQL} {comparison} ?")
            params.append(day.isoformat())
    if score_id is not None:
        where.append("p.score_id = ?")
        params.append(score_id)
    if activity:
        where.append("p.activity = ?")
        params.append(activity)
    conn = connect()
    filters = " AND ".join(where)
    rows = conn.execute(
        f"""SELECT p.*, s.title AS score_title
              FROM practice_sessions p LEFT JOIN scores s ON s.id = p.score_id
             WHERE {filters}
          ORDER BY {practice.LOCAL_DATE_SQL} DESC, p.started_at DESC, p.id DESC
             LIMIT ?""",
        [*params, limit],
    ).fetchall()
    # How many matched, not just how many came back. A list that stops at the
    # limit and says nothing looks identical to a complete one, and a reader
    # totalling it would report less practice than there was - which is the one
    # direction this must never be wrong in.
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM practice_sessions p WHERE {filters}", params
    ).fetchone()["n"]
    return {
        "sessions": [practice.session_dict(r) for r in rows],
        "total": total,
        "truncated": total > len(rows),
    }


@router.get("/practice/summary", tags=[TAG_PRACTICE], response_model=PracticeSummaryOut)
def practice_summary():
    """The last seven days, for the library header.

    Counted over practice DAYS - today and the six before it - rather than over
    a rolling 168 hours of UTC timestamps, so this and the practice page cannot
    disagree about which sessions were "this week". `date('now')` is UTC, which
    can put the window's edge a few hours out for somebody far from Greenwich;
    over seven days that moves nothing anybody would notice, and unlike the
    goal endpoints this one has no period whose end has to be got exactly right.
    """
    conn = connect()
    # Scoped to the owner like every other practice query. Nothing else exists
    # to aggregate across today, but the day accounts arrive this is a site
    # that would silently total somebody else's practice into this one's - and
    # the schema's own comments ask for these to stay greppable and consistent
    # rather than correct by accident.
    week = conn.execute(
        f"""SELECT COALESCE(SUM(p.seconds), 0) AS total_seconds, COUNT(*) AS session_count
            FROM practice_sessions p
            WHERE p.owner = ? AND {practice.LOCAL_DATE_SQL} >= date('now', '-6 days')""",
        (DEFAULT_OWNER,),
    ).fetchone()
    top = conn.execute(
        f"""SELECT s.id, s.title, SUM(p.seconds) AS practice_seconds
            FROM practice_sessions p JOIN scores s ON s.id = p.score_id
            WHERE p.owner = ? AND {practice.LOCAL_DATE_SQL} >= date('now', '-6 days')
            GROUP BY p.score_id ORDER BY practice_seconds DESC LIMIT 5""",
        (DEFAULT_OWNER,),
    ).fetchall()
    return {
        "week_seconds": week["total_seconds"],
        "week_sessions": week["session_count"],
        "top_scores": [dict(r) for r in top],
    }


@router.get("/practice/history", tags=[TAG_PRACTICE], response_model=PracticeHistoryOut)
def practice_history(days: int = practice.DEFAULT_HISTORY_DAYS, today: str | None = None):
    """Where the time went over a stretch of days.

    A day at a time, a piece at a time, and a kind of work at a time, over a
    window as long as three months - which is the shape of question a reader
    has otherwise had to reassemble score by score. It states totals and never
    a trend, a streak, or a comparison between one stretch and another.
    """
    if not 1 <= days <= practice.MAX_HISTORY_DAYS:
        raise HTTPException(422, f"days must be between 1 and {practice.MAX_HISTORY_DAYS}")
    end = _today(today)
    start = end - timedelta(days=days - 1)
    conn = connect()
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        **practice.period_facts(conn, start.isoformat(), end.isoformat()),
        **practice.time_spent(conn, start.isoformat(), end.isoformat()),
    }


class GoalIn(BaseModel):
    """A whole goal. Replaces any goal already set for the same period.

    `period_start` may be omitted, in which case the goal is for the week
    containing `today` under the week_starts_on setting - which is what "set a
    goal for this week" means and what saves a client doing calendar
    arithmetic the server can do once.
    """

    period: str | None = None
    period_start: str | None = None
    target_days: Count | None = None
    target_minutes: Count | None = None
    scope: str | None = None
    score_id: Count | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)
    activity: str | None = None
    intent: str | None = Field(default=None, max_length=practice.MAX_INTENT_CHARS)


class GoalPatch(BaseModel):
    """A change to a goal already set.

    Targets are editable while the period runs, on purpose: seeing where you
    stand is only useful if the goal can still change the week rather than
    only judge it afterwards. `reflection` and `realistic` are the person's
    own account of how it went, and nothing else ever writes them.
    """

    target_days: Count | None = None
    target_minutes: Count | None = None
    scope: str | None = None
    score_id: Count | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)
    activity: str | None = None
    intent: str | None = Field(default=None, max_length=practice.MAX_INTENT_CHARS)
    reflection: str | None = Field(default=None, max_length=practice.MAX_REFLECTION_CHARS)
    realistic: str | None = None


# What a caller may state about a goal, which is what practice.normalise_goal
# takes. period_end is absent on purpose: it is derived from the start and the
# period length, so it can never be given as something other than what the
# period means.
_GOAL_INPUT_FIELDS = (
    "period",
    "period_start",
    "target_days",
    "target_minutes",
    "scope",
    "score_id",
    "activity",
    "intent",
)

# The stored columns, which is the above plus the derived period_end.
_GOAL_COLUMNS = (*_GOAL_INPUT_FIELDS, "period_end")


def _goal_row(conn, goal_id: int):
    row = conn.execute(
        "SELECT * FROM practice_goals WHERE id = ? AND owner = ?",
        (goal_id, DEFAULT_OWNER),
    ).fetchone()
    if not row:
        raise HTTPException(404, "goal not found")
    return row


def _normalise_goal(conn, fields: dict, allow_missing_score: bool = False) -> dict:
    if fields.get("score_id") is not None:
        _score_row(conn, fields["score_id"])
    try:
        return practice.normalise_goal(allow_missing_score=allow_missing_score, **fields)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.post("/practice/goals", tags=[TAG_PRACTICE], response_model=GoalOut)
def set_goal(body: GoalIn, today: str | None = None):
    """Set a goal for a period, replacing any goal already set for it - see
    the function's own comments for what replacing carries over and what it
    does not."""
    day = _today(today)
    fields = body.model_dump()
    if not fields.get("period_start"):
        fields["period_start"] = practice.week_start(day, _week_starts_on()).isoformat()
    with write_tx() as conn:
        values = _normalise_goal(conn, fields)
        # No two goals may share a day. The unique index below covers an
        # identical period; this covers a period that merely overlaps one,
        # which becomes reachable the moment the week-start preference changes
        # - the new grid's weeks are offset from the old grid's rather than
        # being different weeks. Overlapping goals are how the same practice
        # gets counted against two intentions and how two panels of one page
        # come to disagree about which goal this week has.
        clash = practice.overlapping_goal(
            conn,
            DEFAULT_OWNER,
            values["period_start"],
            values["period_end"],
            ignore_start=values["period_start"],
        )
        if clash is not None:
            raise HTTPException(
                409,
                f"a goal is already set for {clash['period_start']} to {clash['period_end']}, "
                f"which shares days with {values['period_start']} to {values['period_end']}. "
                "Adjust or remove that one first.",
            )
        # Setting a goal for a period that already has one REPLACES its
        # targets rather than adding a second goal or refusing. Changing your
        # mind about the week is the ordinary case, and a period with two
        # goals in it is a scorecard.
        #
        # The reflection goes with it. It was written about the intention being
        # replaced, and carrying it over would have the review ask whether a
        # goal was realistic and answer with words about a different one.
        columns = ", ".join(_GOAL_COLUMNS)
        placeholders = ", ".join("?" * len(_GOAL_COLUMNS))
        updates = ", ".join(f"{c} = excluded.{c}" for c in _GOAL_COLUMNS if c != "period_start")
        row = conn.execute(
            f"""INSERT INTO practice_goals(owner, {columns})
                VALUES (?, {placeholders})
                ON CONFLICT(owner, period_start) DO UPDATE SET
                    {updates}, reflection = NULL, realistic = NULL,
                    updated_at = datetime('now')
                RETURNING *""",
            [DEFAULT_OWNER, *(values[c] for c in _GOAL_COLUMNS)],
        ).fetchone()
        # Read back inside the transaction that wrote it. Outside, a delete
        # landing in the gap turns a successful write into a 404 about a row
        # this call had just created.
        return practice.goal_dict(conn, row, day)


# Declared before the {goal_id} routes so "current" is not parsed as an id -
# FastAPI matches in declaration order.
@router.get("/practice/goals/current", tags=[TAG_PRACTICE], response_model=CurrentGoalOut)
def current_goal(today: str | None = None):
    """The goal covering today, or nothing.

    `goal: null` is an answer, not an error: having no goal set is an ordinary
    state and a perfectly good week can happen inside it.
    """
    day = _today(today)
    conn = connect()
    row = conn.execute(
        """SELECT * FROM practice_goals
            WHERE owner = ? AND period_start <= ? AND period_end >= ?
         ORDER BY period_start DESC LIMIT 1""",
        (DEFAULT_OWNER, day.isoformat(), day.isoformat()),
    ).fetchone()
    week = practice.week_start(day, _week_starts_on())
    return {
        "goal": practice.goal_dict(conn, row, day) if row else None,
        "today": day.isoformat(),
        # What a goal set right now would be for, so a client offering to set
        # one does not have to work out the week itself.
        "week_starts_on": _week_starts_on(),
        "week_start": week.isoformat(),
        "week_end": (week + timedelta(days=6)).isoformat(),
    }


@router.get("/practice/goals", tags=[TAG_PRACTICE], response_model=GoalListOut)
def list_goals(limit: int = practice.MAX_REVIEW_WEEKS, today: str | None = None):
    """Recent goals, most recent period first, each with its progress."""
    day = _today(today)
    if not 1 <= limit <= practice.MAX_REVIEW_WEEKS:
        raise HTTPException(422, f"limit must be between 1 and {practice.MAX_REVIEW_WEEKS}")
    conn = connect()
    rows = conn.execute(
        """SELECT * FROM practice_goals WHERE owner = ?
        ORDER BY period_start DESC LIMIT ?""",
        (DEFAULT_OWNER, limit),
    ).fetchall()
    return {"goals": [practice.goal_dict(conn, r, day) for r in rows]}


@router.patch("/practice/goals/{goal_id}", tags=[TAG_PRACTICE], response_model=GoalOut)
def patch_goal(goal_id: RowId, patch: GoalPatch, today: str | None = None):
    """Change a goal's targets, or record a reflection on how its period
    went. `period_start` cannot be patched - see the function's own
    comments."""
    day = _today(today)
    with write_tx() as conn:
        row = _goal_row(conn, goal_id)
        fields = {c: row[c] for c in _GOAL_INPUT_FIELDS}
        for name in patch.model_fields_set:
            if name in fields:
                fields[name] = getattr(patch, name)
        # period_start is not patchable: it is the goal's identity under the
        # unique index, and moving it would silently re-point a goal at a
        # different week's practice. Setting a goal for the other week is what
        # POST does.
        #
        # A goal whose piece has since been deleted can no longer be counted,
        # but its reflection can still be written - a deleted file must not
        # lock the record of an intention that was genuinely formed.
        values = _normalise_goal(
            conn,
            fields,
            allow_missing_score=row["scope"] == "score" and row["score_id"] is None,
        )
        try:
            reflection = practice.normalise_reflection(
                reflection=patch.reflection
                if "reflection" in patch.model_fields_set
                else row["reflection"],
                realistic=patch.realistic
                if "realistic" in patch.model_fields_set
                else row["realistic"],
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        assignments = ", ".join(f"{c} = ?" for c in _GOAL_COLUMNS if c != "period_start")
        updated = conn.execute(
            f"""UPDATE practice_goals
                   SET {assignments}, reflection = ?, realistic = ?,
                       updated_at = datetime('now')
                 WHERE id = ? AND owner = ? RETURNING *""",
            [
                *(values[c] for c in _GOAL_COLUMNS if c != "period_start"),
                reflection["reflection"],
                reflection["realistic"],
                goal_id,
                DEFAULT_OWNER,
            ],
        ).fetchone()
        return practice.goal_dict(conn, updated, day)


@router.delete(
    "/practice/goals/{goal_id}", tags=[TAG_PRACTICE], response_model=GoalDeleteOut
)
def delete_goal(goal_id: RowId):
    """Forget a goal.

    Deleting a goal deletes an intention, never the practice: the sessions in
    its period are untouched and the week still shows what was done. A goal
    whose week has ended is NOT deleted by anything automatic - it stays with
    whatever was written about it, because a record of what someone meant to do
    is the only thing that makes the next goal a better one.
    """
    with write_tx() as conn:
        _goal_row(conn, goal_id)
        conn.execute(
            "DELETE FROM practice_goals WHERE id = ? AND owner = ?", (goal_id, DEFAULT_OWNER)
        )
    return {"deleted": goal_id}


@router.get("/practice/review", tags=[TAG_PRACTICE], response_model=PracticeReviewOut)
def practice_review(weeks: int = practice.DEFAULT_REVIEW_WEEKS, today: str | None = None):
    """Recent weeks, each stating what happened in it.

    Every week in the window appears, whether or not a goal was set for it -
    a week with no goal is not a gap in a record, and a week whose goal was not
    reached is reported with the same fields and in the same order as one whose
    goal was. Nothing here compares one week to another, and nothing carries a
    best or a run of anything: that is the machinery by which a good month
    becomes the standard a bad month is punished against.
    """
    if not 1 <= weeks <= practice.MAX_REVIEW_WEEKS:
        raise HTTPException(422, f"weeks must be between 1 and {practice.MAX_REVIEW_WEEKS}")
    day = _today(today)
    starts_on = _week_starts_on()
    conn = connect()
    out = []
    for period in practice.review_periods(
        conn, DEFAULT_OWNER, practice.week_start(day, starts_on), weeks, starts_on
    ):
        start, end, row = period["period_start"], period["period_end"], period["goal"]
        # The week's own facts, unscoped, even when the goal was about one
        # piece: "I did not reach the goal but I practised four days" is true
        # and is the sort of thing a scoped-only view hides. Reused as the
        # goal's progress when the goal counts everything, because then the two
        # are the same query asked twice.
        facts = practice.period_facts(conn, start, end)
        goal = None
        if row is not None:
            goal = practice.goal_dict(
                conn, row, day, facts=facts if row["scope"] == "all" else None
            )
        out.append(
            {
                "period": "week",
                "period_start": start,
                "period_end": end,
                "status": "running" if start <= day.isoformat() <= end else "past",
                "goal": goal,
                "facts": facts,
                **practice.time_spent(conn, start, end),
            }
        )
    return {"today": day.isoformat(), "week_starts_on": starts_on, "weeks": out}


@router.get(
    "/scores/{score_id}/file",
    tags=[TAG_LIBRARY],
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}, "application/octet-stream": {}}}},
)
def get_file(score_id: RowId):
    """The score's own file - a PDF or, for anything else FILE_TYPES admits,
    an octet stream. No response_model: this returns a FileResponse, whose
    body is the file's bytes rather than JSON.

    `response_class=FileResponse` matters beyond documentation: without it
    FastAPI assumes `application/json` is on offer alongside the real
    content types declared in `responses=` above, and a codegen reading
    /openapi.json would believe this endpoint might hand back JSON. With it,
    only the real content types are advertised - see
    test_binary_routes_do_not_advertise_a_json_content_type."""
    conn = connect()
    row = _score_row(conn, score_id)
    path = LIBRARY_DIR / row["path"]
    if not path.is_file():
        raise HTTPException(404, "file missing from library")
    media = "application/pdf" if row["file_type"] == "pdf" else "application/octet-stream"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get(
    "/scores/{score_id}/thumb",
    tags=[TAG_LIBRARY],
    response_class=FileResponse,
    responses={200: {"content": {"image/png": {}}}},
)
def get_thumb(score_id: RowId):
    """The score's cached first-page thumbnail, if one has been generated.
    `response_class=FileResponse` - see get_file's docstring just above."""
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


# Rule 8 conformance, as the top level of every transcription response.
#
# These are STATED on every response rather than included only when known,
# and that is the whole point of them. A hand edit stores no confidence at
# all, so on the omitting version of this a client that merged the PUT
# response over the transcription it already held kept the figures from
# BEFORE the edit - and went on reporting bars as defective after the edit
# that fixed them, which is the one thing this project must never do. An
# explicit "not recorded" overwrites such a merge; a missing key is silently
# preserved by it.
#
# None means not recorded: a row stored before these were persisted, or a
# hand edit, whose content nothing has measured. It does NOT mean zero -
# zero would claim every bar was measured and every one of them added up,
# which is a far stronger statement than either row can support. Anything
# wanting real figures for edited content has to measure that content; it
# cannot inherit them from the extraction it replaced.
#
# `bars_padded` and `bars_unread` belong with them for the same reason: a bar
# holding silence that was deduced from the meter rather than read from the
# page, or holding nothing that was read at all, is not the reading the other
# figures make it look like, and neither is recoverable from them.
#
# `notes_no_stem` / `staves_no_stem` belong with them too. A filled notehead
# whose stem the decoder never found has no flag or beam to count, so it was
# emitted at the longest duration its notehead alone allows - a floor, not a
# reading, and one that always errs long. That is the same kind of fact as a
# padded bar and is recoverable from nothing else here.
#
# `dots_unassigned` / `staves_dots_unassigned` too: an augmentation dot that
# bound to no note is left out of every note's count rather than forced onto
# the nearest candidate, and that is a fact about the reading nothing else
# stored here can reconstruct. `dots_unassigned_no_candidate` and
# `dots_unassigned_eliminated` split that total by WHY, since it is two
# different claims and not one: the first never had a notehead or rest at an
# offset an engraver would use at all; the second reached one, but it had
# already been given a dot at a different, conflicting position - a note
# that already has its own dot, not one with nothing nearby (see
# tabextract._rhythm_report / glyph._assign_dots).
_BAR_KEYS = ("bars_overfull", "bars_short", "bars_defective", "bars_measured",
             "bars_padded", "bars_unread", "notes_no_stem", "staves_no_stem",
             "dots_unassigned", "dots_unassigned_no_candidate",
             "dots_unassigned_eliminated", "staves_dots_unassigned",
             # Repeat barlines and volta brackets read only partly, and so
             # omitted from the emitted MusicXML (issue #134 Rule 15 / S5) -
             # the same "recoverable from nothing else here" reasoning as
             # notes_no_stem / dots_unassigned above. Producing these on
             # ExtractionResult without adding them here was the recurring
             # server-half-only defect by name (adversarial review, blocker
             # 3): to_dict() is never called in server/, so the prose half
             # of the disclosure reached a reader and the structured half
             # reached nobody.
             "repeats_unread", "endings_unread", "endings_truncated",
             "form_marks_unanchored", "endings_incomplete",
             # `unison_digits_shared` (issue #137): notes given the fret
             # number the tablature printed for a coincident notehead at
             # their own position rather than one printed for them - the
             # right reading of a unison shared between two voices, and an
             # inference about which string those notes are on that nothing
             # else stored here can reconstruct. Added HERE and not only to
             # ExtractionResult/to_dict deliberately - see the note above
             # about the disclosure that reaches a reader in prose and
             # reaches nobody in data.
             "unison_digits_shared",
             # `coincident_unsplit_pairs` / `staves_coincident_unsplit`
             # (issue #116): a unison shared between two voices whose second,
             # distinct candidate stem could not be found, so the two copies
             # of the coincident notehead could not be told apart - the exact
             # "recoverable from nothing else here" case the disclosures
             # above exist for. Reached ExtractionResult and to_dict() with
             # #116 itself but never HERE (issue #143), so a reloaded
             # transcription reported every disclosure the decoder made
             # except this one.
             "coincident_unsplit_pairs", "staves_coincident_unsplit",
             # Navigation marks read but not written in full (issue #134
             # phase 2, Rule 16), here for the same reason the repeat/volta
             # keys above are: a caller with only the API must not have to
             # infer the caveat from a file that looks complete.
             "nav_marks_unanchored", "nav_marks_unresolved",
             # `staves_spacing_rhythm` / `staves_degraded_rhythm` (issue
             # #117): how many staff systems' durations came from the
             # horizontal gaps between noteheads instead of from the
             # noteheads, and how many were read from the engraving with
             # something on them left unread. `rhythm_provenance` on the
             # extraction result already counted both, and that field is
             # stored nowhere and read by nothing - so a reader reloading a
             # transcription had `spacing_bars` (which bars) with no count
             # beside it and no row in the disclosure panel to put it on.
             # Spacing-derived rhythm is only as good as the engraver's
             # spacing being proportional, which a justified or hand-adjusted
             # system is not, so this is exactly the kind of caveat that must
             # not be recoverable only from prose.
             "staves_spacing_rhythm", "staves_degraded_rhythm",
             # `meter_digits_unreadable` (issue #129): printed time signatures
             # refused because a glyph the decoder has no category for sat
             # among their digits. It is the count of a REFUSAL, and the only
             # figure here that says a meter this score prints was not read -
             # `time_signature_source` says how the meter that IS reported was
             # obtained, and cannot say that a different, unread one exists.
             "meter_digits_unreadable",
             # `systems_unread` (issue #152): a SYSTEM whose bars were never
             # read - the only disclosure here about music that is ABSENT
             # from the transcription rather than imperfect in it. It has to
             # survive a reload for the same reason the rest do, and more
             # sharply: every other figure in this blob describes the
             # systems that WERE read, so a reader without this one is
             # holding a set of numbers that silently excludes a page's
             # worth of music. Losing a system can even move
             # `bars_defective` down.
             "systems_unread")

# WHICH bars those were, as data and not only inside the warning prose. The
# prose names them, but it caps the list, and the profile document states that a
# consumer summing the `<forward>` durations in the file should get
# `inferred_rest_quarters` - a claim the application has to actually make good
# on. `padded_bars` / `unread_bars` are lists of bar numbers;
# `inferred_rest_quarters` is a quarter-note count and can be fractional.
#
# `spacing_bars` / `degraded_bars` are the same kind of list for the same
# reason: which bars' durations came from the gaps between noteheads rather
# than the noteheads, and which came off a staff something on it could not be
# read. `rhythm_provenance` on the extraction result says how many staves
# resolved each way, but that field is not one this module stores or exposes
# - only these bar-number lists carry the fact out to a consumer, and a bar
# number is what a reader can carry back to the PDF.
_BAR_LIST_KEYS = ("padded_bars", "unread_bars", "spacing_bars", "degraded_bars",
                   # WHICH bars carried a repeat/volta mark that could not be
                   # read in full - the bar-number half of the repeats_unread
                   # / endings_unread / endings_truncated / form_marks_unanchored
                   # keys above.
                   "repeats_unread_bars", "endings_unread_bars",
                   "endings_truncated_bars", "form_marks_unanchored_bars",
                   # WHICH bars carry a navigation instruction whose jump
                   # target the score does not draw. `nav_marks_unanchored`
                   # has no list of its own on purpose: a mark with no bar
                   # to name has no bar number to report.
                   "nav_marks_unresolved_bars",
                   # WHICH PAGES a lost system was on (issue #152). This is a
                   # page list where its neighbours are bar lists, and that is
                   # forced by the defect: a system that was never read has no
                   # bar numbers, because bar numbers come from the grid its
                   # bars never entered. A page is the coordinate that does
                   # exist, and the one a reader can turn to.
                   "systems_unread_pages")
_BAR_AMOUNT_KEYS = ("inferred_rest_quarters",)


def _stored_count(value):
    """A stored bar count, or None if the blob holds something else there. bool
    is a subclass of int and would otherwise pass as a count."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _stored_bar_list(value):
    """A stored list of bar numbers, or None. Validated element by element: a
    list with a string in it is not a list of bar numbers, and passing it
    through would hand a caller something to do arithmetic on that cannot be."""
    if not isinstance(value, list):
        return None
    return value if all(_stored_count(n) is not None for n in value) else None


def _stored_amount(value):
    """A stored quarter-note count, or None. Accepts int as well as float - 12
    quarters is a whole number and JSON writes it as one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)

# WHAT WAS READ OFF THE PAGE, AND WHAT WAS ASSUMED - stored, for the same
# reason the bar counts are, and STATED on every response for the same reason
# they are.
#
# The extractor works out whether it read the meter, the key and the tuning or
# fell back to an assumption, and until this was persisted that answer existed
# only on the POST /transcribe response and died there. Every later read of the
# same row - which is every ordinary visit to a score - got the assumed value
# with nothing saying it was assumed, which is precisely the thing this project
# exists not to do (issue #103). It is not recoverable from the warning prose
# either: the meter's absence is narrated, the key's is narrated differently,
# and a tuning nobody recognised produces no warning at all.
#
# The values travel with their provenance rather than being stored apart from
# it. "assumed 4/4" is one fact, not two, and a reader that has the source
# string without the digits it qualifies can only say something vaguer than
# what is known.
#
# None means not recorded - a hand-edited row, or one extracted before this was
# stored. It does not mean "standard tuning" or "no key signature", both of
# which are real readings this must not invent.
_PROVENANCE_KEYS = (
    "time_signature",
    "time_signature_source",
    "key_fifths",
    "key_signature_source",
    "tuning",
    "tuning_label",
    # Tuning instructions the extractor recognised on the page and did NOT
    # apply - see tabextract.unread_tuning_instructions. Stored with the tuning
    # because it is what stops the tuning being describable as read: a text
    # match on one tuning name is recognition of a label, not a reading of the
    # tuning, and it cannot be reported as one while the same page carries
    # further instructions nobody parsed.
    "tuning_unread",
)


def _transcription_dict(row) -> dict:
    d = dict(row)
    blob = None
    if d.get("confidence"):
        try:
            blob = json.loads(d["confidence"])
        except (TypeError, ValueError):
            blob = None  # leave the column as the raw text it turned out to be
        else:
            d["confidence"] = blob
    stored = blob if isinstance(blob, dict) else {}

    warnings = stored.get("warnings")
    d["warnings"] = warnings if isinstance(warnings, list) else []
    for key in _BAR_KEYS:
        d[key] = _stored_count(stored.get(key))
    for key in _BAR_LIST_KEYS:
        d[key] = _stored_bar_list(stored.get(key))
    for key in _BAR_AMOUNT_KEYS:
        d[key] = _stored_amount(stored.get(key))
    for key in _PROVENANCE_KEYS:
        d[key] = stored.get(key)
    return d


@router.get(
    "/scores/{score_id}/transcription", tags=[TAG_TRANSCRIPTION], response_model=TranscriptionOut
)
def get_transcription(score_id: RowId):
    """The score's transcription - a hand edit if one exists, otherwise the
    extraction - see _transcription_row."""
    conn = connect()
    _score_row(conn, score_id)
    row = _transcription_row(conn, score_id)
    if not row:
        raise HTTPException(404, "no transcription for this score")
    return _transcription_dict(row)


@router.get(
    "/scores/{score_id}/transcription/analysis",
    tags=[TAG_TRANSCRIPTION],
    response_model=TranscriptionAnalysisOut,
)
def get_transcription_analysis(score_id: RowId):
    """Cheap triage of whether this score is worth extracting - see
    tabextract.analyze."""
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


def _validate_time_signature(ts: tuple[int, int]) -> None:
    """Reject a signature alphaTab could not render. The rule itself lives
    with the decoder (glyph_rhythm.time_signature_is_valid), which has to
    apply it to signatures it reads off the page for exactly the same
    reason - a signature that reaches \\ts is also STORED, and alphaTab
    throws on e.g. `\\ts 3 12`, so a bad one makes the saved transcription
    permanently unrenderable. Two copies of the rule would be two chances
    to disagree."""
    num, den = ts
    if not 1 <= num <= 32:
        raise HTTPException(422, "time_signature numerator must be between 1 and 32")
    if den not in VALID_TS_DENOMINATORS:
        raise HTTPException(
            422, f"time_signature denominator must be one of {sorted(VALID_TS_DENOMINATORS)}")


@router.post(
    "/scores/{score_id}/transcribe",
    tags=[TAG_TRANSCRIPTION],
    response_model=TranscribeResultOut,
)
def transcribe(score_id: RowId, body: TranscribeIn | None = Body(default=None)):
    """Extract tablature from this score's PDF and store it, replacing any
    previous extraction (never a hand edit - see the function's own
    comments). Only valid for pdf scores with a tab staff; an unreadable
    time signature or a non-extractable pdf is a 422."""
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
    #
    # MusicXML REPLACES alphaTex as the stored format rather than sitting
    # beside it: the unique index is (score_id, source) with no format in it,
    # so two extracted rows in different formats could not coexist anyway, and
    # a canonical output that is only sometimes canonical is not one. The
    # `format` column is what makes the swap safe without a data migration -
    # it is read back and dispatched on (see the web TabViewer), so rows
    # written before this change keep rendering as the alphaTex they are, and
    # a hand-edited row stays in whichever format it was edited in until its
    # author edits it again.
    # The Rule 8 figures are STORED, not only echoed on this response, because
    # they cannot be recovered later from the warning prose. A polyphonic bar
    # can hold one voice over its meter and another under it, so it counts into
    # both `overfull` and `short`: their sum double-counts such a bar and can
    # exceed the number of bars measured. `defective` counts each wrong bar
    # once and is the only figure safe to compare against `measured`, and no
    # arithmetic over the two warning sentences recovers it. A reader that
    # reloads a transcription and has only the prose can therefore either say
    # nothing about bars or say something untrue - so it gets the numbers.
    # The meter, key and tuning are stored WITH how each was obtained, for the
    # reasons on _PROVENANCE_KEYS: an assumption that only says so on the
    # response that created it is an assumption nobody will ever be told about.
    confidence_json = json.dumps(
        {
            "warnings": result.warnings,
            "confidence": result.confidence,
            "bars_overfull": result.bars_overfull,
            "bars_short": result.bars_short,
            "bars_defective": result.bars_defective,
            "bars_measured": result.bars_measured,
            "bars_padded": result.bars_padded,
            "bars_unread": result.bars_unread,
            "padded_bars": result.padded_bars,
            "unread_bars": result.unread_bars,
            "inferred_rest_quarters": result.inferred_rest_quarters,
            "notes_no_stem": result.notes_no_stem,
            "staves_no_stem": result.staves_no_stem,
            "dots_unassigned": result.dots_unassigned,
            "dots_unassigned_no_candidate": result.dots_unassigned_no_candidate,
            "dots_unassigned_eliminated": result.dots_unassigned_eliminated,
            "staves_dots_unassigned": result.staves_dots_unassigned,
            # unison_digits_shared (issue #137, gap found in #146): reached
            # ExtractionResult, to_dict() and _BAR_KEYS above with #137 itself,
            # but never named here - the only dict this function actually
            # writes into storage. to_dict() is never called in server/, so
            # its presence there carried no production weight: the warning
            # prose reached a reader, the structured count reached nobody.
            "unison_digits_shared": result.unison_digits_shared,
            # coincident_unsplit_pairs / staves_coincident_unsplit (issue
            # #116, #143): _BAR_KEYS above only controls what a stored blob
            # is READ back as - this dict is what gets written into it in the
            # first place, and it named every other _BAR_KEYS entry by hand
            # already but never picked these two up, so the round trip broke
            # here even after _BAR_KEYS did the reading half. Without this,
            # a score with real unsplit pairs (e.g. Ronfaure, 15 per #116)
            # stores None for both and #143's own verification plan fails.
            "coincident_unsplit_pairs": result.coincident_unsplit_pairs,
            "staves_coincident_unsplit": result.staves_coincident_unsplit,
            "spacing_bars": result.spacing_bars,
            "degraded_bars": result.degraded_bars,
            # The COUNTS beside those two bar lists (issue #117). The lists
            # have been stored since they existed; the counts they belong to
            # lived only inside `rhythm_provenance`, which nothing stores and
            # nothing reads, so the fact that a staff's durations came out of
            # the gaps between noteheads reached the disclosure panel through
            # no field at all.
            "staves_spacing_rhythm": result.staves_spacing_rhythm,
            "staves_degraded_rhythm": result.staves_degraded_rhythm,
            # A printed meter refused because a glyph with no category sat
            # among its digits (issue #129).
            "meter_digits_unreadable": result.meter_digits_unreadable,
            "repeats_unread": result.repeats_unread,
            "repeats_unread_bars": result.repeats_unread_bars,
            "endings_unread": result.endings_unread,
            "endings_unread_bars": result.endings_unread_bars,
            "endings_truncated": result.endings_truncated,
            "endings_truncated_bars": result.endings_truncated_bars,
            "form_marks_unanchored": result.form_marks_unanchored,
            "form_marks_unanchored_bars": result.form_marks_unanchored_bars,
            "endings_incomplete": result.endings_incomplete,
            "nav_marks_unanchored": result.nav_marks_unanchored,
            "nav_marks_unresolved": result.nav_marks_unresolved,
            "nav_marks_unresolved_bars": result.nav_marks_unresolved_bars,
            # systems_unread / systems_unread_pages (issue #152) - written
            # here, the only path into storage, and not merely produced on
            # ExtractionResult. That gap is the one #143 and #146 each shipped
            # once; the structural guard in test_transcription_api.py now
            # fails by name if a _BAR_KEYS entry never reaches this dict.
            "systems_unread": result.systems_unread,
            "systems_unread_pages": result.systems_unread_pages,
            "time_signature": list(result.time_signature) if result.time_signature else None,
            "time_signature_source": result.time_signature_source,
            "key_fifths": result.key_fifths,
            "key_signature_source": result.key_signature_source,
            "tuning": result.tuning,
            "tuning_label": result.tuning_label,
            "tuning_unread": result.tuning_unread,
        }
    )
    with tx() as tx_conn:
        tx_conn.execute(
            """INSERT INTO transcriptions(score_id, format, content, source, confidence, updated_at)
               VALUES (?, ?, ?, 'extracted', ?, datetime('now'))
               ON CONFLICT(score_id, source) DO UPDATE SET
                   format = excluded.format, content = excluded.content,
                   confidence = excluded.confidence, updated_at = datetime('now')""",
            (score_id, TRANSCRIPTION_FORMAT, result.musicxml, confidence_json),
        )

    conn = connect()
    saved = conn.execute(
        "SELECT * FROM transcriptions WHERE score_id = ? AND source = 'extracted'", (score_id,)
    ).fetchone()
    # `warnings`, the Rule 8 conformance figures and the meter/key/tuning
    # provenance are NOT set from `result` here. They come back out of the row
    # that was just written, so that this response and a later GET of the same
    # row are answered from one source rather than two that could drift.
    # Everything below is extraction detail that is genuinely only available on
    # this response.
    d = _transcription_dict(saved)
    d["bars"] = result.bars
    d["beats"] = result.beats
    d["notes"] = result.notes
    d["tempo"] = result.tempo
    return d


class TranscriptionEditIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_TRANSCRIPTION_CHARS)
    # Optional because the stored format changed once and could again: a
    # client that knows what it edited should say so, and one that does not
    # gets the answer sniffed from the content rather than assumed. Storing
    # the wrong format here is not a cosmetic mistake - the renderer dispatches
    # on it, so a MusicXML document labelled alphatex fails to load at all.
    format: str | None = None


def _sniff_transcription_format(content: str) -> str:
    """Which format an edited transcription is written in, read off the content.

    This is the only thing that decides an edit's format, because the format of
    the row being edited says nothing about what was typed into it: pasting
    alphaTex over a MusicXML transcription used to store it as MusicXML, and
    the viewer then handed it to the MusicXML loader and rendered nothing.

    The test is that the content begins with '<'. Every MusicXML document does,
    whether it opens with an XML declaration, a DOCTYPE, a comment or its root
    element, and alphaTex has no form in which it can - metadata lines begin
    with a backslash, beats with a colon or a fret number.
    """
    return "musicxml" if content.lstrip().startswith("<") else "alphatex"


@router.put(
    "/scores/{score_id}/transcription", tags=[TAG_TRANSCRIPTION], response_model=TranscriptionOut
)
def save_transcription(score_id: RowId, body: TranscriptionEditIn):
    """Save a hand edit, replacing any hand edit already stored - never the
    extraction. `format` is sniffed from the content when not given - see
    _sniff_transcription_format."""
    fmt = body.format or _sniff_transcription_format(body.content)
    if fmt not in VALID_TRANSCRIPTION_FORMATS:
        raise HTTPException(
            422, f"format must be one of {sorted(VALID_TRANSCRIPTION_FORMATS)}")
    with tx() as conn:
        _score_row(conn, score_id)
        conn.execute(
            """INSERT INTO transcriptions(score_id, format, content, source, updated_at)
               VALUES (?, ?, ?, 'edited', datetime('now'))
               ON CONFLICT(score_id, source) DO UPDATE SET
                   format = excluded.format, content = excluded.content,
                   updated_at = datetime('now')""",
            (score_id, fmt, body.content),
        )
    conn = connect()
    row = conn.execute(
        "SELECT * FROM transcriptions WHERE score_id = ? AND source = 'edited'", (score_id,)
    ).fetchone()
    return _transcription_dict(row)


@router.delete(
    "/scores/{score_id}/transcription", tags=[TAG_TRANSCRIPTION], response_model=TranscriptionOut
)
def delete_transcription(score_id: RowId):
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


@router.post("/scan", tags=[TAG_SCAN], response_model=ScanTriggerOut)
def trigger_scan():
    """Start a library scan if one is not already running, and return the
    status left behind by whichever pass is now current."""
    started = scanner.start_scan()
    return {"started": started, **scanner.scan_status()}


@router.get("/scan/status", tags=[TAG_SCAN], response_model=ScanStatusOut)
def get_scan_status():
    """Where the most recent (or currently running) scan stands."""
    return scanner.scan_status()


class ScanAcknowledgement(BaseModel):
    # The token from the refusal being acknowledged. Consent has to be about
    # something specific, and this is what says which something - see
    # scanner._acknowledge_token.
    token: str = Field(min_length=1, max_length=128)


@router.post("/scan/acknowledge", tags=[TAG_SCAN], response_model=ScanTriggerOut)
def acknowledge_scan(body: ScanAcknowledgement):
    """Say, once, that a refused reconciliation was meant.

    A guard with no way past it is its own defect, and a worse one than what it
    prevents: the same paths are unmatched on every subsequent pass, so a person
    who genuinely pruned their library would be refused for ever, with an error
    logged on every startup and no way to clear the rows by hand either.

    The token is checked against the live evidence inside the scan rather than
    here, because the library can change between a person reading a message and
    pressing a button. A token that no longer describes what is there does not
    apply, and the scan refuses again with the new figures.
    """
    status = scanner.scan_status()
    if not status["refused"] or not status["acknowledge_token"]:
        raise HTTPException(409, "there is no refused scan to acknowledge")
    if body.token != status["acknowledge_token"]:
        raise HTTPException(
            409,
            "that acknowledgement is for a different set of missing files than the one "
            "Fermata is now looking at - re-read the current scan status and confirm that",
        )
    started = scanner.start_scan(acknowledge=body.token)
    return {"started": started, **scanner.scan_status()}


_SAFE_SEGMENT = re.compile(r"^[^/\\]+$")


@router.post("/upload", tags=[TAG_LIBRARY], response_model=UploadOut)
async def upload(file: UploadFile, folder: str = "Uploads"):
    """Save a file into the library under `folder` (created if needed) and
    trigger a scan to pick it up. `folder` may not contain `..` or an
    absolute path segment."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in FILE_TYPES:
        raise HTTPException(422, f"unsupported file type {suffix!r}")
    parts = [p for p in folder.replace("\\", "/").split("/") if p and p != ".."]
    if not all(_SAFE_SEGMENT.match(p) for p in parts):
        raise HTTPException(422, "invalid folder")
    name = Path(file.filename).name
    # The library ROOT is never created here, and this check is the reason an
    # upload cannot undo the one at startup. ensure_dirs refuses to invent a
    # missing library folder because an empty one is indistinguishable from a
    # library with nothing in it (#95) - and mkdir(parents=True) on a subfolder
    # would have created the root on the way, turning that loud, harmless,
    # self-correcting refusal into a silent start against an almost-empty
    # library. In a container it is worse than pointless: the write lands in the
    # image layer at the mountpoint, invisible from the host and gone on the next
    # start.
    if not LIBRARY_DIR.is_dir():
        raise HTTPException(
            503,
            f"the library folder {LIBRARY_DIR} is not there, so there is nowhere to put "
            "this file. Fermata does not create it - a missing library folder is usually a "
            "drive or volume that did not mount, and creating an empty one would hide "
            "that. See docs/deployment.md.",
        )
    dest_dir = LIBRARY_DIR.joinpath(*parts)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    scanner.start_scan()
    return {"saved": str(dest.relative_to(LIBRARY_DIR))}


# ---------------------------------------------------------------------------
# MANAGING THE LIBRARY: moving, renaming, deleting and reorganising (#56).
#
# THIS IS THE ONE PART OF FERMATA THAT WRITES TO SOMEBODY'S OWN FILES. Every
# other endpoint reads the library and writes only to Fermata's own database.
# These move, rename and delete a person's sheet music, and getting one wrong
# loses something that is not ours to lose. Five rules follow from that, and
# every route below obeys all five:
#
#   1. NOTHING IS WRITTEN OUTSIDE THE LIBRARY FOLDER. Every destination goes
#      through _safe_parts and then _resolve_in_library, which is a check on the
#      RESOLVED path rather than on the text of it - so a symlink pointing out
#      of the library is refused as well as a `..`.
#
#   2. DELETING IS A MOVE, NOT A DELETE. A deleted score's file goes to a trash
#      folder inside the library and its row is marked, keeping the practice
#      history, goals, tags and transcription attached (see db.py's notes on
#      deleted_at). Destroying anything takes a second, separate request to a
#      different route that says what it destroys.
#
#   3. NOTHING IS DESTROYED AS A SIDE EFFECT OF AN ORGANISATIONAL CHANGE. A
#      move that would land on an existing file is refused; it never overwrites.
#      A batch whose plan contains one blocked line applies none of it.
#
#   4. A BULK OPERATION IS A DRY RUN UNTIL SOMEBODY SAYS OTHERWISE. `dry_run`
#      defaults to true on both routes that touch more than one score, so the
#      obvious call - and any client that forgets the flag exists - shows the
#      plan rather than performing it.
#
#   5. THE SCORE ROW FOLLOWS THE FILE BY CONTENT HASH, which is the identity
#      test the scanner already uses, applied to a file whose row we already
#      know (see _relink_moved_file). There is no second notion of what makes a
#      file "the same score".
# ---------------------------------------------------------------------------

# What a single path segment may be. No separators (those are what splits the
# string in the first place), no empty segment, and nothing that is only dots -
# "." and ".." are the two that escape a directory, and a segment of "..." is
# not a name anybody meant to type either. Control characters are excluded
# because a newline in a filename is a filename that cannot be read back out of
# a log or a listing.
_VALID_SEGMENT = re.compile(r"^(?!\.+$)[^/\\\x00-\x1f]+$")

# The move dialog offers folders to move into, and a library with a deep tree
# would otherwise hand a tablet a list thousands long. Deep enough for the
# Collection/Composer/Series/... layout parse_path describes, and then some.
MAX_FOLDER_DEPTH = 8


def _library_dir():
    """The library root, read through the module global rather than captured.

    api.py binds LIBRARY_DIR by value at import (`from .config import ...`), and
    the tests repoint that binding - so every function here has to read it at
    call time or it will happily reorganise the developer's real library from
    inside a test run.
    """
    return LIBRARY_DIR


def _require_library():
    """Refuse to touch anything if the library folder is not there.

    Same reasoning as upload()'s check: a missing library folder is a mount that
    did not appear, and this is the last moment at which a move can decline
    rather than write into an empty directory that will vanish with the
    container.
    """
    root = _library_dir()
    if not root.is_dir():
        raise HTTPException(
            503,
            f"the library folder {root} is not there, so there is nothing to reorganise. "
            "This is usually a drive or volume that did not mount - see "
            "docs/deployment.md. Nothing has been changed.",
        )
    return root


def _safe_parts(folder: str, field: str = "folder") -> tuple[str, ...]:
    """Split a client-supplied folder into segments, or refuse it.

    REFUSES rather than sanitises, which is the opposite of what upload() does
    with the same shape of input (it silently drops `..`). The difference is
    what happens next: an upload lands a new file somewhere slightly unexpected,
    while a move takes a file somebody already has and puts it there. Silently
    moving a score to a folder other than the one that was asked for is not a
    smaller failure than refusing.
    """
    raw = (folder or "").strip().replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise HTTPException(422, f"{field} must be inside the library, not an absolute path")
    parts = tuple(p for p in raw.split("/") if p)
    for part in parts:
        if not _VALID_SEGMENT.match(part):
            raise HTTPException(
                422,
                f"{field} segment {part!r} is not a usable folder name - no separators, no "
                "'..', and no control characters",
            )
    if parts and parts[0] == scanner.TRASH_DIR_NAME:
        raise HTTPException(
            422,
            f"{scanner.TRASH_DIR_NAME} is Fermata's trash folder, not a place to put scores. "
            "Delete a score to send it there, and restore it to bring it back.",
        )
    return parts


def _safe_filename(name: str) -> str:
    """A file name, with its extension checked against FILE_TYPES.

    The extension has to stay something the scanner recognises: renaming a PDF
    to `.txt` would leave a file the library can no longer see, which is a
    deletion wearing a rename's clothes.
    """
    cleaned = (name or "").strip()
    if not cleaned or not _VALID_SEGMENT.match(cleaned):
        raise HTTPException(
            422,
            "filename is not a usable file name - no folders, no '..', and no control "
            "characters",
        )
    suffix = Path(cleaned).suffix.lower()
    if suffix not in FILE_TYPES:
        raise HTTPException(
            422,
            f"filename must keep a score file extension ({', '.join(sorted(FILE_TYPES))}); "
            f"{suffix or 'no extension'} is not one Fermata can read",
        )
    return cleaned


def _resolve_in_library(rel: str) -> Path:
    """The absolute path for a library-relative one, checked to be inside it.

    The check is on the RESOLVED path, so it also refuses a destination that
    only reaches outside the library through a symlink - which no amount of
    inspecting the text of the path can catch. This is the check issue #56 asks
    for by name ("refuse to write outside the configured library directory, and
    test that"), and it is deliberately the last word rather than the first: the
    segment rules above are what produce a good error message, and this is what
    makes the guarantee true.
    """
    root = _library_dir()
    candidate = root.joinpath(*PurePosixPath(rel).parts)
    try:
        resolved = Path(os.path.realpath(candidate))
        root_resolved = Path(os.path.realpath(root))
    except OSError as exc:  # pragma: no cover - realpath on a sane path
        raise HTTPException(422, f"that path cannot be used: {exc}") from None
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise HTTPException(
            422,
            "that would put the file outside your library folder, which Fermata will not "
            "do. Nothing has been changed.",
        )
    return candidate


def _destination_for(row, folder: str | None, filename: str | None) -> str:
    """Where this score's file would end up, as a library-relative path."""
    current = PurePosixPath(row["path"])
    parts = _safe_parts(folder) if folder is not None else current.parent.parts
    name = _safe_filename(filename) if filename is not None else current.name
    if parts and parts[0] == "":  # PurePosixPath('a.pdf').parent is '.', not ''
        parts = ()
    parts = tuple(p for p in parts if p not in ("", "."))
    return "/".join([*parts, name])


def _location_fields(rel: str) -> tuple[str | None, str | None]:
    """The two fields that describe WHERE a score is, re-derived from its path.

    A move changes where a score lives, so `collection` and `series` - which are
    read straight off the folders, and which the sidebar presents as the folder
    tree - have to follow it or the sidebar starts describing a layout that is
    no longer there.

    `title`, `composer` and `source` deliberately do NOT follow. Those are
    statements about the music rather than about the filesystem, they can have
    been corrected by hand through PATCH /api/scores/{id}, and re-deriving them
    from a path would silently undo that correction every time somebody tidied a
    folder. That is why moving and renaming metadata are two different
    operations in this API: this one moves the file, PATCH edits the facts.
    """
    meta = parse_path(rel)
    return meta.collection, meta.series


def _move_file_on_disk(src: Path, dest: Path) -> None:
    """Move one file, creating the folders above it, never overwriting.

    os.replace would be atomic and is exactly wrong here: it replaces the
    destination silently, which is the one thing rule 3 above forbids. The
    existence check is racy against something outside Fermata writing the same
    path in the same millisecond, and that race is accepted - the alternative is
    an exclusive create plus a copy plus a delete, which turns a rename into a
    read and write of the whole file for a case that needs a second process
    writing into the library at the exact moment of a move.
    """
    if dest.exists():
        raise HTTPException(409, f"there is already a file at {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.rename(dest)
    except OSError:
        # Different filesystems under one library tree (a bind mount inside a
        # bind mount) make rename fail with EXDEV; shutil.move copies and
        # unlinks, which is slower and works.
        shutil.move(str(src), str(dest))


def _relink_moved_file(conn, row, dest_rel: str) -> None:
    """Point the score row at where its file now is - by content, not by trust.

    The hash is recomputed at the destination and checked against the one the
    row already carries, and that check is the whole of this function's claim to
    be safe. It is the same identity test scanner._scan_file's relink makes,
    for the same reason: the bytes are what say two paths hold the same score.
    Here it is applied to a file whose row is already known, so the answer is
    yes or no rather than a search - but a NO still has to be an error, because
    a mismatch means the file changed under Fermata between being listed and
    being moved, and pointing the row at it anyway would attach somebody's
    practice history to different music.

    size and mtime are refreshed alongside, so the next scan's unchanged-file
    shortcut recognises the file rather than re-reading and re-hashing it.
    """
    dest = _resolve_in_library(dest_rel)
    moved_hash = scanner.hash_file(dest)
    if moved_hash != row["hash"]:
        raise HTTPException(
            409,
            "the file changed while it was being moved, so Fermata stopped rather than "
            "attach this score's practice history and transcription to different music. "
            "Scan the library and try again.",
        )
    stat = dest.stat()
    _repoint_row(conn, row["id"], dest_rel, stat.st_size, stat.st_mtime)


def _repoint_row(conn, score_id: int, dest_rel: str, size=None, mtime=None) -> None:
    """Say where a score's file is now, and re-derive the two fields that
    describe where it lives.

    `size` and `mtime` are omitted for a score whose file is NOT THERE - see
    rename_folder, which has to carry a score marked missing along with the
    folder it is filed under. There is nothing to stat, and the figures the row
    already carries are the last true ones: overwriting them with zeroes would
    make the scanner's unchanged-file shortcut re-read the file the day it comes
    back, which is harmless, and would make the stored size a lie in the
    meantime, which is not.
    """
    collection, series = _location_fields(dest_rel)
    fields = {"path": dest_rel, "collection": collection, "series": series}
    if size is not None:
        fields["size"], fields["mtime"] = size, mtime
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE scores SET {sets} WHERE id = ?", [*fields.values(), score_id])


def _plan_line(row, dest_rel: str, status: str, reason: str | None = None) -> dict:
    return {
        "score_id": row["id"],
        "title": row["title"],
        "from_path": row["path"],
        "to_path": dest_rel,
        "status": status,
        "reason": reason,
    }


def _plan_move(conn, row, dest_rel: str, claimed: set[str]) -> dict:
    """One line of a move plan: what would happen to this score, and why not.

    `claimed` carries the destinations earlier lines in the same batch have
    already taken, so two scores with the same file name moving into one folder
    are both reported as blocked rather than the second one silently landing on
    the first.
    """
    if row["deleted_at"] is not None:
        # Checked FIRST, and before the unchanged shortcut, because a deleted
        # score has a perfectly good file sitting in the trash: without this it
        # would be moved back out into the library while its row stays marked
        # deleted, leaving a file nothing in the library shows and a trash entry
        # whose file is not in the trash. Restoring is an action with a button,
        # and it is the only thing that takes a score out of the trash.
        return _plan_line(
            row,
            dest_rel,
            "blocked",
            "that score is in the trash. Put it back from there first, and then move it.",
        )
    if dest_rel == row["path"]:
        return _plan_line(row, dest_rel, "unchanged")
    if row["missing_since"] is not None:
        return _plan_line(
            row,
            dest_rel,
            "blocked",
            "Fermata cannot find this score's file, so there is nothing to move. Put the "
            "file back and scan, or delete the score if it is really gone.",
        )
    source = _resolve_in_library(row["path"])
    if not source.is_file():
        return _plan_line(
            row,
            dest_rel,
            "blocked",
            "this score's file is not where Fermata last saw it - scan the library and "
            "try again.",
        )
    if dest_rel in claimed:
        return _plan_line(
            row,
            dest_rel,
            "blocked",
            "another score in this same move would be put at that exact path, and Fermata "
            "will not overwrite one of your files with another. Rename one of them first.",
        )
    taken = conn.execute(
        "SELECT id, title FROM scores WHERE path = ? AND id != ?", (dest_rel, row["id"])
    ).fetchone()
    if taken is not None:
        return _plan_line(
            row, dest_rel, "blocked", f"{taken['title']} is already at that path"
        )
    if _resolve_in_library(dest_rel).exists():
        return _plan_line(
            row, dest_rel, "blocked", "there is already a file at that path"
        )
    claimed.add(dest_rel)
    return _plan_line(row, dest_rel, "move")


def _apply_plan(conn, plan: list[dict]) -> None:
    """Carry out every 'move' line, or leave the library exactly as it was.

    A blocked line anywhere refuses the whole batch - a half-applied
    reorganisation is worse than none, because the person now has to work out
    which half. If a move fails part way through (a file locked, a folder gone
    read-only), the ones already made are put back before the error is raised,
    and the surrounding write_tx rolls the rows back with it.
    """
    blocked = [line for line in plan if line["status"] == "blocked"]
    if blocked:
        raise HTTPException(
            409,
            "nothing was moved. "
            + " ".join(f"{line['title']}: {line['reason']}" for line in blocked),
        )
    done: list[tuple[Path, Path]] = []
    try:
        for line in plan:
            if line["status"] != "move":
                continue
            row = _score_row(conn, line["score_id"])
            source = _resolve_in_library(row["path"])
            dest = _resolve_in_library(line["to_path"])
            _move_file_on_disk(source, dest)
            done.append((source, dest))
            _relink_moved_file(conn, row, line["to_path"])
    except Exception:
        for source, dest in reversed(done):
            try:
                dest.rename(source)
            except OSError:  # pragma: no cover - the filesystem refusing twice
                log.error(
                    "could not put %s back at %s after a failed move - the file is at the "
                    "new path and the database has been rolled back, so a scan will "
                    "re-link it",
                    dest,
                    source,
                )
        raise


class ScoreMoveIn(BaseModel):
    """Where one score's file should go.

    Both `folder` and `filename` are optional and at least one is required:
    omitting the folder keeps the score where it is (so this is a rename), and
    omitting the filename keeps its name (so this is a move). An empty string
    for `folder` is the library root, and is not the same as omitting it.

    `dry_run` defaults FALSE here and TRUE on the two bulk routes. A person
    moving one score has that score in front of them and has said which folder;
    making them confirm a one-line plan first is ceremony, not safety. A batch
    is the case where what is about to happen is not obvious, which is what
    issue #56's dry-run-by-default rule is about.
    """

    folder: str | None = None
    filename: str | None = None
    dry_run: bool = False


class LibraryMoveIn(BaseModel):
    """Several scores into one folder. See LibraryMoveOut for the plan shape."""

    score_ids: list[Count] = Field(min_length=1, max_length=500)
    folder: str
    # True by default, on purpose - see move_scores' docstring.
    dry_run: bool = True


class FolderIn(BaseModel):
    path: str = Field(max_length=1000)


class FolderRenameIn(BaseModel):
    from_path: str = Field(max_length=1000)
    to_path: str = Field(max_length=1000)
    # True by default, on purpose - see rename_folder's docstring.
    dry_run: bool = True


def _busy(exc: scanner.LibraryBusy) -> HTTPException:
    """A scan (or another change) is in flight - see scanner.hold_library_still.

    409 rather than 503: nothing is wrong with the server, the request simply
    conflicts with something already happening, and it will succeed unchanged in
    a moment.
    """
    return HTTPException(409, str(exc))


@router.post("/scores/{score_id}/move", tags=[TAG_LIBRARY], response_model=ScoreMoveOut)
def move_score(score_id: RowId, body: ScoreMoveIn):
    """Move one score's file to another folder in the library, rename it, or
    both, and bring the score row with it.

    `folder` is a library-relative folder (omit it to keep the score where it
    is); `filename` renames the file within that folder and must keep an
    extension Fermata can read. `dry_run` answers with the plan and changes
    nothing.

    THIS RENAMES THE FILE, NOT THE PIECE. A score's title, composer and source
    are edited with PATCH /api/scores/{id} and are deliberately left alone here
    - they can have been corrected by hand, and a tidy-up of the folders must
    not quietly undo that. The two location fields that ARE read off the path,
    `collection` and `series`, do follow the file. Everything else on the score
    - its practice history, goals, tags, transcription, favourite, last page
    read and instrument - is attached to the row, and the row is updated rather
    than replaced, so all of it survives the move untouched.
    """
    _require_library()
    if body.folder is None and body.filename is None:
        raise HTTPException(422, "give a folder to move into, a filename to rename to, or both")
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                row = _score_row(conn, score_id)
                dest_rel = _destination_for(row, body.folder, body.filename)
                _resolve_in_library(dest_rel)
                plan = [_plan_move(conn, row, dest_rel, set())]
                if not body.dry_run:
                    _apply_plan(conn, plan)
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    conn = connect()
    return {
        "dry_run": body.dry_run,
        "applied": not body.dry_run,
        "moves": plan,
        "score": _with_tags(conn, [_score_row(conn, score_id)])[0],
    }


@router.get("/library/folders", tags=[TAG_LIBRARY], response_model=list[FolderOut])
def list_folders():
    """Every folder in the library, for a move dialog to offer as a
    destination.

    Read from the filesystem rather than from the stored paths, so a folder
    created a moment ago and still empty is offered - which is the whole point
    of being able to create one. The trash and any other dot-folder are left
    out: those belong to Fermata or to a sync client, not to the person's
    library.
    """
    root = _require_library()
    conn = connect()
    counts: dict[str, int] = {}
    for r in conn.execute("SELECT path FROM scores WHERE deleted_at IS NULL"):
        parent = PurePosixPath(r["path"]).parent
        key = "" if str(parent) == "." else str(parent)
        counts[key] = counts.get(key, 0) + 1
    folders = [
        {
            "path": "",
            "name": "Library root",
            "depth": 0,
            "score_count": counts.get("", 0),
        }
    ]
    for path in sorted(root.rglob("*")):
        if not path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        parts = PurePosixPath(rel).parts
        if len(parts) > MAX_FOLDER_DEPTH or any(p.startswith(".") for p in parts):
            continue
        folders.append(
            {
                "path": rel,
                "name": parts[-1],
                "depth": len(parts),
                "score_count": counts.get(rel, 0),
            }
        )
    return folders


@router.post("/library/folders", tags=[TAG_LIBRARY], response_model=FolderCreateOut)
def create_folder(body: FolderIn):
    """Create a folder in the library, so scores can be moved into it.

    Creating a folder that is already there is not an error - the caller asked
    to have that folder and does - but the answer says which of the two
    happened.
    """
    _require_library()
    parts = _safe_parts(body.path, "path")
    if not parts:
        raise HTTPException(422, "give a folder name")
    rel = "/".join(parts)
    target = _resolve_in_library(rel)
    existed = target.is_dir()
    if target.exists() and not existed:
        raise HTTPException(409, f"{rel} is a file, not a folder")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # The segment rules cannot know what a particular filesystem will
        # accept: Windows reserves a handful of names outright (CON, NUL, and
        # friends) and refuses trailing dots, and any filesystem can be full or
        # read-only. Refusing with what the filesystem actually said beats a
        # 500 for something a person can simply rename around.
        raise HTTPException(422, f"your filesystem would not take that folder name: {exc}") from None
    return {"created": rel, "existed": existed}


@router.post("/library/move", tags=[TAG_LIBRARY], response_model=LibraryMoveOut)
def move_scores(body: LibraryMoveIn):
    """Move several scores into one folder, keeping each one's file name.

    A DRY RUN UNLESS `dry_run` IS EXPLICITLY FALSE. Issue #56 asks for that by
    name and it is the default here rather than an option, so a client that has
    never heard of the flag gets the plan and not the reorganisation.

    All or nothing: if any line of the plan is blocked - a name collision, a
    file that is not where Fermata thinks it is - none of the moves are made.
    Half a reorganisation is worse than none, because the person then has to
    work out which half happened.
    """
    _require_library()
    parts = _safe_parts(body.folder)
    folder = "/".join(parts)
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                plan: list[dict] = []
                claimed: set[str] = set()
                for score_id in body.score_ids:
                    row = _score_row(conn, score_id)
                    dest_rel = "/".join([*parts, PurePosixPath(row["path"]).name])
                    _resolve_in_library(dest_rel)
                    plan.append(_plan_move(conn, row, dest_rel, claimed))
                if not body.dry_run:
                    _apply_plan(conn, plan)
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    return {
        "dry_run": body.dry_run,
        "applied": not body.dry_run,
        "folder": folder,
        "moves": plan,
        "moved": sum(1 for line in plan if line["status"] == "move"),
        "unchanged": sum(1 for line in plan if line["status"] == "unchanged"),
        "blocked": sum(1 for line in plan if line["status"] == "blocked"),
    }


@router.post("/library/folders/rename", tags=[TAG_LIBRARY], response_model=FolderRenameOut)
def rename_folder(body: FolderRenameIn):
    """Rename a folder, or move it under another one, taking its scores with it.

    A DRY RUN UNLESS `dry_run` IS EXPLICITLY FALSE, for the same reason
    /library/move is: this is the operation that restructures a whole shelf at
    once.

    The FOLDER is renamed on disk, in one step, rather than its scores being
    moved out of it one at a time - so anything else in there (a cover image, a
    text file, a subfolder Fermata does not index) goes along too, which is what
    somebody renaming a folder means. Every score underneath it, at any depth,
    is then re-pointed at its new path.
    """
    _require_library()
    from_parts = _safe_parts(body.from_path, "from_path")
    to_parts = _safe_parts(body.to_path, "to_path")
    if not from_parts or not to_parts:
        raise HTTPException(422, "give both the folder to rename and its new path")
    from_rel, to_rel = "/".join(from_parts), "/".join(to_parts)
    if to_rel == from_rel:
        raise HTTPException(422, "the new path is the same as the old one")
    if to_parts[: len(from_parts)] == from_parts:
        raise HTTPException(422, "a folder cannot be moved inside itself")
    source = _resolve_in_library(from_rel)
    dest = _resolve_in_library(to_rel)
    if not source.is_dir():
        raise HTTPException(404, f"there is no folder {from_rel} in your library")
    if dest.exists():
        raise HTTPException(409, f"{to_rel} already exists")
    prefix = from_rel + "/"
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                # ESCAPE IS NOT OPTIONAL HERE. `_` is a single-character
                # wildcard in LIKE, and folder names full of underscores are
                # exactly what this library's own filenames look like
                # ("Terra_s Theme"). Without the escape clause the escaping
                # below would be literal backslashes in the pattern and a folder
                # with an underscore in its name would match NOTHING - so
                # renaming it would move the folder on disk and re-point not one
                # score, silently. The startswith() pass after it is the belt to
                # this brace: LIKE is what lets SQLite use the index, and Python
                # is what decides.
                pattern = prefix.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")
                rows = [
                    r
                    for r in conn.execute(
                        "SELECT * FROM scores WHERE deleted_at IS NULL"
                        " AND path LIKE ? ESCAPE '\\' ORDER BY path",
                        (pattern + "%",),
                    ).fetchall()
                    if r["path"].startswith(prefix)
                ]
                plan = []
                for r in rows:
                    dest_rel = to_rel + "/" + r["path"][len(prefix):]
                    # A row can name a path with no file behind it - that is
                    # exactly what a score marked missing is - and scores.path is
                    # UNIQUE, so re-pointing a row at a path another row already
                    # claims is an IntegrityError and a 500. Reported as a
                    # blocked line instead, which is a sentence a person can act
                    # on and which _apply_plan's own rule already refuses on.
                    taken = conn.execute(
                        "SELECT title FROM scores WHERE path = ? AND id != ?",
                        (dest_rel, r["id"]),
                    ).fetchone()
                    plan.append(
                        _plan_line(
                            r,
                            dest_rel,
                            "blocked" if taken else "move",
                            f"{taken['title']} is already at that path" if taken else None,
                        )
                    )
                blocked = [line for line in plan if line["status"] == "blocked"]
                if blocked:
                    raise HTTPException(
                        409,
                        "nothing was moved. "
                        + " ".join(f"{line['title']}: {line['reason']}" for line in blocked),
                    )
                if not body.dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    source.rename(dest)
                    try:
                        for line in plan:
                            row = _score_row(conn, line["score_id"])
                            if row["missing_since"] is not None:
                                # A score under this folder whose file is not
                                # there. It still has to follow the rename, or
                                # it is left filed under a folder name that no
                                # longer exists - and there is nothing to hash,
                                # so the content check _relink_moved_file makes
                                # would be a FileNotFoundError and a 500 for the
                                # ordinary case of renaming a folder holding one
                                # score whose file went astray.
                                _repoint_row(conn, row["id"], line["to_path"])
                            else:
                                _relink_moved_file(conn, row, line["to_path"])
                    except Exception:
                        dest.rename(source)
                        raise
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    return {
        "dry_run": body.dry_run,
        "applied": not body.dry_run,
        "from_path": from_rel,
        "to_path": to_rel,
        "moves": plan,
        "moved": len(plan),
    }


def _free_path(rel: str, conn=None, exclude_id: int | None = None) -> str:
    """`rel`, or the first numbered variant of it that nothing occupies.

    Used where refusing would strand somebody: putting a score back from the
    trash when something else has since taken its old path. Landing beside it
    under a distinct name is better than either overwriting (rule 3) or telling
    a person their own file cannot come back.

    "OCCUPIED" MEANS THE DISK OR THE DATABASE, and both halves are needed.
    scores.path is UNIQUE, so a row already naming this path makes the update
    an IntegrityError and a 500 - and a row can name a path with no file behind
    it, which is precisely what a score marked missing is. Checking only the
    filesystem would therefore turn "your other copy of this went missing" into
    an unexplained server error at the moment somebody tried to undo a
    deletion.
    """
    stem, suffix = PurePosixPath(rel).stem, PurePosixPath(rel).suffix
    candidate = PurePosixPath(rel)

    def taken(path: str) -> bool:
        if _resolve_in_library(path).exists():
            return True
        if conn is None:
            return False
        row = conn.execute(
            "SELECT id FROM scores WHERE path = ? AND id IS NOT ?", (path, exclude_id)
        ).fetchone()
        return row is not None

    for attempt in range(1, 1000):
        if not taken(str(candidate)):
            return str(candidate)
        candidate = candidate.with_name(f"{stem} ({attempt}){suffix}")
    raise HTTPException(409, f"nothing could be put back at {rel} - a thousand names were taken")


def _tidy_trash_folder(rel: str) -> None:
    """Remove a per-score trash folder once its file has left it.

    Only ever the one folder, only when it is empty, and only inside the trash -
    so this cannot reach anything of the person's. Without it the trash fills up
    with empty numbered directories, one per score ever deleted, which is a
    small thing that makes the folder look like a mess a person might be tempted
    to clear out by hand.

    Failure is deliberately silent: a leftover empty folder is not worth failing
    a restore that has already succeeded over.
    """
    parts = PurePosixPath(rel).parts
    if len(parts) < 2 or parts[0] != scanner.TRASH_DIR_NAME:
        return
    try:
        _resolve_in_library("/".join(parts[:2])).rmdir()
    except OSError:
        pass


def _attached_counts(conn, score_id: int) -> dict:
    """How much of somebody's own work is hanging off this score row.

    Counted from the database and returned with every delete, so "nothing was
    lost" is a number a person can read rather than a promise.
    """
    one = lambda sql: conn.execute(sql, (score_id,)).fetchone()[0]  # noqa: E731
    return {
        "practice_sessions": one(
            "SELECT COUNT(*) FROM practice_sessions WHERE score_id = ?"
        ),
        "goals": one("SELECT COUNT(*) FROM practice_goals WHERE score_id = ?"),
        "tags": one("SELECT COUNT(*) FROM score_tags WHERE score_id = ?"),
        "transcriptions": one("SELECT COUNT(*) FROM transcriptions WHERE score_id = ?"),
    }


@router.delete("/scores/{score_id}", tags=[TAG_LIBRARY], response_model=ScoreDeleteOut)
def delete_score(score_id: RowId):
    """Delete a score: its file goes to the library's trash folder and its row
    is marked as deleted.

    THIS DESTROYS NOTHING. The file is moved, not removed, into
    `.fermata-trash` inside the library, and the score row stays exactly where
    it was with its practice history, goals, tags and transcription still
    attached - the response counts each of those so an interface can say so.
    The score leaves the library views and appears in GET /api/trash, from
    where POST /api/trash/{id}/restore puts it back.

    Destroying it takes a second, deliberate request to DELETE /api/trash/{id},
    which states what it destroys.
    """
    _require_library()
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                row = _score_row(conn, score_id)
                if row["deleted_at"] is not None:
                    raise HTTPException(409, "that score is already in the trash")
                kept = _attached_counts(conn, score_id)
                name = PurePosixPath(row["path"]).name
                # The score id in the path is what makes a trash path unique
                # without inventing a naming scheme: two scores called
                # "Prelude.pdf" deleted from two folders keep their own names
                # and cannot collide, and the folder says which row the file
                # belongs to if anybody ever looks in there by hand.
                trash_rel = f"{scanner.TRASH_DIR_NAME}/{score_id}/{name}"
                trash_rel = _free_path(trash_rel, conn, exclude_id=score_id)
                source = _resolve_in_library(row["path"])
                if source.is_file():
                    _move_file_on_disk(source, _resolve_in_library(trash_rel))
                conn.execute(
                    """UPDATE scores
                          SET path = ?, deleted_from = ?, deleted_at = datetime('now'),
                              missing_since = NULL
                        WHERE id = ?""",
                    (trash_rel, row["path"], score_id),
                )
                # The library is smaller because somebody said so, so the mark
                # the loss guard measures against comes down with it - see
                # scanner.record_deliberate_shrink for what goes wrong
                # otherwise.
                scanner.record_deliberate_shrink(conn)
                deleted_from = row["path"]
                title = row["title"]
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    return {
        "deleted": score_id,
        "title": title,
        "deleted_from": deleted_from,
        "trashed_to": trash_rel,
        "practice_sessions_kept": kept["practice_sessions"],
        "goals_kept": kept["goals"],
        "tags_kept": kept["tags"],
        "transcriptions_kept": kept["transcriptions"],
    }


def _deleted_row(conn, score_id: int):
    row = _score_row(conn, score_id)
    if row["deleted_at"] is None:
        raise HTTPException(404, "that score is not in the trash")
    return row


@router.get("/trash", tags=[TAG_LIBRARY], response_model=list[ScoreOut])
def list_trash():
    """Scores that have been deleted and not yet destroyed, most recently
    deleted first.

    They are still whole scores - `deleted_from` says where each one came from,
    and every session, goal, tag and transcription is still attached - which is
    why this answers with the same shape the library does rather than a reduced
    one.
    """
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM scores WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC"
    ).fetchall()
    return _with_tags(conn, rows)


@router.post("/trash/{score_id}/restore", tags=[TAG_LIBRARY], response_model=ScoreRestoreOut)
def restore_score(score_id: RowId):
    """Put a deleted score back where it came from.

    If something else has taken that exact path in the meantime the file lands
    beside it under a numbered name rather than overwriting it, and
    `restored_to` says where it actually went - see rule 3 in this section's
    comment.
    """
    _require_library()
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                row = _deleted_row(conn, score_id)
                came_from = row["deleted_from"] or row["path"]
                target = _free_path(came_from, conn, exclude_id=score_id)
                source = _resolve_in_library(row["path"])
                if not source.is_file():
                    raise HTTPException(
                        409,
                        "the file for that score is no longer in Fermata's trash folder, so "
                        "there is nothing to put back. Its practice history is still on the "
                        "score - put the file back in your library and scan, or destroy the "
                        "score from the trash.",
                    )
                _move_file_on_disk(source, _resolve_in_library(target))
                conn.execute(
                    "UPDATE scores SET deleted_at = NULL, deleted_from = NULL WHERE id = ?",
                    (score_id,),
                )
                _relink_moved_file(conn, row, target)
                _tidy_trash_folder(row["path"])
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    conn = connect()
    return {
        "restored": score_id,
        "restored_from": came_from,
        "restored_to": target,
        "score": _with_tags(conn, [_score_row(conn, score_id)])[0],
    }


@router.delete("/trash/{score_id}", tags=[TAG_LIBRARY], response_model=ScorePurgeOut)
def purge_score(score_id: RowId):
    """Destroy a deleted score for good. THIS ONE REALLY DELETES.

    What it destroys: the file, removed from the trash folder and not
    recoverable through Fermata; the score row; its tags; and any
    transcription, including one corrected by hand, which is real work and
    cannot be regenerated identically.

    What it keeps: every practice session and goal that named this score. The
    hours were still spent, so those rows stay in the history recording
    practice with no piece named (see db.py's notes on ON DELETE SET NULL). The
    response counts both sides.

    Only a score already in the trash can be destroyed, so this is always the
    second of two deliberate steps.
    """
    _require_library()
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                row = _deleted_row(conn, score_id)
                counts = _attached_counts(conn, score_id)
                path = _resolve_in_library(row["path"])
                removed = None
                if path.is_file() and scanner.in_trash(row["path"]):
                    # Guarded on the path really being in the trash, not merely
                    # on the row saying it is deleted: unlinking is the one
                    # thing here with no way back, so it happens only for a file
                    # Fermata itself put in its own trash folder.
                    path.unlink()
                    removed = row["path"]
                    _tidy_trash_folder(row["path"])
                conn.execute("DELETE FROM scores WHERE id = ?", (score_id,))
                scanner.record_deliberate_shrink(conn)
                title = row["title"]
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    return {
        "deleted": score_id,
        "title": title,
        "file_deleted": removed,
        "tags_destroyed": counts["tags"],
        "transcriptions_destroyed": counts["transcriptions"],
        "practice_sessions_kept": counts["practice_sessions"],
        "goals_kept": counts["goals"],
    }
