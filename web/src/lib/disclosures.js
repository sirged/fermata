// The structural-form and inference disclosures TranscriptionOut carries
// beside the Rule 8 conformance figures (issue #155).
//
// Every one of these counters was computed, stored, reloaded through the
// API, and mirrored in the response model - and then read by no interface
// code. Only the warning PROSE reached a reader, through ScoreCompare.svelte's
// generic warnings list; the count each sentence is built from never did. See
// api_models.py's TranscriptionOut for the authoritative field list this
// mirrors - every field it carries in the "_BAR_KEYS" group past
// bars_overfull/bars_short/bars_defective/bars_measured/bars_padded/
// bars_unread (which already reach a reader through ScoreCompare's bar-count
// headline and the warning prose the *_bars lists feed) has a row here.
//
// ONE ROW PER COUNTER, on purpose - the issue's own fix shape is "decide the
// presentation once for the family... the next decoder disclosure should
// land in the interface by adding a row, not a component." A new counter
// lands here as one line in DISCLOSURE_ROWS, not a new branch of rendering
// logic.
//
// THE KEY LIST ITSELF IS GUARDED, not just hand-checked once: this file is
// one of FOUR hand-kept copies of "which fields are structural disclosures"
// (server/fermata/tabextract.py's ExtractionResult fields, server/fermata/
// api.py's _BAR_KEYS tuple, api_models.py's TranscriptionOut fields, and this
// one), and it was the only one nothing checked - a fake counter added to
// api.py, or a row silently dropped from here, passed every existing test.
// web/tests/disclosure-keys.json vendors the same key list from the API side
// (kept honest against api._BAR_KEYS by
// server/tests/test_disclosure_keys.py), and web/tests/unit/disclosures.spec.js
// asserts DISCLOSURE_ROWS's keys equal that vendored file's, in both
// directions, by name.
//
// Pure logic, no runes, no imports - same reason provenance.js is - so the
// row-selection rules (what's hidden, what's "not measured") can be tested
// without a browser. What actually renders is Disclosures.svelte.
export const DISCLOSURE_ROWS = [
  { key: "repeats_unread", label: "Repeat marks not read", barsKey: "repeats_unread_bars" },
  { key: "endings_unread", label: "Volta (ending) brackets not read", barsKey: "endings_unread_bars" },
  { key: "endings_truncated", label: "Volta endings truncated", barsKey: "endings_truncated_bars" },
  // A FLAG, not a count - tabextract.py:5172 trips it on ANY gap in the
  // volta numbers actually read, not only a missing "1" (seen_ints !=
  // range(1, seen_ints[-1] + 1) catches [1, 3] just as it catches [2, 3]),
  // and the field itself is only ever 0 or 1 (tabextract.py:5166-5173). A
  // bare "1" in a column of counts reads as "one thing", which is the wrong
  // claim for a yes/no fact - kind: "flag" renders it as presence, not a
  // number.
  { key: "endings_incomplete", label: "Volta numbering has gaps", kind: "flag" },
  // OVERSTATES if read as "repeat/volta marks": tabextract.py:1156 folds a
  // plain bar-style group (a final or double barline, carrying no <repeat>
  // and no <ending> at all - see docs/musicxml-tab-profile.md's "bar-style
  // alone") into the same unanchored-count path as an actual repeat/volta
  // mark whenever it fails to anchor (tabextract.py:1159-1161). And the bar
  // list is a NEAREST-bar fallback, not the bar the mark was drawn over -
  // the mark had no bar to anchor to at all, so tabextract.py:5131-5133's
  // own warning prose calls the list "Nearest bars:", not "bars:".
  {
    key: "form_marks_unanchored",
    label: "Repeat/volta/bar-style marks with no bar to anchor to",
    barsKey: "form_marks_unanchored_bars",
    barsLabel: "near bar",
  },
  // Counted per BAR, not per mark: tabextract.py:2163-2164 counts one bar
  // once even when two navigation instructions close on it together.
  {
    key: "nav_marks_unresolved",
    label: "Bars whose navigation marks name no target this transcription holds",
    barsKey: "nav_marks_unresolved_bars",
  },
  // No barsKey: a navigation mark with no bar to name has no bar number to
  // report (docs/musicxml-tab-profile.md, Rule 16 / issue #134 phase 2).
  { key: "nav_marks_unanchored", label: "Navigation marks with no bar to anchor to" },
  // "System" here means a staff-sized GROUP OF LINES the page-scan found and
  // could not read as a staff (neither 5 nor 6 lines) - tabextract.py:
  // 344-346. Whether that group was actually a musical system is inferred
  // from its size, not confirmed - a stray staff-sized rule or decoration
  // would count the same way. The list beside this one is PAGES, not bars,
  // for the reason api_models.py's comment on systems_unread_pages gives.
  { key: "systems_unread", label: "Staff-sized line groups not read as a system", barsKey: "systems_unread_pages", barUnit: "page" },
  // Counted once per coincident GROUP (glyph_rhythm.py:3103-3113,
  // specifically the `coincident_unsplit_pairs += 1` at line 3111, inside
  // the loop over `_dup_groups` - one increment per group regardless of how
  // many duplicate copies it holds), not once per notehead.
  { key: "coincident_unsplit_pairs", label: "Coincident notehead groups not split across voices" },
  { key: "staves_coincident_unsplit", label: "Staves with an unsplit coincident group" },
  { key: "unison_digits_shared", label: "Notes given another note's fret number" },
  { key: "dots_unassigned", label: "Augmentation dots not assigned to a note" },
  {
    key: "dots_unassigned_no_candidate",
    label: "Unassigned dots with no notehead or rest nearby",
  },
  // glyph_rhythm.py:2957-2959: this dot DID reach a candidate notehead/rest,
  // but every one it reached already carried a dot at a conflicting tier
  // (a different, already-bound position) or was a same-x duplicate of a
  // candidate already given one - not "nothing nearby" (that's the sibling
  // counter above).
  {
    key: "dots_unassigned_eliminated",
    label: "Unassigned dots whose only candidates already had a conflicting or duplicate dot",
  },
  { key: "staves_dots_unassigned", label: "Staves with an unassigned dot" },
  // Quarter-note heads or shorter ONLY - glyph_rhythm.py:2934-2950 counts a
  // stemless filled (or x/diamond) notehead here specifically because a
  // missing stem costs its DURATION (the flag/beam that would say which
  // note value hangs off the stem it can't find); a half or whole notehead
  // is deliberately excluded (glyph_rhythm.py:2946-2950) because neither
  // shape can carry a flag or beam in any notation, so a missing stem on one
  // of those costs only the voice signal, not the duration, and is not
  // counted here.
  { key: "notes_no_stem", label: "Noteheads read with no stem (quarter or shorter)" },
  { key: "staves_no_stem", label: "Staves with a stemless notehead" },
  // HOW THE DURATIONS WERE OBTAINED (issue #117). tabextract.py counted both
  // of these on every extraction from the start, inside `rhythm_provenance` -
  // a field nothing stores, nothing returns and no interface code reads - so
  // the bar lists beside them (spacing_bars / degraded_bars, already on
  // TranscriptionOut) had no counter to hang on and never appeared here.
  // Durations inferred from the horizontal gaps between noteheads are only as
  // good as the engraver's spacing being proportional, which a justified or
  // hand-adjusted system is not, and they presented identically to durations
  // that were read off flags, beams, dots and rest shapes.
  {
    key: "staves_spacing_rhythm",
    label: "Staves whose durations came from note spacing, not from glyphs",
    barsKey: "spacing_bars",
  },
  {
    key: "staves_degraded_rhythm",
    label: "Staves read from the engraving with something on them left unread",
    barsKey: "degraded_bars",
  },
  // A REFUSAL, not a defect in what was read (issue #129): a time signature
  // printed on the page whose digits include a glyph the decoder has no
  // category for, refused outright rather than assembled from the digits that
  // were recognised. No barsKey - a meter that was refused governs bars this
  // transcription barred by some other meter, so there is no bar number that
  // is the refusal's own.
  {
    key: "meter_digits_unreadable",
    label: "Printed time signatures refused over an unrecognised digit glyph",
  },
];

