"""The practice model's own rules, called directly.

The API tests exercise these through HTTP-shaped calls; these exercise the
arithmetic and the vocabulary where they live, because the interesting failures
are all in the arithmetic. A day count that quietly used the UTC date, a
partially-met goal that reported itself met, a week that always started on
Monday - none of those break an endpoint. They just answer wrong.
"""

from datetime import date, timedelta

import pytest

from fermata import db, practice


@pytest.fixture
def conn(app_env):
    return db.connect()


def _score(conn, title="Test Score", path="a/b.pdf") -> int:
    cur = conn.execute(
        """INSERT INTO scores(title, path, file_type, hash, size, mtime)
           VALUES (?, ?, 'pdf', 'deadbeef', 1, 0.0)""",
        (title, path),
    )
    conn.commit()
    return cur.lastrowid


def _log(conn, *, day, seconds, score_id=None, activity="piece", started_at=None):
    """A session as it really sits in the table.

    started_at defaults to the small hours of the day AFTER `day`, which is
    what an evening's practice west of Greenwich actually stores - and it is
    deliberate: a fixture whose UTC timestamp and recorded practice day agree
    cannot tell a query that reads the right one from a query that reads the
    wrong one. Every day-based assertion in this file is therefore also a
    check that the recorded practice day is what got counted.
    """
    if started_at is None:
        started_at = f"{date.fromisoformat(day) + timedelta(days=1)} 02:30:00"
    conn.execute(
        """INSERT INTO practice_sessions(owner, score_id, activity, started_at, local_date, seconds)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (db.DEFAULT_OWNER, score_id, activity, started_at, day, seconds),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Dates and weeks
# ---------------------------------------------------------------------------


def test_a_practice_day_must_be_a_plain_calendar_date():
    """Strict on purpose: date.fromisoformat also accepts '20260817' and full
    timestamps, and a timestamp stored where a day belongs is text no BETWEEN
    over practice days would ever match again."""
    assert practice.parse_day("2026-08-17") == date(2026, 8, 17)
    for bad in ["20260817", "2026-08-17T10:00:00", "2026-8-7", "", None, "not a date",
                "2026-13-01", "2026-02-30"]:
        with pytest.raises(ValueError):
            practice.parse_day(bad)


def test_a_date_outside_the_plausible_range_is_refused():
    """Not a judgement about anybody's practice - it is what keeps the
    arithmetic downstream inside date's own bounds. Every caller adds or
    subtracts up to a year, and date.min/date.max raise OverflowError from
    inside a subtraction, which reaches a client as a 500 for a typo."""
    for bad in ["0001-01-01", "1899-12-31", "9999-12-31", "2200-01-02"]:
        with pytest.raises(ValueError, match="between"):
            practice.parse_day(bad)
    for good in ["1900-01-01", "2026-08-17", "2200-01-01"]:
        assert practice.parse_day(good).isoformat() == good


def test_the_day_index_is_the_one_every_day_query_can_actually_use(conn):
    """The index is on the EXPRESSION these queries filter by, not on the
    column, because a plain index on local_date cannot serve a wrapped one -
    SQLite would scan the owner's whole practice history to answer a question
    about seven days of it. Asserted through the query planner rather than by
    reading the DDL, because the expression has to match character for
    character and nothing else would notice when it stops."""
    plan = " ".join(
        str(row[3])
        for row in conn.execute(
            f"""EXPLAIN QUERY PLAN
                SELECT {practice.LOCAL_DATE_SQL} AS day, SUM(p.seconds)
                  FROM practice_sessions p
                 WHERE p.owner = ? AND {practice.LOCAL_DATE_SQL} BETWEEN ? AND ?
              GROUP BY day""",
            ("local", "2026-08-17", "2026-08-23"),
        )
    )
    assert "idx_practice_day" in plan, plan
    assert "SCAN" not in plan, plan


def test_a_monday_start_week_runs_monday_to_sunday():
    # 2026-08-17 is a Monday.
    for day in ("2026-08-17", "2026-08-19", "2026-08-23"):
        assert practice.week_start(date.fromisoformat(day), "monday") == date(2026, 8, 17)
    assert practice.week_start(date(2026, 8, 16), "monday") == date(2026, 8, 10)


def test_a_sunday_start_week_runs_sunday_to_saturday():
    # The same Monday belongs to a week that began the day before.
    assert practice.week_start(date(2026, 8, 17), "sunday") == date(2026, 8, 16)
    assert practice.week_start(date(2026, 8, 16), "sunday") == date(2026, 8, 16)
    assert practice.week_start(date(2026, 8, 22), "sunday") == date(2026, 8, 16)
    assert practice.week_start(date(2026, 8, 23), "sunday") == date(2026, 8, 23)


def test_every_weekday_is_covered_by_exactly_one_week_under_either_setting():
    """Not a restatement of the offsets above: this walks a whole year and
    checks each day lands in a seven-day window that contains it, which is what
    an off-by-one in the modulo actually breaks."""
    for starts_on in practice.WEEK_STARTS:
        for ordinal in range(date(2026, 1, 1).toordinal(), date(2026, 12, 31).toordinal()):
            day = date.fromordinal(ordinal)
            start, end = practice.period_bounds(practice.week_start(day, starts_on))
            assert start <= day <= end
            assert (end - start).days == 6
            expected_first = 0 if starts_on == "monday" else 6
            assert start.weekday() == expected_first


def test_an_unknown_week_start_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError):
        practice.week_start(date(2026, 8, 17), "tuesday")


# ---------------------------------------------------------------------------
# A session's rules
# ---------------------------------------------------------------------------

TODAY = date(2026, 8, 20)


def _session(**over):
    fields = {
        "score_id": 1,
        "activity": "piece",
        "mode": None,
        "seconds": 600,
        "local_date": "2026-08-20",
        "recorded_on": TODAY,
    }
    fields.update(over)
    return practice.normalise_session(**fields)


def test_a_session_on_a_piece_needs_a_piece():
    with pytest.raises(ValueError, match="score_id"):
        _session(score_id=None)


def test_every_other_kind_of_practice_needs_no_piece():
    """The reason the column had to become nullable. Ear training and simply
    sitting down to play are practice, and neither has a score behind it."""
    for activity in practice.ACTIVITIES:
        if activity == "piece":
            continue
        assert _session(score_id=None, activity=activity)["score_id"] is None


def test_an_unknown_activity_or_mode_is_refused():
    with pytest.raises(ValueError):
        _session(activity="vibes")
    with pytest.raises(ValueError):
        _session(mode="noodling")
    assert _session(mode="section")["mode"] == "section"
    assert _session(mode="run_through")["mode"] == "run_through"


def test_practice_cannot_be_recorded_for_a_day_that_has_not_happened():
    """A goal counts the days inside its period. Practice dated into the future
    would fill one in before it happened."""
    with pytest.raises(ValueError, match="future"):
        _session(local_date="2026-08-25")
    # Tomorrow is allowed: a client an hour ahead of the server's UTC date is
    # not a broken clock, it is a timezone.
    assert _session(local_date="2026-08-21")["local_date"] == "2026-08-21"


def test_a_forgotten_session_can_be_entered_late_but_not_arbitrarily_late():
    assert _session(local_date="2026-08-14")["local_date"] == "2026-08-14"
    with pytest.raises(ValueError, match="days ago"):
        _session(local_date="2020-01-01")


def test_no_practice_day_given_means_none_is_stored():
    """Not the server's own date: a client that does not know its timezone
    should not have one invented for it, and the reader is told the day it gets
    came from the timestamp."""
    assert _session(local_date=None)["local_date"] is None


def test_a_length_has_to_be_a_plausible_length():
    for bad in [0, -1, 86_401, True, 1.5, "600"]:
        with pytest.raises(ValueError):
            _session(seconds=bad)
    assert _session(seconds=86_400)["seconds"] == 86_400


def test_a_range_needs_its_start_and_cannot_run_backwards():
    assert _session(from_bar=17, to_bar=32)["to_bar"] == 32
    assert _session(from_bar=17)["to_bar"] is None
    with pytest.raises(ValueError, match="from_bar"):
        _session(to_bar=32)
    with pytest.raises(ValueError, match="before"):
        _session(from_bar=32, to_bar=17)
    with pytest.raises(ValueError, match="from_page"):
        _session(to_page=3)


def test_a_tempo_outside_anything_playable_is_a_units_mistake():
    for bad in [0, 3, 19, 401, 3000]:
        with pytest.raises(ValueError):
            _session(tempo_bpm=bad)
    assert _session(tempo_bpm=76, target_tempo_bpm=120)["target_tempo_bpm"] == 120


def test_a_rating_is_one_to_five_and_optional():
    assert _session(rating=None)["rating"] is None
    for value in range(1, 6):
        assert _session(rating=value)["rating"] == value
    for bad in [0, 6, -1]:
        with pytest.raises(ValueError):
            _session(rating=bad)


def test_an_empty_note_is_no_note_rather_than_an_empty_one():
    assert _session(note="   ")["note"] is None
    assert _session(note="  bars 1-16  ")["note"] == "bars 1-16"
    with pytest.raises(ValueError):
        _session(note="x" * (practice.MAX_NOTE_CHARS + 1))


# ---------------------------------------------------------------------------
# Whether a tempo ladder reached its target - derived, never stored
# ---------------------------------------------------------------------------


def test_reaching_the_target_is_the_comparison_and_not_a_stored_opinion(conn):
    score_id = _score(conn)
    cases = [
        (120, 120, True),
        (121, 120, True),
        (100, 120, False),
        (None, 120, None),
        (100, None, None),
        (None, None, None),
    ]
    for tempo, target, expected in cases:
        conn.execute(
            """INSERT INTO practice_sessions
                   (owner, score_id, activity, started_at, local_date, seconds,
                    tempo_bpm, target_tempo_bpm)
               VALUES (?, ?, 'piece', datetime('now'), '2026-08-20', 600, ?, ?)""",
            (db.DEFAULT_OWNER, score_id, tempo, target),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM practice_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert practice.session_dict(row)["reached_target"] is expected, (tempo, target)
    # There is no column for it, which is what stops the answer and the two
    # numbers it comes from from ever disagreeing.
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(practice_sessions)")}
    assert "reached_target" not in columns


# ---------------------------------------------------------------------------
# A goal's rules
# ---------------------------------------------------------------------------


def _goal(**over):
    fields = {
        "period": "week",
        "period_start": "2026-08-17",
        "target_days": 4,
        "target_minutes": None,
        "scope": "all",
        "score_id": None,
        "activity": None,
        "intent": None,
    }
    fields.update(over)
    return practice.normalise_goal(**fields)


def test_a_goal_has_to_be_concrete_enough_to_be_met_or_missed():
    with pytest.raises(ValueError, match="needs a target"):
        _goal(target_days=None, target_minutes=None)
    assert _goal(target_days=None, target_minutes=150)["target_minutes"] == 150
    both = _goal(target_days=3, target_minutes=150)
    assert (both["target_days"], both["target_minutes"]) == (3, 150)


def test_a_weekly_goal_cannot_ask_for_more_days_than_the_week_has():
    assert _goal(target_days=7)["target_days"] == 7
    with pytest.raises(ValueError):
        _goal(target_days=8)
    with pytest.raises(ValueError):
        _goal(target_days=0)


def test_the_period_end_is_derived_from_its_start(conn):
    goal = _goal(period_start="2026-08-17")
    assert (goal["period_start"], goal["period_end"]) == ("2026-08-17", "2026-08-23")


def test_each_scope_names_only_the_thing_it_is_scoped_to():
    """A goal carrying a score_id it does not use would read as a goal about
    that piece while being counted over everything practised."""
    everything = _goal(scope="all", score_id=4, activity="technique")
    assert everything["score_id"] is None and everything["activity"] is None

    one_piece = _goal(scope="score", score_id=4, activity="technique")
    assert one_piece["score_id"] == 4 and one_piece["activity"] is None

    one_kind = _goal(scope="activity", activity="technique", score_id=4)
    assert one_kind["activity"] == "technique" and one_kind["score_id"] is None


def test_a_scoped_goal_needs_the_thing_it_is_scoped_to():
    with pytest.raises(ValueError, match="score_id"):
        _goal(scope="score", score_id=None)
    # Unless it is already stored that way, because its piece has since been
    # deleted - such a goal can no longer be counted but must stay writable.
    assert _goal(scope="score", score_id=None, allow_missing_score=True)["score_id"] is None
    with pytest.raises(ValueError, match="activity"):
        _goal(scope="activity", activity=None)
    with pytest.raises(ValueError, match="activity"):
        _goal(scope="activity", activity="vibes")


def test_an_unknown_scope_or_period_is_refused():
    with pytest.raises(ValueError):
        _goal(scope="instrument")
    with pytest.raises(ValueError):
        _goal(period="fortnight")


def test_the_reflection_answers_a_question_and_is_not_a_status():
    assert practice.normalise_reflection(reflection="  busy week  ", realistic="no") == {
        "reflection": "busy week",
        "realistic": "no",
    }
    assert practice.normalise_reflection(reflection="", realistic=None) == {
        "reflection": None,
        "realistic": None,
    }
    for bad in ["maybe", "failed", "met", "true"]:
        with pytest.raises(ValueError):
            practice.normalise_reflection(reflection=None, realistic=bad)


# ---------------------------------------------------------------------------
# What actually happened, counted
# ---------------------------------------------------------------------------


def test_a_practice_day_is_the_practisers_day_and_not_the_utc_one(conn):
    """The whole reason local_date exists. Both sessions here have a UTC
    timestamp on the 21st and were practised on the evening of the 20th; a
    count off the timestamp puts them in the wrong day and, at a week boundary,
    the wrong week."""
    score_id = _score(conn)
    _log(conn, day="2026-08-20", seconds=1800, score_id=score_id,
         started_at="2026-08-21 02:30:00")
    _log(conn, day="2026-08-20", seconds=600, score_id=score_id,
         started_at="2026-08-21 03:10:00")

    facts = practice.period_facts(conn, "2026-08-17", "2026-08-23")
    assert facts["days_practised"] == 1
    assert facts["seconds"] == 2400
    on_the_20th = next(d for d in facts["days"] if d["date"] == "2026-08-20")
    assert (on_the_20th["seconds"], on_the_20th["sessions"]) == (2400, 2)
    assert next(d for d in facts["days"] if d["date"] == "2026-08-21")["sessions"] == 0


def test_a_day_with_nothing_on_it_is_reported_as_zero_not_left_out(conn):
    """A gap in a list is something a reader has to infer. Seven days, always,
    so a week can be drawn without inventing the days nobody practised - and
    they are zeroes, not failures."""
    score_id = _score(conn)
    _log(conn, day="2026-08-19", seconds=900, score_id=score_id)
    facts = practice.period_facts(conn, "2026-08-17", "2026-08-23")
    assert [d["date"] for d in facts["days"]] == [
        "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20",
        "2026-08-21", "2026-08-22", "2026-08-23",
    ]
    assert [d["seconds"] for d in facts["days"]] == [0, 0, 900, 0, 0, 0, 0]


def test_minutes_never_claim_a_minute_that_was_not_practised(conn):
    score_id = _score(conn)
    _log(conn, day="2026-08-19", seconds=119, score_id=score_id)
    assert practice.period_facts(conn, "2026-08-17", "2026-08-23")["minutes"] == 1


def test_two_sessions_in_one_day_are_one_day_of_practice(conn):
    """Days, not sessions, and this is why: counting sessions rewards breaking
    an hour into six, which is not what a goal for four days a week means."""
    score_id = _score(conn)
    _log(conn, day="2026-08-19", seconds=600, score_id=score_id)
    _log(conn, day="2026-08-19", seconds=600, score_id=score_id)
    facts = practice.period_facts(conn, "2026-08-17", "2026-08-23")
    assert (facts["days_practised"], facts["sessions"]) == (1, 2)


def test_practice_outside_the_period_is_not_counted_in_it(conn):
    score_id = _score(conn)
    _log(conn, day="2026-08-16", seconds=3600, score_id=score_id)  # day before
    _log(conn, day="2026-08-24", seconds=3600, score_id=score_id)  # day after
    _log(conn, day="2026-08-17", seconds=600, score_id=score_id)  # first day, inclusive
    _log(conn, day="2026-08-23", seconds=600, score_id=score_id)  # last day, inclusive
    facts = practice.period_facts(conn, "2026-08-17", "2026-08-23")
    assert (facts["days_practised"], facts["seconds"]) == (2, 1200)


def test_a_scoped_period_counts_only_what_it_is_scoped_to(conn):
    piece = _score(conn, "Study in C", "a/study.pdf")
    other = _score(conn, "Something Else", "a/other.pdf")
    _log(conn, day="2026-08-17", seconds=600, score_id=piece)
    _log(conn, day="2026-08-18", seconds=600, score_id=other)
    _log(conn, day="2026-08-19", seconds=600, score_id=None, activity="ear_training")

    everything = practice.period_facts(conn, "2026-08-17", "2026-08-23")
    assert (everything["days_practised"], everything["seconds"]) == (3, 1800)

    one_piece = practice.period_facts(
        conn, "2026-08-17", "2026-08-23", scope="score", score_id=piece
    )
    assert (one_piece["days_practised"], one_piece["seconds"]) == (1, 600)

    one_kind = practice.period_facts(
        conn, "2026-08-17", "2026-08-23", scope="activity", activity="ear_training"
    )
    assert (one_kind["days_practised"], one_kind["seconds"]) == (1, 600)


# ---------------------------------------------------------------------------
# Where a goal stands
# ---------------------------------------------------------------------------


def _stored_goal(conn, **over):
    values = _goal(**over)
    columns = ", ".join(values)
    placeholders = ", ".join("?" * len(values))
    conn.execute(
        f"INSERT INTO practice_goals(owner, {columns}) VALUES (?, {placeholders})",
        [db.DEFAULT_OWNER, *values.values()],
    )
    conn.commit()
    return conn.execute("SELECT * FROM practice_goals ORDER BY id DESC LIMIT 1").fetchone()


def test_a_partly_met_goal_reports_both_numbers_and_no_verdict(conn):
    """Three of four planned days. The counts are stated, `met` is False, and
    there is nothing here that grades the shortfall - no percentage, no
    'missed', and nothing comparing this week to another."""
    score_id = _score(conn)
    for day in ("2026-08-17", "2026-08-18", "2026-08-20"):
        _log(conn, day=day, seconds=1800, score_id=score_id)
    goal = _stored_goal(conn, target_days=4, target_minutes=120)

    progress = practice.goal_progress(conn, goal, date(2026, 8, 24))
    assert progress["days_practised"] == 3
    assert progress["minutes"] == 90
    assert progress["met_days"] is False
    assert progress["met_minutes"] is False
    assert progress["met"] is False
    assert progress["status"] == "past"
    assert not any("best" in key or "streak" in key for key in progress)


def test_a_target_that_was_not_set_reports_none_rather_than_unmet(conn):
    """False would read as a shortfall against a target nobody chose."""
    score_id = _score(conn)
    _log(conn, day="2026-08-17", seconds=600, score_id=score_id)
    goal = _stored_goal(conn, target_days=1, target_minutes=None)
    progress = practice.goal_progress(conn, goal, date(2026, 8, 24))
    assert progress["met_days"] is True
    assert progress["met_minutes"] is None
    assert progress["met"] is True


def test_a_goal_is_met_only_when_every_target_it_set_is_reached(conn):
    score_id = _score(conn)
    for day in ("2026-08-17", "2026-08-18"):
        _log(conn, day=day, seconds=1800, score_id=score_id)
    goal = _stored_goal(conn, target_days=2, target_minutes=120)
    progress = practice.goal_progress(conn, goal, date(2026, 8, 24))
    assert (progress["met_days"], progress["met_minutes"]) == (True, False)
    assert progress["met"] is False


def test_a_running_period_says_how_much_of_it_is_left_and_nothing_about_pace(conn):
    """So the goal can still change the week. Deliberately no per-day rate
    'needed to catch up' - that is a verdict wearing arithmetic."""
    score_id = _score(conn)
    _log(conn, day="2026-08-17", seconds=1800, score_id=score_id)
    goal = _stored_goal(conn, target_days=4)

    running = practice.goal_progress(conn, goal, date(2026, 8, 19))
    assert running["status"] == "running"
    assert running["days_left"] == 5  # the 19th through the 23rd, inclusive
    assert running["met"] is False
    assert not any("pace" in key or "needed" in key for key in running)

    last_day = practice.goal_progress(conn, goal, date(2026, 8, 23))
    assert (last_day["status"], last_day["days_left"]) == ("running", 1)

    after = practice.goal_progress(conn, goal, date(2026, 8, 24))
    assert (after["status"], after["days_left"]) == ("past", 0)

    before = practice.goal_progress(conn, goal, date(2026, 8, 10))
    assert (before["status"], before["days_left"]) == ("upcoming", 7)


def test_a_goal_about_a_piece_that_is_gone_is_uncountable_rather_than_unmet(conn):
    """A goal scoped to a score that no longer exists reports that it cannot be
    counted. Zeros with met=False would say the person did not practise, when
    what actually happened is that the link between their practice and that
    piece went with the file."""
    piece = _score(conn)
    for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
        _log(conn, day=day, seconds=1800, score_id=piece)
    goal = _stored_goal(conn, scope="score", score_id=piece, target_days=3)
    assert practice.goal_progress(conn, goal, date(2026, 8, 24))["met"] is True

    conn.execute("DELETE FROM scores WHERE id = ?", (piece,))
    conn.commit()
    orphaned = conn.execute("SELECT * FROM practice_goals WHERE id = ?", (goal["id"],)).fetchone()
    progress = practice.goal_progress(conn, orphaned, date(2026, 8, 24))
    assert progress["countable"] is False
    assert progress["met"] is None
    assert progress["met_days"] is None
    assert progress["met_minutes"] is None
    # The shape is unchanged, so no reader has to test for missing keys - and
    # the period is still described, because when it was is still known.
    assert progress["status"] == "past"
    assert len(progress["days"]) == 7


def test_an_uncountable_goal_still_says_where_its_period_sits(conn):
    piece = _score(conn)
    goal = _stored_goal(conn, scope="score", score_id=piece, target_days=3)
    conn.execute("DELETE FROM scores WHERE id = ?", (piece,))
    conn.commit()
    orphaned = conn.execute("SELECT * FROM practice_goals WHERE id = ?", (goal["id"],)).fetchone()
    running = practice.goal_progress(conn, orphaned, date(2026, 8, 19))
    assert (running["status"], running["days_left"]) == ("running", 5)


def test_a_countable_goal_says_so(conn):
    """The flag has to be present and True on an ordinary goal, or a reader
    that branches on it would treat every goal as uncountable."""
    goal = _stored_goal(conn, target_days=3)
    assert practice.goal_progress(conn, goal, date(2026, 8, 19))["countable"] is True


def test_a_session_that_outlives_its_score_is_still_piece_practice(conn):
    """Deleting a score sets the reference to NULL rather than deleting the
    practice. The row keeps everything else it recorded, and the pair
    (activity='piece', score_id IS NULL) is what identifies it - a 'piece'
    session cannot be created without a score, so nothing else produces it."""
    piece = _score(conn)
    conn.execute(
        """INSERT INTO practice_sessions
               (owner, score_id, activity, started_at, local_date, seconds, rating, note)
           VALUES (?, ?, 'piece', '2026-08-18 02:00:00', '2026-08-17', 1800, 4, 'nearly there')""",
        (db.DEFAULT_OWNER, piece),
    )
    conn.commit()
    conn.execute("DELETE FROM scores WHERE id = ?", (piece,))
    conn.commit()

    row = conn.execute("SELECT * FROM practice_sessions").fetchone()
    presented = practice.session_dict(row)
    assert presented["score_id"] is None
    assert presented["score_missing"] is True
    assert (presented["seconds"], presented["rating"], presented["note"]) == (
        1800,
        4,
        "nearly there",
    )
    assert presented["local_date"] == "2026-08-17"

    # And it is still counted: the hours were spent.
    facts = practice.period_facts(conn, "2026-08-17", "2026-08-23")
    assert (facts["days_practised"], facts["seconds"]) == (1, 1800)


def test_practice_that_never_had_a_piece_is_not_reported_as_a_missing_one(conn):
    """The other half of the same signal. Ear training has no score and never
    did, and calling that a piece no longer in the library would be a lie about
    what the person did."""
    _log(conn, day="2026-08-17", seconds=600, score_id=None, activity="ear_training")
    row = conn.execute("SELECT * FROM practice_sessions").fetchone()
    assert practice.session_dict(row)["score_missing"] is False
    assert practice.is_orphaned("ear_training", None) is False
    assert practice.is_orphaned("piece", None) is True
    assert practice.is_orphaned("piece", 4) is False


def test_an_orphaned_session_can_be_revalidated_but_one_cannot_be_created(conn):
    """The allowance exists for a row that is already stored in that state, so
    a note can be added to it. It must not make the state creatable."""
    with pytest.raises(ValueError, match="score_id"):
        _session(score_id=None)
    assert _session(score_id=None, allow_missing_score=True)["score_id"] is None


def test_a_scoped_goal_is_counted_against_only_its_own_scope(conn):
    piece = _score(conn, "Study in C", "a/study.pdf")
    other = _score(conn, "Something Else", "a/other.pdf")
    for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
        _log(conn, day=day, seconds=1800, score_id=other)
    _log(conn, day="2026-08-17", seconds=1800, score_id=piece)

    goal = _stored_goal(conn, scope="score", score_id=piece, target_days=3)
    progress = practice.goal_progress(conn, goal, date(2026, 8, 24))
    assert progress["days_practised"] == 1
    assert progress["met_days"] is False


# ---------------------------------------------------------------------------
# Where the time went
# ---------------------------------------------------------------------------


def test_time_spent_names_the_pieces_and_the_kinds_of_work(conn):
    study = _score(conn, "Study in C", "a/study.pdf")
    second_piece = _score(conn, "Second Score", "a/second.pdf")
    _log(conn, day="2026-08-17", seconds=600, score_id=study)
    _log(conn, day="2026-08-18", seconds=1800, score_id=second_piece)
    _log(conn, day="2026-08-18", seconds=900, score_id=None, activity="ear_training")
    _log(conn, day="2026-08-19", seconds=300, score_id=None, activity="free")

    spent = practice.time_spent(conn, "2026-08-17", "2026-08-23")
    assert [(r["title"], r["seconds"]) for r in spent["by_score"]] == [
        ("Second Score", 1800),
        ("Study in C", 600),
    ]
    assert [(r["activity"], r["seconds"]) for r in spent["by_activity"]] == [
        ("piece", 2400),
        ("ear_training", 900),
        ("free", 300),
    ]


def test_practice_with_no_piece_is_absent_from_by_score_rather_than_bucketed(conn):
    """"Which pieces did I work on" has an honest answer that does not include
    a row labelled "no piece" for a reader to know to ignore. The time is not
    lost: it is in by_activity."""
    _log(conn, day="2026-08-17", seconds=900, score_id=None, activity="fretboard")
    spent = practice.time_spent(conn, "2026-08-17", "2026-08-23")
    assert spent["by_score"] == []
    assert [(r["activity"], r["seconds"]) for r in spent["by_activity"]] == [("fretboard", 900)]
