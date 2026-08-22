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
  tuningStatement,
} from "../../src/lib/provenance.js";

// The extraction result for a score where nothing could be read - the common
// case, not the corner one: 18% of first pages lose their printed meter.
const ASSUMED_EVERYTHING = {
  time_signature: [4, 4],
  time_signature_source: "not detected (assumed 4/4)",
  key_fifths: 0,
  key_signature_source: "not detected (assumed no key signature)",
  tuning: ["E2", "A2", "D3", "G3", "B3", "E4"],
  tuning_label: null,
  tuning_unread: [],
};

test("what the extractor read and what it assumed end up in different groups", () => {
  const assumed = transcriptionProvenance(ASSUMED_EVERYTHING);
  expect(assumed.read).toEqual([]);
  expect(assumed.assumed).toEqual(["4/4", "no key signature"]);
  expect(assumed.supplied).toEqual([]);

  const read = transcriptionProvenance({
    time_signature: [6, 8],
    time_signature_source: "glyph-decoded",
    key_fifths: -3,
    key_signature_source: "glyph-decoded",
  });
  expect(read.read).toEqual(["6/8", "3 flats"]);
  expect(read.assumed).toEqual([]);

  // A meter the person typed into the box beside the transcribe button is
  // neither, and saying so confirms it was actually used.
  const supplied = transcriptionProvenance({
    ...ASSUMED_EVERYTHING,
    time_signature: [3, 4],
    time_signature_source: "manual override",
  });
  expect(supplied.supplied).toEqual(["3/4"]);
  expect(supplied.assumed).toEqual(["no key signature"]);

  // THE TUNING IS NOT IN ANY OF THEM, whatever it holds - see tuningStatement
  // for both reasons. It was, and it was wrong in one direction and useless in
  // the other.
  for (const tuning of [
    { tuning: ["E2", "A2", "D3", "G3", "B3", "E4"], tuning_label: null },
    { tuning: ["D2", "A2", "D3", "G3", "B3", "E4"], tuning_label: "Drop D" },
  ]) {
    const groups = transcriptionProvenance({ ...ASSUMED_EVERYTHING, ...tuning });
    const said = [...groups.read, ...groups.assumed, ...groups.supplied].join(" ");
    expect(said, JSON.stringify(tuning)).not.toMatch(/tuning|Drop D/);
  }
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
    expect(tuningStatement(t), JSON.stringify(t)).toBeNull();
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

test("a recognised tuning name is stated as a name, and never as a tuning that was read", () => {
  // The extractor finds a tuning by matching the words "Drop D" in the page
  // text. That is recognition of a label. 100 scores match and all sampled
  // matches are genuine - but a name is not the six strings, and the words for
  // the two must differ.
  const named = tuningStatement({ ...ASSUMED_EVERYTHING, tuning_label: "Drop D" });
  expect(named.kind).toBe("recognised");
  expect(named.text).toBe("Tuning: the page names Drop D. Nothing else about the tuning was read.");
  expect(named.text).not.toMatch(/\bread from the page\b/i);
});

test("a printed tuning instruction the extractor discards makes the tuning incomplete, and says which", () => {
  // 41 of those 100 carry one of these: 9 a half-step-down direction, 32 a
  // capo. The recorded array is then a semitone out, or every sounding pitch
  // is, while looking exactly like something that was read.
  const capo = tuningStatement({
    ...ASSUMED_EVERYTHING,
    tuning_label: "Drop D",
    tuning_unread: ["capo 2"],
  });
  expect(capo.kind).toBe("incomplete");
  expect(capo.text).toBe(
    "Tuning: the page names Drop D, and also says capo 2, which Fermata does not read " +
      "— so the pitches sounded are not the pitches printed.",
  );

  // With no name recognised at all, and with more than one instruction.
  const bare = tuningStatement({
    ...ASSUMED_EVERYTHING,
    tuning_unread: ["tune down a half step", "capo 3"],
  });
  expect(bare.kind).toBe("incomplete");
  expect(bare.text).toBe(
    "Tuning: the page says tune down a half step and capo 3, which Fermata does not read " +
      "— so the pitches sounded are not the pitches printed.",
  );
});

test("a tuning with nothing real to say about it says nothing", () => {
  // The 193-of-293 case: standard strings, no name found. True, uninformative,
  // and stating it put the unverified mark on two thirds of the library.
  expect(tuningStatement(ASSUMED_EVERYTHING)).toBeNull();
  // And the case #80 makes reachable: unlabelled and NOT standard. A tuning
  // that demonstrably differs from standard cannot have come from an assumption
  // OF standard, so "assumed" there is a false accusation - and nothing records
  // where it did come from. Same rule sourceKind follows on an unrecognised
  // source.
  expect(
    tuningStatement({ ...ASSUMED_EVERYTHING, tuning: ["C2", "G2", "C3", "F3", "A3", "D4"] }),
  ).toBeNull();
  // Seven strings, the first six of them standard - length has to count, or an
  // extended-range instrument is treated as a six-string in standard.
  expect(
    tuningStatement({ ...ASSUMED_EVERYTHING, tuning: ["E2", "A2", "D3", "G3", "B3", "E4", "A4"] }),
  ).toBeNull();
});

test("a key signature is said the way a person would say it", () => {
  expect(keySignatureLabel(0)).toBe("no key signature");
  expect(keySignatureLabel(1)).toBe("1 sharp");
  expect(keySignatureLabel(4)).toBe("4 sharps");
  expect(keySignatureLabel(-1)).toBe("1 flat");
  expect(keySignatureLabel(-5)).toBe("5 flats");
});
