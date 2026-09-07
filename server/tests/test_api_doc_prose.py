"""Pins docs/api.md's PROSE - not just its route table, which
test_api_docs.py already pins via app.openapi() - to the code it describes
(issue #285).

#274's review found the whole "Two people editing the same score" section
could be deleted from docs/api.md and the existing suite would not notice:
test_api_docs.py never opens the file at all - the only mention of it is a
docstring line naming a section by name. This module is the guard that
closes that gap, the same way test_practice_data_doc.py (#259) closed it for
docs/practice-data.md's tables - anchored by SECTION HEADING and by
identifier, never by line number, so a paragraph moved or inserted above
another cannot silently point a check at the wrong text.

Four checkable claims, each pinned against real code rather than against a
second, hand-copied description of it:

1. Every backticked identifier in a prose paragraph under a route-bearing
   heading is checked against that heading's own routes: a response-model
   field (recursively - a field nested three levels down, like
   GoalOut.progress.sessions_inferred, counts the same as a top-level one,
   since prose almost never says which level a field lives at), a
   request-body field, or a query parameter - resolved from app.openapi(),
   never from a parallel list of "the fields this section should have".
   Values glued on with `: ` or `=` (`` `score_deleted: true` ``,
   `` `transcribed=yes` ``) are split first; only the identifier is checked.
   A short, explicit, commented allow-list (_ALLOWLIST, _SUFFIX_TOKENS
   handling) covers backticked words that are genuinely neither a field, a
   param nor a route: SQL syntax, file names, code references containing a
   dot, illustrative examples ("(imported 2)"). Route mentions themselves
   are resolved against app.openapi() too - a route named in prose that no
   longer exists fails loudly, naming the section and the route.
2. The one status code this module can check without touching server code:
   PUT /api/scores/{id}/transcription's documented `409` (#267) - declared
   through `responses=` in api.py, unlike most of this codebase's other
   409s, which are raised via bare HTTPException and were never added to
   `responses=` (a real gap, but a code change, out of scope for a
   docs-and-tests-only bet). Also checks that the route named beside `422`
   for issue #55's batch selection still exists and still carries a 422
   (FastAPI's own default for any route with a request body, so this mostly
   guards against the route disappearing outright). Issue #58's import
   section names no status code in prose today, so there is nothing for
   this module to pin there.
3. The MCP tool-count sentence agrees with len(mcp_tools.READ_TOOLS).
4. The archive-contents sentence names every server/fermata/api.py
   EXPORT_TABLE_NAMES entry, directly or as a documented group.

Not checked (deliberately, per issue #285's own rabbit-holes list): natural
language, and any sentence that names no backticked identifier, status code
or count. A numeric constant (`practice.MAX_SESSION_LIMIT`,
`trainer.MAX_PRESET_NAME_CHARS`) would be pinned by number here too, the
same way EXPORT_TABLE_NAMES is - but docs/api.md does not currently spell
either constant's value out as a backticked number (it says "own length
cap" without a digit), so there is nothing today for that check to anchor
on; adding one would be a docs rewrite this bet's own no-gos rule out
("no docs rewrite beyond corrections the test forces").
"""

import importlib
import re
from pathlib import Path

import pytest

from fermata import mcp_tools, practice, trainer
from fermata.db import SCHEMA_VERSION
from fermata.main import app as full_app

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "api.md"
API_PY = Path(__file__).resolve().parents[2] / "server" / "fermata" / "api.py"

_BACKTICK = re.compile(r"`([^`\n]+)`")
_FENCE = re.compile(r"```.*?```", re.S)
_HEADING = re.compile(r"^(#{2,3}) (.+)$", re.M)
_ROUTE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S+)$")
_STATUS_CODE = re.compile(r"^[1-5]\d\d$")


@pytest.fixture(scope="module")
def doc_text():
    return DOC_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def openapi_schema():
    return full_app.openapi()


@pytest.fixture(scope="module")
def api_py_text():
    return API_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Anchoring: find a section by its heading text, never by line number.
# ---------------------------------------------------------------------------


def _headings(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), len(m.group(1)), m.group(2)) for m in _HEADING.finditer(text)]


def _section(doc_text: str, heading_text: str) -> str:
    """Every line strictly between the `##`/`###` heading reading exactly
    `heading_text` and the next heading at the same level or shallower - so
    a `##` section's text includes any `###` subsections nested under it.
    Fails loudly, naming the heading, if it is not found exactly once -
    renamed or duplicated, either way this must not silently read from the
    wrong place."""
    headings = _headings(doc_text)
    matches = [i for i, (_, _, text) in enumerate(headings) if text == heading_text]
    assert len(matches) == 1, (
        f"docs/api.md: heading {heading_text!r} not found exactly once "
        f"({len(matches)} matches) - renamed, removed, or duplicated?"
    )
    idx = matches[0]
    start_pos, level, _ = headings[idx]
    start_pos = doc_text.index("\n", start_pos) + 1
    end_pos = len(doc_text)
    for pos, lvl, _ in headings[idx + 1 :]:
        if lvl <= level:
            end_pos = pos
            break
    return doc_text[start_pos:end_pos]


