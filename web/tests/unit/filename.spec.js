// sanitizeFilename()/midiFilename() called directly - no browser, no
// backend. filename.js has no runes and no imports precisely so this can
// import it, the same reason pitch.spec.js gives for pitch.js.
//
// These are the torture cases the review on issue #270 measured against the
// ORIGINAL inline sanitizeFilename() in TabViewer.svelte: a 300-character
// title produced a 304-character ".mid" name (over Windows' 255-character
// path-component limit), control characters passed through untouched, and a
// ".." title sanitised down to "..mid" - which Chrome's own <a download>
// rewrote to "mid.mid" on its own initiative, not visible from this file's
// return value at all. Every case below is one of those, or the ordinary
// path the original already handled correctly.
import { expect, test } from "@playwright/test";

import { STEM_MAX_LENGTH, midiFilename, sanitizeFilename } from "../../src/lib/filename.js";

test("strips path separators and shell-reserved characters, keeping ordinary letters", () => {
  expect(midiFilename('a/b\\c:d*e?f"g<h>i|j')).toBe("abcdefghij.mid");
});

test("a title made of nothing but reserved characters falls back to Untitled", () => {
  expect(midiFilename('/\\:*?"<>|')).toBe("Untitled.mid");
});

test("an empty title falls back to Untitled", () => {
  expect(midiFilename("")).toBe("Untitled.mid");
});

test("a 300-character title is bounded to a 120-character stem before the extension", () => {
  const title = "L".repeat(300);
  const got = midiFilename(title);
  expect(got).toBe("L".repeat(STEM_MAX_LENGTH) + ".mid");
  // The bound is on the RETURNED stem, not just true of this one input -
  // guards against a fix that special-cases "L" or this exact length.
  expect(sanitizeFilename(title).length).toBe(STEM_MAX_LENGTH);
});

test('".." sanitises to Untitled, not to a name a browser could rewrite as a bare extension', () => {
  expect(midiFilename("..")).toBe("Untitled.mid");
});

test("a tab and a NUL are both stripped as control characters", () => {
  const tab = String.fromCharCode(9);
  const nul = String.fromCharCode(0);
  expect(midiFilename(`a${tab}b${nul}c`)).toBe("abc.mid");
});

test("an ordinary title is unaffected", () => {
  expect(midiFilename("My Score")).toBe("My Score.mid");
});
