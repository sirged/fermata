// Issue #308: search, favourites, tags, the duplicates view and thumbnails are
// the first screen a user meets, and before this file no browser spec drove
// any of them. Every test here seeds real scores through the real
// /api/upload, drives the real library page (and, for tag editing, the real
// score page - see below), and reads the server state back through the API.
//
// WHERE TAGS ARE EDITED. The issue text says "adding a tag from the grid",
// but a library card has no tag control: tags are edited from the score page
// header (Viewer.svelte's "+ tags" button). The library page only offers the
// tag cloud in the sidebar as a filter. The tag test therefore edits on the
// score page and filters from the grid.
//
// WHERE THE COMPOSER COMES FROM. The seeded files carry no composer of their
// own, so it is set through PATCH /api/scores/{id} as test setup; the search
// assertions are what is under test.
//
// Because this file sorts before every zz*.spec.js, which assert an empty
// library when they start, each test removes its rows (trash, then purge) and
// the last test asserts GET /api/scores is empty.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const XML_FIXTURE = path.join(here, "..", "..", "test-fixtures", "multi-part.musicxml");

// A one-page PDF; `tag` makes the bytes (and so the hash) differ per file.
function buildPdf(tag) {
  const objects = [
    "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
    "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
    "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n",
  ];
  let body = `%PDF-1.4\n% ${tag}\n`;
  const offsets = [0];
  for (const obj of objects) {
    offsets.push(body.length);
    body += obj;
  }
  const xrefStart = body.length;
  const total = objects.length + 1;
  let xref = `xref\n0 ${total}\n0000000000 65535 f \n`;
  for (let i = 1; i < total; i++) xref += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
  body += xref + `trailer<</Size ${total}/Root 1 0 R>>\nstartxref\n${xrefStart}\n%%EOF`;
  return Buffer.from(body, "utf-8");
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

async function upload(request, name, buffer) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: { file: { name, mimeType: "application/octet-stream", buffer } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  return waitForScore(request, name);
}

async function getScore(request, id) {
  const res = await request.get(`/api/scores/${id}`);
  expect(res.ok()).toBe(true);
  return res.json();
}

async function emptyTheLibrary(request) {
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

const cards = (page) => page.locator("a.card");
const cardFor = (page, title) => cards(page).filter({ hasText: title });

test.describe("library basics against the real build", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ request }) => {
    expect(
      await (await request.get("/api/scores")).json(),
      "refusing to run: this backend already has scores in its library, so it is not the throwaway instance the suite creates",
    ).toEqual([]);
  });

  test.afterEach(async ({ request }) => {
    await emptyTheLibrary(request);
  });

  test("search narrows the grid by title and by composer and shows the empty state on a miss", async ({ page, request }) => {
    const a = await upload(request, "Quillfeather Study.pdf", buildPdf("search-a"));
    const b = await upload(request, "Marrowbeam Sketch.pdf", buildPdf("search-b"));
    const patched = await request.patch(`/api/scores/${a.id}`, { data: { composer: "Osric Vandermeer" } });
    expect(patched.ok()).toBe(true);
    expect(b.title).not.toBe(a.title);

    await page.goto("/#/");
    await expect(cards(page)).toHaveCount(2);
    const search = page.getByPlaceholder("Search title, composer, source…");

    await search.fill(a.title);
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page).first()).toContainText(a.title);

    await search.fill("");
    await expect(cards(page)).toHaveCount(2);
    await search.fill("Vandermeer");
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page).first()).toContainText(a.title);

    await search.fill("zzqxj-no-such-score");
    await expect(cards(page)).toHaveCount(0);
    await expect(page.locator(".empty")).toContainText("Nothing here yet.");
  });

  test("a favourite toggled on a card survives a reload, reads back from the API and is the only card under the favourites filter", async ({ page, request }) => {
    const a = await upload(request, "Quillfeather Study.pdf", buildPdf("fav-a"));
    const b = await upload(request, "Marrowbeam Sketch.pdf", buildPdf("fav-b"));
    expect(a.favorite).toBeFalsy();

    await page.goto("/#/");
    await expect(cards(page)).toHaveCount(2);
    await cardFor(page, a.title).locator("button.fav").click();
    await expect(cardFor(page, a.title).locator("button.fav")).toHaveClass(/\bon\b/);

    await page.reload();
    await expect(cards(page)).toHaveCount(2);
    await expect(cardFor(page, a.title).locator("button.fav")).toHaveClass(/\bon\b/);
    await expect(cardFor(page, b.title).locator("button.fav")).not.toHaveClass(/\bon\b/);
    expect((await getScore(request, a.id)).favorite).toBe(true);
    expect((await getScore(request, b.id)).favorite).toBeFalsy();

    await page.getByRole("button", { name: /Favorites/ }).click();
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page).first()).toContainText(a.title);
  });

  test("a tag added on the score page reads back from the API, filters the grid, and is gone from the API once removed", async ({ page, request }) => {
    const a = await upload(request, "Quillfeather Study.pdf", buildPdf("tag-a"));
    const b = await upload(request, "Marrowbeam Sketch.pdf", buildPdf("tag-b"));

    await page.goto(`/#/score/${a.id}`);
    await page.getByRole("button", { name: "+ tags" }).click();
    await page.getByPlaceholder("tag, another tag").fill("warmup-basket");
    await page.getByRole("button", { name: "Save" }).click();
    await expect.poll(async () => (await getScore(request, a.id)).tags).toEqual(["warmup-basket"]);
    expect((await getScore(request, b.id)).tags).toEqual([]);

    await page.goto("/#/");
    await expect(cards(page)).toHaveCount(2);
    await page.locator(".tag-cloud .chip", { hasText: "warmup-basket" }).click();
    await expect(cards(page)).toHaveCount(1);
    await expect(cards(page).first()).toContainText(a.title);

    await page.goto(`/#/score/${a.id}`);
    await page.getByRole("button", { name: /warmup-basket/ }).click();
    await page.getByPlaceholder("tag, another tag").fill("");
    await page.getByRole("button", { name: "Save" }).click();
    await expect.poll(async () => (await getScore(request, a.id)).tags).toEqual([]);
  });

  test("two uploads with identical bytes form one group in the duplicates view, and trashing one drops the group from the view", async ({ page, request }) => {
    const bytes = buildPdf("duplicate-bytes");
    const a = await upload(request, "Quillfeather Study.pdf", bytes);
    const b = await upload(request, "Quillfeather Copy.pdf", bytes);
    expect(a.id).not.toBe(b.id);
    expect(a.hash).toBe(b.hash);

    await page.goto("/#/");
    await page.getByRole("button", { name: /Duplicates/ }).click();
    await expect(page.locator(".dupe-group")).toHaveCount(1);
    await expect(page.locator(".dupe-head")).toContainText("2 copies");
    await expect(page.locator(".dupe-path")).toHaveCount(2);
    await expect(page.locator(".dupe-path", { hasText: "Quillfeather Study.pdf" })).toHaveCount(1);
    await expect(page.locator(".dupe-path", { hasText: "Quillfeather Copy.pdf" })).toHaveCount(1);
    const groups = await (await request.get("/api/duplicates")).json();
    expect(groups).toHaveLength(1);
    expect(groups[0].scores.map((s) => s.id).sort()).toEqual([a.id, b.id].sort());

    expect((await request.delete(`/api/scores/${b.id}`)).ok()).toBe(true);
    expect(await (await request.get("/api/duplicates")).json()).toEqual([]);
    await page.reload();
    await page.getByRole("button", { name: /Duplicates/ }).click();
    await expect(page.locator(".dupe-group")).toHaveCount(0);
    await expect(page.locator(".empty")).toContainText("No duplicates found");
  });

  test("a PDF card shows a thumbnail image that the API serves as a PNG", async ({ page, request }) => {
    const pdf = await upload(request, "Quillfeather Study.pdf", buildPdf("thumb-pdf"));

    await page.goto("/#/");
    const img = cardFor(page, pdf.title).locator(".sheet img");
    await expect(img).toHaveCount(1);
    await expect(img).toHaveAttribute("src", `/api/scores/${pdf.id}/thumb`);
    await expect.poll(async () => (await page.request.get(`/api/scores/${pdf.id}/thumb`)).status(), { timeout: 15_000 }).toBe(200);
    const res = await page.request.get(`/api/scores/${pdf.id}/thumb`);
    expect(res.headers()["content-type"]).toContain("image/png");
  });

  test("pinning current behaviour, not a requirement: a MusicXML card has no image and shows the clef placeholder", async ({ page, request }) => {
    const xml = await upload(request, "Tindle Drift.musicxml", fs.readFileSync(XML_FIXTURE));

    await page.goto("/#/");
    const card = cardFor(page, xml.title);
    await expect(card).toHaveCount(1);
    await expect(card.locator(".sheet img")).toHaveCount(0);
    await expect(card.locator(".sheet-icon")).toBeVisible();
    // The server agrees: there is no thumbnail to fetch for it.
    expect((await page.request.get(`/api/scores/${xml.id}/thumb`)).status()).toBe(404);
  });

  test("the library is empty once every seeded row is trashed and purged", async ({ request }) => {
    expect(await (await request.get("/api/scores")).json()).toEqual([]);
    expect(await (await request.get("/api/trash")).json()).toEqual([]);
  });
});