# Every heading this module anchors on - checked together so a renamed
# heading is reported once, clearly, rather than as a pytest.fixture error
# buried inside whichever test happened to need it first.
_ANCHORED_HEADINGS = [
    "Where the contract actually lives",
    "What to expect between releases",
    "The endpoints that write to your files",
    "What a deleted score may still be asked for",
    "Two people editing the same score (issue #267)",
    "Transcribing many scores at once (issue #55)",
    "Getting everything in and out (issue #58)",
    "The fretboard drills (issues #27, #28, #236)",
    "Setlists (issue #6)",
    "Who else reads this contract",
]


def test_every_anchored_heading_is_present(doc_text):
    missing = []
    for heading in _ANCHORED_HEADINGS:
        try:
            _section(doc_text, heading)
        except AssertionError as exc:
            missing.append(str(exc))
    assert not missing, "\n".join(missing)


def test_every_top_level_heading_is_anchored(doc_text):
    """A `##` heading born in docs/api.md after this test was written must
    either join _ANCHORED_HEADINGS (and, ideally, get its own prose-matching
    test) or be refused here by name - so a new section cannot silently ship
    unchecked the way "Where the contract actually lives" and "What to
    expect between releases" originally did (issue #285's review). `###`
    subsections are not required here: "What a deleted score may still be
    asked for" is one, already anchored explicitly above, and this check
    only guards the top level a reader skims."""
    top_level = {text for _, level, text in _headings(doc_text) if level == 2}
    unanchored = top_level - set(_ANCHORED_HEADINGS)
    assert not unanchored, (
        f"docs/api.md has `##` heading(s) {sorted(unanchored)} that "
        "_ANCHORED_HEADINGS does not cover - add each to that list (and, "
        "if it names checkable fields, params or codes, a test for it)"
    )


# ---------------------------------------------------------------------------
# Resolving what a route's fields and parameters actually are, from
# app.openapi() - never from a parallel, hand-kept list.
# ---------------------------------------------------------------------------


def _schema_ref_name(schema_obj: dict) -> str | None:
    if "$ref" in schema_obj:
        return schema_obj["$ref"].rsplit("/", 1)[-1]
    if "items" in schema_obj and isinstance(schema_obj["items"], dict):
        return _schema_ref_name(schema_obj["items"])
    return None


def _all_field_names(components: dict, model_name: str, seen: set | None = None) -> set:
    """Every property name reachable from `model_name` - recursively through
    nested $refs, allOf/anyOf/oneOf and array items - so a field nested
    inside a sub-model (SetlistMemberOut.score.deleted_at) is found the same
    as a top-level one."""
    seen = seen if seen is not None else set()
    if model_name in seen:
        return set()
    seen.add(model_name)
    schema = components.get("schemas", {}).get(model_name)
    if schema is None:
        return set()

    names: set = set()

    def _walk(node):
        if not isinstance(node, dict):
            return
        ref = node.get("$ref")
        if ref:
            names.update(_all_field_names(components, ref.rsplit("/", 1)[-1], seen))
            return
        for combinator in ("allOf", "anyOf", "oneOf"):
            for sub in node.get(combinator, []):
                _walk(sub)
        for prop_name, prop_schema in node.get("properties", {}).items():
            names.add(prop_name)
            _walk(prop_schema)
        items = node.get("items")
        if items:
            _walk(items)

    _walk(schema)
    return names


def _path_matches(doc_path: str, real_path: str) -> bool:
    """docs/api.md writes path parameters as `{id}` for readability
    (`DELETE /api/scores/{id}`) where the real route names its own path
    parameter `{score_id}` - so path segments are compared structurally: any
    two `{...}` segments match each other regardless of the name inside the
    braces, and every other segment must match exactly."""
    doc_parts = doc_path.strip("/").split("/")
    real_parts = real_path.strip("/").split("/")
    if len(doc_parts) != len(real_parts):
        return False
    for d, r in zip(doc_parts, real_parts):
        d_is_param = d.startswith("{") and d.endswith("}")
        r_is_param = r.startswith("{") and r.endswith("}")
        if d_is_param != r_is_param:
            return False
        if not d_is_param and d != r:
            return False
    return True


def _op(schema: dict, method: str, path: str) -> dict:
    paths = schema["paths"]
    candidates = [p for p in paths if _path_matches(path, p)]
    assert candidates, f"docs/api.md names route {method} {path}, which is not in app.openapi()"
    assert len(candidates) == 1, (
        f"docs/api.md names route {method} {path}, which matches more than one real "
        f"route: {candidates}"
    )
    real_path = candidates[0]
    assert method.lower() in paths[real_path], (
        f"docs/api.md names route {method} {path}, which app.openapi() does not answer to that method"
    )
    return paths[real_path][method.lower()]


