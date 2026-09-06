// Issue #267: two pages open on the same score, and the second save silently
// replaced the first.
//
// Measured on main c950501 through the real PUT before any of this was written
// (server/tests/test_transcription_api.py's section header carries the
// numbers): a row saved, loaded by two clients, saved by each in turn - 200 and
// 200, the second replacing the first, and nothing in the application able to
// notice it had happened. All three saves also stored the IDENTICAL updated_at,
// datetime('now') resolving to one second, which is why the stamp had to change
// as well as the check being added.
//
// WHY THIS SPEC RUNS AGAINST THE REAL SERVER rather than stubbing /api the way
// most score-editor*.spec.js files do: the whole subject is a race between two
// clients over one stored row. A stub has no row to race over - it would be
// asserting that the client sends a field, which is a unit test wearing a
// browser. Here both pages talk to the same real database, the refusal is the
// server's own 409, and "the row holds A's edit" is read back out of the API.
//
// Two pages in ONE browser context, not two contexts: this is one person with
// the score open on two things, and nothing here depends on separate cookies or
// storage (Fermata has no login). Two contexts would be the same test, slower.
//
// What each assertion would catch, so this file is falsifiable rather than
// merely green:
//   - the server not comparing at all (the bug): the B-saves test's conflict
//     assertions go red, and the row holds B's fret instead of A's.
//   - the client not sending the precondition: same test, same way.
//   - the client not ADOPTING the new value from the save response: the
//     saves-twice-from-one-page test goes red on its second save, which is
//     refused by the page's own previous save.
//   - the sub-second stamp being a no-op: the two saves in the B-saves test
//     land inside one second, so both stamps read the same and B's stale value
//     matches - the conflict assertions go red.
// All were run as real mutations - see the pull request.
import crypto from "node:crypto";

import { expect, test } from "@playwright/test";

import { EDITOR_MUSICXML } from "./fixtures/editor-score.js";

const FOLDER = "Uploads";
const wrap = (page) => page.locator(".wrap").first();
const host = (page) => page.locator(".at-host");
const fretInput = (page) => page.locator(".edit-fields input");
const conflict = (page) => page.locator(".save-conflict");

// Fresh name per upload: the suite shares one server and one library for the
// whole run, and Playwright starts a fresh worker after a failure - so a
// per-module counter can repeat a name and open a score that is not the one
// this test uploaded. Same reasoning as score-editor-native-262.spec.js.
function uniqueName() {
  return `conflict-editor-${crypto.randomUUID()}.musicxml`;
}

async function waitForScore(request, name) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(name));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared in the library`);
}

/** A real score in the library that ALREADY has a hand edit stored.
 *
 * The stored edit matters: a page that opens a score with no edited row holds
 * no stamp to echo, so its first save is unconditional and proves nothing about
 * the precondition. Every test here starts from a row two pages can both load.
 */
async function uploadEditedScore(request) {
  const name = uniqueName();
  const res = await request.post(`/api/upload?folder=${FOLDER}`, {
    multipart: {
      file: {
        name,
        mimeType: "application/octet-stream",
        buffer: Buffer.from(EDITOR_MUSICXML, "utf-8"),
      },
    },
  });
  expect(res.ok(), await res.text()).toBe(true);
  const score = await waitForScore(request, name);
  expect(score.file_type).toBe("musicxml");

  const saved = await request.put(`/api/scores/${score.id}/transcription`, {
    data: { content: EDITOR_MUSICXML },
  });
  expect(saved.ok(), await saved.text()).toBe(true);
  const row = await saved.json();
  expect(row.source).toBe("edited");
  return { score, row };
}

async function storedRow(request, id) {
  const res = await request.get(`/api/scores/${id}/transcription`);
  expect(res.ok(), await res.text()).toBe(true);
  return res.json();
}

// Same two-step cleanup score-editor-native-262.spec.js makes, for the same
// reason: this file puts real scores in the shared throwaway library, and a
// DELETE only moves a score to the trash, so the trash has to be emptied too or
// later specs' "this is a throwaway instance" checks see rows they never made.
test.afterEach(async ({ request }) => {
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
});

async function openEditor(page, id) {
  await page.goto(`/#/score/${id}`);
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
  await expect(page.locator(".staff-source")).toHaveAttribute("data-staff-source", "edited");
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
}

async function setFret(page, ordinal, fret) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
  await fretInput(page).fill(String(fret));
  await fretInput(page).blur();
  await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", String(fret));
}

const save = (page) => page.getByRole("button", { name: "Save", exact: true }).click();

