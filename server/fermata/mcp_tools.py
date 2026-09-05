"""The tool list, derived from the served OpenAPI document (issue #31).

THIS MODULE DELIBERATELY IMPORTS NOTHING FROM THE PROTOCOL LIBRARY, and
nothing from `fermata.api` either. It is the half of the Model Context
Protocol server that is pure data: which documented routes are exposed as
tools, what each tool's input schema is, and what HTTP request a tool call
turns into. `fermata/mcp_server.py` is the half that speaks the protocol and
makes the request.

The split is what lets `server/tests/test_mcp_server.py` check the tool list
against the OpenAPI document in an ordinary test run, with no protocol
library installed and no server listening - and it is what keeps the promise
in `docs/api.md` that this layer wraps the REST API rather than
reimplementing it. Nothing here knows how a score is stored or how practice
totals are computed; it knows a route's name, its parameters, and how to
spell a URL.

WHY THE SCHEMAS ARE GENERATED. A tool schema written by hand is a second
copy of the contract, and a second copy drifts: a route grows a query
parameter, the tool does not, and a client is told something false about a
surface that still validates. So `build_tools` reads the document the server
already serves at `/openapi.json` - the same one Swagger UI and any codegen
read, the one `server/tests/test_api_docs.py` pins as valid and complete -
and turns each named operation's own `parameters` into the tool's
`inputSchema`. The only thing written by hand is WHICH operations are
exposed, and that hand-written part is itself checked against the document
by `check_tool_mapping` and `unclassified_read_operations`, so an added or
renamed route fails by name rather than passing silently.

WHY THE READ SET IS AN ALLOWLIST AND NOT "EVERY GET". Two reasons. A GET is
not automatically something worth handing a tool caller - `GET /api/export`
is the entire library as one download, `GET /api/scores/{id}/file` is a PDF -
and, more importantly, an allowlist with a matching NOT_EXPOSED census turns
"somebody added a route" into a test failure that names the route and forces
a decision, which is exactly what issue #31's "Done when" asks for. A
denylist would silently expose whatever arrives next.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from urllib.parse import quote

# Only routes under this prefix are considered at all. The application also
# serves the built frontend from a catch-all route when FERMATA_WEB_DIST is
# set, which shows up in the document as `/{full_path}`; that is the SPA
# shell, not part of the REST contract, and scoping to the API prefix keeps
# the census below from having to know that.
API_PREFIX = "/api/"

# The first tool set, exactly as issue #31 names it: list and search scores,
# read a score's metadata and transcription status, read practice history
# and summaries, read goals, read trainer attempts.
#
# Keys are tool names - short, stable, and chosen for a caller reading a
# tool list. Values are OPERATION IDS from the OpenAPI document, which is
# what makes the mapping checkable: FastAPI derives an operation id from the
# handler's function name and route, so renaming either changes the id and
# `check_tool_mapping` fails naming the tool that lost its route.
READ_TOOLS: dict[str, str] = {
    "list_scores": "list_scores_api_scores_get",
    "get_score": "get_score_api_scores__score_id__get",
    "get_score_transcription": "get_transcription_api_scores__score_id__transcription_get",
    "get_score_transcription_analysis": (
        "get_transcription_analysis_api_scores__score_id__transcription_analysis_get"
    ),
    "get_score_practice": "get_practice_api_scores__score_id__practice_get",
    "get_score_practice_progress": (
        "score_practice_progress_api_scores__score_id__practice_progress_get"
    ),
    "list_practice_sessions": "list_sessions_api_practice_sessions_get",
    "get_practice_summary": "practice_summary_api_practice_summary_get",
    "get_practice_history": "practice_history_api_practice_history_get",
    "list_goals": "list_goals_api_practice_goals_get",
    "get_current_goal": "current_goal_api_practice_goals_current_get",
    "list_trainer_attempts": "list_trainer_attempts_api_trainer_attempts_get",
    "list_trainer_chord_attempts": "list_trainer_chord_attempts_api_trainer_chord_attempts_get",
    # Issue #236's named drill scopes. A READ, so the no-write rule this set
    # is built on is untouched - and it earns its place rather than merely
    # passing the census: practice_sessions now carries `preset_id`, which
    # list_practice_sessions hands back, and without this a caller reading
    # that number has no way to learn what scope it names. Recording it in
    # NOT_EXPOSED would have left the practice history describing itself with
    # an id nothing in the tool set can resolve.
    "list_trainer_presets": "list_trainer_presets_api_trainer_presets_get",
}

# Every OTHER readable route under /api, each with the reason it is not a
# tool in this first set. This is not documentation for its own sake:
# `unclassified_read_operations` requires every GET in the document to
# appear either here or in READ_TOOLS, so a route added to api.py without a
# decision recorded here fails the test by name. Deleting an entry from
# here without deleting the route fails the same way.
NOT_EXPOSED: dict[str, str] = {
    "health_api_health_get": "liveness, not library or practice data",
    "get_version_api_version_get": "build identity, not library or practice data",
    "get_me_api_me_get": (
        "identity is inert (issue #31 leaves it that way) - a tool here would report "
        "nothing a caller could act on"
    ),
    "get_settings_api_settings_get": "deployment configuration, not library or practice data",
    "list_instruments_api_instruments_get": "instrument setup is not in the first tool set",
    "list_instrument_presets_api_instruments_presets_get": (
        "instrument setup is not in the first tool set"
    ),
    "get_instrument_api_instruments__instrument_id__get": (
        "instrument setup is not in the first tool set"
    ),
    "list_collections_api_collections_get": "library organisation view, not in the first tool set",
    "list_tags_api_tags_get": "library organisation view, not in the first tool set",
    "list_duplicates_api_duplicates_get": "library maintenance view, not in the first tool set",
    "list_trash_api_trash_get": "library maintenance view, not in the first tool set",
    "list_folders_api_library_folders_get": (
        "library organisation view, not in the first tool set"
    ),
    "list_setlists_api_setlists_get": "setlists are not in the first tool set",
    "get_setlist_api_setlists__setlist_id__get": "setlists are not in the first tool set",
    "get_scan_status_api_scan_status_get": "background job state, not in the first tool set",
    "get_transcribe_batch_status_api_transcribe_batch_status_get": (
        "background job state, not in the first tool set"
    ),
    "export_library_api_export_get": (
        "the whole library as one archive download - a bulk file transfer, not a read tool"
    ),
    "get_file_api_scores__score_id__file_get": "serves the score file itself, not JSON",
    "get_thumb_api_scores__score_id__thumb_get": "serves a thumbnail image, not JSON",
    "practice_review_api_practice_review_get": (
        "answers 'what should I practise next', which is a step past the history and "
        "summaries this first tool set covers"
    ),
}


class ToolMappingError(Exception):
    """Raised when READ_TOOLS and the OpenAPI document disagree.

    Its message always names the tool and the operation id involved, because
    the only way anyone reads this exception is as a test failure telling
    them which route they just added, renamed or removed.
    """


@dataclass(frozen=True)
class ToolSpec:
    """One tool, fully derived from one OpenAPI operation."""

    name: str
    operation_id: str
    method: str
    path: str
    """The OpenAPI path template, e.g. `/api/scores/{score_id}`."""
    summary: str
    description: str
    input_schema: dict
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]

    @property
    def trace(self) -> dict[str, str]:
        """What the tool carries in `_meta` so a client can check for itself
        that the tool came from a documented route: the operation id, the
        method and the path template. Issue #31 asks that every tool's
        schema be traceable to an OpenAPI operation; this is that trace,
        travelling with the tool rather than living only in this file."""
        return {"operationId": self.operation_id, "method": self.method.upper(), "path": self.path}


def _operations(document: dict) -> dict[str, tuple[str, str, dict]]:
    """Index the document's `/api` operations by operation id, as
    `{operation_id: (method, path, operation)}`."""
    found: dict[str, tuple[str, str, dict]] = {}
    for path, item in (document.get("paths") or {}).items():
        if not path.startswith(API_PREFIX):
            continue
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if operation_id:
                found[operation_id] = (method.lower(), path, operation)
    return found


def check_tool_mapping(document: dict) -> None:
    """Every tool names an operation that is really in the document, and
    really is a read. Raises ToolMappingError naming the first tool that
    does not.

    Renaming a route is what this catches in practice: FastAPI's operation
    id contains both the handler's name and the route's path, so either kind
    of rename lands here rather than in a client's lap.
    """
    operations = _operations(document)
    for name, operation_id in READ_TOOLS.items():
        if operation_id not in operations:
            raise ToolMappingError(
                f"tool {name!r} maps to operation id {operation_id!r}, which is not in the "
                "OpenAPI document - the route was renamed, moved or removed. Update "
                "READ_TOOLS in fermata/mcp_tools.py to match, or drop the tool."
            )
        method, path, _ = operations[operation_id]
        if method != "get":
            raise ToolMappingError(
                f"tool {name!r} maps to {method.upper()} {path} - this server exposes read "
                "tools only (issue #31's no-gos), so a tool may only wrap a GET."
            )


def unclassified_read_operations(document: dict) -> list[str]:
    """Readable `/api` operations that are neither exposed as a tool nor
    recorded in NOT_EXPOSED, as `"GET /api/..."` strings.

    A non-empty result means somebody added a route and nobody decided
    whether a tool caller should see it. The strings name the route so the
    failure reads as an instruction rather than a count.
    """
    stray = []
    for operation_id, (method, path, _) in sorted(_operations(document).items(), key=lambda e: e[1][1]):
        if method != "get":
            continue
        if operation_id in READ_TOOLS.values() or operation_id in NOT_EXPOSED:
            continue
        stray.append(f"{method.upper()} {path} ({operation_id})")
    return stray


def _input_schema(operation: dict) -> tuple[dict, tuple[str, ...], tuple[str, ...]]:
    """Turn an operation's declared parameters into a JSON Schema object,
    plus the path and query parameter names in declaration order.

    The parameter's own `schema` is copied through verbatim - type, default,
    enum, title, whatever FastAPI derived from the handler's signature - so
    the tool tells a caller exactly what the route tells a codegen. Copied,
    not referenced, because the document is a live object here and a tool
    schema handed to a client must not be a window onto it.
    """
    properties: dict[str, dict] = {}
    required: list[str] = []
    path_params: list[str] = []
    query_params: list[str] = []
    for parameter in operation.get("parameters") or []:
        name = parameter.get("name")
        location = parameter.get("in")
        if not name or location not in ("path", "query"):
            # Header and cookie parameters are not something a tool caller
            # supplies - this server talks to the API on loopback and sends
            # nothing on a caller's behalf.
            continue
        schema = copy.deepcopy(parameter.get("schema") or {})
        if parameter.get("description") and "description" not in schema:
            schema["description"] = parameter["description"]
        properties[name] = schema
        if parameter.get("required"):
            required.append(name)
        (path_params if location == "path" else query_params).append(name)
    input_schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        # A tool call carrying an argument the route never declared is a
        # mistake worth reporting, not something to quietly drop - and
        # refusing it here is also what stops an unexpected key from being
        # smuggled into the outgoing URL.
        "additionalProperties": False,
    }
    return input_schema, tuple(path_params), tuple(query_params)


def build_tools(document: dict) -> list[ToolSpec]:
    """The whole tool list, generated from the document.

    Called once at startup with `app.openapi()`. Raises ToolMappingError
    (via check_tool_mapping) rather than silently shipping a shorter list if
    the document and READ_TOOLS have parted company - a server that quietly
    served thirteen tools where fourteen were meant is the failure this whole
    design exists to make impossible.
    """
    check_tool_mapping(document)
    operations = _operations(document)
    tools = []
    for name, operation_id in READ_TOOLS.items():
        method, path, operation = operations[operation_id]
        input_schema, path_params, query_params = _input_schema(operation)
        summary = (operation.get("summary") or "").strip()
        description = (operation.get("description") or "").strip()
        tools.append(
            ToolSpec(
                name=name,
                operation_id=operation_id,
                method=method,
                path=path,
                summary=summary,
                description=description,
                input_schema=input_schema,
                path_params=path_params,
                query_params=query_params,
            )
        )
    return tools


def tool_description(spec: ToolSpec) -> str:
    """What a caller sees when it lists the tools: the route, then the
    route's own documentation. The route line is first on purpose - it is
    the sentence that makes the tool traceable to the contract, and it is
    the one thing a caller can check against `/openapi.json` by hand."""
    head = f"Reads {spec.method.upper()} {spec.path}."
    body = spec.description or spec.summary
    return f"{head}\n\n{body}".strip()


def plan_request(spec: ToolSpec, arguments: dict) -> tuple[str, dict[str, str]]:
    """Turn a tool call's arguments into a path and a query string mapping.

    Raises ValueError, naming the argument, on anything the route did not
    declare or anything required that is missing. Path values are
    percent-encoded with no safe characters at all, so a value carrying a
    slash or a `..` cannot walk out of the route it was given to - the
    request this server makes is always the route the tool names.
    """
    declared = set(spec.path_params) | set(spec.query_params)
    for key in arguments:
        if key not in declared:
            raise ValueError(
                f"tool {spec.name!r} has no argument {key!r} - "
                f"{spec.method.upper()} {spec.path} declares {sorted(declared)}"
            )
    for key in spec.input_schema["required"]:
        if key not in arguments:
            raise ValueError(f"tool {spec.name!r} requires argument {key!r}")

    path = spec.path
    for key in spec.path_params:
        path = path.replace("{" + key + "}", quote(_as_text(arguments[key]), safe=""))

    query = {
        key: _as_text(arguments[key]) for key in spec.query_params if arguments.get(key) is not None
    }
    return path, query


def _as_text(value) -> str:
    """A JSON argument as a query/path string. `True` has to become `true`,
    not `True`: FastAPI parses the lowercase spelling and 422s on the other
    one, and `favorite=True` silently failing would be a confusing way to
    learn that."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