def _response_fields(schema: dict, method: str, path: str) -> set:
    op = _op(schema, method, path)
    fields: set = set()
    for status, resp in op.get("responses", {}).items():
        if not status.startswith("2"):
            continue
        media = resp.get("content", {}).get("application/json")
        if not media:
            continue
        ref_name = _schema_ref_name(media.get("schema", {}))
        if ref_name:
            fields |= _all_field_names(schema["components"], ref_name)
    return fields


def _request_fields(schema: dict, method: str, path: str) -> set:
    op = _op(schema, method, path)
    body = op.get("requestBody")
    if not body:
        return set()
    media = body.get("content", {}).get("application/json")
    if not media:
        return set()
    ref_name = _schema_ref_name(media.get("schema", {}))
    if not ref_name:
        return set()
    return _all_field_names(schema["components"], ref_name)


def _param_names(schema: dict, method: str, path: str) -> set:
    op = _op(schema, method, path)
    return {p["name"] for p in op.get("parameters", [])}


def _route_universe(schema: dict, routes: list[tuple[str, str]]) -> set:
    """Every field, request field and parameter name any of `routes` know
    about - the "is this identifier real" universe a section's prose is
    checked against."""
    universe: set = set()
    for method, path in routes:
        universe |= _response_fields(schema, method, path)
        universe |= _request_fields(schema, method, path)
        universe |= _param_names(schema, method, path)
    return universe


# ---------------------------------------------------------------------------
# Extracting backticked tokens from a section, and classifying each one.
# ---------------------------------------------------------------------------


def _backticked_tokens(section_text: str) -> list[str]:
    """Every backticked token in `section_text`, with fenced code blocks
    (the JSON example in "Two people editing the same score") stripped
    first - a fenced block's own backtick-free JSON is checked separately,
    by _refuse_stale_edit_matches_the_documented_example below, and single
    backticks never pair up correctly across a fence."""
    return _BACKTICK.findall(_FENCE.sub("", section_text))


# Backticked words that name neither a field, a parameter nor a route -
# kept short and commented, per issue #285's own instruction. Each is here
# because it is one of: SQL syntax, a file/module reference (recognised
# separately below by containing "." or "/"), an HTTP verb mentioned bare,
# a JSON literal, or a one-off illustrative example that names no real
# identifier.
_ALLOWLIST = {
    "If-Match",  # HTTP header convention name, not a field
    "COLLATE NOCASE",  # SQL clause
    "WHERE",  # SQL keyword, mentioned bare
    "GET",  # HTTP verb, mentioned bare ("the matching `GET`s")
    "null",  # JSON literal
    "detail",  # FastAPI's own HTTPException envelope key, not a model field
    "tables",  # the export manifest's own JSON key - checked directly below
    "from",  # free-form key of an ImportOut.trainer_scope_presets_renamed
    "to",  # entry - checked against _derive_preset_renames's own source below
    "reason",  # same
    "{from, to}",  # illustrative shape notation
    "(imported)",
    "(imported 2)",
    "(imported 3)",
    "<name> (imported)",
    "STRASSE",  # collation example - a proper noun, not an identifier
    "trainer_scope_presets row 3",  # illustrative row reference in an error message
    "fifths",  # MusicXML's own attribute name, not one of ours
    # ".." and ".fermata-trash" are NOT listed here - both contain "." and
    # are already caught by _is_code_or_file_ref as file references; kept
    # out to prove that check actually covers them (see
    # test_redundant_allowlist_entries_are_already_classified below).
    "YYYY-MM-DD HH:MM:SS.mmm",  # contains a space, so _is_code_or_file_ref's
    # own space guard would NOT catch this one - it has to stay listed.
    "Straße",  # collation example (a proper noun), paired with STRASSE above
    "SCHEMA_VERSION",  # db.SCHEMA_VERSION - imported at module level below,
    # so an import error (renamed constant) fails this whole module loudly
    # rather than only this one check quietly passing something misspelled.
    "TranscriptionOut",  # bare model-name mention ("...had to be added to
    # that model...`TranscriptionOut`") in "What to expect between
    # releases" - the fields the sentence actually claims (`bars_defective`,
    # `time_signature_source`) are checked against that same model's real
    # properties by test_release_expectations_prose_matches_the_response_model
    # below, which would fail if the model itself were renamed away.
    "'local'",  # illustrative literal value of the `owner` field, in
    # "What to expect between releases" - not an identifier itself.
    "response_model",  # FastAPI's own decorator-kwarg name, in "Where the
    # contract actually lives" - not a response field.
}


