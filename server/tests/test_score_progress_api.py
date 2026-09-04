"""GET /api/scores/{id}/practice/progress - how one piece is going (#57).

Over real HTTP against a real database, like test_practice_api.py, and for the
same reason: the window arithmetic, the `today` parameter and the 404/422
boundaries all live in the request layer and are not exercised by calling the
handler with keyword arguments.

EVERY NUMBER HERE IS A LITERAL. Not `sum(s["seconds"] for s in sessions)`,
which recomputes the thing under test with the same arithmetic and passes
whatever that arithmetic does - the sessions below are constructed so the
answer can be worked out by hand from the fixture and written down. A grouping
that changed would have to change these constants to stay green, which is what
makes them a test rather than a restatement.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fermata import db, practice
from fermata.main import app

# Every date here is D(n): n days after an anchor 85 days back, so D(85) is
# today and every smaller offset is a day in the past.
#
# Relative and not literal, for the reasons test_practice_api.py gives: a
# session cannot be logged for a day that has not happened, and it cannot be
# logged more than practice.MAX_BACKDATE_DAYS ago, so a fixed calendar date
# would eventually break one rule or the other. 85 days is the anchor because
# it leaves the whole of the default 90-day window in the past while keeping
# every date well inside the back-dating bound.
#
# The window arithmetic is then checkable by eye: a window of N days ending
# today starts at D(86 - N), so a 30-day window starts at D(56) and a 10-day
# one at D(76).
_TODAY = date.today()
_ANCHOR = _TODAY - timedelta(days=85)


def D(offset: int) -> str:
    return (_ANCHOR + timedelta(days=offset)).isoformat()


# The day the endpoint is asked to treat as today throughout. Passed explicitly
# rather than left to the server's UTC date so nothing here depends on the hour
# the suite runs at - and equal to it, because a `today` more than a day from
# the server's own is refused (see api._today).
TODAY = D(85)
DAYS = 90


@pytest.fixture
def client(app_env, monkeypatch):
    monkeypatch.setattr("fermata.main.scanner.start_scan", lambda: False)
    monkeypatch.setattr("fermata.main.init_db", lambda: None)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def score(client):
    conn = db.connect()
    cur = conn.execute(
        """INSERT INTO scores(title, path, file_type, hash, size, mtime)
           VALUES ('Study in C', 'Classical/Study in C.pdf', 'pdf', 'deadbeef', 1, 0.0)"""
    )
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def other_score(client):
    conn = db.connect()
    cur = conn.execute(
        """INSERT INTO scores(title, path, file_type, hash, size, mtime)
           VALUES ('Second Score', 'Patreon/SecondScore.pdf', 'pdf', 'cafebabe', 1, 0.0)"""
    )
    conn.commit()
    return cur.lastrowid


def log(client, score_id, *, day, seconds, **rest):
    """One session on a piece. `day` is the practice day; pass day=None to log
    a session that records none, which is the shape a row from before the
    column existed has."""
    body = {"seconds": seconds, "activity": "piece", "score_id": score_id, **rest}
    if day is not None:
        body["local_date"] = day
    res = client.post("/api/practice/sessions", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def progress(client, score_id, **params):
    query = {"days": DAYS, "today": TODAY, **params}
    res = client.get(
        f"/api/scores/{score_id}/practice/progress",
        params={k: v for k, v in query.items() if v is not None},
    )
    assert res.status_code == 200, res.text
    return res.json()


def day_entry(body, day):
    """One day out of the window's per-day list, which covers every day in the
    window including the empty ones."""
    found = [d for d in body["window"]["days"] if d["date"] == day]
    assert len(found) == 1, f"{day} appears {len(found)} times in the window"
    return found[0]


# ---------------------------------------------------------------------------
# The empty state: a piece nobody has played yet.
# ---------------------------------------------------------------------------


def test_a_piece_never_practised_says_so_rather_than_reporting_zeros(client, score):
    """Invariant: a new user sees a real empty state, not zeros pretending to
    be data. `practised` is the field that distinguishes "nobody has played
    this" from "a quiet three months", and the two render differently because
    they are different facts."""
    body = progress(client, score)

    assert body["practised"] is False
    assert body["all_time"] == {
        "sessions": 0,
        "seconds": 0,
        "minutes": 0,
        "first_practised": None,
        "last_practised": None,
        "sessions_inferred": 0,
    }
    assert body["window"]["seconds"] == 0
    assert body["window"]["days_practised"] == 0
    assert body["window"]["sessions"] == 0
    # Every day in the window is still listed, at zero - an absent day is one a
    # reader has to infer from a gap, and this list is what a strip is drawn
    # from whether or not anything is on it.
    assert len(body["window"]["days"]) == DAYS
    assert {d["seconds"] for d in body["window"]["days"]} == {0}
    assert body["sessions"] == []
    assert body["session_total"] == 0
    assert body["sessions_truncated"] is False
    assert body["goals"] == []
    assert body["modes"] == []


def test_a_piece_never_practised_has_no_tempo_progression_and_no_axis(client, score):
    body = progress(client, score)["tempo"]
    assert body["points"] == []
    assert body["count"] == 0
    assert body["sessions_without_tempo"] == 0
    # Null and not zero. Zero is a tempo nobody played at; null is the absence
    # of any number to bound an axis with, and a chart handed 0 would draw one.
    assert body["axis_low"] is None
    assert body["axis_high"] is None
    assert body["latest_target"] is None
    assert body["comparable"] is False


def test_one_session_at_a_tempo_is_not_a_progression(client, score):
    """Invariant: a score with one session shows no fake trend. The single
    point is reported - it is a fact somebody entered - and `comparable` says
    outright that there is nothing here to draw a line through."""
    log(client, score, day=D(3), seconds=600, tempo_bpm=90, target_tempo_bpm=120)
    tempo = progress(client, score)["tempo"]

    assert tempo["count"] == 1
    assert tempo["comparable"] is False
    assert tempo["points"][0]["tempo_bpm"] == 90
    assert tempo["points"][0]["reached_target"] is False


# ---------------------------------------------------------------------------
# The totals, over several days and against the right piece.
# ---------------------------------------------------------------------------


def test_totals_count_this_piece_across_days_and_ignore_every_other(
    client, score, other_score
):
    # 25m + 35m on one day, 40m on another, all on `score`.
    log(client, score, day=D(3), seconds=1500)
    log(client, score, day=D(3), seconds=2100)
    log(client, score, day=D(10), seconds=2400)
    # A different piece, on a day `score` was also worked, and an hour long -
    # so any query that forgot its score filter would be off by an obvious 3600.
    log(client, other_score, day=D(3), seconds=3600)
    # Practice that is not against a piece at all, on the same day.
    res = client.post(
        "/api/practice/sessions", json={"activity": "technique", "seconds": 1800, "local_date": D(3)}
    )
    assert res.status_code == 200, res.text

    body = progress(client, score)

    assert body["practised"] is True
    assert body["title"] == "Study in C"
    assert body["score_id"] == score
    assert body["all_time"] == {
        "sessions": 3,
        "seconds": 6000,
        "minutes": 100,
        "first_practised": D(3),
        "last_practised": D(10),
        "sessions_inferred": 0,
    }
    assert body["window"]["seconds"] == 6000
    assert body["window"]["sessions"] == 3
    assert body["window"]["days_practised"] == 2
    assert day_entry(body, D(3)) == {"date": D(3), "seconds": 3600, "sessions": 2, "inferred": 0}
    assert day_entry(body, D(10)) == {"date": D(10), "seconds": 2400, "sessions": 1, "inferred": 0}
    assert day_entry(body, D(4)) == {"date": D(4), "seconds": 0, "sessions": 0, "inferred": 0}
    assert body["session_total"] == 3
    assert [s["seconds"] for s in body["sessions"]] == [2400, 2100, 1500]


def test_minutes_are_floored_and_never_claim_a_minute_that_was_not_practised(client, score):
    log(client, score, day=D(3), seconds=119)
    assert progress(client, score)["all_time"]["minutes"] == 1
    assert progress(client, score)["window"]["minutes"] == 1


def test_the_response_names_the_column_every_figure_was_grouped_by(client, score):
    """Timezone honesty: the aggregates group by the practice day, and the
    response says so rather than leaving a reader to assume a UTC timestamp."""
    assert progress(client, score)["grouped_by"] == "local_date"
    assert practice.GROUPED_BY == "local_date"


# ---------------------------------------------------------------------------
# The practice day, which is the practiser's own and not the server's.
# ---------------------------------------------------------------------------


def test_a_session_late_in_the_evening_groups_to_its_own_local_date(client, score):
    """A session recorded at 23:30 on D(5) local time is stored with
    started_at in UTC - which, east of Greenwich, is already D(6) - and it must
    be counted on D(5), the day the practice happened on.

    Written by forcing exactly that state: the row's stored local_date is D(5)
    and its started_at is D(6), which is the disagreement a real evening
    session produces and the one thing every day-based query here has to get
    right. Asserted in both directions, because a query that grouped by the
    timestamp would put the whole hour on D(6) and still look plausible.
    """
    session = log(client, score, day=D(5), seconds=1800)
    conn = db.connect()
    conn.execute(
        "UPDATE practice_sessions SET started_at = ? WHERE id = ?",
        (f"{D(6)} 03:30:00", session["id"]),
    )
    conn.commit()

    body = progress(client, score)
    assert day_entry(body, D(5)) == {"date": D(5), "seconds": 1800, "sessions": 1, "inferred": 0}
    assert day_entry(body, D(6)) == {"date": D(6), "seconds": 0, "sessions": 0, "inferred": 0}
    assert body["all_time"]["first_practised"] == D(5)
    assert body["all_time"]["last_practised"] == D(5)
    assert body["all_time"]["sessions_inferred"] == 0
    assert body["sessions"][0]["local_date"] == D(5)
    assert body["sessions"][0]["local_date_source"] == "recorded"


def test_a_session_with_no_recorded_day_is_counted_and_marked_as_assumed(client, score):
    """A row from before local_date existed is attributed to its UTC day - the
    only day it ever recorded - and both the piece's whole record and the
    window say how many of their sessions rest on one."""
    session = log(client, score, day=None, seconds=900)
    assert session["local_date_source"] == "utc_date"
    conn = db.connect()
    conn.execute(
        "UPDATE practice_sessions SET started_at = ? WHERE id = ?",
        (f"{D(8)} 10:00:00", session["id"]),
    )
    conn.commit()

    body = progress(client, score)
    assert body["all_time"]["sessions_inferred"] == 1
    assert body["all_time"]["first_practised"] == D(8)
    assert body["window"]["sessions_inferred"] == 1
    assert day_entry(body, D(8)) == {"date": D(8), "seconds": 900, "sessions": 1, "inferred": 1}


# ---------------------------------------------------------------------------
# The window, and what sits outside it.
# ---------------------------------------------------------------------------


def test_practice_before_the_window_stays_in_the_whole_record_and_out_of_the_window(
    client, score
):
    """"When did I last play this" has no window and "how much this stretch"
    has nothing else, so the two blocks must disagree here - and a view reading
    one off the other would report a piece worked on for months as untouched.

    A 30-day window ending at D(85) starts at D(56), so the D(3) session is
    outside it by 53 days and the D(70) one is inside it by 14.
    """
    log(client, score, day=D(3), seconds=1200)
    log(client, score, day=D(70), seconds=600)

    body = progress(client, score, days=30)
    assert body["start"] == D(56)
    assert body["end"] == TODAY
    assert body["all_time"]["sessions"] == 2
    assert body["all_time"]["seconds"] == 1800
    assert body["all_time"]["first_practised"] == D(3)
    assert body["practised"] is True
    assert body["window"]["sessions"] == 1
    assert body["window"]["seconds"] == 600
    assert body["session_total"] == 1


def test_a_piece_practised_only_outside_the_window_is_still_a_practised_piece(client, score):
    """`practised` is asked of the whole record. A piece worked solidly last
    spring and untouched since has been practised, and greeting it with the
    empty state would be wrong about the one thing this page exists to
    remember."""
    log(client, score, day=D(3), seconds=1200)
    body = progress(client, score, days=7)
    assert body["practised"] is True
    assert body["window"]["seconds"] == 0
    assert body["all_time"]["last_practised"] == D(3)


def test_the_window_boundaries_are_inclusive_on_both_ends(client, score):
    """A 10-day window ending at TODAY = D(85) starts at D(76). A session on
    each edge is in; one the day before the start is out."""
    log(client, score, day=D(75), seconds=100)
    log(client, score, day=D(76), seconds=200)
    log(client, score, day=D(85), seconds=400)

    body = progress(client, score, days=10)
    assert body["start"] == D(76)
    assert body["end"] == D(85)
    assert body["window"]["seconds"] == 600
    assert body["window"]["sessions"] == 2
    assert body["all_time"]["seconds"] == 700


# ---------------------------------------------------------------------------
# Tempo, as points and never as a line.
# ---------------------------------------------------------------------------


def test_tempo_points_come_back_oldest_first_with_their_own_days(client, score):
    log(client, score, day=D(20), seconds=600, tempo_bpm=100, target_tempo_bpm=120)
    log(client, score, day=D(4), seconds=600, tempo_bpm=80, target_tempo_bpm=120)
    log(client, score, day=D(12), seconds=600, tempo_bpm=90)

    tempo = progress(client, score)["tempo"]
    assert [(p["date"], p["tempo_bpm"]) for p in tempo["points"]] == [
        (D(4), 80),
        (D(12), 90),
        (D(20), 100),
    ]
    assert [p["target_tempo_bpm"] for p in tempo["points"]] == [120, None, 120]
    # None where there is no target to compare against, which is not the same
    # as having failed to reach one.
    assert [p["reached_target"] for p in tempo["points"]] == [False, None, False]
    assert tempo["count"] == 3
    assert tempo["comparable"] is True


def test_the_tempo_axis_spans_both_the_tempos_and_the_targets(client, score):
    """A target line has to fit on the same axis as the points under it, so
    the bounds cover both numbers. 120 is the highest thing on the chart even
    though nobody has played it yet."""
    log(client, score, day=D(4), seconds=600, tempo_bpm=80, target_tempo_bpm=120)
    log(client, score, day=D(12), seconds=600, tempo_bpm=95, target_tempo_bpm=120)

    tempo = progress(client, score)["tempo"]
    assert tempo["axis_low"] == 80
    assert tempo["axis_high"] == 120


def test_reaching_the_target_is_derived_from_the_two_numbers_it_comes_from(client, score):
    log(client, score, day=D(4), seconds=600, tempo_bpm=120, target_tempo_bpm=120)
    log(client, score, day=D(5), seconds=600, tempo_bpm=130, target_tempo_bpm=120)

    tempo = progress(client, score)["tempo"]
    assert [p["reached_target"] for p in tempo["points"]] == [True, True]


def test_the_target_reported_is_the_latest_written_down_and_not_the_highest(client, score):
    """Somebody who decided 140 was too fast and set 110 is aiming at 110.
    Reporting the number they abandoned would be this view arguing with them -
    and it would be the "best" figure this feature does not have."""
    log(client, score, day=D(4), seconds=600, tempo_bpm=100, target_tempo_bpm=140)
    log(client, score, day=D(20), seconds=600, tempo_bpm=100, target_tempo_bpm=110)

    assert progress(client, score)["tempo"]["latest_target"] == 110


def test_sessions_with_no_tempo_are_counted_so_a_sparse_chart_is_not_a_sparse_month(
    client, score
):
    log(client, score, day=D(4), seconds=600, tempo_bpm=80)
    log(client, score, day=D(5), seconds=600)
    log(client, score, day=D(6), seconds=600)

    tempo = progress(client, score)["tempo"]
    assert tempo["count"] == 1
    assert tempo["sessions_without_tempo"] == 2


def test_tempo_points_outside_the_window_are_not_drawn_in_it(client, score):
    log(client, score, day=D(3), seconds=600, tempo_bpm=70)
    log(client, score, day=D(70), seconds=600, tempo_bpm=100)

    tempo = progress(client, score, days=30)["tempo"]
    assert tempo["count"] == 1
    assert tempo["comparable"] is False
    assert tempo["axis_low"] == 100
    assert tempo["axis_high"] == 100


# ---------------------------------------------------------------------------
# Section work against run-throughs, and the ratings.
# ---------------------------------------------------------------------------


def test_the_time_splits_between_section_work_run_throughs_and_the_unstated(client, score):
    log(client, score, day=D(4), seconds=600, mode="section")
    log(client, score, day=D(5), seconds=900, mode="section")
    log(client, score, day=D(6), seconds=1200, mode="run_through")
    log(client, score, day=D(7), seconds=300)

    modes = progress(client, score)["modes"]
    # Ordered by time spent, and a session that did not say which it was comes
    # back under a null mode rather than being dropped or guessed at.
    assert modes == [
        {"mode": "section", "seconds": 1500, "sessions": 2},
        {"mode": "run_through", "seconds": 1200, "sessions": 1},
        {"mode": None, "seconds": 300, "sessions": 1},
    ]


def test_ratings_are_counted_per_value_with_every_bucket_present(client, score):
    log(client, score, day=D(4), seconds=600, rating=4)
    log(client, score, day=D(5), seconds=600, rating=4)
    log(client, score, day=D(6), seconds=600, rating=2)
    log(client, score, day=D(7), seconds=600)

    ratings = progress(client, score)["ratings"]
    assert ratings["counts"] == [
        {"rating": 1, "sessions": 0},
        {"rating": 2, "sessions": 1},
        {"rating": 3, "sessions": 0},
        {"rating": 4, "sessions": 2},
        {"rating": 5, "sessions": 0},
    ]
    assert ratings["rated"] == 3
    assert ratings["unrated"] == 1


# ---------------------------------------------------------------------------
# Goals about this piece.
# ---------------------------------------------------------------------------


def test_a_goal_about_this_piece_arrives_with_its_intent_and_its_progress(client, score):
    """The #3-era intent, counted against this piece's own sessions. The goal
    covers D(0)-D(6); two of those days were practised, for 50 minutes."""
    made = client.post(
        f"/api/practice/goals?today={TODAY}",
        json={
            "period_start": D(0),
            "target_days": 3,
            "target_minutes": 40,
            "scope": "score",
            "score_id": score,
            "intent": "the awkward middle section",
        },
    )
    assert made.status_code == 200, made.text
    log(client, score, day=D(1), seconds=1200)
    log(client, score, day=D(2), seconds=1800)

    goals = progress(client, score)["goals"]
    assert len(goals) == 1
    assert goals[0]["intent"] == "the awkward middle section"
    assert goals[0]["target_days"] == 3
    assert goals[0]["progress"]["days_practised"] == 2
    assert goals[0]["progress"]["minutes"] == 50
    assert goals[0]["progress"]["met_days"] is False
    assert goals[0]["progress"]["met_minutes"] is True
    assert goals[0]["progress"]["met"] is False
    assert goals[0]["progress"]["status"] == "past"
    assert goals[0]["progress"]["countable"] is True


def test_a_goal_over_any_practice_is_not_a_goal_about_this_piece(client, score, other_score):
    """A whole-week intention shown on a page about one score reads as a target
    for that score alone. Only a goal actually scoped to this piece is listed -
    and a goal scoped to a different one is not."""
    everything = client.post(
        f"/api/practice/goals?today={TODAY}",
        json={"period_start": D(0), "target_days": 5, "scope": "all"},
    )
    assert everything.status_code == 200, everything.text
    elsewhere = client.post(
        f"/api/practice/goals?today={TODAY}",
        json={
            "period_start": D(14),
            "target_days": 2,
            "scope": "score",
            "score_id": other_score,
        },
    )
    assert elsewhere.status_code == 200, elsewhere.text

    assert progress(client, score)["goals"] == []
    assert [g["score_id"] for g in progress(client, other_score)["goals"]] == [other_score]


def test_goals_outside_the_window_are_not_listed(client, score):
    made = client.post(
        f"/api/practice/goals?today={TODAY}",
        json={"period_start": D(0), "target_days": 3, "scope": "score", "score_id": score},
    )
    assert made.status_code == 200, made.text
    # A 30-day window starts at D(56); the goal's period ends at D(6).
    assert progress(client, score, days=30)["goals"] == []
    assert len(progress(client, score, days=90)["goals"]) == 1


# ---------------------------------------------------------------------------
# A piece in the trash (#56): counted, marked, and not a way back in.
# ---------------------------------------------------------------------------


def test_a_deleted_piece_still_reports_every_hour_and_says_it_is_deleted(
    client, app_env, monkeypatch, tmp_path
):
    """Through the real delete endpoint, not by writing `deleted_at` by hand,
    because the claim is about the policy end to end: deleting a score moves
    the file and keeps the practice, so this view must still answer in full.

    Refusing to report it would be this application deciding a deletion erases
    practice, which is the one thing the whole feature is built not to do.
    """
    from fermata import api, config, scanner

    root = config.LIBRARY_DIR
    monkeypatch.setattr(api, "LIBRARY_DIR", root)
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root)
    (root / "Inbox").mkdir(parents=True, exist_ok=True)
    score_file = root / "Inbox" / "Study.pdf"
    score_file.write_bytes(b"a score file")
    stat = score_file.stat()
    conn = db.connect()
    score_id = conn.execute(
        """INSERT INTO scores(title, collection, path, file_type, hash, size, mtime)
           VALUES ('Study', 'Inbox', 'Inbox/Study.pdf', 'pdf', ?, ?, ?)""",
        (scanner.hash_file(score_file), stat.st_size, stat.st_mtime),
    ).lastrowid
    conn.commit()

    log(client, score_id, day=D(4), seconds=1500, tempo_bpm=90)
    log(client, score_id, day=D(5), seconds=2100)

    before = progress(client, score_id)
    assert before["deleted"] is False

    removed = client.delete(f"/api/scores/{score_id}")
    assert removed.status_code == 200, removed.text
    assert removed.json()["practice_sessions_kept"] == 2

    after = progress(client, score_id)
    assert after["deleted"] is True
    assert after["title"] == "Study"
    assert after["practised"] is True
    # Every figure unchanged. The hours were spent; only the way into the
    # library goes away, and that is the client's job to honour.
    assert after["all_time"] == before["all_time"]
    assert after["all_time"]["seconds"] == 3600
    assert after["window"]["seconds"] == 3600
    assert after["session_total"] == 2
    assert after["tempo"]["count"] == 1


# ---------------------------------------------------------------------------
# The list, and the boundaries of what may be asked for.
# ---------------------------------------------------------------------------


def test_the_session_list_says_when_it_stopped_short_of_the_total(client, score):
    """A list that stops at the limit and says nothing looks identical to a
    complete one, and a reader totalling it would report less practice than
    there was."""
    for offset, seconds in ((4, 600), (5, 900), (6, 1200)):
        log(client, score, day=D(offset), seconds=seconds)

    body = progress(client, score, limit=2)
    assert len(body["sessions"]) == 2
    assert body["session_total"] == 3
    assert body["sessions_truncated"] is True
    # Newest first, so a truncated list keeps the sessions somebody is most
    # likely to be looking for.
    assert [s["seconds"] for s in body["sessions"]] == [1200, 900]
    # The totals beside it are counted from every row, not from the two listed.
    assert body["window"]["seconds"] == 2700
    assert body["all_time"]["seconds"] == 2700


def test_the_notes_attached_to_a_session_come_back_with_it(client, score):
    log(
        client,
        score,
        day=D(4),
        seconds=600,
        note="left hand shape at bar 34 still collapsing",
        from_bar=30,
        to_bar=38,
        rating=3,
        mode="section",
    )
    session = progress(client, score)["sessions"][0]
    assert session["note"] == "left hand shape at bar 34 still collapsing"
    assert session["from_bar"] == 30
    assert session["to_bar"] == 38
    assert session["rating"] == 3
    assert session["mode"] == "section"


@pytest.mark.parametrize(
    "params",
    [
        {"days": 0},
        {"days": practice.MAX_HISTORY_DAYS + 1},
        {"limit": 0},
        {"limit": practice.MAX_SCORE_SESSION_LIMIT + 1},
    ],
)
def test_a_window_or_a_limit_outside_its_bounds_is_refused(client, score, params):
    res = client.get(
        f"/api/scores/{score}/practice/progress",
        params={"today": TODAY, **params},
    )
    assert res.status_code == 422, res.text


def test_a_today_outside_the_window_this_instance_could_hold_is_refused(client, score):
    res = client.get(
        f"/api/scores/{score}/practice/progress", params={"today": "2099-01-01"}
    )
    assert res.status_code == 422, res.text
    assert "today must be between" in res.json()["detail"]


def test_an_unknown_score_is_a_404(client):
    assert client.get("/api/scores/98765/practice/progress").status_code == 404


# ---------------------------------------------------------------------------
# What this endpoint must never grow.
# ---------------------------------------------------------------------------


def test_nothing_on_this_response_is_a_streak_a_best_or_an_average(client, score):
    """docs/practice-data.md lists all three under what is deliberately
    absent, and issue #3 asks for them by name in its own "deliberately not"
    list: missing a week because of a busy job is information, not a moral
    failure, and a best week is the mechanism by which a good month becomes the
    standard a bad month is measured against.

    A per-piece view is where each would be most tempting - a piece is put down
    and picked up again by design - so the guard lives here, on the field names
    themselves, rather than in a reviewer's memory. `axis_low` and `axis_high`
    are the chart's bounds and are deliberately not named for a record; if they
    ever are, this test is where that argument has to be had.
    """
    log(client, score, day=D(4), seconds=600, tempo_bpm=90, target_tempo_bpm=120, rating=4)
    log(client, score, day=D(5), seconds=900, tempo_bpm=100, target_tempo_bpm=120, rating=5)

    forbidden = ("streak", "best", "worst", "average", "mean", "consecutive", "improv", "trend")
    found = []

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, inner in value.items():
                lowered = key.lower()
                if any(word in lowered for word in forbidden):
                    found.append(f"{path}{key}")
                walk(inner, f"{path}{key}.")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}].")

    walk(progress(client, score))
    assert not found, f"field(s) this response must not carry: {sorted(found)}"