test.describe("two pages editing one score (#267)", () => {
  test("the second page is told the score changed, and the first page's edit stands", async ({
    page,
    context,
    request,
  }) => {
    const { score } = await uploadEditedScore(request);
    const pageB = await context.newPage();
    // BOTH pages load the row BEFORE either saves - which is the whole
    // situation, and the reason B's value is stale rather than merely old.
    await openEditor(page, score.id);
    await openEditor(pageB, score.id);

    await setFret(page, 0, 7);
    await save(page);
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-conflict", /.+/);

    await setFret(pageB, 0, 9);
    await save(pageB);

    // B is TOLD, in words, on the panel it pressed Save on.
    await expect(wrap(pageB)).toHaveAttribute("data-editor-conflict", "edited");
    await expect(conflict(pageB)).toContainText("changed somewhere else");
    await expect(conflict(pageB)).toContainText("nothing was saved");
    // And is offered something to do about it, rather than a dead end.
    await expect(
      pageB.getByRole("button", { name: "Load what is stored" }),
    ).toBeVisible();
    await expect(pageB.getByRole("button", { name: "Save mine over it" })).toBeVisible();
    // The work B did is still in front of B: the editor is open, the note still
    // reads 9, and nothing was thrown away by the refusal.
    await expect(wrap(pageB)).toHaveAttribute("data-editor-active", "true");
    await expect(wrap(pageB)).toHaveAttribute("data-editor-selected-fret", "9");

    // THE ROW HOLDS A'S EDIT. Read from the server, not from either page.
    const stored = await storedRow(request, score.id);
    expect(stored.content).toContain("<fret>7</fret>");
    expect(stored.content).not.toContain("<fret>9</fret>");

    // And A, who did nothing wrong, can go on saving on top of its own row -
    // the refusal must not have touched updated_at either.
    await setFret(page, 1, 4);
    await save(page);
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-conflict", /.+/);
    await pageB.close();
  });

  test("one page can save over and over", async ({ page, request }) => {
    const { score } = await uploadEditedScore(request);
    await openEditor(page, score.id);

    // Three saves in a row from the same page, each on top of the last. The
    // page loaded a stamp before the first of them, so every save here carries
    // a precondition - and each has to carry the value its OWN previous save
    // returned, not the one the page loaded. A page that echoed the loaded
    // value would be refused here by its own first save.
    for (const [ordinal, fret] of [[0, 3], [1, 5], [2, 7]]) {
      await setFret(page, ordinal, fret);
      await save(page);
      await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
      await expect(wrap(page)).not.toHaveAttribute("data-editor-conflict", /.+/);
    }

    const stored = await storedRow(request, score.id);
    expect(stored.content).toContain("<fret>3</fret>");
    expect(stored.content).toContain("<fret>5</fret>");
    expect(stored.content).toContain("<fret>7</fret>");
  });

  test("loading what is stored replaces the working document with it", async ({
    page,
    context,
    request,
  }) => {
    const { score } = await uploadEditedScore(request);
    const pageB = await context.newPage();
    await openEditor(page, score.id);
    await openEditor(pageB, score.id);

    await setFret(page, 0, 7);
    await save(page);
    await setFret(pageB, 0, 9);
    await save(pageB);
    await expect(wrap(pageB)).toHaveAttribute("data-editor-conflict", "edited");

    await pageB.getByRole("button", { name: "Load what is stored" }).click();
    // The message goes, and B is now looking at A's version - fret 7 on the
    // note B had set to 9. B's own change is gone, which is what the button
    // says happens.
    await expect(wrap(pageB)).not.toHaveAttribute("data-editor-conflict", /.+/, {
      timeout: 20_000,
    });
    await expect(wrap(pageB)).toHaveAttribute("data-editor-active", "true");
    const point = await pageB.evaluate(() => window.__scoreEditor.headPoint(0));
    await pageB.mouse.click(point.x, point.y);
    await expect(wrap(pageB)).toHaveAttribute("data-editor-selected-fret", "7");
    await expect(wrap(pageB)).toHaveAttribute("data-editor-divergence-ok", "true");

    // And B can now save again on top of the version it just loaded. Ordinal 1
    // is fret 2 in the fixture, so 4 is a real change - setting it to the fret
    // it already has would leave the document clean and the Save button off.
    await setFret(pageB, 1, 4);
    await save(pageB);
    await expect(wrap(pageB)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(pageB)).not.toHaveAttribute("data-editor-conflict", /.+/);
    const stored = await storedRow(request, score.id);
    expect(stored.content).toContain("<fret>7</fret>");
    expect(stored.content).toContain("<fret>4</fret>");
    await pageB.close();
  });

  test("saving mine over it replaces the other version, deliberately", async ({
    page,
    context,
    request,
  }) => {
    const { score } = await uploadEditedScore(request);
    const pageB = await context.newPage();
    await openEditor(page, score.id);
    await openEditor(pageB, score.id);

    await setFret(page, 0, 7);
    await save(page);
    await setFret(pageB, 0, 9);
    await save(pageB);
    await expect(wrap(pageB)).toHaveAttribute("data-editor-conflict", "edited");

    await pageB.getByRole("button", { name: "Save mine over it" }).click();
    await expect(wrap(pageB)).not.toHaveAttribute("data-editor-conflict", /.+/, {
      timeout: 20_000,
    });
    await expect(wrap(pageB)).toHaveAttribute("data-editor-dirty", "false");

    // B's version is what is stored now. This is the same outcome the bug
    // produced - and the entire difference is that somebody was asked first.
    const stored = await storedRow(request, score.id);
    expect(stored.content).toContain("<fret>9</fret>");
    expect(stored.content).not.toContain("<fret>7</fret>");

    // B holds the new stamp too, so its next save is not refused by its own.
    await setFret(pageB, 1, 4);
    await save(pageB);
    await expect(wrap(pageB)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(pageB)).not.toHaveAttribute("data-editor-conflict", /.+/);
    await pageB.close();
  });
});