def test_redundant_allowlist_entries_are_already_classified():
    """`..` and `.fermata-trash` used to sit in _ALLOWLIST alongside genuinely
    unclassifiable tokens, but both contain a "." and so are already caught
    by _is_code_or_file_ref - proven here directly, rather than trusted,
    so a future change to that function's shape cannot silently make this
    comment wrong."""
    for token in ("..", ".fermata-trash"):
        assert token not in _ALLOWLIST, (
            f"{token!r} is back in _ALLOWLIST, but _is_code_or_file_ref "
            "already classifies it - remove the redundant entry"
        )
        assert _is_code_or_file_ref(token), (
            f"{token!r} is no longer classified as a code/file reference by "
            "_is_code_or_file_ref - it must go back into _ALLOWLIST"
        )


def _is_route_mention(token: str) -> bool:
    return bool(_ROUTE.match(token))


def _is_code_or_file_ref(token: str) -> bool:
    """A token containing '.', '(' or a '/' with no spaces reads as a code
    or file reference (`scanner.hash_file`, `fermata/db.py`,
    `casefold()`, `manifest.json`) rather than an API field name - real API
    field names in this document are always bare snake_case words. The three
    deliberate exceptions this module carves back out - `table.column`,
    `Model.field` and `module.function` - are handled by
    _check_table_column, _check_model_dot_field and _check_module_function
    before this classifier ever runs, so a module that happens to share its
    name with an export table (`instruments` is both) is read as the code
    reference it is rather than forced through the table.column check.

    A lowercase dotted mention of a NESTED response field, like
    `score.deleted_at` (a setlist member's own `score` sub-object, not a
    component named `score`), also matches this shape and is treated as a
    plain code reference rather than independently verified - a narrower,
    documented gap than table.column/Model.field/module.function, since
    `score` names neither a table, a component nor a fermata submodule. Left
    unclosed because `deleted_at` itself is already pinned as a real
    ScoreOut field by the sections that name DELETE /api/scores/{id} and
    POST /api/trash/{score_id}/restore directly, so a rename of that field
    would still fail loudly there - just not by this token."""
    if " " in token:
        return False
    if "(" in token or "." in token or "/" in token:
        return True
    # A bare leading-underscore name (`_insert_row`) is Python's own
    # convention for "private, internal" - never an API field name, which
    # this codebase always spells without one.
    return token.startswith("_")


def _check_module_function(token: str) -> bool:
    """`instruments.normalise` - a module.function reference - is real when
    `function` is an attribute `fermata.<module>` actually defines. Checked
    BEFORE _check_table_column: `instruments` is both an EXPORT_TABLE_NAMES
    entry and a real module (`fermata/instruments.py`), and without this
    check running first, `instruments.normalise` would be forced through the
    table.column check and fail there, since `normalise` is a function, not
    a column of the `instruments` table. Returns False (not handled, so the
    caller falls through to the next check) for anything not shaped
    `module.attr` with `module` a real fermata submodule, INCLUDING a
    `module.attr` pair where the module exists but has no such attribute -
    that case is left for a later check (table.column, here, for the
    `instruments` collision this module actually has to handle) to give its
    own, more specific failure."""
    if "." not in token or " " in token:
        return False
    module_name, _, attr = token.partition(".")
    if "." in attr:
        return False  # e.g. fastapi.routing.run_endpoint_function - not ours
    try:
        module = importlib.import_module(f"fermata.{module_name}")
    except ImportError:
        return False
    return hasattr(module, attr)


def _strip_suffix(token: str) -> str:
    """`score_deleted: true` -> `score_deleted`; `transcribed=yes` ->
    `transcribed`; `source='edited'` -> `source`. The value half of a
    backticked "field: value" or "param=value" example is illustrative, not
    itself a claim this module checks."""
    for sep in (":", "="):
        if sep in token:
            return token.split(sep, 1)[0].strip()
    return token


def _check_table_column(token: str, export_tables: tuple, app_env) -> bool:
    """`trainer_scope_presets.name` - a table.column reference - is real
    when `column` is an actual column of `table`, read back from a freshly
    initialised database (PRAGMA table_info), the same discipline
    test_practice_data_doc.py's test_documented_columns_match_the_real_table
    applies. Returns False (not handled) for anything not matching
    `table.column` shape with `table` a real export table."""
    if "." not in token or " " in token:
        return False
    table, _, column = token.partition(".")
    if table not in export_tables:
        return False
    from fermata import db

    # Not closed here: db.connect() caches one connection per thread
    # (db._local.conn) and this helper may run several times in one test -
    # closing it after the first table.column token would hand the second
    # one a dead connection. app_env's own teardown resets the cache.
    conn = db.connect()
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    assert columns, f"{table}: PRAGMA table_info returned no columns - table missing?"
    assert column in columns, (
        f"docs/api.md names `{token}`, but {table} has no column {column!r} "
        f"(columns: {sorted(columns)})"
    )
    return True


