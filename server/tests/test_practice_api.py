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
           VALUES ('Third Score', 'Patreon/ThirdScore.pdf', 'pdf', 'cafebabe', 1, 0.0)"""
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
        ("Third Score", 3600),
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
    assert [r["title"] for r in this_week["by_score"]] == ["Study in C", "Third Score"]


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
# A preference must not re-slice history
# ---------------------------------------------------------------------------


def test_changing_the_week_start_does_not_change_what_a_past_week_reports(client, score):
    """The same history has to report the same result. A goal stores the dates
    it was set for, and the review has to read them - matching goals to slots
    on today's grid meant flipping this preference turned three days and not
    met into four days and met, on data nobody had touched."""
    for day in (MONDAY, D(1), D(2)):
        log(client, day=day, seconds=1800, score_id=score)
    log(client, day=D(-1), seconds=1800, score_id=score)  # the Sunday before
    goal = set_goal(client, today=MONDAY, target_days=4)

    def reported():
        weeks = client.get(f"/api/practice/review?weeks=4&today={D(9)}").json()["weeks"]
        hosting = [w for w in weeks if w["goal"] and w["goal"]["id"] == goal["id"]]
        assert len(hosting) == 1, "the goal is listed exactly once"
        return hosting[0]

    before = reported()
    assert before["goal"]["progress"]["days_practised"] == 3
    assert before["goal"]["progress"]["met"] is False
    assert (before["period_start"], before["period_end"]) == (MONDAY, SUNDAY)

    client.put("/api/settings", json={"week_starts_on": "sunday"})
    after = reported()
    assert after["goal"]["progress"]["days_practised"] == 3
    assert after["goal"]["progress"]["met"] is False
    assert (after["period_start"], after["period_end"]) == (MONDAY, SUNDAY)


def test_a_past_goal_is_never_reported_as_no_goal_at_all(client, score):
    """The specific false statement. A week carrying somebody's own intent and
    reflection rendered as "no goal was set for this week" after the preference
    changed - a lie about their own record, from the one feature whose premise
    is that its statements are true."""
    log(client, day=MONDAY, seconds=1800, score_id=score)
    goal = set_goal(client, today=MONDAY, target_days=4, intent="the awkward bars")
    client.patch(
        f"/api/practice/goals/{goal['id']}?today={D(9)}",
        json={"reflection": "away for three days", "realistic": "no"},
    )
    client.put("/api/settings", json={"week_starts_on": "sunday"})

    weeks = client.get(f"/api/practice/review?weeks=4&today={D(9)}").json()["weeks"]
    found = [w["goal"] for w in weeks if w["goal"]]
    assert len(found) == 1
    assert found[0]["intent"] == "the awkward bars"
    assert found[0]["reflection"] == "away for three days"


def test_every_day_in_the_window_is_still_reported_after_a_flip(client, score):
    """Dropping a canonical week because a goal overlapped it would take real
    practice out of the review. Each day of the window appears in at least one
    listed period."""
    client.put("/api/settings", json={"week_starts_on": "monday"})
    set_goal(client, today=MONDAY, target_days=4)
    client.put("/api/settings", json={"week_starts_on": "sunday"})

    weeks = client.get(f"/api/practice/review?weeks=4&today={D(9)}").json()["weeks"]
    covered = set()
    for w in weeks:
        for day in w["facts"]["days"]:
            covered.add(day["date"])
    for offset in range(-14, 10):
        assert D(offset) in covered, f"{D(offset)} is in no listed period"


def test_two_goals_cannot_share_a_day(client):
    """Overlapping goals are how the same practice gets counted against two
    intentions, and how two panels of one page come to disagree about which
    goal this week has. Reachable through the ordinary interface the moment the
    preference changes, so it is refused rather than documented."""
    set_goal(client, today=MONDAY, target_days=4)
    res = client.post(
        f"/api/practice/goals?today={MONDAY}",
        json={"period_start": D(-1), "target_days": 2},  # Sunday-start, overlaps by six days
    )
    assert res.status_code == 409
    assert MONDAY in res.text and SUNDAY in res.text
    assert len(client.get(f"/api/practice/goals?today={MONDAY}").json()["goals"]) == 1

    # A period that merely abuts is fine.
    ok = client.post(
        f"/api/practice/goals?today={MONDAY}", json={"period_start": D(7), "target_days": 2}
    )
    assert ok.status_code == 200, ok.text


def test_replacing_a_goal_clears_the_reflection_written_about_the_old_one(client, score):
    """The review asks whether a goal was realistic. Answering with words
    written about a different intention is worse than not asking."""
    goal = set_goal(client, today=MONDAY, target_days=4)
    client.patch(
        f"/api/practice/goals/{goal['id']}?today={D(9)}",
        json={"reflection": "too much on", "realistic": "no"},
    )
    replaced = set_goal(client, today=MONDAY, target_days=2)
    assert replaced["id"] == goal["id"]
    assert replaced["reflection"] is None
    assert replaced["realistic"] is None


# ---------------------------------------------------------------------------
# Dates a client can send
# ---------------------------------------------------------------------------


def test_a_date_at_the_edge_of_the_calendar_is_refused_not_a_crash(client):
    """Every one of these does arithmetic on the date it is given - a review
    reaches 52 weeks back, a history window 366 days - and date's own bounds
    raise OverflowError from inside a subtraction, which reaches a client as a
    500 for what is only ever a typo."""
    for url in (
        "/api/practice/goals/current?today=0001-01-01",
        "/api/practice/review?weeks=52&today=0001-01-01",
        "/api/practice/history?days=366&today=0001-01-01",
        "/api/practice/goals/current?today=9999-12-31",
        "/api/practice/review?weeks=52&today=9999-12-31",
        "/api/practice/history?days=366&today=9999-12-31",
    ):
        assert client.get(url).status_code == 422, url
    for body in ({"period_start": "0001-01-01", "target_days": 2},
                 {"period_start": "9999-12-31", "target_days": 2}):
        assert client.post(f"/api/practice/goals?today={MONDAY}", json=body).status_code == 422


def test_a_today_nowhere_near_the_servers_date_is_refused(client):
    """A perfectly well-formed date that is nonetheless not a day this instance
    could hold practice for. Unbounded, `today=2099-01-01` was answered with a
    plausible-looking empty week - the worst kind of wrong answer, because
    nothing in the response marks it as suspect."""
    for far in ("2099-01-01", "1975-06-01"):
        for url in (
            f"/api/practice/goals/current?today={far}",
            f"/api/practice/review?weeks=4&today={far}",
            f"/api/practice/history?days=30&today={far}",
            f"/api/practice/goals?today={far}",
        ):
            res = client.get(url)
            assert res.status_code == 422, (url, res.text)
        assert (
            client.post(f"/api/practice/goals?today={far}", json={"target_days": 2}).status_code
            == 422
        )
    # A date the practiser's own timezone can actually produce is accepted, and
    # so is one a week ago - reasoning about a period that has ended is what
    # this parameter is for.
    for near in (MONDAY, D(7), date.today().isoformat()):
        assert client.get(f"/api/practice/goals/current?today={near}").status_code == 200


def test_a_boolean_is_not_a_number_of_days(client):
    """Pydantic's default mode coerces before any validator runs, so `true`
    arrived as 1 and set a one-day goal past a guard written to reject bools."""
    res = client.post(f"/api/practice/goals?today={MONDAY}", json={"target_days": True})
    assert res.status_code == 422
    assert client.post(
        f"/api/practice/sessions", json={"seconds": True, "activity": "free"}
    ).status_code == 422


def test_a_session_can_still_be_corrected_when_it_is_old(client, score):
    """How far back a NEW practice day may be is a rule about what somebody may
    claim now. Applied to a date already stored it made every session
    permanently uneditable once it was old enough."""
    session = log(client, day=MONDAY, seconds=600, score_id=score)
    ancient = (date.today() - timedelta(days=1000)).isoformat()
    conn = db.connect()
    conn.execute(
        "UPDATE practice_sessions SET local_date = ? WHERE id = ?", (ancient, session["id"])
    )
    conn.commit()

    res = client.patch(f"/api/practice/sessions/{session['id']}", json={"rating": 5})
    assert res.status_code == 200, res.text
    assert res.json()["rating"] == 5
    assert res.json()["local_date"] == ancient
    # Moving the date itself is still bounded, because that IS a new claim.
    moved = client.patch(
        f"/api/practice/sessions/{session['id']}", json={"local_date": ancient}
    )
    assert moved.status_code == 422
    assert "days ago" in moved.text


# ---------------------------------------------------------------------------
# Saying what was left out
# ---------------------------------------------------------------------------


def test_a_truncated_session_list_says_how_many_there_were(client, score):
    for n in range(5):
        log(client, day=D(n), seconds=600, score_id=score)
    full = client.get("/api/practice/sessions").json()
    assert (full["total"], full["truncated"]) == (5, False)
    capped = client.get("/api/practice/sessions?limit=2").json()
    assert len(capped["sessions"]) == 2
    assert (capped["total"], capped["truncated"]) == (5, True)


def test_a_truncated_by_piece_breakdown_says_how_many_pieces_there_were(client):
    conn = db.connect()
    for n in range(4):
        conn.execute(
            """INSERT INTO scores(title, path, file_type, hash, size, mtime)
               VALUES (?, ?, 'pdf', 'deadbeef', 1, 0.0)""",
            (f"Piece {n}", f"a/{n}.pdf"),
        )
    conn.commit()
    ids = [r["id"] for r in conn.execute("SELECT id FROM scores")]
    for score_id in ids:
        log(client, day=MONDAY, seconds=600, score_id=score_id)

    history = client.get(f"/api/practice/history?days=30&today={D(6)}").json()
    assert history["scores_worked"] == len(ids)
    assert history["by_score_truncated"] is False


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


def test_the_library_views_go_by_the_practice_day_not_the_timestamp(client, score, other_score):
    """A session entered late says which day it happened on, and the library
    has to believe it. Windowing on the UTC timestamp instead would call a
    piece last touched two months ago "recently practised" because the row was
    typed in this morning - and the library is the view a person sees first, so
    it must not disagree with the practice page about when they last played
    something."""
    long_ago = (date.today() - timedelta(days=60)).isoformat()
    log(client, day=long_ago, seconds=1800, score_id=score)

    assert client.get("/api/scores?practiced=recent").json() == []
    neglected = {s["id"] for s in client.get("/api/scores?practiced=neglected").json()}
    assert neglected == {score, other_score}
    # And the day it reports is that day, not today.
    listed = {s["id"]: s for s in client.get("/api/scores").json()}
    assert listed[score]["last_practiced"] == long_ago


def test_an_orphaned_session_does_not_empty_the_neglected_view(client, score, other_score):
    """SQL's NOT IN against a set containing NULL is never true. Now that a
    session can have no score, one orphaned row anywhere used to be enough to
    make "needs attention" list nothing at all - including scores that have
    never been practised, which is exactly what that view is for."""
    log(client, day=MONDAY, seconds=600, score_id=score)
    _delete_score(score)
    neglected = client.get("/api/scores?practiced=neglected").json()
    assert [s["id"] for s in neglected] == [other_score]


def test_every_practice_query_counts_only_this_owner(client, score):
    """Dead today - there is one owner - and checked anyway, because the day
    accounts arrive the failure is somebody else's practice appearing in this
    person's totals, and the schema's comments ask these sites to stay
    consistent and greppable rather than correct by accident. /practice/summary
    was the one that had no owner filter at all.

    The score-scoped totals are deliberately NOT filtered: a score has no owner
    column, so "this owner's practice on this score" is not a question the
    schema can answer yet, and pretending otherwise would be a filter that
    looks like a guarantee and is not.
    """
    today = date.today().isoformat()
    log(client, day=today, seconds=600, score_id=score)
    conn = db.connect()
    conn.execute(
        """INSERT INTO practice_sessions(owner, score_id, activity, started_at, local_date, seconds)
           VALUES ('someone_else', ?, 'piece', datetime('now'), ?, 9999)""",
        (score, today),
    )
    conn.execute(
        """INSERT INTO practice_goals(owner, period_start, period_end, target_days, scope)
           VALUES ('someone_else', ?, ?, 7, 'all')""",
        (MONDAY, SUNDAY),
    )
    conn.commit()

    assert client.get("/api/practice/summary").json()["week_seconds"] == 600
    history = client.get(f"/api/practice/history?days=30&today={today}").json()
    assert history["seconds"] == 600
    assert client.get("/api/practice/sessions").json()["total"] == 1
    assert client.get(f"/api/practice/goals?today={today}").json()["goals"] == []
    week = client.get(f"/api/practice/review?weeks=1&today={today}").json()["weeks"][0]
    assert week["facts"]["seconds"] == 600
    assert week["goal"] is None


def test_the_weekly_summary_counts_practice_days(client, score):
    """Seven calendar days, so this and the practice page cannot disagree about
    what "this week" held. A session back-dated well outside the window does
    not count towards it however recently it was typed in."""
    today = date.today().isoformat()
    log(client, day=today, seconds=1800, score_id=score)
    log(client, day=(date.today() - timedelta(days=30)).isoformat(), seconds=3600, score_id=score)
    summary = client.get("/api/practice/summary").json()
    assert summary["week_seconds"] == 1800
    assert summary["week_sessions"] == 1
    assert [(s["title"], s["practice_seconds"]) for s in summary["top_scores"]] == [
        ("Study in C", 1800)
    ]


def test_the_weekly_summary_still_answers(client, score):
    client.post(f"/api/scores/{score}/practice", json={"seconds": 1800})
    summary = client.get("/api/practice/summary").json()
    assert summary["week_seconds"] == 1800
    assert [s["title"] for s in summary["top_scores"]] == ["Study in C"]


def _delete_score(score_id: int) -> None:
    """Remove a score row the way the scanner does when its file has gone.

    There is no delete endpoint yet (#56), and going through the database is
    the honest stand-in: what matters is what the foreign key does, not which
    code path asked.
    """
    conn = db.connect()
    conn.execute("DELETE FROM scores WHERE id = ?", (score_id,))
    conn.commit()


def test_deleting_a_score_keeps_the_practice(client, score, other_score):
    """The hours were spent whether or not the file is still on disk, and the
    record of them is a property of the person's history rather than of the
    file. Every session survives, naming no piece."""
    log(client, day=MONDAY, seconds=1800, score_id=score, rating=4, note="better today")
    log(client, day=D(1), seconds=600, score_id=other_score)
    _delete_score(score)

    sessions = client.get("/api/practice/sessions").json()["sessions"]
    assert len(sessions) == 2
    orphan = next(s for s in sessions if s["score_id"] is None)
    assert orphan["seconds"] == 1800
    assert orphan["rating"] == 4
    assert orphan["note"] == "better today"
    assert orphan["local_date"] == MONDAY
    # Named as what it is, not left as a blank where a title goes.
    assert orphan["activity"] == "piece"
    assert orphan["score_missing"] is True
    # The other piece is untouched, so the delete took only what it should.
    assert next(s for s in sessions if s["score_id"] == other_score)["score_missing"] is False


def test_a_goal_already_reached_stays_reached_when_a_score_is_deleted(client, score):
    """The failure this has to rule out. A week's goal counted over any
    practice must not become unmet because a file was tidied away afterwards."""
    for day in (MONDAY, D(1), D(2)):
        log(client, day=day, seconds=1800, score_id=score)
    goal = set_goal(client, today=D(7), period_start=MONDAY, target_days=3, target_minutes=60)
    assert goal["progress"]["met"] is True

    _delete_score(score)
    after = client.get(f"/api/practice/goals?today={D(7)}").json()["goals"][0]
    assert after["progress"]["met"] is True
    assert after["progress"]["days_practised"] == 3
    assert after["progress"]["minutes"] == 90
    assert after["progress"]["countable"] is True


def test_orphaned_practice_still_appears_everywhere_practice_is_counted(client, score):
    """Not filtered out anywhere. The history, the review and a week's facts
    all have to keep it, or "where did my time go" starts losing hours to
    library tidying."""
    log(client, day=MONDAY, seconds=1800, score_id=score)
    _delete_score(score)

    history = client.get(f"/api/practice/history?days=30&today={D(6)}").json()
    assert history["seconds"] == 1800
    assert history["days_practised"] == 1
    # by_score can no longer name it - there is no piece to name - so the time
    # is accounted for under the kind of work it was.
    assert history["by_score"] == []
    assert [(r["activity"], r["seconds"]) for r in history["by_activity"]] == [("piece", 1800)]

    week = client.get(f"/api/practice/review?weeks=1&today={D(6)}").json()["weeks"][0]
    assert week["facts"]["seconds"] == 1800
    assert week["facts"]["days_practised"] == 1


def test_a_goal_about_a_deleted_piece_says_it_cannot_be_counted(client, score):
    """Its sessions are still in the history but no longer identifiable as
    being about that piece. So it reports that, rather than reporting zero days
    - which would read as a week nobody practised."""
    log(client, day=MONDAY, seconds=1800, score_id=score)
    goal = set_goal(
        client, today=D(7), period_start=MONDAY, target_days=1, scope="score", score_id=score
    )
    assert goal["progress"]["met"] is True

    _delete_score(score)
    after = client.get(f"/api/practice/goals?today={D(7)}").json()["goals"][0]
    assert after["id"] == goal["id"]
    assert after["scope"] == "score"
    assert after["score_id"] is None
    assert after["progress"]["countable"] is False
    assert after["progress"]["met"] is None
    assert after["progress"]["met_days"] is None


def test_an_orphaned_session_can_still_be_annotated(client, score):
    """A true record has to stay editable. Refusing a note because the piece
    was deleted would turn a rule about making an honest claim into a rule
    about keeping one."""
    session = log(client, day=MONDAY, seconds=1800, score_id=score)
    _delete_score(score)
    res = client.patch(
        f"/api/practice/sessions/{session['id']}", json={"note": "the one I gave up on"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["note"] == "the one I gave up on"
    assert res.json()["score_missing"] is True


def test_a_goal_about_a_deleted_piece_can_still_be_reflected_on(client, score):
    log(client, day=MONDAY, seconds=1800, score_id=score)
    goal = set_goal(client, today=MONDAY, target_days=3, scope="score", score_id=score)
    _delete_score(score)
    res = client.patch(
        f"/api/practice/goals/{goal['id']}?today={D(7)}",
        json={"reflection": "gave that piece up", "realistic": "no"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["reflection"] == "gave that piece up"


def test_a_session_on_a_piece_still_cannot_be_created_without_one(client, score):
    """The allowance is granted from the STORED row, so it cannot be used to
    reach that state deliberately: creating a piece session with no piece is
    still refused, and which piece a session is against is not patchable at
    all - a session cannot be detached from its score, only outlive it."""
    assert (
        client.post("/api/practice/sessions", json={"seconds": 600, "activity": "piece"}).status_code
        == 422
    )
    session = log(client, day=MONDAY, seconds=600, score_id=score)
    res = client.patch(f"/api/practice/sessions/{session['id']}", json={"score_id": None})
    assert res.status_code == 200, res.text
    assert res.json()["score_id"] == score
    assert res.json()["score_missing"] is False


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
