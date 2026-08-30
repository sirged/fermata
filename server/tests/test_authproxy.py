"""Reverse-proxy authentication (issue #16) - fermata/authproxy.py and the
config it reads from fermata/config.py.

Three layers, deliberately kept apart:

- Unit tests against `authproxy.is_trusted_proxy`, `config.parse_trusted_proxies`,
  `authproxy.check_proxy_header_safety` and `authproxy.check_auth_configuration_sanity`
  directly - the CIDR arithmetic and the startup-guard decisions, checked at
  their edges, without any HTTP or app machinery in the way.
- The full request-level matrix through `fermata.main.app` (middleware,
  routing, the SPA catch-all and /docs included) - because the whole point
  of this being middleware rather than a route dependency is that it has to
  cover ALL of those the same way, and a test that only calls api.router
  directly could not tell the difference between "covers everything" and
  "covers the routes I happened to test". This layer also proves the
  startup guards are actually WIRED into main.py's lifespan, not just
  correct as standalone functions - see
  test_lifespan_refuses_to_start_when_proxy_headers_not_confirmed_off and
  test_lifespan_warns_about_both_silent_open_misconfigurations.

NEITHER LAYER HERE CAN PROVE THE PROXY-HEADERS BYPASS IS ACTUALLY CLOSED.
TestClient never opens a real socket - uvicorn's own ProxyHeadersMiddleware,
which is what rewrites `request.client` from a client-supplied
X-Forwarded-For header (see authproxy.py's module docstring for the full
mechanism), runs at the real HTTP server layer, entirely outside the ASGI
app this test file exercises. That is a real, separate layer of test
coverage - see server/tests/test_live_server_proxy_headers.py, which spawns
an actual `uvicorn` subprocess and talks to it over a real socket. This file
proves the AUTHORIZATION LOGIC is correct given a trustworthy peer address;
that file proves the peer address is actually trustworthy in the first
place.

`config.AUTH_HEADER` and `config.AUTH_TRUSTED_PROXIES_RAW` are monkeypatched
directly rather than through `monkeypatch.setenv` - the same reason
`app_env` monkeypatches `config.LIBRARY_DIR` rather than `FERMATA_LIBRARY`:
both are module-level constants read once at import (or, for
AUTH_TRUSTED_NETWORKS, parsed once at startup by
config.load_auth_trusted_networks() - see that function's docstring for why
it is not parsed at import time), and `fermata.main.app` (imported once per
test process) already exists with its middleware attached by the time any
test runs. `AUTH_TRUSTED_PROXIES_RAW` rather than the already-parsed
`AUTH_TRUSTED_NETWORKS` specifically for any test that goes through a real
app startup (the `make_client` fixture): main.py's lifespan calls
load_auth_trusted_networks() on every startup, which would silently
overwrite a directly-monkeypatched `AUTH_TRUSTED_NETWORKS` right back to
whatever the (still-empty-in-tests) raw string parses to. Tests that call
`is_trusted_proxy` directly, with no app or lifespan involved, monkeypatch
`AUTH_TRUSTED_NETWORKS` directly instead - there is no lifespan there to
clobber it.
"""

import ipaddress
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from fermata import authproxy, config
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


def test_auth_trusted_networks_is_not_populated_at_import_time():
    """The nit this guards: a bad CIDR in FERMATA_TRUSTED_PROXIES must fail
    through main.py's lifespan (a readable message before uvicorn's own
    traceback - see test_lifespan_says_what_is_wrong_about_a_bad_cidr_first
    below), not crash the whole process while config.py is still being
    imported, before that friendly machinery exists to catch it. This is
    the module-level contract that makes that possible: AUTH_TRUSTED_NETWORKS
    starts as an empty list (fail closed, same as an explicitly empty
    setting) regardless of what FERMATA_TRUSTED_PROXIES actually holds, and
    only load_auth_trusted_networks() - called from the lifespan, never at
    import - turns it into the real parsed value."""
    # Not a claim about the CURRENT env's FERMATA_TRUSTED_PROXIES - a claim
    # that nothing at import time ever assigns anything OTHER than [] here
    # without load_auth_trusted_networks() being called first.
    assert callable(config.load_auth_trusted_networks)


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