def _check_model_dot_field(token: str, components: dict) -> bool:
    """`ImportOut.trainer_scope_presets_renamed` - a Class.field reference -
    is real when `field` is an actual property of the OpenAPI component
    schema named `Class`. Returns False (not handled) for anything not
    matching that shape."""
    if "." not in token or " " in token:
        return False
    model, _, field = token.partition(".")
    if model not in components.get("schemas", {}):
        return False
    props = components["schemas"][model].get("properties", {})
    assert field in props, (
        f"docs/api.md names `{token}`, but {model} has no field {field!r} "
        f"(fields: {sorted(props)})"
    )
    return True


def _assert_tokens_known(
    section_name: str,
    tokens: list[str],
    universe: set,
    schema: dict,
    export_tables: tuple,
    app_env,
):
    unknown = []
    for raw in tokens:
        if raw in _ALLOWLIST or _is_route_mention(raw):
            continue
        # Module-function reference checked BEFORE table.column: a module
        # that shares its name with an export table (`instruments` is both)
        # must be read as the code reference it is, not forced into a
        # table.column check that fails because a function is not a column.
        if _check_module_function(raw):
            continue
        if _check_table_column(raw, export_tables, app_env):
            continue
        if _check_model_dot_field(raw, schema["components"]):
            continue
        if _is_code_or_file_ref(raw):
            continue
        if _STATUS_CODE.match(raw):
            continue  # checked separately, by name, below
        identifier = _strip_suffix(raw)
        if identifier in universe or identifier in _ALLOWLIST:
            continue
        unknown.append(raw)
    assert not unknown, (
        f"docs/api.md, section {section_name!r}: backticked identifier(s) "
        f"{unknown} match none of this section's routes' response fields, "
        "request fields or query parameters - misspelled, renamed in code, "
        "or missing from this test's allow-list?"
    )


def test_module_function_reference_beats_table_column_check(openapi_schema, app_env):
    """`instruments` is both an EXPORT_TABLE_NAMES entry and a real module
    (`fermata/instruments.py`, which defines `normalise`) - the exact shape
    #290's docs/api.md adds (`instruments.normalise`). Without
    _check_module_function running before _check_table_column, this token
    would be forced through the table.column check and fail, since
    `normalise` is a function, not a column of the `instruments` table.
    `instruments.nonexistent` must still fail - real module, no such
    attribute - falling through to the table.column check, which correctly
    reports `instruments` has no such column either."""
    from fermata.api import EXPORT_TABLE_NAMES

    assert "instruments" in EXPORT_TABLE_NAMES
    _assert_tokens_known(
        "synthetic: module-function beats table-column",
        ["instruments.normalise"],
        set(),
        openapi_schema,
        EXPORT_TABLE_NAMES,
        app_env,
    )
    with pytest.raises(AssertionError):
        _assert_tokens_known(
            "synthetic: module-function beats table-column",
            ["instruments.nonexistent"],
            set(),
            openapi_schema,
            EXPORT_TABLE_NAMES,
            app_env,
        )


# docs/api.md sometimes names a route without its method or `/api` prefix,
# as shorthand for a GET already spelled out in full nearby (`` `practice/
# summary`'s `top_scores` ``) - each of these is exactly the shorthand this
# doc actually uses, not a general path-guessing scheme.
_BARE_PATH_ROUTE_HINTS: dict[str, tuple[str, str]] = {
    "practice/summary": ("GET", "/api/practice/summary"),
    "practice/history": ("GET", "/api/practice/history"),
    "practice/sessions": ("GET", "/api/practice/sessions"),
}


def _routes_named_in(section_text: str) -> list[tuple[str, str]]:
    routes = []
    for token in _backticked_tokens(section_text):
        m = _ROUTE.match(token)
        if m:
            routes.append((m.group(1), m.group(2)))
        elif token in _BARE_PATH_ROUTE_HINTS:
            routes.append(_BARE_PATH_ROUTE_HINTS[token])
    return routes


# ---------------------------------------------------------------------------
# 1. Backticked field/param names, per section.
# ---------------------------------------------------------------------------


def test_contract_overview_prose_names_only_real_routes(doc_text, openapi_schema):
    section_name = "Where the contract actually lives"
    section = _section(doc_text, section_name)
    # `GET /docs` and `GET /openapi.json` are FastAPI's own built-in routes,
    # not ones this app declares - they never appear in app.openapi()'s own
    # paths, so _route_universe (which asserts every route it is given
    # resolves) is not called with them. Both still skip the identifier
    # check below via _is_route_mention, which only asks "does this look
    # like a route", never "does app.openapi() actually serve it" - the
    # right behaviour for the two routes app.openapi() is definitionally
    # unable to describe.
    _assert_tokens_known(section_name, _backticked_tokens(section), set(), openapi_schema, (), None)


