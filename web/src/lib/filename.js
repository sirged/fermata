// Turns an arbitrary score title into a filesystem-safe download name
// (issue #270's review nit, on top of #270 itself).
//
// TabViewer.svelte's original inline sanitizeFilename() only stripped the
// characters a filesystem or a shell would read as a path separator or a
// reserved character. The reviewer measured three gaps that left, none of
// them hypothetical:
//   - no length bound: a 300-character title produced a 304-character
//     ".mid" file, past Windows' 255-character path-COMPONENT limit (not the
//     full-path limit - a single segment).
//   - control characters (codepoints 0-31, plus DEL/127) passed through
//     untouched.
//   - a title of only bad characters could sanitise down to something that
//     READS as a relative path once ".mid" is appended: "." + ".." became
//     "...mid", which Chrome's own <a download> rewrote to "mid.mid" -
//     silently, on the browser's own initiative, not this code's.
//
// STEM_MAX_LENGTH is 120, not something closer to 255: 255 is Windows' limit
// for the whole component INCLUDING the ".mid" suffix, and a browser is free
// to append its own disambiguating suffix to a downloaded file that collides
// with one already on disk (Chrome's own pattern is " (1)", " (2)", ...) -
// that suffix lands after this function returns, so headroom for it has to
// come out of the stem this function controls. 120 leaves well over 100
// characters of slack under 255 for ".mid" plus anything a browser tacks on,
// on any platform, without needing to special-case the extension's length.
export const STEM_MAX_LENGTH = 120;

// Same fallback Library.svelte already shows for a score with no title
// (issue #58's export path) - a MIDI pulled from either place reads the same
// way.
const FALLBACK_STEM = "Untitled";

// The characters a filesystem (or a zip entry, or a URL) would read as a
// path separator or a reserved shell character - checked one at a time
// against this string rather than folded into a regex character class,
// because the control-character check right beside it is written the same
// per-codepoint way and putting one in a regex literal while the other
// walks codepoints would be two different techniques doing one job.
const RESERVED_CHARS = "/\\:*?\"<>|";

/**
 * Sanitises a score title into a filesystem-safe filename stem (no
 * extension). Strips path/shell-reserved characters and every C0 control
 * codepoint plus DEL (0-31, 127), collapses any run of whitespace left
 * behind into a single space, trims the result, strips leading/trailing
 * dots (so an all-dots title can never read as "." or ".." once an
 * extension is appended), bounds it to STEM_MAX_LENGTH characters, and
 * falls back to "Untitled" when nothing survives.
 */
export function sanitizeFilename(name) {
  const source = name ?? "";
  let kept = "";
  for (const ch of source) {
    const code = ch.codePointAt(0);
    if (code <= 0x1f || code === 0x7f) continue; // control characters
    if (RESERVED_CHARS.includes(ch)) continue; // path/shell-reserved
    kept += ch;
  }
  const stripped = kept
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\.+|\.+$/g, "");
  if (!stripped) return FALLBACK_STEM;
  return stripped.slice(0, STEM_MAX_LENGTH);
}

/** sanitizeFilename(name) with ".mid" appended - the full download name. */
export function midiFilename(name) {
  return `${sanitizeFilename(name)}.mid`;
}
