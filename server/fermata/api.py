import errno
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    HTTPException,
    Path as PathParam,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from lxml import etree as lxml_etree
from pydantic import BaseModel, Field, StrictInt

from . import instruments, practice, scanner, trainer, transcribe_batch
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
    ImportOut,
    InstrumentDeleteOut,
    InstrumentOut,
    InstrumentPresetOut,
    LibraryMoveOut,
    LogPracticeOut,
    MeOut,
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
    ScoreProgressOut,
    ScorePurgeOut,
    ScoreRestoreOut,
    SessionDeleteOut,
    SessionListOut,
    SetlistDeleteOut,
    SetlistDetailOut,
    SetlistOut,
    SettingsOut,
    TagOut,
    TrainerAttemptListOut,
    TrainerAttemptOut,
    TrainerChordAttemptListOut,
    TrainerChordAttemptOut,
    TrainerPresetDeleteOut,
    TrainerPresetOut,
    TranscribeBatchStatusOut,
    TranscribeBatchTriggerOut,
    TranscribeResultOut,
    TranscriptionAnalysisOut,
    TranscriptionOut,
    UploadOut,
    VersionOut,
)
from . import config
from .config import FILE_TYPES, LIBRARY_DIR
from .db import DEFAULT_OWNER, SCHEMA_VERSION, connect, tx, write_tx
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
TAG_AUTH = "auth"
TAG_SETTINGS = "settings"
TAG_INSTRUMENTS = "instruments"
TAG_LIBRARY = "library"
TAG_PRACTICE = "practice"
TAG_TRANSCRIPTION = "transcription"
TAG_SCAN = "scan"
TAG_PORTABILITY = "portability"
TAG_TRAINER = "trainer"
TAG_SETLISTS = "setlists"

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
# GET /api/scores' `transcribed` filter (#190): "yes" narrows to scores with
# a transcription (extracted or hand-edited, and the filter draws no
# distinction between those two - see the No-gos on #190), "no" to its exact
# complement. Anything a scan judged non-extractable has no transcription
# and so falls under "no", which is what makes the filter double as "show me
# what a scan could not read".
VALID_TRANSCRIBED = {"yes", "no"}

# scores.key/tempo/difficulty (#8): closed ranges, each enforced the same way
# on both PATCH /scores/{id} and GET /scores' filters, so a value the one
# would reject the other never silently accepts.
#
# `key` is a MusicXML `fifths` count, not a key NAME - see the comment over
# _SCORES_COLUMNS in db.py for why a name (which would have to state a mode
# nothing here ever determines) is not what this column holds. -7..7 is
# every fifths count MusicXML can express as a plain key signature.
MIN_KEY_FIFTHS = -7
MAX_KEY_FIFTHS = 7
# practice.MIN_TEMPO_BPM / MAX_TEMPO_BPM (20..400) - the same bounds a
# practice session's own tempo_bpm already enforces, reused rather than
# reinvented so a number this column refuses is refused everywhere else in
# the application for the same reason.
MIN_TEMPO_BPM = practice.MIN_TEMPO_BPM
MAX_TEMPO_BPM = practice.MAX_TEMPO_BPM
# A 1-5 rating, the same shape as a practice session's own `rating` - nothing
# infers one (#8's No-gos); it is only ever set by hand.
MIN_DIFFICULTY = 1
MAX_DIFFICULTY = 5

KeyFifths = Annotated[StrictInt, Field(ge=MIN_KEY_FIFTHS, le=MAX_KEY_FIFTHS)]
Tempo = Annotated[StrictInt, Field(ge=MIN_TEMPO_BPM, le=MAX_TEMPO_BPM)]
Difficulty = Annotated[StrictInt, Field(ge=MIN_DIFFICULTY, le=MAX_DIFFICULTY)]

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


# WHAT A DELETED SCORE MAY STILL BE ASKED FOR (#56).
#
# A deleted score's row is still there, deliberately: that is the whole of what
# makes deleting recoverable. So every endpoint that takes a score id can still
# reach one, and each of them has to have decided what to do about it. This is
# that decision, stated once, and _live_score_row is how the routes that refuse
# do it.
#
# READS ARE ALLOWED, and that is deliberate rather than an oversight. GET
# /scores/{id}, its file, its thumbnail, its transcription and its practice
# totals all answer for a deleted score, because the trash view is built out of
# exactly those responses and because being able to LOOK at a score - to open
# it, to read the transcription you spent an evening correcting - is the point
# of a trash you can change your mind from. A trash that will not let you check
# what is in it before you destroy it is a worse trash.
#
# WRITES ARE REFUSED, with 409. Every one of them means "work on this piece":
# logging practice against it, setting a goal about it, editing its title,
# extracting or saving a transcription. Nothing in the interface offers any of
# them for a deleted score, so reaching one is a client's mistake or an
# automation's, and the useful answer names the fix rather than silently
# accepting work that will look, afterwards, like practice on a score that is
# not in the library.
#
# PRACTICE ALREADY LOGGED IS UNTOUCHED BY ALL OF THIS. The history is the one
# thing here that cannot be regenerated; it stays, it still names its piece, and
# it still counts. What changes is only that a figure naming a deleted score now
# SAYS the score is deleted - see practice_summary's top_scores and
# practice.time_spent's by_score - so a dashboard can stop offering a way into
# something that is not in the library, instead of quietly linking to it.
def _live_score_row(conn, score_id: int, doing: str):
    """The score row, refusing if it is in the trash. `doing` completes the
    sentence "Fermata will not <doing> a score that is in the trash"."""
    row = _score_row(conn, score_id)
    if row["deleted_at"] is not None:
        raise HTTPException(
            409,
            f"{row['title']} is in the trash, so Fermata will not {doing}. Put it back "
            "from the trash first - everything already on it is still there.",
        )
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


@router.get("/me", tags=[TAG_AUTH], response_model=MeOut)
def get_me(request: Request):
    """The identity, if any, a trusted reverse proxy vouched for on this
    request - see fermata/authproxy.py and issue #16. Fermata has no login
    of its own; this reads back only what RemoteUserAuthMiddleware already
    verified before this handler ran (a header naming the user, sent by a
    proxy address on the configured trust list) and stashed on
    `request.state`. Nothing in Fermata acts on this today - no per-user
    filtering, no permissions - it exists for a consumer (a possible sharing
    layer; the MCP server leaves it inert, #31) to read. Always 200: when
    reverse-proxy auth is off (the default) or this particular request
    carried no identity, that is `{"enabled": false, "username": null}`
    rather than an error, since asking "who am I" is safe regardless of
    whether anything answers it."""
    username = getattr(request.state, "fermata_username", None)
    return {"enabled": bool(config.AUTH_HEADER), "username": username}


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
    transcribed: str = "",
    key: Annotated[int | None, Query(ge=MIN_KEY_FIFTHS, le=MAX_KEY_FIFTHS)] = None,
    difficulty: Annotated[int | None, Query(ge=MIN_DIFFICULTY, le=MAX_DIFFICULTY)] = None,
    tempo_min: Annotated[int | None, Query(ge=MIN_TEMPO_BPM, le=MAX_TEMPO_BPM)] = None,
    tempo_max: Annotated[int | None, Query(ge=MIN_TEMPO_BPM, le=MAX_TEMPO_BPM)] = None,
):
    """The library, filtered and searched. `search` matches title, composer,
    source or series; `practiced` is 'recent' (practised in the last 14 days)
    or 'neglected' (present on disk, and either never practised or not
    practised in 30 days) - see the query's own comments for why those two
    views disagree about a score whose file has gone missing. `transcribed`
    is 'yes' (has a transcription, extracted or hand-edited - this filter
    draws no distinction between the two) or 'no' (its exact complement,
    which is also every score a scan judged non-extractable) (#190).

    `key` is an exact match on the stored `fifths` count (#8) - -7..7, never a
    key name (see ScorePatch.key). `difficulty` is an exact match on the 1-5
    rating. `tempo_min`/`tempo_max` bound the manual bpm, either or both -
    plain column comparisons, so composing them with every other filter here
    costs nothing extra.

    A DELETED SCORE IS NEVER HERE, under any filter - it is in GET /api/trash
    until it is restored or destroyed. A score whose FILE has gone missing is a
    different thing and is still here, carrying `missing_since`: Fermata not
    being able to find a file is not the same statement as somebody having
    thrown it away."""
    if practiced and practiced not in VALID_PRACTICED:
        raise HTTPException(422, f"practiced must be one of {sorted(VALID_PRACTICED)}")
    if transcribed and transcribed not in VALID_TRANSCRIBED:
        raise HTTPException(422, f"transcribed must be one of {sorted(VALID_TRANSCRIBED)}")
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
    if transcribed == "yes":
        where.append("EXISTS (SELECT 1 FROM transcriptions t WHERE t.score_id = s.id)")
    elif transcribed == "no":
        where.append("NOT EXISTS (SELECT 1 FROM transcriptions t WHERE t.score_id = s.id)")
    if favorite:
        where.append("s.favorite = 1")
    if key is not None:
        where.append("s.key = ?")
        params.append(key)
    if difficulty is not None:
        where.append("s.difficulty = ?")
        params.append(difficulty)
    if tempo_min is not None:
        where.append("s.tempo >= ?")
        params.append(tempo_min)
    if tempo_max is not None:
        where.append("s.tempo <= ?")
        params.append(tempo_max)
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
    # #8: musical metadata about the piece. A `fifths` count, never a key
    # NAME - see the comment over db._SCORES_COLUMNS. Explicit null clears it,
    # the same as instrument_id - see patch_score - which is how a wrong hand
    # entry (or an opportunistically-filled one, see
    # api._store_extraction_result) comes off again.
    key: KeyFifths | None = None
    # Manual bpm - never written by anything but this endpoint. Explicit null
    # clears it.
    tempo: Tempo | None = None
    # Manual 1-5 rating - nothing infers one. Explicit null clears it.
    difficulty: Difficulty | None = None


# Fields where an explicit null is itself a request ("clear this") rather
# than an omission, so the omit-nulls rule every other field needs (a title
# cannot be cleared - the column is NOT NULL) must not swallow it. instrument_id
# was the first (#72); key/tempo/difficulty are #8's three, nullable for
# exactly the same reason - a wrong hand entry, or a key
# _store_extraction_result filled in, has to be sayable as gone rather than
# only ever replaceable by another value.
NULLABLE_PATCH_FIELDS = ("instrument_id", "key", "tempo", "difficulty")


@router.patch("/scores/{score_id}", tags=[TAG_LIBRARY], response_model=ScoreOut)
def patch_score(score_id: RowId, patch: ScorePatch):
    """Change one or more fields on a score. `tags` replaces the whole tag
    set when present; an explicit null on `instrument_id`, `key`, `tempo` or
    `difficulty` clears it, which is different from omitting the field
    entirely."""
    if patch.content_kind is not None and patch.content_kind not in VALID_KINDS:
        raise HTTPException(422, f"content_kind must be one of {sorted(VALID_KINDS)}")
    with write_tx() as conn:
        _live_score_row(conn, score_id, "change what it says")
        if patch.instrument_id is not None:
            _instrument_row(conn, patch.instrument_id)
        fields = {
            k: v
            for k, v in patch.model_dump(exclude_none=True).items()
            if k not in ("tags", *NULLABLE_PATCH_FIELDS)
        }
        for field in NULLABLE_PATCH_FIELDS:
            if field in patch.model_fields_set:
                fields[field] = getattr(patch, field)
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
    # The named drill scope this practice was done under (issue #236). A
    # fretboard or chord drill run on a saved preset sends its id here, and
    # from then on "what was practised" is a row this can be joined on rather
    # than a sentence in `note` - see docs/practice-data.md's rule about what
    # may and may not live in free text.
    preset_id: Count | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)


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
    preset_id: Count | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)


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
    "preset_id",
)


