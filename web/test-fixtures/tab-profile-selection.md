# Tab profile selection — regression spec (issue #66)

Written up because there is no frontend test runner in this repo yet (no
`web/package.json` test script, no Playwright, no specs anywhere in the
tree) - `feature/instruments` is bringing one, with a `Browser tests` CI
job. This document is the three scenarios that verified the fix in
[#69](https://github.com/sirged/fermata/pull/69), broken into fixture /
setup / action / assertion so porting onto that harness is mechanical
rather than a re-investigation. It was checked with Playwright against a
real dev server + backend (not just a build); nothing below depends on
Playwright specifically.

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
   staff - see "Why alphaTab treats these differently" below - the mirror
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

## Why alphaTab treats these differently

`score-render.js`'s `supportedProfiles()` (added in #69) filters
`SCORE_PROFILES = ["score", "tab", "scoretab"]` by asking, per staff:
does it have `showStandardNotation` ("score"), `showTablature && tuning.length > 0`
("tab"), or any of those plus `showSlash`/`showNumbered` ("scoretab")? These
mirror the renderer's own `ScoreBarRendererFactory` / `TabBarRendererFactory`
`canCreate()` checks.

For MusicXML specifically, alphaTab's importer sets these per
`<clef><sign>`:

- `sign=g` (or no clef at all): `showStandardNotation` stays at its class
  default (`true`); `showTablature` stays `false` unless a later
  `<staff-tuning>` is seen for that staff.
- `sign=tab`: `showTablature = true`. The **first** `<staff-tuning>`
  element parsed for that staff also flips `showStandardNotation` to
  `false` (`MusicXmlImporter._parseStaffTuning` in
  `@coderline/alphatab`'s bundle).

That second rule is why fixture 2 offers only Tab/Both, never Notation - it
is not a fixture bug, it is what a real Fermata transcription looks like
today (a single TAB staff, never paired with a separate notation staff), so
the regression spec has to assert exactly that, not "all three."

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
scored count in the library view not matching the scratch library. Binding
literally and checking `curl http://127.0.0.1:<PORT>/api/scores` returns
exactly the scratch library's entries avoids this.

Steps:

1. `cd server && python -m venv .venv && .venv/Scripts/pip install -e .`
   (the project requires Python >=3.12; use `py -3.13` if the default
   `python` on the machine is older).
2. Make a scratch library directory containing only fixture 1 (and, to
   exercise scenario 2, a copy of `docs/examples/monophonic.musicxml`), and
   an empty scratch config directory.
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
- The `.at-host` element's `dataset.scoreProfiles` equals `"score,scoretab"`.
- `.at-host` contains at least one rendered `<svg>` (the score actually
  drew, this isn't a silently-blank view).
- Zero `console:error` and zero `pageerror` events across: page load,
  clicking "Both", clicking back to "Notation", Play, Stop.

**Failure before the fix** (`supportedProfiles()`/`onProfiles` did not
exist, so all three buttons were offered and clicking "Tab" was possible).
Clicking the then-offered "Tab" button produced this exact console output,
and the view froze on its previous ("Both") render:

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

The same message also appeared on the page itself, in `TabViewer`'s
pre-existing `.error` paragraph (bound to `loadError`) - but the "Tab"
button stayed visually selected (`.on`) even though the render behind it
had failed and the old "Both" render was still on screen. That is the
misleading half of the bug: the switcher lied about what was on screen. A
**pre-fix** version of this test would assert `.seg button` texts include
"Tab" and that clicking it produces the console error above; the **post-fix**
version asserts "Tab" is simply absent (see above) - the crash can no
longer be reached through the UI at all.

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
(see "Why alphaTab treats these differently" above), so it is closer to
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

## Notes for porting

- Every assertion above was checked with Playwright's
  `page.locator(...).allTextContents()`,
  `page.locator(...).evaluate(el => ({...el.dataset}))`, and `console` /
  `pageerror` event listeners on the page - none of it is
  Playwright-specific and should translate directly to whatever
  `feature/instruments` brings in.
- `SCORE_PROFILES` and the button label order come from
  `web/src/lib/score-render.js` (`SCORE_PROFILES = ["score", "tab", "scoretab"]`)
  and `web/src/lib/TabViewer.svelte` (`PROFILE_LABELS`, mapping those to
  "Notation"/"Tab"/"Both") - if either changes, the expected button-text
  arrays above need to change with them; they are not independent
  assertions.
- `dataset.scoreProfiles` is written by `score-render.js`'s `publish()`,
  added specifically in #69 so a test can assert on it directly rather than
  parsing button text - prefer it once the harness exists.
