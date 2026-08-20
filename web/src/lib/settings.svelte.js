// Where components read and write persisted user preferences. A setting lives
// on the server (server/fermata/api.py's /api/settings), not in browser
// storage, so it follows a person from phone to tablet to desktop. Loaded
// once here; components read the live object rather than each fetching their
// own copy, and writes go back through the same place - so a change made
// from the settings view or an in-viewer control (TabViewer's own theme
// picker) is visible to every other reader of `settings` at once, including
// a score already on screen, with no navigation and no extra fetch.
import { api } from "./api.js";
import { SCORE_THEMES } from "./score-render.js";

export const STAFF_THEMES = SCORE_THEMES;

export const STAFF_THEME_LABELS = {
  parchment: "Parchment",
  noir: "White on black",
  print: "Black on white",
};

// The CSS custom-property prefix each theme's tokens live under, so a
// preview swatch can be drawn from the same values score-render.js reads -
// see app.css.
export const STAFF_THEME_TOKEN_PREFIX = {
  parchment: "--score",
  noir: "--score-noir",
  print: "--score-print",
};

const DEFAULTS = { staff_theme: STAFF_THEMES[0] };

const settings = $state({ ...DEFAULTS });

// Keys a local write has touched. The initial GET and a write race - the
// GET is sent first (at module load) but can resolve after a write the user
// makes while it's still in flight. Once a key is in here, the initial
// load's answer for it is permanently stale: a write reflects intent that
// happened strictly after the GET was sent, so it must never be undone by
// that GET landing late, however long it takes.
const written = new Set();

let loadPromise = null;

function load() {
  if (loadPromise) return loadPromise;
  loadPromise = api
    .settings()
    .then((s) => {
      const merged = { ...DEFAULTS, ...s };
      for (const [key, value] of Object.entries(merged)) {
        if (!written.has(key)) settings[key] = value;
      }
    })
    .catch(() => {
      // network down, backend not deployed yet, etc - the defaults already
      // in `settings` are a perfectly usable fresh-install behaviour
    });
  return loadPromise;
}

load();

/** The live settings object. Mutating it directly does not persist a change -
 * use setSetting for that. */
export function getSettings() {
  return settings;
}

/** Write one setting and update the shared object from what the server
 * actually stored. Applied optimistically so the UI responds at once; rolled
 * back if the write is rejected (an unknown key, an invalid value). */
export async function setSetting(key, value) {
  written.add(key);
  settings[key] = value;
  try {
    const saved = await api.putSettings({ [key]: value });
    Object.assign(settings, saved);
  } catch (e) {
    // Roll back to what the server actually holds, not a guess: `settings[key]`
    // right now might only be the pre-load default if the initial GET hadn't
    // landed yet when this write started, and writing that back would show a
    // value neither the server nor the user ever chose. A fresh GET (rather
    // than the cached `load()` snapshot, which may itself predate this write
    // or an earlier successful one) is the only source of the real answer.
    try {
      const fresh = await api.settings();
      settings[key] = fresh[key] ?? DEFAULTS[key];
    } catch {
      // can't even confirm the real value - leave the optimistic write in
      // place rather than snap to a guessed default
    }
    throw e;
  }
}
