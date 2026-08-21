# How scores are rendered

Fermata renders interactive notation and tablature with
[alphaTab](https://alphatab.net) 1.8. Everything the renderer is told lives in
one module, [`web/src/lib/score-render.js`](../web/src/lib/score-render.js).
No component imports the renderer or names one of its types.

This document is for the contributor who looks at that dependency and asks why
it is not VexFlow.

## What was compared

Three candidates were built side by side, all rendering the same file — a
four-bar polyphonic guitar study emitted by Fermata's own MusicXML writer, with
two voices in every bar, a chord, dotted values, a rest and an accidental — and
each given a working "select a note and change its fret" control. The point was
not engraving quality, which all three have. It was **how much of an editor
each one already is**, because score editing is on the roadmap and the browser
library is the part that would have to support it.

| | alphaTab | OpenSheetMusicDisplay | VexFlow |
| --- | --- | --- | --- |
| Reads MusicXML | native | native | none — a reader had to be written |
| Tab staff | native | native | from primitives |
| Notation staff from a TAB-only file | native, one flag | not possible — the file had to be rewritten into two staves | by hand |
| Note under the cursor | native event | hand-built | hand-built |
| Editable model | native and writable | none — read-only getters | ours by construction |
| Incremental re-render | native | none — full reparse and redraw | per measure, if you build it |
| Playback | native synthesiser and soundfont | none | none |
| Writes MusicXML back | no | no | no |
| Lines of integration code | **159** | 374 | 312 |

Measured re-render, Chrome at 1500×1000, medians of five, three independent
runs: alphaTab 2.0–2.5 ms incremental, OSMD 10.0–11.4 ms (its only edit path is
a full reparse), VexFlow 1.9–2.1 ms per measure and 6.2–7.3 ms whole-score.
First render: alphaTab 157–188 ms, OSMD 20–40 ms, VexFlow 25–32 ms. alphaTab's
first render is paying for a font and a soundfont, once.

One finding decided more than the table did. OSMD's tab notes never enter the
DOM, so a glyph-to-note mapping has to be reconstructed by hand from layout
geometry. That mapping agreed with the source on 29 of 29 notes before any
edit — and on roughly 7 of 19 after a single duration change, because the bar
no longer added up, OSMD regrouped its voice entries, and every ordinal after
the edit slid. Nothing threw and nothing warned; the next click would select
the wrong note. That is the characteristic failure of a hand-built bridge
between a drawn glyph and a source note, and it is not a gap that more code
closes.

## Why alphaTab

It is the only one of the three that is an editor substrate rather than a
viewer: the only one with a writable model, the only one with a documented
incremental re-render, the only one that hands over the note under the cursor,
and the only one that makes a sound. An edit is `note.fret = 4`.

VexFlow stayed credible throughout and remains the realistic alternative. It is
MIT, its model would be entirely ours, and it needed no transform of our
single-TAB-staff MusicXML. What it asks for is the rest of an engraving
application: system and page breaking, responsive measure widths, ties and slurs
across barlines, repeats, multi-voice rest placement, a real MusicXML reader,
and an entire audio stack. Choosing VexFlow is choosing to own the editor's
model and layout — which is defensible precisely because Fermata's model is
already on the server as MusicXML, and it is the reason the rendering layer is
built as a seam rather than spread through the components.

OSMD is the weakest fit here, and not for engraving reasons. It is
architecturally a viewer: read-only notes, no incremental render, no note
events, no audio, and the mapping problem above.

## Licence

alphaTab is **MPL-2.0** — file-level copyleft. Linking it from an MIT project is
fine; only modifications to alphaTab's own files would have to be published
under MPL-2.0. It bundles third-party code under MIT and BSD-3-Clause
(TinySoundFont, SFZero, the Haxe standard library, SharpZipLib, NVorbis, and
libvorbis-derived code), listed in the package's own `LICENSE.header`.

This is why the branding override below is a runtime patch from our own code
rather than a vendored copy of the library with one method edited out: a
modified copy would put us under the obligation to publish it.

## What the renderer does natively

MusicXML and Guitar Pro import, notation and tablature staves, page and
horizontal layout, system breaking, a synthesiser with a bundled soundfont, a
following playback cursor, drag-to-select a bar range, looping, a metronome and
a count-in, and an incremental re-render.

## What this layer adds

`score-render.js` exports `createScoreView(host, options)` plus the constants
and pure functions behind it. Its vocabulary is Fermata's — profiles, widths,
presets, themes — not the renderer's.

### One place that configures the renderer

`createScoreView` is the only call site that constructs a renderer. Profiles
(`"score"`, `"tab"`, `"scoretab"`), sources (`{kind:"alphatex"}`,
`{kind:"musicxml"}`, `{kind:"file", url}`), transport state, fonts, colours and
layout all flow through it. The view it returns exposes `setProfile`,
`setPreset`, `setTheme`, `setSpeed`, `setLooping`, `setMetronome`,
`setMetronomeMode`, `setMetronomeProportion`, `setMetronomeBpm`, `setCountIn`,
`playPause`, `stop` and `destroy`, and reports `layout`, `theme`, `profile`,
`supportedProfiles`, `preset` and `lastRenderMs` (`onMetronomeTempo` reports
the practice metronome's own live value - see below, since it changes on its
own while playing rather than only when asked for).

Which profiles a caller may ask for is score-dependent, not fixed: a score
does not necessarily support all of `SCORE_PROFILES`, and `createScoreView`
does not just trust whatever `profile` it was given. `onProfiles(profiles)`
fires once the loaded score's own content has been inspected, with the
subset it can actually be drawn under (possibly empty - see
`supportedProfiles()`); `onProfileApplied(profile)` fires separately, once a
render with that profile has actually finished, which is what a caller
should wait for before treating a profile switch as visible on screen rather
than merely requested.

### The practice metronome is not alphaTab's

alphaTab has its own metronome (`api.metronomeVolume`), and this layer never
turns it on. It stays permanently muted, set once at construction. The reason
is structural, not a style choice: the renderer generates its metronome as a
track in the same MIDI file as the notes, ticking at the score's own tempo and
scaled by `playbackSpeed` exactly like every other event in that file. There is
no setting anywhere in alphaTab's public API that lets the click run at a
different tempo than the notes beside it - the two are the same timeline. That
is fine until practice wants them to disagree, which is normal and constant: a
click at full tempo over a passage slowed to 70% for a hard bar, or a fixed BPM
that has nothing to do with what the score happens to declare.

So `createScoreView`'s metronome (`setMetronome`, `setMetronomeMode`,
`setMetronomeProportion`, `setMetronomeBpm`) is a second, wholly independent
audio path: a plain Web Audio oscillator click, scheduled off its own clock via
the standard lookahead pattern (queue whatever falls within the next ~120ms of
audio-clock time, on a 25ms poll), rather than a setting on alphaTab's player.
It never touches alphaTab's synthesiser, its generated MIDI, or its notion of
tempo. Two modes:

