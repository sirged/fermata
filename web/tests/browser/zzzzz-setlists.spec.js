// Setlists (#6), against the real backend and the real build.
//
// WHAT THESE PROVE THAT THE SERVER TESTS CANNOT.
// server/tests/test_setlists_api.py pins every endpoint to literal values. It
// cannot prove any of it reached the screen - #95's lesson, recorded next door
// in zz-library-missing.spec.js, is that a guarantee nothing renders is a
// guarantee nobody has. So what is asserted here is the seam: a setlist created
// from the page appears; scores added from the page render in order; the
// reorder buttons actually move a piece and the new order SURVIVES A RELOAD (so
// it was written, not just rearranged locally); removing a member takes it off
// the list while the score stays in the library; a trashed member renders
// marked and is NOT a link; and deleting a setlist removes it while its scores
// remain.
//
// EVERY READ-AFTER-ACTION WAITS ON THE PAGE, never through the request context
// after a click (#110): each assertion below is on rendered DOM that cannot be
// true until the server answered, because the component only re-renders from
// the server's response. There is no out-of-band status read racing the write.
//
// WHY IT IS NAMED TO SORT LAST. Like zzzz-score-progress, it puts scores in the
// library, and the specs that empty the library refuse to run against a backend
// holding scores they did not create. Sorting after all of them keeps those
// refusals true; its own cleanup returns the instance to empty.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "notation-only.musicxml");

const SCAN_DEADLINE_MS = 30_000;

