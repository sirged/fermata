// ScoreCompare.svelte classifies backend transcription warnings by matching
// known wording (see src/lib/warning-patterns.js) rather than by any
// structured tag from the server. A plain build can't catch a rewording in
// server/fermata/tabextract.py silently degrading that classification - the
// per-score list would just start missing lines, or the headline bar count
// would quietly stop appearing, with nothing failing anywhere. Assert the
// patterns still match strings taken from the current backend wording, so a
// mismatch fails loudly here instead of only showing up as a worse summary.
import { STANDING_LIMITS, BAR_RE } from "../src/lib/warning-patterns.js";

// Copied verbatim from server/fermata/tabextract.py at the time this check
// was written (_TUPLET_WARNING, _TIE_WARNING, and _rhythm_report's
// bar-conformance sentences). Update these fixtures - and check the
// patterns above still make sense - whenever that wording changes on
// purpose; that's exactly the case this check exists to catch otherwise.
const TUPLET_WARNING =
  "tuplets (triplets and similar) are not detected - a note written inside a tuplet " +
  "will show its plain written duration rather than the shortened tuplet duration";
const TIE_WARNING =
  "tie detection is low confidence - some tied notes may show up as separately " +
  "re-struck notes instead of one held note";
const OVERFULL_BARS_SENTENCE =
  "3 of 50 bar(s) hold more than their time signature allows. Music written in two " +
  "voices (a melody over a separate bass line) is separated into concurrent voices " +
  "where the stems say so, but a bar whose voices the stems do not separate is still " +
  "flattened into one, and an undetected tuplet or a missed flag lands here too - the " +
  "notes and their individual durations can still be right while the bar as a whole is " +
  "not, so playback timing will drift in those bars";
const SHORT_BARS_SENTENCE =
  "1 of 50 bar(s) hold less than their time signature allows - a note whose duration " +
  "was read short, or one dropped for want of a fret number, leaves the bar with less " +
  "music in it than the meter says it holds. Where such a bar has more than one voice " +
  "the missing part is filled with silence deduced from the meter, at the front of the " +
  "voice if that is where it entered late, so the voices still play in time with each " +
  "other - that silence is not counted here and is not written as a rest. Either way " +
  "the emitted MusicXML falls short by the same amount, so any MusicXML tool will " +
  "report those bars too";
// The two per-bar sentences _rhythm_report adds for inferred silence and for
// bars nothing was read from. These are facts about THIS score and belong in
// the per-score list, so they must NOT match BAR_RE - a bar-conformance line is
// folded into the headline count instead and deliberately not listed again, so
// a rewording that made either of these start like one would delete it from the
// summary with nothing failing anywhere.
const PADDED_BARS_SENTENCE =
  "2 of 50 bar(s) contain silence that was deduced from the time signature rather than " +
  "read from a rest printed in the score, 5.5 quarter note(s) of it in total. The bars " +
  "are: 7, 31. A voice with a note missing from it is filled out the same way a " +
  "genuinely resting voice is, so that the voices of the bar still play in time with " +
  "each other; the inferred silence is NOT counted towards those bars adding up, and is " +
  "written into the MusicXML as <forward> rather than as a rest so no consumer mistakes " +
  "it for one the engraver printed";
const UNREAD_BARS_SENTENCE =
  "1 of 50 bar(s) hold nothing that was read from the score - no fret number and no " +
  "rest glyph fell inside them - and are emitted as a whole bar of rests so the bar " +
  "numbering still matches the source. The bars are: 12. Those bars add up to their " +
  "time signature and so pass every arithmetic check, but nothing in them was read: " +
  "they are not evidence that the score was transcribed, and a bar that is genuinely " +
  "silent in the source cannot be told from one whose contents were missed";
// Score-specific sentences #115/#117 added or reworded. Each starts with a
// digit the way the bar-arithmetic sentences above do (or, for the spacing
// sentence, names bars the same way they do), so each is its own regression
// fixture against the same failure mode: a rewording that made one of these
// start matching BAR_RE would fold it into the headline bar count and
// silently drop it from the per-score list.
const FLOORED_NOTE_DURATIONS_SENTENCE =
  "73 notehead(s) across 4 staff system(s) were read with no stem this decoder could " +
  "find. A note's flags and beams hang off its stem, so for those notes both the " +
  "duration and which of a bar's voices they belong to rest on a guess rather than a " +
  "reading: where such a head could not be attached to a neighbouring stem, it was " +
  "emitted at the plain quarter, the LONGEST duration its notehead on its own allows";
