// The seam between Fermata and its notation renderer. Every setting the
// renderer is given, the theme it draws in, the layout it picks for a width,
// and the one behaviour it offers no setting for, are all decided here.
// Components deal in scores, profiles, widths and themes; nothing outside this
// file imports the renderer or names one of its types. Swapping the renderer
// (VexFlow was the runner-up, and our model lives on the server as MusicXML)
// should mean rewriting this file and nothing else - see docs/rendering.md.
import * as alphaTab from "@coderline/alphatab";
import { createMetronomeEngine } from "./metronome-engine.js";
import { barAtTick } from "./metronome.js";

// ---------------------------------------------------------------- profiles

/** Which staves a score is drawn with. */
export const SCORE_PROFILES = ["score", "tab", "scoretab"];

const STAVE_PROFILE = {
  score: alphaTab.StaveProfile.Score,
  tab: alphaTab.StaveProfile.Tab,
  scoretab: alphaTab.StaveProfile.ScoreTab,
};

// A profile only draws a (track, staff) pair if the renderer has a bar
// renderer willing to take it - standard notation for "score", a strung
// tuning for "tab", any glyph type at all for "scoretab". When every pair in
// the rendered set fails a profile's check, the renderer builds a staff
// system with no staves in it at all, and StaffSystem.addBars crashes
// dereferencing the group that was never created - that crash is what sends
// a MusicXML file with pitches but no fret/string data into a broken view the
// moment "Tab" is chosen. Asking first, here, is what lets a caller offer
// only profiles that will not do that.
//
// This mirrors PageViewLayout.createEmptyStaffSystem's own test -
// `this.profile.has(factory.staffId) && factory.canCreate(track, staff)` -
// against Environment.staveProfiles / Environment.defaultRenderers, the
// renderer's own lookup and factory list. Both are plain public static class
// fields (`/** @internal */` only in the source, which is why they are
// missing from the shipped .d.ts and easy to miss), so calling the real
// canCreate() is possible and is what canDraw below does - a
// reimplementation of the rules can only ever be *consistent with* the
// library, never *be* it, and would silently answer wrong the day a rule
// changes underneath it. It also picks up canCreate's other terms for free,
// e.g. TabBarRendererFactory's hideOnPercussionTrack, without this file
// having to know they exist.
//
// mirroredCanDraw is the fallback for when that internal shape moves or is
// renamed, or when calling the real canCreate() throws despite looking
// right - see environmentCanDraw and canDraw below - kept as a hand-written
// second opinion rather than deleted once the delegation was written.
function mirroredCanDraw(_track, staff, profileKey) {
  const hasTab = staff.showTablature && staff.tuning.length > 0;
  switch (profileKey) {
    case "score":
      return staff.showStandardNotation;
    case "tab":
      return hasTab;
    case "scoretab":
      return staff.showStandardNotation || hasTab || staff.showSlash || staff.showNumbered;
    default:
      return false;
  }
}

/**
 * The delegate for canDraw(track, staff, profileKey), built from the
 * renderer's own internals - or null if they are not shaped the way this
 * expects, so the caller can fall back rather than trust a wrong answer.
 * Checked once, at module load: Environment's static fields are initialised
 * when the class is defined, which has already happened by the time this
 * module's top-level code runs the import above.
 */
function environmentCanDraw() {
  const env = alphaTab.Environment;
  const staveProfiles = env?.staveProfiles;
  const renderers = env?.defaultRenderers;
  const expectedStaveProfiles = [STAVE_PROFILE.score, STAVE_PROFILE.tab, STAVE_PROFILE.scoretab];
  if (
    !(staveProfiles instanceof Map) ||
    !expectedStaveProfiles.every((k) => staveProfiles.get(k) instanceof Set) ||
    !Array.isArray(renderers) ||
    renderers.length === 0 ||
    !renderers.every((f) => typeof f?.staffId === "string" && typeof f?.canCreate === "function")
  ) {
    return null;
  }
  // The checks above only validate the *containers* - that staveProfiles is a
  // Map of Sets and every factory exposes a staffId string and a canCreate
  // function - never that the two collections still refer to the same
  // things. A future alphaTab release could rename every staffId (in both
  // places, consistently with itself) and pass every check above while
  // staveProfiles' sets and defaultRenderers' ids no longer overlap at all -
  // every profile would then match zero factories, and supportedProfiles()
  // would silently answer "nothing is drawable" for every score in the
  // library, with no warning, which is the exact failure this guard exists
  // to catch. Require the two to actually agree: the union of every stave
  // profile's staff ids has to intersect the renderers' ids somewhere.
  const renderersStaffIds = new Set(renderers.map((f) => f.staffId));
  const allProfileStaffIds = new Set(expectedStaveProfiles.flatMap((k) => [...staveProfiles.get(k)]));
  if (![...allProfileStaffIds].some((id) => renderersStaffIds.has(id))) return null;
  return (track, staff, profileKey) => {
    // Map.get returns undefined for a key that was never registered - only
    // reachable here if profileKey is not one of SCORE_PROFILES.
    const staffIds = staveProfiles.get(STAVE_PROFILE[profileKey]);
    if (!staffIds) return false;
    return renderers.some((f) => staffIds.has(f.staffId) && f.canCreate(track, staff));
  };
}

