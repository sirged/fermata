"""Reverse-proxy authentication (issue #16) - fermata/authproxy.py and the
config it reads from fermata/config.py.

Two layers, deliberately kept apart:

- Unit tests against `authproxy.is_trusted_proxy` and
  `config.parse_trusted_proxies` directly - the CIDR arithmetic, checked at
  its edges, without any HTTP or app machinery in the way.
- The full request-level matrix through `fermata.main.app` (middleware,
  routing, the SPA catch-all and /docs included) - because the whole point
  of this being middleware rather than a route dependency is that it has to
  cover ALL of those the same way, and a test that only calls api.router
  directly could not tell the difference between "covers everything" and
  "covers the routes I happened to test".

`config.AUTH_HEADER` and `config.AUTH_TRUSTED_NETWORKS` are monkeypatched
directly rather than through `monkeypatch.setenv` - the same reason
`app_env` monkeypatches `config.LIBRARY_DIR` rather than
`FERMATA_LIBRARY`: both are module-level constants read once at import, and
`fermata.main.app` (imported once per test process) already exists with its
middleware attached by the time any test runs. authproxy.py reads
`config.AUTH_HEADER` / `config.AUTH_TRUSTED_NETWORKS` fresh on every request
rather than caching them at middleware construction, which is exactly what
makes monkeypatching the attribute (rather than the environment variable)
take effect per test.
"""

import ipaddress

import pytest
from fastapi.testclient import TestClient

from fermata import config
from fermata.authproxy import is_trusted_proxy
from fermata.main import app

TRUSTED_PEER = ("127.0.0.1", 443)  # inside 127.0.0.1/32 and 127.0.0.0/8
LAN_PEER = ("10.0.0.5", 443)  # inside 10.0.0.0/24
OUTSIDE_LAN_PEER = ("10.0.1.5", 443)  # NOT inside 10.0.0.0/24
UNTRUSTED_PEER = ("203.0.113.9", 443)  # a real-world unroutable-for-docs address, trusted nowhere


# ---------------------------------------------------------------------------
# Unit level: the CIDR arithmetic itself.
# ---------------------------------------------------------------------------


def test_parse_trusted_proxies_accepts_bare_ips_and_cidr_ranges():
    networks = config.parse_trusted_proxies("127.0.0.1, 10.0.0.0/24  172.20.0.5")
    assert networks == [
        ipaddress.ip_network("127.0.0.1/32"),
        ipaddress.ip_network("10.0.0.0/24"),
        ipaddress.ip_network("172.20.0.5/32"),
    ]


def test_parse_trusted_proxies_rejects_garbage_with_a_readable_message():
    with pytest.raises(RuntimeError, match="not-an-ip"):
        config.parse_trusted_proxies("not-an-ip")


def test_parse_trusted_proxies_of_empty_string_is_an_empty_list():
    """The default (FERMATA_TRUSTED_PROXIES unset) - trusts nothing, which is
    what makes AUTH_HEADER-set-without-this fail closed rather than open."""
    assert config.parse_trusted_proxies("") == []


def test_is_trusted_proxy_cidr_boundary(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("10.0.0.0/24"))
    assert is_trusted_proxy("10.0.0.1") is True
    assert is_trusted_proxy("10.0.0.255") is True  # broadcast address - still IN the range
    assert is_trusted_proxy("10.0.1.0") is False  # one address past the /24
    assert is_trusted_proxy("9.255.255.255") is False


def test_is_trusted_proxy_with_nothing_configured_trusts_nothing(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies(""))
    assert is_trusted_proxy("127.0.0.1") is False


def test_is_trusted_proxy_rejects_none_and_garbage(monkeypatch):
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("127.0.0.1"))
    assert is_trusted_proxy(None) is False
    assert is_trusted_proxy("not-an-ip") is False


# ---------------------------------------------------------------------------
# Full-app matrix.
# ---------------------------------------------------------------------------


@pytest.fixture
def make_client(app_env, monkeypatch):
    """A TestClient against the real `fermata.main.app` - middleware,
    routing, /docs and all - with a chosen peer address and no real scan or
    startup DB work (app_env already did the real init_db() this test
    needs)."""
    monkeypatch.setattr("fermata.main.scanner.start_scan", lambda: False)
    monkeypatch.setattr("fermata.main.init_db", lambda: None)

    def _make(peer=("testclient", 50000)):
        return TestClient(app, client=peer)

    return _make


@pytest.fixture
def auth_on(monkeypatch):
    """Turn reverse-proxy auth on with TRUSTED_PEER and LAN_PEER as the
    allowed proxy addresses - the state every ON test in this module starts
    from."""
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(
        config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("127.0.0.1/32,10.0.0.0/24")
    )


# --- off by default --------------------------------------------------------


def test_off_by_default_everything_is_open_with_no_header_at_all(make_client):
    """The upgrade-safety invariant: FERMATA_AUTH_HEADER unset (the state of
    every existing deployment right after an upgrade that touched nothing)
    must behave exactly as if this feature did not exist."""
    assert config.AUTH_HEADER == ""
    with make_client(UNTRUSTED_PEER) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/settings").status_code == 200
        assert c.get("/api/scores").status_code == 200


