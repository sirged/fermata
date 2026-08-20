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
  "was read short, or one dropped for want of a fret number, leaves the bar with a gap " +
  "at the end. The emitted score says so rather than padding it out, so any MusicXML " +
  "tool will report those bars too";

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

if (problems.length) {
  console.error("warning pattern check failed:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log("warning pattern check passed");
