import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from fermata import api, db


# Compared against SETTINGS_DEFAULTS rather than a literal dict of every
# setting: what these tests are about is the store's behaviour - defaulting,
# upserting, rejecting - and spelling out the whole set in each of them means
# adding a preference breaks eight assertions that were never about it. The
# defaults themselves are pinned once, below.
def defaults(**overrides) -> dict:
    return {**api.SETTINGS_DEFAULTS, **overrides}


def test_the_defaults_are_what_a_fresh_install_behaves_as():
    """Pinned deliberately, because these are what an install with nothing
    stored acts on, and every other test here compares against them."""
    assert api.SETTINGS_DEFAULTS == {"staff_theme": "parchment", "week_starts_on": "monday"}
    for key, value in api.SETTINGS_DEFAULTS.items():
        choices = api.SETTINGS_CHOICES.get(key)
        assert choices is None or value in choices, f"{key}'s default is not one of its choices"


def test_defaults_when_nothing_is_stored(app_env):
    assert api.get_settings() == defaults()


def test_write_then_read_back(app_env):
    written = api.put_settings({"staff_theme": "noir"})
    assert written == defaults(staff_theme="noir")
    assert api.get_settings() == defaults(staff_theme="noir")


def test_write_is_an_upsert_not_a_duplicate_row(app_env):
    api.put_settings({"staff_theme": "noir"})
    api.put_settings({"staff_theme": "print"})
    conn = db.connect()
    rows = conn.execute(
        "SELECT value FROM settings WHERE key = 'staff_theme'"
    ).fetchall()
    assert [r["value"] for r in rows] == ["print"]


def test_unknown_key_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.put_settings({"favorite_color": "brass"})
    assert exc_info.value.status_code == 422


def test_unknown_key_alongside_a_known_one_rejects_the_whole_write(app_env):
    """A partially-valid write must not partially apply - the unknown key
    fails the whole call rather than silently dropping just that key."""
    with pytest.raises(HTTPException):
        api.put_settings({"staff_theme": "noir", "bogus": "x"})
    assert api.get_settings() == defaults()


def test_invalid_value_for_a_known_key_is_rejected(app_env):
    with pytest.raises(HTTPException) as exc_info:
        api.put_settings({"staff_theme": "psychedelic"})
    assert exc_info.value.status_code == 422


def test_owner_defaults_to_the_single_instance_owner(app_env):
    api.put_settings({"staff_theme": "noir"})
    conn = db.connect()
    row = conn.execute(
        "SELECT owner FROM settings WHERE key = 'staff_theme'"
    ).fetchone()
    assert row["owner"] == db.DEFAULT_OWNER == "local"


def test_writing_several_settings_in_one_call(app_env):
    result = api.put_settings({"staff_theme": "noir", "week_starts_on": "sunday"})
    assert result["staff_theme"] == "noir"
    assert result["week_starts_on"] == "sunday"
    assert api.get_settings() == result


# ---------------------------------------------------------------------------
# A value that was valid when written can stop being valid later (a theme
# rename, a retired setting). put_settings() can never write such a value
# itself - these simulate one already sitting in the database, as a rename
# or an old client would leave behind.
# ---------------------------------------------------------------------------


def test_a_stored_value_no_longer_in_choices_falls_back_to_the_default(app_env):
    """This PR renames the theme 'slate' to 'noir'. An install upgraded from
    before the rename can have staff_theme='slate' sitting in its database -
    that must read back as the default, not as a value nothing can render."""
    conn = db.connect()
    conn.execute(
        "INSERT INTO settings(owner, key, value) VALUES (?, 'staff_theme', 'slate')",
        (db.DEFAULT_OWNER,),
    )
    conn.commit()
    assert api.get_settings() == defaults()


def test_a_stored_unknown_key_is_omitted_not_surfaced(app_env):
    conn = db.connect()
    conn.execute(
        "INSERT INTO settings(owner, key, value) VALUES (?, 'retired_setting', 'x')",
        (db.DEFAULT_OWNER,),
    )
    conn.commit()
    assert api.get_settings() == defaults()


def test_staff_theme_choices_match_the_frontends_score_themes():
    """SETTINGS_CHOICES['staff_theme'] here and SCORE_THEMES in
    score-render.js are two copies of the same list with nothing connecting
    them at runtime - the frontend never calls this endpoint to ask what's
    valid. If they drift, the settings view offers a theme the server
    rejects with a 422 the interface has no code path to show. Parsing the
    frontend source is the cheapest way to keep them honest without wiring
    a real dependency between a Python service and a Svelte module."""
    js_path = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "score-render.js"
    source = js_path.read_text(encoding="utf-8")
    match = re.search(r'SCORE_THEMES\s*=\s*\[([^\]]*)\]', source)
    assert match, "could not find SCORE_THEMES in score-render.js"
    frontend_themes = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert frontend_themes == api.SETTINGS_CHOICES["staff_theme"]


def test_week_start_choices_match_the_frontends():
    """Same problem, same cheap check. The week a goal is set for is decided
    from this setting, so a value the picker offers and the server rejects
    would leave somebody unable to set a goal at all - and the two lists have
    nothing connecting them at runtime."""
    js_path = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "settings.svelte.js"
    source = js_path.read_text(encoding="utf-8")
    match = re.search(r"WEEK_STARTS\s*=\s*\[([^\]]*)\]", source)
    assert match, "could not find WEEK_STARTS in settings.svelte.js"
    frontend = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert frontend == api.SETTINGS_CHOICES["week_starts_on"]
