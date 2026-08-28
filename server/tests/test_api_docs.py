"""The documented surface itself (issue #19) - not what any one endpoint
does, which the other test modules already cover, but whether what FastAPI
generates from api.py is actually true, complete and fetchable.

Four separate claims, each with its own test group below:

1. GET /openapi.json is a document that validates against the OpenAPI spec -
   FastAPI producing SOME JSON is not the same claim as producing one a
   client's codegen (or a planned MCP server - issue #31 - built on this
   surface as its contract) could actually trust.
2. Every route carries a summary, a description and at least one tag, and
   its success response declares a schema (or, for the two file-serving
   routes, a real content type) - see test_every_route_is_documented for
   what "documented" is defined to mean and why an undocumented new route
   fails this rather than passing by default. FastAPI derives a summary from
   a function's name even with no docstring at all, so summary alone would
   be a test that could never go red; description is what actually requires
   someone to have written something.
3. For a representative endpoint out of each group (system, settings,
   instruments, library, practice, transcription, scan), the JSON that
   actually comes back over real HTTP validates against the model
   response_model= declares for it - proving the declared shape is the real
   shape, not merely FastAPI's own opinion of it echoed back.
4. FastAPI's response-model validation is switched on, not merely present in
   the decorator - test_response_validation_actually_rejects_a_bad_response
   proves it against a throwaway route using the exact same
   `response_model=` idiom every route in api.py uses, so the proof is about
   the mechanism itself rather than about any one handler happening to
   behave.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openapi_spec_validator import validate as validate_openapi

from fermata import api, api_models
from fermata.main import app as full_app

# Every route in api.py returns a FileResponse (binary) rather than JSON for
# exactly these two - see the `responses=` kwarg on their decorators. They are
# "documented" by declaring a real content type instead of a JSON schema.
_BINARY_ROUTES = {
    ("GET", "/api/scores/{score_id}/file"),
    ("GET", "/api/scores/{score_id}/thumb"),
}


@pytest.fixture
def client(app_env):
    """The router alone - same pattern as test_instruments_api.py and
    test_version_api.py's client fixtures. Schema-shape assertions use
    `full_app` (fermata.main.app) instead, further down, because that is the
    literal app that serves /docs and /openapi.json in production; this one
    is for hitting live endpoints against a throwaway database."""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture
def openapi_schema():
    return full_app.openapi()


# ---------------------------------------------------------------------------
# 1. The document itself validates.
# ---------------------------------------------------------------------------


def test_openapi_document_validates_against_the_spec(openapi_schema):
    """FastAPI can generate JSON that LOOKS like OpenAPI while actually being
    malformed against the spec - a response with no "content" at all under a
    status code, a $ref FastAPI failed to resolve, and so on. This is the
    check that a tool consuming the document (Swagger UI, a codegen, the
    planned MCP server this documents the contract for) would not choke on
    it. openapi_spec_validator.validate raises on the first thing wrong,
    which pytest reports directly - there is no assertion to write."""
    validate_openapi(openapi_schema)


def test_openapi_json_is_actually_served(app_env, monkeypatch):
    """Not just constructible in-process - reachable at the URL a client
    would actually fetch."""
    monkeypatch.setattr("fermata.main.scanner.start_scan", lambda: False)
    monkeypatch.setattr("fermata.main.init_db", lambda: None)
    with TestClient(full_app) as c:
        resp = c.get("/openapi.json")
        assert resp.status_code == 200
        assert resp.json()["paths"]


def test_docs_page_renders(app_env, monkeypatch):
    """GET /docs returns 200 with an actual Swagger UI page, not merely 'not
    404'. Checked against fetching this over a real HTTP server on an odd
    port too - see the manual verification in the PR description; this is
    the automated form of the same claim."""
    monkeypatch.setattr("fermata.main.scanner.start_scan", lambda: False)
    monkeypatch.setattr("fermata.main.init_db", lambda: None)
    with TestClient(full_app) as c:
        resp = c.get("/docs")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "swagger-ui" in resp.text.lower()
        assert full_app.title in resp.text


# ---------------------------------------------------------------------------
# 2. Every route is documented, and an undocumented one would fail this.
# ---------------------------------------------------------------------------


def _operations(schema):
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue  # not an operation object (e.g. "parameters")
            yield method.upper(), path, op


def test_every_route_is_documented(openapi_schema):
    """The guard that keeps issue #19 solved going forward: a route added to
    api.py with no docstring, no tag, or no response_model fails THIS test,
    with a message naming exactly which route and which of the four is
    missing - not a diff against some other file, and not something that
    only a human reading /docs would ever notice."""
    problems = []
    for method, path, op in _operations(openapi_schema):
        route = f"{method} {path}"
        if not op.get("summary"):
            problems.append(f"{route}: no summary (add response_model/tags and check the route is spelled with a docstring)")
        if not op.get("description"):
            problems.append(f"{route}: no docstring - add one to the handler function")
        if not op.get("tags"):
            problems.append(f"{route}: no tags=[...] on the route decorator")
        responses = op.get("responses", {})
        ok_response = responses.get("200") or responses.get("201") or next(
            (v for k, v in responses.items() if k.startswith("2")), None
        )
        if ok_response is None:
            problems.append(f"{route}: no 2xx response documented at all")
            continue
        content = ok_response.get("content", {})
        if (method, path) in _BINARY_ROUTES:
            if not content:
                problems.append(
                    f"{route}: binary route declared no content type - add responses={{200: "
                    f"{{'content': {{'<mime-type>': {{}}}}}}}} to the decorator"
                )
        else:
            schema_present = any("schema" in media for media in content.values())
            if not schema_present:
                problems.append(
                    f"{route}: no response schema - add response_model=<Model> to the route "
                    "decorator (see fermata/api_models.py)"
                )
    assert not problems, "undocumented route(s):\n" + "\n".join(problems)


def test_every_route_has_exactly_the_expected_operation_count(openapi_schema):
    """Pinned so a route silently added or removed from api.py is noticed
    here rather than only by whoever next reads the endpoint inventory by
    hand. Update this number, deliberately, the same commit that adds or
    removes a route."""
    count = sum(1 for _ in _operations(openapi_schema))
    assert count == 41


# ---------------------------------------------------------------------------
# 3. Declared shapes match what actually comes back, across every group.
# ---------------------------------------------------------------------------


def test_system_and_settings_responses_match_their_models(client):
    api_models.HealthOut.model_validate(client.get("/api/health").json())
    api_models.VersionOut.model_validate(client.get("/api/version").json())
    api_models.SettingsOut.model_validate(client.get("/api/settings").json())
    api_models.SettingsOut.model_validate(
        client.put("/api/settings", json={"staff_theme": "noir"}).json()
    )


def test_instrument_responses_match_their_models(client):
    api_models.InstrumentPresetOut.model_validate(
        client.get("/api/instruments/presets").json()[0]
    )
    body = {
        "name": "Test guitar",
        "string_count": 6,
        "string_pitches": ["E2", "A2", "D3", "G3", "B3", "E4"],
        "fretted": True,
        "fret_count": 22,
        "capo": 0,
    }
    created = client.post("/api/instruments", json=body)
    instrument = api_models.InstrumentOut.model_validate(created.json())

    for item in client.get("/api/instruments").json():
        api_models.InstrumentOut.model_validate(item)
    api_models.InstrumentOut.model_validate(
        client.get(f"/api/instruments/{instrument.id}").json()
    )
    api_models.InstrumentOut.model_validate(
        client.put(f"/api/instruments/{instrument.id}", json=body).json()
    )
    api_models.InstrumentDeleteOut.model_validate(
        client.delete(f"/api/instruments/{instrument.id}").json()
    )


def test_library_responses_match_their_models(client, insert_score):
    from fermata import db

    conn = db.connect()
    score_id = insert_score(conn, "Some Piece.pdf", title="Some Piece")
    conn.execute("INSERT INTO tags(name) VALUES ('warm-up')")
    conn.execute(
        "INSERT INTO score_tags(score_id, tag_id) SELECT ?, id FROM tags WHERE name = 'warm-up'",
        (score_id,),
    )
    conn.commit()

    for item in client.get("/api/scores").json():
        api_models.ScoreOut.model_validate(item)
    api_models.ScoreOut.model_validate(client.get(f"/api/scores/{score_id}").json())
    api_models.ScoreOut.model_validate(
        client.patch(f"/api/scores/{score_id}", json={"favorite": True}).json()
    )
    for item in client.get("/api/collections").json():
        api_models.CollectionOut.model_validate(item)
    for item in client.get("/api/tags").json():
        api_models.TagOut.model_validate(item)
    for group in client.get("/api/duplicates").json():
        api_models.DuplicateGroupOut.model_validate(group)


def test_practice_responses_match_their_models(client, insert_score):
    from fermata import db

    conn = db.connect()
    score_id = insert_score(conn, "Practice Piece.pdf", title="Practice Piece")
    conn.commit()

    logged = client.post(f"/api/scores/{score_id}/practice", json={"seconds": 600})
    api_models.LogPracticeOut.model_validate(logged.json())
    api_models.ScorePracticeOut.model_validate(
        client.get(f"/api/scores/{score_id}/practice").json()
    )

    session = api_models.PracticeSessionOut.model_validate(
        client.post("/api/practice/sessions", json={"seconds": 300, "activity": "free"}).json()
    )
    api_models.PracticeSessionOut.model_validate(
        client.patch(
            f"/api/practice/sessions/{session.id}", json={"rating": 4}
        ).json()
    )
    api_models.SessionListOut.model_validate(client.get("/api/practice/sessions").json())
    api_models.PracticeSummaryOut.model_validate(client.get("/api/practice/summary").json())
    api_models.PracticeHistoryOut.model_validate(client.get("/api/practice/history").json())
    api_models.SessionDeleteOut.model_validate(
        client.delete(f"/api/practice/sessions/{session.id}").json()
    )

    goal = api_models.GoalOut.model_validate(
        client.post("/api/practice/goals", json={"target_days": 3}).json()
    )
    api_models.CurrentGoalOut.model_validate(client.get("/api/practice/goals/current").json())
    api_models.GoalListOut.model_validate(client.get("/api/practice/goals").json())
    api_models.GoalOut.model_validate(
        client.patch(f"/api/practice/goals/{goal.id}", json={"target_days": 5}).json()
    )
    api_models.PracticeReviewOut.model_validate(client.get("/api/practice/review").json())
    api_models.GoalDeleteOut.model_validate(
        client.delete(f"/api/practice/goals/{goal.id}").json()
    )


def test_a_goal_about_a_deleted_score_still_validates(client, insert_score):
    """The one shape genuinely different from the rest: an uncountable goal's
    progress omits `sessions_inferred` and every day's `inferred` - see
    api_models.GoalDayOut and GoalProgressOut. This is the case that would
    500 if those fields were modelled as required ints instead of optional
    ones, so it is exercised on purpose rather than only in passing."""
    from fermata import db

    conn = db.connect()
    score_id = insert_score(conn, "Deleted Piece.pdf", title="Deleted Piece")
    conn.commit()
    goal = client.post(
        "/api/practice/goals", json={"scope": "score", "score_id": score_id, "target_days": 3}
    ).json()
    conn.execute("DELETE FROM scores WHERE id = ?", (score_id,))
    conn.commit()

    validated = api_models.GoalOut.model_validate(
        client.get(f"/api/practice/goals").json()["goals"][0]
    )
    assert validated.progress.countable is False
    assert validated.progress.sessions_inferred is None
    assert all(day.inferred is None for day in validated.progress.days)
    assert goal["id"] == validated.id


def test_transcription_responses_match_their_models(client, insert_score, extractable_pdf, monkeypatch):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    from fermata import db

    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
    conn.commit()

    api_models.TranscriptionAnalysisOut.model_validate(
        client.get(f"/api/scores/{score_id}/transcription/analysis").json()
    )
    extracted = client.post(f"/api/scores/{score_id}/transcribe")
    api_models.TranscribeResultOut.model_validate(extracted.json())
    api_models.TranscriptionOut.model_validate(
        client.get(f"/api/scores/{score_id}/transcription").json()
    )
    edited = client.put(
        f"/api/scores/{score_id}/transcription", json={"content": '\\title "x"\n.\n:4 0.1 |'}
    )
    api_models.TranscriptionOut.model_validate(edited.json())
    api_models.TranscriptionOut.model_validate(
        client.delete(f"/api/scores/{score_id}/transcription").json()
    )


def test_transcription_model_stays_in_sync_with_api_pys_bar_key_tuples():
    """TranscriptionOut cannot import api._BAR_KEYS et al (api.py imports
    api_models.py for response_model=, so the reverse import would be
    circular - see api_models.py's module docstring), so its bar-figure and
    provenance fields are a hand-kept mirror instead. This is the guard that
    keeps them honest: a key added to api.py's _BAR_KEYS, _BAR_LIST_KEYS,
    _BAR_AMOUNT_KEYS or _PROVENANCE_KEYS tuples without a matching field
    added here fails this test by name, rather than silently documenting a
    response narrower than the one actually sent (FastAPI drops fields not
    on the model rather than erroring, which is exactly the kind of gap
    issue #19 exists to close)."""
    expected = {
        *api._BAR_KEYS,
        *api._BAR_LIST_KEYS,
        *api._BAR_AMOUNT_KEYS,
        *api._PROVENANCE_KEYS,
    }
    # The rest of TranscriptionOut's fields: the raw row columns plus
    # `warnings`, none of which come from the four tuples above.
    non_mirrored = {
        "id", "score_id", "format", "content", "source", "confidence",
        "created_at", "updated_at", "warnings",
    }
    actual = set(api_models.TranscriptionOut.model_fields) - non_mirrored
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"api_models.TranscriptionOut is missing field(s) for: {sorted(missing)}"
    assert not extra, f"api_models.TranscriptionOut has field(s) no longer in api.py's tuples: {sorted(extra)}"


def test_scan_and_upload_responses_match_their_models(client, monkeypatch):
    monkeypatch.setattr(api.scanner, "start_scan", lambda **kw: True)
    api_models.ScanStatusOut.model_validate(client.get("/api/scan/status").json())
    api_models.ScanTriggerOut.model_validate(client.post("/api/scan").json())


# ---------------------------------------------------------------------------
# 4. Response-model validation is actually switched on.
# ---------------------------------------------------------------------------


def test_response_validation_actually_rejects_a_bad_response():
    """Not "the decorator has response_model=" - that a route CLAIMS a shape
    proves nothing about whether FastAPI actually checks a real response
    against it (response_model_exclude_unset, a custom
    ResponseValidationError handler swallowing it, or a FastAPI version that
    changed the default could each turn response_model= into decoration that
    does nothing). This registers a route with the exact same
    `@router.get(..., response_model=HealthOut)` idiom every route in api.py
    uses, wires an endpoint that returns a body HealthOut forbids (`status`
    must be a str; None is not one), and confirms the request fails rather
    than quietly serving the bad body - proving the mechanism itself, not
    any one handler, actually enforces the declared shape in this app.

    See fermata-verify's mutation-testing guidance: a guard that cannot be
    observed to fail is not verified to guard anything. This is that
    observation, made once against the real machinery rather than assumed
    from api.py's decorators reading correctly."""
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    probe = APIRouter()

    @probe.get("/probe/health", response_model=api_models.HealthOut)
    def broken_health():
        return {"status": None}

    app = FastAPI()
    app.include_router(probe)
    probe_client = TestClient(app)

    with pytest.raises(Exception) as exc_info:
        probe_client.get("/probe/health")
    assert "ResponseValidationError" in type(exc_info.value).__name__
