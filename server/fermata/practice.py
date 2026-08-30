"""Practice: what was actually done, and what someone meant to do.

Two records, kept apart on purpose.

A SESSION is a fact. Somebody sat down at a time, worked for a length, and
this is what the work was: which piece if any, what kind of practice, which
bars or pages, at what tempo, and how it felt to them. A session is never
edited by anything except the person who logged it.

A GOAL is an intention, with a period attached. It says how many days they
mean to practise and for how long in total, and optionally what on. It carries
no verdict: nothing anywhere stores whether a goal was met. That is counted
from the sessions inside its period, every time it is asked for, which is what
keeps a goal and the history it is about from ever disagreeing - and which is
the only version of this that stays true when a session is logged late.

HOW THIS TALKS ABOUT A PERIOD THAT DID NOT GO AS PLANNED. The numbers are
reported and nothing is concluded from them. `days_practised` beside
`target_days` is three and four; there is no "missed", no percentage, no
grade, and no comparison against any other period - a best week is the
mechanism by which a good month becomes the standard a bad month is measured
against, so no query here ranks one period against another and no field
invites it. `met` exists because "did this happen" is a fact somebody may
want, and it is a fact about the goal, not about the person. Everything after
that is theirs to say, in `reflection`, in answer to a question rather than to
a verdict: was this goal realistic?

WHY A PRACTICE DAY IS ITS OWN STORED VALUE. Every day-based number here -
days practised, the per-day breakdown, which week a session falls in - counts
`local_date`, the calendar day in the practiser's own time, not the UTC day
inside `started_at`. West of Greenwich those are different days for every
evening practice, and "how many days did I practise" would be wrong by one for
exactly the sessions someone is most likely to have. Rows written before the
column existed have no local date and are attributed to their UTC day, which
is the only day they ever recorded; a reader is told which of the two it got
(see `local_date_source`) rather than being handed a guess dressed as a fact.

A back-dated session is a first-class thing: `started_at` is when the session
was RECORDED and `local_date` is the day the practice HAPPENED, and somebody
entering yesterday's forgotten hour is not lying about either.
"""

from datetime import date, timedelta

from .db import DEFAULT_OWNER

# What kind of work a session was. One column on one table rather than a table
# per kind: the exercises this has to carry next - fret-to-note, ear training,
# chord drills - are practice in exactly the way a piece is, and a parallel
# table per kind would mean the history view and every goal calculation
# growing one more special case with each of them.
#
# 'piece' is work on something in the library and is the only value that
# requires a score. 'free' is unstructured playing, which is real practice and
# had nowhere to go before. Per-attempt detail from a trainer (which positions
# were missed, response times) is NOT here: that belongs beside the trainer
# that produces it, and inventing its shape before one exists would be
# guessing. What this vocabulary guarantees is that when a trainer arrives, the
# time it accounts for lands in the same history and the same goals as
# everything else.
ACTIVITIES = (
    "piece",
    "technique",
    "sight_reading",
    "ear_training",
    "fretboard",
    "chords",
    "improvisation",
    "theory",
    "free",
    "other",
)
DEFAULT_ACTIVITY = "piece"

# How the work was approached, which is a different question from what it was.
# Twenty minutes of one awkward bar and twenty minutes of playing the piece
# end to end are not the same practice, and only the person doing it knows
# which it was - so this is recorded, never inferred from whether a bar range
# happens to be present.
MODES = ("section", "run_through")

GOAL_SCOPES = ("all", "score", "activity")

# 'week' and nothing else today. A goal for the week broken into days is what
# was asked for; a longer period arrives here as another value rather than as
# another table.
GOAL_PERIODS = ("week",)
PERIOD_DAYS = {"week": 7}

# The answer to "was this goal realistic?", which is the only question asked
# about a period that did not go to plan. Not a status, and not something
# anything but the person writes.
REALISTIC_ANSWERS = ("yes", "no")

WEEK_STARTS = ("monday", "sunday")
DEFAULT_WEEK_START = "monday"

MAX_SESSION_SECONDS = 86_400
MIN_RATING = 1
MAX_RATING = 5
# Wide enough for anything a person plays and narrow enough to catch a units
# mistake - a tempo of 3 or 3000 is a bug in whatever sent it.
MIN_TEMPO_BPM = 20
MAX_TEMPO_BPM = 400
MAX_BAR = 100_000
MAX_PAGE = 10_000
MAX_NOTE_CHARS = 2_000
MAX_INTENT_CHARS = 200
MAX_REFLECTION_CHARS = 2_000

MIN_TARGET_MINUTES = 1
MAX_TARGET_MINUTES = 7 * 24 * 60

# How far a recorded practice day may sit from the day it is being recorded on.
# Back-dating is legitimate - forgetting to log an hour is the ordinary case -
# so the past is generous. The future is not: a date ahead of tomorrow is a
# broken clock or a broken client, and practice that has not happened yet must
# not be able to fill in a goal.
MAX_BACKDATE_DAYS = 400
MAX_FUTURE_DAYS = 1

