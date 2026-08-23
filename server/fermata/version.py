"""What build is actually running, for GET /api/version.

Surfaced because a self-hosted container updated by rebuild has no auto-update
and no release channel, so a stale deployment is the ordinary failure mode -
and its symptom is always the same misleading one: a feature that was merged
looks like it does not exist, because the running image predates it. This is
the one place that answers "what build am I on?" without guessing from
behaviour (see issue #119).

`version` is read from the installed package's own metadata rather than
parsed a second time out of pyproject.toml by hand - one source of truth,
read the way any installed package's version is read. The fallback below is
for the one case that is not "installed": a bare PYTHONPATH pointed at the
source tree, with nothing pip has ever recorded a distribution for.

`commit` and `built` are never computed here and this module never shells out
to git - there is no repository inside the built image to ask (see the
Dockerfile). Both are supplied at image build time as Docker build args,
landing here as environment variables. Absent - the dev server, or an image
built without them - reads as "dev", the plain admission that nothing was
baked in, rather than an invented commit or a crash.
"""

import os
import tomllib
from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "fermata"

# server/pyproject.toml - one level up from this file.
_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _version_from_pyproject() -> str:
    """The version straight out of pyproject.toml.

    Only reached when metadata.version() found no installed distribution to
    ask - reading the same file by hand rather than inventing a second number
    that could drift from it.
    """
    try:
        with _PYPROJECT.open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return _version_from_pyproject()


def info() -> dict:
    return {
        "version": version(),
        "commit": os.environ.get("FERMATA_BUILD_COMMIT") or "dev",
        "built": os.environ.get("FERMATA_BUILD_DATE") or "dev",
    }
