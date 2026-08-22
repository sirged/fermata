// The ear exercise's arithmetic and its phrasing, called directly.
//
// What these are for that a browser test cannot do cheaply: WHICH four notes
// get offered is the whole difference between an exercise and a coin toss, and
// checking it through a page means driving a soundfont for every case. The
// generator takes its randomness as a parameter precisely so this file can pin
// it, and the interesting cases - a range with no octave in it, a range too
// narrow to ask a question at all - are ones a page would have to be
// constructed instrument-by-instrument to reach.
//
// The phrasing is here for the same reason practice.js's is: the words are the
// feature. A drill is the easiest place in a practice tool to start grading
// somebody, and the rule against it is only real if something checks.
import { expect, test } from "@playwright/test";

import {
  CHOICE_COUNT,
  DEFAULT_RANGE,
  MIN_RANGE_SEMITONES,
  SYNTH_REFERENCE_HZ,
  buildChoices,
  distractorKinds,
  instrumentRange,
  loggedStatement,
  pickTarget,
  progressStatement,
  rangeIsAskable,
  rangeLabel,
  rangeSourceStatement,
  rangeStatement,
  referenceStatement,
  roundStatement,
  sessionNote,
} from "../../src/lib/ear-training.js";
import { spellMidi } from "../../src/lib/pitch.js";
import { FORBIDDEN_WORDS, forbiddenWord } from "../../src/lib/practice.js";

/** An instrument in the shape the API answers with - what matters here is
 * `strings[].sounding_midi`, which already has any capo in it. */
function instrument(over = {}) {
  const pitches = over.midis ?? [40, 45, 50, 55, 59, 64]; // guitar, standard
  return {
    name: "Guitar (standard)",
    fretted: true,
    fret_count: 22,
    reference_pitch: 440,
    ...over,
    strings: pitches.map((midi, index) => ({
      number: pitches.length - index,
      sounding_midi: midi,
    })),
  };
}

// A generator that walks a fixed list, so every pick in a test is chosen rather
// than observed. Returns 0 once the list runs out, which selects the first
// candidate of whatever is left.
function fixed(...values) {
  let i = 0;
  return () => (i < values.length ? values[i++] : 0);
}

test("an instrument's range runs from its lowest string to its top fret", () => {
  // A guitar's top string is E4 (64) and it declares 22 frets, so the highest
  // note its own definition says it makes is D6 (86). Reading the strings alone
  // would offer a drill two octaves narrower than the instrument.
  expect(instrumentRange(instrument())).toEqual({ low: 40, high: 86, top: "frets" });
  expect(rangeLabel(instrumentRange(instrument()))).toBe("E2 to D6");
});

test("a capo moves the range, because it moves what the instrument sounds", () => {
  // sounding_midi is what the drill reads, which is the same rule the
  // instruments editor auditions by: the capo decides what comes out.
  const capoed = instrument({ midis: [45, 50, 55, 60, 64, 69] });
  expect(instrumentRange(capoed)).toEqual({ low: 45, high: 91, top: "frets" });
});

test("an unfretted range stops at its strings, and says why", () => {
  // A violin: G3 to E5. Its definition declares no frets and therefore says
  // nothing at all about how high it is played, so the range stops at the top
  // string rather than at an invented ceiling - and the interface has a sentence
  // for that, because a cellist looking at a range ending on their top string
  // deserves to know it is the definition talking and not the drill.
  const violin = instrument({ name: "Violin", fretted: false, fret_count: null, midis: [55, 62, 69, 76] });
  expect(instrumentRange(violin)).toEqual({ low: 55, high: 76, top: "strings" });
  expect(rangeSourceStatement(instrumentRange(violin))).toContain("unfretted definition");
  // and nothing is said about a fretted one, which needs no explaining
  expect(rangeSourceStatement(instrumentRange(instrument()))).toBe("");
});

test("an instrument with no strings yields no range at all rather than a wrong one", () => {
  expect(instrumentRange({ strings: [] })).toBeNull();
  expect(instrumentRange(null)).toBeNull();
});

