"""The Model Context Protocol server (issue #31) - the flag that gates it,
the tool list generated from the OpenAPI document, and a real client talking
to a real listener.

Four claims, each with its own group below.

1. THE FLAG IS THE WHOLE GATE. With FERMATA_MCP unset: nothing listens,
   `import fermata.main` does not import the protocol library, and the
   application starts in a deployment where that library is not installed at
   all. Turning the flag on also does not change the REST contract by one
   byte - the two documents are compared literally.
2. THE TOOL LIST CANNOT DRIFT FROM THE ROUTES. Every tool names an operation
   that is really in the document and really is a GET; every readable route
   in the document is either a tool or recorded, with a reason, as not
   exposed. Renaming a route, adding one, or dropping a tool's mapping each
   fail here, naming the route - which is what issue #31's "Done when" asks
   for. Those three failures are not hypothetical: three tests below perform
   the mutation and assert the red.
3. THE TOOLS ARE READ-ONLY AND CANNOT BE TALKED PAST. A write route cannot
   be made into a tool, an argument the route did not declare is refused,
   and a path argument carrying a slash is encoded rather than obeyed.
4. A GENERIC CLIENT SEES WHAT THIS FILE SAYS IT WILL. The last group starts
   a real server on a free port, connects the protocol's own client library
   over the real transport, lists the tools, and asserts that a tool call
   returns the SAME JSON as an ordinary HTTP request to the route it wraps -
   which is the only assertion that can tell "wraps the route" from
   "reimplements the route badly".

Group 4 needs the optional [mcp] extra and skips without it, saying so. CI
installs it (see .github/workflows/ci.yml) precisely so that group runs
there rather than passing by absence.
"""

import asyncio
import copy
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
import time

import pytest

from fermata import mcp_tools
from fermata.main import app as full_app


SERVER_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "engraved"
STARTUP_TIMEOUT = 40.0

# The whole point of the feature is that this is the only switch.
FLAG = "FERMATA_MCP"

# Every environment variable this feature reads, cleared from a spawned
# server's environment before the test sets what it means to set - so a
# value left in the ambient environment (a developer who exported the flag
# in their shell) cannot make the flag-off tests pass or fail for the wrong
# reason.
FEATURE_ENV = (
    FLAG,
    "FERMATA_MCP_HOST",
    "FERMATA_MCP_PORT",
    "FERMATA_MCP_API_URL",
    # Not this feature's own, but the one setting that changes what it does:
    # with reverse-proxy auth on, the two together are refused at startup
    # (see the group below), so an ambient value would turn every spawn in
    # this file into a test of that refusal instead.
    "FERMATA_AUTH_HEADER",
    "FERMATA_TRUSTED_PROXIES",
)


