import ipaddress
import os
from pathlib import Path

LIBRARY_DIR = Path(os.environ.get("FERMATA_LIBRARY", "./library")).resolve()
CONFIG_DIR = Path(os.environ.get("FERMATA_CONFIG", "./config")).resolve()
WEB_DIST = os.environ.get("FERMATA_WEB_DIST", "")

CACHE_DIR = CONFIG_DIR / "cache"
DB_PATH = CONFIG_DIR / "fermata.db"

# Reverse-proxy authentication (issue #16) - trust a header naming the
# logged-in user, but ONLY when it was set by something Fermata was told to
# trust. Off by default: AUTH_HEADER empty means the whole feature is a
# no-op and every request behaves exactly as it did before this existed,
# which is what keeps an existing deployment working unattended on upgrade.
#
# Setting FERMATA_AUTH_HEADER alone does not turn anything on for real - see
# fermata/authproxy.py, which refuses to trust ANY request's header until
# FERMATA_TRUSTED_PROXIES also names the proxy allowed to set it. That is a
# deliberate fail-closed default: forgetting to set the trusted-proxy list
# after setting the header name locks every request out (a loud, safe
# failure) rather than silently trusting a header anyone could send by hand
# (the spoofing hole this exists to close). Documented in
# docs/deployment.md's "Reverse proxy authentication" section.
AUTH_HEADER = os.environ.get("FERMATA_AUTH_HEADER", "").strip()
AUTH_TRUSTED_PROXIES_RAW = os.environ.get("FERMATA_TRUSTED_PROXIES", "")


def parse_trusted_proxies(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Turn a comma/whitespace-separated list of IPs and CIDR ranges into
    networks `authproxy.is_trusted_proxy` can test an address against. A bare
    IP (`127.0.0.1`) is treated as a /32 (or /128 for IPv6) - a network of
    exactly one address - via `strict=False`, so operators do not have to
    remember to spell a single address as a range.

    Raises RuntimeError on a token that is neither, naming it and the whole
    setting it came from - this only ever runs at startup, against a value
    someone just typed into an environment variable, so failing loudly there
    beats accepting a typo that silently trusts nothing (or, worse, is later
    misread as trusting everything)."""
    networks = []
    for token in raw.replace(",", " ").split():
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError as exc:
            raise RuntimeError(
                f"FERMATA_TRUSTED_PROXIES entry {token!r} is not a valid IP address or CIDR "
                "range. See docs/deployment.md's 'Reverse proxy authentication' section."
            ) from exc
    return networks


AUTH_TRUSTED_NETWORKS = parse_trusted_proxies(AUTH_TRUSTED_PROXIES_RAW)

# File extensions the scanner picks up, mapped to a broad type used by the UI
# to pick a viewer.
FILE_TYPES = {
    ".pdf": "pdf",
    ".musicxml": "musicxml",
    ".mxl": "musicxml",
    ".gp": "gp",
    ".gp3": "gp",
    ".gp4": "gp",
    ".gp5": "gp",
    ".gpx": "gp",
}


def ensure_dirs() -> None:
    """Create what is ours to create, and refuse to invent what is not.

    THE LIBRARY FOLDER IS NOT CREATED HERE, and that is the whole point of this
    function having a docstring. It used to be, with mkdir(exist_ok=True), and
    the consequence was the worst kind of failure this application can have.

    A library folder that is not there is almost never a first run - it is a
    bind mount that did not appear. The host path was renamed, an external
    drive did not come back, the container started before the mount was ready.
    Creating the folder in that situation does not recover anything; it
    manufactures an empty library, and the startup scan then reads that empty
    library as the truth and reconciles the database down to match it. That is
    how a drive failing to mount came to destroy practice history (#95).

    So the absence is reported as the configuration error it is, and nothing
    starts. That is a loud, obvious, harmless failure, and it is strictly
    better than a quiet destructive one: under `restart: unless-stopped` a
    container that refuses to start simply keeps trying, and recovers by itself
    the moment the mount appears - whereas one that started with an empty
    library has already done the damage by then.

    The config folder IS created, and the difference is not arbitrary. That
    folder is Fermata's own storage - nobody mounts anything into it that
    Fermata did not put there, an empty one is a genuine first run, and there
    is no data it could be shadowing. The library folder is the user's.

    THAT DISTINCTION SURVIVED FERMATA LEARNING TO WRITE TO THE LIBRARY (#56),
    and this paragraph exists because the sentence it replaces - "Fermata only
    ever reads it" - stopped being true. Fermata now moves, renames and deletes
    files in there when a person asks it to, which makes the reasoning above
    stronger rather than weaker: an invented empty library is now somewhere a
    reorganisation could be applied, not merely somewhere an index could be
    reconciled away. What is still true, and is the rule those operations are
    written to, is that Fermata never CREATES the library folder and never
    writes outside it.
    """
    if not LIBRARY_DIR.is_dir():
        what = "is a file, not a folder" if LIBRARY_DIR.exists() else "is not there"
        raise RuntimeError(
            f"Fermata cannot start: its library folder {LIBRARY_DIR} {what}.\n"
            "\n"
            "Fermata will not create this folder, on purpose. A library folder that is "
            "missing is usually a mount that did not appear - a renamed host folder, a "
            "drive that did not come back, a container that started before its volume was "
            "ready. Creating an empty one in that situation would look like a library with "
            "nothing in it, and your index would be reconciled down to match.\n"
            "\n"
            "Check that the folder exists and is readable by the user Fermata runs as. In "
            "Docker that is the volume mapped to /data/library; see docs/deployment.md. "
            "Running from source, FERMATA_LIBRARY names it.\n"
            "\n"
            "Nothing has been changed. Your sheet music and your practice history are both "
            "as they were."
        )
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