test("the four choices include the note that sounded, exactly once", () => {
  for (const sounded of [36, 48, 60, 72, 84]) {
    const choices = buildChoices(sounded, DEFAULT_RANGE, fixed(0, 0, 0, 0, 0, 0, 0));
    expect(choices).toHaveLength(CHOICE_COUNT);
    expect(choices.filter((c) => c === sounded)).toHaveLength(1);
    expect(new Set(choices).size).toBe(CHOICE_COUNT);
  }
});

test("the three notes you did not hear are one of each kind worth confusing", () => {
  // The issue's requirement, and the difference between an exercise and a coin
  // toss: "near neighbours, or the same note in another octave, rather than four
  // notes far apart which teaches nothing."
  const sounded = 60;
  const choices = buildChoices(sounded, DEFAULT_RANGE, fixed(0, 0, 0, 0, 0, 0, 0));
  const others = choices.filter((c) => c !== sounded);
  expect(others).toHaveLength(3);

  const semitoneAway = others.filter((c) => Math.abs(c - sounded) === 1);
  const sameName = others.filter((c) => c !== sounded && (c - sounded) % 12 === 0);
  const aStepOrTwo = others.filter((c) => {
    const gap = Math.abs(c - sounded);
    return gap >= 2 && gap <= 5;
  });
  expect(semitoneAway).toHaveLength(1);
  expect(sameName).toHaveLength(1);
  expect(aStepOrTwo).toHaveLength(1);
  // and the same-name one really does read as the same name
  expect(spellMidi(sameName[0]).replace(/-?\d+$/, "")).toBe(spellMidi(sounded).replace(/-?\d+$/, ""));
  // Nothing is far away. The widest of the three is the octave.
  expect(Math.max(...others.map((c) => Math.abs(c - sounded)))).toBeLessThanOrEqual(24);
});

test("which candidate of each kind is taken is the caller's randomness, not a fixed choice", () => {
  // Otherwise the drill offers the same three distractors for a given note
  // every single time, which is a pattern to learn instead of an ear to train.
  const kinds = distractorKinds(60, DEFAULT_RANGE);
  expect(kinds.semitone).toEqual([59, 61]);
  expect(kinds.octave).toEqual([48, 72, 36, 84]);
  expect(kinds.nearby).toEqual([55, 56, 57, 58, 62, 63, 64, 65]);

  // rand() near 1 takes the last of each pool; near 0 the first. The shuffle
  // consumes the tail of the sequence, so only the first three values decide
  // WHICH notes are in the set.
  const low = new Set(buildChoices(60, DEFAULT_RANGE, fixed(0, 0, 0)));
  const high = new Set(buildChoices(60, DEFAULT_RANGE, fixed(0.99, 0.99, 0.99)));
  expect([...low].sort()).toEqual([48, 55, 59, 60]);
  expect([...high].sort()).toEqual([60, 61, 65, 84]);
});

test("every choice stays inside the range, so no note is offered the drill would not play", () => {
  const narrow = { low: 60, high: 67, top: "chosen" };
  for (const sounded of [60, 61, 63, 66, 67]) {
    for (const seed of [0, 0.3, 0.7, 0.99]) {
      const choices = buildChoices(sounded, narrow, () => seed);
      expect(choices).toHaveLength(CHOICE_COUNT);
      expect(new Set(choices).size).toBe(CHOICE_COUNT);
      for (const choice of choices) {
        expect(choice).toBeGreaterThanOrEqual(narrow.low);
        expect(choice).toBeLessThanOrEqual(narrow.high);
      }
    }
  }
});

test("a range with no octave in it still offers four, filled with the nearest instead", () => {
  // A violin's range is under two octaves at the top of it, and a range narrower
  // than an octave has no same-name distractor to give. The shortfall is taken
  // nearest-first rather than the question shrinking to three.
  const narrow = { low: 70, high: 76, top: "strings" };
  const choices = buildChoices(76, narrow, fixed(0, 0, 0, 0));
  expect(choices).toHaveLength(CHOICE_COUNT);
  expect(new Set(choices).size).toBe(CHOICE_COUNT);
  expect(distractorKinds(76, narrow).octave).toEqual([]);
  // the semitone below, then the nearest of the rest
  expect([...choices].sort((a, b) => a - b)).toEqual([71, 74, 75, 76]);
});