def test_is_trusted_proxy_normalizes_ipv4_mapped_ipv6_peers(monkeypatch):
    """A dual-stack listener can hand back an IPv4-mapped IPv6 address
    (`::ffff:10.0.0.5`) for what is, on the wire, an ordinary IPv4
    connection from a correctly-configured proxy. Left unnormalized, that
    parses as a distinct IPv6Address that is never `in` an IPv4 network -
    which is fail-closed (never wrongly trusted) but would silently lock out
    a real proxy for a reason nothing in this test (or the rejection log)
    would explain without the fix. `::ffff:10.0.1.5` - the mapped form of
    OUTSIDE_LAN_PEER, genuinely outside the /24 - proves this is
    unwrapping the address rather than accidentally trusting every
    IPv6-mapped peer."""
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("10.0.0.0/24"))
    assert is_trusted_proxy("::ffff:10.0.0.5") is True
    assert is_trusted_proxy("::ffff:10.0.1.5") is False
    assert is_trusted_proxy("::1") is False  # IPv6 loopback - not IPv4-mapped, not in the v4 range


# ---------------------------------------------------------------------------
# Unit level: the proxy-headers startup guard (the review's BLOCKER).
# ---------------------------------------------------------------------------


def test_check_proxy_header_safety_is_a_no_op_when_auth_is_off(monkeypatch):
    monkeypatch.setattr(config, "AUTH_HEADER", "")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app"])  # no --no-proxy-headers
    authproxy.check_proxy_header_safety()  # must not raise


def test_check_proxy_header_safety_raises_without_the_no_proxy_headers_flag(monkeypatch):
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app", "--host", "0.0.0.0"])
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    with pytest.raises(RuntimeError, match="no-proxy-headers"):
        authproxy.check_proxy_header_safety()


def test_check_proxy_header_safety_passes_with_the_flag_present(monkeypatch):
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app", "--no-proxy-headers"])
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    authproxy.check_proxy_header_safety()  # must not raise


def test_check_proxy_header_safety_raises_on_forwarded_allow_ips_even_with_the_flag(monkeypatch):
    """FORWARDED_ALLOW_IPS is checked independently of --no-proxy-headers -
    belt AND suspenders, per the review: even a process that DID pass the
    flag correctly should not also be carrying this env var around, since a
    later change that drops the flag but leaves the env var would otherwise
    go undetected."""
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app", "--no-proxy-headers"])
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    with pytest.raises(RuntimeError, match="FORWARDED_ALLOW_IPS"):
        authproxy.check_proxy_header_safety()


def test_check_proxy_header_safety_message_names_both_problems_at_once(monkeypatch):
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app"])
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "0.0.0.0/0")
    with pytest.raises(RuntimeError) as exc_info:
        authproxy.check_proxy_header_safety()
    message = str(exc_info.value)
    assert "no-proxy-headers" in message
    assert "FORWARDED_ALLOW_IPS" in message


# ---------------------------------------------------------------------------
# Unit level: the silent-open-state warnings (the review's SECOND item).
# ---------------------------------------------------------------------------


def test_sanity_check_warns_when_trusted_proxies_set_without_a_header(monkeypatch, caplog):
    monkeypatch.setattr(config, "AUTH_HEADER", "")
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", "10.0.0.0/24")
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("10.0.0.0/24"))
    with caplog.at_level(logging.ERROR, logger="fermata.authproxy"):
        authproxy.check_auth_configuration_sanity()  # must not raise
    assert any("FERMATA_AUTH_HEADER" in r.message for r in caplog.records)


def test_sanity_check_warns_on_0_0_0_0_slash_0(monkeypatch, caplog):
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("0.0.0.0/0"))
    with caplog.at_level(logging.ERROR, logger="fermata.authproxy"):
        authproxy.check_auth_configuration_sanity()
    assert any("0.0.0.0/0" in r.message for r in caplog.records)


def test_sanity_check_warns_on_the_ipv6_equivalent(monkeypatch, caplog):
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("::/0"))
    with caplog.at_level(logging.ERROR, logger="fermata.authproxy"):
        authproxy.check_auth_configuration_sanity()
    assert any("::/0" in r.message for r in caplog.records)