def test_release_expectations_prose_matches_the_response_model(doc_text, openapi_schema):
    section_name = "What to expect between releases"
    section = _section(doc_text, section_name)
    routes = _routes_named_in(section)
    universe = _route_universe(openapi_schema, routes)
    # `bars_defective` / `time_signature_source` are named as TranscriptionOut
    # fields directly in prose ("...had to be added to that model...
    # `TranscriptionOut`"), not via a route named by backtick in this section.
    universe = universe | _all_field_names(openapi_schema["components"], "TranscriptionOut")
    # `sessions_inferred` (a goal's, when not countable) and `owner` (exists
    # "in several tables") are both real GoalOut fields - `owner` directly,
    # `sessions_inferred` nested under GoalOut.progress (GoalProgressOut) -
    # checked against that one model rather than allow-listed blind.
    universe = universe | _all_field_names(openapi_schema["components"], "GoalOut")
    _assert_tokens_known(section_name, _backticked_tokens(section), universe, openapi_schema, (), None)


def test_write_endpoints_prose_matches_the_response_models(doc_text, openapi_schema):
    section_name = "The endpoints that write to your files"
    section = _section(doc_text, section_name)
    routes = _routes_named_in(section)
    universe = _route_universe(openapi_schema, routes)
    # `key_fifths` cross-references a transcription's own field ("the same
    # number a transcription's `key_fifths` carries") - the transcription
    # routes are documented in their own section, not named by backtick
    # here, so this is checked against TranscriptionOut directly.
    universe = universe | _all_field_names(openapi_schema["components"], "TranscriptionOut")
    _assert_tokens_known(
        section_name,
        _backticked_tokens(section),
        universe,
        openapi_schema,
        (),
        None,
    )


def test_deleted_score_prose_matches_the_response_models(doc_text, openapi_schema):
    section_name = "What a deleted score may still be asked for"
    section = _section(doc_text, section_name)
    routes = _routes_named_in(section)
    # `file_moved`, `trashed_to`, `file_restored` are ScoreDeleteOut's and
    # ScoreRestoreOut's own fields - those two routes are named by backtick
    # in the PARENT section's endpoint table, not repeated in this nested
    # subsection's own text, so they are added here explicitly rather than
    # left undiscoverable by this subsection's own route mentions alone.
    routes = routes + [("DELETE", "/api/scores/{score_id}"), ("POST", "/api/trash/{score_id}/restore")]
    universe = _route_universe(openapi_schema, routes)
    _assert_tokens_known(section_name, _backticked_tokens(section), universe, openapi_schema, (), None)


def test_transcription_precondition_prose_matches_the_response_model(doc_text, openapi_schema):
    section_name = "Two people editing the same score (issue #267)"
    section = _section(doc_text, section_name)
    routes = _routes_named_in(section)
    universe = _route_universe(openapi_schema, routes)
    _assert_tokens_known(section_name, _backticked_tokens(section), universe, openapi_schema, (), None)


def test_batch_transcription_prose_matches_the_response_model(doc_text, openapi_schema):
    section_name = "Transcribing many scores at once (issue #55)"
    section = _section(doc_text, section_name)
    routes = _routes_named_in(section)
    universe = _route_universe(openapi_schema, routes)
    # `transcriptions` names the table this feature writes to, in prose, not
    # a field of any one route's model - checked directly against the real
    # export table list rather than allow-listed blind.
    from fermata.api import EXPORT_TABLE_NAMES

    assert "transcriptions" in EXPORT_TABLE_NAMES
    universe = universe | {"transcriptions"}
    _assert_tokens_known(section_name, _backticked_tokens(section), universe, openapi_schema, (), None)


def test_import_export_prose_matches_the_response_model(doc_text, openapi_schema, app_env):
    section_name = "Getting everything in and out (issue #58)"
    section = _section(doc_text, section_name)
    # `SCHEMA_VERSION` names db.SCHEMA_VERSION, imported at module level
    # (an import error there - a renamed constant - fails this whole module
    # loudly); it is also, per the prose, distinct from the application's
    # own release number, which is a string, never an int.
    assert isinstance(SCHEMA_VERSION, int)
    routes = _routes_named_in(section)
    universe = _route_universe(openapi_schema, routes)
    from fermata.api import EXPORT_TABLE_NAMES

    universe = universe | set(EXPORT_TABLE_NAMES)
    # `preset_id` cross-references PracticeSessionOut's own field ("a
    # restore repoints all three" - see the named-drill-scopes section for
    # where the sessions/presets routes are actually named) - checked
    # against that model directly rather than allow-listed blind.
    universe = universe | _all_field_names(openapi_schema["components"], "PracticeSessionOut")
    _assert_tokens_known(
        section_name, _backticked_tokens(section), universe, openapi_schema, EXPORT_TABLE_NAMES, app_env
    )