const delegatedCanDraw = environmentCanDraw();
if (!delegatedCanDraw) {
  console.warn(
    "score-render: alphaTab's Environment.staveProfiles/defaultRenderers are not shaped as " +
      "expected (an internal API this file depends on has likely moved or been renamed) - " +
      "falling back to a hand-mirrored profile-support check, which can silently drift from " +
      "the renderer's own rules after an upgrade. Update environmentCanDraw() in score-render.js.",
  );
}
// The shape guard above only checks that canCreate *is* a function, never
// that calling it is actually safe - a changed parameter list or return
// contract would pass the guard and then throw the first time it is
// actually called. That throw is caught here rather than left to escape:
// once caught, this permanently downgrades to mirroredCanDraw for the rest
// of the session (a structural incompatibility like a changed signature
// will not un-happen on the next call) and warns exactly once, rather than
// warning - or crashing whatever called it - every time.
let canDrawBroken = false;
function canDraw(track, staff, profileKey) {
  if (delegatedCanDraw && !canDrawBroken) {
    try {
      return delegatedCanDraw(track, staff, profileKey);
    } catch (e) {
      canDrawBroken = true;
      console.warn(
        "score-render: alphaTab's canCreate() threw when called, despite passing the shape guard " +
          "(likely a changed parameter list or return contract) - falling back to the hand-mirrored " +
          "profile-support check for the rest of this session.",
        e,
      );
    }
  }
  return mirroredCanDraw(track, staff, profileKey);
}

/**
 * Which of SCORE_PROFILES the given tracks can actually be drawn with, in
 * SCORE_PROFILES order. Can legitimately be empty: a MusicXML part carrying a
 * percussion clef together with `<staff-details><staff-tuning>` imports as
 * showStandardNotation=false, showTablature=false (Staff.finish clears tuning
 * and tablature for a percussion staff) and showSlash=showNumbered=false too
 * - nothing this file, or the renderer, can draw that with. An earlier
 * version of this function answered a wholly-unsupported score with "offer
 * everything instead of nothing", which only moved this exact crash from a
 * click to page load: the default profile would pass the (now permissive)
 * support check, the renderer would still find zero drawable staves on its
 * first render, and StaffSystem.addBars would still throw. There is no
 * profile a caller can pick that fixes a score with nothing to draw - the
 * caller has to be told that plainly instead (see UNRENDERABLE_MESSAGE and
 * its use in createScoreView below), not handed a full-but-lying menu.
 *
 * The crash this exists to prevent only happens when *every* (track, staff)
 * pair in the rendered set fails a profile's check - the renderer loops over
 * all of them building one staff system - so a profile is supported the
 * moment any single pair can draw it: this ORs canDraw across every pair, it
 * does not decide from one staff. A score with two staves in the one track
 * that gets rendered - one notation-only, one tab-only - still has to offer
 * both "score" and "tab", each justified by only one of the two staves; see
 * multi-staff.musicxml in web/test-fixtures for a fixture built to reach
 * exactly this (deliberately one track, not two - only the first track
 * renders by default, so a second track would never be part of the pairs
 * this function is given at all, and would not exercise the OR here).
 */
export function supportedProfiles(tracks) {
  // The whole body is inside the try, not just the canDraw loop: a malformed
  // track (a `.staves` access that itself throws, say) has to degrade the
  // same way a throwing canCreate() does, not escape past this function
  // entirely just because it happened one line earlier.
  try {
    const pairs = (tracks ?? []).flatMap((t) => (t?.staves ?? []).map((s) => [t, s]));
    return SCORE_PROFILES.filter((p) => pairs.some(([t, s]) => canDraw(t, s, p)));
  } catch (e) {
    // canDraw's own try/catch already turns a throwing canCreate() into a
    // permanent, warned-once fallback (see above) - this is a second, outer
    // net for anything else that could go wrong here. Without it, a throw
    // escapes all the way out of the scoreLoaded handler below, skipping
    // publish() and onProfiles() entirely: profileOptions would stay null
    // forever, showing neither buttons nor the unrenderable notice, and the
    // renderer's own attempt to draw with whatever profile it already had
    // would still run and surface its raw error - silence that then breaks
    // into the exact failure mode this file exists to prevent, rather than
    // degrading to a checked answer.
    console.warn("score-render: supportedProfiles() failed unexpectedly - falling back to the mirrored check.", e);
    try {
      const pairs = (tracks ?? []).flatMap((t) => (t?.staves ?? []).map((s) => [t, s]));
      return SCORE_PROFILES.filter((p) => pairs.some(([t, s]) => mirroredCanDraw(t, s, p)));
    } catch {
      // Even re-reading `tracks` failed both times - there is nothing left
      // to answer from. Offering nothing is the safe direction to fail in
      // (see "A score that draws nothing" in the spec): the caller shows its
      // plain notice rather than a menu this function cannot vouch for.
      return [];
    }
  }
}

/** Shown in place of the staff view when supportedProfiles() comes back empty. */
export const UNRENDERABLE_MESSAGE = "This score has no notation or tablature the staff view can draw.";

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
// Generous - the soundfont is about a megabyte and a cold cache on a slow link
// is not a failure - but finite, so a stalled fetch is recoverable.
const SOUNDFONT_TIMEOUT_MS = 45_000;

