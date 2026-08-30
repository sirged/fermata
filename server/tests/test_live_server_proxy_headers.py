"""A real `uvicorn` process, talked to over a real socket - the one layer
server/tests/test_authproxy.py cannot reach (see that file's own module
docstring).

WHY THIS FILE EXISTS. Issue #16's reverse-proxy authentication trusts a
header naming the logged-in user, but only from a request whose peer address
is on an operator-configured allowlist - the peer address being the one
thing a client cannot forge, or so the first version of this feature
assumed. Review found that assumption false: `uvicorn` runs its own
`ProxyHeadersMiddleware` OUTSIDE the ASGI app, by default (`--proxy-headers`,
with `forwarded_allow_ips` defaulting to `127.0.0.1`), and that middleware
REWRITES `scope["client"]` from a client-supplied `X-Forwarded-For` header
before a single line of Fermata's own code - including
RemoteUserAuthMiddleware - ever runs. `TestClient` never opens a socket and
never goes near `uvicorn`'s own server layer at all, so nothing in
test_authproxy.py's 39 tests, all passing, could see this: the bypass was
reproduced for real, against a real `uvicorn fermata.main:app` process with
the ORIGINAL Dockerfile CMD, before the fix (`--no-proxy-headers`, plus the
`authproxy.check_proxy_header_safety` startup guard) existed:

    curl -H 'X-Remote-User: eve' http://127.0.0.1:PORT/api/me
        -> 401 (correct - direct request, no proxy involved)
    curl -H 'X-Remote-User: eve' -H 'X-Forwarded-For: <a-trusted-address>' \\
        http://127.0.0.1:PORT/api/me
        -> 200, {"enabled": true, "username": "eve"}   <- THE BYPASS

The two tests below are the closed version of exactly that experiment,
proving both halves of the fix:

1. test_xff_forgery_does_not_bypass_auth_when_proxy_headers_are_off - with
   `--no-proxy-headers` (what the Dockerfile's CMD and README's dev-run
   command both now pass), the same forged X-Forwarded-For does nothing;
   `scope["client"]` is always the real socket peer.
2. test_server_refuses_to_start_without_confirming_proxy_headers_off -
   WITHOUT that flag, and with reverse-proxy auth turned on, the server
   cannot even reach a state where request 2 above would matter: it refuses
   to start at all. Together, these two prove there is no longer a reachable
   state where the bypass works - the unsafe launch never serves a request,
   and the safe launch is not forgeable.
"""

import http.client
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]  # server/
STARTUP_TIMEOUT = 20.0


def _free_port() -> int:
    """A real ephemeral TCP port, picked the same way the OS would for an
    outgoing connection - bind, read back the assigned port, release it
    immediately. There is a narrow window where something else could grab
    it before uvicorn does; the same risk this project's own
    web/playwright.config.js accepts for its dev-server port, and cheap
    enough that retrying on a bind failure is not worth the complexity."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port: int, path: str, headers: list[tuple[str, str]] | None = None):
    """A raw HTTP GET over http.client - not httpx/TestClient, and
    deliberately so: this needs to send the SAME two headers as two
    genuinely separate wire lines when asked to (see the duplicate-header
    test in test_authproxy.py for why that distinction matters), which
    http.client's `putheader`, called once per (name, value) pair, does
    directly rather than through any library that might normalize or
    collapse repeated header names first."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", path, skip_accept_encoding=True)
        for name, value in headers or []:
            conn.putheader(name, value)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body
    finally:
        conn.close()


