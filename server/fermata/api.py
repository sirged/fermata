import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from .config import FILE_TYPES, LIBRARY_DIR
from .db import DEFAULT_OWNER, connect, tx, write_tx
from .glyph_rhythm import VALID_TS_DENOMINATORS
from .tabextract import analyze as analyze_pdf, extract as extract_pdf
from .thumbs import thumb_path

router = APIRouter(prefix="/api")

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


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/settings")
def get_settings():
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


@router.put("/settings")
def put_settings(patch: dict[str, str] = Body(...)):
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


@router.get("/instruments")
def list_instruments():
    conn = connect()
    rows = conn.execute(
        "SELECT * FROM instruments WHERE owner = ? ORDER BY name COLLATE NOCASE, id",
        (DEFAULT_OWNER,),
    ).fetchall()
    return [_instrument_dict(r) for r in rows]


# Declared before /instruments/{instrument_id}: FastAPI matches in declaration
# order, and the other way round "presets" would be tried as an int path
# parameter and answered with a 422 about parsing rather than with the presets.
@router.get("/instruments/presets")
def list_instrument_presets():
    return instruments.presets()


@router.post("/instruments")
def create_instrument(body: InstrumentIn):
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


@router.get("/instruments/{instrument_id}")
def get_instrument(instrument_id: RowId):
    conn = connect()
    return _instrument_dict(_instrument_row(conn, instrument_id))


@router.put("/instruments/{instrument_id}")
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


@router.delete("/instruments/{instrument_id}")
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


@router.get("/duplicates")
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
           WHERE missing_since IS NULL
           GROUP BY hash HAVING COUNT(*) > 1
           ORDER BY count DESC, hash"""
    ).fetchall()
    groups = []
    for d in dupes:
        rows = conn.execute(
            "SELECT * FROM scores WHERE hash = ? AND missing_since IS NULL ORDER BY path",
            (d["hash"],),
        ).fetchall()
        groups.append({"hash": d["hash"], "count": d["count"], "scores": _with_tags(conn, rows)})
    return groups


@router.get("/collections")
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
            WHERE collection IS NOT NULL
         GROUP BY collection ORDER BY collection"""
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
def get_score(score_id: RowId):
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


@router.patch("/scores/{score_id}")
def patch_score(score_id: RowId, patch: ScorePatch):
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


@router.post("/scores/{score_id}/practice")
def log_practice(score_id: RowId, body: PracticeIn):
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


@router.get("/scores/{score_id}/practice")
def get_practice(score_id: RowId):
    conn = connect()
    _score_row(conn, score_id)
    return {
        "sessions": [practice.session_dict(r) for r in _recent_sessions(conn, score_id)],
        **_practice_totals(conn, score_id),
    }


@router.post("/practice/sessions")
def log_session(body: SessionIn):
    """Log practice that is not necessarily against a piece.

    The general form of the per-score endpoint above, and the one an exercise
    or a stretch of unstructured playing uses: `score_id` may be omitted for
    every activity except 'piece', which is defined by having one.
    """
    with write_tx() as conn:
        values = _normalise_session(conn, body.model_dump())
        return practice.session_dict(_insert_session(conn, values))


@router.patch("/practice/sessions/{session_id}")
def patch_session(session_id: RowId, patch: SessionPatch):
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


@router.delete("/practice/sessions/{session_id}")
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


@router.get("/practice/sessions")
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


@router.get("/practice/summary")
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


@router.get("/practice/history")
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


@router.post("/practice/goals")
def set_goal(body: GoalIn, today: str | None = None):
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
@router.get("/practice/goals/current")
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


@router.get("/practice/goals")
def list_goals(limit: int = practice.MAX_REVIEW_WEEKS, today: str | None = None):
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


@router.patch("/practice/goals/{goal_id}")
def patch_goal(goal_id: RowId, patch: GoalPatch, today: str | None = None):
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


@router.delete("/practice/goals/{goal_id}")
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


@router.get("/practice/review")
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


@router.get("/scores/{score_id}/file")
def get_file(score_id: RowId):
    conn = connect()
    row = _score_row(conn, score_id)
    path = LIBRARY_DIR / row["path"]
    if not path.is_file():
        raise HTTPException(404, "file missing from library")
    media = "application/pdf" if row["file_type"] == "pdf" else "application/octet-stream"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/scores/{score_id}/thumb")
def get_thumb(score_id: RowId):
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
_BAR_KEYS = ("bars_overfull", "bars_short", "bars_defective", "bars_measured",
             "bars_padded", "bars_unread")

# WHICH bars those were, as data and not only inside the warning prose. The
# prose names them, but it caps the list, and the profile document states that a
# consumer summing the `<forward>` durations in the file should get
# `inferred_rest_quarters` - a claim the application has to actually make good
# on. `padded_bars` / `unread_bars` are lists of bar numbers;
# `inferred_rest_quarters` is a quarter-note count and can be fractional.
_BAR_LIST_KEYS = ("padded_bars", "unread_bars")
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
    return d


@router.get("/scores/{score_id}/transcription")
def get_transcription(score_id: RowId):
    conn = connect()
    _score_row(conn, score_id)
    row = _transcription_row(conn, score_id)
    if not row:
        raise HTTPException(404, "no transcription for this score")
    return _transcription_dict(row)


@router.get("/scores/{score_id}/transcription/analysis")
def get_transcription_analysis(score_id: RowId):
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


@router.post("/scores/{score_id}/transcribe")
def transcribe(score_id: RowId, body: TranscribeIn | None = Body(default=None)):
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
    # `warnings` and the Rule 8 conformance figures are NOT set from `result`
    # here. They come back out of the row that was just written, so that this
    # response and a later GET of the same row are answered from one source
    # rather than two that could drift. Everything below is extraction detail
    # that is genuinely only available on this response.
    d = _transcription_dict(saved)
    d["bars"] = result.bars
    d["beats"] = result.beats
    d["notes"] = result.notes
    d["tempo"] = result.tempo
    d["tuning"] = result.tuning
    d["tuning_label"] = result.tuning_label
    d["time_signature"] = list(result.time_signature) if result.time_signature else None
    d["time_signature_source"] = result.time_signature_source
    d["key_fifths"] = result.key_fifths
    d["key_signature_source"] = result.key_signature_source
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


@router.put("/scores/{score_id}/transcription")
def save_transcription(score_id: RowId, body: TranscriptionEditIn):
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


@router.delete("/scores/{score_id}/transcription")
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


@router.post("/scan")
def trigger_scan():
    started = scanner.start_scan()
    return {"started": started, **scanner.scan_status()}


@router.get("/scan/status")
def get_scan_status():
    return scanner.scan_status()


class ScanAcknowledgement(BaseModel):
    # The token from the refusal being acknowledged. Consent has to be about
    # something specific, and this is what says which something - see
    # scanner._acknowledge_token.
    token: str = Field(min_length=1, max_length=128)


@router.post("/scan/acknowledge")
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


@router.post("/upload")
async def upload(file: UploadFile, folder: str = "Uploads"):
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