- `"proportion"` (the default) clicks a proportion of the score's own tempo
  *at the playhead*, read from `playerPositionChanged`'s `originalTempo` -
  the tempo alphaTab is internally playing at before `playbackSpeed` is
  applied - so a piece that changes tempo mid-stream is tracked continuously
  rather than resolved once when playback starts.
- `"bpm"` clicks a number set directly and ignores the score.

Time signature (for subdivision and which click is accented) comes from a
flat tick-ordered index built from `score.masterBars` once per load, looked up
against `api.tickPosition` on every scheduled click - not from alphaTab's own
metronome MIDI events, which exist (`MidiEventType.AlphaTabMetronome`) but are
timed on the same speed-scaled timeline this feature exists to get away from.

The count-in (`setCountIn`) is untouched by any of this - a separate,
pre-roll-only feature that still uses alphaTab's own click at the score's
tempo, scaled by `playbackSpeed`, exactly as before.

### Responsive layout

The layer picks `layoutMode`, `barsPerRow` and `scale` from the width of the
**scrolling stage** — not the window, because a side-by-side PDF comparison
hands the staff half a desktop and half a desktop should lay out like the tablet
it is that wide. Breakpoints are `PHONE_MAX_WIDTH` (620) and `TABLET_MAX_WIDTH`
(1100).

Two details of this are load-bearing and were both got wrong first time round.

**The width comes from the stage, never from the host.** The host is also the
renderer's own container, and in horizontal layout it grows to fit the score
(see the quirks below) — so measuring it would feed the layout's output back
into its input: a wide score reads as a wide screen, flips to the desktop tier,
shrinks, reads as narrow, and oscillates. A `ResizeObserver` on the stage, whose
width never depends on the layout chosen, is what drives the decision.

**A tier change is a full render of the layer's own.** The renderer's answer to
a width change is resize-*optimised* rather than a re-layout, and it is not
enough:

- it rebuilds the layout only when `layoutMode` itself changed;
- otherwise it asks the layout to resize, and `HorizontalScreenLayout` declines
  — `supportsResize` is `false` and `doResize()` is empty — so **nothing is
  drawn at all**. Crossing 620px in the `stand` preset is horizontal to
  horizontal with only a scale change, which is exactly this case;
- and the vertical layouts regroup systems only when `barsPerRow` is auto.
  `_resizeAndRenderScore` tests `getBarsPerSystem(0) > 0` against the *new*
  value and, when set, keeps the grouping it already has and merely refits
  widths — so phone (1 bar) to tablet (2 bars) would stay one stretched bar per
  row.

Relying on that path meant the layer could announce a new scale it had not
drawn. It now renders the tier change itself, from a microtask so the renderer's
new width is already assigned, and before the next paint so no intermediate
state is visible. On a 360-bar score that render is 29–61 ms; on a normal one it
is 1–4 ms.

There are two presets, because the same width wants different answers depending
on where the screen is:

| | phone (≤620) | tablet (≤1100) | desktop |
| --- | --- | --- | --- |
| `desk` | page, 1 bar/row, scale 1.15 | page, 2 bars/row, scale 1.05 | page, auto bars/row, scale 1.00 |
| `stand` | horizontal, scale 1.30 | horizontal, scale 1.20 | page, 2 bars/row, scale 1.15 |

`desk` is a pointer and a whole window: more bars on screen at once is worth
more than size. `stand` is gig mode — a tablet propped on a music stand, read
from a metre away with both hands busy — so glyphs get bigger and the layout
becomes one endless system scrolled sideways, which is what horizontal layout
exists for: no page breaks to lose your place at, and a single scroll axis a
thumb or a pedal can drive.

The layer's decisions are reflected onto the host element as `data-score-*`
attributes, so what it chose is readable in devtools and assertable in a test
rather than only visible in a screenshot.

### Theming

The staff is drawn from Fermata's own palette. The tokens live in
`web/src/app.css` and the layer reads them from the live stylesheet, so the
palette has one home:

```
--score-surface  --score-ink  --score-ink-soft  --score-line  --score-accent
--score-noir-surface  --score-noir-ink  --score-noir-ink-soft  ...
--score-print-surface  --score-print-ink  --score-print-ink-soft  ...
```

Three themes: `parchment` (warm paper, dark ink - the default, matching the
rest of the interface), `noir` (true black with near-white ink, for a dim room
or a bright stage) and `print` (black ink on white, the printed-page look,
most legible under harsh light). The PDF reader has to invert a fixed page; a
rendered staff can simply be drawn in whichever of these it needs.

The staff theme is a user setting stored on the server (`/api/settings`), not
browser-local, so it follows a person between devices - see
`web/src/lib/settings.svelte.js` and the settings view
(`web/src/lib/Settings.svelte`).

Values must stay in a form the renderer's colour parser accepts: hex, or the
**comma-separated** `rgb()`/`rgba()` form. It splits the function body on
commas, so the modern space-separated syntax — `rgb(36 29 15)` — is not one of
them; it parses to `null`, and a null colour draws as nothing at all. Some
malformed input makes it throw instead. So the layer does not try to recognise
the syntax itself: it hands each token to the parser, catches both answers, and
falls back to a built-in default for anything the parser will not take. A
mistyped token gives a readable staff, not an invisible one.

**Exactly what the renderer exposes** — this is the whole surface:

| Resource | What it colours |
| --- | --- |
| `mainGlyphColor` | every glyph of voice 1: note heads, stems, beams, rests, clefs, fret numbers |
| `secondaryGlyphColor` | the same for voice 2 and up |
| `staffLineColor` | staff and tab lines |
| `barSeparatorColor` | barlines, braces and brackets |
| `barNumberColor` | bar numbers |
| `scoreInfoColor` | title, subtitle, words |
| `tablatureFont`, `graceFont`, `numberedNotationFont`, `numberedNotationGraceFont` | the four font resources settable through settings |
| `elementFonts` | per-element fonts (title, subtitle, words, bar numbers, markers, …), reachable only through the resource object, not through settings |
| `engravingSettings` | SMuFL metrics — stem thickness, staff line thickness, beam spacing, and ~80 more |

**What is therefore out of reach of configuration:**

