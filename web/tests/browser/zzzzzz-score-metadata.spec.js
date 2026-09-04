// Key, tempo and difficulty as queryable score columns (issue #8) - the UI
// seam server tests cannot reach: that a difficulty set from the score view
// actually persists through a real PATCH and survives a reload, and that the
// library's own filter controls narrow a real grid rather than merely
// existing on the page.
//
// WHY THESE TWO PAGES TOGETHER. Setting a value happens on the score view
// (Viewer.svelte); filtering happens on the library grid (Library.svelte).
// Neither page alone proves the round trip #8's Done-when asks for - a
// select that calls the right PATCH is not the same claim as a filter that
// reads back what that PATCH wrote, and a server test proves the PATCH and
// the filter query in isolation but never that the SAME select element on
// the SAME page a person actually uses drives either one.
//
// WHY IT IS NAMED TO SORT LAST, AFTER EVEN zzzzz-setlists.spec.js. Like
// viewer-practice.spec.js, this one puts two files in the throwaway library
// and leaves their rows behind rather than deleting them afterwards (issue
// #95 - a deleted file's row stays, marked, and cleaning up by deleting
// through the API would exercise a different code path than what this file
// is actually testing). Every ordinary spec's own refusal guard (see
// zz-library-missing.spec.js's comment on its own OWN/Uploads check)
// tolerates anything already sitting under Uploads/, so leaving these rows
// behind is safe for every spec that checks WHICH scores are present.
//
// It is not safe for zz-library-missing.spec.js's own "a refused scan says
// so" test, which is not about which scores exist but about how MANY: a
// scan refuses to reconcile when it can account for half or fewer of the
// library's high-water mark (scanner.py's LOSS_FRACTION), and that mark is a
// persisted, monotonically-increasing count for the whole run - discovered
// the hard way, by running the full suite and watching that one test go red
// once this file's own two rows were added on top of viewer-practice.spec.js's
// two. Sorting after every spec that depends on that mark's exact size is
// what keeps this file's own two permanent rows from ever being counted
// against it - a second reason "leaves rows behind" specs sort late, beside
// the OWN_PATHS-refusal reason viewer-practice.spec.js's own header gives.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "notation-only.musicxml");

const SCORE_A_NAME = "score-metadata-fixture-a.musicxml";
const SCORE_B_NAME = "score-metadata-fixture-b.musicxml";
const SCORE_A_TITLE = "Score Metadata Fixture A";
const SCORE_B_TITLE = "Score Metadata Fixture B";

async function waitForScore(request, name) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(name));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared in the library`);
}

async function uploadNamed(request, name, title) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: {
      file: { name, mimeType: "application/xml", buffer: fs.readFileSync(FIXTURE) },
    },
  });
  expect(res.ok(), await res.text()).toBe(true);
  const found = await waitForScore(request, name);
  const patched = await request.patch(`/api/scores/${found.id}`, { data: { title } });
  expect(patched.ok(), await patched.text()).toBe(true);
  return (await patched.json());
}

let scoreA;
let scoreB;

test.beforeEach(async ({ request }) => {
  // The same tolerant refusal every spec that reuses Uploads/ rows makes -
  // see this file's own header. A score already marked missing, or sitting
  // anywhere under Uploads/, is some earlier spec's own fixture and not
  // evidence this is a real library.
  const existing = await (await request.get("/api/scores")).json();
  const foreign = existing.filter((s) => !s.missing_since && !s.path.startsWith("Uploads/"));
  expect(
    foreign,
    "refusing to run: this backend has scores in its library that no spec here put " +
      "there, so it is not the throwaway instance the suite creates",
  ).toEqual([]);

  scoreA = await uploadNamed(request, SCORE_A_NAME, SCORE_A_TITLE);
  scoreB = await uploadNamed(request, SCORE_B_NAME, SCORE_B_TITLE);
  // Cleared on the way IN, not out (same reasoning as viewer-practice.spec.js
  // and zz-library-missing.spec.js): every assertion below is about exactly
  // these two rows' key/tempo/difficulty, and an earlier run's values on the
  // SAME reused rows would otherwise leak into this one.
  await request.patch(`/api/scores/${scoreA.id}`, { data: { key: null, tempo: null, difficulty: null } });
  await request.patch(`/api/scores/${scoreB.id}`, { data: { key: null, tempo: null, difficulty: null } });
});

test("setting a difficulty from the score view writes it, and it survives a reload", async ({
  page,
}) => {
  await page.goto(`/#/score/${scoreA.id}`);
  const select = page.locator("select.difficulty-select");
  await expect(select).toBeVisible();
  await expect(select).toHaveValue("");

  // Waited for explicitly, not merely awaited after selectOption(): the DOM
  // already shows "4" the instant the browser applies the selection, well
  // before api.patch's fetch resolves - so a bare toHaveValue() assertion
  // here proves nothing about the network round trip, and reloading before
  // that fetch lands would cancel it out from under the test, leaving the
  // reload assertion below checking a PATCH that never actually happened.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/scores/${scoreA.id}`) && r.request().method() === "PATCH",
    ),
    select.selectOption("4"),
  ]);
  await expect(select).toHaveValue("4");

  await page.reload();
  await expect(page.locator("select.difficulty-select")).toHaveValue("4");
});