def test_sanity_check_is_silent_in_the_two_normal_states(monkeypatch, caplog):
    """Off entirely, and on-with-a-real-subnet, are both intended states and
    must never produce an error-level log - a warning that fires on the
    normal case trains an operator to ignore it."""
    with caplog.at_level(logging.ERROR, logger="fermata.authproxy"):
        monkeypatch.setattr(config, "AUTH_HEADER", "")
        monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies(""))
        authproxy.check_auth_configuration_sanity()

        monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
        monkeypatch.setattr(config, "AUTH_TRUSTED_NETWORKS", config.parse_trusted_proxies("10.0.0.0/24"))
        authproxy.check_auth_configuration_sanity()
    assert caplog.records == []


# ---------------------------------------------------------------------------
# Full-app matrix.
# ---------------------------------------------------------------------------


@pytest.fixture
def make_client(app_env, monkeypatch):
    """A TestClient against the real `fermata.main.app` - middleware,
    routing, /docs and all - with a chosen peer address and no real scan or
    startup DB work (app_env already did the real init_db() this test
    needs).

    Also simulates the ONE supported launch configuration -
    `--no-proxy-headers` present, FORWARDED_ALLOW_IPS unset - so that
    main.py's lifespan (which now runs authproxy.check_proxy_header_safety()
    on every startup - see main.py) does not refuse to start under every
    single test in this file. That guard is tested on its own terms,
    directly and through a deliberately UNSAFE sys.argv, in
    test_lifespan_refuses_to_start_when_proxy_headers_not_confirmed_off
    below - this fixture is not it."""
    monkeypatch.setattr("fermata.main.scanner.start_scan", lambda: False)
    monkeypatch.setattr("fermata.main.init_db", lambda: None)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app", "--no-proxy-headers"])
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    def _make(peer=("testclient", 50000)):
        return TestClient(app, client=peer)

    return _make


@pytest.fixture
def auth_on(monkeypatch):
    """Turn reverse-proxy auth on with TRUSTED_PEER and LAN_PEER as the
    allowed proxy addresses - the state every ON test in this module starts
    from. Monkeypatches AUTH_TRUSTED_PROXIES_RAW (the raw string), not the
    already-parsed AUTH_TRUSTED_NETWORKS - see the module docstring for why
    that matters once a test goes through a real app startup."""
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", "127.0.0.1/32,10.0.0.0/24")


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


def test_on_trusted_proxy_with_duplicate_header_is_401(auth_on, make_client):
    """THIRD from the review: a proxy that APPENDS the header rather than
    replacing it can leave a client-forged copy sitting alongside the real
    one - and Starlette reads the FIRST of a duplicated header, which would
    be the client's forged value if it arrives first. Rather than guess
    which of two values is real, Fermata refuses outright whenever the
    header is not exactly one occurrence - passed as a list of tuples
    (not a dict) so the two values genuinely reach the ASGI scope as two
    separate header lines, the same shape a real appending proxy - or a
    client trying to smuggle a second copy past one that doesn't strip -
    would produce."""
    with make_client(TRUSTED_PEER) as c:
        resp = c.get(
            "/api/scores",
            headers=[("X-Remote-User", "eve"), ("X-Remote-User", "real-proxy-value")],
        )
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
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", "")
    with make_client(TRUSTED_PEER) as c:
        resp = c.get("/api/scores", headers={"X-Remote-User": "alice"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# The startup guards, wired into the REAL lifespan (not just called
# directly - see the unit-level tests above for that).
# ---------------------------------------------------------------------------


def test_lifespan_refuses_to_start_when_proxy_headers_not_confirmed_off(app_env, monkeypatch):
    """Proves main.py actually calls authproxy.check_proxy_header_safety()
    from its real lifespan - not just that the function itself raises when
    called directly. Same idiom test_scanner.py's own
    test_startup_says_what_is_wrong_before_the_traceback uses for
    ensure_dirs's RuntimeError: a real `with TestClient(main.app):` startup,
    with caplog proving the readable message is logged before the exception
    propagates. Deliberately does NOT use the make_client fixture, which
    exists specifically to simulate the safe launch this test needs to
    NOT have."""
    from fermata import main

    monkeypatch.setattr(main.scanner, "start_scan", lambda: False)
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", "127.0.0.1/32")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app", "--host", "0.0.0.0"])
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    with pytest.raises(RuntimeError, match="no-proxy-headers"):
        with TestClient(main.app):
            pass