def _wait_until_serving(port: int, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = _get(port, "/api/health")
            if status == 200:
                return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            pass
        time.sleep(0.2)
    return False


def _spawn_uvicorn(tmp_path, port: int, extra_args: list[str], env_overrides: dict[str, str]):
    """Launches `python -m uvicorn fermata.main:app` against a throwaway
    library/config pair under tmp_path - the same shape
    web/playwright.config.js's own `webServer` block uses to start a real
    Fermata for the browser test suite, the closest existing precedent in
    this repository for spawning a live server from a test runner."""
    library = tmp_path / "library"
    library.mkdir()
    config_dir = tmp_path / "config"
    env = dict(os.environ)
    env.pop("FORWARDED_ALLOW_IPS", None)  # never let the ambient environment leak this in
    env.update({
        "FERMATA_LIBRARY": str(library),
        "FERMATA_CONFIG": str(config_dir),
        "PYTHONPATH": str(SERVER_ROOT),
    })
    env.update(env_overrides)
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "fermata.main:app",
            "--host", "127.0.0.1", "--port", str(port),
            *extra_args,
        ],
        cwd=str(SERVER_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _terminate(proc: subprocess.Popen) -> str:
    """Stops the process and returns whatever it printed - used both for
    cleanup and, in the refuses-to-start test, as the evidence for WHY it
    exited."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    try:
        return proc.stdout.read() or ""
    except Exception:
        return ""


def test_xff_forgery_does_not_bypass_auth_when_proxy_headers_are_off(tmp_path):
    """The fix, proven against a real socket: `--no-proxy-headers` present
    (what the Dockerfile's CMD and README's dev-run command now both pass),
    reverse-proxy auth on, trusting an address that is NOT this test's own
    loopback peer - mirroring the manual reproduction in this module's
    docstring exactly, but asserting the closed result instead of the open
    one."""
    port = _free_port()
    proc = _spawn_uvicorn(
        tmp_path, port,
        extra_args=["--no-proxy-headers"],
        env_overrides={
            "FERMATA_AUTH_HEADER": "X-Remote-User",
            # An address that will never actually be this test's peer - this
            # test always connects from 127.0.0.1 (loopback), so a
            # SUCCESSFUL forgery would require uvicorn to have rewritten the
            # peer to this address from the X-Forwarded-For header below.
            "FERMATA_TRUSTED_PROXIES": "203.0.113.9/32",
        },
    )
    try:
        assert _wait_until_serving(port), (
            "server did not become healthy in time:\n" + _terminate(proc)
        )

        # Sanity: the forged header ALONE, no X-Forwarded-For at all, is
        # correctly refused - the real peer (loopback) is not on the trust
        # list. Establishes the baseline the next request's result actually
        # means something against.
        status, body = _get(port, "/api/me", headers=[("X-Remote-User", "eve")])
        assert status == 401, body

        # THE BYPASS ATTEMPT: the same forged identity header, plus a
        # forged X-Forwarded-For naming the address FERMATA_TRUSTED_PROXIES
        # actually trusts. With --no-proxy-headers, uvicorn never looks at
        # X-Forwarded-For at all, so scope["client"] stays the real peer
        # (loopback, untrusted) and this must still be refused.
        status, body = _get(
            port, "/api/me",
            headers=[("X-Remote-User", "eve"), ("X-Forwarded-For", "203.0.113.9")],
        )
        assert status == 401, (
            f"XFF forgery bypassed reverse-proxy auth: got {status} {body!r} - "
            "uvicorn's own X-Forwarded-For handling is rewriting the peer address "
            "despite --no-proxy-headers"
        )
    finally:
        _terminate(proc)


def test_server_refuses_to_start_without_confirming_proxy_headers_off(tmp_path):
    """The backstop, proven against a real process: WITHOUT
    --no-proxy-headers, and with reverse-proxy auth turned on,
    authproxy.check_proxy_header_safety() (wired into main.py's lifespan)
    must refuse to start at all - the vulnerable state from this module's
    docstring is not a state a real server can be running in once this
    guard exists, which is what makes the previous test's "safe launch"
    the ONLY reachable one with auth turned on."""
    port = _free_port()
    proc = _spawn_uvicorn(
        tmp_path, port,
        extra_args=[],  # --no-proxy-headers deliberately omitted
        env_overrides={
            "FERMATA_AUTH_HEADER": "X-Remote-User",
            "FERMATA_TRUSTED_PROXIES": "203.0.113.9/32",
        },
    )
    try:
        became_healthy = _wait_until_serving(port, timeout=10.0)
        output = _terminate(proc)
        assert not became_healthy, (
            "server became healthy without --no-proxy-headers while reverse-proxy "
            "auth was on - the startup guard did not fire:\n" + output
        )
        assert "no-proxy-headers" in output, (
            "server exited, but not for the reason this test expects - full output:\n"
            + output
        )
    finally:
        _terminate(proc)
