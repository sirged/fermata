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
// Pure logic, no runes, no imports - same reason provenance.js is - so the
// row-selection rules (what's hidden, what's "not measured") can be tested
// without a browser. What actually renders is Disclosures.svelte.
export const DISCLOSURE_ROWS = [
  { key: "repeats_unread", label: "Repeat marks not read", barsKey: "repeats_unread_bars" },
  { key: "endings_unread", label: "Volta (ending) brackets not read", barsKey: "endings_unread_bars" },
  { key: "endings_truncated", label: "Volta endings truncated", barsKey: "endings_truncated_bars" },
  { key: "endings_incomplete", label: "Volta numbering doesn't start at 1" },
  {
    key: "form_marks_unanchored",
    label: "Repeat/volta marks with no bar to anchor to",
    barsKey: "form_marks_unanchored_bars",
  },
  {
    key: "nav_marks_unresolved",
    label: "Navigation marks written with no jump target",
    barsKey: "nav_marks_unresolved_bars",
  },
  // No barsKey: a navigation mark with no bar to name has no bar number to
  // report (docs/musicxml-tab-profile.md, Rule 16 / issue #134 phase 2).
  { key: "nav_marks_unanchored", label: "Navigation marks with no bar to anchor to" },
  // The list beside this one is PAGES, not bars, for the same reason -
  // see api_models.py's comment on systems_unread_pages.
  { key: "systems_unread", label: "Systems not read at all", barsKey: "systems_unread_pages", barUnit: "page" },
  { key: "coincident_unsplit_pairs", label: "Coincident notehead pairs not split" },
  { key: "staves_coincident_unsplit", label: "Staves with an unsplit coincident pair" },
  { key: "unison_digits_shared", label: "Notes given another note's fret number" },
  { key: "dots_unassigned", label: "Augmentation dots not assigned to a note" },
  {
    key: "dots_unassigned_no_candidate",
    label: "Unassigned dots with no notehead or rest nearby",
  },
  {
    key: "dots_unassigned_eliminated",
    label: "Unassigned dots that reached a candidate but lost it to a shared owner",
  },
  { key: "staves_dots_unassigned", label: "Staves with an unassigned dot" },
  { key: "notes_no_stem", label: "Noteheads read with no stem" },
  { key: "staves_no_stem", label: "Staves with a stemless notehead" },
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
      measured,
      value: measured ? value : null,
      barUnit: row.barUnit ?? "bar",
      bars: measured && Array.isArray(barsRaw) ? barsRaw : [],
    });
  }
  return rows;
}
