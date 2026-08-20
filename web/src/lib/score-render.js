// The seam between Fermata and its notation renderer. Every setting the
// renderer is given, the theme it draws in, the layout it picks for a width,
// and the one behaviour it offers no setting for, are all decided here.
// Components deal in scores, profiles, widths and themes; nothing outside this
// file imports the renderer or names one of its types. Swapping the renderer
// (VexFlow was the runner-up, and our model lives on the server as MusicXML)
// should mean rewriting this file and nothing else - see docs/rendering.md.
import * as alphaTab from "@coderline/alphatab";

// ---------------------------------------------------------------- profiles

/** Which staves a score is drawn with. */
export const SCORE_PROFILES = ["score", "tab", "scoretab"];

const STAVE_PROFILE = {
  score: alphaTab.StaveProfile.Score,
  tab: alphaTab.StaveProfile.Tab,
  scoretab: alphaTab.StaveProfile.ScoreTab,
};

// ---------------------------------------------------------------- layout

// Widths are of the score container, not the window: a side-by-side PDF
// comparison hands the staff half a desktop, and half a desktop should lay
// out like the tablet it is that wide.
export const PHONE_MAX_WIDTH = 620;
export const TABLET_MAX_WIDTH = 1100;

/**
 * "desk" is a pointer and a whole window, where more bars on screen at once is
 * worth more than size. "stand" is a tablet propped on a music stand, read
 * from a metre away with both hands busy - big glyphs, and scrolling in one
 * direction only. The same width wants different answers in each, so the
 * preset is the caller's to choose rather than a function of the width.
 */
export const LAYOUT_PRESETS = ["desk", "stand"];

// The renderer's own "fit as many as will fit" value.
const AUTO_BARS_PER_ROW = -1;

const LAYOUT_TABLE = {
  desk: [
    { maxWidth: PHONE_MAX_WIDTH, mode: "page", barsPerRow: 1, scale: 1.15 },
    { maxWidth: TABLET_MAX_WIDTH, mode: "page", barsPerRow: 2, scale: 1.05 },
    { maxWidth: Infinity, mode: "page", barsPerRow: AUTO_BARS_PER_ROW, scale: 1 },
  ],
  stand: [
    // One endless system scrolled sideways is what horizontal layout is for,
    // and it is the layout a stand wants: no page breaks to lose your place
    // at, and a single scroll axis a thumb or a pedal can drive.
    { maxWidth: PHONE_MAX_WIDTH, mode: "horizontal", barsPerRow: AUTO_BARS_PER_ROW, scale: 1.3 },
    { maxWidth: TABLET_MAX_WIDTH, mode: "horizontal", barsPerRow: AUTO_BARS_PER_ROW, scale: 1.2 },
    { maxWidth: Infinity, mode: "page", barsPerRow: 2, scale: 1.15 },
  ],
};

const LAYOUT_MODE = {
  page: alphaTab.LayoutMode.Page,
  horizontal: alphaTab.LayoutMode.Horizontal,
};

/**
 * The layout this width and preset should be drawn with. `mode` is ours
 * ("page" / "horizontal"), not the renderer's enum, so callers can read and
 * assert on it without importing anything.
 */
export function layoutForWidth(width, preset = "desk") {
  const rows = LAYOUT_TABLE[preset] ?? LAYOUT_TABLE.desk;
  // width 0 happens once at construction, before the stage is measured
  const w = Number.isFinite(width) && width > 0 ? width : TABLET_MAX_WIDTH;
  return rows.find((r) => w <= r.maxWidth) ?? rows[rows.length - 1];
}

// ---------------------------------------------------------------- theme

/**
 * The renderer exposes six colours and no background at all - the surface it
 * draws on is transparent - so a theme is five tokens: one for the paper the
 * application paints behind the staff, and four the renderer itself uses.
 * Values come from web/src/app.css so the palette has one home; the fallbacks
 * here only matter if a token is missing or unparseable.
 *
 * Kept in sync with SETTINGS_CHOICES["staff_theme"] in
 * server/fermata/api.py - server/tests/test_settings_api.py's
 * test_staff_theme_choices_match_the_frontends_score_themes parses this
 * array out of this file and fails if the two ever disagree.
 */
