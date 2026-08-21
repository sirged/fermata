"""The practice endpoints, over real HTTP against a real database.

Called through TestClient rather than as Python functions, because half of what
this change adds lives in the request layer - a query parameter that decides
which week is "this" one, an upsert that must replace rather than duplicate, a
patch whose explicit null has to mean "clear this" and not "I said nothing".
None of that is exercised by calling the handler with keyword arguments.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fermata import db
from fermata.main import app

# Every date here is an offset from one Monday, six weeks back.
#
# Relative and not literal, deliberately: a session cannot be logged for a day
# that has not happened, so a fixed calendar date would start failing the day
# the suite ran past it - and it cannot be logged more than
# practice.MAX_BACKDATE_DAYS ago either, so a date far enough in the past to be
# permanently safe from the first rule would eventually break the second. Six
# weeks back satisfies both on any day the suite is run.
#
# D(0) is a Monday, so under the default week start the week under test is
# D(0) through D(6) and the arithmetic below can be checked by eye.
_TODAY = date.today()
_WEEK_START = _TODAY - timedelta(days=_TODAY.weekday() + 42)


def D(offset: int) -> str:
    return (_WEEK_START + timedelta(days=offset)).isoformat()


MONDAY = D(0)
SUNDAY = D(6)


@pytest.fixture
def client(app_env, monkeypatch, tmp_path):
    """A client whose startup does not scan a library or serve a build."""
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
           VALUES ('To Zanarkand', 'Patreon/Zanarkand.pdf', 'pdf', 'cafebabe', 1, 0.0)"""
    )
    conn.commit()
    return cur.lastrowid


