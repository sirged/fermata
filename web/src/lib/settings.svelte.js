// Where components read and write persisted user preferences. A setting lives
// on the server (server/fermata/api.py's /api/settings), not in browser
// storage, so it follows a person from phone to tablet to desktop. Loaded
// once here; components read the live object rather than each fetching their
// own copy, and writes go back through the same place.
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

// A single reactive object every caller shares, so a change made from one
// component (the settings view) is seen immediately by another (a score
// already on screen) without either knowing how the value got there.
const settings = $state({ ...DEFAULTS });

let loadStarted = false;

function load() {
  if (loadStarted) return;
  loadStarted = true;
  api
    .settings()
    .then((s) => Object.assign(settings, DEFAULTS, s))
    .catch(() => {
      // network down, backend not deployed yet, etc - the defaults already
      // in `settings` are a perfectly usable fresh-install behaviour
    });
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
  const previous = settings[key];
  settings[key] = value;
  try {
    const saved = await api.putSettings({ [key]: value });
    Object.assign(settings, saved);
  } catch (e) {
    settings[key] = previous;
    throw e;
  }
}