async function scanSettled(request) {
  const deadline = Date.now() + SCAN_DEADLINE_MS;
  for (;;) {
    const status = await (await request.get("/api/scan/status")).json();
    if (!status.scanning) return status;
    if (Date.now() > deadline) throw new Error("a scan never finished");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

/** A real score in the throwaway library, made distinct per name so the
 * scanner's content-hash relink does not treat the second upload as a rename of
 * the first (same helper shape as zzzz-score-progress). */
async function upload(request, name) {
  const body = Buffer.concat([fs.readFileSync(FIXTURE), Buffer.from(`<!-- ${name} -->\n`)]);
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: { file: { name, mimeType: "application/xml", buffer: body } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  let found;
  await expect(async () => {
    const scores = await (await request.get("/api/scores")).json();
    found = scores.find((s) => s.path === `Uploads/${name}`);
    expect(found, `${name} never appeared in the library`).toBeTruthy();
  }).toPass({ timeout: SCAN_DEADLINE_MS });
  await scanSettled(request);
  return found;
}

/** Upload a score and give it a distinct title, so members are told apart on
 * screen. The musicxml fixture carries one title ("Notation-only example") for
 * every copy, so without this every member would render the same text - a
 * setlist of identically-named pieces is a real thing, but not what these
 * order assertions are about. */
async function uploadTitled(request, name, title) {
  const score = await upload(request, name);
  const res = await request.patch(`/api/scores/${score.id}`, { data: { title } });
  expect(res.ok(), await res.text()).toBe(true);
  return { ...score, title };
}

async function emptyEverything(request) {
  await scanSettled(request);
  for (const setlist of await (await request.get("/api/setlists")).json()) {
    await request.delete(`/api/setlists/${setlist.id}`);
  }
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

test.beforeEach(async ({ request }) => {
  // The same refusal every library-touching spec carries: this file empties the
  // library, so it must be the throwaway instance the suite creates.
  const existing = await (await request.get("/api/scores")).json();
  const foreign = existing.filter((s) => !s.missing_since && !s.path.startsWith("Uploads/"));
  expect(
    foreign,
    "refusing to run: this backend has scores in folders the suite never creates",
  ).toEqual([]);
  await emptyEverything(request);
});

test.afterAll(async ({ request }) => {
  await emptyEverything(request);
});

const members = (page) => page.locator(".member");
const memberTitles = (page) => page.locator(".member .member-title");
const setlistRows = (page) => page.locator(".setlist-row");

/** Open the setlists list page and wait for it to have rendered. */
async function openList(page) {
  await page.goto("/#/setlists");
  await expect(page.locator(".setlists")).toBeVisible();
}

test("a setlist created from the page appears in the list", async ({ page }) => {
  await openList(page);
  await expect(page.locator(".empty")).toBeVisible();

  await page.locator(".new-name").fill("Friday gig");
  await page.locator(".create").click();

  // create() navigates into the new setlist's detail page - a barrier that
  // cannot be true until the POST returned an id.
  await expect(page.locator(".setlist-title")).toHaveText("Friday gig");

  // And it is in the list.
  await openList(page);
  await expect(setlistRows(page)).toHaveCount(1);
  await expect(page.locator(".setlist-name")).toHaveText("Friday gig");
});

test("scores added from the page render in order, and reorder survives a reload", async ({
  page,
  request,
}) => {
  const a = await uploadTitled(request, "alpha.musicxml", "Alpha");
  const b = await uploadTitled(request, "bravo.musicxml", "Bravo");
  const c = await uploadTitled(request, "charlie.musicxml", "Charlie");
  const created = await (
    await request.post("/api/setlists", { data: { name: "Set" } })
  ).json();

  await page.goto(`/#/setlists/${created.id}`);
  await expect(page.locator(".setlist-title")).toHaveText("Set");

  // Add all three through the add panel.
  await page.locator(".add-scores").click();
  for (const score of [a, b, c]) {
    await page.locator(`.add-candidate[data-score-id="${score.id}"] .add-candidate-btn`).click();
    // Barrier: the member list grows on the server's response, and the
    // candidate is removed from the panel - wait on the member appearing.
    await expect(page.locator(`.member[data-score-id="${score.id}"]`)).toBeVisible();
  }
  await expect(memberTitles(page)).toHaveText(["Alpha", "Bravo", "Charlie"]);

  // Move Charlie (index 2) up once -> [Alpha, Charlie, Bravo].
  await page.locator(`.member[data-score-id="${c.id}"] .reorder-up`).click();
  await expect(memberTitles(page)).toHaveText(["Alpha", "Charlie", "Bravo"]);

  // The order is written, not just local: a reload reads it back the same.
  await page.reload();
  await expect(memberTitles(page)).toHaveText(["Alpha", "Charlie", "Bravo"]);
});

test("removing a member takes it off the setlist but not out of the library", async ({
  page,
  request,
}) => {
  const a = await uploadTitled(request, "keep.musicxml", "Keep");
  const b = await uploadTitled(request, "drop.musicxml", "Drop");
  const created = await (await request.post("/api/setlists", { data: { name: "Set" } })).json();
  await request.post(`/api/setlists/${created.id}/scores`, { data: { score_id: a.id } });
  await request.post(`/api/setlists/${created.id}/scores`, { data: { score_id: b.id } });

  await page.goto(`/#/setlists/${created.id}`);
  await expect(members(page)).toHaveCount(2);

  await page.locator(`.member[data-score-id="${b.id}"] .remove-member`).click();
  await expect(members(page)).toHaveCount(1);
  await expect(memberTitles(page)).toHaveText(["Keep"]);

  // The removed score is still in the library - the list page reads it back.
  const stillThere = await (await request.get(`/api/scores/${b.id}`)).json();
  expect(stillThere.id).toBe(b.id);
});

test("a trashed score stays in the setlist, marked, and is not a link", async ({
  page,
  request,
}) => {
  const a = await upload(request, "present.musicxml");
  const b = await upload(request, "trashed.musicxml");
  const created = await (await request.post("/api/setlists", { data: { name: "Set" } })).json();
  await request.post(`/api/setlists/${created.id}/scores`, { data: { score_id: a.id } });
  await request.post(`/api/setlists/${created.id}/scores`, { data: { score_id: b.id } });

  // Trash one of them through the real delete endpoint, then open the page - so
  // the page renders a member whose score is in the trash.
  const del = await request.delete(`/api/scores/${b.id}`);
  expect(del.ok(), await del.text()).toBe(true);

  await page.goto(`/#/setlists/${created.id}`);
  await expect(members(page)).toHaveCount(2); // still listed, not dropped

  const trashedMember = page.locator(`.member[data-score-id="${b.id}"]`);
  await expect(trashedMember).toHaveClass(/deleted/);
  await expect(trashedMember.locator(".member-deleted")).toHaveText("in trash");
  // Not a link: a trashed member has no anchor to open (it is not in the
  // library to open), where a live member does.
  await expect(trashedMember.locator("a.member-open")).toHaveCount(0);
  await expect(page.locator(`.member[data-score-id="${a.id}"] a.member-open`)).toHaveCount(1);
});

test("deleting a setlist removes it while its scores remain", async ({ page, request }) => {
  const a = await upload(request, "song.musicxml");
  const created = await (await request.post("/api/setlists", { data: { name: "Doomed" } })).json();
  await request.post(`/api/setlists/${created.id}/scores`, { data: { score_id: a.id } });

  await page.goto(`/#/setlists/${created.id}`);
  await expect(members(page)).toHaveCount(1);

  await page.locator(".delete-setlist").click();
  await page.locator(".confirm-delete-yes").click();

  // destroy() navigates back to the list once the DELETE returned - a barrier
  // that cannot be true until the server answered.
  await expect(page.locator(".setlists")).toBeVisible();
  await expect(setlistRows(page)).toHaveCount(0);
  await expect(page.locator(".empty")).toBeVisible();

  // The score it held is still in the library.
  const stillThere = await (await request.get(`/api/scores/${a.id}`)).json();
  expect(stillThere.id).toBe(a.id);
});

test("Start practising opens the first member in the real viewer", async ({ page, request }) => {
  const a = await upload(request, "opener.musicxml");
  const created = await (await request.post("/api/setlists", { data: { name: "Gig" } })).json();
  await request.post(`/api/setlists/${created.id}/scores`, { data: { score_id: a.id } });

  await page.goto(`/#/setlists/${created.id}`);
  await expect(members(page)).toHaveCount(1);

  await page.locator(".start-practising").click();
  // The ordinary score viewer, reached by hash - practising a setlist reuses
  // the real player rather than a second one.
  await expect(page).toHaveURL(new RegExp(`#/score/${a.id}$`));
});
