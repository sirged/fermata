import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, version as version_mod


@pytest.fixture
def client():
    """The router alone, same pattern as test_scanner.py's client fixture -
    this endpoint touches no database and no library, so nothing else about
    the app is needed to reach it."""
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_a_dev_server_reports_dev_for_commit_and_built(client, monkeypatch):
    """The ordinary case for anyone running from source: no image ever baked
    a commit or a date in, so the honest answer is "dev" for both - never an
    invented value and never a 500."""
    monkeypatch.delenv("FERMATA_BUILD_COMMIT", raising=False)
    monkeypatch.delenv("FERMATA_BUILD_DATE", raising=False)
    body = client.get("/api/version").json()
    assert body["commit"] == "dev"
    assert body["built"] == "dev"
    assert body["version"]


def test_a_built_image_reports_its_baked_commit_and_date(client, monkeypatch):
    """What a built image looks like: the two build args land as environment
    variables, and the endpoint reports exactly what was baked in - nothing
    computed, nothing read off a git repository that is not there."""
    monkeypatch.setenv("FERMATA_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("FERMATA_BUILD_DATE", "2026-08-23")
    body = client.get("/api/version").json()
    assert body["commit"] == "abc1234"
    assert body["built"] == "2026-08-23"


def test_version_is_read_from_the_installed_packages_own_metadata():
    """Not a second copy of the number - the same value importlib.metadata
    gives any caller asking what "fermata" resolves to, which is itself read
    from pyproject.toml at install time."""
    assert version_mod.version() == metadata.version("fermata")


def test_version_falls_back_to_pyproject_toml_when_nothing_is_installed(monkeypatch):
    """A bare PYTHONPATH pointed at the source tree - nothing pip ever
    recorded a distribution for - must not crash this endpoint. It reads the
    same file metadata.version() would otherwise be quoting, by hand.

    Compared against pyproject.toml parsed HERE, independently of
    version.py's own parsing helper, so this cannot pass merely by agreeing
    with itself.
    """

    def not_installed(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(version_mod.metadata, "version", not_installed)

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as f:
        expected = tomllib.load(f)["project"]["version"]

    assert version_mod.version() == expected