# ---------------------------------------------------------------------------
# Harness - the same shape as test_live_server_proxy_headers.py's, which is
# this repository's existing precedent for driving a real uvicorn process
# from the test runner.
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """A port nothing is on, obtained by binding zero and letting the OS
    choose. Inherently a small race - something could take it between the
    close here and the bind in the server - and accepted for the same reason
    test_live_server_proxy_headers.py accepts it: retrying on a bind failure
    costs more than it saves. Never a fixed number, and never 8080."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
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


def _accepts_a_connection(port: int, timeout: float = 1.0) -> bool:
    """Whether anything at all is listening on the port - a TCP connect, not
    an HTTP request, because "nothing listens" is a claim about the socket
    and not about what any protocol would answer."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _spawn(tmp_path: Path, port: int, env_overrides: dict, seed_score: bool = False):
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    if seed_score:
        # A real PDF from the committed engraved fixtures, so the library the
        # tools read is not empty - comparing two empty lists would be an
        # equality assertion that could never notice a difference.
        shutil.copy(FIXTURES / "dadgad.pdf", library / "mcp_fixture_score.pdf")
    env = dict(os.environ)
    for name in FEATURE_ENV:
        env.pop(name, None)
    env.pop("FORWARDED_ALLOW_IPS", None)
    env.update({
        "FERMATA_LIBRARY": str(library),
        "FERMATA_CONFIG": str(tmp_path / "config"),
        "PYTHONPATH": str(SERVER_ROOT),
    })
    env.update(env_overrides)
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "fermata.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--no-proxy-headers",
        ],
        cwd=str(SERVER_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _terminate(proc: subprocess.Popen) -> str:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    try:
        return proc.stdout.read() or ""
    except Exception:
        return ""


def _python(snippet: str, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    """Run a snippet in a FRESH interpreter. Import-time claims cannot be
    checked in this process: pytest has already imported fermata, and this
    very module imports the protocol library in group 4."""
    env = dict(os.environ)
    for name in FEATURE_ENV:
        env.pop(name, None)
    env["PYTHONPATH"] = str(SERVER_ROOT)
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(SERVER_ROOT), env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.fixture(scope="module")
def openapi_document():
    return full_app.openapi()


# ---------------------------------------------------------------------------
# 1. The flag is the whole gate.
# ---------------------------------------------------------------------------


def test_the_feature_is_off_unless_the_flag_says_otherwise():
    """Off by default, in a fresh interpreter with the variable genuinely
    absent - not merely off in this test session's imported config."""
    off = _python("from fermata import config; print(config.MCP_ENABLED)")
    assert off.returncode == 0, off.stderr
    assert off.stdout.strip() == "False", off.stdout

    on = _python("from fermata import config; print(config.MCP_ENABLED)", {FLAG: "1"})
    assert on.returncode == 0, on.stderr
    assert on.stdout.strip() == "True", (
        f"{FLAG}=1 did not turn the feature on - the flag is the only switch this feature "
        "has, so this failing means it has no switch at all"
    )


def test_importing_the_application_does_not_import_the_protocol_library():
    """`import fermata.main` must not drag the protocol library in. If it
    did, the optional dependency would be a required one in everything but
    name, and the next test could not pass."""
    result = _python(
        "import sys; import fermata.main; "
        "print('LOADED' if any(m == 'mcp' or m.startswith('mcp.') for m in sys.modules) else 'ABSENT')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "ABSENT", (
        "importing fermata.main imported the protocol library - the import in "
        "main._start_mcp_server has escaped the `if config.MCP_ENABLED` it belongs inside:\n"
        + result.stdout
    )


def test_the_application_imports_with_the_protocol_library_absent():
    """The deployment that never installed the [mcp] extra. Simulated by
    making the import genuinely fail, and the blocker is proven to work
    first - a blocker that silently did nothing would turn this into a test
    that passes on any machine for no reason."""
    snippet = (
        "import sys\n"
        "class Absent:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'mcp' or name.startswith('mcp.'):\n"
        "            raise ModuleNotFoundError(f'No module named {name!r}', name=name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Absent())\n"
        "try:\n"
        "    import mcp\n"
        "except ImportError:\n"
        "    pass\n"
        "else:\n"
        "    print('BLOCKER-INERT'); raise SystemExit(2)\n"
        "import fermata.main\n"
        "from fermata import mcp_tools\n"
        "print(len(mcp_tools.build_tools(fermata.main.app.openapi())))\n"
        "print('IMPORTED')\n"
    )
    result = _python(snippet)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "IMPORTED", result.stdout
    # The tool table is derivable without the protocol library too - that is
    # what keeps group 2 below runnable on a machine without the extra.
    assert lines[-2] == str(len(mcp_tools.READ_TOOLS)), result.stdout


def test_the_flag_on_without_the_extra_says_so_instead_of_a_traceback():
    """Turning the flag on in a source install that never asked for the
    optional extra is an ordinary mistake. It has to arrive as this
    application's own kind of startup failure - a RuntimeError carrying a
    sentence, routed through the lifespan's handler - and not as the bare
    ModuleNotFoundError traceback that sends a worried operator reaching for
    the previous image tag.

    The module is blocked rather than uninstalled, and the blocker raises
    ModuleNotFoundError specifically (what a genuinely absent package
    raises, carrying `.name`) rather than a plain ImportError, so what is
    caught here is what would really happen.
    """
    snippet = (
        "import sys\n"
        "class Absent:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'mcp' or name.startswith('mcp.'):\n"
        "            raise ModuleNotFoundError(f'No module named {name!r}', name=name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Absent())\n"
        "from fermata import config, main\n"
        "config.MCP_ENABLED = True\n"
        "config.MCP_PORT = 1\n"
        "try:\n"
        "    main._start_mcp_server(main.app)\n"
        "except RuntimeError as exc:\n"
        "    print('RUNTIMEERROR')\n"
        "    print(exc)\n"
        "except ModuleNotFoundError:\n"
        "    print('RAW-TRACEBACK-ESCAPED')\n"
        "    raise SystemExit(3)\n"
        "else:\n"
        "    print('NOTHING-RAISED')\n"
        "    raise SystemExit(4)\n"
    )
    result = _python(snippet)
    assert result.returncode == 0, result.stdout + result.stderr
    # `in`, not startswith: importing fermata prints a third-party
    # deprecation warning to stdout first, and that is not this test's
    # business.
    assert "RUNTIMEERROR" in result.stdout, result.stdout
    assert "'mcp' is not installed" in result.stdout, result.stdout
    assert 'server[mcp]' in result.stdout, result.stdout


def test_the_feature_and_reverse_proxy_auth_are_refused_together(tmp_path):
    """Both on is not a working deployment: the tools read the API as an
    anonymous loopback client, so reverse-proxy auth would 401 every one of
    them while the tool list still advertised fourteen. Refusing to start
    says that out loud - see
    authproxy.check_mcp_is_not_configured_behind_proxy_auth for why neither
    workaround is offered instead.
    """
    api_port = _free_port()
    mcp_port = _free_port()
    proc = _spawn(
        tmp_path, api_port,
        {
            FLAG: "1",
            "FERMATA_MCP_PORT": str(mcp_port),
            "FERMATA_MCP_API_URL": f"http://127.0.0.1:{api_port}",
            "FERMATA_AUTH_HEADER": "X-Remote-User",
            "FERMATA_TRUSTED_PROXIES": "203.0.113.9/32",
        },
    )
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "the server started with both the protocol server and reverse-proxy auth on - "
            "every tool would answer 401 while advertising itself as working:\n"
            + _terminate(proc)
        )
    finally:
        output = _terminate(proc)
    # Both variables named, so the log says which two settings to choose
    # between rather than merely that something is wrong.
    assert FLAG in output, output
    assert "FERMATA_AUTH_HEADER" in output, output
    assert not _accepts_a_connection(mcp_port), "a listener was opened before the refusal"
    assert not _accepts_a_connection(api_port), "the API is still serving after that refusal"


def test_reverse_proxy_auth_alone_still_starts(tmp_path):
    """The refusal above must be about the COMBINATION. Reverse-proxy auth
    on its own is a supported deployment and this is what stops the new
    check quietly breaking it."""
    api_port = _free_port()
    proc = _spawn(
        tmp_path, api_port,
        {"FERMATA_AUTH_HEADER": "X-Remote-User", "FERMATA_TRUSTED_PROXIES": "203.0.113.9/32"},
    )
    try:
        assert _wait_until_serving(api_port), (
            "reverse-proxy auth alone stopped the server:\n" + _terminate(proc)
        )
    finally:
        _terminate(proc)


def test_nothing_listens_with_the_flag_off(tmp_path):
    """A real server, really running, with the flag unset: the API answers
    and the port the protocol server WOULD have used is refused.

    The port is reserved by asking the OS for a free one and then telling the
    server that number, so a pass cannot come from having pointed the check
    at an arbitrary closed port - it is the exact port a flag-on run would
    bind.
    """
    api_port = _free_port()
    mcp_port = _free_port()
    proc = _spawn(tmp_path, api_port, {"FERMATA_MCP_PORT": str(mcp_port)})
    try:
        assert _wait_until_serving(api_port), "server never became healthy:\n" + _terminate(proc)
        assert not _accepts_a_connection(mcp_port), (
            f"something is listening on {mcp_port} with {FLAG} unset - the feature is not "
            "off by default"
        )
    finally:
        _terminate(proc)


def test_the_listener_is_there_with_the_flag_on(tmp_path):
    """The other half of the previous test: the same arrangement with the
    flag set does bind that port. Without this, "nothing listens" could be
    passing because nothing ever listens."""
    api_port = _free_port()
    mcp_port = _free_port()
    proc = _spawn(
        tmp_path, api_port,
        {
            FLAG: "1",
            "FERMATA_MCP_PORT": str(mcp_port),
            "FERMATA_MCP_API_URL": f"http://127.0.0.1:{api_port}",
        },
    )
    try:
        assert _wait_until_serving(api_port), "server never became healthy:\n" + _terminate(proc)
        assert _accepts_a_connection(mcp_port), (
            f"{FLAG}=1 but nothing is listening on {mcp_port}:\n" + _terminate(proc)
        )
    finally:
        _terminate(proc)


def test_a_port_already_taken_stops_the_server_loudly(tmp_path):
    """An operator who turned this on and published a port deserves a
    failure, not a healthy-looking container with nothing on that port. The
    port is genuinely occupied here - by a socket this test holds open - so
    the bind really does fail.
    """
    api_port = _free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", 0))
        squatter.listen(1)
        taken = squatter.getsockname()[1]
        proc = _spawn(
            tmp_path, api_port,
            {
                FLAG: "1",
                "FERMATA_MCP_PORT": str(taken),
                "FERMATA_MCP_API_URL": f"http://127.0.0.1:{api_port}",
            },
        )
        try:
            # It must EXIT, not merely fail to serve: a half-started
            # deployment - API up, published protocol port dead - is exactly
            # what the loud failure exists to prevent.
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail(
                "the server kept running even though the protocol server could not bind:\n"
                + _terminate(proc)
            )
        finally:
            output = _terminate(proc)
    assert not _accepts_a_connection(api_port), "the API is still serving after that failure"
    assert str(taken) in output, output
    assert "Model Context Protocol server" in output, output


def test_an_unusable_port_setting_is_harmless_while_the_flag_is_off(tmp_path):
    """The other side of that loudness: a stale, nonsensical value in the
    environment of a deployment that never turned the feature on must not
    stop it starting."""
    api_port = _free_port()
    proc = _spawn(tmp_path, api_port, {"FERMATA_MCP_PORT": "not-a-port"})
    try:
        assert _wait_until_serving(api_port), (
            "a bad FERMATA_MCP_PORT stopped a server that never turned the feature on:\n"
            + _terminate(proc)
        )
    finally:
        _terminate(proc)


def test_the_documented_defaults_are_the_real_ones():
    """docs/deployment.md's settings table names a default port and a
    default host; this is what stops that table becoming fiction."""
    result = _python(
        "from fermata import config; "
        "print(config.MCP_HOST, config.MCP_PORT_RAW, config.MCP_API_URL)"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "127.0.0.1 8765 http://127.0.0.1:8080"


def test_turning_the_flag_on_changes_the_rest_contract_not_at_all(tmp_path):
    """Two real servers, one with the flag off and one with it on, and their
    served OpenAPI documents compared byte for byte.

    This is the promise that makes the feature safe to turn on: it READS the
    document to build its tools and adds nothing to it, so every existing
    client sees the identical contract either way.
    """
    off_port, on_port, mcp_port = _free_port(), _free_port(), _free_port()
    off = _spawn(tmp_path / "off", off_port, {})
    on = _spawn(
        tmp_path / "on", on_port,
        {
            FLAG: "1",
            "FERMATA_MCP_PORT": str(mcp_port),
            "FERMATA_MCP_API_URL": f"http://127.0.0.1:{on_port}",
        },
    )
    try:
        assert _wait_until_serving(off_port), _terminate(off)
        assert _wait_until_serving(on_port), _terminate(on)
        _, off_doc = _get(off_port, "/openapi.json")
        _, on_doc = _get(on_port, "/openapi.json")
        assert off_doc == on_doc, (
            "the served OpenAPI document differs with the feature on - this feature is "
            "supposed to read the contract, not add to it"
        )
        # Said as a number too, so a future failure reports what moved.
        operations = [
            (method, path)
            for path, item in json.loads(off_doc)["paths"].items()
            for method in item
        ]
        assert len(operations) == len(
            [
                (method, path)
                for path, item in json.loads(on_doc)["paths"].items()
                for method in item
            ]
        )
    finally:
        _terminate(off)
        _terminate(on)


# ---------------------------------------------------------------------------
# 2. The tool list cannot drift from the routes.
# ---------------------------------------------------------------------------


def test_every_tool_maps_to_a_read_operation_in_the_document(openapi_document):
    mcp_tools.check_tool_mapping(openapi_document)
    tools = mcp_tools.build_tools(openapi_document)
    assert len(tools) == len(mcp_tools.READ_TOOLS)
    assert all(tool.method == "get" for tool in tools)


def test_every_readable_route_is_either_a_tool_or_recorded_as_not_exposed(openapi_document):
    """The census. A GET added to api.py and left unclassified fails here,
    naming the route, which is what forces the decision rather than letting
    a new endpoint quietly become invisible to tool callers."""
    stray = mcp_tools.unclassified_read_operations(openapi_document)
    assert stray == [], (
        "these readable routes are neither exposed as tools nor recorded in NOT_EXPOSED "
        "with a reason - add each to READ_TOOLS or to NOT_EXPOSED in "
        "fermata/mcp_tools.py:\n  " + "\n  ".join(stray)
    )


def test_renaming_a_route_fails_the_tool_check_naming_it(openapi_document):
    """THE MUTATION, performed. A copy of the document with one operation id
    changed - exactly what renaming a handler or moving a route does - must
    make the check fail, and the message must name the tool that lost its
    route. A guard nobody has watched go red is not a guard."""
    document = copy.deepcopy(openapi_document)
    scores = document["paths"]["/api/scores"]["get"]
    assert scores["operationId"] == mcp_tools.READ_TOOLS["list_scores"]
    scores["operationId"] = "list_scores_api_library_get"

    with pytest.raises(mcp_tools.ToolMappingError) as raised:
        mcp_tools.check_tool_mapping(document)
    message = str(raised.value)
    assert "list_scores" in message and mcp_tools.READ_TOOLS["list_scores"] in message, message


def test_adding_a_route_fails_the_census_naming_it(openapi_document):
    """The other half of "an added or renamed route fails by name": a new
    readable route nobody classified."""
    document = copy.deepcopy(openapi_document)
    document["paths"]["/api/practice/streaks"] = {
        "get": {"operationId": "list_streaks_api_practice_streaks_get", "responses": {}}
    }
    stray = mcp_tools.unclassified_read_operations(document)
    assert stray == ["GET /api/practice/streaks (list_streaks_api_practice_streaks_get)"], stray


def test_dropping_a_tools_mapping_fails_the_census_naming_it(openapi_document, monkeypatch):
    """The third mutation: a tool quietly removed from READ_TOOLS. The
    census notices, because the route it used to cover is now unclassified,
    and it names that route."""
    shortened = dict(mcp_tools.READ_TOOLS)
    dropped = shortened.pop("get_practice_summary")
    monkeypatch.setattr(mcp_tools, "READ_TOOLS", shortened)

    stray = mcp_tools.unclassified_read_operations(openapi_document)
    assert stray == [f"GET /api/practice/summary ({dropped})"], stray


def test_tool_names_and_operation_ids_are_one_to_one(openapi_document):
    """No two tools wrap the same route, and nothing is both exposed and
    recorded as not exposed - either would make the census pass while the
    tool list said something else."""
    ids = list(mcp_tools.READ_TOOLS.values())
    assert len(set(ids)) == len(ids), "two tools name the same operation id"
    overlap = set(ids) & set(mcp_tools.NOT_EXPOSED)
    assert overlap == set(), f"operation ids both exposed and excluded: {sorted(overlap)}"

    from_document = {tool.operation_id: tool.name for tool in mcp_tools.build_tools(openapi_document)}
    assert len(from_document) == len(mcp_tools.READ_TOOLS)


def test_input_schemas_are_generated_from_the_document(openapi_document):
    """Each tool's schema property set is exactly its operation's declared
    path and query parameters - no more (which would be invented) and no
    less (which would hide one)."""
    operations = {
        operation["operationId"]: operation
        for path, item in openapi_document["paths"].items()
        if path.startswith("/api/")
        for operation in item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }
    for tool in mcp_tools.build_tools(openapi_document):
        declared = {
            parameter["name"]
            for parameter in operations[tool.operation_id].get("parameters") or []
            if parameter.get("in") in ("path", "query")
        }
        assert set(tool.input_schema["properties"]) == declared, tool.name
        assert tool.input_schema["additionalProperties"] is False


def test_the_scores_tool_schema_carries_the_routes_own_filters(openapi_document):
    """A named spot-check under the generated one: the library tool really
    does offer search and the filters `GET /api/scores` documents, so
    "generated" cannot mean "generated empty"."""
    tools = {tool.name: tool for tool in mcp_tools.build_tools(openapi_document)}
    scores = tools["list_scores"]
    assert set(scores.input_schema["properties"]) >= {
        "search", "collection", "kind", "tag", "favorite", "practiced",
    }
    assert scores.input_schema["required"] == []
    assert tools["get_score"].input_schema["required"] == ["score_id"]
    assert scores.trace == {
        "operationId": mcp_tools.READ_TOOLS["list_scores"],
        "method": "GET",
        "path": "/api/scores",
    }


# ---------------------------------------------------------------------------
# 3. Read-only, and it cannot be talked past.
# ---------------------------------------------------------------------------


def test_a_write_route_cannot_be_made_into_a_tool(openapi_document, monkeypatch):
    """Read-only is structural here, not a convention: point a tool at a
    POST and the server refuses to build at all, rather than building a tool
    that writes."""
    monkeypatch.setattr(
        mcp_tools, "READ_TOOLS", {"log_practice": "log_practice_api_scores__score_id__practice_post"}
    )
    with pytest.raises(mcp_tools.ToolMappingError) as raised:
        mcp_tools.build_tools(openapi_document)
    assert "read tools only" in str(raised.value)
    assert "POST" in str(raised.value)


def test_an_undeclared_argument_is_refused_by_name(openapi_document):
    tools = {tool.name: tool for tool in mcp_tools.build_tools(openapi_document)}
    with pytest.raises(ValueError) as raised:
        mcp_tools.plan_request(tools["list_scores"], {"limit": 5})
    assert "'limit'" in str(raised.value)


def test_a_missing_required_argument_is_refused_by_name(openapi_document):
    tools = {tool.name: tool for tool in mcp_tools.build_tools(openapi_document)}
    with pytest.raises(ValueError) as raised:
        mcp_tools.plan_request(tools["get_score"], {})
    assert "'score_id'" in str(raised.value)


def test_a_path_argument_cannot_walk_out_of_its_route(openapi_document):
    """A tool call is always a request to the route the tool names. A path
    value carrying slashes and dot segments is percent-encoded, so it stays
    one path segment instead of steering the request somewhere else."""
    tools = {tool.name: tool for tool in mcp_tools.build_tools(openapi_document)}
    path, query = mcp_tools.plan_request(tools["get_score"], {"score_id": "1/../../api/export"})
    assert path == "/api/scores/1%2F..%2F..%2Fapi%2Fexport"
    assert query == {}


def test_a_boolean_argument_is_spelled_the_way_the_route_reads_it(openapi_document):
    tools = {tool.name: tool for tool in mcp_tools.build_tools(openapi_document)}
    _, query = mcp_tools.plan_request(tools["list_scores"], {"favorite": True, "search": "etude"})
    assert query == {"favorite": "true", "search": "etude"}


# ---------------------------------------------------------------------------
# 4. A generic client, over the real transport, against a real server.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """One real Fermata with the feature on, shared by the tests below -
    starting a process per test would triple the runtime of this file for no
    extra claim."""
    pytest.importorskip(
        "mcp",
        reason="the optional [mcp] extra is not installed (pip install -e '.[dev,mcp]')",
    )
    tmp_path = tmp_path_factory.mktemp("mcp_live")
    api_port = _free_port()
    mcp_port = _free_port()
    proc = _spawn(
        tmp_path, api_port,
        {
            FLAG: "1",
            "FERMATA_MCP_PORT": str(mcp_port),
            "FERMATA_MCP_API_URL": f"http://127.0.0.1:{api_port}",
        },
        seed_score=True,
    )
    if not _wait_until_serving(api_port):
        pytest.fail("server never became healthy:\n" + _terminate(proc))
    # The startup scan runs in the background; the equality assertions below
    # are worth much more against a library with something in it.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status, body = _get(api_port, "/api/scores")
        if status == 200 and json.loads(body):
            break
        time.sleep(0.2)
    try:
        yield api_port, mcp_port
    finally:
        _terminate(proc)


def _through_the_client(mcp_port: int, action):
    """Connect a generic protocol client to the listener and run `action`
    against it. `mcp.client.Client` is the protocol's own client library -
    deliberately not a hand-rolled request, so what this proves is that the
    server speaks the protocol rather than that it agrees with itself."""
    from mcp.client import Client

    async def run():
        async with Client(f"http://127.0.0.1:{mcp_port}/mcp") as client:
            return await action(client)

    return asyncio.run(run())


def _tool_json(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_a_client_lists_exactly_the_generated_tools(live_server):
    api_port, mcp_port = live_server

    async def action(client):
        return (await client.list_tools()).tools

    tools = _through_the_client(mcp_port, action)
    assert len(tools) == len(mcp_tools.READ_TOOLS), [tool.name for tool in tools]
    assert {tool.name for tool in tools} == set(mcp_tools.READ_TOOLS)

    # Every listed tool traces to an operation in the document the SAME
    # server is serving - fetched over HTTP here rather than built in
    # process, so the two halves really are the same document.
    _, raw = _get(api_port, "/openapi.json")
    served = {
        operation["operationId"]
        for item in json.loads(raw)["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }
    for tool in tools:
        trace = (tool.meta or {})["fermata"]
        assert trace["operationId"] in served, tool.name
        assert trace["operationId"] == mcp_tools.READ_TOOLS[tool.name]
        assert trace["method"] == "GET"
        assert tool.annotations.read_only_hint is True


def test_the_library_tool_returns_the_same_json_as_the_route(live_server):
    """The claim that this wraps the API rather than reimplementing it,
    stated as an equality anyone can check: the tool's answer and the
    route's answer, for the same parameters, are the same JSON."""
    api_port, mcp_port = live_server

    async def action(client):
        return (
            await client.call_tool("list_scores", {}),
            await client.call_tool("list_scores", {"search": "dadgad"}),
            await client.call_tool("list_scores", {"search": "nothing-matches-this"}),
        )

    everything, matching, empty = _through_the_client(mcp_port, action)

    _, raw_all = _get(api_port, "/api/scores")
    assert _tool_json(everything) == json.loads(raw_all)
    assert json.loads(raw_all), "the seeded library was never scanned - the equality above is vacuous"

    _, raw_search = _get(api_port, "/api/scores?search=dadgad")
    assert _tool_json(matching) == json.loads(raw_search)
    assert len(json.loads(raw_search)) == 1, "the search filter reached the route"

    assert _tool_json(empty) == []


def test_a_score_tool_reaches_its_path_parameter(live_server):
    api_port, mcp_port = live_server
    _, raw_all = _get(api_port, "/api/scores")
    score_id = json.loads(raw_all)[0]["id"]

    async def action(client):
        return (
            await client.call_tool("get_score", {"score_id": score_id}),
            await client.call_tool("get_score_practice_progress", {"score_id": score_id}),
        )

    score, progress = _through_the_client(mcp_port, action)
    _, raw_score = _get(api_port, f"/api/scores/{score_id}")
    _, raw_progress = _get(api_port, f"/api/scores/{score_id}/practice/progress")
    assert _tool_json(score) == json.loads(raw_score)
    assert _tool_json(progress) == json.loads(raw_progress)


def test_the_practice_tools_match_their_routes(live_server):
    api_port, mcp_port = live_server

    async def action(client):
        return (
            await client.call_tool("get_practice_summary", {}),
            await client.call_tool("get_practice_history", {}),
            await client.call_tool("list_goals", {}),
            await client.call_tool("list_trainer_attempts", {}),
        )

    summary, history, goals, attempts = _through_the_client(mcp_port, action)
    for result, path in (
        (summary, "/api/practice/summary"),
        (history, "/api/practice/history"),
        (goals, "/api/practice/goals"),
        (attempts, "/api/trainer/attempts"),
    ):
        _, raw = _get(api_port, path)
        assert _tool_json(result) == json.loads(raw), path


def test_a_write_route_is_neither_offered_nor_callable(live_server):
    """No tool wraps a write route, and asking for one by the name it would
    have had is refused rather than guessed at."""
    _, mcp_port = live_server

    async def action(client):
        listed = (await client.list_tools()).tools
        return listed, await client.call_tool("log_practice", {"score_id": 1, "seconds": 60})

    tools, refused = _through_the_client(mcp_port, action)
    assert "log_practice" not in {tool.name for tool in tools}
    assert refused.is_error
    assert "log_practice" in refused.content[0].text


def test_an_argument_the_route_never_declared_is_refused_over_the_wire(live_server):
    """The same refusal as the in-process test above, but reached through
    the real transport - the guard is in the request path, not only in a
    helper a test happens to call."""
    _, mcp_port = live_server

    async def action(client):
        return await client.call_tool("list_scores", {"order_by": "title; drop table scores"})

    result = _through_the_client(mcp_port, action)
    assert result.is_error
    assert "order_by" in result.content[0].text