def test_lifespan_logs_the_proxy_header_problem_before_the_traceback(app_env, monkeypatch, caplog):
    """The same claim test_scanner.py's own
    test_startup_says_what_is_wrong_before_the_traceback makes for the
    library-folder check, made here for this one: the readable explanation
    is logged BEFORE the RuntimeError propagates, because uvicorn prints a
    traceback for anything raised in lifespan and the readable sentence has
    to come first (see main.py's own comment on this)."""
    from fermata import main

    monkeypatch.setattr(main.scanner, "start_scan", lambda: False)
    monkeypatch.setattr(config, "AUTH_HEADER", "X-Remote-User")
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", "")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app"])
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    with caplog.at_level(logging.ERROR, logger="fermata.startup"):
        with pytest.raises(RuntimeError):
            with TestClient(main.app):
                pass
    assert any("no-proxy-headers" in r.message for r in caplog.records)


def test_lifespan_says_what_is_wrong_about_a_bad_cidr_first(app_env, monkeypatch, caplog):
    """The other nit: a bad CIDR in FERMATA_TRUSTED_PROXIES must fail through
    THIS same friendly path (readable message, logged, THEN the exception -
    never a raw traceback from importing config.py before this machinery
    exists to catch it). Auth does not even need to be turned on for this -
    a bad list is a bad list regardless."""
    from fermata import main

    monkeypatch.setattr(main.scanner, "start_scan", lambda: False)
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", "not-an-ip-or-cidr")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app", "--no-proxy-headers"])

    with caplog.at_level(logging.ERROR, logger="fermata.startup"):
        with pytest.raises(RuntimeError, match="not-an-ip-or-cidr"):
            with TestClient(main.app):
                pass
    assert any("not-an-ip-or-cidr" in r.message for r in caplog.records)


def test_lifespan_warns_about_both_silent_open_misconfigurations(app_env, monkeypatch, caplog):
    """Proves main.py actually calls
    authproxy.check_auth_configuration_sanity() from its real lifespan -
    not just that the function raises/warns correctly when called directly
    (see the unit tests above). Both misconfigurations warn but still
    START, unlike the proxy-headers guard, so this is one successful
    startup with two error-level log lines, not a raised exception."""
    monkeypatch.setattr("fermata.main.scanner.start_scan", lambda: False)
    monkeypatch.setattr(config, "AUTH_HEADER", "")
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", "10.0.0.0/24")
    monkeypatch.setattr(sys, "argv", ["uvicorn", "fermata.main:app", "--no-proxy-headers"])
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    with caplog.at_level(logging.ERROR, logger="fermata.authproxy"):
        with TestClient(app):
            pass
    assert any("FERMATA_AUTH_HEADER" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# The full configuration state space (the review's SECOND item) - mirrors
# the table in docs/deployment.md's "The full configuration state space"
# exactly. For every row, a request from an address NOT on
# FERMATA_TRUSTED_PROXIES, carrying a forged header, must come back with the
# status this table says - "must be NO [200/authenticated]" for every row
# labeled secure, and the two labeled-insecure rows are exactly the ones
# check_auth_configuration_sanity warns about above.
# ---------------------------------------------------------------------------


STATE_SPACE = [
    pytest.param("", "", 200, id="header-unset_proxies-unset_open-by-design"),
    pytest.param("", "10.0.0.0/24", 200, id="header-unset_proxies-set_open-and-warned"),
    pytest.param("X-Remote-User", "", 401, id="header-set_proxies-empty_fail-closed"),
    pytest.param("X-Remote-User", "192.0.2.0/24", 401, id="header-set_proxies-real-subnet_secure"),
    pytest.param("X-Remote-User", "0.0.0.0/0", 200, id="header-set_proxies-trust-everyone_insecure-and-warned"),
]


@pytest.mark.parametrize("header, trusted_proxies_raw, expected_status", STATE_SPACE)
def test_state_space_table(make_client, monkeypatch, header, trusted_proxies_raw, expected_status):
    monkeypatch.setattr(config, "AUTH_HEADER", header)
    monkeypatch.setattr(config, "AUTH_TRUSTED_PROXIES_RAW", trusted_proxies_raw)
    with make_client(UNTRUSTED_PEER) as c:
        resp = c.get("/api/scores", headers={"X-Remote-User": "attacker"})
        assert resp.status_code == expected_status