test("a range too narrow to hold four notes is not askable, and produces no question", () => {
  // Reachable through the ordinary interface: a one-string unfretted instrument.
  const single = instrumentRange(
    instrument({ name: "One string", fretted: false, fret_count: null, midis: [55] }),
  );
  expect(single).toEqual({ low: 55, high: 55, top: "strings" });
  expect(rangeIsAskable(single)).toBe(false);
  expect(buildChoices(55, single, fixed(0))).toEqual([]);

  // and the boundary is where it says it is
  expect(rangeIsAskable({ low: 60, high: 60 + MIN_RANGE_SEMITONES - 1 })).toBe(false);
  expect(rangeIsAskable({ low: 60, high: 60 + MIN_RANGE_SEMITONES })).toBe(true);
  expect(rangeIsAskable(DEFAULT_RANGE)).toBe(true);
});

test("the note to sound is never the note just heard", () => {
  // The one repeat a person would notice, and it reads as the drill having
  // failed to advance rather than as a second question.
  const range = { low: 60, high: 63, top: "chosen" };
  const previous = 61;
  // Every index in the reduced span, so this covers the note landing below the
  // excluded one and above it.
  const picked = [0, 0.4, 0.9].map((seed) => pickTarget(range, previous, () => seed));
  expect(picked).toEqual([60, 62, 63]);
  expect(picked).not.toContain(previous);

  // With nothing heard yet the whole range is available, including that note.
  expect(pickTarget(range, null, () => 0.3)).toBe(61);
  // A previous note from OUTSIDE the range (the range was changed) excludes
  // nothing, rather than silently dropping a note from the new one.
  expect(pickTarget(range, 90, () => 0.99)).toBe(63);
});

test("the note to sound stays inside the range", () => {
  for (const seed of [0, 0.25, 0.5, 0.75, 0.999999, 1]) {
    const midi = pickTarget(DEFAULT_RANGE, null, () => seed);
    expect(midi).toBeGreaterThanOrEqual(DEFAULT_RANGE.low);
    expect(midi).toBeLessThanOrEqual(DEFAULT_RANGE.high);
  }
});

test("what happened names the note either way, and names the other one when there was one", () => {
  // The note is named in the same words and the same place whether or not it
  // was named correctly, because that is the information. "Wrong" is not
  // something a person can practise with; "you took a G for the G below it" is.
  expect(roundStatement({ sounded: 66, chosen: 66 })).toBe("That was F#4.");
  expect(roundStatement({ sounded: 66, chosen: 65 })).toBe("That was F#4. You chose F4.");
  // The octave error specifically: the name is right and the place is not, and
  // the statement has to distinguish those two rather than say "no".
  expect(roundStatement({ sounded: 66, chosen: 54 })).toBe("That was F#4. You chose F#3.");
  // Nothing is said before an answer exists.
  expect(roundStatement({ sounded: 66, chosen: null })).toBe("");
  expect(roundStatement(null)).toBe("");
  // Both branches open with the identical clause - so a reader's eye lands on
  // the same words in the same place either way.
  expect(roundStatement({ sounded: 66, chosen: 65 })).toContain(
    roundStatement({ sounded: 66, chosen: 66 }),
  );
});

test("how the drill is going is two counts and nothing else", () => {
  expect(progressStatement({ asked: 0, named: 0 })).toBe("Nothing named yet.");
  expect(progressStatement({ asked: 1, named: 1 })).toBe("1 note, 1 named as heard.");
  expect(progressStatement({ asked: 12, named: 9 })).toBe("12 notes, 9 named as heard.");
  // Zero is a word rather than a nought, the same rule the practice page
  // applies - a bare nought beside a total reads like a mark out of five.
  expect(progressStatement({ asked: 4, named: 0 })).toBe("4 notes, none named as heard.");
});