export const SCORE_THEMES = ["parchment", "noir", "print"];

const THEME_TOKENS = {
  parchment: {
    surface: ["--score-surface", "#f7f2e6"],
    ink: ["--score-ink", "#241d0f"],
    inkSoft: ["--score-ink-soft", "#6f6045"],
    line: ["--score-line", "#b3a284"],
    accent: ["--score-accent", "#8a6a24"],
  },
  // Maximum contrast for a dim room or a bright stage - true black, not the
  // warm dark brown this used to be.
  noir: {
    surface: ["--score-noir-surface", "#000000"],
    ink: ["--score-noir-ink", "#f5f3ea"],
    inkSoft: ["--score-noir-ink-soft", "#9a9488"],
    line: ["--score-noir-line", "#4a4438"],
    accent: ["--score-noir-accent", "#e6c377"],
  },
  // The printed-page look: black ink on white, most legible under harsh
  // light.
  print: {
    surface: ["--score-print-surface", "#ffffff"],
    ink: ["--score-print-ink", "#14110a"],
    inkSoft: ["--score-print-ink-soft", "#6b6558"],
    line: ["--score-print-line", "#c9c3b3"],
    accent: ["--score-print-accent", "#8a6a24"],
  },
};

function cssValue(root, token) {
  try {
    return getComputedStyle(root).getPropertyValue(token).trim();
  } catch {
    return "";
  }
}

/**
 * Ask the renderer's own parser whether it can read this value, because
 * guessing at the syntax it accepts gets it wrong: it splits an `rgb()` body on
 * commas, so the modern space-separated form (`rgb(36 29 15)`) parses to null
 * rather than raising, and a null colour draws as nothing at all. It also
 * throws outright on some malformed input. Anything it will not take is a
 * token we must not use.
 */
function parseColor(value) {
  if (!value) return null;
  try {
    return alphaTab.model.Color.fromJson(value) ?? null;
  } catch {
    return null;
  }
}

/**
 * Resolve a theme's tokens against the live stylesheet. Each token is kept only
 * if the renderer can parse it; otherwise the built-in fallback is used, so a
 * mistyped token degrades to a readable staff rather than an invisible one.
 */
export function readScoreTheme(name = SCORE_THEMES[0], root = document.documentElement) {
  // includes(), not a property lookup: "constructor" and "toString" are
  // truthy on any object and would yield a theme with no colours in it.
  const resolved = SCORE_THEMES.includes(name) ? name : SCORE_THEMES[0];
  const theme = { name: resolved, colors: {} };
  for (const [key, [token, fallback]] of Object.entries(THEME_TOKENS[resolved])) {
    const value = cssValue(root, token);
    const parsed = parseColor(value);
    theme[key] = parsed ? value : fallback;
    theme.colors[key] = parsed ?? parseColor(fallback);
  }
  return theme;
}

