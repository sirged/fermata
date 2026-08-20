// Fixture data + route stubs for the transcription-warnings behavior in
// ScoreCompare.svelte (see ../score-compare-warnings.spec.js). Kept separate
// from the spec so the shapes below can be reused across scenarios and
// stay obviously in sync with the real backend contract they model.
//
// The transcription-object shape mirrors server/fermata/api.py's
// _transcription_dict / _BLOB_TOP_LEVEL exactly: `warnings`, `bars_overfull`,
// `bars_short`, `bars_defective`, and `bars_measured` are lifted to the TOP
// LEVEL of the transcription object on both GET /transcription and POST
// /transcribe - they are siblings of `confidence`, not nested inside it.
// `confidence` itself stays a pure aspect->sentence mapping
// (frets/rhythm/time_signature); dropping numbers into it would make
// anything that renders it as prose print "40" as a confidence statement.
// Getting this backwards was a real integration bug (frontend read the bar
// counts nested inside confidence.confidence; the server never put them
// there) that neither side's own tests caught, because each was written
// against its own idea of the shape. These fixtures exist so that mistake
// can't quietly happen again on either side of this file.

export const MIN_PDF = Buffer.from(
  "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" +
    "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
    "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n" +
    "xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n" +
    "trailer<</Size 4/Root 1 0 R>>\nstartxref\n164\n%%EOF",
  "utf-8",
);

export const SCORE = {
  id: 1,
  title: "Test Score",
  file_type: "pdf",
  has_transcription: true,
  favorite: false,
  content_kind: "tab",
  tags: [],
};

// Renders a real staff/tab via the real alphaTab importer - not a stub - so
// a test that checks the staff pane's bounding box is measuring something
// that actually painted, the way "a viewer that built cleanly and rendered
// nothing" would not.
export const SAMPLE_TEX = `\\title "Test Score"
\\tempo 90
.
:8 0.1 3.2 2.3 0.1 3.2 2.3 0.1 3.2 |
:8 0.2 2.3 2.4 0.2 2.3 2.4 0.2 2.3 |
:2 (0.6 2.5 2.4 0.3 0.2 0.1) :2 (0.6 2.5 2.4 0.3 0.2 0.1)`;

// A realistic nine-item warning list: one bar-conformance sentence plus the
// two standing-limitation sentences (tuplets/ties - see warning-patterns.js)
// plus six other per-score items, matching the shape (if not the literal
// wording) of a real transcribe() response - see the "9 of 50" numbers.
export const NINE_WARNINGS = [
  "3 of 50 bar(s) hold more than their time signature allows. Music written in two voices (a melody over a separate bass line) is separated into concurrent voices where the stems say so, but a bar whose voices the stems do not separate is still flattened into one, and an undetected tuplet or a missed flag lands here too - the notes and their individual durations can still be right while the bar as a whole is not, so playback timing will drift in those bars",
  "tuplets (triplets and similar) are not detected - a note written inside a tuplet will show its plain written duration rather than the shortened tuplet duration",
  "tie detection is low confidence - some tied notes may show up as separately re-struck notes instead of one held note",
  "time signature not detected - glyphs live in a subsetted music font at remapped codepoints; assumed 4/4 for bar/beat grouping, pass time_signature to override",
  "key signature: could not be read - notes are spelled as if there were none",
  "2 fret number(s) could not be matched to a note in the engraved notation and got an estimated duration instead - treat those specific notes as low confidence",
  "5 digit token(s) near a tab staff could not be assigned to a string",
  "music font: unrecognised glyph set 'CustomTabFont-Subset' - some symbols may not decode",
  "1 fret number(s) above 24 were read directly from the PDF's own text (not from a merge) - likely two adjacent notes rendered as one text span in the source - treat those frets as low confidence",
];
// scopedWarnings = NINE_WARNINGS minus the 2 standing-limitation lines = 7.
// One of those 7 is the bar-conformance sentence above; with the structured
// bars_defective/bars_measured fields present (see REALISTIC_TRANSCRIPTION)
// that line is folded into the headline, leaving 6 "more".
export const NINE_WARNINGS_EXPECTED_SUMMARY =
  "3 of 50 bars don't add up · rhythm confidence medium · 6 more";

export const CAPPED_CONFIDENCE = {
  frets: "high - read directly from vector text spans positioned against detected tab staff lines",
  rhythm:
    "medium - decoded from the score's engraving, but some music-font glyphs used by this score are outside the decoder's calibrated vocabulary",
  time_signature: "low - not detected, assumed 4/4",
};

export const CLEAN_CONFIDENCE = {
  frets: "high - read directly from vector text spans positioned against detected tab staff lines",
  rhythm:
    "high - decoded directly from the notehead/stem/flag/beam/dot glyphs in the score's own engraving",
  time_signature: "high - read directly from the time-signature digit glyphs printed on the score",
};

