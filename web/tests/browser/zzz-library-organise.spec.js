// What a person can actually DO to their library from the library view, and
// what they are shown while they do it (issue #56).
//
// WHY THIS IS A BROWSER TEST AND NOT ONLY A SERVER ONE. server/tests/
// test_library_management_api.py proves the endpoints keep their promises. It
// cannot prove any of those promises reached the screen - and #95's lesson,
// recorded in zz-library-missing.spec.js next door, is that a guarantee
// nothing renders is a guarantee nobody has. The specific things asserted
// here are the ones that would otherwise be invisible:
//
//   1. the preview really is shown before a move happens, and the move that
//      happens is the one the preview described;
//   2. a folder can be made and renamed from the same dialog, and the scores
//      follow it;
//   3. deleting takes a score out of the grid and puts it in the trash, with
//      its practice history still on it, and putting it back is one press;
//   4. destroying takes TWO presses and says what it destroys;
//   5. a batch move previews as a batch, and a collision is shown as a
//      blocked line rather than silently overwriting one of the two files.
//
// WHY IT IS NAMED TO SORT LAST, AFTER zz-library-missing. It moves scores out
// of Uploads/ and leaves deleted rows behind, and every other spec in this
// suite - including zz-library-missing's own beforeEach - refuses to run
// against a backend holding scores it did not put there. Sorting after all of
// them is what keeps that true for them.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { localDay } from "../../src/lib/practice.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "notation-only.musicxml");
// The browser's own date, the same way Viewer.svelte stamps a practice
// session (Viewer.svelte:558) - not the server's UTC date, which stamped
// these sessions one day into the future on a machine west of Greenwich
// after 18:00 local, dropping them off the practice page's local-day window.
const today = localDay();

const libraryDir = () => {
  const dir = process.env.FERMATA_TEST_LIBRARY_DIR;
  if (!dir) throw new Error("FERMATA_TEST_LIBRARY_DIR is not set - see playwright.config.js");
  return dir;
};

const SCAN_DEADLINE_MS = 30_000;