- **There is no separate note-head, stem or beam colour.** Every one of them
  follows `mainGlyphColor` or `secondaryGlyphColor` according to which voice the
  note belongs to. Anything finer means writing colours into the score model
  (`note.style.colors` and friends) after every load — per-document overrides,
  not configuration.
- **There is no background colour, and no background at all.** The surface the
  renderer draws on is transparent and it emits no background rectangle. The
  paper behind the staff is ours, in CSS.
- **Cursors and the range selection ship with no colour.** The renderer creates
  `.at-cursor-bar`, `.at-cursor-beat` and the selection divs with position only,
  which means they are invisible until styled. They are styled in
  `TabViewer.svelte` from the same tokens — before this layer they were not
  styled at all.
- One hardcoded exception found in the source: a tab bend spanning more than one
  note is forced to the secondary colour regardless of voice.

### Horizontal layout, and where the paper ends

Two things the layer has to correct in horizontal layout, both of which are
invisible in page layout and so easy to ship broken:

- The renderer draws the whole score as one system, far wider than any sensible
  card. Left alone, the staff scrolls off the paper onto the page background —
  and with the parchment theme that is dark ink on a dark page, effectively
  invisible. `noir` hides the problem too, since its surface is close enough
  to the page's own dark background; `print`'s white surface would have made
  it obvious immediately.
- The renderer sizes its drawing surface from the total width it *reports*, but
  draws partials wider than that and clips the excess with `overflow: hidden`.
  On a real transcription that hid 53 of 316 glyphs — the last bar and the final
  barline — with no scroll position that could reach them.

So after a horizontal render the layer measures what was actually drawn and sets
the host's width and the surface's overflow to match. Because the host is also
the renderer's container, that width is cleared again before any render, or the
renderer would measure the last score's width instead of the screen's. Partials
arrive after the render reports itself finished — and more of them arrive as the
score is scrolled — so the measurement follows a `MutationObserver` on the host
rather than the render event.

### The branding override

The renderer draws **"rendered by alphaTab"** onto every score. It comes from
`_layoutAndRenderAnnotation`, a private method on its layout base class, called
unconditionally from four render paths. There is no setting to disable it, and
the MPL-2.0 licence asks for no attribution in rendered output.

`score-render.js` neutralises it at runtime:

- It walks up the prototype chain from the live layout object to whichever
  prototype **owns** the method, and replaces it there. In 1.8.4 the concrete
  layouts are `PageViewLayout` and `ParchmentLayout` (via `VerticalLayoutBase`)
  and `HorizontalScreenLayout` (directly), and the owner is `ScoreLayout` in
  every case — so patching the owner covers all layout modes rather than only
  whichever one happens to be active. None of those classes is exported, so a
  live instance is the only route to them.
- It is applied once, to the prototype, not per view.
- If the method cannot be found it **warns to the console and carries on**. The
  only consequence of the patch missing is that the annotation comes back, so
  failing loudly would be worse than a warning — but failing silently would mean
  noticing in a screenshot months later.

Two things about this are worth knowing before an upgrade.

**It reaches past the public API.** `_layoutAndRenderAnnotation` is not declared
in the type definitions and its owner is not exported. It is present verbatim in
the bundle the app imports (`dist/alphaTab.core.mjs`, which is not minified), and
a stock Vite production build does not mangle property names — verified against
the built output, where the annotation is still absent. A future version that
renames or moves the method will trip the warning.

**It requires the renderer on the main thread.** By default alphaTab runs layout
and drawing in a Web Worker, and then the layout object does not exist in our
realm at all — there is no prototype to patch. So the layer sets
`core.useWorkers: false` (the `RENDER_IN_WORKER` constant). Measured cost, same
scores, same machine:

| | main thread | in a worker |
| --- | --- | --- |
| First paint, navigation to first staff on screen | 267–403 ms | 504–621 ms |
| First render, as the library reports it | 6.9–16 ms | 7.9–13.5 ms |
| Profile switch, library work | 0.5–1.0 ms | 0.2–0.6 ms |
| Profile switch, to pixels | 16–35 ms | 17–36 ms |

The worker costs 130–260 ms of startup and buys nothing measurable back. The
cost it is meant to avoid is a main-thread stall on a long score, so that was
measured too: a **360-bar** score renders in 69 ms of library work and 103 ms
wall to first paint, a tier change re-renders in 29–61 ms, and the longest
main-thread task across the whole run is **102 ms**, with none over 250 ms —
because the renderer chunks the work into partials and only draws the ones on
screen. That is the trade this one constant represents, if it ever bites.