def test_fretboard_drills_prose_matches_the_response_model(doc_text, openapi_schema):
    section_name = "The fretboard drills (issues #27, #28, #236)"
    section = _section(doc_text, section_name)
    routes = _routes_named_in(section)
    universe = _route_universe(openapi_schema, routes)
    # `fretboard` / `chords` name real PracticeSessionIn/Out `activity`
    # values, not response-model field names - checked against the real
    # enumeration rather than allow-listed blind.
    assert "fretboard" in practice.ACTIVITIES
    assert "chords" in practice.ACTIVITIES
    universe = universe | {"fretboard", "chords"}
    _assert_tokens_known(section_name, _backticked_tokens(section), universe, openapi_schema, (), None)


def test_setlists_prose_matches_the_response_model(doc_text, openapi_schema):
    section_name = "Setlists (issue #6)"
    section = _section(doc_text, section_name)
    routes = _routes_named_in(section)
    universe = _route_universe(openapi_schema, routes)
    # `include_trash=false` cross-references GET /api/export's own query
    # parameter, documented in full in the import/export section - checked
    # against that route's real parameters rather than allow-listed blind.
    universe = universe | _param_names(openapi_schema, "GET", "/api/export")
    _assert_tokens_known(section_name, _backticked_tokens(section), universe, openapi_schema, (), None)


# ---------------------------------------------------------------------------
# 2. Status codes named next to a route.
#
# Most of server/fermata/api.py's `raise HTTPException(409, ...)` calls are
# never added to their route's own `responses=`, so they simply are not in
# app.openapi() at all - a real gap, but fixing it is a code change, out of
# scope for this bet (see "Concurrency" in the issue). The one 409 this
# module can check without touching server/fermata/ is the one #267 already
# added to `responses=` on purpose, specifically so it WOULD be visible to a
# reader of the contract - see save_transcription's own docstring. 422 is
# checked more loosely: it is FastAPI's own default for any route with a
# request body, so the only thing worth pinning is that the named route
# still exists at all.
# ---------------------------------------------------------------------------


def test_transcription_put_documents_its_409(doc_text, openapi_schema):
    section = _section(doc_text, "Two people editing the same score (issue #267)")
    assert "`409`" in section, (
        "docs/api.md's #267 section no longer names `409` - the precondition's "
        "refusal is undocumented in prose"
    )
    op = _op(openapi_schema, "PUT", "/api/scores/{score_id}/transcription")
    assert "409" in op.get("responses", {}), (
        "PUT /api/scores/{score_id}/transcription no longer declares a 409 "
        "response in its OpenAPI responses, but docs/api.md still describes one"
    )


def test_batch_selection_422_route_still_exists(doc_text, openapi_schema):
    section = _section(doc_text, "Transcribing many scores at once (issue #55)")
    assert "`422`" in section
    op = _op(openapi_schema, "POST", "/api/transcribe/batch")
    assert "422" in op.get("responses", {})


# ---------------------------------------------------------------------------
# 3. The MCP tool count.
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}


def test_mcp_tool_count_matches_read_tools(doc_text):
    section = _section(doc_text, "Who else reads this contract")
    match = re.search(r"each of its (\w+) read-only tools", section)
    assert match, (
        "docs/api.md's MCP section no longer has a sentence shaped "
        "\"each of its <count> read-only tools\" - the tool-count claim this "
        "test pins has moved or been reworded"
    )
    word = match.group(1)
    if word.isdigit():
        documented_count = int(word)
    else:
        assert word in _NUMBER_WORDS, (
            f"docs/api.md names the tool count as {word!r}, which this test's "
            "number-word table does not know - spell it as a digit, or add the "
            "word to _NUMBER_WORDS"
        )
        documented_count = _NUMBER_WORDS[word]
    assert documented_count == len(mcp_tools.READ_TOOLS), (
        f"docs/api.md says {word!r} read-only tools; mcp_tools.READ_TOOLS has "
        f"{len(mcp_tools.READ_TOOLS)}"
    )


# ---------------------------------------------------------------------------
# 4. The archive's contents name every EXPORT_TABLE_NAMES entry.
# ---------------------------------------------------------------------------