test("clearing a difficulty from the score view writes null, not zero or the empty string", async ({
  page,
  request,
}) => {
  await request.patch(`/api/scores/${scoreA.id}`, { data: { difficulty: 3 } });
  await page.goto(`/#/score/${scoreA.id}`);
  const select = page.locator("select.difficulty-select");
  await expect(select).toHaveValue("3");

  // Waited for explicitly - see the previous test's comment on why a bare
  // DOM assertion after selectOption() cannot stand in for the network round
  // trip this test is actually about.
  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/scores/${scoreA.id}`) && r.request().method() === "PATCH",
    ),
    select.selectOption(""),
  ]);
  await expect(select).toHaveValue("");

  const stored = await (await request.get(`/api/scores/${scoreA.id}`)).json();
  expect(stored.difficulty).toBeNull();
});

test("the difficulty filter on the library grid narrows to an exact match", async ({
  page,
  request,
}) => {
  await request.patch(`/api/scores/${scoreA.id}`, { data: { difficulty: 5 } });
  await request.patch(`/api/scores/${scoreB.id}`, { data: { difficulty: 2 } });

  await page.goto("/#/");
  await expect(page.locator(".card", { hasText: SCORE_A_TITLE })).toBeVisible();
  await expect(page.locator(".card", { hasText: SCORE_B_TITLE })).toBeVisible();

  await page.locator("select.difficulty-filter").selectOption("5");
  await expect(page.locator(".card", { hasText: SCORE_A_TITLE })).toBeVisible();
  await expect(page.locator(".card", { hasText: SCORE_B_TITLE })).toHaveCount(0);

  await page.locator("select.difficulty-filter").selectOption("");
  await expect(page.locator(".card", { hasText: SCORE_B_TITLE })).toBeVisible();
});

test("the key filter on the library grid narrows to an exact match, including 0 fifths", async ({
  page,
  request,
}) => {
  // 0 is the common "no sharps or flats" answer and the falsy-value trap
  // this filter's own JS state has to not fall into (see Library.svelte's
  // comment on why `key`/`difficulty` travel as strings).
  await request.patch(`/api/scores/${scoreA.id}`, { data: { key: 0 } });
  await request.patch(`/api/scores/${scoreB.id}`, { data: { key: 3 } });

  await page.goto("/#/");
  await page.locator("select.key-filter").selectOption("0");
  await expect(page.locator(".card", { hasText: SCORE_A_TITLE })).toBeVisible();
  await expect(page.locator(".card", { hasText: SCORE_B_TITLE })).toHaveCount(0);
});