let auditionApi = null;
let auditionHost = null;
let auditionReady = null;

// Puts the audition back to never-having-been-built. Called on any failure, so
// the next click starts a fresh attempt: a cached rejection would turn one
// failed soundfont fetch into every play button being dead for the life of the
// page, with nothing but a reload to recover.
function resetAudition() {
  try {
    auditionApi?.destroy();
  } catch {
    // a half-constructed renderer may not survive its own teardown; the host
    // still has to go
  }
  auditionHost?.remove();
  auditionApi = null;
  auditionHost = null;
  auditionReady = null;
}

function auditionPlayer() {
  if (auditionReady) return auditionReady;

  const host = document.createElement("div");
  host.style.cssText =
    "position:absolute; left:-9999px; top:0; width:0; height:0; overflow:hidden";
  document.body.appendChild(host);
  auditionHost = host;

  let api;
  try {
    api = new alphaTab.AlphaTabApi(host, {
      core: { fontDirectory: "/font/", useWorkers: RENDER_IN_WORKER },
      player: {
        // EnabledSynthesizer, not the automatic mode the score view uses: the
        // automatic mode decides between the synthesiser and an embedded
        // backing track by looking at the loaded score, so with no score it
        // builds no player at all and nothing ever loads.
        playerMode: alphaTab.PlayerMode.EnabledSynthesizer,
        soundFont: "/soundfont/sonivox.sf2",
      },
    });
  } catch (e) {
    // The host is already in the document, so it has to come back out here or
    // every retry would leave another orphan behind it.
    resetAudition();
    return Promise.reject(e instanceof Error ? e : new Error(String(e)));
  }
  auditionApi = api;

  // Waits on the soundfont rather than on playerReady, which is the renderer's
  // "ready to play THIS SCORE" and needs a midi file generated from one. There
  // is no score here and never will be, so it would never fire; a loaded
  // soundfont is the whole of what a one-shot note needs.
  //
  // Failure comes from the synth's own soundFontLoadFailed where there is one,
  // rather than from api.error: that fires for anything at all, and a render
  // complaint has no business disabling playback.
  const ready = new Promise((resolve, reject) => {
    api.soundFontLoaded.on(() => resolve(api.player));
    const failed = api.player?.soundFontLoadFailed;
    if (failed) {
      failed.on((e) => reject(toError(e, "the synthesiser's soundfont could not be loaded")));
    } else {
      api.error.on((e) => reject(toError(e, "the synthesiser could not be loaded")));
    }
    // A fetch that never resolves and never errors is not covered by either
    // event, and without this the promise would be cached in a pending state
    // for the life of the page - a hang being permanent where a failure is
    // retryable, which is the wrong way round.
    setTimeout(
      () => reject(new Error("the synthesiser took too long to load")),
      SOUNDFONT_TIMEOUT_MS,
    );
  }).catch((e) => {
    // Guarded on identity so a failure arriving late cannot tear down a newer
    // attempt that has already succeeded.
    if (auditionApi === api) resetAudition();
    throw e;
  });

  auditionReady = ready;
  return ready;
}

function toError(e, fallback) {
  // Tested on the MESSAGE, not just the type, and joined with `||` rather than
  // `??`: an Error whose message is the empty string is common (an aborted fetch
  // is one) and is not a usable explanation. Passing it through renders as
  // nothing, which is a failure indistinguishable from a working click - exactly
  // what playPitch promises not to leave behind.
  if (e instanceof Error && e.message) return e;
  return new Error(e?.message || fallback);
}

/**
 * Sound one pitch on its own, as a MIDI note number.
 *
 * Resolves with THE MIDI NOTE ACTUALLY SOUNDED - the number written into the
 * note-on event - or null if it is not one the synthesiser can play. Returning
 * the note rather than a success flag is deliberate: it lets a caller display
 * and publish what crossed this boundary instead of restating what it asked
 * for, which is the only way an interface can be observed to have played the
 * right pitch rather than merely to have intended one.
 *
 * Rejects if the synthesiser could not be loaded, so a caller can say so rather
 * than leaving a silent click looking like a working one.
 *
 * Note that the synthesiser is equal-tempered around A440 and takes no
 * reference pitch, so an instrument defined at A415 has its frequencies shown
 * at A415 but is auditioned at A440. Fine for finding a note by ear against
 * itself; not yet a period-pitch reference.
 */
export async function playPitch(midi) {
  const key = Math.round(Number(midi));
  if (!Number.isFinite(key) || key < MIN_MIDI || key > MAX_MIDI) return null;
  const player = await auditionPlayer();
  if (!player) return null;
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
  // Read back off the events that were handed over rather than off the input,
  // so what this reports is the note the synthesiser actually received.
  const noteOn = file.events.find((e) => e instanceof NoteOnEvent);
  return noteOn ? noteOn.noteKey : key;
}

