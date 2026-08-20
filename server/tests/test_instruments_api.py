import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from fermata import api, db, instruments

GUITAR = {
    "name": "My guitar",
    "fretted": True,
    "string_count": 6,
    "string_pitches": ["E2", "A2", "D3", "G3", "B3", "E4"],
    "fret_count": 22,
}

VIOLIN = {
    "name": "My violin",
    "fretted": False,
    "string_count": 4,
    "string_pitches": ["G3", "D4", "A4", "E5"],
}


def make(**overrides) -> api.InstrumentIn:
    return api.InstrumentIn(**{**GUITAR, **overrides})


def make_unfretted(**overrides) -> api.InstrumentIn:
    return api.InstrumentIn(**{**VIOLIN, **overrides})


# ---------------------------------------------------------------- presets


def test_presets_load():
    keys = [p["key"] for p in instruments.presets()]
    assert keys == [
        "guitar-standard",
        "guitar-drop-d",
        "guitar-dadgad",
        "guitar-open-g",
        "guitar-seven-string",
        "bass-four-string",
        "bass-five-string",
        "ukulele",
        "violin",
        "viola",
        "cello",
    ]
    assert api.list_instrument_presets() == instruments.presets()


def test_every_presets_string_count_matches_its_pitches():
    for preset in instruments.presets():
        assert preset["string_count"] == len(preset["string_pitches"]), preset["key"]


def test_unfretted_presets_carry_no_fretted_only_fields():
    unfretted = [p for p in instruments.presets() if not p["fretted"]]
    assert [p["key"] for p in unfretted] == ["violin", "viola", "cello"]
    for preset in unfretted:
        assert preset["fret_count"] is None
        assert preset["capo"] is None


def test_every_preset_is_a_definition_the_api_accepts(app_env):
    """A preset a player picks is submitted back verbatim, so any preset the
    validator would reject is a preset that cannot be saved."""
    for preset in instruments.presets():
        saved = api.create_instrument(
            api.InstrumentIn(
                name=preset["name"],
                fretted=preset["fretted"],
                string_count=preset["string_count"],
                string_pitches=preset["string_pitches"],
                fret_count=preset["fret_count"],
                capo=preset["capo"],
                reference_pitch=preset["reference_pitch"],
            )
        )
        assert saved["string_pitches"] == preset["string_pitches"]
        assert saved["strings"] == preset["strings"]


def test_the_reentrant_ukulele_preset_is_not_rejected_for_its_order(app_env):
    """A ukulele's string 4 sounds above its string 3. String order is physical,
    not ascending pitch, so nothing may enforce a rising sequence."""
    uke = next(p for p in instruments.presets() if p["key"] == "ukulele")
    assert uke["string_pitches"] == ["G4", "C4", "E4", "A4"]
    saved = api.create_instrument(
        api.InstrumentIn(
            name="Ukulele", fretted=True, string_count=4,
            string_pitches=uke["string_pitches"], fret_count=12,
        )
    )
    assert [s["number"] for s in saved["strings"]] == [4, 3, 2, 1]
    assert saved["strings"][0]["midi"] > saved["strings"][1]["midi"]


# ---------------------------------------------------------------- frequencies


def test_standard_tuning_frequencies_at_a440(app_env):
    saved = api.create_instrument(make())
    by_pitch = {s["pitch"]: s["frequency"] for s in saved["strings"]}
    assert by_pitch == {
        "E2": 82.407,
        "A2": 110.0,
        "D3": 146.832,
        "G3": 195.998,
        "B3": 246.942,
        "E4": 329.628,
    }


def test_the_reference_pitch_moves_every_string(app_env):
    saved = api.create_instrument(make(reference_pitch=415.0))
    by_pitch = {s["pitch"]: s["frequency"] for s in saved["strings"]}
    # A415, a common Baroque pitch: concert A lands on 103.75 (415/4) and the
    # low E follows it down from 82.407 rather than staying put.
    assert by_pitch["A2"] == pytest.approx(103.75, abs=0.001)
    assert by_pitch["E2"] == pytest.approx(77.725, abs=0.001)


def test_string_numbers_run_opposite_to_list_order(app_env):
    saved = api.create_instrument(make())
    assert [(s["number"], s["pitch"]) for s in saved["strings"]] == [
        (6, "E2"), (5, "A2"), (4, "D3"), (3, "G3"), (2, "B3"), (1, "E4"),
    ]


# ---------------------------------------------------------------- create/read


def test_create_then_read_back(app_env):
    created = api.create_instrument(make())
    assert created["name"] == "My guitar"
    assert created["fretted"] is True
    assert created["string_pitches"] == GUITAR["string_pitches"]
    assert created["fret_count"] == 22
    assert created["capo"] == 0
    assert created["reference_pitch"] == 440.0
    assert api.get_instrument(created["id"]) == created
    assert api.list_instruments() == [created]


