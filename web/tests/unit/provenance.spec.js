// How a transcription's meter, key and tuning are sorted into "read off the
// page" and "assumed", called directly - no browser, no backend. provenance.js
// has no runes and no imports precisely so this can import it.
//
// The wording IS the feature here, the same way it is in practice.js: a line
// that puts an assumed 4/4 in the read group has failed at the one thing it
// exists for. What is NOT tested here is whether any of it reaches a screen -
// that is tests/browser/score-compare-warnings.spec.js's job, and a suite that
// only checked these strings would have proved nothing about the defect issue
// #103 is actually about, which was six fields computed and never displayed.
import { expect, test } from "@playwright/test";

import {
  keySignatureLabel,
  sourceKind,
  transcriptionProvenance,
  tuningDescription,
} from "../../src/lib/provenance.js";

// The extraction result for a score where nothing could be read - the common
// case, not the corner one: 18% of first pages lose their printed meter and
// every score not literally labelled "Drop D" reads as standard tuning.
const ASSUMED_EVERYTHING = {
  time_signature: [4, 4],
  time_signature_source: "not detected (assumed 4/4)",
  key_fifths: 0,
  key_signature_source: "not detected (assumed no key signature)",
  tuning: ["E2", "A2", "D3", "G3", "B3", "E4"],
  tuning_label: null,
};

test("what the extractor read and what it assumed end up in different groups", () => {
  const assumed = transcriptionProvenance(ASSUMED_EVERYTHING);
  expect(assumed.read).toEqual([]);
  expect(assumed.assumed).toEqual(["4/4", "no key signature", "standard tuning"]);
  expect(assumed.supplied).toEqual([]);

  const read = transcriptionProvenance({
    time_signature: [6, 8],
    time_signature_source: "glyph-decoded",
    key_fifths: -3,
    key_signature_source: "glyph-decoded",
    tuning: ["D2", "A2", "D3", "G3", "B3", "E4"],
    tuning_label: "Drop D",
  });
  expect(read.read).toEqual(["6/8", "3 flats", "Drop D tuning"]);
  expect(read.assumed).toEqual([]);

  // A meter the person typed into the box beside the transcribe button is
  // neither, and saying so confirms it was actually used.
  const supplied = transcriptionProvenance({
    ...ASSUMED_EVERYTHING,
    time_signature: [3, 4],
    time_signature_source: "manual override",
  });
  expect(supplied.supplied).toEqual(["3/4"]);
  expect(supplied.assumed).toEqual(["no key signature", "standard tuning"]);
});

test("a row that records nothing claims nothing, in either direction", () => {
  // Every hand-edited row, and every row extracted before the provenance was
  // stored. "standard tuning assumed" would be inventing a reading of content
  // nothing has looked at; "read from the page" would be worse.
  for (const t of [null, undefined, {}, { source: "edited", warnings: [] }]) {
    const groups = transcriptionProvenance(t);
    expect(groups.read, JSON.stringify(t)).toEqual([]);
    expect(groups.assumed, JSON.stringify(t)).toEqual([]);
    expect(groups.supplied, JSON.stringify(t)).toEqual([]);
  }
});

test("a source string this version does not know is reported as neither, rather than guessed at", () => {
  // The safe-looking default in both directions is wrong: calling an
  // unrecognised future source "read" is the exact lie this file exists to
  // prevent, and calling it "assumed" is a false accusation about a value that
  // may well have been decoded off the page.
  expect(sourceKind("decoded-by-some-later-method")).toBeNull();
  expect(sourceKind("")).toBeNull();
  expect(sourceKind(null)).toBeNull();
  expect(sourceKind(undefined)).toBeNull();
  expect(sourceKind("glyph-decoded")).toBe("read");
  expect(sourceKind("auto-detected")).toBe("read");
  expect(sourceKind("manual override")).toBe("supplied");
  expect(sourceKind("not detected")).toBe("assumed");
  expect(sourceKind("not detected (assumed 4/4)")).toBe("assumed");

  const groups = transcriptionProvenance({
    ...ASSUMED_EVERYTHING,
    time_signature_source: "decoded-by-some-later-method",
  });
  expect(groups.read).not.toContain("4/4");
  expect(groups.assumed).not.toContain("4/4");
});

test("an unlabelled tuning that is not the standard one is neither read nor assumed", () => {
  expect(tuningDescription(["E2", "A2", "D3", "G3", "B3", "E4"], null)).toBe("standard tuning");
  expect(tuningDescription(["D2", "A2", "D3", "G3", "B3", "E4"], "Drop D")).toBe("Drop D tuning");
  // The case #80 makes reachable. A tuning that demonstrably differs from
  // standard cannot have come from an assumption OF standard, so "assumed"
  // there is a false accusation - and nothing records where it did come from.
  // Same rule sourceKind follows on an unrecognised source: report neither.
  expect(tuningDescription(["C2", "G2", "C3", "F3", "A3", "D4"], null)).toBeNull();
  // Seven strings, the first six of them standard - length has to count, or an
  // extended-range instrument is described as a six-string in standard.
  expect(tuningDescription(["E2", "A2", "D3", "G3", "B3", "E4", "A4"], null)).toBeNull();
  expect(tuningDescription(null, null)).toBeNull();
  expect(tuningDescription([], null)).toBeNull();

  // ...and it reaches no group at all, rather than quietly landing in
  // "assumed" the way an unlabelled standard tuning correctly does.
  const groups = transcriptionProvenance({
    ...ASSUMED_EVERYTHING,
    tuning: ["C2", "G2", "C3", "F3", "A3", "D4"],
    tuning_label: null,
  });
  expect(groups.assumed).toEqual(["4/4", "no key signature"]);
  expect(groups.read).toEqual([]);
});

test("a key signature is said the way a person would say it", () => {
  expect(keySignatureLabel(0)).toBe("no key signature");
  expect(keySignatureLabel(1)).toBe("1 sharp");
  expect(keySignatureLabel(4)).toBe("4 sharps");
  expect(keySignatureLabel(-1)).toBe("1 flat");
  expect(keySignatureLabel(-5)).toBe("5 flats");
});
