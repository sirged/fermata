// Patterns for classifying backend transcription warnings (see
// server/fermata/tabextract.py: _rhythm_report, _bar_conformance) into
// what's a fact about this score and what reads identically on every score.
// Kept in their own module, apart from ScoreCompare.svelte's presentation
// logic, so they can be checked directly against the backend's current
// wording (see scripts/check-warning-patterns.mjs) instead of only ever
// being exercised inside a component.

// Two caveats that read identically on every transcribed score
// (tabextract._TUPLET_WARNING / _TIE_WARNING) - a standing limit of the
// feature, not a fact about this score, so they're kept out of the
// per-score list and its count.
export const STANDING_LIMITS = [
  { test: /tuplets? \(triplets and similar\) are not detected/i, label: "tuplets aren't detected" },
  {
    test: /a tie is written only where the curve joining its two notes was matched/i,
    label: "a tie across a system break isn't matched",
  },
];

// Identifies (but does not total) _rhythm_report's bar-conformance
// sentences - "N of M bar(s) hold more/less than their time signature
// allows...". Never sum the two matches' captured counts into a bar-defect
// total: _bar_conformance counts a bar into BOTH overfull and short when one
// voice is over its meter and another is under it (the two-voice case), so
// overfull + short can double-count a bar and exceed bars_measured - see
// docs/musicxml-tab-profile.md. bars_defective/bars_measured from the
// backend's structured confidence field is the only correct source for that
// total; this regex exists only to classify a warning line as "about bars"
// (e.g. to avoid double-reporting it once the structured total already has).
export const BAR_RE =
  /^(\d+) of (\d+) bar\(s\) hold (?:more|less) than (?:its|their) time signature allows/i;