def log(client, *, day, seconds, score_id=None, **rest):
    body = {"seconds": seconds, "local_date": day, **rest}
    if score_id is not None:
        body["score_id"] = score_id
        body.setdefault("activity", "piece")
    res = client.post("/api/practice/sessions", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def set_goal(client, *, today=MONDAY, **body):
    res = client.post(f"/api/practice/goals?today={today}", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Logging a session
# ---------------------------------------------------------------------------


def test_the_timer_can_still_log_a_bare_session_against_a_score(client, score):
    """The one call the practice timer makes. It has to keep working with
    nothing but a length, because a player stopping a clock should not be
    standing in front of a form."""
    res = client.post(f"/api/scores/{score}/practice", json={"seconds": 900})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["practice_seconds"] == 900
    assert body["session_count"] == 1
    # The id, so detail can be added to what was just logged.
    assert body["session"]["id"]
    assert body["session"]["activity"] == "piece"
    assert body["session"]["local_date_source"] == "utc_date"


def test_the_timer_can_say_which_day_it_was(client, score):
    res = client.post(
        f"/api/scores/{score}/practice", json={"seconds": 900, "local_date": MONDAY}
    )
    session = res.json()["session"]
    assert session["local_date"] == MONDAY
    assert session["local_date_source"] == "recorded"


def test_rich_detail_survives_a_round_trip(client, score):
    """Every field this change added, written and read back. A store that
    accepted a field and dropped it would look identical from the write side."""
    detail = {
        "seconds": 1500,
        "local_date": MONDAY,
        "activity": "piece",
        "mode": "section",
        "from_bar": 17,
        "to_bar": 32,
        "from_page": 2,
        "to_page": 3,
        "tempo_bpm": 76,
        "target_tempo_bpm": 120,
        "rating": 3,
        "note": "left hand still behind",
    }
    logged = client.post(f"/api/scores/{score}/practice", json=detail).json()["session"]
    stored = client.get(f"/api/scores/{score}/practice").json()["sessions"][0]
    for field, value in detail.items():
        assert stored[field] == value, field
        assert logged[field] == value, field
    assert stored["reached_target"] is False


def test_practice_that_is_not_about_a_piece_needs_no_piece(client):
    session = log(client, day=MONDAY, seconds=600, activity="ear_training")
    assert session["score_id"] is None
    assert session["activity"] == "ear_training"
    listed = client.get("/api/practice/sessions").json()["sessions"]
    assert [s["activity"] for s in listed] == ["ear_training"]


def test_a_session_on_a_piece_that_names_no_piece_is_refused(client):
    res = client.post("/api/practice/sessions", json={"seconds": 600, "activity": "piece"})
    assert res.status_code == 422
    assert "score_id" in res.text


def test_a_session_naming_a_score_that_does_not_exist_is_a_404(client):
    res = client.post(
        "/api/practice/sessions",
        json={"seconds": 600, "activity": "piece", "score_id": 4321},
    )
    assert res.status_code == 404


def test_an_impossible_length_or_rating_is_refused_with_a_reason(client, score):
    for body, expected in (
        ({"seconds": 0}, "seconds"),
        ({"seconds": 90_000}, "seconds"),
        ({"seconds": 600, "rating": 9}, "rating"),
        ({"seconds": 600, "tempo_bpm": 3}, "tempo_bpm"),
        ({"seconds": 600, "to_bar": 32}, "from_bar"),
        ({"seconds": 600, "activity": "vibes"}, "activity"),
        ({"seconds": 600, "mode": "noodling"}, "mode"),
    ):
        res = client.post(f"/api/scores/{score}/practice", json=body)
        assert res.status_code == 422, (body, res.text)
        assert expected in res.text, (body, res.text)


# ---------------------------------------------------------------------------
# Adding detail afterwards, and taking it back
# ---------------------------------------------------------------------------


def test_detail_can_be_added_to_a_session_already_logged(client, score):
    """How the interface works: the timer stores the length the moment it
    stops, and how it went is written afterwards - so a stopped clock is never
    waiting on a form, and a session is never lost because the form was
    abandoned."""
    session = client.post(f"/api/scores/{score}/practice", json={"seconds": 900}).json()[
        "session"
    ]
    res = client.patch(
        f"/api/practice/sessions/{session['id']}",
        json={"rating": 4, "note": "much better", "mode": "run_through"},
    )
    assert res.status_code == 200, res.text
    patched = res.json()
    assert (patched["rating"], patched["note"], patched["mode"]) == (4, "much better", "run_through")
    assert patched["seconds"] == 900  # untouched by a patch that did not mention it


def test_an_explicit_null_clears_a_field_rather_than_being_ignored(client, score):
    """A rating entered by mistake has to be removable. The omit-nulls rule
    that suits a create would swallow the only way to say "no rating"."""
    session = client.post(
        f"/api/scores/{score}/practice", json={"seconds": 900, "rating": 2, "note": "oops"}
    ).json()["session"]
    patched = client.patch(
        f"/api/practice/sessions/{session['id']}", json={"rating": None}
    ).json()
    assert patched["rating"] is None
    assert patched["note"] == "oops"  # not mentioned, so not cleared


def test_a_patch_cannot_reach_a_state_a_fresh_log_would_be_refused(client, score):
    """The whole record is re-checked, not the fragment: to_bar on its own is
    meaningless, and validating only what changed would let a patch build a row
    the create path rejects."""
    session = client.post(f"/api/scores/{score}/practice", json={"seconds": 900}).json()[
        "session"
    ]
    res = client.patch(f"/api/practice/sessions/{session['id']}", json={"to_bar": 32})
    assert res.status_code == 422
    assert "from_bar" in res.text

    ok = client.patch(
        f"/api/practice/sessions/{session['id']}", json={"from_bar": 17, "to_bar": 32}
    )
    assert ok.status_code == 200
    reversed_range = client.patch(
        f"/api/practice/sessions/{session['id']}", json={"to_bar": 4}
    )
    assert reversed_range.status_code == 422


def test_a_session_can_be_deleted_and_stops_counting(client, score):
    """A timer left running by accident is otherwise permanent, and a record
    with an invented two-hour session in it is not honest either."""
    session = log(client, day=MONDAY, seconds=7200, score_id=score)
    assert client.delete(f"/api/practice/sessions/{session['id']}").status_code == 200
    assert client.get(f"/api/scores/{score}/practice").json()["practice_seconds"] == 0
    assert client.delete(f"/api/practice/sessions/{session['id']}").status_code == 404


def test_patching_or_deleting_a_session_that_is_not_there_is_a_404(client):
    assert client.patch("/api/practice/sessions/999", json={"rating": 3}).status_code == 404
    assert client.delete("/api/practice/sessions/999").status_code == 404


# ---------------------------------------------------------------------------
# Reading the record back
# ---------------------------------------------------------------------------


def test_sessions_can_be_read_across_every_piece_and_filtered(client, score, other_score):
    """What answers "what did I actually do this week" without a caller
    walking the library one score at a time."""
    log(client, day=D(0), seconds=600, score_id=score)
    log(client, day=D(2), seconds=1200, score_id=other_score)
    log(client, day=D(3), seconds=900, activity="fretboard")
    log(client, day=D(8), seconds=1800, score_id=score)

    everything = client.get("/api/practice/sessions").json()["sessions"]
    assert [s["local_date"] for s in everything] == [
        D(8), D(3), D(2), D(0),
    ]

    week = client.get(f"/api/practice/sessions?start={MONDAY}&end={SUNDAY}").json()["sessions"]
    assert [s["seconds"] for s in week] == [900, 1200, 600]

    one_piece = client.get(f"/api/practice/sessions?score_id={score}").json()["sessions"]
    assert [s["seconds"] for s in one_piece] == [1800, 600]
    assert {s["score_title"] for s in one_piece} == {"Study in C"}

    one_kind = client.get("/api/practice/sessions?activity=fretboard").json()["sessions"]
    assert [s["seconds"] for s in one_kind] == [900]


def test_history_says_where_the_time_went_over_a_long_window(client, score, other_score):
    """Three months, a day at a time and a piece at a time - the shape of
    question that previously had to be reassembled score by score."""
    log(client, day=D(-16), seconds=1800, score_id=score)
    log(client, day=D(2), seconds=3600, score_id=other_score)
    log(client, day=D(2), seconds=600, activity="ear_training")

    res = client.get(f"/api/practice/history?days=90&today={D(6)}")
    assert res.status_code == 200
    body = res.json()
    assert body["start"] == D(6 - 89) and body["end"] == D(6)
    assert len(body["days"]) == 90
    assert body["days_practised"] == 2
    assert body["seconds"] == 6000
    assert [(r["title"], r["seconds"]) for r in body["by_score"]] == [
        ("To Zanarkand", 3600),
        ("Study in C", 1800),
    ]
    assert [(r["activity"], r["seconds"]) for r in body["by_activity"]] == [
        ("piece", 5400),
        ("ear_training", 600),
    ]


def test_a_silly_window_is_refused_rather_than_served(client):
    assert client.get("/api/practice/history?days=0").status_code == 422
    assert client.get("/api/practice/history?days=5000").status_code == 422
    assert client.get("/api/practice/sessions?limit=0").status_code == 422
    assert client.get("/api/practice/sessions?activity=vibes").status_code == 422
    assert client.get("/api/practice/sessions?start=17-08-2026").status_code == 422
    assert client.get("/api/practice/review?weeks=0").status_code == 422
    assert client.get("/api/practice/review?weeks=99").status_code == 422
    assert client.get(f"/api/practice/goals/current?today=nonsense").status_code == 422


# ---------------------------------------------------------------------------
# Setting a goal
# ---------------------------------------------------------------------------


def test_a_goal_set_without_a_period_is_for_the_week_containing_today(client):
    goal = set_goal(client, today=D(2), target_days=4)
    assert (goal["period_start"], goal["period_end"]) == (MONDAY, SUNDAY)
    assert goal["target_days"] == 4
    assert goal["progress"]["status"] == "running"


def test_the_week_start_preference_decides_which_seven_days_that_is(client):
    """Half the world starts a week on Sunday, and a goal counted over the
    wrong seven days is counted against days its owner did not think were part
    of the week."""
    assert client.put("/api/settings", json={"week_starts_on": "sunday"}).status_code == 200
    goal = set_goal(client, today=D(2), target_days=4)
    assert (goal["period_start"], goal["period_end"]) == (D(-1), D(5))

    current = client.get(f"/api/practice/goals/current?today={D(2)}").json()
    assert current["week_starts_on"] == "sunday"
    assert current["week_start"] == D(-1)


def test_changing_the_week_start_does_not_move_a_goal_already_set(client):
    """A goal stores the two dates it was actually set for, so a preference
    changed afterwards cannot silently re-point it at a different week's
    practice."""
    goal = set_goal(client, today=D(2), target_days=4)
    client.put("/api/settings", json={"week_starts_on": "sunday"})
    again = client.get(f"/api/practice/goals/current?today={D(2)}").json()["goal"]
    assert again["id"] == goal["id"]
    assert (again["period_start"], again["period_end"]) == (MONDAY, SUNDAY)


def test_setting_a_goal_again_for_the_same_week_replaces_it(client):
    """Changing your mind about the week is the ordinary case. A period with
    two goals in it is a scorecard."""
    first = set_goal(client, target_days=6, target_minutes=600)
    second = set_goal(client, target_days=3, target_minutes=None)
    assert second["id"] == first["id"]
    assert second["target_days"] == 3
    assert second["target_minutes"] is None
    assert len(client.get("/api/practice/goals").json()["goals"]) == 1


def test_a_goal_with_no_target_at_all_is_refused(client):
    res = client.post(f"/api/practice/goals?today={MONDAY}", json={"intent": "practise more"})
    assert res.status_code == 422
    assert "target" in res.text


def test_a_goal_can_be_scoped_to_one_piece_and_names_it(client, score):
    goal = set_goal(client, target_days=3, scope="score", score_id=score)
    assert goal["scope"] == "score"
    assert goal["score_title"] == "Study in C"


def test_a_goal_scoped_to_a_score_that_does_not_exist_is_a_404(client):
    res = client.post(
        f"/api/practice/goals?today={MONDAY}",
        json={"target_days": 3, "scope": "score", "score_id": 4321},
    )
    assert res.status_code == 404


def test_no_goal_set_is_an_answer_and_not_an_error(client):
    """Having no goal is an ordinary state, and a perfectly good week can
    happen inside it."""
    res = client.get(f"/api/practice/goals/current?today={MONDAY}")
    assert res.status_code == 200
    body = res.json()
    assert body["goal"] is None
    assert (body["week_start"], body["week_end"]) == (MONDAY, SUNDAY)


def test_a_goal_can_be_deleted_without_touching_the_practice(client, score):
    goal = set_goal(client, target_days=3)
    log(client, day=MONDAY, seconds=1800, score_id=score)
    assert client.delete(f"/api/practice/goals/{goal['id']}").status_code == 200
    assert client.get(f"/api/practice/goals/current?today={MONDAY}").json()["goal"] is None
    assert client.get(f"/api/scores/{score}/practice").json()["practice_seconds"] == 1800
    assert client.delete(f"/api/practice/goals/{goal['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Standing where the week can still change
# ---------------------------------------------------------------------------


def test_progress_is_visible_while_the_period_is_still_running(client, score):
    log(client, day=D(0), seconds=1800, score_id=score)
    log(client, day=D(1), seconds=1800, score_id=score)
    goal = set_goal(client, today=D(2), target_days=4, target_minutes=180)

    progress = goal["progress"]
    assert progress["status"] == "running"
    assert progress["days_practised"] == 2
    assert progress["minutes"] == 60
    assert progress["days_left"] == 5
    assert progress["met"] is False


def test_a_target_can_be_changed_while_the_period_runs(client, score):
    """Seeing where you stand is only useful if the goal can still change the
    week rather than only judge it afterwards."""
    goal = set_goal(client, today=D(2), target_days=6)
    res = client.patch(
        f"/api/practice/goals/{goal['id']}?today={D(2)}", json={"target_days": 3}
    )
    assert res.status_code == 200
    assert res.json()["target_days"] == 3


def test_a_late_entered_session_still_lands_in_the_week_it_happened(client, score):
    """Nothing stores whether a goal was met, so an hour remembered on Friday
    counts towards Tuesday - which it would not if a verdict had been written
    down when the week looked different."""
    goal = set_goal(client, today=D(4), target_days=1)
    assert goal["progress"]["days_practised"] == 0
    log(client, day=D(1), seconds=3600, score_id=score)
    after = client.get(f"/api/practice/goals/current?today={D(4)}").json()["goal"]
    assert after["progress"]["days_practised"] == 1
    assert after["progress"]["met"] is True


def test_today_is_the_clients_own_date_not_the_servers(client, score):
    """Whether a period is still running must not be an accident of the hour
    the server happens to be in - west of Greenwich the UTC date is already
    tomorrow while somebody still has their evening."""
    set_goal(client, today=MONDAY, target_days=4)
    running = client.get(f"/api/practice/goals/current?today={SUNDAY}").json()["goal"]
    assert running["progress"]["status"] == "running"
    over = client.get(f"/api/practice/goals/current?today={D(7)}").json()["goal"]
    assert over is None


# ---------------------------------------------------------------------------
# The review
# ---------------------------------------------------------------------------


def test_the_review_states_what_happened_in_each_recent_week(client, score, other_score):
    log(client, day=D(0), seconds=1800, score_id=score)
    log(client, day=D(1), seconds=1800, score_id=other_score)
    log(client, day=D(-5), seconds=600, score_id=score)  # the week before
    set_goal(client, today=MONDAY, target_days=4, target_minutes=120)

    res = client.get(f"/api/practice/review?weeks=3&today={D(7)}")
    assert res.status_code == 200
    weeks = res.json()["weeks"]
    assert [w["period_start"] for w in weeks] == [D(7), D(0), D(-7)]

    this_week = weeks[1]
    assert this_week["status"] == "past"
    assert this_week["goal"]["progress"]["days_practised"] == 2
    assert this_week["goal"]["progress"]["met_days"] is False
    assert this_week["facts"]["seconds"] == 3600
    assert [r["title"] for r in this_week["by_score"]] == ["Study in C", "To Zanarkand"]


def test_a_week_with_no_goal_still_appears_with_its_facts(client, score):
    """A week nobody set a goal for is not a gap in the record, and leaving it
    out would make the review a list of judged weeks."""
    log(client, day=D(-5), seconds=1800, score_id=score)
    weeks = client.get(f"/api/practice/review?weeks=3&today={D(7)}").json()["weeks"]
    previous = next(w for w in weeks if w["period_start"] == D(-7))
    assert previous["goal"] is None
    assert previous["facts"]["days_practised"] == 1
    assert previous["facts"]["seconds"] == 1800


def test_a_week_with_nothing_in_it_reports_zeroes_and_not_an_absence(client):
    weeks = client.get(f"/api/practice/review?weeks=2&today={D(7)}").json()["weeks"]
    for week in weeks:
        assert week["facts"]["days_practised"] == 0
        assert len(week["facts"]["days"]) == 7
        assert week["facts"]["seconds"] == 0


def test_the_review_never_ranks_one_week_against_another(client, score):
    """Deliberate absence. A best week is the mechanism by which a good month
    becomes the standard a bad month is punished against, so no field here
    offers one and no field counts a run of anything."""
    for day in (D(-7), D(-6), D(-5), D(0)):
        log(client, day=day, seconds=1800, score_id=score)
    body = client.get(f"/api/practice/review?weeks=4&today={D(7)}").json()
    text = repr(body).lower()
    for word in ("best", "streak", "rank", "worst", "average", "personal"):
        assert word not in text, f"the review mentions {word!r}"


def test_a_reflection_is_stored_and_read_back(client, score):
    """The person's own account of their own week. The only question asked is
    whether the goal was realistic - which is useful for setting the next one
    and says nothing about them."""
    log(client, day=D(0), seconds=1800, score_id=score)
    goal = set_goal(client, today=MONDAY, target_days=4)
    res = client.patch(
        f"/api/practice/goals/{goal['id']}?today={D(7)}",
        json={"reflection": "away for three days", "realistic": "no"},
    )
    assert res.status_code == 200
    assert res.json()["reflection"] == "away for three days"
    assert res.json()["realistic"] == "no"

    reviewed = client.get(f"/api/practice/review?weeks=2&today={D(7)}").json()["weeks"]
    stored = next(w for w in reviewed if w["period_start"] == MONDAY)["goal"]
    assert (stored["reflection"], stored["realistic"]) == ("away for three days", "no")


def test_a_reflection_is_not_a_status(client):
    goal = set_goal(client, target_days=4)
    for bad in ("failed", "met", "maybe", "true"):
        res = client.patch(f"/api/practice/goals/{goal['id']}", json={"realistic": bad})
        assert res.status_code == 422, bad


def test_a_goal_whose_week_ended_is_left_exactly_where_it_was(client, score):
    """Nothing closes, archives, grades or deletes a goal when its period ends.
    The record of what somebody meant to do is what makes the next goal a
    better one."""
    log(client, day=D(0), seconds=1800, score_id=score)
    goal = set_goal(client, today=MONDAY, target_days=4)
    client.patch(
        f"/api/practice/goals/{goal['id']}?today=MONDAY".replace("MONDAY", MONDAY),
        json={"intent": "the awkward middle section"},
    )

    later = client.get(f"/api/practice/goals?today={D(28)}").json()["goals"]
    assert len(later) == 1
    assert later[0]["id"] == goal["id"]
    assert later[0]["intent"] == "the awkward middle section"
    assert later[0]["target_days"] == 4
    assert later[0]["progress"]["status"] == "past"
    assert later[0]["progress"]["days_practised"] == 1
    # and a new week's goal sits beside it rather than replacing it
    set_goal(client, today=D(28), target_days=2)
    assert len(client.get(f"/api/practice/goals?today={D(28)}").json()["goals"]) == 2


def test_nothing_in_a_goal_response_carries_a_verdict_on_the_person(client, score):
    """The vocabulary is checked, not just the numbers. This is the whole
    difference between accountable and shamed: what the tool says out loud."""
    log(client, day=D(0), seconds=600, score_id=score)
    goal = set_goal(client, today=D(7), target_days=5, target_minutes=300)
    text = repr(goal).lower()
    for word in ("fail", "missed", "behind", "should", "only", "streak", "shame"):
        assert word not in text, f"a goal response mentions {word!r}"


def test_a_goals_period_cannot_be_moved_onto_a_different_week(client, score):
    """period_start is the goal's identity, and moving it would silently
    re-point the goal at another week's practice. Setting a goal for the other
    week is what the create endpoint is for."""
    goal = set_goal(client, today=MONDAY, target_days=4)
    client.patch(
        f"/api/practice/goals/{goal['id']}",
        json={"period_start": D(-7), "period_end": D(-1)},
    )
    after = client.get(f"/api/practice/goals/current?today={MONDAY}").json()["goal"]
    assert (after["period_start"], after["period_end"]) == (MONDAY, SUNDAY)


# ---------------------------------------------------------------------------
# The rest of the app still sees practice the way it did
# ---------------------------------------------------------------------------


def test_the_library_still_reports_a_scores_practice_totals(client, score):
    log(client, day=MONDAY, seconds=1800, score_id=score)
    log(client, day=D(1), seconds=600, score_id=score)
    listed = client.get("/api/scores").json()
    assert [s["practice_seconds"] for s in listed] == [2400]
    assert listed[0]["last_practiced"]


def test_the_recently_practised_and_neglected_views_still_work(client, score, other_score):
    today = date.today().isoformat()
    log(client, day=today, seconds=1800, score_id=score)
    recent = client.get("/api/scores?practiced=recent").json()
    assert [s["id"] for s in recent] == [score]
    neglected = client.get("/api/scores?practiced=neglected").json()
    assert [s["id"] for s in neglected] == [other_score]


def test_the_weekly_summary_still_answers(client, score):
    client.post(f"/api/scores/{score}/practice", json={"seconds": 1800})
    summary = client.get("/api/practice/summary").json()
    assert summary["week_seconds"] == 1800
    assert [s["title"] for s in summary["top_scores"]] == ["Study in C"]


def test_deleting_a_score_takes_its_practice_and_its_goal_with_it(client, score):
    """Documented, and checked here so it stays deliberate: a score row goes
    only when its file has left the library, and a goal about a piece that is
    no longer there could not be reviewed against anything."""
    log(client, day=MONDAY, seconds=1800, score_id=score)
    set_goal(client, today=MONDAY, target_days=3, scope="score", score_id=score)
    conn = db.connect()
    conn.execute("DELETE FROM scores WHERE id = ?", (score,))
    conn.commit()
    assert client.get("/api/practice/sessions").json()["sessions"] == []
    assert client.get("/api/practice/goals").json()["goals"] == []


def test_practice_recorded_before_this_change_is_still_counted(client, score):
    """A row with no recorded practice day - which is every row an existing
    install has. It has to appear in a week's facts, attributed to its UTC day,
    and be marked as inferred rather than presented as recorded."""
    conn = db.connect()
    conn.execute(
        """INSERT INTO practice_sessions(owner, score_id, activity, started_at, seconds, note)
           VALUES (?, ?, 'piece', ? || ' 20:00:00', 1800, 'from before')""",
        (db.DEFAULT_OWNER, score, D(2)),
    )
    conn.commit()

    session = client.get("/api/practice/sessions").json()["sessions"][0]
    assert session["local_date"] == D(2)
    assert session["local_date_source"] == "utc_date"

    week = client.get(f"/api/practice/review?weeks=1&today={D(3)}").json()["weeks"][0]
    assert week["facts"]["days_practised"] == 1
    assert week["facts"]["seconds"] == 1800