def test_off_a_sent_header_is_ignored_for_identity_and_for_authorization(make_client):
    """Decision: when auth is off, a client-supplied header is never read as
    an identity - /api/me reports no one, even though a request carrying
    that header succeeds exactly as any other request does. Anything else
    would mean an operator who merely SET FERMATA_AUTH_HEADER without also
    configuring FERMATA_TRUSTED_PROXIES got silent, unauthenticated
    impersonation instead of the documented fail-closed 401."""
    assert config.AUTH_HEADER == ""
    with make_client(UNTRUSTED_PEER) as c:
        resp = c.get("/api/me", headers={"X-Remote-User": "eve"})
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False, "username": None}


# --- on + trusted + header --------------------------------------------------


def test_on_trusted_proxy_with_header_succeeds_with_literal_identity(auth_on, make_client):
    with make_client(TRUSTED_PEER) as c:
        headers = {"X-Remote-User": "alice"}
        resp = c.get("/api/me", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True, "username": "alice"}

        # And an ordinary route, not just /api/me, actually serves 200 too -
        # health is exempt regardless, settings needs the header like anything
        # else on the trust list does.
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/settings", headers=headers).status_code == 200


def test_on_trusted_lan_subnet_member_with_header_succeeds(auth_on, make_client):
    """LAN_PEER is inside the 10.0.0.0/24 range, not the exact address
    listed - proving the CIDR match, not just an exact-string comparison."""
    with make_client(LAN_PEER) as c:
        resp = c.get("/api/me", headers={"X-Remote-User": "bob"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "bob"


# --- on + trusted + no header -----------------------------------------------


def test_on_trusted_proxy_without_header_is_401(auth_on, make_client):
    with make_client(TRUSTED_PEER) as c:
        resp = c.get("/api/scores")
        assert resp.status_code == 401
        assert "detail" in resp.json()


def test_on_trusted_proxy_with_blank_header_is_401(auth_on, make_client):
    """An empty string is not a username - a proxy misconfigured to set the
    header to '' must not authenticate as an empty-string user."""
    with make_client(TRUSTED_PEER) as c:
        resp = c.get("/api/scores", headers={"X-Remote-User": "   "})
        assert resp.status_code == 401


# --- on + UNTRUSTED + header (the spoof case) -------------------------------


def test_on_untrusted_peer_with_header_is_401_the_spoof_case(auth_on, make_client):
    """THE spoofing hole this feature exists to close: a client that is not
    the configured proxy sets the trusted header itself and must be refused
    exactly as if it had sent nothing - not partially trusted, not logged
    in as whatever name it typed."""
    with make_client(UNTRUSTED_PEER) as c:
        resp = c.get("/api/scores", headers={"X-Remote-User": "attacker"})
        assert resp.status_code == 401

        me = c.get("/api/me", headers={"X-Remote-User": "attacker"})
        # /api/me itself also requires the header to come from a trusted
        # proxy when auth is on - it is a route like any other, not an
        # exemption - so this is 401 too, never a 200 quietly reporting
        # username=null.
        assert me.status_code == 401


def test_on_peer_just_outside_the_trusted_subnet_with_header_is_401(auth_on, make_client):
    """OUTSIDE_LAN_PEER (10.0.1.5) is one address past the configured
    10.0.0.0/24 - the CIDR edge, not just an unrelated address."""
    with make_client(OUTSIDE_LAN_PEER) as c:
        resp = c.get("/api/scores", headers={"X-Remote-User": "attacker"})
        assert resp.status_code == 401


# --- the documented health exemption ----------------------------------------


def test_health_is_exempt_even_on_and_even_from_an_untrusted_peer(auth_on, make_client):
    """Docker's own HEALTHCHECK (see the Dockerfile) hits this with no
    header and no relationship to whatever proxy is configured - it has to
    keep working or turning auth on flips a healthy container to
    'unhealthy' and into a restart loop."""
    with make_client(UNTRUSTED_PEER) as c:
        resp = c.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# --- /docs and /openapi.json: covered when on, unaffected when off ---------


def test_docs_and_openapi_are_open_when_auth_is_off(make_client):
    assert config.AUTH_HEADER == ""
    with make_client(UNTRUSTED_PEER) as c:
        assert c.get("/docs").status_code == 200
        assert c.get("/openapi.json").status_code == 200


def test_docs_and_openapi_require_auth_when_on(auth_on, make_client):
    """Decision (issue #16, invariant 6): /docs and /openapi.json are NOT
    exempt - only /api/health is. Turning auth on locks down the documented
    API surface along with everything else rather than leaving it as the
    one thing still published to anyone who can reach the container."""
    with make_client(UNTRUSTED_PEER) as c:
        assert c.get("/docs").status_code == 401
        assert c.get("/openapi.json").status_code == 401

    with make_client(TRUSTED_PEER) as c:
        assert c.get("/docs", headers={"X-Remote-User": "alice"}).status_code == 200
        assert c.get("/openapi.json", headers={"X-Remote-User": "alice"}).status_code == 200


# --- fail-closed when the header is configured but no proxy is trusted -----


def test_header_configured_with_no_trusted_proxies_locks_out_everyone(make_client, monkeypatch):
    """The specific misconfiguration this is designed to fail SAFE on: an
    operator sets FERMATA_AUTH_HEADER and forgets FERMATA_TRUSTED_PROXIES.
    Every request is refused - including one from what would otherwise look
    like a perfectly normal proxy address - rather than silently trusting
    the header from anywhere."""
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies(""))
    with make_client(TRUSTED_PEER) as c:
        resp = c.get("/api/scores", headers={"X-Remote-User": "alice"})
        assert resp.status_code == 401