def test_owner_defaults_to_the_single_instance_owner(app_env):
    created = api.create_instrument(make())
    conn = db.connect()
    row = conn.execute(
        "SELECT owner FROM instruments WHERE id = ?", (created["id"],)
    ).fetchone()
    assert row["owner"] == db.DEFAULT_OWNER == "local"
    assert created["owner"] == "local"


def test_an_unfretted_instrument_stores_no_fret_fields(app_env):
    created = api.create_instrument(make_unfretted())
    assert created["fretted"] is False
    assert created["fret_count"] is None
    assert created["capo"] is None
    assert [s["pitch"] for s in created["strings"]] == ["G3", "D4", "A4", "E5"]


def test_reading_an_instrument_that_does_not_exist(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.get_instrument(999)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------- validation


def test_too_few_string_pitches_for_the_string_count_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=6, string_pitches=["E2", "A2", "D3"]))
    assert exc_info.value.status_code == 422
    assert "3 string pitch" in exc_info.value.detail
    assert api.list_instruments() == []


def test_too_many_string_pitches_for_the_string_count_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(
            make(string_count=4, string_pitches=["E1", "A1", "D2", "G2", "C3"])
        )
    assert exc_info.value.status_code == 422


def test_a_fret_count_on_an_unfretted_instrument_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make_unfretted(fret_count=19))
    assert exc_info.value.status_code == 422
    assert "fret_count" in exc_info.value.detail
    assert api.list_instruments() == []


def test_a_capo_on_an_unfretted_instrument_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make_unfretted(capo=2))
    assert exc_info.value.status_code == 422
    assert "capo" in exc_info.value.detail


def test_a_fretted_instrument_needs_a_fret_count(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(fret_count=None))
    assert exc_info.value.status_code == 422


def test_a_capo_past_the_last_fret_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(fret_count=12, capo=13))
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("fret_count", [0, instruments.MAX_FRETS + 1])
def test_fret_counts_outside_the_bounds_are_rejected(app_env, fret_count):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(fret_count=fret_count))
    assert exc_info.value.status_code == 422


def test_a_string_count_of_zero_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=0, string_pitches=[]))
    assert exc_info.value.status_code == 422


def test_more_strings_than_any_instrument_has_is_rejected(app_env):
    count = instruments.MAX_STRINGS + 1
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=count, string_pitches=["E2"] * count))
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("pitch", ["", "H2", "E", "E2x", "Ebb"])
def test_a_string_pitch_that_is_not_a_pitch_name_is_rejected(app_env, pitch):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(
            make(string_count=2, string_pitches=[pitch, "A2"], fret_count=12)
        )
    assert exc_info.value.status_code == 422


def test_a_string_outside_the_playable_range_is_rejected(app_env):
    """MIDI stops at 127, and a string the synthesiser cannot sound is a
    tuning that cannot be checked - which is most of the point."""
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(string_count=1, string_pitches=["C12"], fret_count=12))
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize("hz", [0, 100.0, 1000.0])
def test_a_reference_pitch_outside_the_bounds_is_rejected(app_env, hz):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(reference_pitch=hz))
    assert exc_info.value.status_code == 422


def test_a_blank_name_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.create_instrument(make(name="   "))
    assert exc_info.value.status_code == 422


def test_a_name_is_stored_trimmed(app_env):
    created = api.create_instrument(make(name="  Parlour guitar  "))
    assert created["name"] == "Parlour guitar"


# ---------------------------------------------------------------- update


def test_update_replaces_the_definition(app_env):
    created = api.create_instrument(make())
    updated = api.update_instrument(
        created["id"],
        api.InstrumentIn(
            name="Dropped D",
            fretted=True,
            string_count=6,
            string_pitches=["D2", "A2", "D3", "G3", "B3", "E4"],
            fret_count=20,
            capo=2,
        ),
    )
    assert updated["id"] == created["id"]
    assert updated["name"] == "Dropped D"
    assert updated["string_pitches"][0] == "D2"
    assert updated["fret_count"] == 20
    assert updated["capo"] == 2
    assert api.get_instrument(created["id"]) == updated
    assert len(api.list_instruments()) == 1


def test_update_switching_to_unfretted_clears_the_fret_fields(app_env):
    created = api.create_instrument(make())
    updated = api.update_instrument(
        created["id"],
        api.InstrumentIn(
            name="Fretless", fretted=False, string_count=4,
            string_pitches=["E1", "A1", "D2", "G2"],
        ),
    )
    assert updated["fretted"] is False
    assert updated["fret_count"] is None
    assert updated["capo"] is None


def test_update_applies_the_same_rules_as_create(app_env):
    created = api.create_instrument(make())
    with pytest.raises(HTTPException) as exc_info:
        api.update_instrument(created["id"], make(string_count=6, string_pitches=["E2"]))
    assert exc_info.value.status_code == 422
    # rejected outright, not half-applied
    assert api.get_instrument(created["id"])["string_pitches"] == GUITAR["string_pitches"]