test("nothing this exercise says is a grade", () => {
  // Every string the module can produce, checked against practice.js's own
  // list. The rule lives there so there is one list and one way of applying it.
  const range = instrumentRange(instrument());
  const said = [
    rangeLabel(range),
    rangeStatement(range, "Guitar (standard)"),
    rangeSourceStatement({ low: 55, high: 76, top: "strings" }),
    referenceStatement(instrument({ reference_pitch: 415 })),
    roundStatement({ sounded: 66, chosen: 66 }),
    roundStatement({ sounded: 66, chosen: 65 }),
    progressStatement({ asked: 0, named: 0 }),
    progressStatement({ asked: 12, named: 0 }),
    progressStatement({ asked: 12, named: 12 }),
    sessionNote({ asked: 12, named: 0, range, instrumentName: "Guitar (standard)" }),
    sessionNote({ asked: 12, named: 12, range }),
    loggedStatement(930),
  ];
  // Anchored: the list is proved to be able to catch something, or every
  // assertion below passes by checking nothing.
  expect(forbiddenWord(`this week was my ${FORBIDDEN_WORDS[0]}`)).toBe(FORBIDDEN_WORDS[0]);
  for (const text of said) {
    expect(text, `"${text}"`).not.toBe(undefined);
    expect(forbiddenWord(text), `"${text}"`).toBeNull();
    // No percentage anywhere. A count is a fact; a number out of a hundred is a
    // mark, and a mark invites a colour.
    expect(text, `"${text}"`).not.toMatch(/%|per cent|percent/i);
    // and no verdict adverbs, which the shared list does not carry because the
    // practice page has no occasion to use them
    expect(text, `"${text}"`).not.toMatch(/\b(wrong|incorrect|correct|score|accuracy)\b/i);
  }
});

test("the session's note says what was done and in what range", () => {
  const range = instrumentRange(instrument());
  expect(sessionNote({ asked: 12, named: 9, range, instrumentName: "Guitar (standard)" })).toBe(
    "Hear a note, name it. 12 notes, 9 named as heard. E2 to D6, Guitar (standard).",
  );
  // No instrument, so nothing is named that was not chosen.
  expect(sessionNote({ asked: 3, named: 3, range: DEFAULT_RANGE })).toBe(
    "Hear a note, name it. 3 notes, 3 named as heard. C2 to C6.",
  );
  // A drill that was listened to and never answered is still practice, and the
  // note says exactly that rather than reading as a broken row.
  expect(sessionNote({ asked: 0, named: 0, range: DEFAULT_RANGE })).toBe(
    "Hear a note, name it. Nothing named yet. C2 to C6.",
  );
});

test("a reference pitch that is not the synthesiser's is disclosed, and one that is is not", () => {
  // playPitch's own note says the synth is equal-tempered around A440 and takes
  // no reference, so an instrument defined at A415 has its frequencies SHOWN at
  // A415 and is SOUNDED at A440. A drill that quietly used such a definition
  // would be teaching names against pitches that are not the player's own.
  const baroque = referenceStatement(instrument({ name: "Baroque lute", reference_pitch: 415 }));
  expect(baroque).toContain("Baroque lute");
  expect(baroque).toContain("A415");
  expect(baroque).toContain(`A${SYNTH_REFERENCE_HZ}`);
  // Silent when there is nothing to disclose, which is most instruments - a
  // disclosure printed unconditionally is one nobody reads.
  expect(referenceStatement(instrument())).toBe("");
  expect(referenceStatement(null)).toBe("");
  // and a fractional one is not rounded away into looking like 440
  expect(referenceStatement(instrument({ reference_pitch: 440.5 }))).toContain("A440.5");
});

test("what was logged is stated as a length, not as a result", () => {
  expect(loggedStatement(930)).toBe("15m of ear training is in your practice history.");
  expect(loggedStatement(30)).toBe("under a minute of ear training is in your practice history.");
});
