# Tab profile selection — a specification for tests (issue #66)

Written up because there is no frontend test runner in this repo yet (no
`web/package.json` test script, no Playwright, no specs anywhere in the
tree) - `feature/instruments` is bringing one, with a `Browser tests` CI
job. This document is not itself a test - nothing executes it, so no
assertion in it can fail on its own - it is the five scenarios that verified
the fix in [#69](https://github.com/sirged/fermata/pull/69), broken into
fixture / setup / action / assertion so porting onto that harness is
mechanical rather than a re-investigation. Each was checked with Playwright
against a real dev server + backend (not just a build); nothing below
depends on Playwright specifically.

## Fixtures

1. **`web/test-fixtures/notation-only.musicxml`** (this file, new). A
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
   constant in `web/src/lib/TabViewer.svelte` (currently lines 68-76),
   reachable at the `#/demo` route (`web/src/App.svelte` routes
   `#/demo` to `<Viewer demo={true} />`, which passes `demo` through to
   `TabViewer`). alphaTeX's default staff has both `showStandardNotation`
   and `showTablature` true, so this is the one fixture here that genuinely
   supports all three profiles - no MusicXML fixture in this repo does,
   since Fermata's own MusicXML tab profile deliberately never pairs a tab
   staff with a notation staff (`docs/musicxml-tab-profile.md`, "Out of
   scope").

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
`showSlash`/`showNumbered` for "scoretab"). **This fallback path is verified
by code inspection, not exercised here** - forcing it would mean breaking
`Environment`'s shape at runtime, which isn't worth the complexity for a
path whose only job is "fail loudly and degrade to today's behaviour, don't
answer wrong silently." A test harness with the room to monkeypatch
`alphaTab.Environment` before importing `score-render.js` could exercise it
directly - assert the warning fires, and that `supportedProfiles()` still
returns the fixture 1/2/3 answers below off `mirroredCanDraw` alone.

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

`<staff-tuning>`'s presence is the one thing driving both flags. This is why
fixture 2 (TAB clef, `<staff-tuning>` present) offers only Tab/Both, never
Notation - it is not a fixture bug, it is what a real Fermata transcription
looks like today (a single tab staff, never paired with a separate notation
staff), so the spec has to assert exactly that, not "all three."

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
  handler - so it still runs. What happens next depends on timing that this
  document does not treat as guaranteed: if the `.score-scroll.hidden` class
  has already been applied to the DOM by the time that render executes, the
  host measures 0 width and the renderer's own `render()` logs
  `"AlphaTab skipped rendering because of width=0"` and returns without ever
  reaching `addBars` - no crash, no `error` event, nothing to suppress. If it
  has not (or `.score-scroll` is not hidden, as forced in the verification
  below to check the other path directly), the render proceeds and throws
  exactly as it always has - alphaTab's own `Logger.error` still writes the
  raw `TypeError` to devtools (that call is inside the library, unconditional,
  and not something this file can suppress), and the library's `error` event
  fires with it. `score-render.js`'s `api.error` handler recognises this
  specific, predicted failure (`unrenderable` was already true, and
  `renderInFlight` shows a render actually started and never finished) and
  swallows it - `console.debug`, not `onError` - rather than forwarding the
  raw message to `loadError`/the page's `.error` paragraph. **Both paths are
  safe for the person looking at the page**: either way, nothing renders,
  nothing crashes past the library's own already-existing try/catch, and the
  only thing shown is the plain sentence. Only devtools output differs.
- `renderStarted` with no matching `postRenderFinished` since is a complete
  failed-render detector - `ScoreRenderer.renderScore`'s own try/catch means
  `postRenderFinished` never fires for a render that threw partway through.
  `data-score-render-ok` reflects this per attempt; `data-score-render-ms` /
  `data-score-renders` only ever advance on success and are not safe to read
  as "did the last attempt succeed" on their own - check
  `data-score-render-ok` first.

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
  nothing" above:
  - **Natural**: open the score and wait. In this sandbox's Chromium, the
    `.score-scroll.hidden` class was already applied by the time the
    renderer's automatic first render ran, so it logged
    `"AlphaTab skipped rendering because of width=0"` and returned - no
    `error` event, no console error, `dataset.scoreRenderOk` stays at its
    initial `"true"` (no render attempt ever completed or failed to update
    it). Forcing a real window resize afterwards changes nothing:
    `reapply()` skips outright while `unrenderable` is true, by design (see
    `score-render.js`), so this state is stable rather than one accidental
    early return away from the crash reappearing.
  - **Forced**: with a stylesheet override neutralising `.score-scroll.hidden`
    (`display: flex !important`) injected *before* opening the score, so the
    host keeps real dimensions, the renderer's first render does proceed and
    does throw the exact `TypeError` from Scenario 1's stack trace. alphaTab's
    own `Logger.error` still writes that to devtools (unsuppressable from
    outside the library) and a `console:debug` line
    ("score-render: suppressed the predicted render failure…") appears - but
    `.error` stays empty, `.notice` still shows the plain sentence, and
    `dataset.scoreRenderOk` flips to `"false"`. This is the path that proves
    the `api.error` + `unrenderable` + `renderInFlight` handling in
    `score-render.js` actually works, independent of whichever path a given
    browser's timing happens to take.

## Notes for porting

- Every assertion above was checked with Playwright's
  `page.locator(...).allTextContents()`,
  `page.locator(...).evaluate(el => ({...el.dataset}))`, `console` /
  `pageerror` event listeners, `page.addStyleTag(...)` (Scenario 5's forced
  path), and `page.setViewportSize(...)` (to trigger a real resize) - none of
  it is Playwright-specific and should translate directly to whatever
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
  read without checking `dataset.scoreRenderOk` first.
- `UNRENDERABLE_MESSAGE` is exported from `score-render.js` - import it
  rather than hardcoding the sentence in a test, so the two cannot drift.