# The window a client's idea of "today" may fall in, relative to the server's
# date: exactly the window a practice day may name. A date outside it cannot be
# about any period this instance could hold practice for, so answering it means
# returning a confident, plausible, empty week - the worst kind of wrong answer,
# being one nothing in the response marks as suspect.
#
# Deliberately as wide as the back-dating window rather than as narrow as a
# timezone. A browser is never more than a day from UTC, but reasoning about a
# period that has already ended is a legitimate use of this parameter and the
# thing that makes every period rule here testable without waiting for the
# calendar.

# How far back a review looks by default. Long enough to show a pattern
# including a couple of unusual weeks; short enough that opening it is not a
# wall of past periods to scroll through. Nothing accumulates beyond what is
# asked for.
DEFAULT_REVIEW_WEEKS = 8
MAX_REVIEW_WEEKS = 52

DEFAULT_HISTORY_DAYS = 90
MAX_HISTORY_DAYS = 366

DEFAULT_SESSION_LIMIT = 100
MAX_SESSION_LIMIT = 1_000

# How many of one piece's sessions come back with its progress, and how many
# goals about it. Both are windowed already, so these bound a window somebody
# practised unusually hard in rather than a whole history - and both report
# what they truncated, the way every other list here does.
DEFAULT_SCORE_SESSION_LIMIT = 200
MAX_SCORE_SESSION_LIMIT = 1_000
MAX_SCORE_GOALS = 52

# The column every aggregate in this module groups and filters by, named on
# each response that carries one. A day is `local_date` where the practiser's
# own clock recorded it and `date(started_at)` where it did not, and a reader
# totalling by day needs to know which question was asked of the rows - see
# LOCAL_DATE_SQL and the note above it.
GROUPED_BY = "local_date"

# The day a session belongs to, in SQL, for every query that groups or filters
# by day. Written once because it is not a formula anyone should retype: the
# COALESCE is what attributes a row from before local_date existed, and a query
# that forgot it would silently drop that row out of every day count rather
# than fail.
LOCAL_DATE_SQL = "COALESCE(p.local_date, date(p.started_at))"


# The range a date is allowed to name. Not a judgement about anybody's
# practice: it is what keeps the arithmetic downstream inside date's own bounds.
# Every caller adds or subtracts up to a year - a review reaches 52 weeks back,
# a history window 366 days - and date.min/date.max are hard walls that raise
# OverflowError from inside a subtraction, which reaches a client as a 500 for
# what is only ever a typo. Both ends leave a century of headroom.
MIN_DAY = date(1900, 1, 1)
MAX_DAY = date(2200, 1, 1)


def parse_day(value, field: str = "date") -> date:
    """A YYYY-MM-DD string as a date, or a ValueError naming the field.

    Deliberately strict. date.fromisoformat on Python 3.11+ also accepts
    'YYYYMMDD' and full timestamps, and a timestamp arriving where a practice
    day belongs would be stored as text that no other query's BETWEEN would
    ever match.
    """
    text = str(value or "").strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ValueError(f"{field} must be a date written YYYY-MM-DD")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field} must be a date written YYYY-MM-DD") from None
    if not MIN_DAY <= day <= MAX_DAY:
        raise ValueError(
            f"{field} must be between {MIN_DAY.isoformat()} and {MAX_DAY.isoformat()}"
        )
    return day


def week_start(day: date, starts_on: str = DEFAULT_WEEK_START) -> date:
    """The first day of the week `day` falls in.

    Which day a week starts on is a preference, not a fact, and it is asked for
    here rather than assumed: a goal counted over the wrong seven days is
    counted against days its owner did not consider part of the week.
    """
    if starts_on not in WEEK_STARTS:
        raise ValueError(f"week start must be one of {sorted(WEEK_STARTS)}")
    # weekday() is 0 for Monday. A Sunday-start week begins one day earlier, so
    # Sunday itself (weekday 6) is offset 0 and Monday is offset 1.
    offset = day.weekday() if starts_on == "monday" else (day.weekday() + 1) % 7
    return day - timedelta(days=offset)