def test_update_leaves_a_fret_count_behind_when_switching_to_unfretted(app_env):
    """The usual way to send a contradictory definition: pick a fretted preset,
    switch it to unfretted, and leave the fret count in the form."""
    created = api.create_instrument(make())
    with pytest.raises(HTTPException) as exc_info:
        api.update_instrument(created["id"], make(fretted=False))
    assert exc_info.value.status_code == 422
    assert api.get_instrument(created["id"])["fretted"] is True


def test_updating_an_instrument_that_does_not_exist(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.update_instrument(999, make())
    assert exc_info.value.status_code == 404


def test_update_keeps_created_at(app_env):
    created = api.create_instrument(make())
    updated = api.update_instrument(created["id"], make(name="Renamed"))
    assert updated["created_at"] == created["created_at"]


# ---------------------------------------------------------------- delete


def test_delete_removes_it(app_env):
    created = api.create_instrument(make())
    assert api.delete_instrument(created["id"]) == {"deleted": created["id"]}
    assert api.list_instruments() == []
    with pytest.raises(HTTPException) as exc_info:
        api.get_instrument(created["id"])
    assert exc_info.value.status_code == 404


def test_deleting_it_twice_is_a_404_the_second_time(app_env):
    created = api.create_instrument(make())
    api.delete_instrument(created["id"])
    with pytest.raises(HTTPException) as exc_info:
        api.delete_instrument(created["id"])
    assert exc_info.value.status_code == 404


def test_delete_leaves_the_others_alone(app_env):
    first = api.create_instrument(make(name="A guitar"))
    second = api.create_instrument(make(name="B guitar"))
    api.delete_instrument(first["id"])
    assert [i["id"] for i in api.list_instruments()] == [second["id"]]


# ------------------------------------------------- a score's instrument


def test_a_score_can_reference_an_instrument(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    patched = api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    assert patched["instrument_id"] == guitar["id"]
    assert api.get_score(score_id)["instrument_id"] == guitar["id"]


def test_a_score_cannot_reference_an_instrument_that_does_not_exist(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    with pytest.raises(HTTPException) as exc_info:
        api.patch_score(score_id, api.ScorePatch(instrument_id=999))
    assert exc_info.value.status_code == 404


def test_a_scores_instrument_can_be_cleared(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    cleared = api.patch_score(score_id, api.ScorePatch(instrument_id=None))
    assert cleared["instrument_id"] is None


def test_patching_something_else_leaves_the_instrument_alone(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    patched = api.patch_score(score_id, api.ScorePatch(title="Renamed"))
    assert patched["title"] == "Renamed"
    assert patched["instrument_id"] == guitar["id"]


def test_deleting_an_instrument_leaves_its_scores_in_place(app_env, insert_score):
    """Deleting an instrument means the player no longer has it, not that the
    scores written for it are gone - the reference is dropped, the score stays."""
    conn = db.connect()
    score_id = insert_score(conn, "a.pdf")
    guitar = api.create_instrument(make())
    api.patch_score(score_id, api.ScorePatch(instrument_id=guitar["id"]))
    api.delete_instrument(guitar["id"])
    score = api.get_score(score_id)
    assert score["instrument_id"] is None


def test_the_bounds_the_editor_offers_match_the_ones_the_server_enforces():
    """web/src/lib/instruments.svelte.js mirrors these four bounds so a number
    input can offer the right range. Nothing connects the two at runtime - the
    editor never asks the server what is valid - so if they drift, the form
    accepts a value the server answers with a 422 for no visible reason.
    Parsing the frontend source is the cheapest way to keep them honest,
    following test_settings_api.py's check on the staff themes."""
    js_path = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "instruments.svelte.js"
    )
    source = js_path.read_text(encoding="utf-8")
    found = dict(re.findall(r"export const (\w+) = ([\d.]+);", source))
    assert found.get("MAX_STRINGS") == str(instruments.MAX_STRINGS)
    assert found.get("MAX_FRETS") == str(instruments.MAX_FRETS)
    assert float(found["MIN_REFERENCE_HZ"]) == instruments.MIN_REFERENCE_HZ
    assert float(found["MAX_REFERENCE_HZ"]) == instruments.MAX_REFERENCE_HZ
    assert float(found["DEFAULT_REFERENCE_HZ"]) == instruments.DEFAULT_REFERENCE_HZ


def test_the_instrument_column_is_added_to_a_database_that_predates_it(app_env):
    """SCHEMA's CREATE TABLE IF NOT EXISTS does nothing to a scores table that
    already exists, so the column has to be added explicitly. Simulated by
    dropping it and re-running init_db, which is what an upgrade looks like."""
    conn = db.connect()
    conn.execute("ALTER TABLE scores DROP COLUMN instrument_id")
    conn.commit()
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(scores)")}
    assert "instrument_id" not in columns
    db.init_db()
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(scores)")}
    assert "instrument_id" in columns
