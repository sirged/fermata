"""The Model Context Protocol server (issue #31) - an open standard for
offering a named set of tools to a program that reads them.

WHAT THIS IS. A second listener, off unless `FERMATA_MCP` is set, that
answers the protocol's `tools/list` with the read tools
`fermata/mcp_tools.py` generated from the served OpenAPI document, and
answers `tools/call` by making the corresponding HTTP request to the REST
API and handing back the response body unchanged.

WHAT THIS IS NOT. It is not a second copy of the API. Every tool call leaves
this process as an ordinary HTTP GET to `FERMATA_MCP_API_URL` and comes back
as bytes; nothing here opens the database, imports `fermata.api`, or knows
what a score is. That is the rule `docs/api.md`'s "Who else reads this
contract" section states, and it is the reason the API's own tests are
enough to know a tool is correct - there is no second implementation for
them to be correct about.

It is also read-only, and structurally so rather than by intention:
`mcp_tools.check_tool_mapping` refuses to build a tool for anything but a
GET, and `mcp_tools.plan_request` refuses an argument the route did not
declare. There is no code path here that can send a POST.

WHY THIS MODULE IS IMPORTED LATE. `fermata/main.py` imports it inside its
lifespan, under the flag, so that with the flag unset the protocol library
is never imported and does not have to be installed. Everything above that
line - `import fermata.main` itself - must keep working in a deployment that
never installed the extra, which `server/tests/test_mcp_server.py` pins.

WHY A THREAD AND NOT A SECOND PROCESS. Fermata ships as one container with
one command. A second process there means a supervisor, a second thing that
can die unnoticed, and a second set of logs; a thread running its own
`uvicorn` on its own port is the smallest arrangement that actually
satisfies "off by default, nothing listens" and "an external client connects
to the running container". It starts and stops with the application, and
when the flag is off it does not exist at all. See docs/deployment.md's "The
Model Context Protocol server" section.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx
import uvicorn
from mcp import types
from mcp.server.lowlevel import Server

from . import mcp_tools
from .mcp_tools import ToolSpec
from .version import version as fermata_version


log = logging.getLogger("fermata.mcp")

# How long a tool's HTTP call to the REST API may take. Generous, because
# `GET /api/scores` on a large library and `GET /api/practice/history` over
# a long history are both genuinely slow the first time, and a truncated
# read reported as a tool error would send a caller looking for a bug that
# is not there.
API_TIMEOUT_SECONDS = 30.0

# How long to wait for the listener to actually bind before giving up. A
# port already in use is the realistic failure, and it is one uvicorn
# reports by exiting its thread - so this is what turns "the flag is on and
# nothing happened" into a startup error naming the port.
BIND_TIMEOUT_SECONDS = 10.0


def _text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=is_error)


def build_server(document: dict, api_base_url: str) -> Server:
    """A protocol server whose tools are generated from `document`.

    Kept separate from `start` so a test can build one, list its tools and
    call them in-process - the tool list and the flag gate are different
    claims and are checked separately.
    """
    specs: list[ToolSpec] = mcp_tools.build_tools(document)
    by_name = {spec.name: spec for spec in specs}
    base = api_base_url.rstrip("/")

    tools = [
        types.Tool(
            name=spec.name,
            description=mcp_tools.tool_description(spec),
            inputSchema=spec.input_schema,
            # Both of these are the same claim said twice, to two different
            # readers. The annotation tells a client this tool does not
            # change anything; the `_meta` entry names the OpenAPI operation
            # it came from, so a client (or a test, which is what actually
            # reads it) can check the tool against `/openapi.json` without
            # having this repository in front of it.
            annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False),
            _meta={"fermata": spec.trace},
        )
        for spec in specs
    ]

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        spec = by_name.get(params.name)
        if spec is None:
            # Named, not merely refused: a caller working from a stale tool
            # list needs to know WHICH name went away, and the list it
            # should have is right there.
            return _text_result(
                f"no tool named {params.name!r} - this server offers {sorted(by_name)}",
                is_error=True,
            )
        try:
            path, query = mcp_tools.plan_request(spec, params.arguments or {})
        except ValueError as exc:
            return _text_result(str(exc), is_error=True)

        url = f"{base}{path}"
        try:
            async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
                response = await client.get(url, params=query)
        except httpx.HTTPError as exc:
            # The REST API not being reachable is a deployment fact, not a
            # bad tool call, and saying which URL was tried is the whole
            # diagnosis: it is almost always FERMATA_MCP_API_URL naming a
            # port uvicorn was not told to serve on.
            return _text_result(
                f"could not reach the REST API at {url}: {exc} - check FERMATA_MCP_API_URL "
                "names the host and port this instance actually serves on.",
                is_error=True,
            )

        if response.status_code >= 400:
            return _text_result(
                f"{spec.method.upper()} {path} returned {response.status_code}: {response.text}",
                is_error=True,
            )
        # The body VERBATIM. Not re-serialised, not reshaped, not summarised:
        # the point of this server is that a tool answers with exactly what
        # the documented route answers, and a test asserts that equality
        # against a plain HTTP request for the same parameters.
        return _text_result(response.text)

    return Server(
        "fermata",
        version=fermata_version(),
        title="Fermata",
        instructions=(
            "Read-only tools over Fermata's documented REST API. Every tool wraps one "
            "documented route and returns its JSON response unchanged; the route each tool "
            "reads is named in the tool's description and in its _meta.fermata entry."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


class Listener:
    """A running protocol server, and the handle used to stop it."""

    def __init__(self, server: uvicorn.Server, thread: threading.Thread, host: str, port: int):
        self._server = server
        self._thread = thread
        self.host = host
        self.port = port

    def stop(self, timeout: float = 10.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            # Not fatal, and deliberately not escalated: the thread is a
            # daemon, so a listener that will not let go cannot keep the
            # process alive past the application's own exit. Worth a line
            # in the log so a slow shutdown is explicable.
            log.warning("the Model Context Protocol listener did not stop within %ss", timeout)


def start(document: dict, *, host: str, port: int, api_base_url: str) -> Listener:
    """Build the server from `document` and start listening on host:port.

    Raises RuntimeError, naming the port, if the listener does not bind -
    which is what an operator who turned this on deserves instead of a
    healthy-looking container with nothing on the port they published.
    """
    server = build_server(document, api_base_url)
    app = server.streamable_http_app(streamable_http_path="/mcp", host=host)
    runner = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    )
    thread = threading.Thread(target=runner.run, name="fermata-mcp", daemon=True)
    thread.start()

    deadline = time.monotonic() + BIND_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if runner.started:
            log.info(
                "Model Context Protocol server listening on http://%s:%s/mcp, reading the REST "
                "API at %s", host, port, api_base_url,
            )
            return Listener(runner, thread, host, port)
        if not thread.is_alive():
            break
        time.sleep(0.05)

    runner.should_exit = True
    raise RuntimeError(
        f"Fermata cannot start: FERMATA_MCP is on, but the Model Context Protocol server "
        f"could not listen on {host}:{port}. Either something else already has that port - "
        "set FERMATA_MCP_PORT to a free one - or FERMATA_MCP_HOST names an address this "
        f"machine does not have ({host!r}); inside a container that is usually 0.0.0.0 or "
        "127.0.0.1 and nothing else. Unsetting FERMATA_MCP turns the feature off. See "
        "docs/deployment.md's 'The Model Context Protocol server' section.\n"
        "\n"
        "Nothing has been changed. Your sheet music and your practice history are both as "
        "they were."
    )


def describe_tools(document: dict) -> str:
    """The tool list as JSON, for `python -m fermata.mcp_server` - a way to
    see exactly what this server would offer without starting it or
    connecting a client. Handy when an operator wants to know what a tool
    caller can see before they publish a port."""
    return json.dumps(
        [
            {
                "name": spec.name,
                "operationId": spec.operation_id,
                "route": f"{spec.method.upper()} {spec.path}",
                "inputSchema": spec.input_schema,
            }
            for spec in mcp_tools.build_tools(document)
        ],
        indent=2,
    )


if __name__ == "__main__":  # pragma: no cover - an operator's convenience
    from .main import app

    print(describe_tools(app.openapi()))