// The regression fixture for finding 1: a bar wrong in BOTH directions at
// once (typical of two-voice writing, one voice over its meter and the
// other under) is counted into BOTH tabextract._bar_conformance's overfull
// and short - so summing the two warning sentences' counts (7 + 6 = 13)
// would claim more defective bars than exist (12 measured). Across the full
// production library (144 extractable scores) not one score is wrong in
// both directions at once - this does not reproduce on any real score, only
// on constructed two-voice material - so this fixture is the only place
// this case is exercised at all. If it is lost, the bug it encodes will not
// be rediscovered by testing against real scores.
export const TWO_VOICE_WARNINGS = [
  "7 of 12 bar(s) hold more than their time signature allows. Music written in two voices (a melody over a separate bass line) is separated into concurrent voices where the stems say so, but a bar whose voices the stems do not separate is still flattened into one, and an undetected tuplet or a missed flag lands here too - the notes and their individual durations can still be right while the bar as a whole is not, so playback timing will drift in those bars",
  "6 of 12 bar(s) hold less than their time signature allows - a note whose duration was read short, or one dropped for want of a fret number, leaves the bar with a gap at the end. The emitted score says so rather than padding it out, so any MusicXML tool will report those bars too",
];
export const TWO_VOICE_CONFIDENCE = {
  frets: "high - read directly from vector text spans positioned against detected tab staff lines",
  rhythm:
    "low overall - individual durations were read from the score's own engraving, but 9 of 12 bar(s) do not add up to their time signature",
  time_signature: "high - read directly from the time-signature digit glyphs printed on the score",
};

export const STANDING_LIMITS_ONLY_WARNINGS = [
  "tuplets (triplets and similar) are not detected - a note written inside a tuplet will show its plain written duration rather than the shortened tuplet duration",
  "tie detection is low confidence - some tied notes may show up as separately re-struck notes instead of one held note",
];

/**
 * Builds a GET /transcription (or POST /transcribe) response body.
 *
 * `bars` is `{ defective, measured, overfull, short }` or omitted entirely -
 * mirrors _transcription_dict lifting bars_* out of the stored blob onto the
 * top level ONLY when they were present in it (an edited row, or a row
 * written before this field existed, legitimately has none - absent, not
 * zero, is what that means; see _BLOB_TOP_LEVEL in api.py).
 *
 * `nestWarningsOnly` models the GET-shape inconsistency that motivated
 * ScoreCompare.svelte's own defensive read: when true, `warnings` is
 * IsUnusual and only present nested inside the `confidence` JSON string,
 * not at the top level, and the frontend must still find it.
 */
export function transcriptionResponse({ warnings, confidence, bars, nestWarningsOnly = false }) {
  const blob = { warnings, confidence };
  if (bars) {
    blob.bars_overfull = bars.overfull ?? 0;
    blob.bars_short = bars.short ?? 0;
    blob.bars_defective = bars.defective;
    blob.bars_measured = bars.measured;
  }
  const body = {
    id: 1,
    score_id: 1,
    format: "alphatex",
    content: SAMPLE_TEX,
    source: "extracted",
    confidence: JSON.stringify(blob),
  };
  if (!nestWarningsOnly) body.warnings = warnings;
  if (bars) {
    body.bars_overfull = blob.bars_overfull;
    body.bars_short = blob.bars_short;
    body.bars_defective = blob.bars_defective;
    body.bars_measured = blob.bars_measured;
  }
  return body;
}

/**
 * A saved hand edit's response shape, mirroring server/fermata/api.py's
 * _transcription_dict exactly for a row whose `confidence` column is NULL
 * (every edited row): `warnings` is stated as `[]` and all four bar keys as
 * `null` - NEVER omitted - specifically because an earlier version of the
 * backend omitted them, and a client that spread such a response over the
 * transcription it already held kept the PRE-EDIT figures. A user opened a
 * score reading "4 of 50 bars don't add up", fixed exactly those bars,
 * saved, and the panel still said "4 of 50 bars don't add up" and still
 * listed warnings about notes that no longer existed - the confidently
 * wrong state this whole feature exists to prevent. `null` (not `0`) is the
 * deliberate way of saying "nothing has measured this content"; `0` would
 * claim every bar was measured and every one of them added up.
 */
export function editedTranscriptionResponse() {
  return {
    id: 1,
    score_id: 1,
    format: "musicxml",
    content: "<score-partwise><!-- hand-edited --></score-partwise>",
    source: "edited",
    confidence: null,
    warnings: [],
    bars_overfull: null,
    bars_short: null,
    bars_defective: null,
    bars_measured: null,
  };
}

/**
 * Stubs every /api route ScoreCompare.svelte/PdfViewer/TabViewer touch for
 * score id 1. `transcription` is either a transcriptionResponse() object, or
 * `null` for "no transcription yet" (GET returns 404, matching a fresh
 * score).
 *
 * `editRevert`, if given, wires PUT (save) to always answer with
 * `editedTranscriptionResponse()` and DELETE (revert) to always answer with
 * `transcription` again - modeling "hand-edit this extracted row, then
 * revert" regardless of what the test actually types into the editor.
 */
export async function stubScoreApi(page, transcription, { editRevert = false } = {}) {
  await page.route("**/api/scores/1", (route) => route.fulfill({ json: SCORE }));
  await page.route("**/api/scores/1/file", (route) =>
    route.fulfill({ body: MIN_PDF, contentType: "application/pdf" }),
  );
  await page.route("**/api/scores/1/practice", (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
  await page.route("**/api/scores/1/transcription/analysis", (route) =>
    route.fulfill({ json: { extractable: true } }),
  );
  if (editRevert) {
    await page.route("**/api/scores/1/transcription", (route) => {
      const method = route.request().method();
      if (method === "PUT") return route.fulfill({ json: editedTranscriptionResponse() });
      if (method === "DELETE") return route.fulfill({ json: transcription });
      return route.fulfill({ json: transcription });
    });
    return;
  }
  await page.route("**/api/scores/1/transcription", (route) => {
    if (route.request().method() !== "GET") return route.continue();
    if (!transcription) return route.fulfill({ status: 404, json: { detail: "not found" } });
    return route.fulfill({ json: transcription });
  });
}