/**
 * Turns a transcription object (the shape TranscriptionOut/api.js hand back)
 * into the rows Disclosures.svelte renders, applying the three rules the
 * issue asks for:
 *
 *   - a counter that is exactly 0 is HIDDEN - a wall of zeros is noise, and a
 *     transcription with all zeros shows nothing new here.
 *   - a counter that is `null`/`undefined` (a legacy row from before it was
 *     computed, or an edited row with no confidence at all) is DISTINCT from
 *     zero: it renders as "not measured", never silently as "0" - the API
 *     contract already keeps the two apart and this must not collapse them.
 *   - a `*_bars` (or `*_pages`) list rides along as the counter's detail:
 *     the bar/page numbers it exists to name.
 *
 * The one thing that gates the WHOLE section rather than one row: if every
 * single counter below is null, nothing here was ever computed for this row
 * at all (the common shape of a hand-edited row - see saveEdit() in
 * ScoreCompare.svelte, which states every one of these `null` on purpose).
 * That state already renders as nothing elsewhere on this panel (no bar
 * headline, no warnings block), and a wall of seventeen "not measured" rows
 * would be exactly the noise this function exists to avoid - so this
 * returns no rows at all rather than that wall. A row that measured SOME of
 * these counters (a real extraction whose schema predates one particular
 * counter) still shows the gap on that one counter specifically, because in
 * that case something else on this same object is a real number and the
 * absence of this one is worth knowing.
 */
export function disclosureRows(t) {
  if (!t) return [];
  const anyMeasured = DISCLOSURE_ROWS.some((row) => t[row.key] !== null && t[row.key] !== undefined);
  if (!anyMeasured) return [];

  const rows = [];
  for (const row of DISCLOSURE_ROWS) {
    const value = t[row.key];
    if (value === 0) continue; // hidden - good news needs no row
    const measured = value !== null && value !== undefined;
    const barsRaw = row.barsKey ? t[row.barsKey] : null;
    rows.push({
      key: row.key,
      label: row.label,
      kind: row.kind ?? "count",
      measured,
      value: measured ? value : null,
      barUnit: row.barUnit ?? "bar",
      barsLabel: row.barsLabel ?? null,
      bars: measured && Array.isArray(barsRaw) ? barsRaw : [],
    });
  }
  return rows;
}