def _normalise_session(
    conn, fields: dict, allow_missing_score: bool = False, check_day_window: bool = True
) -> dict:
    if fields.get("score_id") is not None:
        _live_score_row(conn, fields["score_id"], "log practice against it")
    if fields.get("preset_id") is not None:
        # Checked here rather than left to the foreign key, for the reason
        # every other reference in this file is: a raised IntegrityError is a
        # 500 and says nothing a person could act on, whereas this is a 404
        # naming what was not found (see _trainer_preset_row).
        _trainer_preset_row(conn, fields["preset_id"])
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
        _live_score_row(conn, score_id, "log practice against it")
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
        f"""SELECT p.*, s.title AS score_title,
                   s.deleted_at IS NOT NULL AS score_deleted
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
    # A DELETED SCORE IS STILL COUNTED HERE, AND NOW SAYS THAT IT IS (#56).
    # Dropping it would be the wrong fix twice over: the hours were spent, and
    # this figure's own totals would then stop adding up to the week_seconds
    # beside it, with nothing saying why. What was actually wrong was that a
    # dashboard named a piece and gave a client every reason to link to it,
    # while the library it links into no longer holds it. So the fact travels
    # with the row instead - the same shape as `score_missing` on a session.
    top = conn.execute(
        f"""SELECT s.id, s.title, SUM(p.seconds) AS practice_seconds,
                   s.deleted_at IS NOT NULL AS deleted
            FROM practice_sessions p JOIN scores s ON s.id = p.score_id
            WHERE p.owner = ? AND {practice.LOCAL_DATE_SQL} >= date('now', '-6 days')
            GROUP BY p.score_id ORDER BY practice_seconds DESC LIMIT 5""",
        (DEFAULT_OWNER,),
    ).fetchall()
    return {
        "week_seconds": week["total_seconds"],
        "week_sessions": week["session_count"],
        "top_scores": [{**dict(r), "deleted": bool(r["deleted"])} for r in top],
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
        _live_score_row(conn, fields["score_id"], "set a goal about it")
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
             "bars_padded", "bars_unread",
             # `bars_anacrusis` (issue #174): how many bars a first-bar pickup
             # let Rule 8 excuse. A Rule 8 CONFORMANCE figure, not a structural
             # disclosure - it is the bars_short/bars_defective arithmetic
             # itself, adjusted for a pickup that is normal notation rather than
             # a misread, and is shown through the bar-count headline and the
             # warning prose (which names `anacrusis_bars`) like its
             # bars_padded / bars_unread neighbours, not the disclosures panel.
             # So it is listed in test_disclosure_keys._RULE8_CONFORMANCE_KEYS
             # and stays OFF the web mirror. It still has to survive a reload
             # for the same reason the rest do: a reader reopening a stored
             # transcription must see that a first bar was excused, and which.
             "bars_anacrusis", "notes_no_stem", "staves_no_stem",
             # `staves_stemless` (issue #91): a notation staff whose stem/beam
             # vector pass found NO stems at all, though it decoded noteheads
             # that must carry one. A different, stronger fact than
             # `staves_no_stem`, which counts a staff with one FILLED head
             # whose own stem was missing - here the whole stem layer is
             # empty, so nothing on the staff was read from a stem, flag or
             # beam, and it is recoverable from nothing else stored here.
             "staves_stemless",
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
             # `tie_ends_unpaired` (issue #81): an end of a tie the decoder
             # matched in the engraving whose other end it did not, so the
             # tie is not written and its second note is transcribed as
             # separately re-struck rather than held. It belongs here for the
             # same reason as its neighbours and one of its own: EVERY OTHER
             # tie is countable from the emitted file, and these are in
             # neither the file nor any other figure - the bar still adds up,
             # the note is still there, and it is struck twice where the
             # score strikes it once, which nothing else stored here says.
             "tie_ends_unpaired",
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
_BAR_LIST_KEYS = ("padded_bars", "unread_bars",
                   # WHICH bars a first-bar pickup excused (issue #174) - the
                   # bar-number half of `bars_anacrusis`, named in the warning
                   # prose so a reader can carry it back to the printed page and
                   # confirm the excused bar really is a pickup.
                   "anacrusis_bars",
                   "spacing_bars", "degraded_bars",
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
                   # WHICH bars hold an end of a tie whose other end was not
                   # found - the bar-number half of `tie_ends_unpaired`. The
                   # bar named is where the unmatched END is, which for a tie
                   # broken across a system break is the bar the phrase
                   # resumes in as often as the one it left.
                   "tie_ends_unpaired_bars",
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
    # How the tuning was obtained - one of tabextract's TUNING_* words
    # ("instrument", "label", "assumed standard"), or None on a row extracted
    # before this was stored. The provenance the tuning travels with (issue
    # #80), the same way the meter travels with time_signature_source.
    "tuning_source",
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
    row = _live_score_row(conn, score_id, "extract a transcription from it")
    if row["file_type"] != "pdf":
        raise HTTPException(422, "transcription is only supported for pdf scores")
    path = LIBRARY_DIR / row["path"]
    if not path.is_file():
        raise HTTPException(404, "file missing from library")

    ts = tuple(body.time_signature) if body and body.time_signature else None
    if ts is not None:
        _validate_time_signature(ts)

    # The tuning of the instrument this score is assigned, if any (issue #72).
    # Until this, scores.instrument_id was recorded and read by nobody, so a
    # drop-D or seven-string score was transcribed as a standard six-string
    # whatever instrument it named (see db.py's comment on the column). The
    # instrument's `string_pitches` are ordered highest string NUMBER first,
    # which is exactly the order tabextract's tuning uses, so they pass straight
    # through. A dangling instrument_id (an instrument deleted without the
    # ON DELETE SET NULL firing, which cannot happen through the API but a
    # restored backup could) is treated as no instrument rather than a 500.
    instrument_tuning = None
    if row["instrument_id"] is not None:
        inst = conn.execute(
            "SELECT string_pitches FROM instruments WHERE id = ? AND owner = ?",
            (row["instrument_id"], DEFAULT_OWNER),
        ).fetchone()
        if inst is not None:
            try:
                pitches = json.loads(inst["string_pitches"])
            except (TypeError, ValueError):
                pitches = None
            if isinstance(pitches, list) and pitches:
                instrument_tuning = [str(p) for p in pitches]

    result = extract_pdf(path, time_signature=ts, instrument_tuning=instrument_tuning)
    if not result.extractable:
        raise HTTPException(422, result.reason or "pdf is not extractable")
    return _store_extraction_result(score_id, result)


def _store_extraction_result(score_id: int, result) -> dict:
    """Write an EXTRACTABLE ExtractionResult as this score's extracted
    transcription and return the same dict transcribe() has always handed
    back - the confidence blob, read back from the row just written, plus
    bars/beats/notes/tempo from `result` itself (see the closing comment
    below for why those four are not re-read from the row).

    THE ONE PLACE THIS IS BUILT. transcribe_batch's bulk pass calls this too
    (through api._batch_process_one), rather than each caller building its
    own confidence dict - two copies of the ~50-field mapping below are two
    chances for a bulk-transcribed row to end up missing a figure a
    single-transcribed one has, which is exactly the class of bug #137,
    #143 and #146 each shipped once over this same dict.

    Only ever writes the source='extracted' row (see unique index on
    (score_id, source) in db.py) - a source='edited' row is untouched.
    """
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
            # bars_anacrusis / anacrusis_bars (issue #174): how many first-bar
            # pickups Rule 8 excused, and which bars. Written here, the only
            # path into storage, so a reader reopening a stored transcription
            # still sees that a first bar was read as a pickup rather than
            # counted against the meter - the round trip the structural guard
            # in test_transcription_api.py checks by name.
            "bars_anacrusis": result.bars_anacrusis,
            "anacrusis_bars": result.anacrusis_bars,
            "inferred_rest_quarters": result.inferred_rest_quarters,
            "notes_no_stem": result.notes_no_stem,
            "staves_no_stem": result.staves_no_stem,
            "staves_stemless": result.staves_stemless,
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
            # a score with real unsplit pairs (e.g. score_o, 15 per #116)
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
            # tie_ends_unpaired / tie_ends_unpaired_bars (issue #81), written
            # here for the same reason: this dict is the only path into
            # storage, and a count that reaches ExtractionResult and not this
            # comes back None on every reload.
            "tie_ends_unpaired": result.tie_ends_unpaired,
            "tie_ends_unpaired_bars": result.tie_ends_unpaired_bars,
            "time_signature": list(result.time_signature) if result.time_signature else None,
            "time_signature_source": result.time_signature_source,
            "key_fifths": result.key_fifths,
            "key_signature_source": result.key_signature_source,
            "tuning": result.tuning,
            "tuning_label": result.tuning_label,
            # How the tuning was obtained (issue #80): "instrument", "label" or
            # "assumed standard". Stored with the tuning for the same reason
            # time_signature_source is stored with the meter - a tuning read
            # back on a later visit with no word for where it came from is the
            # assumed-presented-as-read failure this exists to end.
            "tuning_source": result.tuning_source,
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
        # Opportunistic fill (#8): copy the key onto the score row, but only
        # when it was actually READ off the page (key_signature_source ==
        # "glyph-decoded" - see tabextract._key_fifths_and_source, the only
        # place that string is written) rather than the 0-fifths fallback
        # every non-decoded page reports, which would otherwise plant a false
        # "no sharps or flats" on every score this extractor could not read a
        # key from. `AND key IS NULL` is the entire guarantee that a hand-set
        # key - or one this same fill already made on an earlier
        # (re-)transcription - is never overwritten; this INSERT...ON CONFLICT
        # runs on every re-transcription of an already-extracted score, so
        # without that guard a rescan would silently clobber a correction.
        # Never touches tempo or difficulty - #8's No-gos rule out inferring
        # either, and this is the one path that writes `key` for anything
        # other than a person.
        if result.key_fifths is not None and result.key_signature_source == "glyph-decoded":
            tx_conn.execute(
                "UPDATE scores SET key = ? WHERE id = ? AND key IS NULL",
                (result.key_fifths, score_id),
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


# Cached as (path, parsed schema) rather than just the schema, so a changed
# config.MUSICXML_XSD - which only happens in tests, via monkeypatch; a real
# deployment sets the environment variable once, before Fermata starts -
# invalidates the cache instead of going on serving whatever path was parsed
# first. Parsing the schema is not cheap (it is hundreds of kilobytes with its
# own imports to resolve), and PUT /scores/{id}/transcription would otherwise
# pay that cost on every single save.
_musicxml_schema_cache: tuple[str, "lxml_etree.XMLSchema"] | None = None


def _musicxml_schema():
    """The parsed MusicXML 4.0 schema named by config.MUSICXML_XSD, or None
    when it is unset or does not name a file that is actually there.

    None is the default-runtime answer, and it is what keeps
    save_transcription's behaviour unchanged for every deployment that has
    not opted in: no schema configured means _validate_musicxml_edit is a
    no-op, exactly as this endpoint has always behaved. Loaded from disk only
    - this never fetches anything - which matters because the schema's own
    xsi:noNamespaceSchemaLocation names a remote URL that must not be
    fetched on every request. See config.MUSICXML_XSD's comment for how a
    deployment or CI run obtains a local copy.
    """
    global _musicxml_schema_cache
    path = config.MUSICXML_XSD
    if not path or not os.path.isfile(path):
        return None
    if _musicxml_schema_cache is None or _musicxml_schema_cache[0] != path:
        _musicxml_schema_cache = (path, lxml_etree.XMLSchema(lxml_etree.parse(path)))
    return _musicxml_schema_cache[1]


def _validate_musicxml_edit(content: str) -> None:
    """422 when a MusicXML edit is not well-formed XML, or is well-formed but
    invalid against the real MusicXML 4.0 schema - belt-and-suspenders behind
    the note editor's own client-side Rule 11 guard, against a hand-crafted
    request (#188). Only called for `fmt == "musicxml"` - alphaTex is never
    XSD-checked, there being no schema for it.

    A no-op whenever _musicxml_schema() answers None, which is the entire
    point: an unconfigured deployment must see no change in behaviour, so
    this function raises nothing until FERMATA_MUSICXML_XSD names a real
    local schema. Uses the exact same lxml.etree.XMLSchema.validate call as
    tests/test_musicxml.py's test_validates_against_xsd, against the same
    schema, rather than a second hand-rolled loader.
    """
    schema = _musicxml_schema()
    if schema is None:
        return
    try:
        doc = lxml_etree.fromstring(content.encode("utf-8"))
    except lxml_etree.XMLSyntaxError as exc:
        raise HTTPException(422, f"transcription is not well-formed XML: {exc}") from None
    if not schema.validate(doc):
        errors = "; ".join(f"line {e.line}: {e.message}" for e in schema.error_log)
        raise HTTPException(
            422, f"transcription does not validate against the MusicXML 4.0 schema: {errors}")


@router.put(
    "/scores/{score_id}/transcription", tags=[TAG_TRANSCRIPTION], response_model=TranscriptionOut
)
def save_transcription(score_id: RowId, body: TranscriptionEditIn):
    """Save a hand edit, replacing any hand edit already stored - never the
    extraction. `format` is sniffed from the content when not given - see
    _sniff_transcription_format. A MusicXML edit is also checked against the
    real schema when one is configured - see _validate_musicxml_edit."""
    fmt = body.format or _sniff_transcription_format(body.content)
    if fmt not in VALID_TRANSCRIPTION_FORMATS:
        raise HTTPException(
            422, f"format must be one of {sorted(VALID_TRANSCRIPTION_FORMATS)}")
    if fmt == "musicxml":
        _validate_musicxml_edit(body.content)
    with tx() as conn:
        _live_score_row(conn, score_id, "save a transcription for it")
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
        _live_score_row(conn, score_id, "throw away its hand-corrected transcription")
        conn.execute(
            "DELETE FROM transcriptions WHERE score_id = ? AND source = 'edited'", (score_id,)
        )
    conn = connect()
    row = _transcription_row(conn, score_id)
    if not row:
        raise HTTPException(404, "no transcription for this score")
    return _transcription_dict(row)


# ---------------------------------------------------------------------------
# Bulk transcription (issue #55): many scores in one background pass, rather
# than a client looping single POST /transcribe calls itself - the planned
# MCP layer wraps THIS, not a loop (#32). Follows the library scan's own
# pattern (start it, poll a status endpoint) rather than adding a queue -
# see transcribe_batch.py for the state machine and the reasoning behind
# every choice below; this section is only what needs api.py's own helpers.
# ---------------------------------------------------------------------------


def _batch_process_one(score_id: int, reconvert: bool) -> dict:
    """One score's outcome for POST /transcribe/batch. Never raises for an
    ordinary refusal - not found, deleted, not a pdf, already transcribed,
    file missing, not extractable - because a silent skip is exactly what
    issue #55 asks not to have; every one of those comes back as a dict
    naming the outcome instead. transcribe_batch.start_batch/_run_batch call
    this back (see transcribe_batch.py's own comment for why the dependency
    runs this direction and not the other), and still catch anything this
    raises regardless, so a genuine bug in one score cannot cost every score
    after it its result.

    Reads the score's row FRESH, not from a list snapshotted when the batch
    started - a scan running at the same time (deliberately not held against
    this, see start_batch's docstring) may mark a score missing or relink it
    between the batch starting and this score's own turn, and this is what
    makes that read as of NOW rather than raced against.
    """
    conn = connect()
    row = conn.execute("SELECT * FROM scores WHERE id = ?", (score_id,)).fetchone()
    if not row:
        return {
            "score_id": score_id, "title": None, "outcome": "errored",
            "reason": "score not found", "bars_defective": None, "bars_measured": None,
        }
    title = row["title"]
    if row["deleted_at"] is not None:
        return {
            "score_id": score_id, "title": title, "outcome": "errored",
            "reason": f"{title} is in the trash, so bulk transcription skipped it",
            "bars_defective": None, "bars_measured": None,
        }
    if row["file_type"] != "pdf":
        return {
            "score_id": score_id, "title": title, "outcome": "errored",
            "reason": "transcription is only supported for pdf scores",
            "bars_defective": None, "bars_measured": None,
        }

    # Never overwrites an edited transcription, `reconvert` or not (#10) -
    # checked before anything else about "already transcribed" so that
    # protection reads as its own, unconditional rule rather than a special
    # case of the reconvert branch below.
    sources = {
        r["source"]
        for r in conn.execute(
            "SELECT source FROM transcriptions WHERE score_id = ?", (score_id,)
        )
    }
    if "edited" in sources:
        return {
            "score_id": score_id, "title": title, "outcome": "already_transcribed",
            "reason": "has a hand-edited transcription, which bulk transcription never "
                      "overwrites",
            "bars_defective": None, "bars_measured": None,
        }
    if "extracted" in sources and not reconvert:
        return {
            "score_id": score_id, "title": title, "outcome": "already_transcribed",
            "reason": "already has an extracted transcription - pass reconvert to redo it",
            "bars_defective": None, "bars_measured": None,
        }

    path = LIBRARY_DIR / row["path"]
    if not path.is_file():
        return {
            "score_id": score_id, "title": title, "outcome": "errored",
            "reason": "file missing from library", "bars_defective": None, "bars_measured": None,
        }

    result = extract_pdf(path)
    if not result.extractable:
        return {
            "score_id": score_id, "title": title, "outcome": "non_extractable",
            "reason": result.reason or "pdf is not extractable",
            "bars_defective": None, "bars_measured": None,
        }

    stored = _store_extraction_result(score_id, result)
    return {
        "score_id": score_id, "title": title, "outcome": "transcribed", "reason": None,
        "bars_defective": stored.get("bars_defective"), "bars_measured": stored.get("bars_measured"),
    }


# A freshly scanned library should not sit with zero transcriptions until
# somebody clicks a bulk pass by hand (#190) - so the scan itself starts one,
# over exactly the scores it added, once its own chain of passes finishes
# (scanner._finish_scan_chain). scanner.py cannot call `_batch_process_one`
# directly - it would have to import this module to reach it, and this
# module already imports scanner.py, which is the circular import
# transcribe_batch.py's own module comment already argues against one layer
# up. Registering it here, right after its definition, is the seam instead:
# scanner.py depends on nothing above it, and this is the one place that
# hands it something to call.
scanner.register_transcribe_hook(_batch_process_one)


class TranscribeBatchIn(BaseModel):
    """What to bulk-transcribe (issue #55).

    Give EITHER `score_ids` (a person selecting particular scores) OR
    `collection` (a person pointing at a folder) - not both, since a caller
    that meant one and typed the other deserves to be told rather than have
    one silently win. Omitting both selects every eligible score in the
    whole library.

    `score_ids`, when given, is honoured EXACTLY - every id in it gets an
    outcome, including one that turns out not to be a pdf or to already be
    deleted, because a person who explicitly chose a score is exactly the
    caller a silent omission would mislead. Omitted, the selection is built
    from the library itself and is already narrowed to live pdf scores -
    nothing here filters a folder's non-pdf or deleted scores in loudly,
    because nobody named them.
    """

    score_ids: list[Count] | None = Field(default=None, min_length=1, max_length=500)
    collection: str | None = None
    # False by default: an extracted row already on a score is real work an
    # earlier pass did, and the extractor keeps improving - see transcribe()'s
    # own note on why a re-run replaces the extracted row and never the
    # edited one. reconvert=True asks for exactly that replacement, in bulk.
    # An EDITED row is never replaced regardless of this flag - see
    # _batch_process_one.
    reconvert: bool = False


@router.post(
    "/transcribe/batch", tags=[TAG_TRANSCRIPTION], response_model=TranscribeBatchTriggerOut
)
def start_transcribe_batch(body: TranscribeBatchIn):
    """Start transcribing many scores in one background pass and return the
    status left behind by whichever pass (this one, or one already running)
    is now current - poll GET /transcribe/batch/status for progress, the
    same pattern POST /scan uses (issue #55).

    Selects every live pdf score named by `score_ids`, or every live pdf
    score under `collection`, or every live pdf score in the whole library
    if neither is given - see TranscribeBatchIn. Reports what happened to
    EVERY one of them: transcribed, already had a transcription (and why
    that was not replaced - an edited one never is), not extractable (with
    the extractor's own reason), or errored (with its own reason) - never a
    silent skip.

    Refuses (`started: false`, nothing changed) only if a batch is already
    running. A library scan running at the same time is not held against
    this in either direction - see transcribe_batch.start_batch's docstring
    for why the two may run together safely.
    """
    if body.score_ids and body.collection is not None:
        raise HTTPException(422, "give score_ids or collection, not both")
    conn = connect()
    if body.score_ids:
        # De-duplicated, order preserved, and NOT filtered to live pdf scores
        # here - every id given gets its own outcome, including "not a pdf"
        # or "in the trash", from _batch_process_one. See TranscribeBatchIn.
        score_ids = list(dict.fromkeys(body.score_ids))
    else:
        where, params = ["deleted_at IS NULL", "file_type = 'pdf'"], []
        if body.collection is not None:
            where.append("collection = ?")
            params.append(body.collection)
        rows = conn.execute(
            f"SELECT id FROM scores WHERE {' AND '.join(where)} ORDER BY id", params
        ).fetchall()
        score_ids = [r["id"] for r in rows]
    started = transcribe_batch.start_batch(_batch_process_one, score_ids, body.reconvert)
    return {"started": started, **transcribe_batch.batch_status()}


@router.get(
    "/transcribe/batch/status", tags=[TAG_TRANSCRIPTION], response_model=TranscribeBatchStatusOut
)
def get_transcribe_batch_status():
    """Where the most recent (or currently running) bulk transcription pass
    stands - see transcribe_batch.batch_status()."""
    return transcribe_batch.batch_status()


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
    absolute path segment.

    DELIBERATELY NOT HELD against a running scan or a move, unlike the
    library-management routes below. This only ever creates a file at a path
    nothing claims - it never moves or removes one - so it cannot invalidate a
    scan's listing, and holding it would refuse the second of two uploads in a
    row for the scan the first one started. See scanner.hold_library_still for
    the full argument and for what catches the one overlap that matters."""
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

# What a single path segment may be.
#
# No separators (those are what splits the string in the first place), no empty
# segment, and nothing that is only dots - "." and ".." are the two that escape
# a directory, and a segment of "..." is not a name anybody meant to type
# either. Control characters are excluded because a newline in a filename is a
# filename that cannot be read back out of a log or a listing.
#
# AND THE CHARACTERS WINDOWS ITSELF WILL NOT TAKE, which is not tidiness and is
# not a portability nicety - each one is a way for the text of a path to say one
# thing and the filesystem to do another, and this whole section's guarantee is
# that where a file goes is where the caller asked for it to go:
#
#   `:` IS AN NTFS ALTERNATE DATA STREAM. `evil:stream.pdf` is not a file called
#   "evil:stream.pdf"; it is a hidden stream attached to a file called "evil".
#   The move succeeded, the bytes went into the stream, and the content hash
#   read back THROUGH the stream matched - so every check this code makes passed
#   - while the library got a 0-byte "evil" the scanner cannot read, and any
#   copy or backup that does not preserve streams drops the music silently.
#
#   `< > " | ? *` are refused by Win32 outright, so on Windows they are a 500
#   from deep inside a rename, and on Linux they are a filename that cannot be
#   copied to a Windows machine or a common network share. Refused here so the
#   answer is the same sentence on both.
#
# The trailing-dot and trailing-space rule is separate and lives in
# _reject_trailing - see there for why a regex is the wrong place for it.
_VALID_SEGMENT = re.compile(r'^(?!\.+$)[^/\\:<>"|?*\x00-\x1f]+$')

# Windows silently STRIPS a trailing dot or space from every path component -
# `"Music."` and `"Music"` are the same directory, and `"Study .pdf"` is
# `"Study.pdf"`. That is a divergence between what the database records and what
# is on disk, and in one case it is a way straight through this section's
# guarantees: `.fermata-trash.` passes the trash-folder check on its text and
# then lands, on disk, INSIDE the trash folder. A live score would sit in the
# folder scans skip - so it is marked missing on the next pass - and in the
# folder docs/deployment.md tells people they may empty by hand.
_TRAILING = " ."


def _reject_trailing(part: str, field: str) -> None:
    """Refuse a segment that a filesystem would quietly rename.

    Not folded into _VALID_SEGMENT because the two say different things and one
    of them needs its own sentence: the regex is about characters a name may not
    CONTAIN, and this is about a name the filesystem will not STORE AS WRITTEN.
    A caller who typed a trailing dot deserves to be told that rather than to
    find their score somewhere else.
    """
    if part and part[-1] in _TRAILING:
        raise HTTPException(
            422,
            f"{field} segment {part!r} ends in a space or a dot. Windows silently drops "
            "those, so the folder Fermata recorded and the folder on disk would be two "
            "different places - and one of them could be inside Fermata's own trash.",
        )

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
    # NOT .strip()ped, and that is the same rule as everything else here rather
    # than an omission. Stripping is sanitising: "Classical " came in, a folder
    # called "Classical" came out, and the caller was never told the two are not
    # the same request. It is also precisely the trailing character
    # _reject_trailing exists to refuse, so stripping first would have quietly
    # disarmed that check for every space. An empty string is still the library
    # root; a string that is only a space is now a refusal, which is right.
    raw = (folder or "").replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise HTTPException(422, f"{field} must be inside the library, not an absolute path")
    parts = tuple(p for p in raw.split("/") if p)
    for part in parts:
        if not _VALID_SEGMENT.match(part):
            raise HTTPException(
                422,
                f"{field} segment {part!r} is not a usable folder name - no separators, no "
                "'..', no control characters, and none of : < > \" | ? *",
            )
        _reject_trailing(part, field)
    # AFTER the trailing-dot rule, and that order is the whole point of the
    # rule existing: ".fermata-trash." is not equal to ".fermata-trash", so it
    # passes this check on its text, and Windows then drops the dot and puts the
    # score inside the trash folder anyway.
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
    # Not stripped, for the reason _safe_parts gives: "Study.pdf " and
    # "Study.pdf" are two different requests, and only one of them is a name
    # Windows will actually store.
    cleaned = name or ""
    if not cleaned or not _VALID_SEGMENT.match(cleaned):
        raise HTTPException(
            422,
            "filename is not a usable file name - no folders, no '..', no control "
            "characters, and none of : < > \" | ? * (a ':' is not part of a name on "
            "Windows at all; it opens a hidden data stream on the file before it)",
        )
    _reject_trailing(cleaned, "filename")
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


def _same_file(a: Path, b: Path) -> bool:
    """Are these two paths the same file on disk?

    Asked, rather than assumed from the text of the paths, because on a
    case-insensitive filesystem two different strings routinely name one file -
    which is the whole of the case-only rename problem below. False when either
    path is not there, which is the answer every caller here wants.
    """
    try:
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return False


def _fs_refused(exc: OSError) -> HTTPException:
    """The filesystem would not take this path, said as a refusal not a crash.

    The segment rules cannot know what a particular filesystem will accept: a
    name Windows reserves outright (NUL, CON), a path over its length limit, a
    disk that is full, a mount that went read-only. Every one of those is
    something a person can rename or free their way around, and none of them is
    a bug in Fermata - so they come back as a 422 carrying what the filesystem
    actually said, the same way create_folder already answers.
    """
    return HTTPException(422, f"your filesystem would not accept that path: {exc}")


def _move_file_on_disk(src: Path, dest: Path) -> None:
    """Move one file, creating the folders above it, never overwriting.

    os.replace would be atomic and is exactly wrong here: it replaces the
    destination silently, which is the one thing rule 3 above forbids. The
    existence check is racy against something outside Fermata writing the same
    path in the same millisecond, and that race is accepted - the alternative is
    an exclusive create plus a copy plus a delete, which turns a rename into a
    read and write of the whole file for a case that needs a second process
    writing into the library at the exact moment of a move.

    A DESTINATION THAT IS THE SOURCE IS NOT IN THE WAY. On NTFS - which is the
    platform this is deployed on - "Toccata.pdf" and "toccata.pdf" are one file,
    so correcting a score's capitalisation asks to move a file onto itself and
    the existence check above would refuse it, blaming the score for being where
    it is. _same_file asks the filesystem instead of the path text.

    THE EXDEV FALLBACK IS NARROWED TO EXDEV, and that is a fix rather than
    tidying. shutil.move copies and unlinks, and copying onto an existing
    destination OVERWRITES it - so catching every OSError and falling through to
    it turned the one case rule 3 forbids (a file already there, appearing in the
    gap between the check and the rename, which is exactly the race an upload can
    produce) into a silent overwrite of somebody's file.
    """
    if dest.exists() and not _same_file(src, dest):
        raise HTTPException(409, f"there is already a file at {dest}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _fs_refused(exc) from None
    try:
        src.rename(dest)
        return
    except FileExistsError:
        # Windows' rename refuses an existing destination outright; POSIX's
        # replaces it silently, which is why the check above exists at all.
        raise HTTPException(409, f"there is already a file at {dest}") from None
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise _fs_refused(exc) from None
    # Different filesystems under one library tree (a bind mount inside a bind
    # mount). Re-checked, because the copy below would overwrite where the
    # rename above would not.
    if dest.exists() and not _same_file(src, dest):
        raise HTTPException(409, f"there is already a file at {dest}")
    try:
        shutil.move(str(src), str(dest))
    except OSError as exc:
        raise _fs_refused(exc) from None


_MOVE_CHANGED_ADVICE = "Scan the library so Fermata re-reads it, then move the score again."

# WHY THE RESTORE PATH NEEDS ITS OWN SENTENCE. The advice this refusal used to
# give everybody - scan the library and try again - was actively harmful before
# the rollback above existed, because the file was left at the new path and a
# scan filed it as a brand new historyless score. With the rollback it is no
# longer harmful, but on the restore path it is still USELESS: the file is back
# in the trash, and scans skip the trash folder by design, so the one action the
# message asks for provably cannot change the outcome. What is actually true
# there is that the file in the trash is no longer the file that was deleted,
# and a person has two real ways out.
_RESTORE_CHANGED_ADVICE = (
    "The file in the trash is not the one this score was deleted with - check it: put the "
    "original back in the trash folder and restore again, or, if this score is not worth "
    "keeping, delete it for good."
)


def _relink_moved_file(conn, row, dest_rel: str, advice: str = _MOVE_CHANGED_ADVICE) -> None:
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
            "the file's contents are not the ones Fermata recorded for this score, so it "
            "stopped rather than attach this score's practice history and transcription to "
            f"different music. Nothing was changed. {advice}",
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
    # `and not _same_file(...)` is what makes correcting a score's
    # capitalisation possible on NTFS, where "Toccata.pdf" and "toccata.pdf" are
    # one file: without it the file in the way IS this score's own file, and the
    # plan blocked the rename while blaming the score for existing.
    dest = _resolve_in_library(dest_rel)
    if dest.exists() and not _same_file(source, dest):
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

    NOT held against a running scan, unlike every other route in this section:
    mkdir moves nothing and removes nothing, and a scan has no opinion about an
    empty directory. See scanner.hold_library_still for the two exemptions and
    why each one is safe.
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
    # THE SAME EXCEPTION THE FILE PATH NEEDED, for the same reason and on the
    # same platform: "Inbox" and "inbox" are one directory on NTFS, so fixing a
    # folder's capitalisation asks to rename it onto itself and a bare
    # exists() refused it - "inbox already exists", naming the folder as its own
    # obstacle. Asking the filesystem separates the two cases a path string
    # cannot: the same directory under another spelling (allowed - it IS the
    # rename), and a genuinely different directory that happens to be there
    # (still refused, because renaming onto it would merge two folders' scores
    # into one with nothing said).
    if dest.exists() and not _same_file(source, dest):
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


def _now(conn) -> str:
    """One clock, UTC, in exactly the format every other timestamp here uses.

    Read from SQLite rather than from Python so a value written by hand and one
    written by a DEFAULT cannot come out in two different formats - which is
    what would happen the first time anything compared them as text, and every
    date in this schema is compared as text.
    """
    return conn.execute("SELECT datetime('now')").fetchone()[0]


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
                file_moved = source.is_file()
                if file_moved:
                    _move_file_on_disk(source, _resolve_in_library(trash_rel))
                # missing_since is CARRIED when there was no file to move, and
                # cleared when there was. A score whose file had already gone is
                # a real thing to want out of your library - it is the case where
                # deleting is most obviously the right answer - so this refuses
                # nothing; but it must not then claim a file was put in the trash.
                # `file_moved` on the response is that claim withdrawn, and the
                # mark stays so restoring puts the score back in exactly the
                # state it was in: present in the library, flagged as missing.
                conn.execute(
                    """UPDATE scores
                          SET path = ?, deleted_from = ?, deleted_at = datetime('now'),
                              missing_since = ?
                        WHERE id = ?""",
                    (
                        trash_rel,
                        row["path"],
                        None if file_moved else (row["missing_since"] or _now(conn)),
                        score_id,
                    ),
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
        # null, not a path, when nothing was moved there - see `file_moved`.
        "trashed_to": trash_rel if file_moved else None,
        "file_moved": file_moved,
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

    A SCORE WITH NO FILE IN THE TRASH STILL COMES BACK, and this used to be a
    409 that stranded it. Two ways to reach that state - the score was already
    marked missing when it was deleted, or somebody emptied the trash folder by
    hand, which docs/deployment.md says they may - and in both the row is the
    only thing left worth having. Refusing meant the score could never leave the
    trash except by being destroyed, taking its tags and transcription with it,
    which is the opposite of what a trash is for. It is restored to the library
    marked missing instead: exactly the state a score whose file has gone is
    supposed to be in, from which putting the file back and scanning recovers it
    by itself. `file_restored` says which of the two happened.
    """
    _require_library()
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                row = _deleted_row(conn, score_id)
                came_from = row["deleted_from"] or row["path"]
                target = _free_path(came_from, conn, exclude_id=score_id)
                source = _resolve_in_library(row["path"])
                file_restored = source.is_file()
                conn.execute(
                    "UPDATE scores SET deleted_at = NULL, deleted_from = NULL WHERE id = ?",
                    (score_id,),
                )
                if not file_restored:
                    _repoint_row(conn, score_id, target)
                    conn.execute(
                        "UPDATE scores SET missing_since = ? WHERE id = ? "
                        "AND missing_since IS NULL",
                        (_now(conn), score_id),
                    )
                else:
                    _move_file_on_disk(source, _resolve_in_library(target))
                    # THE FILESYSTEM HALF OF THE ROLLBACK, and its absence was
                    # the one place in this section where a refusal stranded a
                    # score rather than leaving it alone. _relink_moved_file
                    # refuses a file whose bytes no longer match the row - the
                    # right answer - but the file has already MOVED by then, and
                    # write_tx only rolls back the database. So the trash listed
                    # a score whose file was not in the trash: restoring it
                    # refused for ever, the next scan filed the file as a brand
                    # new historyless score, and the error told the person to
                    # scan, which is what caused that. _apply_plan has had these
                    # three lines from the start; this path had not.
                    try:
                        _relink_moved_file(conn, row, target, _RESTORE_CHANGED_ADVICE)
                    except Exception:
                        try:
                            _resolve_in_library(target).rename(source)
                        except OSError:  # pragma: no cover - refused twice
                            log.error(
                                "could not put %s back in the trash at %s after a refused "
                                "restore - the file is at the new path and the database has "
                                "been rolled back",
                                target,
                                row["path"],
                            )
                        raise
                    _tidy_trash_folder(row["path"])
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    conn = connect()
    return {
        "restored": score_id,
        "restored_from": came_from,
        "restored_to": target,
        "file_restored": file_restored,
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


# ---------------------------------------------------------------------------
# HOW IS THIS PIECE GOING (#57).
#
# The practice page answers "how am I doing" across the library. This answers
# the other question the issue names and treats it as a different one, because
# it is: deciding what to practise next needs the per-piece picture as much as
# the overall one, and the two are not slices of each other. "When did I last
# play this" has no window; "where did the time go this quarter" has nothing
# else.
#
# ONE ENDPOINT AND NOT SIX, and every figure on it computed here. A client that
# had to fetch the sessions for one score and total them itself would be
# writing the arithmetic this module already owns - and the second reader of
# this surface (the MCP server, #31) would then write it a third time,
# slightly differently, with nothing to say which of the three was right. See
# issue #32's design rules: every field readable through the documented API, so
# any future integration wraps one source of truth.
#
# WHAT IS NOT HERE, deliberately: no streak and no run of days, no trend line
# through the tempo points, no average rating, and no comparison between this
# window and any other. All four are listed under "what is deliberately absent"
# in docs/practice-data.md and asked for by name in issue #3's own "deliberately
# not" list. A per-piece view is where each would be most tempting and would do
# the most damage - a piece is put down and picked up again by design, and a
# run of days is a number that punishes exactly that.
# ---------------------------------------------------------------------------


@router.get(
    "/scores/{score_id}/practice/progress",
    tags=[TAG_PRACTICE],
    response_model=ScoreProgressOut,
)
def score_practice_progress(
    score_id: RowId,
    days: int = practice.DEFAULT_HISTORY_DAYS,
    today: str | None = None,
    limit: int = practice.DEFAULT_SCORE_SESSION_LIMIT,
):
    """How one piece is going: its whole record, the last stretch of days a
    piece at a time, the tempo each session was practised at, how the time
    split between section work and run-throughs, what was written about it,
    and any goal set about this piece.

    Every figure is grouped by the practice day, which the response names in
    `grouped_by` - see the model for what that means for a session whose day
    was not recorded. `all_time` is the one block the window does not bound.

    A piece in the trash answers this in full and says so: the practice still
    counts, and only the way into the library goes away (#56).
    """
    if not 1 <= days <= practice.MAX_HISTORY_DAYS:
        raise HTTPException(422, f"days must be between 1 and {practice.MAX_HISTORY_DAYS}")
    if not 1 <= limit <= practice.MAX_SCORE_SESSION_LIMIT:
        raise HTTPException(
            422, f"limit must be between 1 and {practice.MAX_SCORE_SESSION_LIMIT}"
        )
    end = _today(today)
    start = end - timedelta(days=days - 1)
    first, last = start.isoformat(), end.isoformat()
    conn = connect()
    # _score_row and NOT _live_score_row: a piece in the trash still has every
    # hour that was ever spent on it, and refusing to report them would be this
    # application deciding a deletion erases practice - which is the one thing
    # the whole feature is built not to do. The row says `deleted` instead.
    row = _score_row(conn, score_id)
    all_time = practice.score_all_time(conn, score_id)
    listed = practice.score_sessions(conn, score_id, first, last, limit=limit)
    return {
        "score_id": score_id,
        "title": row["title"],
        "deleted": row["deleted_at"] is not None,
        # Asked of the whole record and not of the window: a piece practised
        # solidly last year and untouched since has been practised, and a view
        # that greeted it with "nothing logged yet" would be wrong about the
        # one thing this page exists to remember.
        "practised": all_time["sessions"] > 0,
        "start": first,
        "end": last,
        "grouped_by": practice.GROUPED_BY,
        "all_time": all_time,
        "window": practice.period_facts(conn, first, last, scope="score", score_id=score_id),
        "tempo": practice.tempo_progression(conn, score_id, first, last),
        "modes": practice.mode_totals(conn, score_id, first, last),
        "ratings": practice.rating_counts(conn, score_id, first, last),
        "goals": practice.score_goals(conn, score_id, first, last, end),
        "sessions": listed["sessions"],
        "session_total": listed["total"],
        "sessions_truncated": listed["truncated"],
    }


# ---------------------------------------------------------------------------
# GETTING EVERYTHING IN AND OUT (#58).
#
# Issue #32's design rule - structured, queryable, every field readable
# through the documented API - is what makes the OTHER half of #32 possible:
# the MCP server (#31) wraps this REST surface rather than reading
# SQLite directly. That rule is also what makes THIS feature nearly free.
# Nothing here invents a second notion of what a session or a goal is; export
# reads the same tables every other endpoint reads, and import writes rows
# through the same columns every other write does.
#
# THE FORMAT: one zip, always. `manifest.json` at its root is a JSON object
# naming this file's `schema_version` (db.SCHEMA_VERSION - not this
# application's own release number, which changes for reasons that have
# nothing to do with what a database holds) and carrying every table's rows
# verbatim, keyed by the table name under `tables`. `files/<hash><ext>` holds
# the bytes of every score file the export could read from disk, named by
# CONTENT HASH rather than by the score's library path - the same identity
# scanner.hash_file already gives every score, so two scores that happen to
# share content share one entry rather than two, and nothing about a
# person's folder names ever has to survive as a zip entry name (which on
# Windows has its own reserved characters - see the module comment above
# _VALID_SEGMENT). A score row whose file could not be read at export time
# (missing, or the archive was asked to leave files out with
# `include_files=false`) still has its whole row in the manifest -
# `file_included: false` says so - because the METADATA is not the same
# thing as the bytes, and losing the practice history and tags a score
# accumulated is not an acceptable price for a file that happened to be
# offline that day.
#
# ROW VALUES TRAVEL VERBATIM, deliberately, rather than through the
# normalising functions (practice.normalise_session, instruments.normalise,
# ...) every ordinary write goes through. Those functions exist to validate
# and default a REQUEST from a person typing into a form today; an archived
# row already passed them once, when it was first written, and running it
# through them again on the way back in would let today's defaults quietly
# overwrite yesterday's actual values - the opposite of a lossless round
# trip. `_dump_table` and `_insert_row` below are the only two functions
# either direction needs, and neither one hand-picks which columns matter:
# every column the live table has going out, every key an archived row
# carries coming back in. That is what keeps this feature from becoming a
# third hand-mirrored copy of the schema, alongside the bugs issues #143 and
# #146 shipped from exactly that shape of mistake one layer up in the API
# responses built from these same tables.
#
# WHAT IMPORT DOES NOT DO: replace, sync or merge-by-matching. Every row from
# a validated archive is INSERTED as a new row with a fresh id - the only
# exception is a tag whose NAME already exists in the target library, which
# is reused rather than duplicated (a tag is purely a name; two rows with the
# same name is not a second tag, it is the same tag counted twice). Importing
# the same archive twice therefore creates two copies of every score, session
# and goal - which is the same trade-off `POST /api/library/move` and
# `POST /api/scores/{id}/move` make in the other direction (refuse to
# overwrite, land beside it instead): a merge that could guess wrong about
# which of two similarly-shaped rows is "the same one" would risk silently
# discarding somebody's practice history, and this feature's one absolute
# rule (see the module docstring on this section's own issue) is that it
# never does that. The right library to import into is an empty one - a
# fresh install, or one just scanned onto an empty database - and every test
# of this feature below imports into exactly that.
#
# TRANSACTIONAL, AND WHAT THAT ACTUALLY COVERS. Validation - the archive is a
# real zip, `manifest.json` parses, its schema_version matches, every table
# is the right shape, every foreign key inside the archive resolves to a row
# also in the archive, every archived file's bytes hash to what the archive
# itself claims for them - all happens BEFORE anything is written, exactly
# like _scan's "decide first, write second" (see scanner.py's module
# comment for the bug that taught this codebase that lesson the first time).
# So the ordinary rejection - a malformed archive, the wrong schema version -
# never reaches write_tx() at all: nothing was touched, and there is nothing
# to roll back. The rarer case is a validated archive that still fails
# partway through being applied (a goal whose owner/period_start collides
# with one already in the target library, say - the one uniqueness rule
# validation cannot check without knowing what is already there). write_tx()
# rolls the database back the same way every other write in this file relies
# on it to; the only thing import adds is doing the same for the files it
# had already written to the library before the failure - see
# `written_paths` in import_library.
# ---------------------------------------------------------------------------

# The manifest's own name for what it is, checked on the way in so an
# arbitrary zip someone renamed to .zip and uploaded here is refused with a
# sentence about what it actually is, rather than an obscure KeyError several
# steps into being treated as one of Fermata's own exports.
EXPORT_FORMAT = "fermata-export"
EXPORT_MANIFEST_NAME = "manifest.json"
EXPORT_FILES_DIR = "files"

# Every table this feature carries, and the only tables it carries - a
# manifest with a different set of keys under `tables` is refused rather than
# read partially (see LEGACY_OPTIONAL_TABLES below for the one deliberate
# exception, for archives written before this tuple grew). Order matters only
# for readability here; `_apply_import` decides the real insert order
# (instruments and tags before the scores that reference them, scores before
# everything that references A SCORE).
EXPORT_TABLE_NAMES = (
    "instruments",
    "tags",
    "scores",
    "score_tags",
    "transcriptions",
    "practice_sessions",
    "practice_goals",
    "settings",
    # #6's two, carried so a setlist a person arranged by hand - which cannot
    # be regenerated from anything on disk - survives an export/import round
    # trip. `setlist_scores` rides ON the scores it references: on the way out
    # its rows for a score this export is leaving out are dropped (the same
    # thing score_tags does), and on the way in both its foreign keys follow
    # this import's id remap (setlist_id to the new setlist, score_id to the
    # new score), exactly as practice_sessions.score_id does.
    "setlists",
    "setlist_scores",
    # #243's two: docs/practice-data.md already decided drill history is
    # durable practice data - structured rows, kept for later reading, the
    # same status practice_sessions has - so it rides the same export/import
    # round trip practice_sessions does. Each ONLY references a
    # practice_sessions row (`session_id`, nullable - most attempts are never
    # linked to one, see db.py's note on that column), never a score
    # directly, so on the way in `session_id` follows this import's id remap
    # (the practice_sessions old-id to new-id map) exactly as
    # setlist_scores.score_id follows the score map. Neither table is
    # filtered by score the way practice_sessions/practice_goals are,
    # because neither one references a score at all.
    "trainer_attempts",
    "trainer_chord_attempts",
    # #236's two. A named scope is exactly the kind of thing this feature
    # exists for: a person arranged it by hand, nothing on disk can
    # regenerate it, and practice_sessions.preset_id points at it - so an
    # archive that carried the sessions but not the presets would restore a
    # history whose "what was practised" column named rows that no longer
    # exist. `trainer_scope_preset_strings` rides ON its preset exactly as
    # setlist_scores rides on its setlist: its `preset_id` follows this
    # import's id remap, and so does practice_sessions.preset_id. Neither
    # table references a score, so neither is filtered by one.
    "trainer_scope_presets",
    "trainer_scope_preset_strings",
)

# Tables added to EXPORT_TABLE_NAMES after some archives already on disk were
# written. A manifest missing one of these under `tables` was produced by a
# Fermata that predates the table, not a malformed archive - _apply_import
# treats a missing one as an empty table, per #243 (refusing every backup
# taken before today because a table it never knew about is absent would be
# worse than importing with an empty drill history). Every OTHER name still
# has to match exactly, per the comment above EXPORT_TABLE_NAMES.
#
# #236's two are here for the identical reason #243's two are, and the
# reasoning carries over unchanged: an archive written yesterday cannot have
# a `trainer_scope_presets` key, and refusing every backup a person already
# holds - the ones this feature exists to make restorable - because a table
# that did not exist when they took it is absent would be a far worse
# outcome than restoring with no named scopes. A session in such an archive
# has no `preset_id` either (the column did not exist), so importing one
# with these two empty leaves nothing dangling and nothing to guess.
LEGACY_OPTIONAL_TABLES = (
    "trainer_attempts",
    "trainer_chord_attempts",
    "trainer_scope_presets",
    "trainer_scope_preset_strings",
)


def _dump_table(conn, sql: str, params=()) -> list[dict]:
    """Every row a query returns, as plain dicts - the shape both the
    manifest's JSON and `_insert_row` want. Never SELECT * blindly filtered
    down by hand afterwards: whatever columns the query names are exactly the
    columns that travel, so a table's own SELECT * carries a column added to
    it tomorrow with no edit needed here."""
    return [dict(row) for row in conn.execute(sql, params)]


def _build_export_manifest(conn, *, include_trash: bool) -> dict:
    """Everything #32 says has to be readable through this API, read through
    it in bulk. See the module comment above for what each table means here
    and why practice_sessions/practice_goals are exported in full (never
    filtered by trash) with only their `score_id` touched.

    `file_included` is decided here, from the real filesystem, rather than
    left for the caller to work out a second time when it actually writes the
    zip - one place decides whether a score's bytes are coming along, and the
    manifest and the archive's `files/` folder can never disagree about it.
    """
    root = _library_dir()
    scores = _dump_table(
        conn,
        "SELECT * FROM scores" + ("" if include_trash else " WHERE deleted_at IS NULL")
        + " ORDER BY id",
    )
    score_ids = {row["id"] for row in scores}
    for row in scores:
        path = root / row["path"] if root.is_dir() else None
        row["file_included"] = bool(path and path.is_file())

    score_tags = [
        dict(row)
        for row in conn.execute("SELECT * FROM score_tags ORDER BY score_id, tag_id")
        if row["score_id"] in score_ids
    ]
    transcriptions = [
        dict(row)
        for row in conn.execute("SELECT * FROM transcriptions ORDER BY id")
        if row["score_id"] in score_ids
    ]

    # A session or goal about a score this export is leaving out (a trashed
    # one, with include_trash=False) keeps its row - the hours were still
    # spent - but loses the reference, the same thing actually destroying
    # that score (DELETE /api/trash/{id}) would do to it via ON DELETE SET
    # NULL (see db.py's notes on that choice). Never filtered out entirely:
    # that is the one thing this feature promises never to do to practice
    # history.
    practice_sessions = _dump_table(
        conn, "SELECT * FROM practice_sessions WHERE owner = ? ORDER BY id", (DEFAULT_OWNER,)
    )
    practice_goals = _dump_table(
        conn, "SELECT * FROM practice_goals WHERE owner = ? ORDER BY id", (DEFAULT_OWNER,)
    )
    for row in (*practice_sessions, *practice_goals):
        if row["score_id"] is not None and row["score_id"] not in score_ids:
            row["score_id"] = None

    # Setlists travel whole; their membership rows travel only for scores that
    # are actually in this export. A setlist_scores row is pure association
    # (like score_tags) - it says nothing once the score it names is gone - so
    # a member whose score was left out (a trashed one, include_trash=False) is
    # dropped from the archive rather than carried as a dangling reference,
    # exactly the filter score_tags/transcriptions above use. The setlist row
    # itself still travels; it simply arrives with that member missing.
    setlists = _dump_table(
        conn, "SELECT * FROM setlists WHERE owner = ? ORDER BY id", (DEFAULT_OWNER,)
    )
    setlist_scores = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM setlist_scores ORDER BY setlist_id, position"
        )
        if row["score_id"] in score_ids
    ]

    # #243's two: every attempt either drill ever logged, in the order it was
    # asked (ORDER BY id, the same order practice_sessions travels in).
    # Neither table references a score - only `session_id`, nullable, into
    # practice_sessions - so unlike practice_sessions/practice_goals above
    # there is no score_id to null out here; practice_sessions itself is
    # never filtered by score (or by anything but owner), so every session_id
    # a row in either table could name is already in this export.
    trainer_attempts = _dump_table(
        conn, "SELECT * FROM trainer_attempts WHERE owner = ? ORDER BY id", (DEFAULT_OWNER,)
    )
    trainer_chord_attempts = _dump_table(
        conn,
        "SELECT * FROM trainer_chord_attempts WHERE owner = ? ORDER BY id",
        (DEFAULT_OWNER,),
    )

    # #236's two: every named scope, and the string set of each. The child
    # rows are filtered to the presets actually travelling - the same filter
    # setlist_scores/score_tags use, and here it can only ever be a no-op
    # (presets are filtered by owner alone, exactly as their strings are),
    # which is the point: it states the invariant rather than assuming it.
    trainer_scope_presets = _dump_table(
        conn,
        "SELECT * FROM trainer_scope_presets WHERE owner = ? ORDER BY id",
        (DEFAULT_OWNER,),
    )
    preset_ids = {row["id"] for row in trainer_scope_presets}
    trainer_scope_preset_strings = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM trainer_scope_preset_strings ORDER BY preset_id, string_number"
        )
        if row["preset_id"] in preset_ids
    ]

    return {
        "format": EXPORT_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "fermata_version": version_info.version(),
        "exported_at": _now(conn),
        "owner": DEFAULT_OWNER,
        "include_trash": include_trash,
        "tables": {
            "instruments": _dump_table(
                conn, "SELECT * FROM instruments WHERE owner = ? ORDER BY id", (DEFAULT_OWNER,)
            ),
            "tags": _dump_table(conn, "SELECT * FROM tags ORDER BY id"),
            "scores": scores,
            "score_tags": score_tags,
            "transcriptions": transcriptions,
            "practice_sessions": practice_sessions,
            "practice_goals": practice_goals,
            "settings": _dump_table(conn, "SELECT * FROM settings ORDER BY owner, key"),
            "setlists": setlists,
            "setlist_scores": setlist_scores,
            "trainer_attempts": trainer_attempts,
            "trainer_chord_attempts": trainer_chord_attempts,
            "trainer_scope_presets": trainer_scope_presets,
            "trainer_scope_preset_strings": trainer_scope_preset_strings,
        },
    }


@router.get(
    "/export",
    tags=[TAG_PORTABILITY],
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
def export_library(include_trash: bool = True, include_files: bool = True):
    """Everything Fermata knows, as one zip archive: every score row,
    transcription (content, source and every disclosure field), practice
    session, goal, trainer attempt (both drills), tag, favourite, instrument,
    setting and setlist (with its ordered membership), plus the score files
    themselves - see the module comment above this section for the archive's
    exact shape.

    `include_trash` (default true) decides whether a score currently in the
    trash - deleted but not yet destroyed, see #56 - travels too. Leaving it
    true is what makes an export a real backup: a restorable score left out
    of one is data loss the moment the original library is gone. Set it
    false only to deliberately leave the trash behind; either way, every
    session and goal ever logged travels regardless (see
    `_build_export_manifest`).

    `include_files` (default true) decides whether the score files' own
    bytes are bundled alongside the database rows that describe them. Score
    files are already ordinary files in a folder - the starting point issue
    #58 itself names - so `include_files=false` is for someone who is going
    to copy the library folder across by other means (rsync, a drive image)
    and wants Fermata's own export to carry only the part that is not
    already portable that way. `POST /api/import` still restores every
    field either way; only the files themselves are what a false here
    leaves for the person to bring across on their own.

    No response_model: like GET .../file and .../thumb, this answers with
    real bytes (a zip archive) rather than JSON - `response_class=Response`
    is what keeps /openapi.json from advertising `application/json`
    alongside it (see test_binary_routes_do_not_advertise_a_json_content_type).
    """
    conn = connect()
    manifest = _build_export_manifest(conn, include_trash=include_trash)
    root = _library_dir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if include_files:
            written_hashes: set[str] = set()
            for row in manifest["tables"]["scores"]:
                if not row["file_included"] or row["hash"] in written_hashes:
                    continue
                suffix = PurePosixPath(row["path"]).suffix.lower()
                zf.write(root / row["path"], f"{EXPORT_FILES_DIR}/{row['hash']}{suffix}")
                written_hashes.add(row["hash"])
        else:
            # Said outright in the manifest, not left to be inferred from an
            # empty files/ folder - a reader of the archive alone (a person,
            # or a future import from an older Fermata that a version bump
            # has taught to accept this schema_version) must not conclude a
            # file is missing from the LIBRARY when it was only ever left out
            # of THIS ARCHIVE on purpose.
            for row in manifest["tables"]["scores"]:
                row["file_included"] = False
        zf.writestr(EXPORT_MANIFEST_NAME, json.dumps(manifest, indent=2))
    filename = "fermata-export-" + re.sub(r"[^0-9A-Za-z]+", "", manifest["exported_at"]) + ".zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _validate_import_score_path(path) -> None:
    """Refuse an archived score path that is not a safe library-relative
    location, before anything from the archive is written anywhere.

    Deliberately NOT `_safe_parts`, which is the same segment rules applied
    to where a MOVE may put a file - and refuses `scanner.TRASH_DIR_NAME` as
    a destination on purpose, because nothing may be moved there except
    through DELETE /api/scores/{id}. A trashed score's own archived path
    legitimately starts there; this only has to keep every segment inside
    the library, whatever it is named, so it borrows `_VALID_SEGMENT` and
    `_reject_trailing` directly rather than the destination-only wrapper
    around them. `_resolve_in_library`, called on this same path later, is
    what actually enforces "inside the library" against the resolved
    filesystem path (catching a symlink escape no amount of text inspection
    could) - this is the check that produces a readable message for the
    ordinary case of a bad character or a `..` before that lower-level check
    ever runs.
    """
    if not isinstance(path, str) or not path:
        raise HTTPException(422, "the archive names a score with no path")
    normalised = path.replace("\\", "/")
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
        raise HTTPException(
            422, f"the archive names a score path outside the library: {path!r}"
        )
    parts = PurePosixPath(normalised).parts
    if not parts:
        raise HTTPException(422, "the archive names a score with no path")
    for part in parts[:-1]:
        if not _VALID_SEGMENT.match(part):
            raise HTTPException(
                422, f"the archive's score path segment {part!r} is not a usable name"
            )
        _reject_trailing(part, "archived score path")
    _safe_filename(parts[-1])


def _read_and_validate_manifest(zf: zipfile.ZipFile) -> dict:
    """Everything about the archive that can be checked without writing
    anything - see the module comment on why this runs to completion, naming
    every problem it can find that matters, before import touches the
    database or the library at all.
    """
    try:
        raw = zf.read(EXPORT_MANIFEST_NAME)
    except KeyError:
        raise HTTPException(
            422, f"the archive has no {EXPORT_MANIFEST_NAME} - this is not a Fermata export"
        ) from None
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            422, f"the archive's {EXPORT_MANIFEST_NAME} is not valid JSON: {exc}"
        ) from None
    if not isinstance(manifest, dict):
        raise HTTPException(422, f"the archive's {EXPORT_MANIFEST_NAME} is not a JSON object")
    if manifest.get("format") != EXPORT_FORMAT:
        raise HTTPException(
            422, "this archive was not produced by Fermata's own export (no recognised format "
            "marker) - Fermata will not guess at what a foreign zip's contents mean"
        )
    version = manifest.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise HTTPException(422, "the archive's schema_version is missing or not a whole number")
    if version != SCHEMA_VERSION:
        raise HTTPException(
            422,
            f"this archive is at schema version {version}, but this Fermata understands "
            f"{SCHEMA_VERSION}. Import only accepts an archive written by the exact version "
            "of Fermata that is running now - restore it with that version, or export again "
            "once this one has produced a database at the version it understands. Nothing "
            "has been changed.",
        )
    tables = manifest.get("tables")
    # A key this Fermata does not know at all, or a REQUIRED key missing
    # entirely, is refused exactly as before. A key in LEGACY_OPTIONAL_TABLES
    # missing entirely is the one deliberate exception (see that tuple's own
    # comment): the archive predates the table, not a malformed manifest, so
    # it is filled in here as an empty list and treated as such by every
    # check and insert below - nothing downstream needs to know the
    # difference between "this archive logged no attempts" and "this archive
    # predates attempts".
    if not isinstance(tables, dict):
        raise HTTPException(
            422,
            "the archive's manifest does not carry exactly the tables Fermata's export "
            f"writes ({', '.join(EXPORT_TABLE_NAMES)})",
        )
    provided = set(tables)
    required = set(EXPORT_TABLE_NAMES) - set(LEGACY_OPTIONAL_TABLES)
    if provided - set(EXPORT_TABLE_NAMES) or not required <= provided:
        raise HTTPException(
            422,
            "the archive's manifest does not carry exactly the tables Fermata's export "
            f"writes ({', '.join(EXPORT_TABLE_NAMES)})",
        )
    for name in LEGACY_OPTIONAL_TABLES:
        tables.setdefault(name, [])
    for name in EXPORT_TABLE_NAMES:
        if not isinstance(tables[name], list) or not all(isinstance(r, dict) for r in tables[name]):
            raise HTTPException(422, f"the archive's {name!r} table is not a list of objects")

    # Every row this import will ever index by `["id"]` (never `.get`, once
    # apply actually runs) has to have one, and has to have it as a real
    # int - checked here, once, rather than left for a missing key to
    # surface as an unhandled KeyError three functions later, after
    # write_tx() has already opened. A row failing this check is refused with
    # everything else validation finds, before anything is written.
    def _require_id(row, table_name: str) -> int:
        rid = row.get("id")
        if not isinstance(rid, int) or isinstance(rid, bool):
            raise HTTPException(
                422, f"a row in the archive's {table_name!r} table has no numeric id"
            )
        return rid

    for row in tables["instruments"]:
        _require_id(row, "instruments")
    for row in tables["tags"]:
        _require_id(row, "tags")
        if not isinstance(row.get("name"), str) or not row["name"]:
            raise HTTPException(422, "a row in the archive's 'tags' table has no name")

    # Referential integrity WITHIN THE ARCHIVE, checked before anything is
    # written - a row naming an id that is not also in the archive cannot be
    # inserted without either dropping the reference silently (which this
    # feature never does to somebody's history) or crashing partway through
    # write_tx() on a foreign-key violation, which is exactly the
    # half-applied import this whole design exists to avoid.
    instrument_ids = {row["id"] for row in tables["instruments"]}
    tag_ids = {row["id"] for row in tables["tags"]}
    score_ids = {_require_id(row, "scores") for row in tables["scores"]}
    for row in tables["scores"]:
        if "deleted_at" not in row:
            raise HTTPException(
                422, f"score {row.get('path')!r} in the archive is missing 'deleted_at'"
            )
        _validate_import_score_path(row.get("path"))
        if not isinstance(row.get("hash"), str) or not row["hash"]:
            raise HTTPException(422, f"score {row.get('path')!r} in the archive has no hash")
        iid = row.get("instrument_id")
        if iid is not None and iid not in instrument_ids:
            raise HTTPException(
                422,
                f"score {row['path']!r} in the archive names instrument {iid!r}, which is not "
                "in the archive",
            )
    for row in tables["score_tags"]:
        if row.get("score_id") not in score_ids or row.get("tag_id") not in tag_ids:
            raise HTTPException(
                422, "the archive's score_tags table names a score or tag that is not in the "
                "archive"
            )
    for row in tables["transcriptions"]:
        if row.get("score_id") not in score_ids:
            raise HTTPException(
                422, "the archive has a transcription for a score that is not in the archive"
            )
    for table_name in ("practice_sessions", "practice_goals"):
        for row in tables[table_name]:
            sid = row.get("score_id")
            if sid is not None and sid not in score_ids:
                raise HTTPException(
                    422,
                    f"the archive's {table_name} table names a score that is not in the archive",
                )
    # Every practice_sessions row needs a real id from here on - #243's two
    # trainer tables reference one (`session_id`), the same way scores are
    # required above for everything that names one.
    session_ids = {_require_id(row, "practice_sessions") for row in tables["practice_sessions"]}
    for table_name in ("trainer_attempts", "trainer_chord_attempts"):
        for row in tables[table_name]:
            sid = row.get("session_id")
            if sid is not None and sid not in session_ids:
                raise HTTPException(
                    422,
                    f"the archive's {table_name} table names a practice session that is not "
                    "in the archive",
                )
    for row in tables["settings"]:
        if not isinstance(row.get("key"), str) or not row["key"] or "value" not in row:
            raise HTTPException(422, "the archive's settings table has a malformed row")
    # Setlists and their membership (#6), checked the same way as everything
    # else that references a score: a membership row naming a setlist or a
    # score not also in the archive cannot be inserted without dropping the
    # reference or crashing partway through, so it is refused here before
    # anything is written.
    setlist_ids = {_require_id(row, "setlists") for row in tables["setlists"]}
    for row in tables["setlist_scores"]:
        if row.get("setlist_id") not in setlist_ids:
            raise HTTPException(
                422,
                "the archive's setlist_scores table names a setlist that is not in the archive",
            )
        if row.get("score_id") not in score_ids:
            raise HTTPException(
                422,
                "the archive's setlist_scores table names a score that is not in the archive",
            )
    # Named drill scopes and their string sets (#236), checked the same way:
    # a string-set row naming a preset that is not in the archive, or a
    # practice session naming one, cannot be inserted without dropping the
    # reference or crashing partway through write_tx().
    preset_ids = {_require_id(row, "trainer_scope_presets") for row in tables["trainer_scope_presets"]}
    for row in tables["trainer_scope_preset_strings"]:
        if row.get("preset_id") not in preset_ids:
            raise HTTPException(
                422,
                "the archive's trainer_scope_preset_strings table names a preset that is not "
                "in the archive",
            )
    for row in tables["practice_sessions"]:
        pid = row.get("preset_id")
        if pid is not None and pid not in preset_ids:
            raise HTTPException(
                422,
                "the archive's practice_sessions table names a preset that is not in the "
                "archive",
            )
    return manifest


def _referenced_files(manifest: dict) -> list[tuple[str, str]]:
    """The (hash, extension) of every file the manifest's scores actually
    need - one entry per distinct hash, which is what the archive itself
    only ever wrote one of (see export_library)."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for row in manifest["tables"]["scores"]:
        if not row.get("file_included"):
            continue
        h = row["hash"]
        if h in seen:
            continue
        seen.add(h)
        out.append((h, PurePosixPath(row["path"]).suffix.lower()))
    return out


def _insert_row(
    conn, table: str, row: dict, *, overrides: dict | None = None, exclude: tuple[str, ...] = ()
) -> int:
    """Insert one archived row into `table`, letting SQLite assign a fresh
    id. Every column the row carries travels across unchanged except `id`
    (never reused - see the module comment on why import always adds rather
    than trying to restore original rowids), whatever `exclude` names (a key
    the manifest carries that is not a real column of `table` at all -
    scores' own `file_included`, manifest bookkeeping rather than something
    scanner._scan_file ever wrote), and whatever `overrides` names - a
    foreign key repointed at this import's own newly-inserted parent, or
    `owner` pinned to this instance's one owner regardless of what an
    archive happens to carry for it.

    Nothing here hand-picks which REAL columns matter, which is what keeps a
    column added to a table tomorrow travelling through this path with no
    matching edit here - the same discipline `_dump_table` takes on the way
    out."""
    data = {k: v for k, v in row.items() if k != "id" and k not in exclude}
    if overrides:
        data.update(overrides)
    columns = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = conn.execute(
        f"INSERT INTO {table}({columns}) VALUES ({placeholders})", list(data.values())
    )
    return cur.lastrowid


def _apply_import(conn, manifest: dict, file_bytes: dict[str, bytes], written_paths: list) -> dict:
    """Write a validated archive's rows and files into this library. Runs
    entirely inside the caller's write_tx(), so a failure partway through -
    the one thing `_read_and_validate_manifest` cannot rule out in advance,
    a uniqueness collision against what is already in THIS library rather
    than anything wrong with the archive itself - rolls every row back the
    same way any other write in this file does. `written_paths` is filled as
    each file lands, so the caller can undo the filesystem half of that same
    failure; see import_library.
    """
    tables = manifest["tables"]

    instrument_id_map: dict[int, int] = {}
    for row in tables["instruments"]:
        instrument_id_map[row["id"]] = _insert_row(
            conn, "instruments", row, overrides={"owner": DEFAULT_OWNER}
        )

    tag_id_map: dict[int, int] = {}
    tags_reused = 0
    for row in tables["tags"]:
        existing = conn.execute(
            "SELECT id FROM tags WHERE name = ?", (row["name"],)
        ).fetchone()
        if existing is not None:
            tag_id_map[row["id"]] = existing["id"]
            tags_reused += 1
        else:
            tag_id_map[row["id"]] = _insert_row(conn, "tags", row)

    score_id_map: dict[int, int] = {}
    files_written = 0
    scores_trashed = 0
    for row in tables["scores"]:
        target_rel = _free_path(row["path"], conn, exclude_id=0)
        instrument_id = row.get("instrument_id")
        new_id = _insert_row(
            conn,
            "scores",
            row,
            exclude=("file_included",),
            overrides={
                "path": target_rel,
                "instrument_id": instrument_id_map.get(instrument_id)
                if instrument_id is not None
                else None,
            },
        )
        score_id_map[row["id"]] = new_id
        if row["deleted_at"] is not None:
            scores_trashed += 1
        data = file_bytes.get(row["hash"]) if row.get("file_included") else None
        if data is None:
            continue
        dest = _resolve_in_library(target_rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            # _free_path already checked the database and the filesystem for
            # this exact path moments ago - reachable only if something else
            # wrote there in between, which the write_tx() this runs inside
            # cannot prevent on the filesystem side. Treated as a hard stop
            # rather than a silent overwrite, the same rule every other
            # library-management route in this file holds.
            raise HTTPException(409, f"there is already a file at {dest} - import stopped")
        dest.write_bytes(data)
        written_paths.append(dest)
        # The same identity test _relink_moved_file makes for a move,
        # applied here to a file this import just wrote: the hash is
        # recomputed from what actually landed on disk and checked against
        # what the archive claimed for it, with scanner.hash_file - the one
        # function in this codebase that answers "is this the same score" -
        # rather than trusting the copy succeeded silently.
        if scanner.hash_file(dest) != row["hash"]:
            raise HTTPException(
                422,
                f"the file written for {target_rel} does not match the hash the archive "
                "recorded for it - import stopped, nothing else was written.",
            )
        files_written += 1

    for row in tables["score_tags"]:
        conn.execute(
            "INSERT OR IGNORE INTO score_tags(score_id, tag_id) VALUES (?, ?)",
            (score_id_map[row["score_id"]], tag_id_map[row["tag_id"]]),
        )

    for row in tables["transcriptions"]:
        _insert_row(
            conn, "transcriptions", row, overrides={"score_id": score_id_map[row["score_id"]]}
        )

    # #243's session_id_map: nothing referenced a practice_sessions row's own
    # id before the trainer tables existed, so nothing needed its
    # lastrowid - now trainer_attempts/trainer_chord_attempts do, the same
    # way score_id_map exists for everything that references a score.
    # #236's presets go in BEFORE the sessions that name them, for the same
    # reason instruments and tags go in before the scores that name them: a
    # session's `preset_id` is repointed at this import's own new preset row,
    # so that row has to exist and its new id has to be known first. The
    # string set follows its preset, exactly as setlist_scores follows its
    # setlist.
    preset_id_map: dict[int, int] = {}
    for row in tables["trainer_scope_presets"]:
        preset_id_map[row["id"]] = _insert_row(
            conn, "trainer_scope_presets", row, overrides={"owner": DEFAULT_OWNER}
        )
    for row in tables["trainer_scope_preset_strings"]:
        _insert_row(
            conn,
            "trainer_scope_preset_strings",
            row,
            overrides={"preset_id": preset_id_map[row["preset_id"]]},
        )

    session_id_map: dict[int, int] = {}
    for row in tables["practice_sessions"]:
        sid = row.get("score_id")
        pid = row.get("preset_id")
        session_id_map[row["id"]] = _insert_row(
            conn,
            "practice_sessions",
            row,
            overrides={
                "owner": DEFAULT_OWNER,
                "score_id": score_id_map.get(sid) if sid is not None else None,
                "preset_id": preset_id_map.get(pid) if pid is not None else None,
            },
        )

    for row in tables["practice_goals"]:
        sid = row.get("score_id")
        _insert_row(
            conn,
            "practice_goals",
            row,
            overrides={
                "owner": DEFAULT_OWNER,
                "score_id": score_id_map.get(sid) if sid is not None else None,
            },
        )

    for row in tables["settings"]:
        conn.execute(
            """INSERT INTO settings(owner, key, value) VALUES (?, ?, ?)
               ON CONFLICT(owner, key) DO UPDATE SET value = excluded.value""",
            (DEFAULT_OWNER, row["key"], row["value"]),
        )

    # Setlists after the scores they hold (#6): the setlist row gets a fresh
    # id like everything else, and each membership row's TWO foreign keys are
    # repointed - setlist_id at this import's new setlist, score_id at this
    # import's new score - the same id-remap practice_sessions.score_id rides.
    # setlist_scores has no id of its own (its key is the pair), so nothing
    # here needs its lastrowid.
    setlist_id_map: dict[int, int] = {}
    for row in tables["setlists"]:
        setlist_id_map[row["id"]] = _insert_row(
            conn, "setlists", row, overrides={"owner": DEFAULT_OWNER}
        )
    for row in tables["setlist_scores"]:
        _insert_row(
            conn,
            "setlist_scores",
            row,
            overrides={
                "setlist_id": setlist_id_map[row["setlist_id"]],
                "score_id": score_id_map[row["score_id"]],
            },
        )

    # #243's two, in logged order (see EXPORT_TABLE_NAMES' own comment):
    # `session_id` follows the practice_sessions id remap exactly as
    # setlist_scores.score_id follows the score one, and is None wherever it
    # already was - most attempts are never linked to a session at all (see
    # db.py's note on trainer_attempts.session_id).
    for row in tables["trainer_attempts"]:
        sid = row.get("session_id")
        _insert_row(
            conn,
            "trainer_attempts",
            row,
            overrides={
                "owner": DEFAULT_OWNER,
                "session_id": session_id_map.get(sid) if sid is not None else None,
            },
        )
    for row in tables["trainer_chord_attempts"]:
        sid = row.get("session_id")
        _insert_row(
            conn,
            "trainer_chord_attempts",
            row,
            overrides={
                "owner": DEFAULT_OWNER,
                "session_id": session_id_map.get(sid) if sid is not None else None,
            },
        )

    # The library just grew - see scanner.record_deliberate_shrink, which
    # (despite the name it was written under for #56's delete) only ever sets
    # the high-water mark to the library's current size. Left unset here, an
    # import's own new scores would not raise the mark themselves until the
    # next scan happened to run, and a genuinely lossy scan landing in that
    # gap would be measured against a mark that does not yet know about the
    # scores this import just restored.
    scanner.record_deliberate_shrink(conn)

    return {
        "schema_version": manifest["schema_version"],
        "exported_at": manifest["exported_at"],
        "fermata_version": manifest.get("fermata_version") or "unknown",
        "scores_imported": len(tables["scores"]),
        "scores_trashed_imported": scores_trashed,
        "files_written": files_written,
        "transcriptions_imported": len(tables["transcriptions"]),
        "tags_imported": len(tables["tags"]),
        "tags_reused": tags_reused,
        "score_tags_imported": len(tables["score_tags"]),
        "instruments_imported": len(tables["instruments"]),
        "practice_sessions_imported": len(tables["practice_sessions"]),
        "practice_goals_imported": len(tables["practice_goals"]),
        "settings_imported": len(tables["settings"]),
        "setlists_imported": len(tables["setlists"]),
        "setlist_scores_imported": len(tables["setlist_scores"]),
        "trainer_attempts_imported": len(tables["trainer_attempts"]),
        "trainer_chord_attempts_imported": len(tables["trainer_chord_attempts"]),
        "trainer_presets_imported": len(tables["trainer_scope_presets"]),
        "trainer_preset_strings_imported": len(tables["trainer_scope_preset_strings"]),
    }


@router.post("/import", tags=[TAG_PORTABILITY], response_model=ImportOut)
async def import_library(file: UploadFile, dry_run: bool = True):
    """Restore an archive written by `GET /api/export` - every score row,
    transcription, practice session, goal, tag, instrument, setting, setlist
    and drill attempt it carries, added to this library. A setlist's ordered membership
    is restored with both its foreign keys repointed at this import's own new
    setlist and score rows, so the arrangement survives intact. See the
    module comment above this section for the archive's shape, exactly what
    "added" means (never a replace or a merge-by-matching - see there for
    why), and what transactional covers here.

    `dry_run` (default true, the same default every bulk operation in this
    API uses - see #56) validates the archive completely - it really is a
    Fermata export, its schema_version matches this Fermata's, every row's
    foreign keys resolve within the archive, every archived file's bytes
    hash to what the archive itself claims for them - and reports what it
    found, WITHOUT opening a database transaction or writing a single file.
    Nothing is compared against what is already in this library on a dry
    run, which is why `tags_reused` is always 0 there - see ImportOut.

    A REJECTED IMPORT LEAVES NOTHING CHANGED, whether it was rejected by
    validation (a malformed archive, the wrong schema version - nothing was
    ever opened for writing) or partway through being applied (a real
    validated archive that still collides with what this library already
    holds - write_tx() rolls the database back, and every file this import
    had already written is removed again). See _apply_import's docstring for
    the one thing validation cannot catch in advance.
    """
    try:
        zf = zipfile.ZipFile(file.file)
    except zipfile.BadZipFile:
        raise HTTPException(422, "that is not a valid zip archive") from None
    manifest = _read_and_validate_manifest(zf)

    file_bytes: dict[str, bytes] = {}
    for file_hash, suffix in _referenced_files(manifest):
        name = f"{EXPORT_FILES_DIR}/{file_hash}{suffix}"
        try:
            data = zf.read(name)
        except KeyError:
            raise HTTPException(
                422, f"the archive is missing the file it names for {name}"
            ) from None
        # The same identity the rest of this feature relies on (sha1 of the
        # bytes), applied here to bytes still only in the archive - nothing
        # has been written yet, so there is no path to hand scanner.hash_file
        # and no reason to invent a byte-string variant of it: this is the
        # one place a mismatch has to be caught before anything is on disk
        # to check with the real function.
        if hashlib.sha1(data).hexdigest() != file_hash:
            raise HTTPException(
                422,
                f"the file at {name} in the archive does not match the hash the archive "
                "itself records for it - the archive is corrupt. Nothing has been imported.",
            )
        file_bytes[file_hash] = data

    tables = manifest["tables"]
    if dry_run:
        return {
            "dry_run": True,
            "schema_version": manifest["schema_version"],
            "exported_at": manifest["exported_at"],
            "fermata_version": manifest.get("fermata_version") or "unknown",
            "scores_imported": len(tables["scores"]),
            "scores_trashed_imported": sum(
                1 for row in tables["scores"] if row.get("deleted_at") is not None
            ),
            "files_written": len(file_bytes),
            "transcriptions_imported": len(tables["transcriptions"]),
            "tags_imported": len(tables["tags"]),
            "tags_reused": 0,
            "score_tags_imported": len(tables["score_tags"]),
            "instruments_imported": len(tables["instruments"]),
            "practice_sessions_imported": len(tables["practice_sessions"]),
            "practice_goals_imported": len(tables["practice_goals"]),
            "settings_imported": len(tables["settings"]),
            "setlists_imported": len(tables["setlists"]),
            "setlist_scores_imported": len(tables["setlist_scores"]),
            "trainer_attempts_imported": len(tables["trainer_attempts"]),
            "trainer_chord_attempts_imported": len(tables["trainer_chord_attempts"]),
            "trainer_presets_imported": len(tables["trainer_scope_presets"]),
            "trainer_preset_strings_imported": len(tables["trainer_scope_preset_strings"]),
        }

    _require_library()
    written_paths: list[Path] = []
    try:
        with scanner.hold_library_still():
            with write_tx() as conn:
                result = _apply_import(conn, manifest, file_bytes, written_paths)
    except scanner.LibraryBusy as exc:
        raise _busy(exc) from None
    except sqlite3.IntegrityError as exc:
        # The one failure validation cannot rule out in advance - see
        # _apply_import's docstring. A real, internally-consistent archive
        # can still collide with something already in THIS library once
        # apply starts writing (two goals for the same week, most likely -
        # practice_goals' own UNIQUE(owner, period_start)). write_tx() has
        # already rolled the database back by the time this runs; the
        # except Exception below (which this is also caught by, were it not
        # narrowed here first) is what undoes the files.
        for path in written_paths:
            try:
                path.unlink()
            except OSError:
                pass
        raise HTTPException(
            409,
            f"the archive could not be applied - it collides with something already in this "
            f"library ({exc}). Nothing has been imported.",
        ) from None
    except Exception:
        for path in written_paths:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return {"dry_run": False, **result}


# ---------------------------------------------------------------------------
# Trainer: per-attempt fretboard drill results (issue #27)
# ---------------------------------------------------------------------------


class TrainerAttemptIn(BaseModel):
    """One question from a fretboard drill, as answered.

    `session_id` is optional and, in the ordinary case, absent: an attempt is
    written the moment a question is answered, which is normally well before
    the drill stops and the practice_sessions row that carries its total
    TIME is written - see trainer.py's module docstring. A client that
    already has one (a session opened up front) may still pass it.

    `correct` is deliberately not a field here at all - see
    trainer.normalise_attempt for why it is always computed from
    target_note/given_note server-side rather than accepted from a caller.
    """

    session_id: Count | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)
    drill: str
    direction: str
    target_string: Count | None = None
    target_fret: Count | None = None
    target_note: str
    given_string: Count | None = None
    given_fret: Count | None = None
    given_note: str
    response_ms: Count | None = None


_ATTEMPT_COLUMNS = (
    "session_id",
    "drill",
    "direction",
    "target_string",
    "target_fret",
    "target_note",
    "given_string",
    "given_fret",
    "given_note",
    "correct",
    "response_ms",
)


@router.post("/trainer/attempts", tags=[TAG_TRAINER], response_model=TrainerAttemptOut)
def log_trainer_attempt(body: TrainerAttemptIn):
    """Record one question from a fretboard drill: what was asked, what was
    answered, and whether the two agree.

    Structured, not a free-text note (issue #32) - so "which positions get
    missed" is a query over this table rather than something parsed out of a
    session's note the way ear_training's counts are. `correct` in the
    response is what this endpoint computed, never what the request claimed.
    """
    with write_tx() as conn:
        if body.session_id is not None:
            _session_row(conn, body.session_id)
        try:
            values = trainer.normalise_attempt(**{
                k: v for k, v in body.model_dump().items() if k != "session_id"
            })
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        values["session_id"] = body.session_id
        columns = ", ".join(_ATTEMPT_COLUMNS)
        placeholders = ", ".join("?" * len(_ATTEMPT_COLUMNS))
        row = conn.execute(
            f"""INSERT INTO trainer_attempts(owner, {columns})
                VALUES (?, {placeholders})
                RETURNING *""",
            [DEFAULT_OWNER, *(values[c] for c in _ATTEMPT_COLUMNS)],
        ).fetchone()
        return trainer.attempt_dict(row)


@router.get("/trainer/attempts", tags=[TAG_TRAINER], response_model=TrainerAttemptListOut)
def list_trainer_attempts(
    drill: str = "",
    direction: str = "",
    correct: bool | None = None,
    session_id: Annotated[int | None, Query(ge=1, le=SQLITE_MAX_INTEGER)] = None,
    limit: int = practice.DEFAULT_SESSION_LIMIT,
):
    """The raw record of every question a fretboard drill has asked, newest
    first - filterable by drill, direction, whether it was answered
    correctly, or which session it belongs to.

    This is the queryable half of issue #32's promise: a client asking "which
    positions am I weak on" filters `correct=false` and groups the results by
    `target_string`/`target_fret` itself, rather than needing a bespoke
    aggregate endpoint this application would have to keep in step with every
    future drill's own idea of what a weak spot is.
    """
    if not 1 <= limit <= practice.MAX_SESSION_LIMIT:
        raise HTTPException(422, f"limit must be between 1 and {practice.MAX_SESSION_LIMIT}")
    if drill and drill not in trainer.DRILLS:
        raise HTTPException(422, f"drill must be one of {list(trainer.DRILLS)}")
    if direction and direction not in trainer.DIRECTIONS:
        raise HTTPException(422, f"direction must be one of {list(trainer.DIRECTIONS)}")
    where = ["owner = ?"]
    params: list = [DEFAULT_OWNER]
    if drill:
        where.append("drill = ?")
        params.append(drill)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    if correct is not None:
        where.append("correct = ?")
        params.append(1 if correct else 0)
    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    filters = " AND ".join(where)
    conn = connect()
    rows = conn.execute(
        f"""SELECT * FROM trainer_attempts WHERE {filters}
             ORDER BY created_at DESC, id DESC LIMIT ?""",
        [*params, limit],
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM trainer_attempts WHERE {filters}", params
    ).fetchone()["n"]
    return {
        "attempts": [trainer.attempt_dict(r) for r in rows],
        "total": total,
        "truncated": total > len(rows),
    }


# ---------------------------------------------------------------------------
# Trainer: per-attempt chord flash card results (issue #28)
# ---------------------------------------------------------------------------


class ChordShapePositionIn(BaseModel):
    string: int
    fret: int


class TrainerChordAttemptIn(BaseModel):
    """One question from the chord flash card drill, as answered - see
    trainer.normalise_chord_attempt for the pairing rules `direction`
    decides (a shown shape and a chosen chord name for `shape_to_name`, a
    tapped shape's resolved notes for `name_to_shape`).

    `correct` is deliberately not a field here at all, the same rule
    TrainerAttemptIn follows and for the same reason: it is always computed
    from tone sets, server-side, never accepted from a caller.
    """

    session_id: Count | None = Field(default=None, ge=1, le=SQLITE_MAX_INTEGER)
    drill: str
    direction: str
    target_root: str
    target_quality: str
    target_shape: list[ChordShapePositionIn] | None = None
    given_root: str | None = None
    given_quality: str | None = None
    given_notes: list[str] | None = None
    given_shape: list[ChordShapePositionIn] | None = None
    response_ms: Count | None = None


_CHORD_ATTEMPT_COLUMNS = (
    "session_id",
    "drill",
    "direction",
    "target_root",
    "target_quality",
    "target_shape",
    "given_root",
    "given_quality",
    "given_notes",
    "given_shape",
    "correct",
    "response_ms",
)


@router.post("/trainer/chord-attempts", tags=[TAG_TRAINER], response_model=TrainerChordAttemptOut)
def log_trainer_chord_attempt(body: TrainerChordAttemptIn):
    """Record one question from the chord flash card drill: what chord was
    asked, what was answered, and whether the two name the same notes.

    Structured, not a free-text note (issue #32) - so "which chords get
    missed" is a query over this table rather than something parsed out of
    a session's note. `correct` in the response is what this endpoint
    computed, never what the request claimed.
    """
    with write_tx() as conn:
        if body.session_id is not None:
            _session_row(conn, body.session_id)
        try:
            values = trainer.normalise_chord_attempt(**{
                k: v for k, v in body.model_dump().items() if k != "session_id"
            })
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        values["session_id"] = body.session_id
        columns = ", ".join(_CHORD_ATTEMPT_COLUMNS)
        placeholders = ", ".join("?" * len(_CHORD_ATTEMPT_COLUMNS))
        row = conn.execute(
            f"""INSERT INTO trainer_chord_attempts(owner, {columns})
                VALUES (?, {placeholders})
                RETURNING *""",
            [DEFAULT_OWNER, *(values[c] for c in _CHORD_ATTEMPT_COLUMNS)],
        ).fetchone()
        return trainer.chord_attempt_dict(row)


@router.get("/trainer/chord-attempts", tags=[TAG_TRAINER], response_model=TrainerChordAttemptListOut)
def list_trainer_chord_attempts(
    drill: str = "",
    direction: str = "",
    correct: bool | None = None,
    root: str = "",
    quality: str = "",
    session_id: Annotated[int | None, Query(ge=1, le=SQLITE_MAX_INTEGER)] = None,
    limit: int = practice.DEFAULT_SESSION_LIMIT,
):
    """The raw record of every question the chord flash card drill has
    asked, newest first - filterable by drill, direction, whether it was
    answered correctly, which chord was asked (root and/or quality), or
    which session it belongs to. The queryable half of issue #32's promise
    for this drill, the same shape GET /trainer/attempts offers fret to
    note.
    """
    if not 1 <= limit <= practice.MAX_SESSION_LIMIT:
        raise HTTPException(422, f"limit must be between 1 and {practice.MAX_SESSION_LIMIT}")
    if drill and drill not in trainer.CHORD_DRILLS:
        raise HTTPException(422, f"drill must be one of {list(trainer.CHORD_DRILLS)}")
    if direction and direction not in trainer.CHORD_DIRECTIONS:
        raise HTTPException(422, f"direction must be one of {list(trainer.CHORD_DIRECTIONS)}")
    if root and root not in trainer.PITCH_CLASSES:
        raise HTTPException(422, f"root must be one of {list(trainer.PITCH_CLASSES)}")
    if quality and quality not in trainer.CHORD_QUALITIES:
        raise HTTPException(422, f"quality must be one of {list(trainer.CHORD_QUALITIES)}")
    where = ["owner = ?"]
    params: list = [DEFAULT_OWNER]
    if drill:
        where.append("drill = ?")
        params.append(drill)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    if correct is not None:
        where.append("correct = ?")
        params.append(1 if correct else 0)
    if root:
        where.append("target_root = ?")
        params.append(root)
    if quality:
        where.append("target_quality = ?")
        params.append(quality)
    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)
    filters = " AND ".join(where)
    conn = connect()
    rows = conn.execute(
        f"""SELECT * FROM trainer_chord_attempts WHERE {filters}
             ORDER BY created_at DESC, id DESC LIMIT ?""",
        [*params, limit],
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM trainer_chord_attempts WHERE {filters}", params
    ).fetchone()["n"]
    return {
        "attempts": [trainer.chord_attempt_dict(r) for r in rows],
        "total": total,
        "truncated": total > len(rows),
    }


# ---------------------------------------------------------------------------
# Trainer: named drill scopes (issue #236)
#
# A scope - which strings, which fret range, which key - saved under a name.
# Until this surface existed a scope was browser state that reset on every
# page load, and the only trace it left behind was an English sentence in
# practice_sessions.note, which docs/practice-data.md's rule for this data
# layer forbids: "what was practised" has to be a row a reader can query, not
# prose it has to parse.
#
# THREE ROUTES AND NO UPDATE, deliberately. A preset is a small, whole thing:
# saving the current scope under a new name is what "change it" means in both
# drills, and an edit route would need a shape (replace the string set? merge
# it?) that nothing is asking for. Delete and save again is the operation.
#
# ONE LIST FOR BOTH DRILLS, which is the point of the bet - see db.py's note
# on why trainer_scope_presets has no `drill` column.
# ---------------------------------------------------------------------------


class TrainerPresetIn(BaseModel):
    """A scope to save under a name.

    `strings` is required and must not be empty - "every string" is spelled
    by naming every string, see trainer._preset_strings for why a saved
    preset cannot use the browser's "empty means no filter" convention.
    `key_root`/`key_quality` are given together or not at all; omitting both
    is a scope over every note, which is the ordinary case.
    """

    name: str = Field(max_length=trainer.MAX_PRESET_NAME_CHARS)
    start_fret: StrictInt
    end_fret: StrictInt
    strings: list[StrictInt]
    key_root: str | None = None
    key_quality: str | None = None


def _trainer_preset_row(conn, preset_id: int):
    row = conn.execute(
        "SELECT * FROM trainer_scope_presets WHERE id = ? AND owner = ?",
        (preset_id, DEFAULT_OWNER),
    ).fetchone()
    if not row:
        raise HTTPException(404, "preset not found")
    return row


def _trainer_preset_strings(conn, preset_id: int) -> list[int]:
    return [
        r["string_number"]
        for r in conn.execute(
            "SELECT string_number FROM trainer_scope_preset_strings "
            "WHERE preset_id = ? ORDER BY string_number",
            (preset_id,),
        )
    ]


def _trainer_preset_dict(conn, row) -> dict:
    return trainer.preset_dict(row, _trainer_preset_strings(conn, row["id"]))


@router.get("/trainer/presets", tags=[TAG_TRAINER], response_model=list[TrainerPresetOut])
def list_trainer_presets():
    """Every named drill scope, newest first, each with the strings it
    allows.

    The strings come along here rather than on a detail endpoint: a scope is
    small, and a picker that has to make a second request per entry before it
    can say what any of them mean would be a list nobody can read.
    """
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM trainer_scope_presets WHERE owner = ? ORDER BY created_at DESC, id DESC",
        (DEFAULT_OWNER,),
    ).fetchall()
    return [_trainer_preset_dict(conn, r) for r in rows]


@router.post("/trainer/presets", tags=[TAG_TRAINER], response_model=TrainerPresetOut)
def create_trainer_preset(body: TrainerPresetIn):
    """Save the current drill scope under a name, so it can be picked up
    again tomorrow and in the other drill.

    A name already in use is refused with 409 rather than accepted as a
    second entry - unlike a setlist, whose duplicate names are allowed on
    purpose (see create_setlist). The difference is what the list is FOR: a
    setlist is a thing you open, while a preset is picked in order to change
    what the next question will be, and two identically named entries make
    "which scope am I about to practise" unanswerable from the screen. The
    comparison ignores case for the same reason: "Jazz box" and "jazz box"
    are one entry to a reader, so they are one entry here.
    """
    try:
        values = trainer.normalise_preset(**body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    preset = values["preset"]
    with write_tx() as conn:
        clash = conn.execute(
            "SELECT id FROM trainer_scope_presets WHERE owner = ? AND name = ? COLLATE NOCASE",
            (DEFAULT_OWNER, preset["name"]),
        ).fetchone()
        if clash:
            raise HTTPException(409, f"there is already a preset called {preset['name']}")
        columns = ", ".join(preset)
        placeholders = ", ".join("?" * len(preset))
        row = conn.execute(
            f"""INSERT INTO trainer_scope_presets(owner, {columns})
                VALUES (?, {placeholders}) RETURNING *""",
            [DEFAULT_OWNER, *preset.values()],
        ).fetchone()
        conn.executemany(
            "INSERT INTO trainer_scope_preset_strings(preset_id, string_number) VALUES (?, ?)",
            [(row["id"], n) for n in values["strings"]],
        )
        return _trainer_preset_dict(conn, row)


@router.delete(
    "/trainer/presets/{preset_id}", tags=[TAG_TRAINER], response_model=TrainerPresetDeleteOut
)
def delete_trainer_preset(preset_id: RowId):
    """Delete a named scope. The practice logged under it is NOT touched -
    `sessions_kept` counts the sessions that keep their day, their length and
    their activity and lose only the reference (db.py's ON DELETE SET NULL
    note says why). Its string set goes with it, by cascade, because a row
    saying "(this preset) includes (string 3)" states nothing once the preset
    is gone."""
    with write_tx() as conn:
        _trainer_preset_row(conn, preset_id)
        kept = conn.execute(
            "SELECT COUNT(*) AS n FROM practice_sessions WHERE preset_id = ? AND owner = ?",
            (preset_id, DEFAULT_OWNER),
        ).fetchone()["n"]
        conn.execute(
            "DELETE FROM trainer_scope_presets WHERE id = ? AND owner = ?",
            (preset_id, DEFAULT_OWNER),
        )
    return {"deleted": preset_id, "sessions_kept": kept}


# ---------------------------------------------------------------------------
# SETLISTS (#6).
#
# A setlist is an ordered collection of scores a player works through - a gig
# set, a lesson plan, a practice rotation. This surface is the whole of what a
# setlist IS: a documented set of endpoints over the setlists and
# setlist_scores tables, not client-side state a browser assembles and could
# lose. Order is explicit and stored (setlist_scores.position), so reorder is a
# real operation the server performs rather than an artefact of the order rows
# happen to come back in.
#
# WHAT THESE ENDPOINTS DELIBERATELY DO NOT DO, each stated once here and
# enforced by the routes below:
#
#   Removing a score from a setlist does not delete the score. It removes one
#   membership row and nothing else - the score, its file, its practice
#   history, its tags and its transcription are untouched, and it stays in
#   every other setlist it is in.
#
#   Deleting a setlist does not touch its scores. It removes the setlist row
#   and, by cascade, its membership rows - reaching nothing else (see db.py's
#   setlist_scores note). DELETE reports how many scores were untouched.
#
#   A score can be in any number of setlists. Nothing here constrains that; the
#   only uniqueness is that a score appears in one setlist at most once.
#
#   A trashed score (#56) stays in the setlists it was in, marked. The member
#   still lists, carrying its `score.deleted_at`, so a client marks it deleted
#   rather than offering a link into something that is not in the library. A
#   trashed score cannot be ADDED to a setlist, the same way every other write
#   against a trashed score is refused - see _live_score_row. A score PURGED
#   from the trash leaves its setlists on its own, because the membership row
#   cascades away with the score row.
#
# The practice-through / gig-mode flow is a client concern (it reuses the
# existing viewer and practice logging - there is no separate "gig session"
# row): this surface's job is to hand that client the ordered members, each
# with the practice totals the score already carries (#32's one-source-of-truth
# rule - see SetlistMemberOut), so nothing downstream re-counts what the API
# already knows.
# ---------------------------------------------------------------------------

# The raw ceiling on a submitted name, before _clean_setlist_name strips
# control characters and collapses whitespace; MAX_SETLIST_NAME_CHARS is then
# applied to what will actually be stored. Two numbers for the same reason
# instruments has MAX_RAW_NAME_CHARS and MAX_NAME_CHARS: reject something
# absurdly large outright at the edge, then bound what is kept after cleaning.
MAX_SETLIST_NAME_RAW_CHARS = 2000
MAX_SETLIST_NAME_CHARS = 200


class SetlistIn(BaseModel):
    """A new setlist. Only its name is set here; scores are added afterwards
    through POST /api/setlists/{id}/scores, and order through the reorder
    endpoint - a setlist is created empty and arranged, rather than posted
    whole, so the ordering operations are the one way membership and order
    ever change and there is no second path that could disagree with them."""

    name: str = Field(max_length=MAX_SETLIST_NAME_RAW_CHARS)


class SetlistPatch(BaseModel):
    """Rename a setlist. Name is the only thing about a setlist that is edited
    in place - its membership and order have their own endpoints - so this
    carries nothing else."""

    name: str = Field(max_length=MAX_SETLIST_NAME_RAW_CHARS)


class SetlistAddIn(BaseModel):
    """Add one score to a setlist, appended after the last member. `score_id`
    is StrictInt (via Count) for the same reason every numeric field a client
    sends here is: Pydantic's default mode would coerce `true` to 1, and a
    bool is never a row id."""

    score_id: Count


class SetlistOrderIn(BaseModel):
    """Set a setlist's order outright. `score_ids` must be exactly the setlist's
    current members, each once, in the desired order - not a subset and not a
    superset. Reorder is a permutation, deliberately: expressing it as "here is
    the whole order now" rather than "move this one to index n" means the
    result cannot depend on what the client believed the old order was, and a
    member silently missing from the list is a mistake worth a 422 rather than
    a member quietly dropped from the setlist. Add and remove are the endpoints
    that change WHICH scores are in the setlist; this one only changes their
    order."""

    score_ids: list[Count]


def _clean_setlist_name(name: str) -> str:
    """Strip control characters, collapse runs of whitespace to single spaces,
    trim the ends, and bound the length - the same shape of cleaning
    instruments.normalise does to an instrument name, kept here rather than
    shared because it is three lines and a setlist name has no other rules. An
    empty result (a name that was only whitespace, or only control characters)
    is a 422 rather than a stored blank."""
    cleaned = re.sub(r"\s+", " ", "".join(ch for ch in name if ch.isprintable())).strip()
    if not cleaned:
        raise HTTPException(422, "a setlist needs a name")
    return cleaned[:MAX_SETLIST_NAME_CHARS]


def _setlist_row(conn, setlist_id: int):
    row = conn.execute(
        "SELECT * FROM setlists WHERE id = ? AND owner = ?", (setlist_id, DEFAULT_OWNER)
    ).fetchone()
    if not row:
        raise HTTPException(404, "setlist not found")
    return row


def _setlist_member_ids(conn, setlist_id: int) -> list[int]:
    """The setlist's member score ids in stored order. Used both to build the
    ordered members and to validate a reorder against the exact current set."""
    return [
        r["score_id"]
        for r in conn.execute(
            "SELECT score_id FROM setlist_scores WHERE setlist_id = ? ORDER BY position, score_id",
            (setlist_id,),
        )
    ]


def _setlist_members(conn, setlist_id: int) -> list[dict]:
    """The ordered members, each a `position` and a whole ScoreOut-shaped
    score - trashed members included, marked by their own `deleted_at`.

    The scores go through _with_tags exactly as the library and trash views do,
    so a member carries the same tags, transcription flag and practice totals
    every other view of that score does (#32). `position` is kept OUT of the
    score dict on purpose: it is the member's key, not the score's, and leaving
    it on the score would be a field response_model drops - which the api-docs
    drift guard would (correctly) flag."""
    member_rows = conn.execute(
        "SELECT score_id, position FROM setlist_scores WHERE setlist_id = ? "
        "ORDER BY position, score_id",
        (setlist_id,),
    ).fetchall()
    if not member_rows:
        return []
    ids = [r["score_id"] for r in member_rows]
    placeholders = ",".join("?" * len(ids))
    by_id = {
        r["id"]: r
        for r in conn.execute(f"SELECT * FROM scores WHERE id IN ({placeholders})", ids)
    }
    # Ordered to match member_rows so _with_tags (which returns a list aligned
    # to its input) hands back the score dicts in setlist order.
    ordered = [by_id[i] for i in ids]
    scored = _with_tags(conn, ordered)
    return [
        {"position": m["position"], "score": s}
        for m, s in zip(member_rows, scored)
    ]


def _setlist_dict(conn, setlist_id: int) -> dict:
    row = _setlist_row(conn, setlist_id)
    d = dict(row)
    d["scores"] = _setlist_members(conn, setlist_id)
    return d


def _setlist_summary(conn, row) -> dict:
    d = dict(row)
    d["score_count"] = conn.execute(
        "SELECT COUNT(*) AS n FROM setlist_scores WHERE setlist_id = ?", (row["id"],)
    ).fetchone()["n"]
    return d


def _touch_setlist(conn, setlist_id: int) -> None:
    conn.execute(
        "UPDATE setlists SET updated_at = datetime('now') WHERE id = ?", (setlist_id,)
    )


@router.get("/setlists", tags=[TAG_SETLISTS], response_model=list[SetlistOut])
def list_setlists():
    """Every setlist, most recently created first, each with the number of
    scores it holds. The ordered scores themselves are on the detail endpoint -
    a list of setlists does not need every member of every one."""
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM setlists WHERE owner = ? ORDER BY created_at DESC, id DESC",
        (DEFAULT_OWNER,),
    ).fetchall()
    return [_setlist_summary(conn, r) for r in rows]


@router.post("/setlists", tags=[TAG_SETLISTS], response_model=SetlistOut)
def create_setlist(body: SetlistIn):
    """Make a new, empty setlist with the given name. Two setlists may share a
    name - a name is a label a person chose, not an identity - so this never
    refuses one for being a duplicate."""
    name = _clean_setlist_name(body.name)
    with write_tx() as conn:
        cur = conn.execute(
            "INSERT INTO setlists(owner, name) VALUES (?, ?)", (DEFAULT_OWNER, name)
        )
        setlist_id = cur.lastrowid
        summary = _setlist_summary(conn, _setlist_row(conn, setlist_id))
    return summary


@router.get("/setlists/{setlist_id}", tags=[TAG_SETLISTS], response_model=SetlistDetailOut)
def get_setlist(setlist_id: RowId):
    """One setlist and its scores, in order. A member whose score is in the
    trash is still here, marked by its `score.deleted_at` - see this section's
    comment and SetlistMemberOut."""
    conn = connect()
    return _setlist_dict(conn, setlist_id)


@router.patch("/setlists/{setlist_id}", tags=[TAG_SETLISTS], response_model=SetlistDetailOut)
def rename_setlist(setlist_id: RowId, patch: SetlistPatch):
    """Rename a setlist. Returns the setlist with its scores, the same shape
    GET does, so a client that renamed from a detail view has the current
    state without a second request."""
    name = _clean_setlist_name(patch.name)
    with write_tx() as conn:
        _setlist_row(conn, setlist_id)
        conn.execute(
            "UPDATE setlists SET name = ?, updated_at = datetime('now') WHERE id = ? AND owner = ?",
            (name, setlist_id, DEFAULT_OWNER),
        )
        detail = _setlist_dict(conn, setlist_id)
    return detail


@router.delete("/setlists/{setlist_id}", tags=[TAG_SETLISTS], response_model=SetlistDeleteOut)
def delete_setlist(setlist_id: RowId):
    """Delete a setlist. The scores in it are NOT touched - `scores_untouched`
    counts them so a caller can say so - only the setlist and its membership
    rows go (see db.py's setlist_scores note on the cascade). A score in this
    setlist stays in the library, keeps its practice history, and stays in any
    other setlist it was in."""
    with write_tx() as conn:
        _setlist_row(conn, setlist_id)
        untouched = conn.execute(
            "SELECT COUNT(*) AS n FROM setlist_scores WHERE setlist_id = ?", (setlist_id,)
        ).fetchone()["n"]
        # The membership rows go by cascade when the setlist row does; deleting
        # the setlist is the whole operation.
        conn.execute(
            "DELETE FROM setlists WHERE id = ? AND owner = ?", (setlist_id, DEFAULT_OWNER)
        )
    return {"deleted": setlist_id, "scores_untouched": untouched}


@router.post(
    "/setlists/{setlist_id}/scores", tags=[TAG_SETLISTS], response_model=SetlistDetailOut
)
def add_setlist_score(setlist_id: RowId, body: SetlistAddIn):
    """Add a score to a setlist, at the end of the order.

    Refused with 409 if the score is already in this setlist (a setlist holds a
    score at most once), or if the score is in the trash - a trashed score
    cannot be added, the same way every write against a trashed score is
    refused (see _live_score_row). A missing score - one whose file is gone but
    which is still in the library - CAN be added: it is still your piece.
    Returns the setlist with its new member in place."""
    with write_tx() as conn:
        _setlist_row(conn, setlist_id)
        # 404 if the score does not exist at all, 409 if it is in the trash -
        # the shared decision for a write naming a score (see _live_score_row).
        _live_score_row(conn, body.score_id, "add a score that is in the trash to a setlist")
        already = conn.execute(
            "SELECT 1 FROM setlist_scores WHERE setlist_id = ? AND score_id = ?",
            (setlist_id, body.score_id),
        ).fetchone()
        if already:
            raise HTTPException(409, "that score is already in this setlist")
        next_position = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM setlist_scores WHERE setlist_id = ?",
            (setlist_id,),
        ).fetchone()["p"]
        conn.execute(
            "INSERT INTO setlist_scores(setlist_id, score_id, position) VALUES (?, ?, ?)",
            (setlist_id, body.score_id, next_position),
        )
        _touch_setlist(conn, setlist_id)
        detail = _setlist_dict(conn, setlist_id)
    return detail


@router.delete(
    "/setlists/{setlist_id}/scores/{score_id}",
    tags=[TAG_SETLISTS],
    response_model=SetlistDetailOut,
)
def remove_setlist_score(setlist_id: RowId, score_id: RowId):
    """Remove a score from a setlist. THIS DOES NOT DELETE THE SCORE - it
    removes one membership row. The score stays in the library with everything
    attached to it, and in any other setlist it is in. 404 if the score is not
    a member of this setlist (the setlist itself is 404 separately, so the two
    cases are distinguishable). The remaining members keep their order; their
    positions are left as they were, which does not affect the order they are
    read in. Returns the setlist as it now stands."""
    with write_tx() as conn:
        _setlist_row(conn, setlist_id)
        cur = conn.execute(
            "DELETE FROM setlist_scores WHERE setlist_id = ? AND score_id = ?",
            (setlist_id, score_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "that score is not in this setlist")
        _touch_setlist(conn, setlist_id)
        detail = _setlist_dict(conn, setlist_id)
    return detail


@router.put("/setlists/{setlist_id}/order", tags=[TAG_SETLISTS], response_model=SetlistDetailOut)
def reorder_setlist(setlist_id: RowId, body: SetlistOrderIn):
    """Set the order of a setlist's scores. `score_ids` must be exactly the
    setlist's current members, each listed once, in the order wanted - see
    SetlistOrderIn for why it is the whole permutation and not a move. A list
    that is missing a member, names one twice, names one that is not in the
    setlist, or is the wrong length is a 422 that says which, and nothing is
    written. Trashed members are still members, so they are part of the
    permutation and keep a place in the order. Returns the reordered setlist."""
    with write_tx() as conn:
        _setlist_row(conn, setlist_id)
        current = _setlist_member_ids(conn, setlist_id)
        wanted = list(body.score_ids)
        if len(set(wanted)) != len(wanted):
            raise HTTPException(422, "the order lists a score more than once")
        if set(wanted) != set(current):
            raise HTTPException(
                422,
                "the order must be exactly the scores currently in this setlist - it is "
                f"missing or adds scores (setlist has {len(current)} scores, order lists "
                f"{len(wanted)})",
            )
        # Positions rewritten to the given order, 1-based. No unique constraint
        # on position (see db.py), so writing them one at a time cannot collide.
        for index, score_id in enumerate(wanted, start=1):
            conn.execute(
                "UPDATE setlist_scores SET position = ? WHERE setlist_id = ? AND score_id = ?",
                (index, setlist_id, score_id),
            )
        _touch_setlist(conn, setlist_id)
        detail = _setlist_dict(conn, setlist_id)
    return detail
