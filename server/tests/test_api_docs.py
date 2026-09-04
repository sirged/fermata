"""The documented surface itself (issue #19) - not what any one endpoint
does, which the other test modules already cover, but whether what FastAPI
generates from api.py is actually true, complete and fetchable.

Five separate claims, each with its own test group below:

1. GET /openapi.json is a document that validates against the OpenAPI spec -
   FastAPI producing SOME JSON is not the same claim as producing one a
   client's codegen (or the MCP server - issue #31 - which generates its
   whole tool list from this document) could actually trust.
2. Every route carries a summary, a description and at least one tag, and
   its success response declares a schema (or, for the two file-serving
   routes, a real content type, with no stray application/json alongside
   it) - see test_every_route_is_documented for what "documented" is
   defined to mean and why an undocumented new route fails this rather than
   passing by default. FastAPI derives a summary from a function's name even
   with no docstring at all, so summary alone would be a test that could
   never go red; description is what actually requires someone to have
   written something.
3. For a representative endpoint out of each group (system, settings,
   instruments, library, practice, transcription, scan, upload), the JSON
   that actually comes back over real HTTP validates against the model
   response_model= declares for it.
4. FastAPI's response-model validation is switched on, not merely present in
   the decorator - test_response_validation_actually_rejects_a_bad_response
   proves it against a throwaway route using the exact same
   `response_model=` idiom every route in api.py uses, so the proof is about
   the mechanism itself rather than about any one handler happening to
   behave.
5. THE SYSTEMIC GUARD. Group 3's `Model.model_validate(response.json())`
   checks are tautological on their own: a response the model just filtered
   and serialized is model-valid by construction, so they can never catch a
   field the model silently drops on the way to the wire - the exact bug
   class this repo has shipped more than once when a handler grew a field
   and its response model did not (see #143, #145, #146 for the same
   pattern one layer down, in `_BAR_KEYS` versus what actually got written).
   `client`, below, is wrapped so every request made through it in group 3
   is ALSO checked against the raw value the handler itself returned, before
   response_model ever touched it - captured via fastapi.routing's own
   run_endpoint_function, the one seam between "what the handler computed"
   and "what got filtered". Every key present on the raw return must reach
   the wire at the same path, recursively, so a field dropped three levels
   down (goal.progress.sessions_inferred, say) is named exactly where it was
   lost. This is what actually keeps ~30 hand-mirrored models honest - not
   the individual model_validate() calls, which stay in group 3 because they
   check a different property (declared TYPES match reality) than this one
   (no declared field is silently dropped).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openapi_spec_validator import validate as validate_openapi

import fastapi.routing as fastapi_routing
from fermata import api, api_models
from fermata.main import app as full_app

# Every route in api.py returns a FileResponse (binary) rather than JSON for
# exactly these two - see the `responses=` kwarg on their decorators. They are
# "documented" by declaring a real content type instead of a JSON schema.
_BINARY_ROUTES = {
    ("GET", "/api/scores/{score_id}/file"),
    ("GET", "/api/scores/{score_id}/thumb"),
    # Issue #58's export: a zip archive, not JSON - see api.export_library's
    # docstring for why it has no response_model, the same reason the two
    # routes above do not.
    ("GET", "/api/export"),
}


def _assert_wire_carries_every_raw_key(raw, wire, route: str, path: str = ""):
    """The systemic drift guard (claim 5 above). `raw` is what the handler
    itself returned, captured before response_model touched it; `wire` is
    the parsed JSON that actually left the server for the same request.
    Every key present in `raw` must be reachable at the same path in
    `wire` - recursively through nested dicts and lists of dicts, so a field
    dropped inside a nested model (GoalOut.progress, InstrumentOut.strings,
    ...) is named at its own path rather than only at the top.

    Deliberately checks KEY PRESENCE only, never value equality - a bool
    becoming a JSON `true`, or an int surviving a round trip, is not what
    this is for for; response-model type-correctness is group 3's job
    (Model.model_validate). This one question only: did every field the
    handler computed make it to the wire, or did the model quietly drop it.

    dict[str, Any] passthrough fields (TranscriptionOut.confidence) declare
    no sub-schema, so nothing between the handler and the wire strips
    anything inside them - recursing into them same as any other dict is
    safe (it can only ever agree) rather than something this needs to
    special-case around.
    """
    if isinstance(raw, dict):
        assert isinstance(wire, dict), (
            f"{route}: at '{path or '(root)'}' the raw return was a dict but the wire "
            f"response was {type(wire).__name__} - response_model changed the shape, "
            "not just dropped a field"
        )
        for key, raw_value in raw.items():
            assert key in wire, (
                f"{route}: field '{path}{key}' is on the handler's raw return but missing "
                "from the wire JSON - grow its response model in api_models.py to include it"
            )
            _assert_wire_carries_every_raw_key(raw_value, wire[key], route, f"{path}{key}.")
    elif isinstance(raw, (list, tuple)):
        if not isinstance(wire, list):
            return  # a type mismatch here is group 3's claim to catch, not this one's
        for i, (raw_item, wire_item) in enumerate(zip(raw, wire)):
            _assert_wire_carries_every_raw_key(raw_item, wire_item, route, f"{path}[{i}].")
    # scalars: nothing further to check - key presence is this function's whole claim


class _DriftGuardedClient:
    """Wraps a TestClient so every JSON request made through it is checked
    by _assert_wire_carries_every_raw_key - see claim 5 in the module
    docstring. `_captured` is filled by the run_endpoint_function spy the
    `client` fixture installs; `_call` reads off exactly the entries added
    during its own request (there is exactly one per request in this app,
    since no route here calls another route through Depends() - a handler
    calling another handler function directly, as create_instrument calls
    get_instrument, is a plain Python call and never re-enters FastAPI's
    routing at all)."""

    def __init__(self, inner: TestClient, captured: list):
        self._inner = inner
        self._captured = captured

    def _call(self, method: str, url: str, **kwargs):
        before = len(self._captured)
        resp = getattr(self._inner, method)(url, **kwargs)
        new_raws = self._captured[before:]
        content_type = resp.headers.get("content-type", "")
        if new_raws and content_type.startswith("application/json"):
            _assert_wire_carries_every_raw_key(
                new_raws[-1], resp.json(), f"{method.upper()} {url}"
            )
        return resp

    def get(self, url, **kw):
        return self._call("get", url, **kw)

    def post(self, url, **kw):
        return self._call("post", url, **kw)

    def put(self, url, **kw):
        return self._call("put", url, **kw)

    def patch(self, url, **kw):
        return self._call("patch", url, **kw)

    def delete(self, url, **kw):
        return self._call("delete", url, **kw)


@pytest.fixture
def client(app_env, monkeypatch):
    """The router alone - same pattern as test_instruments_api.py and
    test_version_api.py's client fixtures. Schema-shape assertions use
    `full_app` (fermata.main.app) instead, further down, because that is the
    literal app that serves /docs and /openapi.json in production; this one
    is for hitting live endpoints against a throwaway database.

    Wrapped in _DriftGuardedClient rather than handed back as a bare
    TestClient: every group-3 test already makes real requests here to
    validate response shape, and reusing that same traffic for the systemic
    drift guard (claim 5) needs no separate scenario or fixture of its own.
    `run_endpoint_function` is FastAPI's own seam between "the handler
    returned this" and "response_model filtered it to this" - patched at
    the module fastapi.routing resolves it from, which is where routing.py's
    own `await run_endpoint_function(...)` call looks it up at call time."""
    app = FastAPI()
    app.include_router(api.router)

    captured: list = []
    original = fastapi_routing.run_endpoint_function

    async def spy(*, dependant, values, is_coroutine):
        result = await original(dependant=dependant, values=values, is_coroutine=is_coroutine)
        captured.append(result)
        return result

    monkeypatch.setattr(fastapi_routing, "run_endpoint_function", spy)
    return _DriftGuardedClient(TestClient(app), captured)


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
    MCP server this documents the contract for) would not choke on
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
            # `"schema" in media` is satisfied by response_model=None's own
            # empty `{"schema": {}}` - FastAPI emits that placeholder for
            # every JSON-content route regardless of whether a real model
            # was ever attached, so a route with NO response_model still
            # passed this check. `.get(...)` truthy-checked, not `in`, is
            # what actually requires a non-empty schema object.
            schema_present = any(media.get("schema") for media in content.values())
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
    # 41 before issue #56, plus its nine: move one score, list/create folders,
    # rename a folder, move several scores, delete a score, list the trash,
    # restore from it, and destroy from it. Plus issue #57's one: how one
    # piece is going. Plus issue #16's one: GET /api/me. Plus issue #58's two:
    # export the library to an archive, import one back. Plus issue #27's
    # two: log a fretboard drill attempt, and list them. Plus issue #55's
    # two: start a bulk transcription pass, poll its status. Plus issue #28's
    # two: log a chord flash card attempt, and list them. Plus issue #6's
    # eight setlist routes: list, create, get one, rename, delete, add a
    # score, remove a score, reorder.
    assert count == 68


def test_binary_routes_do_not_advertise_a_json_content_type(openapi_schema):
    """Without `response_class=FileResponse`, FastAPI assumes a route can
    also answer with `application/json` (its default) alongside whatever
    real content types `responses=` declares - so a codegen reading
    /openapi.json would believe GET .../file or .../thumb might hand back
    JSON, when neither ever does. Only the real content types may appear."""
    for method, path in _BINARY_ROUTES:
        content = openapi_schema["paths"][path][method.lower()]["responses"]["200"]["content"]
        assert "application/json" not in content, (
            f"{method} {path} advertises application/json - add "
            "response_class=FileResponse to its decorator"
        )
        assert content, f"{method} {path} advertises no content type at all"


# ---------------------------------------------------------------------------
# 3. Declared shapes match what actually comes back, across every group.
# ---------------------------------------------------------------------------


def test_system_and_settings_responses_match_their_models(client):
    api_models.HealthOut.model_validate(client.get("/api/health").json())
    api_models.VersionOut.model_validate(client.get("/api/version").json())
    api_models.MeOut.model_validate(client.get("/api/me").json())
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


def test_library_management_responses_match_their_models(client, app_env, monkeypatch, tmp_path):
    """Issue #56's nine routes, through the same drift-guarded client.

    A real file in a real throwaway library, because every one of these
    endpoints reads or writes the filesystem - a stub row with a made-up hash
    would be refused by the move's own content check, which is the point of
    that check. api.py bound LIBRARY_DIR by value at import, so it is
    repointed the way test_scanner.py's fixtures do it.
    """
    from fermata import config, db, scanner

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

    for item in client.get("/api/library/folders").json():
        api_models.FolderOut.model_validate(item)
    api_models.FolderCreateOut.model_validate(
        client.post("/api/library/folders", json={"path": "Classical"}).json()
    )
    api_models.ScoreMoveOut.model_validate(
        client.post(f"/api/scores/{score_id}/move", json={"folder": "Classical"}).json()
    )
    api_models.LibraryMoveOut.model_validate(
        client.post(
            "/api/library/move", json={"score_ids": [score_id], "folder": "Classical/Sor"}
        ).json()
    )
    api_models.FolderRenameOut.model_validate(
        client.post(
            "/api/library/folders/rename",
            json={"from_path": "Classical", "to_path": "Romantic", "dry_run": False},
        ).json()
    )
    api_models.ScoreDeleteOut.model_validate(client.delete(f"/api/scores/{score_id}").json())
    for item in client.get("/api/trash").json():
        api_models.ScoreOut.model_validate(item)
    api_models.ScoreRestoreOut.model_validate(
        client.post(f"/api/trash/{score_id}/restore").json()
    )
    client.delete(f"/api/scores/{score_id}")
    api_models.ScorePurgeOut.model_validate(client.delete(f"/api/trash/{score_id}").json())


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
    # Issue #57's endpoint, exercised with a session, a tempo, a mode, a
    # rating and a goal actually present - every nested block below `progress`
    # is empty or absent-shaped on a piece nobody has practised, and a drift
    # guard that only ever sees empty lists cannot notice a field dropped from
    # the objects inside them.
    detailed = client.post(
        f"/api/scores/{score_id}/practice",
        json={"seconds": 900, "tempo_bpm": 90, "target_tempo_bpm": 120, "mode": "section", "rating": 4},
    )
    assert detailed.status_code == 200, detailed.text
    scoped = client.post(
        "/api/practice/goals",
        json={"scope": "score", "score_id": score_id, "target_days": 2, "intent": "bar 34"},
    )
    assert scoped.status_code == 200, scoped.text
    api_models.ScoreProgressOut.model_validate(
        client.get(f"/api/scores/{score_id}/practice/progress").json()
    )
    client.delete(f"/api/practice/goals/{scoped.json()['id']}")

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
    """The one shape genuinely different from the rest: practice.goal_progress
    OMITS `sessions_inferred` and every day's `inferred` key entirely on its
    uncountable branch - see api_models.GoalDayOut and GoalProgressOut's
    docstrings. That is a fact about the RAW handler return; it is not what a
    client actually sees. response_model= fills in each field's declared
    default (None) for a key the handler never set, so the WIRE response
    carries `"sessions_inferred": null` and `"inferred": null` explicitly -
    additive relative to what main sends (an absent key becomes a present
    null), and the one payload change this PR is actually responsible for.
    Checked directly against the raw JSON below, deliberately, rather than
    only through the parsed model - a model attribute reads the same
    (`None`) whether the wire carried the key as `null` or omitted it
    altogether, so only inspecting `response.json()` itself proves which one
    actually happened.

    Also exercises the case that would 500 if these fields were modelled as
    required ints instead of optional ones, which is the reason this test
    exists at all."""
    from fermata import db

    conn = db.connect()
    score_id = insert_score(conn, "Deleted Piece.pdf", title="Deleted Piece")
    conn.commit()
    goal = client.post(
        "/api/practice/goals", json={"scope": "score", "score_id": score_id, "target_days": 3}
    ).json()
    conn.execute("DELETE FROM scores WHERE id = ?", (score_id,))
    conn.commit()

    wire = client.get("/api/practice/goals").json()["goals"][0]
    progress = wire["progress"]
    assert progress["countable"] is False
    assert "sessions_inferred" in progress and progress["sessions_inferred"] is None
    for day in progress["days"]:
        assert "inferred" in day and day["inferred"] is None

    validated = api_models.GoalOut.model_validate(wire)
    assert validated.progress.countable is False
    assert validated.progress.sessions_inferred is None
    assert all(day.inferred is None for day in validated.progress.days)
    assert goal["id"] == validated.id


def test_setlist_responses_match_their_models(client, insert_score):
    """Issue #6's eight routes, through the same drift-guarded client, with a
    real member in place - a setlist with no scores would let a field dropped
    from SetlistMemberOut or the nested ScoreOut pass unnoticed, exactly the
    empty-collection blind spot the practice test above guards against."""
    from fermata import db

    conn = db.connect()
    first = insert_score(conn, "Setlist One.pdf", title="Setlist One")
    second = insert_score(conn, "Setlist Two.pdf", title="Setlist Two")
    conn.commit()

    created = api_models.SetlistOut.model_validate(
        client.post("/api/setlists", json={"name": "Friday gig"}).json()
    )
    for item in client.get("/api/setlists").json():
        api_models.SetlistOut.model_validate(item)

    client.post(f"/api/setlists/{created.id}/scores", json={"score_id": first})
    api_models.SetlistDetailOut.model_validate(
        client.post(f"/api/setlists/{created.id}/scores", json={"score_id": second}).json()
    )
    api_models.SetlistDetailOut.model_validate(
        client.get(f"/api/setlists/{created.id}").json()
    )
    api_models.SetlistDetailOut.model_validate(
        client.put(
            f"/api/setlists/{created.id}/order", json={"score_ids": [second, first]}
        ).json()
    )
    api_models.SetlistDetailOut.model_validate(
        client.patch(f"/api/setlists/{created.id}", json={"name": "Saturday gig"}).json()
    )
    api_models.SetlistDetailOut.model_validate(
        client.delete(f"/api/setlists/{created.id}/scores/{first}").json()
    )
    api_models.SetlistDeleteOut.model_validate(
        client.delete(f"/api/setlists/{created.id}").json()
    )


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


def test_trainer_responses_match_their_models(client):
    """Issue #27's two routes, through the drift-guarded client - proves both
    that TrainerAttemptOut/TrainerAttemptListOut describe the real response
    shape and (claim 5) that every key the handler actually computed reaches
    the wire, which a bare model_validate() on the response alone could
    never catch."""
    logged = client.post(
        "/api/trainer/attempts",
        json={
            "drill": "fret_to_note",
            "direction": "position_to_note",
            "target_string": 6,
            "target_fret": 3,
            "target_note": "G",
            "given_note": "G",
        },
    )
    assert logged.status_code == 200, logged.text
    api_models.TrainerAttemptOut.model_validate(logged.json())

    api_models.TrainerAttemptListOut.model_validate(
        client.get("/api/trainer/attempts").json()
    )


def test_transcribe_batch_responses_match_their_models(
    client, insert_score, extractable_pdf, monkeypatch
):
    """POST /transcribe/batch and GET /transcribe/batch/status (issue #55).
    Routed through `client`, so this also exercises claim 5's drift guard -
    a field _batch_process_one/_run_batch computes but
    TranscribeBatchStatusOut/TranscribeBatchResultLineOut do not declare
    would fail here by name rather than silently reaching the wire
    narrower than what was actually returned."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    from fermata import db

    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
    conn.commit()

    triggered = client.post("/api/transcribe/batch", json={"score_ids": [score_id]})
    api_models.TranscribeBatchTriggerOut.model_validate(triggered.json())

    import time as _time

    deadline = _time.monotonic() + 10
    while True:
        status = client.get("/api/transcribe/batch/status")
        api_models.TranscribeBatchStatusOut.model_validate(status.json())
        if not status.json()["running"]:
            break
        if _time.monotonic() > deadline:
            raise AssertionError("the bulk transcription pass did not finish")
        _time.sleep(0.02)
    assert status.json()["results"][0]["outcome"] == "transcribed"


def test_scan_and_upload_responses_match_their_models(client, monkeypatch):
    from fermata import config

    monkeypatch.setattr(api.scanner, "start_scan", lambda **kw: True)
    api_models.ScanStatusOut.model_validate(client.get("/api/scan/status").json())
    api_models.ScanTriggerOut.model_validate(client.post("/api/scan").json())

    # UploadOut, actually exercised - app_env already made config.LIBRARY_DIR
    # a real throwaway folder; api.py bound its own LIBRARY_DIR name at
    # import time (`from .config import LIBRARY_DIR`), so it has to be
    # repointed directly - see test_scanner.py's upload tests for the same
    # pattern.
    monkeypatch.setattr(api, "LIBRARY_DIR", config.LIBRARY_DIR)
    uploaded = client.post(
        "/api/upload?folder=Uploads",
        files={"file": ("thing.pdf", b"%PDF-1.4 not a real pdf", "application/pdf")},
    )
    assert uploaded.status_code == 200
    api_models.UploadOut.model_validate(uploaded.json())


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
