# Tab profile selection — a specification for tests (issue #66)

Written up because there is no frontend test runner in this repo yet (no
`web/package.json` test script, no Playwright, no specs anywhere in the
tree) - `feature/instruments` is bringing one, with a `Browser tests` CI
job. This document is not itself a test - nothing executes it, so no
assertion in it can fail on its own - it is the five numbered scenarios
(plus a few narrower behaviors called out in their own sections: async load
timing, retryability after a failed switch, and dataset staleness across a
score switch) that verified the fix in
[#69](https://github.com/sirged/fermata/pull/69), broken into fixture /
setup / action / assertion so porting onto that harness is mechanical rather
than a re-investigation. Each was checked with Playwright against a real
dev server + backend (not just a build), except the delegation-guard cases
in `environment-guard.mjs`, which are plain Node and check `score-render.js`
directly; nothing below depends on Playwright specifically.

## Fixtures

1. **`web/test-fixtures/notation-only.musicxml`** (new in this PR). A
   minimal valid MusicXML 4.0 document: one part, one measure, G clef, four
   quarter notes with `<pitch>` and **no** `<notations><technical>` at all -
   no TAB clef, no `<staff-tuning>`. This is the exact repro condition from
   issue #66: pitches, but nothing for a tab renderer to draw.

   Deliberately **not** placed under `docs/examples/`, even though that
   directory holds the repo's other committed MusicXML fixtures: it is
   pinned by `server/tests/test_musicxml.py::test_the_published_examples_exist_and_conform`
   to exactly `["monophonic.musicxml", "two-voice.musicxml"]`, and every
   file there is validated against tab-profile-specific rules (TAB clef,
   `<staff-tuning>`, Rule 8 measure arithmetic) that a notation-only file
   does not, and should not, satisfy.

2. **`docs/examples/monophonic.musicxml`** (already committed). One
   monophonic bar, TAB clef, six-line `<staff-tuning>`, every note carrying
   `<notations><technical><string>/<fret>`. This is a genuine *tab-only*
   staff - see "How profile support is actually decided" below - the mirror
   image of fixture 1, and already validated by the server test suite (see
   `docs/musicxml-tab-profile.md`).

3. **No new fixture.** The bundled alphaTeX demo score is the `DEMO_TEX`
   constant in `web/src/lib/TabViewer.svelte` (currently lines 75-83),
   reachable at the `#/demo` route (`web/src/App.svelte` routes
   `#/demo` to `<Viewer demo={true} />`, which passes `demo` through to
   `TabViewer`). alphaTeX's default staff has both `showStandardNotation`
   and `showTablature` true on the *same* staff, so this is the one fixture
   here where a single staff genuinely supports all three profiles by
   itself. That is a narrower claim than "no fixture offers all three" -
   fixture 4, below, also offers all three, but only by combining two
   staves that each support one; no *single* staff in any MusicXML fixture
   in this repo does, since Fermata's own MusicXML tab profile deliberately
   never pairs a tab staff with a notation staff on one staff
   (`docs/musicxml-tab-profile.md`, "Out of scope").

4. **`web/test-fixtures/multi-staff.musicxml`** (new). One part, one
   measure, `<staves>2</staves>`: staff 1 is plain notation (G clef, no
   `<technical>`, same as fixture 1), staff 2 is tab-only (TAB clef,
   six-line `<staff-tuning>`, every note carrying `<technical><string>/<fret>`,
   same as fixture 2) - each note tagged `<staff>1</staff>` or
   `<staff>2</staff>`, with a `<backup>` between the two staves' notes so
   both start at the measure's beginning. See "A correctness bug to fix"
   below: two staves in the one track that gets rendered by default, each
   supporting a different single profile, neither supporting all three
   alone.

5. **`web/test-fixtures/unrenderable.musicxml`** (new). One part, one
   measure, a percussion clef *combined with* `<staff-details><staff-tuning>`.
   `Staff.finish()` clears the tuning and forces `showTablature = false` for
   any percussion staff regardless of what the tuning claimed, and a
   percussion clef never sets `showStandardNotation`, `showSlash` or
   `showNumbered` either - so this staff ends up unable to satisfy any of the
   three profiles at all. See "A score that draws nothing" below.

## How profile support is actually decided

`score-render.js`'s `supportedProfiles(tracks)` decides, for each of
`SCORE_PROFILES = ["score", "tab", "scoretab"]`, whether **any** `(track,
staff)` pair in the given tracks can be drawn under it - not whether the
first staff, or any one staff, can. The check for a single pair delegates to
the renderer itself rather than reimplementing its rules:

```js
const staffIds = Environment.staveProfiles.get(STAVE_PROFILE[profileKey]);
const drawable = Environment.defaultRenderers.some(
  (f) => staffIds.has(f.staffId) && f.canCreate(track, staff),
);
```

This is exactly `PageViewLayout.createEmptyStaffSystem`'s own test
(`this.profile.has(factory.staffId) && factory.canCreate(track, staff)`)
against `Environment.staveProfiles` / `Environment.defaultRenderers` - both
plain public static class fields in 1.8.4 (`/** @internal */` only in the
JSDoc, which is why they are absent from the shipped `.d.ts`). Calling the
real `canCreate()` means this file cannot answer differently than the
renderer that is about to be asked to draw - a hand-written mirror of the
rules could only ever be *consistent with* the library at the time it was
written, and would silently drift the day a rule changes.

If `Environment.staveProfiles` or `Environment.defaultRenderers` are missing
or not shaped as expected (an upgrade moved or renamed them),
`environmentCanDraw()` in `score-render.js` returns `null`, one
`console.warn` fires, and everything falls back to `mirroredCanDraw` - a
kept-not-deleted hand-written version of the same per-staff rules this PR
originally shipped with (`showStandardNotation` for "score", `showTablature
&& tuning.length > 0` for "tab", either of those plus
`showSlash`/`showNumbered` for "scoretab").

The guard checks more than field types. A shape check that only confirms
"`staveProfiles` is a `Map` of `Set`s" and "every factory has a string
`staffId` and a function `canCreate`" passes even when those two collections
have quietly stopped referring to the same things - a release that renames
every `staffId` consistently with itself (in both collections' *shape*, but
not their *content*) would satisfy every field-type check while every
profile matched zero factories, and `supportedProfiles()` would silently
answer "nothing is drawable" for every score in the library, with no
warning. `environmentCanDraw()` additionally requires the union of every
stave profile's staff ids to actually intersect the renderers' ids
somewhere, which a rename like that fails.

The guard also cannot check that calling `canCreate` is *safe*, only that it
*is a function* - a changed parameter list or return contract would pass the
guard and throw on first use. That throw is caught where `canDraw()` calls
the delegate, which permanently downgrades to `mirroredCanDraw` for the rest
of the session (a structural incompatibility will not un-happen on the next
call) and warns exactly once. `supportedProfiles()` itself is wrapped a
second time, around that whole loop: without it, anything else going wrong
in there (a malformed track, say) would throw out of the `scoreLoaded`
handler in `createScoreView` entirely, skipping `publish()` and
`onProfiles()` - `profileOptions` would stay `null` forever, showing neither
buttons nor the unrenderable notice, while the renderer's own attempt to
draw with whatever profile it already had still ran and surfaced its raw
error. Both catches degrade to a checked answer instead of silence.

**All four of these are exercised, not just inspected** - see
`web/test-fixtures/environment-guard.mjs` (`node
test-fixtures/environment-guard.mjs` from `web/`), a small monkeypatching
script run directly with Node's own ESM loader rather than a browser: it
imports `score-render.js` fresh (a cache-busting query on each dynamic
`import()`) against four different states of `alphaTab.Environment` in
turn - untouched, renamed staff ids, a throwing `canCreate`, and a
malformed track passed straight to `supportedProfiles()` - and checks the
warning fires (or doesn't, for the healthy case) and the answer is still
correct in every case. This only works outside a browser because
`score-render.js`'s module-level code only touches `alphaTab.Environment` at
import time; everything DOM-dependent is inside functions, called later.

For MusicXML specifically, what actually drives `showStandardNotation` /
`showTablature` is **`<staff-tuning>`, not the clef sign** - this is worth
stating precisely, because the natural-sounding guess (clef sign decides
tab, staff-tuning decides notation) is backwards and does not match what
alphaTab actually does. Verified by execution, not read off the clef-parsing
code alone (which sets `showTablature = true` on a TAB clef sign by itself,
but that gets overridden - see below):

- A TAB clef with **no** `<staff-tuning>` at all gives
  `showStandardNotation = true, showTablature = false`. `Staff.finish()`
  clears `showTablature` back to `false` whenever a staff's tuning array
  ended up empty, which is exactly what "no `<staff-tuning>` was ever
  parsed" leaves it as - overriding whatever the clef parsing set.
- A plain G clef **with** `<staff-tuning>` gives
  `showStandardNotation = false, showTablature = true`. The **first**
  `<staff-tuning>` element parsed for a staff sets both flags
  (`MusicXmlImporter._parseStaffTuning` in `@coderline/alphatab`'s bundle),
  regardless of what clef sign was declared.

`<staff-tuning>`'s presence is what drives both flags for a non-percussion
staff - not the *only* thing that can affect them (a percussion clef
overrides `showTablature` back to `false` regardless of tuning; see fixture
5), but the one relevant to fixtures 1-4. This is why fixture 2 (TAB clef,
`<staff-tuning>` present) offers only Tab/Both, never Notation - it is not a
fixture bug, it is what a real Fermata transcription looks like today (a
single tab staff, never paired with a separate notation staff), so the spec
has to assert exactly that, not "all three."

## Async load timing (the `score` prop's fetch window)

`profileOptions` in `TabViewer.svelte` starts `null`, not `SCORE_PROFILES`,
and the reason is specific to the `score` prop's load path, not just
tidiness. `source()` for a `score` prop resolves to `{kind: "file", url}`,
and `score-render.js`'s `load()` fetches that URL and only calls `api.load()`
once the fetch resolves - `createScoreView` itself, and the `view` it
returns, exist synchronously well before that. If `profileOptions` started
permissive (every button offered), there would be a real window - however
short - between mount and the fetch actually landing during which a click on
"Tab" would reach `view.setProfile("tab")` for a score whose actual content
is not yet known. Before this round's fix that click did not crash (the
renderer's own `score`/`tracks` are still unset at that point, so `render()`
takes its early "nothing to draw" branch and no-ops harmlessly), but the
*internal* `profile` variable in `score-render.js` was still set to `"tab"`
regardless - and once the fetch resolved and `scoreLoaded` ran its real
check, a notation-only score would correct it right back, moving the
highlighted button out from under a still-hovering pointer with no warning.
Starting `profileOptions` at `null` (and resetting it to `null`, not to the
previous score's list, on every new load - see `TabViewer.svelte`'s
`$effect`) closes the window entirely: no button is clickable until the real
answer is already known, so there is nothing to walk back.

## A correctness bug to fix (caught before merge, not after)

The crash `supportedProfiles()` exists to prevent only happens when **every**
`(track, staff)` pair in the rendered set fails a profile's check - the
renderer loops over all of them building one staff system, so it takes only
one pair that can draw a profile to keep the system non-empty. A version of
`supportedProfiles()` that decided from a single staff (e.g. only
`tracks[0].staves[0]`) would answer wrong for exactly fixture 4: staff 1
alone would say only "score" is supported, staff 2 alone would say only
"tab" - either answer would incorrectly hide a profile the *other* staff
can actually draw, once both are rendered together. `supportedProfiles()`
therefore flattens every track's every staff into `(track, staff)` pairs
first and ORs the per-pair check across all of them - see Scenario 4.

## A score that draws nothing

`supportedProfiles()` can legitimately return an empty array - fixture 5 is
exactly that. An earlier version of this fix answered a wholly-unsupported
score by offering every profile instead of none, which only moved the same
crash from a click to page load: the permissive fallback would pass the
`scoreProfiles.includes(profile)` check for whatever the default profile
was, the renderer would still find zero drawable staves on its first
attempt, and `StaffSystem.addBars` would still throw - now on load, with no
click required to reach it, and the toolbar would have shown all three
buttons for a score none of them could draw.

There is no profile a caller can pick that fixes a score with nothing to
draw, so `score-render.js` does not try to pick one. Instead:

- `TabViewer` offers no profile buttons at all when `supportedProfiles()`
  comes back empty, and shows `UNRENDERABLE_MESSAGE` ("This score has no
  notation or tablature the staff view can draw.") in a `<p class="notice">`
  instead - never the renderer's own error text. The `.score-scroll`
  container stays in the DOM (`score-render.js`'s `host` binding needs a
  stable element to attach to) but gets a `hidden` class so nothing broken
  is visible inside it.
- The renderer's own automatic first render, triggered by AlphaTabApi itself
  immediately after `scoreLoaded`, cannot be cancelled from the `scoreLoaded`
  handler - so it still runs, and can still throw exactly as it always has.
  **Which of two paths that render takes is a font-load race, not a browser
  quirk** - an earlier version of this document attributed it to this
  sandbox's particular Chromium, which is wrong and would mislead whoever
  ports it. AlphaTabApi defers the whole render while its fonts are still
  loading. On a cold cache that deferral outlasts Svelte's reactivity flush
  of the `hidden` class onto `.score-scroll`, so by the time the deferred
  render actually runs, the host measures 0 width and the renderer's own
  `render()` logs `"AlphaTab skipped rendering because of width=0"` and
  returns without ever reaching `addBars` - no crash, no `error` event,
  nothing to suppress. On a **warm** cache (fonts already cached - the
  common case for a shipped app after the first load) there is no deferral:
  the render runs synchronously inside `load()`, before the `hidden` class
  has had any chance to apply, and it throws exactly as it always has.
  **The forced path below is therefore the normal path for a warm cache, not
  an exotic one** - it is what a real user hits on essentially every load of
  an unrenderable score once the app has been open a moment. Either way,
  alphaTab's own `Logger.error` still writes the raw `TypeError` to devtools
  when it does throw (that call is inside the library, unconditional, and
  not something this file can suppress); what differs between the two paths
  is only whether devtools shows that line at all, never what the page
  itself shows.
- `score-render.js`'s `api.error` handler has to tell this specific,
  predicted failure apart from an unrelated one - a soundfont load failure,
  say - and **it cannot do that from timing alone**. An earlier version keyed
  suppression on "a render is currently in flight" (`renderStarted` fired,
  no matching `postRenderFinished` yet), which is not specific enough:
  alphaTab registers its own resize handling unconditionally in the
  `AlphaTabApi` constructor (see the `ResizeObserver` note in
  `score-render.js`), and on an unrenderable score that internal path can
  start and fail a render this file never even sees start -
  `ScoreRenderer.resizeRender()`'s full-rerender branch calls `render()`
  directly, with no try/catch around it, so it never reaches `api.error` for
  its own failure, but it does still fire this file's `renderStarted`
  listener on the way in and leaves nothing to ever clear it afterwards. The
  in-flight flag stayed set until the *next* `api.error` of any kind, which
  then got silently swallowed regardless of what actually caused it - a
  reviewer reproduced this by injecting unrelated soundfont errors through
  the library's own emitter after a resize and watching them alternate
  shown, swallowed, shown, swallowed, one swallow armed per resize. The fix
  keys suppression on the error itself instead: `e.stack` has to contain
  both `"StaffSystem"` and `"addBars"` - the exact frames in Scenario 1's
  trace - before anything is swallowed, in addition to `unrenderable` being
  true. An unrelated error's stack never matches, regardless of what
  `renderInFlight` (kept only for `data-score-render-ok` now, see below) is
  doing at the time. **A known, accepted limitation**: `resizeRender()`'s own
  failure for an unrenderable score still never reaches `api.error` at all
  (it becomes an uncaught exception at `window.onerror`, which
  `score-render.js` does not listen to), so it is not suppressed *or*
  double-counted - it simply stays invisible to this file, the same as it
  was before this fix, on every resize of an unrenderable score whose
  container is not `display:none` for some reason.
- `renderStarted` with no matching `postRenderFinished` since is a complete
  failed-render detector, used for `data-score-render-ok` only (not for the
  suppression decision above) - `ScoreRenderer.renderScore`'s own try/catch
  means `postRenderFinished` never fires for a render that threw partway
  through. `data-score-render-ms` / `data-score-renders` only ever advance on
  success and are not safe to read as "did the last attempt succeed" on
  their own - check `data-score-render-ok` first, **and** that
  `data-score-renders` actually incremented from its previous value:
  `data-score-render-ok` reads `"true"` in two states where nothing was
  drawn - before the first render ever runs, and when a render request was
  silently dropped at width 0 or deferred by font loading (the natural path
  above) - so it means "the last render that *reached* the renderer did not
  throw", not "the last requested render actually produced something."

See Scenario 5 for the exact assertions, and both the natural and forced
verification of the two paths above.

## Environment

Backend and frontend were run from a scratch checkout of the fix branch,
isolated from any other running fermata instance on the machine.

**Bind explicitly to `127.0.0.1` and a non-default port for both frontend
and backend, and address them by that literal IP in the test client - not
`localhost`.** On the machine this was verified on, `localhost` resolves to
`::1` before `127.0.0.1`, and another already-running fermata instance (a
different worktree's dev server, or a live instance) can already hold the
default Vite/uvicorn ports on the IPv6 side. An early run of this spec
against `localhost:5173`/`5174` silently hit that other instance's real
250+-score library instead of the fixtures below - the giveaway was the
score count in the library view not matching the scratch library. Binding
literally and checking `curl http://127.0.0.1:<PORT>/api/scores` returns
exactly the scratch library's entries avoids this.

Steps:

1. `cd server && python -m venv .venv && .venv/Scripts/pip install -e .`
   (the project requires Python >=3.12; use `py -3.13` if the default
   `python` on the machine is older).
2. Make a scratch library directory containing fixtures 1, 4 and 5, plus a
   copy of `docs/examples/monophonic.musicxml` for fixture 2, and an empty
   scratch config directory.
3. `FERMATA_LIBRARY=<library dir> FERMATA_CONFIG=<config dir> .venv/Scripts/python -m uvicorn fermata.main:app --host 127.0.0.1 --port <PORT_A>`
4. Once the backend is up: `curl -X POST http://127.0.0.1:<PORT_A>/api/scan`
5. `cd web && npm install`, then either point `vite.config.js`'s
   `server.proxy["/api"]` at `http://127.0.0.1:<PORT_A>` and set
   `server.port = <PORT_B>` with `strictPort: true`, or otherwise confirm
   the dev server under test really proxies to `PORT_A` (see the `curl`
   check above) before running anything against it.
6. Browser: Chromium via Playwright (`npx playwright install chromium`),
   driven by a plain Node script against `http://127.0.0.1:<PORT_B>/` -
   no browser-automation extension was available in the sandbox this was
   verified in.

## Scenario 1 — notation-only score: Tab must not be offered

**Setup:** open the library card for fixture 1 ("Notation-only example").

**Assertions (post-fix):**

- `.seg button` texts are exactly `["Notation", "Both"]` - no "Tab" button
  rendered at all.
- The `.at-host` element's `dataset.scoreProfiles` equals `"score,scoretab"`,
  and `dataset.scoreRenderOk` equals `"true"`.
- `.at-host` contains at least one rendered `<svg>` (the score actually
  drew, this isn't a silently-blank view).
- Zero `console:error` and zero `pageerror` events across: page load,
  clicking "Both", clicking back to "Notation", Play, Stop.

**Pre-fix**, this score's toolbar offered all three buttons, and clicking
the (then-offered) "Tab" button produced this exact console output:

```
[AlphaTab][API] An unexpected error occurred
TypeError: Cannot read properties of undefined (reading 'staves')
    at StaffSystem.addBars (.../@coderline_alphatab.js:52929:32)
    at PageViewLayout._createStaffSystem (.../@coderline_alphatab.js:54168:34)
    at PageViewLayout._layoutAndRenderScore (.../@coderline_alphatab.js:54092:27)
    at PageViewLayout.doLayoutAndRender (.../@coderline_alphatab.js:53902:14)
    at PageViewLayout.layoutAndRender (.../@coderline_alphatab.js:53483:10)
    at ScoreRenderer._layoutAndRender (.../@coderline_alphatab.js:37450:17)
    at ScoreRenderer.render (.../@coderline_alphatab.js:37429:12)
    at ScoreRenderer.renderScore (.../@coderline_alphatab.js:37375:12)
    at ScoreRendererWrapper.renderScore (.../@coderline_alphatab.js:41186:21)
    at _AlphaTabApi.render (.../@coderline_alphatab.js:42476:22)
```

This was **never console-only**: `score-render.js` already forwarded the
renderer's `error` event to the viewer before this fix, so the same message
also appeared on the page itself, in `TabViewer`'s pre-existing `.error`
paragraph (bound to `loadError`). The full pre-fix experience was the raw
`TypeError` text on the page, the console error, the stale previous ("Both")
render frozen underneath it, and the "Tab" button still shown highlighted
even though its render had failed and nothing behind it had changed - that
mismatch between the highlighted button and what was actually on screen is
the defect Scenario 1 and F4 (see PR #69's review) are both about. Post-fix,
"Tab" is not offered at all, so none of the above is reachable through this
score's UI - a score that draws *nothing at all* under *any* profile is a
different case with its own failure mode; see Scenario 5, not this one.

## Scenario 2 — tab-only score (fixture 2): Notation must not be offered

**Setup:** open the library card for "Monophonic example"
(`docs/examples/monophonic.musicxml`).

**Assertions:**

- `.seg button` texts are exactly `["Tab", "Both"]`.
- `.at-host` `dataset.scoreProfiles` equals `"tab,scoretab"`.
- Clicking "Tab" then "Both": zero console errors either time; `.at-host`
  keeps at least one rendered `<svg>` after each.
- Player controls work: wait for `.player .primary` to become enabled
  (`playerReady`), click it, its text becomes `"❚❚ Pause"`; click the
  `.player button` with text `"■"` to stop.

This scenario is the symmetric case the ticket also names ("a tab-only
score should not offer a notation profile it cannot draw"), and it is not
hypothetical - it is what every current Fermata transcription looks like
(see "How profile support is actually decided" above), so it is closer to
"the common case" for real library content than scenario 3 is.

## Scenario 3 — the bundled demo score: all three profiles work

**Setup:** navigate directly to `#/demo` (no library entry needed).

**Assertions:**

- Zero console/page errors from navigation alone - this is also one of the
  fix's own acceptance criteria.
- `.seg button` texts are exactly `["Notation", "Tab", "Both"]`.
- Clicking through all three, in any order: zero console errors per click;
  `.at-host` keeps rendered `<svg>` content after each.
- Wait for `.player .primary` to become enabled, then:
  - click the `.practice button` with text "Loop" → gains class `on`.
  - click the `.practice button` with text "Metronome" → gains class `on`.
  - click the `.practice button` with text "Count-in" → gains class `on`.
  - click the `.practice button` with text "Ladder" → gains class `on`,
    and `.ladder-controls` becomes visible (Start/Step/Target number
    inputs plus a `%` readout).
  - click `.player .primary` → its text becomes `"❚❚ Pause"`.
  - click the `.player button` with text `"■"` → stops without error.
- Zero console errors across the whole playback sequence.

## Scenario 4 — multi-staff score (fixture 4): OR across every pair, not one staff

**Setup:** open the library card for "Multi-staff example".

**Assertions:**

- `.seg button` texts are exactly `["Notation", "Tab", "Both"]` - staff 1
  alone would only justify "Notation", staff 2 alone only "Tab"; all three
  are offered because `supportedProfiles()` ORs the check across both
  `(track, staff)` pairs rather than answering from either staff on its own.
- `.at-host` `dataset.scoreProfiles` equals `"score,tab,scoretab"`.
- Clicking through all three: zero console errors per click; `.at-host`
  keeps rendered `<svg>` content after each (both staves draw together
  under "Both"; each individual profile still finds at least the one staff
  that supports it).

This is the scenario a single-staff answer would get wrong - see "A
correctness bug to fix" above for why.

## Scenario 5 — a score that draws nothing (fixture 5): no buttons, plain sentence, no crash reaches the page

**Setup:** open the library card for "Unrenderable example".

**Assertions:**

- No `.seg` element is rendered at all (zero profile buttons).
- A `<p class="notice">` is shown with exactly `UNRENDERABLE_MESSAGE`'s text:
  "This score has no notation or tablature the staff view can draw."
- No `.error` paragraph is shown (`loadError` never receives the renderer's
  raw message for this predicted failure).
- `.score-scroll` has class `hidden`.
- `.at-host` `dataset.scoreProfiles` equals `""` (empty string, present -
  distinct from the attribute being absent, which means no score has loaded
  at all yet).
- Verified twice, to cover both paths described in "A score that draws
  nothing" above - which one a given run takes is a font cache state
  (cold/warm), not something to assert on directly; assert the *outcome*
  (buttons, notice, `.error`, `dataset.scoreRenderOk`) the same way either
  time:
  - **Natural (cold cache)**: open the score and wait, on the first load
    after starting the dev server. The `.score-scroll.hidden` class is
    applied before alphaTab's font-load-deferred render runs, so it logs
    `"AlphaTab skipped rendering because of width=0"` and returns - no
    `error` event, no console error, `dataset.scoreRenderOk` stays at its
    initial `"true"` (no render attempt ever completed or failed to update
    it; do not read that as "something rendered" - see the note on
    `data-score-renders` above). Reloading and reopening the same score
    again *without* restarting the server - warm cache now - takes the
    forced path below instead, without needing the stylesheet override; the
    override exists to make that path reachable on the very first (cold)
    load too, for a deterministic single-run check.
  - **Forced (or warm cache)**: with a stylesheet override neutralising
    `.score-scroll.hidden` (`display: flex !important`) injected *before*
    opening the score, so the host keeps real dimensions, the renderer's
    first render does proceed and does throw the exact `TypeError` from
    Scenario 1's stack trace. alphaTab's own `Logger.error` still writes
    that to devtools (unsuppressable from outside the library) and a
    `console:debug` line ("score-render: suppressed the predicted render
    failure…") appears - but `.error` stays empty, `.notice` still shows the
    plain sentence, and `dataset.scoreRenderOk` flips to `"false"`. This is
    the path that proves the `api.error` handler's stack-based check (see
    "A score that draws nothing" above) actually recognises and swallows
    the specific crash, rather than merely never encountering it.

## A profile switch has to survive failing (retry, and clearing the error)

A render that fails - for *any* reason, not only the unsupported-profile
case above - used to leave two traps, both reachable without any error at
all (see the next paragraph):

- **Permanently inert.** `score-render.js`'s `setProfile()` used to
  de-duplicate against `profile`, the *requested* profile, set eagerly the
  moment a switch is asked for whether or not it ever renders. Clicking the
  same profile again after a failed switch then looked like a no-op
  (`next === profile` already true from the failed attempt) and skipped
  `reapply()` entirely - the only way out was detouring through a third
  profile first. Fixed by keying the de-duplication on `appliedProfile`
  instead - the profile a render has actually *finished* with successfully,
  updated only from `postRenderFinished` - so asking again for a profile
  that never actually rendered is never mistaken for "no change requested."
- **A stale `loadError`.** Nothing cleared `loadError` on a later, unrelated
  render succeeding, so an error from one failed switch could keep showing
  (and keep the `.error` paragraph occupying space) after the view had
  since recovered via a different profile. Fixed by clearing it from
  `TabViewer`'s `onProfileApplied` handler - the same signal that moves the
  highlighted button, since both represent "trust what's on screen now, not
  what went wrong before."

**Reachable with no error at all**, which is what makes it a real bug rather
than a hypothetical one: switching profiles while the stage measures 0
width (e.g. a hidden pane, mid-layout) hits the same "render silently
skipped, nothing ever fires" path used by the natural cold-cache case above
- `profile` (old code) or nothing (fixed code) advances, but no
`postRenderFinished` ever runs to confirm it. Verified directly: open the
multi-staff score, force `.at-host { width: 0 }` via a stylesheet override,
click "Tab" (the request silently drops - the highlighted button correctly
stays on whatever last actually rendered, `dataset.scoreProfile` shows
`"tab"` requested but `data-score-renders` does not advance), remove the
override, click "Tab" again - it renders successfully and the highlight
moves, proving the second click was not blocked by the first.

## A switch between scores must not leak the previous one's dataset

`host` (the `.at-host` element) is the same DOM node across a score switch -
`TabViewer`'s markup does not recreate it, only `createScoreView`'s closure
is torn down and rebuilt. `publish()` explicitly deletes
`dataset.scoreProfiles` when `scoreProfiles` is `null` (rather than simply
not setting it) specifically so its very first call for a *new* score - made
before anything about that score is known - wipes whatever the *previous*
score left there, rather than letting it sit through the whole loading
window and falsely read as the new score's answer. Verified directly:
open a tab-only score (`dataset.scoreProfiles` = `"tab,scoretab"`), navigate
back to the library and open the notation-only one, and confirm
`dataset.scoreProfiles` reads `"score,scoretab"` promptly rather than
briefly (or permanently, if something regresses) showing the tab-only
score's stale value.

Similarly, a theme or preset change on an *unrenderable* score still has to
publish `dataset.scoreTheme` / `dataset.scorePreset` - `reapply()`'s early
return for `unrenderable` runs `publish()` before returning, not just
`return` outright, because `setTheme`/`setPreset` already updated their own
variables before calling `reapply()`, and skipping `publish()` along with
the (correctly) skipped render would leave those two attributes reporting
the *previous* theme/preset instead of the one actually now in effect.
Verified directly: open the unrenderable score, switch the theme picker to
"White on black", and confirm `dataset.scoreTheme` reads `"noir"` promptly.

## Notes for porting

- Every assertion above was checked with Playwright's
  `page.locator(...).allTextContents()`,
  `page.locator(...).evaluate(el => ({...el.dataset}))`, `console` /
  `pageerror` event listeners, and `page.addStyleTag(...)` (Scenario 5's
  forced path, and the retry check's width-0 override) - none of it is
  Playwright-specific and should translate directly to whatever
  `feature/instruments` brings in.
- `SCORE_PROFILES` and the button label order come from
  `web/src/lib/score-render.js` (`SCORE_PROFILES = ["score", "tab", "scoretab"]`)
  and `web/src/lib/TabViewer.svelte` (`PROFILE_LABELS`, mapping those to
  "Notation"/"Tab"/"Both") - if either changes, the expected button-text
  arrays above need to change with them; they are not independent
  assertions.
- `dataset.scoreProfiles` and `dataset.scoreRenderOk` are written by
  `score-render.js`'s `publish()` specifically so a test can assert on them
  directly rather than parsing button text or guessing at render success -
  prefer them once the harness exists. `dataset.scoreRenderMs` /
  `dataset.scoreRenders` are not reset on a failed render and must not be
  read without checking `dataset.scoreRenderOk` **and** that
  `dataset.scoreRenders` itself incremented from its prior value - see "A
  score that draws nothing" above for why `scoreRenderOk` alone is not
  enough.
- `UNRENDERABLE_MESSAGE` is exported from `score-render.js` - import it
  rather than hardcoding the sentence in a test, so the two cannot drift.
- `web/test-fixtures/environment-guard.mjs` covers the delegation-guard
  cases (renamed staff ids, a throwing `canCreate`, an unrelated throw
  inside `supportedProfiles()`) directly with Node's own ESM loader, not
  Playwright - see "How profile support is actually decided" above. Run it
  with `node test-fixtures/environment-guard.mjs` from `web/`.
