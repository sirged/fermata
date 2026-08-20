import pytest
from fastapi import HTTPException

from fermata import api, db


def test_defaults_when_nothing_is_stored(app_env):
    assert api.get_settings() == {"staff_theme": "parchment"}


def test_write_then_read_back(app_env):
    written = api.put_settings({"staff_theme": "noir"})
    assert written == {"staff_theme": "noir"}
    assert api.get_settings() == {"staff_theme": "noir"}


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
    assert api.get_settings() == {"staff_theme": "parchment"}


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


def test_writing_several_settings_in_one_call(app_env, monkeypatch):
    # Only one real setting exists today; a second is faked in to prove the
    # endpoint's "one or several" contract rather than only ever exercising
    # a single-key dict.
    monkeypatch.setitem(api.SETTINGS_DEFAULTS, "practice_reminder", "off")
    monkeypatch.setitem(api.SETTINGS_CHOICES, "practice_reminder", {"on", "off"})
    result = api.put_settings({"staff_theme": "noir", "practice_reminder": "on"})
    assert result["staff_theme"] == "noir"
    assert result["practice_reminder"] == "on"
    assert api.get_settings() == result