// ------------------------------------------------- the practice metronome
//
// The click itself is NOT here. It lives in metronome-engine.js, as a general
// tool every part of Fermata calls - the score viewer, the practice page, and
// on its own - and it knows nothing about scores or this renderer. See that
// file's own header for why an independent Web Audio path is the only way a
// click can run at a tempo the notes beside it are not.
//
// What is left here is the only part that genuinely belongs behind this seam:
// translating the renderer's vocabulary into the plain shapes the engine
// accepts. Three things, and nothing else -
//
//   1. the playhead and the bar sounding at it, from api.tickCache;
//   2. the score's own tempo at the playhead, from playerPositionChanged;
//   3. when real playback is under way, which is NOT the same question as
//      alphaTab's own Playing state - see setPlaying below.
//
// alphaTab's own metronome is left permanently muted (metronomeVolume stays
// 0, set once below and never touched again) so the two never sound at once.

/**
 * Where the tempo `score.tempo` hands back actually came from: "start" (the
 * document declares it), "later" (the document declares a tempo, but not one
 * that applies at the start, so the reported number is still the fallback), or
 * "none" (the document declares no tempo anywhere).
 *
 * This exists because `score.tempo` cannot answer the question. It is a
 * getter over the first bar's tempo automations that returns a hard-coded
 * 120 when there are none, so a score printing no tempo at all and a score
 * printing "quarter = 120" are indistinguishable through it - which is how
 * the metronome came to report "marked quarter = 120" for editions that
 * print *Andante* and no number at all (issue #102). A `?? null` guard on
 * that getter is dead code: it never returns null.
 *
 * Nor can `score.tempoLabel` answer it, despite being the obvious candidate.
 * It is the automation's `text`, and alphaTab's MusicXML importer never sets
 * that - it is "" for a score with a printed metronome mark of 96 exactly as
 * it is for a score with nothing. Measured against the real importer, on the
 * three shapes that matter: a `<metronome>` mark, a bare `<sound tempo=>`,
 * and a document with neither.
 *
 * What does answer it is `isVisible`. ModelUtils.consolidate() runs at the
 * end of every import and, when the first bar has no tempo automation at
 * position zero, SYNTHESISES one carrying score.tempo (the 120 fallback) with
 * `isVisible = false`. Every automation built from something actually in the
 * document is visible.
 *
 * WHY THREE ANSWERS AND NOT TWO. The first version of this asked only whether
 * the FIRST BAR held a visible automation, and reported anything else as
 * "the score declares no tempo". A score whose tempo mark sits in a later bar
 * - it opens with a pickup, or the exporter attached the mark to the first
 * real note rather than to bar one - was therefore described as having none.
 * The number was still right, because the fallback is genuinely what applies
 * before the mark, but the interface then PRINTED that the document says
 * nothing about its tempo, which is untrue of the document. That is #102 with
 * the sign flipped, and worse than #102: failing to mention a fact is not the
 * same as asserting a false one. So "not at the start" and "not anywhere" are
 * kept apart, and only the second is allowed to say the score has none.
 *
 * The test for "start" is deliberately about `automations[0]` rather than
 * about any visible automation in bar one, because index 0 is exactly the
 * entry `score.tempo` reads: consolidate only ever APPENDS its synthesised
 * one, so a real automation in bar one - even one attached part-way through
 * it - is always index 0, and the number being labelled is then that
 * automation's own value rather than the fallback.
 *
 * A non-empty tempoLabel counts as declared, for the formats whose importers
 * fill it in (the Guitar Pro readers set it from the file's own label) - a
 * named tempo is a declared one however it arrived, and nothing invents a
 * label.
 */
function tempoProvenance(score) {
  const bars = score?.masterBars ?? [];
  const opening = (bars[0]?.tempoAutomations ?? [])[0];
  if (opening && (opening.isVisible !== false || !!score?.tempoLabel)) return "start";
  for (const bar of bars) {
    if ((bar?.tempoAutomations ?? []).some((a) => a?.isVisible !== false)) return "later";
  }
  return "none";
}

/**
 * The score viewer's pre-fill for the general metronome: an engine, plus the
 * adapter that keeps it fed with this renderer's playhead and meter.
 *
 * `onTempo` and `onClick` are the engine's own callbacks, passed straight
 * through - see createMetronomeEngine for what they guarantee, in particular
 * that onClick can only fire from inside the real oscillator-creating call.
 */
