// disclosureRows() called directly - no browser, no backend. disclosures.js
// has no runes and no imports precisely so this can import it, the same
// reason provenance.spec.js gives for provenance.js.
//
// What is NOT tested here is whether any of this reaches a screen - that is
// tests/browser/score-compare-disclosures.spec.js's job, and a suite that
// only checked this selection logic would prove nothing about issue #155,
// which was seventeen fields computed and never displayed.
import { expect, test } from "@playwright/test";

import { DISCLOSURE_ROWS, disclosureRows } from "../../src/lib/disclosures.js";

// A transcription with every counter present and real (the shape a current
// extraction produces): all zero except one real defect in each family, so
// this doubles as "every field this issue lists is wired to a row."
function baseTranscription(overrides = {}) {
  const t = {};
  for (const row of DISCLOSURE_ROWS) {
    t[row.key] = 0;
    if (row.barsKey) t[row.barsKey] = [];
  }
  return { ...t, ...overrides };
}

test("a counter that is exactly 0 is hidden - a clean transcription shows no disclosure rows at all", () => {
  expect(disclosureRows(baseTranscription())).toEqual([]);
});

test("every counter the issue lists renders its own row with its label and value when non-zero", () => {
  for (const row of DISCLOSURE_ROWS) {
    const t = baseTranscription({ [row.key]: 3 });
    if (row.barsKey) t[row.barsKey] = [4, 9, 12];
    const rows = disclosureRows(t);
    const found = rows.find((r) => r.key === row.key);
    expect(found, `${row.key} did not produce a row`).toBeTruthy();
    expect(found.label).toBe(row.label);
    expect(found.measured).toBe(true);
    expect(found.value).toBe(3);
    // every OTHER counter is still 0 in this fixture, so this must be the
    // only row - proves the loop isn't leaking some unrelated always-on row
    expect(rows).toHaveLength(1);
  }
});

test("a bar list rides along as the row's detail, and an empty one renders none", () => {
  const withBars = disclosureRows(
    baseTranscription({ repeats_unread: 2, repeats_unread_bars: [5, 8] }),
  );
  expect(withBars).toEqual([
    expect.objectContaining({ key: "repeats_unread", value: 2, bars: [5, 8], barUnit: "bar" }),
  ]);

  // A counter with no bars field at all (e.g. coincident_unsplit_pairs) never
  // claims one.
  const noBars = disclosureRows(baseTranscription({ coincident_unsplit_pairs: 4 }));
  expect(noBars).toEqual([
    expect.objectContaining({ key: "coincident_unsplit_pairs", value: 4, bars: [] }),
  ]);
});

test("systems_unread's list is pages, not bars, and says so", () => {
  const rows = disclosureRows(
    baseTranscription({ systems_unread: 1, systems_unread_pages: [7] }),
  );
  expect(rows).toEqual([
    expect.objectContaining({ key: "systems_unread", barUnit: "page", bars: [7] }),
  ]);
});

test("a null counter beside real measured siblings renders as 'not measured', distinct from zero", () => {
  // Models a row extracted before nav_marks (#134 phase 2) shipped: the
  // phase-1 fields are real numbers (0 counts as a real, honest zero here),
  // but the phase-2 field was never computed for this row at all.
  const t = baseTranscription({ repeats_unread: 3, nav_marks_unresolved: null });
  const rows = disclosureRows(t);
  const navRow = rows.find((r) => r.key === "nav_marks_unresolved");
  expect(navRow).toBeTruthy();
  expect(navRow.measured).toBe(false);
  expect(navRow.value).toBeNull();
  // and not silently rendered as the number 0
  expect(navRow.value).not.toBe(0);

  const repeatsRow = rows.find((r) => r.key === "repeats_unread");
  expect(repeatsRow.measured).toBe(true);
  expect(repeatsRow.value).toBe(3);
});

test("a row with every counter null (a hand edit, or one wholly predating the counters) shows nothing at all", () => {
  // saveEdit() in ScoreCompare.svelte states every one of these fields null
  // on purpose for an edited row - the same state the bar headline and the
  // warnings box already render as nothing for. A wall of seventeen "not
  // measured" lines here would be exactly the noise this rule exists to
  // avoid.
  const allNull = {};
  for (const row of DISCLOSURE_ROWS) allNull[row.key] = null;
  expect(disclosureRows(allNull)).toEqual([]);
});

test("no transcription at all renders no rows", () => {
  expect(disclosureRows(null)).toEqual([]);
  expect(disclosureRows(undefined)).toEqual([]);
});

test("undefined is treated the same as null for both the per-row and whole-row gates", () => {
  const t = baseTranscription({ repeats_unread: 5 });
  delete t.nav_marks_unresolved;
  const navRow = disclosureRows(t).find((r) => r.key === "nav_marks_unresolved");
  expect(navRow.measured).toBe(false);
});