def period_bounds(start: date, period: str = "week") -> tuple[date, date]:
    """The inclusive first and last day of a period beginning on `start`."""
    if period not in GOAL_PERIODS:
        raise ValueError(f"period must be one of {sorted(GOAL_PERIODS)}")
    return start, start + timedelta(days=PERIOD_DAYS[period] - 1)


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def _optional_int(value, field: str, low: int, high: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a whole number")
    if not low <= value <= high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return value


def _optional_text(value, field: str, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        # An empty box is not a note. Stored as NULL so "nothing was written"
        # is one value rather than two that every reader has to test for.
        return None
    if len(text) > limit:
        raise ValueError(f"{field} must be at most {limit} characters")
    return text


def _range(low_value, high_value, low_field: str, high_field: str, ceiling: int):
    low = _optional_int(low_value, low_field, 1, ceiling)
    high = _optional_int(high_value, high_field, 1, ceiling)
    if high is not None and low is None:
        raise ValueError(f"{high_field} needs {low_field} as well")
    if low is not None and high is not None and high < low:
        raise ValueError(f"{high_field} cannot be before {low_field}")
    return low, high


def normalise_session(
    *,
    score_id,
    activity,
    mode,
    seconds,
    local_date,
    recorded_on,
    from_bar=None,
    to_bar=None,
    from_page=None,
    to_page=None,
    tempo_bpm=None,
    target_tempo_bpm=None,
    rating=None,
    note=None,
    allow_missing_score=False,
    check_day_window=True,
) -> dict:
    """Check a session and return the values to store.

    Raises ValueError with a message meant for a person to read. Every write
    goes through here, whether it arrived on the per-score path or the general
    one, because a session recorded through one route and one recorded through
    the other are the same row and have to obey the same rules.

    `recorded_on` is today's date as the SERVER sees it, and exists so the
    bound on how far a practice day may sit from it is testable without
    waiting for the calendar.

    `allow_missing_score` is for a row that is ALREADY stored as work on a
    piece the library no longer has - see is_orphaned. Such a row is a true
    record and must stay editable: refusing to let somebody add a note to it
    because the piece has since been deleted would turn a rule about creating
    an honest claim into a rule about keeping one.

    `check_day_window` is False when the practice day is not what is being
    written. How far back a NEW date may be is a rule about what somebody may
    claim now; applied to a date already stored it becomes a rule that makes a
    session permanently uneditable once it is old enough - so a note or a
    rating on last year's practice could not be corrected, for reasons that
    have nothing to do with either.
    """
    activity = activity or DEFAULT_ACTIVITY
    if activity not in ACTIVITIES:
        raise ValueError(f"activity must be one of {sorted(ACTIVITIES)}")

    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {sorted(MODES)}")

    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError("seconds must be a whole number")
    if not 0 < seconds <= MAX_SESSION_SECONDS:
        raise ValueError(f"seconds must be between 1 and {MAX_SESSION_SECONDS}")

    if activity == DEFAULT_ACTIVITY and score_id is None and not allow_missing_score:
        # Every other activity is practice that may well have no piece behind
        # it. This one is defined by having one, and a 'piece' session with no
        # piece would sit in the history saying nothing about what was worked.
        raise ValueError("a session on a piece needs a score_id")

    day = None
    if local_date is not None:
        day = parse_day(local_date, "local_date")
        if check_day_window:
            if day > recorded_on + timedelta(days=MAX_FUTURE_DAYS):
                raise ValueError("local_date is in the future")
            if day < recorded_on - timedelta(days=MAX_BACKDATE_DAYS):
                raise ValueError(f"local_date is more than {MAX_BACKDATE_DAYS} days ago")

    from_bar, to_bar = _range(from_bar, to_bar, "from_bar", "to_bar", MAX_BAR)
    from_page, to_page = _range(from_page, to_page, "from_page", "to_page", MAX_PAGE)

    return {
        "score_id": score_id,
        "activity": activity,
        "mode": mode,
        "seconds": seconds,
        "local_date": day.isoformat() if day else None,
        "from_bar": from_bar,
        "to_bar": to_bar,
        "from_page": from_page,
        "to_page": to_page,
        "tempo_bpm": _optional_int(tempo_bpm, "tempo_bpm", MIN_TEMPO_BPM, MAX_TEMPO_BPM),
        "target_tempo_bpm": _optional_int(
            target_tempo_bpm, "target_tempo_bpm", MIN_TEMPO_BPM, MAX_TEMPO_BPM
        ),
        "rating": _optional_int(rating, "rating", MIN_RATING, MAX_RATING),
        "note": _optional_text(note, "note", MAX_NOTE_CHARS),
    }


def normalise_goal(
    *,
    period,
    period_start,
    target_days,
    target_minutes,
    scope,
    score_id,
    activity,
    intent,
    allow_missing_score=False,
) -> dict:
    """Check a goal and return the values to store.

    A goal has to be concrete enough to be either met or missed, which is the
    whole point of setting one - so at least one target is required. Both are
    allowed together, and mean what they say: this many days, and this much
    time in total across them.

    `allow_missing_score` is for a goal ALREADY stored against a piece the
    library no longer has: the goal cannot be counted any more (see
    goal_progress) but the reflection on it can still be written, and refusing
    the edit would mean a deleted file silently locking a record of intent.
    """
    period = period or "week"
    if period not in GOAL_PERIODS:
        raise ValueError(f"period must be one of {sorted(GOAL_PERIODS)}")
    start, end = period_bounds(parse_day(period_start, "period_start"), period)

    length = PERIOD_DAYS[period]
    days = _optional_int(target_days, "target_days", 1, length)
    minutes = _optional_int(
        target_minutes, "target_minutes", MIN_TARGET_MINUTES, MAX_TARGET_MINUTES
    )
    if days is None and minutes is None:
        raise ValueError("a goal needs a target: days of practice, minutes, or both")

    scope = scope or "all"
    if scope not in GOAL_SCOPES:
        raise ValueError(f"scope must be one of {sorted(GOAL_SCOPES)}")
    # Each scope names exactly the thing it is scoped to and nothing else. A
    # goal carrying a score_id it does not use would read as a goal about that
    # piece while being counted over everything.
    if scope == "score":
        if score_id is None and not allow_missing_score:
            raise ValueError("a goal for one piece needs a score_id")
        activity = None
    elif scope == "activity":
        if activity not in ACTIVITIES:
            raise ValueError(f"activity must be one of {sorted(ACTIVITIES)}")
        score_id = None
    else:
        score_id = None
        activity = None

    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "target_days": days,
        "target_minutes": minutes,
        "scope": scope,
        "score_id": score_id,
        "activity": activity,
        "intent": _optional_text(intent, "intent", MAX_INTENT_CHARS),
    }


def normalise_reflection(*, reflection, realistic) -> dict:
    """The person's own account of a period, as stored.

    `realistic` answers a question and is not a status: 'no' says the goal
    was too big for that week, which is useful for setting the next one, and
    says nothing whatever about the week or about them.
    """
    if realistic is not None and realistic not in REALISTIC_ANSWERS:
        raise ValueError(f"realistic must be one of {sorted(REALISTIC_ANSWERS)}")
    return {
        "reflection": _optional_text(reflection, "reflection", MAX_REFLECTION_CHARS),
        "realistic": realistic,
    }


def overlapping_goal(conn, owner: str, start: str, end: str, ignore_start: str | None = None):
    """A stored goal whose period shares a day with [start, end], if any.

    Overlap and not equality, because equality is what the unique index on
    (owner, period_start) already covers and it is not the property that
    matters. Two goals sharing days is how the same practice ends up counted
    against two intentions and how two panels of one page come to disagree
    about which goal "this week" has - and it becomes reachable the moment the
    week-start preference changes, because the new grid's weeks are offset from
    the old grid's by a few days rather than being different weeks.

    `ignore_start` is the period being written, which is a replacement rather
    than an overlap.
    """
    sql = """SELECT * FROM practice_goals
              WHERE owner = ? AND period_start <= ? AND period_end >= ?"""
    params = [owner, end, start]
    if ignore_start is not None:
        sql += " AND period_start != ?"
        params.append(ignore_start)
    return conn.execute(sql + " ORDER BY period_start LIMIT 1", params).fetchone()


def review_periods(conn, owner: str, grid_start: date, weeks: int, starts_on: str) -> list[dict]:
    """The periods a review covers, most recent first.

    A GOAL CONTRIBUTES ITS OWN PERIOD, not a slot on today's grid. Matching
    goals to grid weeks by their start date was wrong in a way that mattered:
    change the week-start preference and every existing goal stops matching,
    so a past week carrying somebody's own intent and reflection rendered as
    "no goal was set for this week" - a false statement about their own record,
    made by the one feature whose whole premise is that its statements are
    true. The dates a goal was set for are stored, so history is sliced the way
    it was lived and a later preference cannot re-slice it.

    The timeline is then walked BACKWARDS from the end of the current week, a
    period at a time, and each step asks the same question: is there a goal
    covering this day? If so the period is that goal's own; if not it is the
    canonical week containing the day. Either way the next step resumes the day
    before whatever was emitted.

    Walking rather than intersecting two lists is what makes the result
    contiguous and non-overlapping whatever the two grids do. Listing every
    goal period AND every canonical week would report the same day twice after
    a preference change; dropping a canonical week because a goal overlapped it
    would leave the days on the far side of that goal in no period at all, and
    a review that quietly stops mentioning a day somebody practised on is the
    one failure this whole feature cannot afford.
    """
    window_end = grid_start + timedelta(days=PERIOD_DAYS["week"] - 1)
    earliest = grid_start - timedelta(days=7 * (weeks - 1))
    goals = conn.execute(
        """SELECT * FROM practice_goals
            WHERE owner = ? AND period_end >= ? AND period_start <= ?
         ORDER BY period_start DESC""",
        (owner, earliest.isoformat(), window_end.isoformat()),
    ).fetchall()

    def covering(day: date):
        stamp = day.isoformat()
        for goal in goals:
            if goal["period_start"] <= stamp <= goal["period_end"]:
                return goal
        return None

    periods = []
    cursor = window_end
    # Every step moves the cursor back by at least one day and normally by a
    # week, so this terminates; the bound is belt to that braces, and it is
    # generous enough that a shorter period type arriving later cannot silently
    # truncate the answer.
    while cursor >= earliest and len(periods) <= weeks * PERIOD_DAYS["week"]:
        goal = covering(cursor)
        if goal is not None:
            start, end = parse_day(goal["period_start"]), parse_day(goal["period_end"])
        else:
            start = week_start(cursor, starts_on)
            end = start + timedelta(days=PERIOD_DAYS["week"] - 1)
        periods.append(
            {
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "goal": goal,
            }
        )
        cursor = start - timedelta(days=1)
    return periods


def _scope_filter(scope: str, score_id, activity, params: list) -> str:
    """The extra WHERE this goal's scope adds, appending its parameters."""
    if scope == "score":
        params.append(score_id)
        return " AND p.score_id = ?"
    if scope == "activity":
        params.append(activity)
        return " AND p.activity = ?"
    return ""


def is_orphaned(activity, score_id) -> bool:
    """Whether this session is work on a piece that is no longer in the library.

    A 'piece' session cannot be CREATED without a score (see
    normalise_session), and every other activity may legitimately have none -
    so this one pair of values says, without a column of its own, that a score
    row went away from underneath a session. Deleting a score sets score_id to
    NULL rather than deleting the practice; see db._PRACTICE_SESSIONS_COLUMNS.
    """
    return activity == DEFAULT_ACTIVITY and score_id is None


def session_dict(row) -> dict:
    """One session as the API presents it.

    Three things are derived here rather than stored. `local_date` is the day
    the practice is attributed to, with `local_date_source` saying whether that
    day was recorded ('recorded') or taken from the UTC timestamp because the
    row predates the column ('utc_date') - a reader that treats an inferred day
    as a recorded one is the reason the distinction is on the response at all.
    `reached_target` is the tempo comparison, computed so it can never
    contradict the two numbers it comes from; None means one of them is
    missing, which is not the same as "did not reach it". And `score_missing`
    says the work was on a piece that has since left the library, so a reader
    can name it as that rather than showing a blank where a title goes.
    """
    d = dict(row)
    recorded = d.get("local_date")
    d["local_date"] = recorded or (d["started_at"] or "")[:10]
    d["local_date_source"] = "recorded" if recorded else "utc_date"
    tempo, target = d.get("tempo_bpm"), d.get("target_tempo_bpm")
    d["reached_target"] = None if tempo is None or target is None else tempo >= target
    d["score_missing"] = is_orphaned(d.get("activity"), d.get("score_id"))
    return d


def day_totals(conn, owner: str, start: str, end: str, extra: str = "", params=()) -> dict:
    """Seconds and session count per practice day, for the days that have any.

    `inferred` counts the sessions in that day's total whose day was NOT
    recorded - rows from before local_date existed, attributed to their UTC
    day. A single session says which it is; a total said nothing, so over a
    long window two different kinds of day were being added up with nothing
    marking the join. See period_facts's `sessions_inferred`.
    """
    sql = f"""SELECT {LOCAL_DATE_SQL} AS day,
                     SUM(p.seconds) AS seconds, COUNT(*) AS sessions,
                     SUM(CASE WHEN p.local_date IS NULL THEN 1 ELSE 0 END) AS inferred
                FROM practice_sessions p
               WHERE p.owner = ? AND {LOCAL_DATE_SQL} BETWEEN ? AND ?{extra}
            GROUP BY day ORDER BY day"""
    rows = conn.execute(sql, [owner, start, end, *params]).fetchall()
    return {
        r["day"]: {
            "seconds": r["seconds"],
            "sessions": r["sessions"],
            "inferred": r["inferred"],
        }
        for r in rows
    }


def period_facts(
    conn,
    start: str,
    end: str,
    *,
    owner: str = DEFAULT_OWNER,
    scope: str = "all",
    score_id=None,
    activity=None,
) -> dict:
    """What actually happened between two days, stated and not judged.

    The per-day list covers every day in the period, including the ones with
    nothing on them. A day with no practice is a fact about the week and is
    reported as zero seconds; it is not an absence for a reader to have to
    infer from a gap in a list, and it is not a failure.
    """
    params: list = []
    extra = _scope_filter(scope, score_id, activity, params)
    totals = day_totals(conn, owner, start, end, extra, params)
    days = [
        {
            "date": day.isoformat(),
            "seconds": totals.get(day.isoformat(), {}).get("seconds", 0),
            "sessions": totals.get(day.isoformat(), {}).get("sessions", 0),
            "inferred": totals.get(day.isoformat(), {}).get("inferred", 0),
        }
        for day in days_between(parse_day(start), parse_day(end))
    ]
    seconds = sum(d["seconds"] for d in days)
    return {
        "days": days,
        "seconds": seconds,
        # Floored, so this can never claim a minute that was not practised.
        "minutes": seconds // 60,
        "days_practised": sum(1 for d in days if d["sessions"]),
        "sessions": sum(d["sessions"] for d in days),
        # How much of the above rests on a day nobody recorded. A single
        # session says whether its day was recorded or taken from its UTC
        # timestamp; a total said nothing, so a window spanning the upgrade
        # quietly added two kinds of day together. Zero on any install that has
        # only ever run this version, which is the point: it is silent when
        # there is nothing to disclose.
        "sessions_inferred": sum(d["inferred"] for d in days),
    }


def _period_status(goal, today: date) -> tuple[str, int]:
    """Where the period sits relative to today, and how much of it is left.

    Says nothing about how the period went - a goal not reached and a goal
    reached are both 'past' once the week is over. `days_left` counts today
    itself, because a day somebody still has is a day they can practise in.
    """
    start = parse_day(goal["period_start"])
    end = parse_day(goal["period_end"])
    if today < start:
        return "upcoming", (end - start).days + 1
    if today > end:
        return "past", 0
    return "running", (end - today).days + 1


def goal_progress(conn, goal, today: date, facts: dict | None = None) -> dict:
    """Where a goal stands: the counts, the targets, and nothing else.

    `met_days` and `met_minutes` are None when that target was not set, which
    is not the same as unmet - a goal with no minutes target has nothing to
    say about minutes, and a False there would read as a shortfall against a
    target nobody chose.

    `status` is where the period sits relative to today, not how it went. A
    period still running is reported with `days_left` so the goal can still
    change the week rather than only judge it - and deliberately without any
    per-day rate needed "to catch up", which is a verdict wearing arithmetic.

    `countable` is False for a goal about a piece the library no longer has.
    The practice is still there, but nothing now says which of it was about
    that piece, so the goal cannot be counted - and reporting that is the only
    honest option. Reporting zero days instead would turn a week somebody
    practised into a shortfall, and a goal that was reached into one that was
    not, because a file was deleted afterwards.
    """
    status, days_left = _period_status(goal, today)
    countable = not (goal["scope"] == "score" and goal["score_id"] is None)
    if not countable:
        return {
            "days": [
                {"date": day.isoformat(), "seconds": 0, "sessions": 0}
                for day in days_between(
                    parse_day(goal["period_start"]), parse_day(goal["period_end"])
                )
            ],
            "seconds": 0,
            "minutes": 0,
            "days_practised": 0,
            "sessions": 0,
            "status": status,
            "days_left": days_left,
            "countable": False,
            # None and not False: nothing here is a shortfall. There is simply
            # no answer to be had, and a reader must say so rather than draw
            # one from the zeros above - which are here only so the shape of
            # this object never changes.
            "met_days": None,
            "met_minutes": None,
            "met": None,
        }
    # `facts` may be supplied by a caller that has already counted exactly this
    # period with exactly this scope - the review, whose week facts and a
    # scope='all' goal's progress are the same question. Only ever an
    # optimisation: passing facts for a different period or scope would make
    # this report something it did not measure, which is why the review passes
    # them only for scope='all'.
    if facts is None:
        facts = period_facts(
            conn,
            goal["period_start"],
            goal["period_end"],
            owner=goal["owner"],
            scope=goal["scope"],
            score_id=goal["score_id"],
            activity=goal["activity"],
        )
    target_days = goal["target_days"]
    target_minutes = goal["target_minutes"]
    met_days = None if target_days is None else facts["days_practised"] >= target_days
    met_minutes = None if target_minutes is None else facts["minutes"] >= target_minutes
    return {
        **facts,
        "status": status,
        "days_left": days_left,
        "countable": True,
        "met_days": met_days,
        "met_minutes": met_minutes,
        # True only when every target that was set has been reached. None of
        # the three values is a grade: "not yet" during a running week and
        # "not this time" after it are the same False, and the interface says
        # which by reading `status`.
        "met": all(m for m in (met_days, met_minutes) if m is not None)
        and any(m is not None for m in (met_days, met_minutes)),
    }


def goal_dict(conn, row, today: date, facts: dict | None = None) -> dict:
    """A goal with its progress attached, and its piece named if it has one."""
    d = dict(row)
    d["score_title"] = None
    if d["score_id"] is not None:
        title = conn.execute(
            "SELECT title FROM scores WHERE id = ?", (d["score_id"],)
        ).fetchone()
        d["score_title"] = title["title"] if title else None
    d["progress"] = goal_progress(conn, row, today, facts=facts)
    return d


def time_spent(
    conn, start: str, end: str, *, owner: str = DEFAULT_OWNER, limit: int = 20
) -> dict:
    """Where the time went between two days: by piece, and by kind of work.

    Ordered by time spent, which is the question being asked, and not
    presented anywhere as a ranking of pieces or of weeks. Sessions with no
    score - a trainer, unstructured playing - are counted in `by_activity` and
    simply absent from `by_score`, which is the honest answer to "which pieces
    did I work on" rather than a bucket labelled "no piece" that a reader has
    to know to ignore.
    """
    # `deleted` travels with the row (#56). A score somebody deleted is STILL
    # listed here, because the hours were spent and dropping it would leave this
    # breakdown not adding up to the total beside it - the same reason
    # `scores_worked` below exists. What it must not do is read as a piece that
    # is in the library: a client can render it as gone, and stop linking to it,
    # only if it is told.
    by_score = conn.execute(
        f"""SELECT p.score_id, s.title,
                   SUM(p.seconds) AS seconds, COUNT(*) AS sessions,
                   MAX({LOCAL_DATE_SQL}) AS last_practised,
                   s.deleted_at IS NOT NULL AS deleted
              FROM practice_sessions p JOIN scores s ON s.id = p.score_id
             WHERE p.owner = ? AND {LOCAL_DATE_SQL} BETWEEN ? AND ?
          GROUP BY p.score_id ORDER BY seconds DESC, s.title LIMIT ?""",
        (owner, start, end, limit),
    ).fetchall()
    # How many pieces were worked on, not how many are listed. Without this a
    # by-piece breakdown that stopped at the limit read as a complete account
    # of where the time went, and its numbers did not add up to the total
    # beside it with nothing saying why.
    scores_worked = conn.execute(
        f"""SELECT COUNT(DISTINCT p.score_id) AS n FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id IS NOT NULL
               AND {LOCAL_DATE_SQL} BETWEEN ? AND ?""",
        (owner, start, end),
    ).fetchone()["n"]
    by_activity = conn.execute(
        f"""SELECT p.activity, SUM(p.seconds) AS seconds, COUNT(*) AS sessions
              FROM practice_sessions p
             WHERE p.owner = ? AND {LOCAL_DATE_SQL} BETWEEN ? AND ?
          GROUP BY p.activity ORDER BY seconds DESC, p.activity""",
        (owner, start, end),
    ).fetchall()
    return {
        "by_score": [{**dict(r), "deleted": bool(r["deleted"])} for r in by_score],
        "by_activity": [dict(r) for r in by_activity],
        "scores_worked": scores_worked,
        "by_score_truncated": scores_worked > len(by_score),
    }


# ---------------------------------------------------------------------------
# ONE PIECE: how is this piece going (#57).
#
# Everything above answers "how am I doing" across the library. This answers
# the other question the issue names, and says it is a different one: a person
# deciding what to practise next needs the per-piece picture as much as the
# overall one, and reassembling it from the general endpoints meant a client
# filtering the whole history by score_id and doing the arithmetic itself -
# which is the arithmetic a second reader (the planned MCP server, #31) would
# then have to write again and get subtly differently.
#
# WHAT THIS DELIBERATELY DOES NOT COMPUTE, and will not:
#
#   No streak, and no run of days. docs/practice-data.md lists it under what
#   is deliberately absent, and issue #3 asks for it by name in its "deliberately
#   not" list: missing a week because of a busy job is information, not a moral
#   failure. A per-piece view is exactly where a run of days would be most
#   tempting and would do the most damage, since a piece is put down and picked
#   up again by design.
#
#   No fitted line through the tempo points, and no rate of improvement. The
#   points themselves are facts somebody entered; a slope drawn through three
#   of them is this application claiming to know something it does not, which
#   is the "without pretending to more analysis than the data supports" half of
#   the issue. `comparable` says whether there is more than one point, so a
#   reader can decline to draw anything rather than drawing a confident line
#   through a single session.
#
#   No average rating and no accuracy. Counts per rating and never a mean, for
#   the same reason a drill records counts and never a rate: a number out of
#   five is a mark rather than a fact, and it invites a colour.
# ---------------------------------------------------------------------------


def score_all_time(conn, score_id: int, *, owner: str = DEFAULT_OWNER) -> dict:
    """Everything ever practised on one piece, ignoring any window.

    Separate from the windowed figures beside it, and named so, because "when
    did I last play this" has no window: a piece untouched for four months
    answers that with a date and answers "how much this quarter" with a zero,
    and the two must not be read off each other. `first_practised` is the day
    it entered the record, which is the only honest way to say how long
    somebody has been working on something.
    """
    row = conn.execute(
        f"""SELECT COUNT(*) AS sessions,
                   COALESCE(SUM(p.seconds), 0) AS seconds,
                   MIN({LOCAL_DATE_SQL}) AS first_practised,
                   MAX({LOCAL_DATE_SQL}) AS last_practised,
                   COALESCE(SUM(CASE WHEN p.local_date IS NULL THEN 1 ELSE 0 END), 0)
                       AS sessions_inferred
              FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id = ?""",
        (owner, score_id),
    ).fetchone()
    seconds = row["seconds"]
    return {
        "sessions": row["sessions"],
        "seconds": seconds,
        # Floored, like every other minute figure here, so this can never
        # claim a minute that was not practised.
        "minutes": seconds // 60,
        "first_practised": row["first_practised"],
        "last_practised": row["last_practised"],
        "sessions_inferred": row["sessions_inferred"],
    }


def tempo_progression(
    conn, score_id: int, start: str, end: str, *, owner: str = DEFAULT_OWNER
) -> dict:
    """The tempo each session on this piece was practised at, oldest first.

    THE POINTS ARE THE ANSWER. Each one is two numbers somebody entered - what
    they played it at and what they were aiming for - and their own day. There
    is no fitted line, no slope, no "improving", and no figure for how much
    faster this month is than last: a tempo ladder is climbed and fallen off
    and climbed again, and a trend drawn through that is a claim about a
    person's playing that these numbers cannot support.

    `axis_low` and `axis_high` are the chart's bounds and nothing else. They
    span both numbers, so a target line fits on the same axis as the tempos
    under it, and they are computed here rather than in a client because two
    readers deriving an axis differently is two charts that disagree about the
    same history. They are NOT a personal best: nothing states them as text,
    and there is no field here comparing one point to another.

    `comparable` is whether there is more than one point. One session at a
    tempo is one session at a tempo - it is not a progression, and a view that
    draws it as one is inventing the thing the reader came to look for.
    `sessions_without_tempo` is how much of this piece's practice these points
    say nothing about, so a sparse chart is not read as a sparse month.
    """
    rows = conn.execute(
        f"""SELECT p.id AS session_id, {LOCAL_DATE_SQL} AS date,
                   p.tempo_bpm, p.target_tempo_bpm, p.mode
              FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id = ? AND p.tempo_bpm IS NOT NULL
               AND {LOCAL_DATE_SQL} BETWEEN ? AND ?
          ORDER BY date, p.started_at, p.id""",
        (owner, score_id, start, end),
    ).fetchall()
    points = []
    for r in rows:
        tempo, target = r["tempo_bpm"], r["target_tempo_bpm"]
        points.append(
            {
                "session_id": r["session_id"],
                "date": r["date"],
                "tempo_bpm": tempo,
                "target_tempo_bpm": target,
                # The same derivation session_dict makes, for the same reason:
                # a stored answer is one that can contradict the two numbers it
                # came from. None means one of them is missing, which is not
                # the same as "did not reach it".
                "reached_target": None if target is None else tempo >= target,
                "mode": r["mode"],
            }
        )
    without_tempo = conn.execute(
        f"""SELECT COUNT(*) AS n FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id = ? AND p.tempo_bpm IS NULL
               AND {LOCAL_DATE_SQL} BETWEEN ? AND ?""",
        (owner, score_id, start, end),
    ).fetchone()["n"]
    numbers = [p["tempo_bpm"] for p in points]
    numbers += [p["target_tempo_bpm"] for p in points if p["target_tempo_bpm"] is not None]
    # The target most recently written down, which is what a piece is currently
    # being worked towards. The LATEST and not the highest ever set: somebody
    # who decided 120 was too fast and set 100 is aiming at 100, and reporting
    # the number they abandoned would be this view arguing with them.
    latest_target = next(
        (p["target_tempo_bpm"] for p in reversed(points) if p["target_tempo_bpm"] is not None),
        None,
    )
    return {
        "points": points,
        "count": len(points),
        "sessions_without_tempo": without_tempo,
        "axis_low": min(numbers) if numbers else None,
        "axis_high": max(numbers) if numbers else None,
        "latest_target": latest_target,
        "comparable": len(points) > 1,
    }


def mode_totals(
    conn, score_id: int, start: str, end: str, *, owner: str = DEFAULT_OWNER
) -> list[dict]:
    """Where this piece's time went between picking at a section and playing it
    through - #32's "distinguish focused section work from full run-throughs",
    asked of one piece.

    A session that did not say which it was comes back with `mode` null rather
    than being dropped or filed under one of the two. It is time that was
    genuinely spent, and guessing which kind it was from whether a bar range
    happens to be present is exactly what the stored column exists to avoid.
    """
    rows = conn.execute(
        f"""SELECT p.mode, SUM(p.seconds) AS seconds, COUNT(*) AS sessions
              FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id = ? AND {LOCAL_DATE_SQL} BETWEEN ? AND ?
          GROUP BY p.mode
          ORDER BY seconds DESC, p.mode IS NULL, p.mode""",
        (owner, score_id, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def rating_counts(
    conn, score_id: int, start: str, end: str, *, owner: str = DEFAULT_OWNER
) -> dict:
    """How many sessions on this piece got each 1-5 rating, and how many got
    none.

    COUNTS AND NEVER A MEAN. An average rating is a mark out of five wearing a
    decimal point, and it is the field a client would eventually colour. The
    same rule the ear-training drill follows for its answers (see
    docs/practice-data.md) - a rate is a grade and a count is a fact.

    Every rating from 1 to 5 is present whether or not it was ever chosen. A
    bucket somebody never used is a fact about how they rate their own
    practice, and a list with a hole in it is one a reader has to reconstruct
    the missing rows of before it can be drawn.
    """
    rows = conn.execute(
        f"""SELECT p.rating, COUNT(*) AS sessions
              FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id = ? AND {LOCAL_DATE_SQL} BETWEEN ? AND ?
          GROUP BY p.rating""",
        (owner, score_id, start, end),
    ).fetchall()
    counted = {r["rating"]: r["sessions"] for r in rows}
    return {
        "counts": [
            {"rating": rating, "sessions": counted.get(rating, 0)}
            for rating in range(MIN_RATING, MAX_RATING + 1)
        ],
        "rated": sum(n for rating, n in counted.items() if rating is not None),
        "unrated": counted.get(None, 0),
    }


def score_goals(
    conn,
    score_id: int,
    start: str,
    end: str,
    today: date,
    *,
    owner: str = DEFAULT_OWNER,
    limit: int = MAX_SCORE_GOALS,
) -> list[dict]:
    """Goals set about this piece whose period touches the window, newest
    first, each with its progress counted the way every other goal's is.

    Scoped goals only. A goal over "any practice" is not a goal about this
    piece even in a week when this piece was the only thing practised, and
    listing it here would put somebody's whole-week intention on a page about
    one score, where it reads as a target for that score alone.
    """
    rows = conn.execute(
        """SELECT * FROM practice_goals
            WHERE owner = ? AND scope = 'score' AND score_id = ?
              AND period_end >= ? AND period_start <= ?
         ORDER BY period_start DESC LIMIT ?""",
        (owner, score_id, start, end, limit),
    ).fetchall()
    return [goal_dict(conn, r, today) for r in rows]


def score_sessions(
    conn,
    score_id: int,
    start: str,
    end: str,
    *,
    owner: str = DEFAULT_OWNER,
    limit: int = DEFAULT_SCORE_SESSION_LIMIT,
) -> dict:
    """This piece's own sessions in the window, newest first, with their notes.

    Reports `total` and `truncated` beside the rows for the same reason
    /practice/sessions does: a list that stops at the limit and says nothing
    looks identical to a complete one, and a reader totalling it would report
    less practice than there was.
    """
    rows = conn.execute(
        f"""SELECT * FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id = ? AND {LOCAL_DATE_SQL} BETWEEN ? AND ?
          ORDER BY {LOCAL_DATE_SQL} DESC, p.started_at DESC, p.id DESC
             LIMIT ?""",
        (owner, score_id, start, end, limit),
    ).fetchall()
    total = conn.execute(
        f"""SELECT COUNT(*) AS n FROM practice_sessions p
             WHERE p.owner = ? AND p.score_id = ? AND {LOCAL_DATE_SQL} BETWEEN ? AND ?""",
        (owner, score_id, start, end),
    ).fetchone()["n"]
    return {
        "sessions": [session_dict(r) for r in rows],
        "total": total,
        "truncated": total > len(rows),
    }
