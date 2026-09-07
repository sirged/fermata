// disclosureRows() called directly - no browser, no backend. disclosures.js
// has no runes and no imports precisely so this can import it, the same
// reason provenance.spec.js gives for provenance.js.
//
// What is NOT tested here is whether any of this reaches a screen - that is
// tests/browser/score-compare-disclosures.spec.js's job, and a suite that
// only checked this selection logic would prove nothing about issue #155,
// which was seventeen fields computed and never displayed.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { DISCLOSURE_ROWS, disclosureRows } from "../../src/lib/disclosures.js";

// The vendored copy of the API's disclosure-counter key list
// (../disclosure-keys.json) - see that file's own comment, and
// server/tests/test_disclosure_keys.py, for the other two links in this
// chain. Read the same way spec-floors.js reads tests/spec-floors/: plain
// fs.readFileSync + JSON.parse, not an import assertion, so this has no
// dependency on the test runner's module loader understanding `type: "json"`.
const VENDORED_KEYS = JSON.parse(
  fs.readFileSync(
    path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "disclosure-keys.json"),
    "utf8",
  ),
);

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

// THE FOURTH MIRROR, GUARDED. server/fermata/tabextract.py's ExtractionResult
// fields, server/fermata/api.py's _BAR_KEYS tuple and api_models.py's
// TranscriptionOut fields are three hand-kept copies of "which fields are
// structural disclosures", and test_transcription_model_stays_in_sync_with_
// api_pys_bar_key_tuples (server/tests/test_api_docs.py) already keeps the
// third honest against the second. DISCLOSURE_ROWS here was a FOURTH copy
// with nothing checking it at all: neither a counter silently dropped from
// it, nor a fake one added on the API side that this file never picked up,
// failed any test that existed before this one - the test above
// ("every counter the issue lists...") iterates DISCLOSURE_ROWS itself, so
// it is tautological against exactly this kind of drift.
//
// disclosure-keys.json is the fix: a small vendored file, kept honest against
// api._BAR_KEYS by server/tests/test_disclosure_keys.py (the only side that
// can make that check, since a browser test cannot import api.py), and
// checked against DISCLOSURE_ROWS here, in BOTH directions, so a config row
// that goes missing OR a vendored key nothing renders each fail by name.
// Per-family gate (issue #294 follow-up): disclosures live in one stored
// `confidence` JSON blob that is never backfilled, so a pre-#294
// transcription carries the five bar counters as `undefined` while its
// structural counters (issue #155's seventeen) are real numbers, or vice
// versa. A single whole-object gate would render either an "all bar rows
// missing" wall beside real structural rows, or the converse - both state
// an absence of measurement as fact for a family that just predates the
// other one. The fix gates each family independently.
const BAR_KEYS = DISCLOSURE_ROWS.filter((row) => row.family === "bar").map((row) => row.key);
const STRUCTURAL_KEYS = DISCLOSURE_ROWS.filter((row) => row.family !== "bar").map((row) => row.key);

test("a legacy blob with bar counters but no structural ones renders exactly the bar rows", () => {
  const t = {};
  for (const key of STRUCTURAL_KEYS) t[key] = null; // never computed on this old blob
  for (const key of BAR_KEYS) t[key] = 2; // measured, and non-zero so it would render
  for (const row of DISCLOSURE_ROWS) {
    if (row.barsKey) t[row.barsKey] = row.family === "bar" ? [1, 2] : [];
  }

  const rows = disclosureRows(t);
  const renderedKeys = rows.map((r) => r.key).sort();
  expect(renderedKeys).toEqual([...BAR_KEYS].sort());
  for (const row of rows) {
    expect(row.measured).toBe(true);
    expect(row.value).toBe(2);
  }
});

test("the converse legacy blob - structural counters measured, bars null - renders exactly the structural rows", () => {
  const t = {};
  for (const key of BAR_KEYS) t[key] = null; // never computed on this old blob
  for (const key of STRUCTURAL_KEYS) t[key] = 2; // measured, and non-zero so it would render
  for (const row of DISCLOSURE_ROWS) {
    if (row.barsKey) t[row.barsKey] = row.family !== "bar" ? [3] : [];
  }

  const rows = disclosureRows(t);
  const renderedKeys = rows.map((r) => r.key).sort();
  expect(renderedKeys).toEqual([...STRUCTURAL_KEYS].sort());
  for (const row of rows) {
    expect(row.measured).toBe(true);
    expect(row.value).toBe(2);
  }
});

test("DISCLOSURE_ROWS carries exactly the vendored disclosure-keys.json key set - no more, no fewer", () => {
  const configKeys = DISCLOSURE_ROWS.map((row) => row.key).sort();
  const vendoredKeys = [...VENDORED_KEYS].sort();
  const missing = vendoredKeys.filter((k) => !configKeys.includes(k));
  const stale = configKeys.filter((k) => !vendoredKeys.includes(k));
  expect(missing, `disclosure-keys.json lists key(s) DISCLOSURE_ROWS has no row for: ${missing}`).toEqual([]);
  expect(stale, `DISCLOSURE_ROWS has row(s) no longer in disclosure-keys.json: ${stale}`).toEqual([]);
  // Belt-and-suspenders against a duplicate key silently masking a missing
  // one in the two filters above (two identical arrays with a duplicate in
  // one would pass both filters and still be wrong).
  expect(configKeys).toEqual(vendoredKeys);
});