### Asking the renderer what it can draw

Not every score can be drawn under every profile. A score with no tablature has
no tab staff to draw, and asking for one used to throw inside the renderer and
leave the view dead — the staff system was built with zero staves and the code
that adds bars to it dereferenced a staff group that was never created.

The profile buttons therefore offer only what the loaded score can actually
draw. The tempting way to decide that is to restate the renderer's rules —
standard notation for one profile, a tuned tab staff for another — but a
restatement is only correct until the library changes its mind, and a wrong
answer either hides a profile that works or offers one that crashes. Neither
failure announces itself.

So the question is put to the library instead. Two static fields on
`Environment` carry the answer:

- `staveProfiles`, a `Map` from each profile to the set of staff ids it draws.
  In 1.8.4 the default and combined profiles map to all four ids, the notation
  profile to `score` alone, and both tab profiles to `tab` alone.
- `defaultRenderers`, the bar-renderer factories, each exposing a `staffId` and
  a `canCreate(track, staff)` predicate.

A profile is drawable when some factory whose `staffId` the profile includes
says it can create a renderer — which is the same test the renderer itself
applies when it builds a staff system. Because it is the deciding code rather
than a description of it, it cannot quietly disagree with the renderer.

Three things about this are worth knowing before an upgrade.

**It reaches past the public API, in the same way the branding override does.**
Both fields are plain public statics, so they are reachable at runtime in the
bundle the app imports, but they are tagged internal and so absent from the type
definitions. The layer checks that both are present and shaped as expected, and
falls back to restating the rules — with a warning — if they are not. That way a
version that moves them degrades to a maintained approximation instead of
returning nonsense.

**One staff is not enough to decide.** The renderer only fails when *no* staff
is created across every track and staff in the system, so the answer has to be
OR-ed over every pair being rendered. Deciding from the first staff alone gets a
multi-track score wrong — a score whose second track carries the tablature would
be told it has none.

**The percussion rule is already handled, and not by us.** Only the tab factory
declines percussion staves, and `Staff.finish` clears a percussion staff's
tuning and turns its tablature off, so the two conditions cannot coexist on a
finished staff. Every importer calls `finish`, and so does the deserialisation
step used when rendering in a worker. The one path that does not is handing the
api an already-built score object on the main thread — which is the mode this
layer runs in, so it matters here more than it would elsewhere. Nothing in
Fermata does that today: scores arrive as alphaTex or MusicXML and go through an
importer. Delegating to `canCreate` covers the case regardless.

### The resize handler that cannot be turned off

The library registers its own resize handler in its constructor, throttled at a
hardcoded 10 ms. There is no setting to disable it, the unsubscribe function it
returns is discarded, and the container class that owns the event is not
exported — so a consumer running its own `ResizeObserver`, as this layer does,
cannot switch the library's off through any public route.

It is quiescent rather than absent: the handler returns immediately unless the
container's width differs from the width the renderer last used. That is the
only lever available, and it is why the observer here measures the scrolling
stage and not the host element. Measuring the host let a re-layout change the
host's width, which woke this handler, which triggered another re-layout — an
oscillation that was not obvious from either side alone, because each half looks
correct in isolation. Anyone changing the observer needs to know there is a
second one they cannot see.

## Keeping it replaceable

The reason this is a seam and not a scattering of settings is that VexFlow
remains a credible alternative, and it stays credible because the authoritative
model lives on the server as MusicXML — the browser library is a view, not the
source of truth. So:

- Components pass and read Fermata's vocabulary. `layout.mode` is `"page"` or
  `"horizontal"`, not an enum from the library. `profile` is `"scoretab"`, not a
  stave-profile constant. Themes are token names.
- Loading is expressed as a source shape (`alphatex`, `musicxml`, `file`), not as
  the byte-loader call the library happens to want.
- The transport is `setSpeed` / `setLooping` / `setMetronome` /
  `setMetronomeMode` / `setMetronomeProportion` / `setMetronomeBpm` /
  `setCountIn` / `playPause` / `stop`, not volume fields and boolean
  properties on a live api object - and the practice metronome's click is not
  even one of those properties (see "The practice metronome is not
  alphaTab's" above); it is a second audio path this file owns outright.
- The renderer's event names, enums, settings tree, colour objects, font objects
  and prototype quirks all stop at this file.

If replacing the renderer would mean touching anything other than
`score-render.js` and the `--score-*` tokens, the boundary has drifted and
should be pulled back.