// A CSS font-family list ("\"Figtree Variable\", Segoe UI, sans-serif") as the
// renderer wants it: a plain array of family names.
function familyList(root, token, fallback) {
  const raw = cssValue(root, token) || fallback;
  return raw
    .split(",")
    .map((f) => f.trim().replace(/^['"]|['"]$/g, ""))
    .filter(Boolean);
}

function fontsFor(root) {
  const { Font, FontStyle, FontWeight } = alphaTab.model;
  const display = familyList(root, "--font-display", "Georgia, serif");
  const ui = familyList(root, "--font-ui", "Segoe UI, sans-serif");
  return {
    // Fret numbers and their grace-size cousin. These are the two font
    // resources the renderer will accept through settings.
    tablature: Font.withFamilyList(ui, 13, FontStyle.Plain, FontWeight.Bold),
    grace: Font.withFamilyList(ui, 11, FontStyle.Plain, FontWeight.Regular),
    // Title block. These live behind deprecated accessors that write into the
    // renderer's per-element font map, and are ignored if passed as settings,
    // so they are assigned after construction instead.
    title: Font.withFamilyList(display, 32, FontStyle.Plain, FontWeight.Bold),
    subTitle: Font.withFamilyList(display, 19, FontStyle.Plain, FontWeight.Regular),
    words: Font.withFamilyList(ui, 14, FontStyle.Plain, FontWeight.Regular),
    barNumber: Font.withFamilyList(ui, 10, FontStyle.Plain, FontWeight.Regular),
  };
}

// The renderer's colour resources have to hold its own Color objects. A hex
// string is only accepted when the whole settings tree is handed over as JSON
// at construction; assigning one onto a live resource leaves the renderer with
// a string where it expects a colour, and it draws nothing. readScoreTheme
// parsed them once, so this only names them.
function themeColors(theme) {
  const c = theme.colors;
  return {
    mainGlyphColor: c.ink,
    // Voice 2 and up, and whatever else the renderer treats as secondary. Not
    // a note-head or stem colour - there is no such resource; every glyph
    // follows main or secondary according to the voice it belongs to.
    secondaryGlyphColor: c.inkSoft,
    staffLineColor: c.line,
    barSeparatorColor: c.inkSoft,
    barNumberColor: c.accent,
    scoreInfoColor: c.ink,
  };
}

function displaySettings(theme, fonts, layout, profile) {
  return {
    staveProfile: STAVE_PROFILE[profile] ?? STAVE_PROFILE.scoretab,
    layoutMode: LAYOUT_MODE[layout.mode],
    barsPerRow: layout.barsPerRow,
    scale: layout.scale,
    resources: {
      ...themeColors(theme),
      tablatureFont: fonts.tablature,
      graceFont: fonts.grace,
    },
  };
}

// ------------------------------------------------- the branding override

// The renderer draws "rendered by alphaTab" onto every score, from a private
// method on its layout base class, called unconditionally from four render
// paths. There is no setting to turn it off, and its licence (MPL-2.0) asks
// for no attribution in the output - so this reaches past the public API to
// neutralise it rather than vendoring a patched copy of the library, which
// would put us under MPL-2.0's obligation to publish the modified file.
//
// The method is defined on a base class two or three levels above whichever
// layout is active, and is not exported, so the only way to it is up the
// prototype chain from a live layout object. Patching what we find there
// covers every layout mode rather than only the one that happens to be in
// use. If a future version renames or moves it the patch simply misses, the
// annotation comes back, and the warning below is the only thing that will
// tell us - so it warns rather than failing quietly.
const BRAND_METHOD = "_layoutAndRenderAnnotation";

let brandingState = "pending";

function applyBrandingOverride(api) {
  if (brandingState === "applied") return;
  // The layout object is created by the renderer, not the api, and only exists
  // on this thread at all because RENDER_IN_WORKER is off.
  const layout = api?.renderer?.instance?.layout;
  let proto = layout ? Object.getPrototypeOf(layout) : null;
  while (proto && !Object.prototype.hasOwnProperty.call(proto, BRAND_METHOD)) {
    proto = Object.getPrototypeOf(proto);
  }
  if (!proto) {
    if (brandingState !== "missing") {
      brandingState = "missing";
      console.warn(
        `score-render: could not find ${BRAND_METHOD} on the layout's prototype chain. ` +
          "The renderer's own annotation will be drawn on every score until this is updated.",
      );
    }
    return;
  }
  // The original returns the y it was given plus the height it consumed;
  // consuming nothing is exactly what "do not draw it" means.
  proto[BRAND_METHOD] = function neutralised(y) {
    return y;
  };
  brandingState = "applied";
}

// Put the renderer in a worker and its layout object does not exist in our
// realm, so there is no prototype to reach - which is the only reason this is
// off. It measured faster either way: the worker costs 130-260 ms of startup
// and re-renders identically. docs/rendering.md has the numbers.
const RENDER_IN_WORKER = false;

// ------------------------------------------------- auditioning one pitch

// The synthesiser Fermata has is the renderer's, and this is how a single
// pitch is made audible with no score in play. It is what lets a tuning be
// checked by ear, and on an unfretted instrument it is not a convenience: with
// no fret to aim at, a heard pitch is the thing a player matches against, so
// this is the interface rather than an extra.
//
// Played as a one-shot midi file handed to the synth rather than by loading a
// one-note score, because loading replaces whatever the renderer holds and
// forces a render - and the settings view has no score to lose in the first
// place. The renderer will not exist without a container, so the audition view
// gets an off-screen one of its own, built once and lazily: the soundfont is
// worth roughly a megabyte and nothing should pay for it until a string is
// actually clicked.

const AUDITION_CHANNEL = 0;
// Raw midi program numbers are 0-based, so 24 is Acoustic Guitar (nylon) -
// the same voice server/fermata/musicxml.py writes, where MusicXML's 1-based
// numbering calls it 25. What a tuning check needs is a clear fundamental
// with some decay, not the exact timbre of the instrument in hand.
const AUDITION_PROGRAM = 24;
const AUDITION_VELOCITY = 100;
// Long enough to hear against a plucked string and let it decay, short enough
// that clicking down a set of six is not a wait.
const AUDITION_SECONDS = 1.6;
// The renderer's own division, and a tempo that makes a quarter note half a
// second. Both are stated rather than left to the sequencer's defaults so the
// tick arithmetic below has one obvious reading.
const TICKS_PER_QUARTER = 960;
const MICROSECONDS_PER_QUARTER = 500_000;
const TICKS_PER_SECOND = TICKS_PER_QUARTER / (MICROSECONDS_PER_QUARTER / 1_000_000);

const MIN_MIDI = 0;
const MAX_MIDI = 127;

let auditionApi = null;
let auditionReady = null;

function auditionPlayer() {
  if (auditionReady) return auditionReady;
  const host = document.createElement("div");
  host.style.cssText =
    "position:absolute; left:-9999px; top:0; width:0; height:0; overflow:hidden";
  document.body.appendChild(host);
  auditionApi = new alphaTab.AlphaTabApi(host, {
    core: { fontDirectory: "/font/", useWorkers: RENDER_IN_WORKER },
    player: {
      // EnabledSynthesizer, not the automatic mode the score view uses: the
      // automatic mode decides between the synthesiser and an embedded backing
      // track by looking at the loaded score, so with no score it builds no
      // player at all and nothing ever loads.
      playerMode: alphaTab.PlayerMode.EnabledSynthesizer,
      soundFont: "/soundfont/sonivox.sf2",
    },
  });
  // Waits on the soundfont rather than on playerReady, which is the renderer's
  // "ready to play THIS SCORE" and needs a midi file generated from one. There
  // is no score here and never will be, so it would never fire; a loaded
  // soundfont is the whole of what a one-shot note needs.
  auditionReady = new Promise((resolve, reject) => {
    auditionApi.soundFontLoaded.on(() => resolve(auditionApi.player));
    auditionApi.error.on((e) =>
      reject(new Error(e?.message ?? "the synthesiser could not be loaded")),
    );
  });
  return auditionReady;
}

/**
 * Sound one pitch on its own, as a MIDI note number.
 *
 * Resolves true once the note has been handed to the synthesiser, false if the
 * number is not one the synthesiser can sound. Rejects if the synthesiser
 * itself could not be loaded, so a caller can say so rather than leaving a
 * silent click looking like a working one.
 */
export async function playPitch(midi) {
  const key = Math.round(Number(midi));
  if (!Number.isFinite(key) || key < MIN_MIDI || key > MAX_MIDI) return false;
  const player = await auditionPlayer();
  if (!player) return false;
  const {
    MidiFile,
    TempoChangeEvent,
    ProgramChangeEvent,
    ControlChangeEvent,
    ControllerType,
    NoteOnEvent,
    NoteOffEvent,
    EndOfTrackEvent,
  } = alphaTab.midi;
  const end = Math.round(AUDITION_SECONDS * TICKS_PER_SECOND);
  const file = new MidiFile();
  file.division = TICKS_PER_QUARTER;
  file.addEvent(new TempoChangeEvent(0, MICROSECONDS_PER_QUARTER));
  file.addEvent(new ProgramChangeEvent(0, 0, AUDITION_CHANNEL, AUDITION_PROGRAM));
  // The channel is fresh each time only in the sense that the file is; the
  // synth's channel volume is whatever the last thing to play left behind, so
  // it is set rather than assumed.
  file.addEvent(
    new ControlChangeEvent(0, 0, AUDITION_CHANNEL, ControllerType.VolumeCoarse, 127),
  );
  file.addEvent(new NoteOnEvent(0, 0, AUDITION_CHANNEL, key, AUDITION_VELOCITY));
  file.addEvent(new NoteOffEvent(0, end, AUDITION_CHANNEL, key, 0));
  file.addEvent(new EndOfTrackEvent(0, end + 1));
  // Replaces any audition still sounding, which is what clicking down a set of
  // strings in quick succession should do.
  player.playOneTimeMidiFile(file);
  return true;
}

// ---------------------------------------------------------------- the view

/**
 * Create a score view on `host`. Returns a handle whose vocabulary is
 * Fermata's, not the renderer's.
 *
 * @param host      element the score is drawn into
 * @param opts.scroller     scrolling ancestor the playback cursor follows
 * @param opts.source       {kind:"alphatex",text} | {kind:"musicxml",text} | {kind:"file",url}
 * @param opts.profile      one of SCORE_PROFILES
 * @param opts.preset       one of LAYOUT_PRESETS
 * @param opts.theme        one of SCORE_THEMES
 * @param opts.transport    {speed, looping, metronome, countIn} to start at
 * @param opts.onReady      playback became available
 * @param opts.onPlaying    (boolean) transport state changed
 * @param opts.onError      (message) load or render failed
 * @param opts.onPassComplete  one pass finished (drives the tempo ladder)
 * @param opts.onLayout     (layout) the chosen layout changed
 */
export function createScoreView(host, opts = {}) {
  const {
    scroller = null,
    source = null,
    profile: initialProfile = "scoretab",
    preset: initialPreset = "desk",
    theme: initialTheme = SCORE_THEMES[0],
    transport = {},
    onReady = () => {},
    onPlaying = () => {},
    onError = () => {},
    onPassComplete = () => {},
    onLayout = () => {},
  } = opts;

  let profile = SCORE_PROFILES.includes(initialProfile) ? initialProfile : "scoretab";
  let preset = LAYOUT_PRESETS.includes(initialPreset) ? initialPreset : "desk";
  let theme = readScoreTheme(initialTheme);
  const fonts = fontsFor(document.documentElement);

  // The width to choose a layout from is the space the score has to fill - the
  // scrolling stage - and never the host's own width. In horizontal layout the
  // host grows to fit the score it is given, so measuring it would feed the
  // layout's own output back into its input: a wide score would read as a wide
  // screen, flip to the desktop tier, shrink, read as narrow, and oscillate.
  function stageWidth() {
    const el = scroller ?? host;
    if (!el) return 0;
    const style = getComputedStyle(el);
    const padding = Number.parseFloat(style.paddingLeft) + Number.parseFloat(style.paddingRight);
    return el.clientWidth - (Number.isFinite(padding) ? padding : 0);
  }

  let layout = layoutForWidth(stageWidth(), preset);
  let renderStartedAt = 0;
  let lastRenderMs = null;
  let renderCount = 0;
  // a queued resize render must not touch a torn-down renderer
  let destroyed = false;

  const api = new alphaTab.AlphaTabApi(host, {
    // worker/audio-worklet URLs are wired up by the @coderline/alphatab-vite
    // plugin (vite.config.js); fontDirectory/soundFont still need to match
    // where that plugin copies the assets (site root, see its README).
    core: {
      fontDirectory: "/font/",
      useWorkers: RENDER_IN_WORKER,
    },
    player: {
      enablePlayer: true,
      soundFont: "/soundfont/sonivox.sf2",
      scrollElement: scroller ?? undefined,
    },
    display: displaySettings(theme, fonts, layout, profile),
  });

  applyBrandingOverride(api);

  // Fonts for the title block are not readable through settings; they are
  // assigned onto the live resource object, which is why this happens after
  // construction and before the first score is loaded.
  const resources = api.settings.display.resources;
  resources.titleFont = fonts.title;
  resources.subTitleFont = fonts.subTitle;
  resources.wordsFont = fonts.words;
  resources.barNumberFont = fonts.barNumber;
  api.updateSettings();

  // Reflect the layer's decisions onto the host: readable in devtools, and
  // the only thing a test needs in order to assert that a width produced a
  // different layout rather than merely a different screenshot.
  function publish() {
    if (!host) return;
    host.dataset.scoreLayout = layout.mode;
    host.dataset.scoreBarsPerRow = String(layout.barsPerRow);
    host.dataset.scoreScale = String(layout.scale);
    host.dataset.scorePreset = preset;
    host.dataset.scoreTheme = theme.name;
    host.dataset.scoreProfile = profile;
    if (lastRenderMs != null) {
      host.dataset.scoreRenderMs = lastRenderMs.toFixed(1);
      host.dataset.scoreRenders = String(renderCount);
    }
  }
  // Horizontal layout sizes its drawing surface from the total width the
  // renderer reports, but draws partials wider than that and clips the excess
  // with overflow:hidden - on a real transcription that hid 53 of 316 glyphs,
  // the last bar, with nothing able to scroll to them. So after a horizontal
  // render the paper is grown to cover what was actually drawn.
  //
  // The host is also the renderer's own container, so it must be back to its
  // natural width before any render or the renderer would measure the last
  // score's width instead of the screen's. Hence two functions, and the reset
  // runs first.
  function resetSurfaceFit() {
    if (!host) return;
    const surface = host.querySelector(".at-surface");
    if (surface) surface.style.overflow = "";
    host.style.width = "";
    host.style.maxWidth = "";
  }

  function growPaperToDrawing() {
    if (!host || layout.mode !== "horizontal") return;
    const drawn = [...host.querySelectorAll("svg")].reduce((w, s) => Math.max(w, s.getBoundingClientRect().width), 0);
    if (!drawn) return;
    const surface = host.querySelector(".at-surface");
    if (surface) surface.style.overflow = "visible";
    const style = getComputedStyle(host);
    const padding = Number.parseFloat(style.paddingLeft) + Number.parseFloat(style.paddingRight);
    // never narrower than the stage, or a short score would stop mid-screen
    const width = `${Math.max(Math.ceil(drawn + (Number.isFinite(padding) ? padding : 0)), stageWidth())}px`;
    if (host.style.width === width) return;
    // an explicit width, not max-content: the surface clips its own overflow,
    // so content-based sizing would measure the same short width that hid the
    // last bar. The stylesheet's reading-measure cap has to give way here.
    host.style.maxWidth = "none";
    host.style.width = width;
  }

  // Partials reach the DOM after the render reports itself finished, and in
  // horizontal layout more of them arrive as the score is scrolled, so the
  // measurement above has to follow the partials rather than the render.
  // childList only: our own style writes must not retrigger it.
  let fitQueued = false;
  const partialWatcher = new MutationObserver(() => {
    if (fitQueued || layout.mode !== "horizontal") return;
    fitQueued = true;
    requestAnimationFrame(() => {
      fitQueued = false;
      if (!destroyed) growPaperToDrawing();
    });
  });
  if (host) partialWatcher.observe(host, { childList: true, subtree: true });

  publish();
  onLayout(layout);

  // Second chance for the override: the layout object exists by the time the
  // view is built today, but it is created by the renderer rather than the
  // api, so a version that creates it later would still be covered here -
  // before the first layout pass, which is when the annotation is drawn.
  api.renderStarted.on(() => {
    renderStartedAt = performance.now();
    applyBrandingOverride(api);
  });
  api.postRenderFinished.on(() => {
    lastRenderMs = performance.now() - renderStartedAt;
    renderCount += 1;
    publish();
    growPaperToDrawing();
  });

  api.playerReady.on(() => onReady());
  api.playerStateChanged.on((e) => onPlaying(e.state === 1));
  api.error.on((e) => onError(e?.message ?? "failed to load score"));
  // Fires at the end of each loop pass, not only the final stop, which is
  // what makes it usable as "one clean pass done".
  api.playerFinished.on(() => onPassComplete());

  // A fresh renderer starts at its own transport defaults, so carry over
  // whatever the caller was already using.
  if (transport.speed != null) api.playbackSpeed = transport.speed;
  if (transport.looping != null) api.isLooping = transport.looping;
  if (transport.metronome != null) api.metronomeVolume = transport.metronome ? 1 : 0;
  if (transport.countIn != null) api.countInVolume = transport.countIn ? 1 : 0;

  // A tier change is applied as a full render of our own, because the
  // renderer's own answer to a width change is resize-*optimised* rather than
  // a re-layout:
  //
  //  - it only rebuilds the layout when layoutMode itself changed;
  //  - otherwise it asks the layout to resize, and horizontal layout declines
  //    (supportsResize is false, doResize is empty) so nothing is drawn at all;
  //  - and the vertical layouts only regroup systems when barsPerRow is auto.
  //    With an explicit barsPerRow they keep the grouping they already have and
  //    merely refit widths, so phone (1 bar) -> tablet (2 bars) would stay one
  //    stretched bar per row.
  //
  // Watching the stage rather than listening for the renderer's own resize
  // event, because the event reports the host's width - which in horizontal
  // layout is the score's width, not the screen's.
  let resizePending = false;
  const observer = new ResizeObserver(() => {
    const next = layoutForWidth(stageWidth(), preset);
    if (next === layout) return;
    layout = next;
    if (resizePending) return;
    resizePending = true;
    // out of the observer callback: rendering inside it would measure and
    // mutate layout in the same frame the browser is still resolving
    queueMicrotask(() => {
      resizePending = false;
      if (destroyed) return;
      reapply();
      onLayout(layout);
    });
  });
  if (scroller ?? host) observer.observe(scroller ?? host);

  function reapply() {
    // before anything measures it: the host carries the previous layout's
    // width when that layout was horizontal
    resetSurfaceFit();
    // and tell the renderer that width directly rather than waiting for it to
    // notice, so the render below cannot use the stale one
    api.renderer.width = host?.clientWidth ?? 0;
    const display = api.settings.display;
    display.staveProfile = STAVE_PROFILE[profile];
    display.layoutMode = LAYOUT_MODE[layout.mode];
    display.barsPerRow = layout.barsPerRow;
    display.scale = layout.scale;
    Object.assign(display.resources, themeColors(theme));
    api.updateSettings();
    api.render();
    publish();
  }

  function load(next) {
    if (!next) return;
    if (next.kind === "alphatex") {
      api.tex(next.text);
    } else if (next.kind === "musicxml") {
      // Goes through the same byte loader a library file uses: the format is
      // detected from the content, so there is no separate entry point.
      api.load(new TextEncoder().encode(next.text));
    } else if (next.kind === "file") {
      fetch(next.url)
        .then((r) => r.arrayBuffer())
        .then((buf) => api.load(new Uint8Array(buf)))
        .catch((e) => onError(String(e)));
    }
  }

  load(source);

  return {
    get layout() {
      return layout;
    },
    get theme() {
      return theme;
    },
    get profile() {
      return profile;
    },
    get preset() {
      return preset;
    },
    /** Milliseconds of renderer work in the last render, not time to pixels. */
    get lastRenderMs() {
      return lastRenderMs;
    },

    setProfile(next) {
      if (!SCORE_PROFILES.includes(next) || next === profile) return;
      profile = next;
      reapply();
    },
    /** Gig mode wants "stand" at the same width a desk session wants "desk". */
    setPreset(next) {
      if (!LAYOUT_PRESETS.includes(next) || next === preset) return;
      preset = next;
      layout = layoutForWidth(stageWidth(), preset);
      reapply();
      onLayout(layout);
    },
    setTheme(next) {
      const resolved = readScoreTheme(next);
      if (resolved.name === theme.name) return;
      theme = resolved;
      reapply();
    },

    setSpeed(v) {
      api.playbackSpeed = v;
    },
    setLooping(v) {
      api.isLooping = v;
    },
    setMetronome(v) {
      api.metronomeVolume = v ? 1 : 0;
    },
    setCountIn(v) {
      api.countInVolume = v ? 1 : 0;
    },
    playPause() {
      api.playPause();
    },
    stop() {
      api.stop();
    },

    destroy() {
      destroyed = true;
      observer.disconnect();
      partialWatcher.disconnect();
      api.destroy();
    },
  };
}