/** Wait until no scan is running, then hand back the idle status. */
async function scanSettled(request) {
  const deadline = Date.now() + SCAN_DEADLINE_MS;
  for (;;) {
    const status = await (await request.get("/api/scan/status")).json();
    if (!status.scanning) return status;
    if (Date.now() > deadline) throw new Error("a scan never finished");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/**
 * Upload a file and wait for its score row, then wait out the scan the upload
 * started.
 *
 * BOTH WAITS MATTER, and the second one is this feature's own version of the
 * #110 lesson zz-library-missing.spec.js records. Every upload starts a scan,
 * and a move or a delete is REFUSED while a scan is running on purpose (see
 * scanner.hold_library_still) - so a test that clicked Move the instant the
 * row appeared would sometimes get the refusal instead of the move, and would
 * read as a bug in the feature rather than as a test racing the scan it
 * started. The content is made distinct per name so the scanner's content-hash
 * relink does not correctly treat the second upload as a rename of the first.
 */
async function upload(request, name, folder = "Uploads") {
  const body = Buffer.concat([fs.readFileSync(FIXTURE), Buffer.from(`<!-- ${name} -->\n`)]);
  const res = await request.post(`/api/upload?folder=${encodeURIComponent(folder)}`, {
    multipart: { file: { name, mimeType: "application/xml", buffer: body } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  let found;
  await expect(async () => {
    const scores = await (await request.get("/api/scores")).json();
    found = scores.find((s) => s.path === `${folder}/${name}`);
    expect(found, `${name} never appeared in the library`).toBeTruthy();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  await scanSettled(request);
  return found;
}

/** Everything this suite has ever put in this throwaway library, destroyed. */
async function emptyTheLibrary(request) {
  await scanSettled(request);
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

// Every folder this suite ever puts a score in. Anything else present means
// this is not the throwaway instance, and emptyTheLibrary below must not run.
const SUITE_FOLDERS = new Set([
  "Uploads", // zz-library-missing.spec.js and this file's own default
  "Bach",
  "Chopin",
  "Patreon",
  "Recital",
  "Favourites",
  "Arrangements",
]);

test.beforeEach(async ({ request }) => {
  // The strictest refusal in this suite, and the reason is the strongest: the
  // helper below DESTROYS every score it can see. Nothing but a folder this
  // suite creates may be present. Rows already marked missing are inert
  // leftovers from zz-library-missing.spec.js, which runs before this one.
  const existing = await (await request.get("/api/scores")).json();
  const foreign = existing.filter(
    (s) => !s.missing_since && !SUITE_FOLDERS.has(s.path.split("/")[0]),
  );
  expect(
    foreign,
    "refusing to run: this backend has scores in folders the suite never creates, so it is " +
      "not the throwaway instance the suite creates - and this file empties the library",
  ).toEqual([]);
  await emptyTheLibrary(request);
});

test.afterAll(async ({ request }) => {
  await emptyTheLibrary(request);
});

async function organise(page) {
  await page.goto("/#/");
  await page.locator(".organise-toggle").click();
  await expect(page.locator(".organise-bar")).toBeVisible();
}

async function choose(page, score) {
  const card = page.locator(`.card[href="#/score/${score.id}"]`);
  await expect(card).toBeVisible();
  await card.click();
  await expect(card).toHaveClass(/selected/);
}

async function unchoose(page, score) {
  const card = page.locator(`.card[href="#/score/${score.id}"]`);
  await card.click();
  await expect(card).not.toHaveClass(/selected/);
}

test("a move is previewed before it happens, and the move that happens is the one shown", async ({
  page,
  request,
}) => {
  const score = await upload(request, "organise-move.musicxml");
  // Practice on it FIRST, so what survives the move is something a person put
  // there rather than an empty row.
  const logged = await request.post(`/api/scores/${score.id}/practice`, {
    data: { seconds: 1800, note: "before the move", local_date: today },
  });
  expect(logged.ok(), await logged.text()).toBe(true);

  await organise(page);
  await choose(page, score);
  await page.locator(".move-open").click();

  const dialog = page.locator(".dialog.move");
  await expect(dialog).toBeVisible();
  // A folder that does not exist yet, made from inside the dialog - the whole
  // point of being able to create one is filing something into it now.
  await dialog.locator(".new-folder-input").fill("Recital");
  await dialog.locator(".new-folder-create").click();

  // The preview: the server's own dry run, line for line. Asserted BEFORE the
  // apply, so what follows is known to be what was shown rather than a
  // coincidence.
  const line = dialog.locator(".plan-line");
  await expect(line).toHaveCount(1);
  await expect(line).toContainText("Uploads/organise-move.musicxml");
  await expect(line).toContainText("Recital/organise-move.musicxml");
  // ...and nothing has moved yet.
  expect(fs.existsSync(path.join(libraryDir(), "Uploads", "organise-move.musicxml"))).toBe(
    true,
  );

  await dialog.locator(".move-apply").click();
  await expect(page.locator(".dialog.move")).toHaveCount(0);
  await expect(page.locator(".notice")).toContainText("Recital");

  // The file really moved, and the SAME score followed it with its practice.
  await expect(async () => {
    const after = await (await request.get(`/api/scores/${score.id}`)).json();
    expect(after.path).toBe("Recital/organise-move.musicxml");
    expect(after.practice_seconds).toBe(1800);
    expect(after.missing_since).toBeNull();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  expect(fs.existsSync(path.join(libraryDir(), "Recital", "organise-move.musicxml"))).toBe(
    true,
  );
  expect(fs.existsSync(path.join(libraryDir(), "Uploads", "organise-move.musicxml"))).toBe(
    false,
  );
  // And it is still in the library, under its new collection. RELOADED rather
  // than navigated to: the page is already at "/#/", and a goto to the URL the
  // browser is already on is a no-op, so the view under test would not change.
  await page.reload();
  await expect(page.locator(`.card[href="#/score/${score.id}"]`)).toBeVisible();
  await expect(page.locator(".side-item", { hasText: "Recital" }).first()).toBeVisible();
});

test("a folder can be renamed from the same dialog, and its scores go with it", async ({
  page,
  request,
}) => {
  const score = await upload(request, "organise-rename.musicxml", "Patreon");

  await organise(page);
  await choose(page, score);
  await page.locator(".move-open").click();

  const dialog = page.locator(".dialog.move");
  await dialog.locator(".folder-option", { hasText: "Patreon" }).click();
  await dialog.locator(".folder-rename-open").click();
  await dialog.locator(".folder-rename-input").fill("Arrangements");
  await dialog.locator(".folder-rename-preview").click();
  // Shown before it happens, like every other bulk change here.
  await expect(dialog.locator(".rename-preview")).toContainText("1 score would move with it");
  expect(fs.existsSync(path.join(libraryDir(), "Patreon"))).toBe(true);

  await dialog.locator(".folder-rename-apply").click();
  await expect(page.locator(".dialog.move")).toHaveCount(0);
  await expect(page.locator(".notice")).toContainText("Arrangements");

  await expect(async () => {
    const after = await (await request.get(`/api/scores/${score.id}`)).json();
    expect(after.path).toBe("Arrangements/organise-rename.musicxml");
    expect(after.collection).toBe("Arrangements");
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  expect(fs.existsSync(path.join(libraryDir(), "Patreon"))).toBe(false);
});

test("deleting a score takes it to the trash with its history, and one press brings it back", async ({
  page,
  request,
}) => {
  const score = await upload(request, "organise-delete.musicxml");
  await request.post(`/api/scores/${score.id}/practice`, {
    data: { seconds: 3600, note: "hours on this", local_date: today },
  });
  // ScoreDeleteOut's tags_kept and goals_kept were never named in the receipt
  // (issue #284) - seeded here so deleting actually carries a nonzero count
  // of each, not only the sessions and transcriptions the receipt already
  // stated. A period far from "this week" so it cannot collide with a goal
  // any other spec in this shared library has set for the current one.
  await request.patch(`/api/scores/${score.id}`, { data: { tags: ["organise-delete-tag"] } });
  const goal = await (
    await request.post("/api/practice/goals", {
      data: { period_start: "2019-03-04", target_days: 1, scope: "score", score_id: score.id },
    })
  ).json();

  await organise(page);
  await choose(page, score);
  await page.locator(".delete-open").click();

  const dialog = page.locator(".dialog.delete");
  // What it keeps, said BEFORE the button that does it.
  await expect(dialog).toContainText("trash folder inside your library");
  await expect(dialog).toContainText("practice history");
  await dialog.locator(".delete-apply").click();

  // Gone from the grid, and the receipt says what is still attached - every
  // fact ScoreDeleteOut carries about what was kept and moved, not only the
  // two this receipt named before.
  await expect(page.locator(`.card[href="#/score/${score.id}"]`)).toHaveCount(0);
  await expect(page.locator(".notice")).toContainText("every file moved to Trash");
  await expect(page.locator(".notice")).toContainText("1 practice session");
  await expect(page.locator(".notice")).toContainText("1 tag");
  await expect(page.locator(".notice")).toContainText("1 goal");
  await expect(page.locator(".notice")).toContainText("nothing was destroyed");

  // In the trash, saying where it came from.
  await page.locator(".trash-link").click();
  const row = page.locator(".trash-row");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("Uploads/organise-delete.musicxml");
  await expect(row).toContainText("60 min practised");

  await row.locator(".trash-restore").click();
  await expect(page.locator(".notice")).toContainText("is back at");
  await expect(page.locator(".trash-row")).toHaveCount(0);

  await expect(async () => {
    const after = await (await request.get(`/api/scores/${score.id}`)).json();
    expect(after.path).toBe("Uploads/organise-delete.musicxml");
    expect(after.deleted_at).toBeNull();
    expect(after.practice_seconds).toBe(3600);
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  // Reloaded, not navigated: this page is already at "/#/" and showing the
  // trash, and a goto to the URL the browser is already on would leave it
  // there - which is exactly what this assertion must not be reading.
  await page.reload();
  await expect(page.locator(`.card[href="#/score/${score.id}"]`)).toBeVisible();
  await request.delete(`/api/practice/goals/${goal.id}`);
});

test("deleting a score whose file was already missing needs no file moved", async ({
  page,
  request,
}) => {
  // The other end of ScoreDeleteOut's `file_moved` fold (issue #284): a score
  // that was already flagged missing before it was deleted has nothing on
  // disk to move to Trash, and the receipt says so rather than the "every
  // file moved" wording the test above expects. A second file is left in
  // place so the scan that marks the first one missing sees a library that
  // still has SOME readable files - scanner._implausible refuses a pass that
  // finds none at all, on purpose, regardless of how small the library is.
  const score = await upload(request, "organise-delete-missing.musicxml");
  await upload(request, "organise-delete-keepalive.musicxml");
  fs.unlinkSync(path.join(libraryDir(), "Uploads", "organise-delete-missing.musicxml"));
  await (await request.post("/api/scan")).json();
  await scanSettled(request);
  await expect(async () => {
    const after = await (await request.get(`/api/scores/${score.id}`)).json();
    expect(after.missing_since, "the scan should have flagged it missing").not.toBeNull();
  }).toPass({ timeout: SCAN_DEADLINE_MS });

  await page.goto("/#/");
  await organise(page);
  await choose(page, score);
  await page.locator(".delete-open").click();
  await page.locator(".dialog.delete .delete-apply").click();

  await expect(page.locator(".notice")).toContainText("no file needed moving");
});

test("restoring a score whose file already left the trash still comes back, flagged missing", async ({
  page,
  request,
}) => {
  // ScoreRestoreOut's `file_restored` was never read (issue #284): the trash
  // folder is a real folder a person may empty by hand (docs/deployment.md),
  // and until now restoring after that said exactly the same "is back at"
  // words as an ordinary restore, with nothing on screen distinguishing the
  // row that actually needs a file put back from the one that does not.
  const score = await upload(request, "organise-restore-nofile.musicxml");
  const deleted = await request.delete(`/api/scores/${score.id}`);
  expect(deleted.ok(), await deleted.text()).toBe(true);
  const trashed = await deleted.json();
  fs.unlinkSync(path.join(libraryDir(), trashed.trashed_to));

  await page.goto("/#/");
  await page.locator(".trash-link").click();
  const row = page.locator(".trash-row");
  await expect(row).toHaveCount(1);
  await row.locator(".trash-restore").click();

  await expect(page.locator(".notice")).toContainText("there was no file in Trash to restore");
  await expect(page.locator(".notice")).toContainText("show as missing");
  await expect(async () => {
    const after = await (await request.get(`/api/scores/${score.id}`)).json();
    expect(after.deleted_at).toBeNull();
    expect(after.missing_since, "no file came back, so it must read as missing").not.toBeNull();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
});

test("destroying a score takes two presses and says what it destroys", async ({
  page,
  request,
}) => {
  const score = await upload(request, "organise-destroy.musicxml");
  const logged = await (
    await request.post(`/api/scores/${score.id}/practice`, {
      data: { seconds: 600, local_date: today },
    })
  ).json();
  // ScorePurgeOut's goals_kept and file_deleted were never named in the
  // "gone for good" receipt (issue #284) - a goal here makes the first
  // nonzero, and this score's real file on disk (see upload()) makes the
  // second true rather than the "no file on disk" case.
  const goal = await (
    await request.post("/api/practice/goals", {
      data: { period_start: "2019-03-11", target_days: 1, scope: "score", score_id: score.id },
    })
  ).json();
  const deleted = await request.delete(`/api/scores/${score.id}`);
  expect(deleted.ok(), await deleted.text()).toBe(true);

  await page.goto("/#/");
  await page.locator(".trash-link").click();
  const row = page.locator(".trash-row");
  await expect(row).toHaveCount(1);

  // The first press only reveals the second one. Nothing is destroyed by it,
  // which is the point of there being two.
  await row.locator(".trash-destroy").click();
  const confirm = row.locator(".trash-destroy-confirm");
  await expect(confirm).toContainText("your practice history stays");
  expect((await (await request.get("/api/trash")).json()).length).toBe(1);

  await confirm.click();
  await expect(page.locator(".trash-row")).toHaveCount(0);
  await expect(page.locator(".notice")).toContainText("gone for good");
  await expect(page.locator(".notice")).toContainText("its file is deleted too");
  await expect(page.locator(".notice")).toContainText("1 goal");
  await expect(page.locator(".notice")).toContainText("stayed in your history");

  await expect(async () => {
    const gone = await request.get(`/api/scores/${score.id}`);
    expect(gone.status()).toBe(404);
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  // The hours were still spent. THAT session - by its own id, not a matching
  // one - is still in the history, now with no piece named.
  const sessions = await (await request.get("/api/practice/sessions")).json();
  const survivor = sessions.sessions.find((s) => s.id === logged.session.id);
  expect(survivor, "the practice session went with the score").toBeTruthy();
  expect(survivor.seconds).toBe(600);
  expect(survivor.score_id).toBeNull();
  await request.delete(`/api/practice/goals/${goal.id}`);
});

test("practice on a deleted score still counts, and stops being a way into it", async ({
  page,
  request,
}) => {
  // Issue #56, adversarial review F6. The hours were spent, so the piece stays
  // in the by-piece breakdown and its totals still add up - dropping it would
  // leave that column not adding up to the total beside it. What it must stop
  // being is a LINK, into a library that no longer holds the score.
  const score = await upload(request, "organise-history.musicxml");
  const logged = await request.post(`/api/scores/${score.id}/practice`, {
    data: { seconds: 2700, note: "before it went", local_date: today },
  });
  expect(logged.ok(), await logged.text()).toBe(true);

  // While it is in the library, BOTH lists that name it are ordinary links.
  await page.goto("/#/practice");
  const row = page.locator(".by-score li", { hasText: score.title });
  await expect(row.locator("a")).toHaveAttribute("href", `#/score/${score.id}`);
  const session = page.locator(".session-list li", { hasText: score.title });
  await expect(session.locator("a")).toHaveAttribute("href", `#/score/${score.id}`);

  const deleted = await request.delete(`/api/scores/${score.id}`);
  expect(deleted.ok(), await deleted.text()).toBe(true);

  await page.reload();
  const after = page.locator(".by-score li", { hasText: score.title });
  await expect(after).toBeVisible();
  await expect(after.locator(".deleted-mark")).toHaveText("deleted");
  // Still counted - the 45 minutes are still on the page...
  await expect(after).toContainText("45m");
  // ...and no longer a route into a score that is not in the library.
  await expect(after.locator("a")).toHaveCount(0);

  // AND THE SESSION LIST FURTHER DOWN THE SAME PAGE. Fixing the by-piece
  // breakdown alone left this one still linking into the trash, two lists
  // apart on one screen - which is why this asserts both rather than trusting
  // that one flag reached every list that reads it.
  const sessionAfter = page.locator(".session-list li", { hasText: score.title });
  await expect(sessionAfter).toBeVisible();
  await expect(sessionAfter.locator(".deleted-mark")).toHaveText("deleted");
  await expect(sessionAfter).toContainText("45m");
  await expect(sessionAfter.locator("a")).toHaveCount(0);
});

test("uploading the same name twice is refused until Replace is pressed, and the file is untouched until then", async ({
  page,
  request,
}) => {
  // Issue #293: the upload route used to overwrite a same-named file
  // silently, with no receipt of any kind (Library.svelte's onUpload
  // discarded the response). This drives the real file input, the way a
  // person actually uploads, rather than posting to /api/upload directly.
  const name = "organise-upload-conflict.musicxml";
  const original = Buffer.concat([fs.readFileSync(FIXTURE), Buffer.from(`<!-- ${name} original -->\n`)]);
  const replacement = Buffer.concat([
    fs.readFileSync(FIXTURE),
    Buffer.from(`<!-- ${name} replacement -->\n`),
  ]);

  await page.goto("/#/");
  const input = page.locator('input[type="file"]');

  // ".notice:not(.upload-conflict)" throughout, not plain ".notice" - the
  // conflict paragraph below carries "notice" too (issue #293's own upload
  // receipt uses the same styling as every other receipt on this page), and
  // once both are on screen at once a plain ".notice" is not one element.
  const receipt = page.locator(".notice:not(.upload-conflict)");
  await input.setInputFiles({ name, mimeType: "application/xml", buffer: original });
  await expect(receipt).toContainText(`Saved as Uploads/${name}`);
  await expect(async () => {
    const scores = await (await request.get("/api/scores")).json();
    expect(scores.find((s) => s.path === `Uploads/${name}`), `${name} never appeared`).toBeTruthy();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  await scanSettled(request);

  // The second upload of the same name is refused, and shown as a decision
  // still waiting to be made - not as a receipt of something already done.
  await input.setInputFiles({ name, mimeType: "application/xml", buffer: replacement });
  const conflict = page.locator(".upload-conflict");
  await expect(conflict).toContainText(`Uploads/${name}`);
  // This batch was one file and it 409'd, so it posts no receipt of its own -
  // and must not leave the PREVIOUS batch's "Saved as ..." sitting beside the
  // conflict sentence, naming a file that already existed as if this upload
  // had just written it.
  await expect(receipt).toHaveCount(0);
  const replaceButton = conflict.locator(".conflict-replace");
  await expect(replaceButton).toBeVisible();
  expect(fs.readFileSync(path.join(libraryDir(), "Uploads", name))).toEqual(original);

  // Pressing Replace is the second, deliberate request that actually changes
  // the file - and only now does it change.
  await replaceButton.click();
  await expect(receipt).toContainText(`Replaced Uploads/${name}`);
  await expect(conflict).toHaveCount(0);
  await expect(async () => {
    expect(fs.readFileSync(path.join(libraryDir(), "Uploads", name))).toEqual(replacement);
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  await scanSettled(request);
});

test("a batch move shows every line, and a collision is blocked rather than overwritten", async ({
  page,
  request,
}) => {
  // Two scores with the SAME file name in two different folders. Moved into
  // one folder, the second would land on the first.
  const first = await upload(request, "clash.musicxml", "Bach");
  const second = await upload(request, "clash.musicxml", "Chopin");
  expect(second.id).not.toBe(first.id);

  await organise(page);
  await choose(page, first);
  await choose(page, second);
  await expect(page.locator(".selected-count")).toHaveText("2 selected");

  await page.locator(".move-open").click();
  const dialog = page.locator(".dialog.move");
  await dialog.locator(".new-folder-input").fill("Favourites");
  await dialog.locator(".new-folder-create").click();

  await expect(dialog.locator(".plan-line")).toHaveCount(2);
  await expect(dialog.locator(".plan-line.blocked")).toHaveCount(1);
  await expect(dialog.locator(".plan-line.blocked")).toContainText("another score in this same move");
  // ...and the move cannot be applied at all while a line is blocked: a batch
  // is all or nothing, so the button says so rather than moving the one that
  // would have worked.
  const apply = dialog.locator(".move-apply");
  await expect(apply).toBeDisabled();
  await expect(apply).toContainText("Fix the problems above first");

  // Neither file has moved.
  expect(fs.existsSync(path.join(libraryDir(), "Bach", "clash.musicxml"))).toBe(true);
  expect(fs.existsSync(path.join(libraryDir(), "Chopin", "clash.musicxml"))).toBe(true);

  // Deselecting one leaves a plan that can be applied, and it moves exactly
  // that one.
  await dialog.locator(".dialog-cancel").click();
  await unchoose(page, second);
  await expect(page.locator(".selected-count")).toHaveText("1 selected");
  await page.locator(".move-open").click();
  await dialog.locator(".folder-option", { hasText: "Favourites" }).click();
  await expect(dialog.locator(".plan-line.blocked")).toHaveCount(0);
  await dialog.locator(".move-apply").click();

  await expect(async () => {
    const one = await (await request.get(`/api/scores/${first.id}`)).json();
    const other = await (await request.get(`/api/scores/${second.id}`)).json();
    expect(one.path).toBe("Favourites/clash.musicxml");
    expect(other.path).toBe("Chopin/clash.musicxml");
  }).toPass({ timeout: SCAN_DEADLINE_MS });
});
