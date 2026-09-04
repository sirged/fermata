"""Key, tempo and difficulty as queryable score columns (issue #8).

Three nullable columns on `scores`, added through the existing ADD-COLUMN
mechanism (db._SCORES_COLUMNS / db.COLUMN_ADDITIONS), a patch surface with a
closed range on each, filters on GET /api/scores that compose with the
existing ones, and one opportunistic write nothing but transcribing ever
makes: copying a REAL glyph-decoded key onto a score whose key is still null.

Migration and filter-composition tests call api.list_scores/api.patch_score
directly (the same level test_instruments_api.py uses for #72's own
instrument_id, which key/tempo/difficulty's PATCH-clearing behaviour mirrors
exactly). The 422 boundary tests go over real HTTP through `client`, because
FastAPI's own Query()/Field() bound-checking is request-layer behaviour a
direct Python call bypasses entirely - calling list_scores(key=99) in-process
would just pass 99 straight through as an ordinary keyword argument.

Opportunistic-fill tests run against the committed engraved fixtures
(server/tests/fixtures/engraved) rather than a library score, the same choice
test_transcription_api.py made: this is about the API's own behaviour around
a decoded key, not about any one real scan of anyone's music, so it needs no
library and never skips in CI.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db


@pytest.fixture
def client(app_env):
    """The router alone, without main.py's lifespan or static mount - see
    test_instruments_api.py's identical fixture for why (a query parameter's
    bounds are request-layer behaviour, never applied when a handler is
    called directly)."""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _scores_columns(conn) -> set[str]:
    return {r["name"] for r in conn.execute("PRAGMA table_info(scores)")}


# --------------------------------------------------------------- migration


def test_a_fresh_database_has_all_three_columns_and_they_start_null(app_env, insert_score):
    conn = db.connect()
    assert {"key", "tempo", "difficulty"} <= _scores_columns(conn)
    score_id = insert_score(conn, "a.pdf")
    row = conn.execute(
        "SELECT key, tempo, difficulty FROM scores WHERE id = ?", (score_id,)
    ).fetchone()
    assert (row["key"], row["tempo"], row["difficulty"]) == (None, None, None)


def test_the_three_columns_are_added_to_a_database_that_predates_them(app_env, insert_score):
    """SCHEMA's CREATE TABLE IF NOT EXISTS does nothing to a scores table that
    already exists (the same reasoning test_instruments_api.py's identical
    migration test spells out for instrument_id), so simulate an upgrade by
    dropping the three columns from an already-initialised database and
    re-running init_db - which is what an upgrade actually does."""
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    for column in ("key", "tempo", "difficulty"):
        conn.execute(f"ALTER TABLE scores DROP COLUMN {column}")
    conn.commit()
    assert not {"key", "tempo", "difficulty"} & _scores_columns(conn)

    db.init_db()

    assert {"key", "tempo", "difficulty"} <= _scores_columns(conn)
    row = conn.execute(
        "SELECT key, tempo, difficulty FROM scores WHERE id = ?", (score_id,)
    ).fetchone()
    assert (row["key"], row["tempo"], row["difficulty"]) == (None, None, None)


# ------------------------------------------------------------------- PATCH


def test_patch_sets_key_tempo_and_difficulty(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    patched = api.patch_score(
        score_id, api.ScorePatch(key=2, tempo=120, difficulty=3)
    )
    assert (patched["key"], patched["tempo"], patched["difficulty"]) == (2, 120, 3)
    assert (
        api.get_score(score_id)["key"],
        api.get_score(score_id)["tempo"],
        api.get_score(score_id)["difficulty"],
    ) == (2, 120, 3)


def test_patch_clears_key_tempo_and_difficulty_explicitly(app_env, insert_score):
    """An explicit null is a request ("clear this"), not an omission - the
    same distinction instrument_id already draws (see NULLABLE_PATCH_FIELDS),
    now shared by these three."""
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    api.patch_score(score_id, api.ScorePatch(key=2, tempo=120, difficulty=3))
    cleared = api.patch_score(
        score_id, api.ScorePatch(key=None, tempo=None, difficulty=None)
    )
    assert (cleared["key"], cleared["tempo"], cleared["difficulty"]) == (None, None, None)


def test_patching_something_else_leaves_key_tempo_and_difficulty_alone(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    api.patch_score(score_id, api.ScorePatch(key=2, tempo=120, difficulty=3))
    patched = api.patch_score(score_id, api.ScorePatch(title="Renamed"))
    assert patched["title"] == "Renamed"
    assert (patched["key"], patched["tempo"], patched["difficulty"]) == (2, 120, 3)


def test_a_zero_key_is_a_settable_value_not_an_omission(app_env, insert_score):
    """0 fifths (no sharps or flats) is a real, common answer - the field must
    not treat it as though nothing were sent, the classic falsy-value trap."""
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    patched = api.patch_score(score_id, api.ScorePatch(key=0))
    assert patched["key"] == 0


@pytest.mark.parametrize("bad", [-8, 8])
def test_a_key_outside_the_fifths_range_is_a_422(client, insert_score, bad):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    response = client.patch(f"/api/scores/{score_id}", json={"key": bad})
    assert response.status_code == 422


@pytest.mark.parametrize("bad", [19, 401])
def test_a_tempo_outside_the_bpm_range_is_a_422(client, insert_score, bad):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    response = client.patch(f"/api/scores/{score_id}", json={"tempo": bad})
    assert response.status_code == 422


@pytest.mark.parametrize("bad", [0, 6])
def test_a_difficulty_outside_the_1_to_5_range_is_a_422(client, insert_score, bad):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    response = client.patch(f"/api/scores/{score_id}", json={"difficulty": bad})
    assert response.status_code == 422


def test_a_non_integer_key_is_a_422(client, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    response = client.patch(f"/api/scores/{score_id}", json={"key": 2.5})
    assert response.status_code == 422


def test_a_bool_is_not_silently_taken_as_a_difficulty(client, insert_score):
    """The same trap Count's own comment names: Pydantic's default mode
    coerces before a validator sees the value, so a bare `int` field would
    accept `true` as 1. StrictInt is what refuses it."""
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    response = client.patch(f"/api/scores/{score_id}", json={"difficulty": True})
    assert response.status_code == 422


# --------------------------------------------------------------- GET filters


def test_the_key_filter_narrows_to_an_exact_match(app_env, insert_score):
    conn = db.connect()
    a = insert_score(conn, "a.pdf")
    b = insert_score(conn, "b.pdf")
    api.patch_score(a, api.ScorePatch(key=2))
    api.patch_score(b, api.ScorePatch(key=-3))
    ids = {s["id"] for s in api.list_scores(key=2)}
    assert ids == {a}


def test_the_difficulty_filter_narrows_to_an_exact_match(app_env, insert_score):
    conn = db.connect()
    a = insert_score(conn, "a.pdf")
    b = insert_score(conn, "b.pdf")
    api.patch_score(a, api.ScorePatch(difficulty=5))
    api.patch_score(b, api.ScorePatch(difficulty=1))
    ids = {s["id"] for s in api.list_scores(difficulty=5)}
    assert ids == {a}


def test_the_tempo_filter_bounds_by_min_and_max(app_env, insert_score):
    conn = db.connect()
    slow = insert_score(conn, "slow.pdf")
    mid = insert_score(conn, "mid.pdf")
    fast = insert_score(conn, "fast.pdf")
    api.patch_score(slow, api.ScorePatch(tempo=60))
    api.patch_score(mid, api.ScorePatch(tempo=120))
    api.patch_score(fast, api.ScorePatch(tempo=200))

    assert {s["id"] for s in api.list_scores(tempo_min=100)} == {mid, fast}
    assert {s["id"] for s in api.list_scores(tempo_max=150)} == {slow, mid}
    assert {s["id"] for s in api.list_scores(tempo_min=100, tempo_max=150)} == {mid}


def test_a_zero_key_filter_is_not_dropped_as_though_it_were_unset(app_env, insert_score):
    conn = db.connect()
    a = insert_score(conn, "a.pdf")
    b = insert_score(conn, "b.pdf")
    api.patch_score(a, api.ScorePatch(key=0))
    api.patch_score(b, api.ScorePatch(key=2))
    assert {s["id"] for s in api.list_scores(key=0)} == {a}


def test_key_and_difficulty_filters_compose_with_favorite(app_env, insert_score):
    conn = db.connect()
    a = insert_score(conn, "a.pdf")
    b = insert_score(conn, "b.pdf")
    api.patch_score(a, api.ScorePatch(key=2, difficulty=3, favorite=True))
    api.patch_score(b, api.ScorePatch(key=2, difficulty=3, favorite=False))
    ids = {s["id"] for s in api.list_scores(key=2, difficulty=3, favorite=True)}
    assert ids == {a}


def test_a_score_with_no_key_set_is_excluded_from_a_key_filter(app_env, insert_score):
    conn = db.connect()
    a = insert_score(conn, "a.pdf")
    insert_score(conn, "b.pdf")
    api.patch_score(a, api.ScorePatch(key=2))
    assert {s["id"] for s in api.list_scores(key=2)} == {a}
    assert api.list_scores(key=-5) == []


@pytest.mark.parametrize("bad", [-8, 8])
def test_a_key_filter_outside_the_fifths_range_is_a_422(client, bad):
    response = client.get(f"/api/scores?key={bad}")
    assert response.status_code == 422


@pytest.mark.parametrize("bad", [0, 6])
def test_a_difficulty_filter_outside_the_range_is_a_422(client, bad):
    response = client.get(f"/api/scores?difficulty={bad}")
    assert response.status_code == 422


@pytest.mark.parametrize("bad", [19, 401])
def test_a_tempo_min_filter_outside_the_bpm_range_is_a_422(client, bad):
    response = client.get(f"/api/scores?tempo_min={bad}")
    assert response.status_code == 422


# ------------------------------------------------ opportunistic fill (#8)


def test_transcribing_fills_a_null_key_from_a_glyph_decoded_signature(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    """notation_and_tab.pdf decodes to key_fifths=2 (see
    test_engraved_fixtures.py's own pin on this fixture) - the fixture is
    unambiguous and committed, not any one person's real music."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
    assert api.get_score(score_id)["key"] is None

    result = api.transcribe(score_id, body=None)
    assert result["key_fifths"] == 2
    assert result["key_signature_source"] == "glyph-decoded"

    assert api.get_score(score_id)["key"] == 2


def test_transcribing_never_overwrites_a_hand_set_key(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    """The never-overwrite guarantee: a key a person set - or one an earlier
    transcription already filled in - survives a (re-)transcription that
    would otherwise plant a different decoded value over it."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
    api.patch_score(score_id, api.ScorePatch(key=-5))

    result = api.transcribe(score_id, body=None)
    assert result["key_fifths"] == 2  # the decoder still reads 2 off the page

    assert api.get_score(score_id)["key"] == -5


def test_a_key_that_was_not_actually_decoded_is_never_copied(
    app_env, engraved, monkeypatch, insert_score
):
    """tab_only.pdf has no standard staff to read a key signature off at all,
    so the extractor falls back to the assumed-zero-fifths answer
    ("not detected (assumed no key signature)") rather than a real read.
    Copying that onto every such score would plant a false "no sharps or
    flats" on precisely the scores this extractor could not read a key
    from."""
    fixture = engraved("tab_only")
    monkeypatch.setattr(api, "LIBRARY_DIR", fixture.parent)
    conn = db.connect()
    score_id = insert_score(conn, fixture.name)

    result = api.transcribe(score_id, body=None)
    assert result["key_signature_source"] != "glyph-decoded"

    assert api.get_score(score_id)["key"] is None


def test_transcribing_never_writes_tempo_or_difficulty(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    """#8's No-gos: no difficulty inference, and tempo stays manual because
    the decoder's own tempo reading carries no confidence figure. A
    hand-set difficulty must also survive - the same guarantee the key gets,
    proved here over the field nothing ever auto-fills at all."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
    api.patch_score(score_id, api.ScorePatch(difficulty=4))

    api.transcribe(score_id, body=None)
    api.transcribe(score_id, body=None)  # a second pass, i.e. a rescan

    after = api.get_score(score_id)
    assert after["tempo"] is None
    assert after["difficulty"] == 4