const SPACING_STAVES_SENTENCE =
  "durations were read from the engraved notation for 3 staff system(s); 2 staff " +
  "system(s) could not be read that way and use a rougher estimate from note spacing " +
  "instead - treat those sections as low confidence. The bars they produced are: " +
  "5, 6, 7, 11.";
const DEGRADED_STAVES_SENTENCE =
  "2 staff system(s) were read from the engraved notation but not everything on them " +
  "could be read - a music-font glyph this decoder has not been calibrated for, a " +
  "notehead with no stem this decoder could find, or a rest whose printed position " +
  "did not say which value it was - so treat their durations as medium confidence. " +
  "The bars they produced are: 3, 4.";

const problems = [];

if (!STANDING_LIMITS.some((lim) => lim.test.test(TUPLET_WARNING))) {
  problems.push("STANDING_LIMITS no longer matches tabextract.py's _TUPLET_WARNING wording");
}
if (!STANDING_LIMITS.some((lim) => lim.test.test(TIE_WARNING))) {
  problems.push("STANDING_LIMITS no longer matches tabextract.py's _TIE_WARNING wording");
}
if (!BAR_RE.test(OVERFULL_BARS_SENTENCE)) {
  problems.push("BAR_RE no longer matches _rhythm_report's overfull-bars sentence");
}
if (!BAR_RE.test(SHORT_BARS_SENTENCE)) {
  problems.push("BAR_RE no longer matches _rhythm_report's short-bars sentence");
}
if (BAR_RE.test(PADDED_BARS_SENTENCE)) {
  problems.push(
    "BAR_RE now matches _rhythm_report's inferred-silence sentence - it would be " +
      "folded into the headline bar count and dropped from the per-score list",
  );
}
if (BAR_RE.test(UNREAD_BARS_SENTENCE)) {
  problems.push(
    "BAR_RE now matches _rhythm_report's bars-read-as-nothing sentence - it would be " +
      "folded into the headline bar count and dropped from the per-score list",
  );
}
// Same failure mode again for the three sentences #115/#117 added or
// reworded: none of them is bar-arithmetic prose, so none of them may ever
// start matching BAR_RE, on pain of being folded into the headline bar count
// and silently dropped from the per-score list.
for (const [name, sentence] of [
  ["floored-note-durations", FLOORED_NOTE_DURATIONS_SENTENCE],
  ["spacing-derived-staves", SPACING_STAVES_SENTENCE],
  ["degraded-staves", DEGRADED_STAVES_SENTENCE],
]) {
  if (BAR_RE.test(sentence)) {
    problems.push(
      `BAR_RE now matches _rhythm_report's ${name} sentence - it would be folded into ` +
        "the headline bar count and dropped from the per-score list",
    );
  }
  if (STANDING_LIMITS.some((lim) => lim.test.test(sentence))) {
    problems.push(`_rhythm_report's ${name} sentence reads as a standing limit`);
  }
}
for (const [name, sentence] of [
  ["inferred-silence", PADDED_BARS_SENTENCE],
  ["bars-read-as-nothing", UNREAD_BARS_SENTENCE],
]) {
  // Both name the affected bars after a fixed lead-in, which is what a reader
  // checks against the PDF. A rewording that dropped it would leave a warning
  // stating a count and nothing else.
  if (!/The bars are: \d+/.test(sentence)) {
    problems.push(`_rhythm_report's ${name} sentence no longer names the bars`);
  }
}
for (const [name, sentence] of [
  ["spacing-derived-staves", SPACING_STAVES_SENTENCE],
  ["degraded-staves", DEGRADED_STAVES_SENTENCE],
]) {
  if (!/The bars they produced are: \d+/.test(sentence)) {
    problems.push(`_rhythm_report's ${name} sentence no longer names the bars it produced`);
  }
}

if (problems.length) {
  console.error("warning pattern check failed:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log("warning pattern check passed");