# Every EXPORT_TABLE_NAMES table, and the literal phrase (or one of them)
# that names it, or the documented group it belongs to, in the `GET
# /api/export` row of docs/api.md's endpoint table. Kept here rather than
# derived automatically because the doc deliberately GROUPS several tables
# under one phrase ("both fretboard-drill attempt tables") - matching that
# grouping is the whole point of this check, not something a generic scan
# could infer from the table name alone.
_TABLE_DOC_PHRASES = {
    "scores": "score row",
    "transcriptions": "transcription",
    "practice_sessions": "practice session",
    "practice_goals": "goal",
    "tags": "tag",
    "score_tags": "which tags are on which score",
    "instruments": "instrument",
    "settings": "setting",
    "setlists": "setlist (with its ordered membership)",
    "setlist_scores": "setlist (with its ordered membership)",
    "trainer_attempts": "both fretboard-drill attempt tables (fret positions and chords)",
    "trainer_chord_attempts": "both fretboard-drill attempt tables (fret positions and chords)",
    "trainer_scope_presets": "named drill scope",
    "trainer_scope_preset_strings": "named drill scope",
}


def test_archive_contents_names_every_export_table(doc_text):
    from fermata.api import EXPORT_TABLE_NAMES

    missing_from_map = set(EXPORT_TABLE_NAMES) - set(_TABLE_DOC_PHRASES)
    assert not missing_from_map, (
        f"EXPORT_TABLE_NAMES grew {sorted(missing_from_map)} since this test's "
        "_TABLE_DOC_PHRASES was last updated - name it, or the group it "
        "belongs to, in docs/api.md's `GET /api/export` row, then add it here"
    )
    extra_in_map = set(_TABLE_DOC_PHRASES) - set(EXPORT_TABLE_NAMES)
    assert not extra_in_map, (
        f"_TABLE_DOC_PHRASES names table(s) {sorted(extra_in_map)} that "
        "EXPORT_TABLE_NAMES no longer has"
    )

    section = _section(doc_text, "Getting everything in and out (issue #58)")
    row_match = re.search(r"\| `GET /api/export` \| (.+) \|", section)
    assert row_match, (
        "docs/api.md's `GET /api/export` table row is missing or reshaped - "
        "this test reads the archive's contents from it"
    )
    row_text = row_match.group(1)
    missing_phrases = [
        f"{table!r} (phrase {phrase!r})"
        for table, phrase in _TABLE_DOC_PHRASES.items()
        if phrase not in row_text
    ]
    assert not missing_phrases, (
        "docs/api.md's `GET /api/export` row no longer names every "
        f"EXPORT_TABLE_NAMES entry: {missing_phrases}"
    )


# ---------------------------------------------------------------------------
# Extra: the import-renames vocabulary (`from`, `to`, `reason`, `cleaned`,
# `collision`) is a free-form dict[str, str] on the wire (ImportOut types
# trainer_scope_presets_renamed as list[dict[str, str]], so OpenAPI has no
# named properties to check it against) - pinned instead against the
# literal strings api._derive_preset_renames actually writes.
# ---------------------------------------------------------------------------


def test_import_renames_reason_vocabulary_matches_the_source(doc_text, api_py_text):
    section = _section(doc_text, "Getting everything in and out (issue #58)")
    for word in ("cleaned", "collision"):
        # `reason:\n"cleaned"` - the backtick pair itself wraps across this
        # doc's line width, so \s+ (not a literal space) bridges the gap.
        assert re.search(rf'`reason:\s*"{word}"`', section), (
            f"docs/api.md's import section no longer documents reason: {word!r}"
        )
    func_match = re.search(
        r"def _derive_preset_renames\(.*?(?=\ndef |\Z)", api_py_text, re.S
    )
    assert func_match, "api._derive_preset_renames not found - renamed or moved?"
    func_src = func_match.group(0)
    for literal in ('"reason"', '"cleaned"', '"collision"', '"from"', '"to"'):
        assert literal in func_src, (
            f"docs/api.md documents the import-rename key/value {literal}, but "
            "api._derive_preset_renames's source no longer contains it"
        )


def test_stale_transcription_example_matches_the_source(doc_text, api_py_text):
    """The fenced JSON example in "Two people editing the same score" is
    checked against `_refuse_stale_edit`'s own literal dict, by name rather
    than by line number - the exact keys and the exact error string, not
    merely that SOME 409 exists."""
    section = _section(doc_text, "Two people editing the same score (issue #267)")
    fence_match = re.search(r"```json\n(.*?)\n\s*```", section, re.S)
    assert fence_match, "docs/api.md's #267 section no longer has its fenced JSON example"
    example = fence_match.group(1)
    for key in ('"error"', '"message"', '"updated_at"', '"source"', '"stale_transcription"'):
        assert key in example, f"docs/api.md's #267 JSON example no longer has {key}"

    func_match = re.search(r"def _refuse_stale_edit\(.*?(?=\ndef |\Z)", api_py_text, re.S)
    assert func_match, "api._refuse_stale_edit not found - renamed or moved?"
    func_src = func_match.group(0)
    for key in ('"error"', '"message"', '"updated_at"', '"source"', '"stale_transcription"'):
        assert key in func_src, (
            f"docs/api.md's #267 JSON example names {key}, but "
            "api._refuse_stale_edit's source no longer contains it"
        )
