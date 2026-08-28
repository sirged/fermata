# The REST API

Fermata's own web frontend is one client of a REST API under `/api`, and that
API is not private to it. Anything that can make an HTTP request can browse
the library, log or query practice, manage instruments, read or write
transcriptions, and trigger a scan - which is what makes a companion app, a
script, or a scoreboard on a second screen possible without touching this
codebase at all.

## Where the contract actually lives

The API is documented by generating its OpenAPI schema rather than by hand:
every route in `server/fermata/api.py` declares a `response_model` (a Pydantic
model in `server/fermata/api_models.py`), a docstring, and a tag, and FastAPI
turns that into the same schema Swagger UI and any codegen read.

- **`GET /docs`** - interactive Swagger UI: every route, its parameters, its
  request and response shapes, and a "Try it out" button that calls a real,
  running instance.
- **`GET /openapi.json`** - the schema itself, for generating a client or
  feeding a tool that reads OpenAPI directly.

Both are served by the same process as the API itself - there is nothing
separate to stand up or keep in sync. `server/tests/test_api_docs.py` pins
that the generated document validates against the OpenAPI spec, that every
route carries a summary, a description, a tag and a response schema, and
that FastAPI's response-model validation is genuinely switched on for this
app rather than merely declared in a decorator - so an endpoint added without
documenting it fails that test, with a message naming what is missing,
rather than shipping undocumented.

## What to expect between releases

Fermata does not yet version this API independently of the application - the
number `GET /api/version` reports is the application's own release, and there
is no `/api/v2` alongside `/api/v1`. Until that changes, the practical
expectations are:

- **A new field reaches clients once its response model carries it, not the
  moment a handler starts computing it.** Every response is filtered through
  a Pydantic model (`server/fermata/api_models.py`) before it leaves the
  server, so a field a handler adds to its own return value is invisible on
  the wire until the matching model grows to match - see, for example, how
  the transcription endpoints' Rule 8 conformance figures and provenance
  fields (`bars_defective`, `time_signature_source`, and the rest - see
  `TranscriptionOut`) had to be added to that model, not only to the
  handler, to actually reach a reader (issues #143, #146). Growing a field
  in the ordinary case is not a breaking change; forgetting to grow it is a
  bug, and it is the one `server/tests/test_api_docs.py` actually guards
  against: every request its tests make is checked against the RAW value the
  handler returned, captured (via `fastapi.routing.run_endpoint_function`)
  before response_model ever touched it, against what actually reached the
  wire - and fails naming the exact route and field the moment a model falls
  behind its handler, across every endpoint group in one mechanism rather
  than one hand-written pin per model.
- **A field already documented is not silently repurposed or removed.**
  Renaming or dropping a field, or changing what a value means, is called out
  in the release it ships in.
- **`null` is a meaningful answer, not "not implemented yet".** Several
  fields across this API are `null` on purpose - a Rule 8 figure nothing has
  measured, a goal's `sessions_inferred` when the goal is not countable, a
  transcription's provenance on a hand edit - and each is documented as such
  in `api_models.py` rather than treated as a gap to fill in later. A client
  should not infer "this will become non-null once the feature is finished".
- **This is a single-owner, self-hosted server**, not a multi-tenant service.
  `owner` fields exist in several tables for a future multi-account version
  and are always `'local'` today; nothing in the API takes or checks
  credentials yet (see [SECURITY.md](../SECURITY.md)).

The data model behind the practice endpoints specifically - what a session
and a goal mean, what is derived versus stored, and what deliberately has no
column - is documented in more depth in
[docs/practice-data.md](practice-data.md); the MusicXML this API's
transcription endpoints read and write is documented in
[docs/musicxml-tab-profile.md](musicxml-tab-profile.md).

## Who else reads this contract

A planned companion server speaks the Model Context Protocol, an open
standard, and is meant to wrap this REST API rather than reimplement its
logic - which is the reason "generated but wrong or incomplete" is not good
enough here: that layer's own correctness depends on this one meaning what it
says.