function createScoreMetronome(api, onTempo, onClick) {
  // ready: false - reporting a rate (even the correct one) before a score
  // exists to be a proportion OF would show a caller a number that has
  // nothing behind it yet. The transport-initialisation calls in
  // createScoreView (setMode / setProportion / setBpm, before any score
  // exists) would otherwise report the fallback-derived value immediately.
  const engine = createMetronomeEngine({ onTempo, onClick, ready: false });

  // True only once REAL playback is under way - never during a count-in, see
  // setPlaying below for why alphaTab's own Playing state is not the same
  // question.
  let playing = false;
  // True from the moment a play() with a count-in configured is detected
  // until the count-in finishes - see setPlaying.
  let countInPending = false;
  // Shut before anything can ask. Belt-and-braces rather than the real gate:
  // alphaTab raises playerStateChanged during load, so setPlaying below closes
  // it independently, and deleting this line changes nothing observable (that
  // was measured, not assumed). Kept because "the click is not running until
  // something says it is" should be true of a freshly built adapter without
  // depending on a renderer event having fired first.
  engine.setRunning(false);

  // api.tickCache is rebuilt by the renderer on each render, and mapping it
  // into the plain shape barAtTick wants is wasted work on every one of the
  // ~40 scheduling checks a second the engine's timer performs - so this is
  // done once per tickCache instance, not once per click.
  let cachedTickCache = null;
  let cachedBars = [];

  function currentBars() {
    const cache = api.tickCache;
    if (cache !== cachedTickCache) {
      cachedTickCache = cache;
      // MasterBarTickLookup.start/end are on the GENERATED MIDI's timeline -
      // the same timeline api.tickPosition reports on - which is exactly why
      // this is read from here rather than summed from MasterBar durations:
      // a repeat or an unplayed alternate ending makes the notated bar order
      // and the played tick order different timelines, and only alphaTab's
      // own lookup, built from the actual generated MIDI, knows which bar is
      // really sounding at a given tick.
      cachedBars = (cache?.masterBars ?? []).map((mb) => ({
        startTick: mb.start,
        endTick: mb.end,
        numerator: mb.masterBar.timeSignatureNumerator,
        denominator: mb.masterBar.timeSignatureDenominator,
      }));
    }
    return cachedBars;
  }

  // The whole seam, in five lines: the engine asks where the playhead is and
  // which bar is sounding there, and this answers in plain numbers. Looked up
  // fresh on every scheduled click, never cached across one, which is what
  // lets the engine derive each click's phase from the playhead rather than
  // counting from when the click started.
  engine.setPulseSource(() => {
    const tick = api.tickPosition ?? 0;
    return { tick, bar: barAtTick(currentBars(), tick) };
  });

  return {
    // The metronome-shaped surface, for whoever owns the interface. Exactly
    // the engine's own settings vocabulary and nothing else - no scoreLoaded,
    // no setPlaying - so an interface component cannot tell a click driven
    // through this apart from one it made itself, and the renderer-facing
    // hooks below are not something a caller can reach in and drive.
    control: {
      prime: engine.prime,
      currentLimit: engine.currentLimit,
      setEnabled: engine.setEnabled,
      setMode: engine.setMode,
      setProportion: engine.setProportion,
      setBpm: engine.setBpm,
      setSubdivision: engine.setSubdivision,
      setAccent: engine.setAccent,
      currentRate: engine.currentRate,
    },
    scoreLoaded(loadedScore) {
      if (loadedScore?.tempo != null) engine.setBaseTempo(loadedScore.tempo);
      // The first bar's own declared denominator - a reasonable assumption for
      // the click rate to display before api.tickCache exists to ask (it is
      // built during rendering, not necessarily by the instant scoreLoaded
      // fires). Once a real bar lookup is available the pulse source always
      // wins; this is only ever read when one is not.
      const first = loadedScore?.masterBars?.[0];
      if (first) {
        engine.setMeter(first.timeSignatureNumerator ?? 4, first.timeSignatureDenominator ?? 4);
      }
      // Last, and unconditionally: this is what opens reporting, and it forces
      // the next report through even if the new score's rate happens to match
      // a stale one already announced - relevant if this view is ever handed a
      // second score to load, which nothing in this codebase does today but
      // nothing here should assume.
      engine.setReady(true);
    },
    // originalTempo is the score's OWN tempo at the playhead, unaffected by
    // playbackSpeed (see PositionChangedEventArgs in alphaTab's typings, and
    // modifiedTempo beside it for the one that does track speed). Called on
    // every position update while playing, which is the continuous tracking
    // a proportion needs rather than a value read once at play time - so a
    // piece that changes tempo mid-stream is followed instead of frozen at
    // whatever was true at bar one.
    positionChanged(originalTempo) {
      if (Number.isFinite(originalTempo) && originalTempo > 0) engine.setBaseTempo(originalTempo);
    },
    // isPlayingNow is alphaTab's own playerStateChanged boolean, and it is
    // NOT the same question as "should the click be running": alphaTab
    // raises Playing before a count-in even starts (play() fires the state
    // change, then conditionally calls sequencer.startCountIn()), and raises
    // it a SECOND time, with no intervening Paused, the instant the count-in
    // finishes and real playback begins. Naively starting on the first event
    // sounds the click underneath the count-in at whatever tempo the click
    // is set to - a second, differently-paced click defeating the reason a
    // count-in exists - and the second event would then be swallowed by the
    // engine's own "already running" check, leaving the click's grid anchored
    // to whenever the count-in happened to start rather than to the first
    // real beat, for the rest of the session.
    //
    // So a rising edge is treated as a count-in, and suppressed, exactly
    // when api.countInVolume is on at the moment it arrives; the covering
    // SECOND rising edge (no Paused in between) is what actually starts the
    // click, freshly anchored - nothing here has to detect the count-in
    // ending explicitly, because that second event already means exactly that.
    setPlaying(isPlayingNow) {
      if (isPlayingNow) {
        if (playing) return; // already running - a redundant event
        if (!countInPending && (api.countInVolume ?? 0) > 0) {
          countInPending = true; // this rising edge IS the count-in
          return;
        }
        countInPending = false;
        playing = true;
        engine.setRunning(true);
        return;
      }
      countInPending = false;
      playing = false;
      engine.setRunning(false);
    },
    destroy: engine.destroy,
  };
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
 * @param opts.transport    {speed, looping, countIn} to start at. The
 *                          metronome's own settings are not here: they are
 *                          pushed by whoever owns the metronome interface
 *                          (Metronome.svelte), which re-pushes all of them
 *                          whenever the view it is driving is replaced - so
 *                          there is exactly one place they come from
 * @param opts.onReady      playback became available
 * @param opts.onPlaying    (boolean) transport state changed
 * @param opts.onError      (message) load or render failed
 * @param opts.onPassComplete  one pass finished (drives the tempo ladder)
 * @param opts.onLayout     (layout) the chosen layout changed
 * @param opts.onProfiles   (profiles, unrenderable) the profiles this score
 *                          supports changed - fires once a score has loaded.
 *                          `profiles` can be empty; `unrenderable` is true in
 *                          exactly that case, when nothing about this score
 *                          can be drawn under any profile
 * @param opts.onProfileApplied  (profile) a render has actually finished
 *                          successfully with this profile showing - the
 *                          signal a caller should wait for before treating a
 *                          profile switch as having taken effect, rather than
 *                          assuming success the moment it is requested
 * @param opts.onScoreTempo (bpm, provenance) the quarter-note tempo a
 *                          proportion is a proportion of, once a score has
 *                          loaded - reported so an interface can say "70% of
 *                          what" rather than showing a bare percentage - and
 *                          where that number came from: "start", "later" or
 *                          "none". Both, rather than a null for the cases
 *                          where nothing was declared, because the click
 *                          still has to run at something and the interface
 *                          still has to name it; what must not happen is
 *                          naming it as a marking. Reported once per load and
 *                          not tracked afterwards, which is why it can be a
 *                          fact about the score: the live playhead tempo goes
 *                          to the engine (and so to the readout), not to this
 *                          callback. See tempoProvenance.
 * @param opts.onMetronomeTempo  (bpm, limit) the tempo the metronome is actually
 *                          clicking at just changed - see
 *                          metronome-engine.js, and createScoreMetronome
 *                          above for the pre-fill this view gives it. Fires
 *                          on a setting change, a score load, and
 *                          continuously while playing under a proportion
 *                          whose base tempo moves.
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
    onProfiles = () => {},
    onProfileApplied = () => {},
    onMetronomeTempo = () => {},
    onScoreTempo = () => {},
  } = opts;

  let profile = SCORE_PROFILES.includes(initialProfile) ? initialProfile : "scoretab";
  // The profile a caller has most recently asked for - not necessarily the
  // one on screen; see appliedProfile below for that. Read by reapply() to
  // configure the next render attempt.
  //
  // null until scoreLoaded fires below: nothing is known to be supported yet,
  // which is not the same thing as everything being supported. A caller
  // (TabViewer) reads null as "do not offer any profile button yet" rather
  // than showing every button and having to walk one back once the real
  // answer arrives - see "Async load timing" in
  // web/test-fixtures/tab-profile-selection.md for the bug that came from
  // treating "not yet known" as "everything works" here.
  let scoreProfiles = null;
  // The profile a render has actually finished with, successfully - distinct
  // from `profile` above, which only records what was last requested. Used
  // as setProfile()'s de-duplication key instead of `profile`: keying on the
  // request would make a failed profile switch permanently unretriable, since
  // asking for the same (already "current" by request, but never actually
  // drawn) profile again would look like a no-op change and skip reapply()
  // entirely. Also what onProfileApplied reports.
  let appliedProfile = null;
  // Set once scoreLoaded finds a score with nothing to draw under any
  // profile - see UNRENDERABLE_MESSAGE. The renderer's own automatic first
  // render cannot be cancelled from this handler (AlphaTabApi calls it
  // unconditionally right after scoreLoaded's listeners return), so it still
  // runs and can still throw inside addBars exactly as it always has - see
  // the api.error handler below for how that specific, predicted failure is
  // told apart from an unrelated one.
  let unrenderable = false;
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
  // A snapshot of `profile` taken when the render that is now finishing
  // began, not whatever `profile` has since become - profile can only
  // actually change between a render starting and finishing today because
  // every render path here is synchronous, so reading the live variable at
  // finish time happens to agree with this snapshot now, but only by
  // accident of that timing. Taking the snapshot is one line and does not
  // depend on it.
  let renderingProfile = profile;
  // Set on renderStarted, cleared on postRenderFinished - a render that
  // throws (see the error handler below) leaves this true, because
  // ScoreRenderer.renderScore's try/catch means postRenderFinished never
  // fires for a render that failed partway through. That makes this a
  // complete failed-render detector: "a render started and none finished
  // since" is exactly "the last render attempt failed", without needing to
  // know why. Used only for data-score-render-ok below - not for deciding
  // whether to suppress an error (see the api.error handler for why that
  // has to be keyed on the error itself instead).
  let renderInFlight = false;
  // Whether the most recently *attempted* render actually finished. Kept
  // distinct from renderCount/lastRenderMs, which only ever advance on
  // success and would otherwise go stale and misleadingly report the
  // previous successful render's numbers after a failed one.
  let renderOk = true;
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

  // Muted permanently, not toggled with the metronome below - see
  // metronome-engine.js's own header for why alphaTab's built-in click cannot
  // be the thing this feature is built from.
  api.metronomeVolume = 0;

  // host is the SAME DOM element across a score switch within one mounted
  // TabViewer (this closure is rebuilt; the element is not - see publish()'s
  // own dataset.scoreProfiles delete for the identical reason). Without this,
  // a fresh view for a NEW score would inherit the previous score's click
  // count and last-reported bpm sitting on the element from before - stale
  // data a test (or a player) could read as live.
  if (host) {
    delete host.dataset.metronomeClicks;
    delete host.dataset.metronomeAccent;
    delete host.dataset.metronomeNumerator;
    delete host.dataset.metronomeDenominator;
    delete host.dataset.metronomePhase;
    delete host.dataset.metronomeBpm;
  }

  // Reflects each scheduled click onto the host, the same way publish() below
  // reflects layout and theme - so a test can assert on a click that actually
  // happened rather than on a value this module only intended to produce.
  function publishMetronomeClick(accent, numerator, denominator, phase) {
    if (!host) return;
    host.dataset.metronomeClicks = String((Number(host.dataset.metronomeClicks) || 0) + 1);
    host.dataset.metronomeAccent = String(accent);
    host.dataset.metronomeNumerator = String(numerator);
    host.dataset.metronomeDenominator = String(denominator);
    host.dataset.metronomePhase = String(phase);
  }

  const metronome = createScoreMetronome(
    api,
    (bpm, limit) => {
      if (host) host.dataset.metronomeBpm = String(bpm);
      onMetronomeTempo(bpm, limit);
    },
    publishMetronomeClick,
  );

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
    // "" (not omitted) once scoreLoaded has run and found nothing to offer -
    // distinct from the attribute being altogether absent, which means no
    // score has loaded yet at all. Explicitly deleted rather than just left
    // unset in the null case: `host` is the same DOM element across a score
    // switch (TabViewer's markup does not recreate it, only this closure),
    // so without the delete, publish()'s very first call for a *new* score -
    // called below before anything is known - would leave the *previous*
    // score's dataset.scoreProfiles sitting there through the whole loading
    // window, silently breaking the "absent means nothing has loaded yet"
    // contract this attribute is documented to have.
    if (scoreProfiles != null) host.dataset.scoreProfiles = scoreProfiles.join(",");
    else delete host.dataset.scoreProfiles;
    // Read this before trusting scoreRenderMs/scoreRenders: those only ever
    // advance on a successful render (see renderOk above), so after a failed
    // one they still hold the previous successful render's numbers.
    host.dataset.scoreRenderOk = String(renderOk);
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
    renderInFlight = true;
    renderingProfile = profile;
    applyBrandingOverride(api);
  });
  api.postRenderFinished.on(() => {
    renderInFlight = false;
    renderOk = true;
    lastRenderMs = performance.now() - renderStartedAt;
    renderCount += 1;
    appliedProfile = renderingProfile;
    publish();
    growPaperToDrawing();
    // The profile that just actually finished drawing, snapshotted at the
    // moment this render started rather than read live off `profile` here -
    // see renderingProfile's declaration.
    onProfileApplied(renderingProfile);
  });

  // A profile carried over from a previous score, or the "scoretab" default,
  // can be one this score has nothing to draw for a staff under - offering
  // "Tab" for a MusicXML file with pitches but no fret/string data is exactly
  // that, and the renderer's own answer to being asked for it anyway is to
  // throw out of addBars (see canDraw above) rather than draw an empty
  // staff. Correcting the setting here, in the scoreLoaded handler, runs
  // before the load's own first render: AlphaTabApi triggers scoreLoaded
  // synchronously and only calls render() once every listener has returned,
  // so this is not a race against it.
  api.scoreLoaded.on((loadedScore) => {
    scoreProfiles = supportedProfiles(api.tracks?.length ? api.tracks : loadedScore.tracks);
    unrenderable = scoreProfiles.length === 0;
    if (!unrenderable && !scoreProfiles.includes(profile)) {
      profile = scoreProfiles[0];
      api.settings.display.staveProfile = STAVE_PROFILE[profile];
      api.updateSettings();
    }
    metronome.scoreLoaded(loadedScore);
    onScoreTempo(loadedScore?.tempo ?? null, tempoProvenance(loadedScore));
    publish();
    onProfiles(scoreProfiles, unrenderable);
  });

  api.playerReady.on(() => onReady());
  api.playerStateChanged.on((e) => {
    const isPlaying = e.state === 1;
    metronome.setPlaying(isPlaying);
    onPlaying(isPlaying);
  });
  // originalTempo is the score's own tempo at the playhead, unaffected by
  // playback speed - see createScoreMetronome's positionChanged for why
  // this is what a proportion has to track rather than resolve once.
  api.playerPositionChanged.on((e) => metronome.positionChanged(e.originalTempo));
  api.error.on((e) => {
    if (renderInFlight) {
      renderInFlight = false;
      renderOk = false;
      publish();
    }
    // Keyed on the error itself - not on "a render happened to be in flight"
    // - because a render being in flight is not specific enough. alphaTab
    // registers its own resize handling unconditionally (see the
    // ResizeObserver below) and, on an unrenderable score, that path can
    // start and fail a render this file never sees start: unlike
    // renderScore(), ScoreRenderer.resizeRender()'s full-rerender branch
    // calls render() with no try/catch around it and never reaches api.error
    // at all for its own failure, but it does leave renderInFlight set (this
    // file's renderStarted listener still fires) with nothing to ever clear
    // it. An earlier version keyed suppression on that flag alone, and it
    // stayed armed across such an invisible failure until the *next*
    // api.error of any kind - a soundfont load failure, unrelated to any of
    // this - which then got silently swallowed too. Checking the error's own
    // stack for the specific crash this suppression exists for means an
    // unrelated error is never at risk, regardless of what left renderInFlight
    // set.
    const isPredictedEmptyStaffSystemCrash =
      unrenderable && typeof e?.stack === "string" && e.stack.includes("StaffSystem") && e.stack.includes("addBars");
    if (isPredictedEmptyStaffSystemCrash) {
      // Every (track, staff) pair failed every profile's check (see
      // supportedProfiles), so this render was never going to succeed no
      // matter which profile was set, and the caller has already been told
      // so via onProfiles(profiles, true). Surfacing the renderer's own
      // TypeError here on top of that would put a developer's stack trace in
      // front of a guitarist for a condition that isn't actually broken -
      // it's a score with nothing to stage.
      console.debug("score-render: suppressed the predicted render failure for an unrenderable score.", e);
      return;
    }
    onError(e?.message || "failed to load score");
  });
  // Fires at the end of each loop pass, not only the final stop, which is
  // what makes it usable as "one clean pass done".
  api.playerFinished.on(() => onPassComplete());

  // A fresh renderer starts at its own transport defaults, so carry over
  // whatever the caller was already using.
  if (transport.speed != null) api.playbackSpeed = transport.speed;
  if (transport.looping != null) api.isLooping = transport.looping;
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
  //
  // The renderer registers its own ResizeObserver on the container, unasked
  // and unconditionally, in the AlphaTabApi constructor - there is no setting
  // to turn it off in 1.8.4, its unsubscribe closure is discarded, and the
  // container class isn't exported, so this file cannot reach in and remove
  // it. It is throttled at a hardcoded 10ms and its handler no-ops once
  // `container.width === renderer.width`, which is what keeps the two
  // observers from fighting each other once a render settles - reapply()
  // below writes `api.renderer.width` before rendering specifically to reach
  // that quiescent state. This is the fact whose absence caused the original
  // resize oscillation bug here; if this observer's logic changes, re-check
  // against the library's handler, not just against itself.
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
    // A resize, theme or preset change still fires while an unrenderable
    // score is on screen, and unlike the load's own first render, this call
    // is entirely ours to skip - re-attempting a render already known to
    // find zero drawable staves would just repeat the same suppressed
    // failure on every window resize for no benefit. publish() still has to
    // run first, though: setTheme/setPreset already updated `theme`/`preset`
    // before calling this, and skipping publish() along with the render
    // would leave dataset.scoreTheme/scorePreset reporting the *previous*
    // theme or preset - stale, even though nothing about the (still
    // unrenderable) view visibly changes either way.
    if (unrenderable) {
      publish();
      return;
    }
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
    /**
     * Which of SCORE_PROFILES the loaded score can actually be drawn with -
     * null before a score has loaded, possibly empty once one has (see
     * supportedProfiles). A copy, not the live array, so a caller cannot
     * accidentally mutate this instance's notion of what is supported.
     */
    get supportedProfiles() {
      return scoreProfiles ? [...scoreProfiles] : scoreProfiles;
    },
    get preset() {
      return preset;
    },
    /** Milliseconds of renderer work in the last render, not time to pixels. */
    get lastRenderMs() {
      return lastRenderMs;
    },

    setProfile(next) {
      // Keyed on appliedProfile, not profile: profile is set eagerly, right
      // below, the moment a switch is requested, whether or not it ever
      // renders successfully. Keying on it would make a failed switch
      // permanently unretriable - asking for the same profile again would
      // look like a no-op ("next === profile" already true from the failed
      // attempt) and skip reapply() entirely, with no way back to that
      // profile except detouring through a third one first.
      if (!scoreProfiles?.includes(next) || next === appliedProfile) return;
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
    /**
     * The metronome, pre-filled from this score - a plain metronome handle
     * (`setEnabled`, `setMode`, `setProportion`, `setBpm`, `prime`, ...), not
     * a set of setMetronome* methods on the view, because the click is not a
     * renderer setting and never was. What this view contributes is the
     * pre-fill and the live playhead behind it; the click itself is the
     * general tool in metronome-engine.js, and alphaTab's own is permanently
     * muted (see api.metronomeVolume = 0 above).
     *
     * "proportion" clicks a percentage of the score's own tempo at the
     * playhead, tracking a tempo change written mid-piece; "bpm" clicks the
     * typed number and ignores the score entirely. Either way the click's
     * tempo has nothing to do with setSpeed() - that is the whole point.
     */
    metronome: metronome.control,
    setCountIn(v) {
      api.countInVolume = v ? 1 : 0;
    },
    playPause() {
      metronome.control.prime();
      api.playPause();
    },
    stop() {
      api.stop();
    },

    destroy() {
      destroyed = true;
      observer.disconnect();
      partialWatcher.disconnect();
      metronome.destroy();
      api.destroy();
    },
  };
}
