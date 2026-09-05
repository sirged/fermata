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
// IT USED TO BE UNSAFE for zz-library-missing.spec.js's own "a refused scan
// says so" test as well, which is not about which scores exist but about how
// MANY - discovered the hard way, by running the full suite and watching that
// one test go red once this file's own two rows were added on top of
// viewer-practice.spec.js's two. Sorting after it was the fix, and it was a
// workaround: the constraint lived in this file's NAME and in nothing that
// could enforce it.
//
// That is no longer a reason to sort here (#250). That test now counts what is
// already in the library and builds a loss of its own that outweighs it, so it
// is refused whatever anybody left behind - measured by placing a spec before
// it that leaves five files, and again with twenty-five. The reason above, the
// one viewer-practice.spec.js's own header gives, is why this file still sorts
// late; only the high-water one has gone.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { WIDTHS, clippingAudit, tap } from "./responsive-audit.js";

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

// The tempo control: an out-of-range value is refused before it ever reaches
// the network, a fractional one is rounded (setTempo's own numberOrNull),
// and an ordinary value sends exactly one PATCH - see Viewer.svelte's
// setTempo for the client-side bounds this mirrors (api.MIN_TEMPO_BPM /
// MAX_TEMPO_BPM). A number input's min/max attributes are advice a browser
// does not enforce on a typed value - a page listener for uncaught errors is
// what proves the fix actually stops the previously-uncaught ApiError, not
// merely that the box LOOKS right afterwards.
test("typing an out-of-range tempo is refused: the box reverts to the server's value, an error is shown, and nothing reaches the page as an uncaught error", async ({
  page,
}) => {
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto(`/#/score/${scoreA.id}`);
  const tempo = page.locator(".tempo-input");
  await expect(tempo).toHaveValue("");

  let patchSent = false;
  page.on("request", (r) => {
    if (r.url().includes(`/api/scores/${scoreA.id}`) && r.method() === "PATCH") patchSent = true;
  });

  await tempo.fill("500");
  await tempo.dispatchEvent("change");

  // The box is put back to what the server actually holds (still unset, in
  // this test) rather than left showing the rejected 500.
  await expect(tempo).toHaveValue("");
  await expect(page.locator(".tempo-error")).toContainText("must be between 20 and 400");
  expect(patchSent, "an out-of-range value must never reach the network").toBe(false);
  expect(pageErrors, `uncaught page error(s): ${pageErrors.join("; ")}`).toEqual([]);
});

test("a fractional tempo is rounded and saved, in one PATCH", async ({ page }) => {
  await page.goto(`/#/score/${scoreA.id}`);
  const tempo = page.locator(".tempo-input");

  const [request] = await Promise.all([
    page.waitForRequest(
      (r) => r.url().includes(`/api/scores/${scoreA.id}`) && r.method() === "PATCH",
    ),
    (async () => {
      await tempo.fill("76.5");
      await tempo.dispatchEvent("change");
    })(),
  ]);
  expect(request.postDataJSON()).toEqual({ tempo: 77 });
  await expect(tempo).toHaveValue("77");
});

test("an ordinary in-range tempo sends exactly one PATCH", async ({ page }) => {
  await page.goto(`/#/score/${scoreA.id}`);
  const tempo = page.locator(".tempo-input");

  const patches = [];
  page.on("request", (r) => {
    if (r.url().includes(`/api/scores/${scoreA.id}`) && r.method() === "PATCH") {
      patches.push(r.postDataJSON());
    }
  });

  await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes(`/api/scores/${scoreA.id}`) && r.request().method() === "PATCH",
    ),
    (async () => {
      await tempo.fill("120");
      await tempo.dispatchEvent("change");
    })(),
  ]);
  await expect(tempo).toHaveValue("120");
  expect(patches).toEqual([{ tempo: 120 }]);
});

test("a tempo refusal shown for one score does not survive navigating to another (#8 review)", async ({
  page,
}) => {
  // Viewer.svelte:386's own teardown effect already flushes the practice
  // timer and dismisses the session-detail panel on an `id` swap for exactly
  // this reason - the route changes `id` on the SAME component instance
  // rather than remounting it, so nothing tied to the previous score is
  // cleared for free. tempoError is a statement about scoreA's tempo box
  // ("500 was refused"); left standing over scoreB's it describes a refusal
  // that never happened there.
  await page.goto(`/#/score/${scoreA.id}`);
  const tempo = page.locator(".tempo-input");
  await tempo.fill("500");
  await tempo.dispatchEvent("change");
  await expect(page.locator(".tempo-error")).toContainText("must be between 20 and 400");

  // Navigated by changing the hash from inside the page, not with goto() -
  // see viewer-practice.spec.js's identical choice and its own comment: a
  // full document load would unmount the component and clear tempoError
  // along with everything else, whatever Viewer.svelte's own teardown did,
  // which would be a test that passes for the wrong reason.
  await page.evaluate((id) => {
    window.location.hash = `#/score/${id}`;
  }, scoreB.id);
  await expect(page.locator("header .title")).toContainText(SCORE_B_TITLE);
  await expect(page.locator(".tempo-error")).toHaveCount(0);
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

// The score view's own controls row - a second row that stopped fitting for
// the same reason .toolbar (issue #106) and the library's own filter row
// (toolbar-responsive.spec.js) did: three new controls (key, difficulty,
// tempo) added to a row that already held tags, content-kind, the practice
// timer, the history link, favorite and gig-mode, with nothing in it able to
// shrink to fit. Fixed the same way - wrap, not shrink (Viewer.svelte's
// `header` and `.controls` rules) - and checked the same two ways:
// clippingAudit/tap are shared with toolbar-responsive.spec.js via
// ./responsive-audit.js rather than re-derived here.
test.describe("the score view's controls row has zero clipping and the page never overflows horizontally", () => {
  for (const width of WIDTHS) {
    test(`at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/#/score/${scoreA.id}`);
      await page.waitForSelector(".controls");
      const audit = await clippingAudit(page, ".controls");
      expect(audit.rootMissing, ".controls was not found at all").toBeFalsy();
      expect(audit.clipped, `clipped controls: ${JSON.stringify(audit.clipped)}`).toEqual([]);
      expect(audit.pageOverflow, "page grew a horizontal scrollbar").toBe(0);
    });
  }
});

test.describe("every score view control is reachable by an actual click, not merely present", () => {
  for (const width of [768, 430]) {
    test(`at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/#/score/${scoreA.id}`);
      await page.waitForSelector(".controls");

      const keySelect = page.locator("select.key-select");
      await tap(page, keySelect, "key select");
      await keySelect.selectOption("2");
      await expect(keySelect).toHaveValue("2");

      const difficultySelect = page.locator("select.difficulty-select");
      await tap(page, difficultySelect, "difficulty select");
      await difficultySelect.selectOption("4");
      await expect(difficultySelect).toHaveValue("4");

      const tempoInput = page.locator(".tempo-input");
      await tap(page, tempoInput, "tempo input");
      await tempoInput.fill("120");
      await tempoInput.dispatchEvent("change");
      await expect(tempoInput).toHaveValue("120");
    });
  }
});
