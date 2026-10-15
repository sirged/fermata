// Two-page spread in the PDF pane (issue #307).
//
// A 4-page PDF is uploaded through the real /api/upload, opened at
// #/score/{id}, and driven with real key presses and real viewport changes
// against the built client. Pages are told apart by the `data-page`
// attribute each canvas already carries; "shown" means the canvas has a
// layout box, so a canvas hidden with display:none does not count. Each test
// gets a fresh browser context, so the stored spread choice starts empty.
//
// The auto rule is: pane at least 760 wide, at least 500 tall, and at least
// 0.95 times as wide as it is tall. 1000x900 is a spread; 600x900 is not.
import { expect, test } from "@playwright/test";

const OWN_NAME = "spread-fixture.pdf";
const PAGES = 4;

// Built rather than hand-copied so the byte offsets are right for any count.
function buildPdf(pageCount) {
  const objects = ["1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"];
  const kids = Array.from({ length: pageCount }, (_, i) => `${3 + i} 0 R`).join(" ");
  objects.push(`2 0 obj<</Type/Pages/Kids[${kids}]/Count ${pageCount}>>endobj\n`);
  for (let i = 0; i < pageCount; i++) {
    objects.push(`${3 + i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 520]>>endobj\n`);
  }
  let body = "%PDF-1.4\n";
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

// A single DELETE moves a score to the trash, so the trash is purged too or
// a later spec's "the library is empty" check would see it.
async function emptyTheLibrary(request) {
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

async function uploadPdf(request) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: { file: { name: OWN_NAME, mimeType: "application/pdf", buffer: buildPdf(PAGES) } },
  });
  expect(res.ok(), await res.text()).toBe(true);
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(OWN_NAME));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("the uploaded PDF never appeared in the library");
}

// Data-page numbers of the canvases that currently have a layout box.
const shownPages = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll(".pdf-page")]
      .filter((c) => getComputedStyle(c).display !== "none" && c.getBoundingClientRect().width > 0)
      .map((c) => Number(c.dataset.page)),
  );
const hud = (page) => page.locator(".hud span");
const spreadButton = (page) => page.locator('.hud button[title="Two-page spread"]');
const exitGig = (page) => page.locator('.hud button[title="Exit gig mode (Esc)"]');

async function open(page, score, size) {
  await page.setViewportSize(size);
  await page.goto(`/#/score/${score.id}`);
  await expect(page.locator(".pdf-page")).toHaveCount(PAGES, { timeout: 30_000 });
  await expect(hud(page)).toBeVisible();
}

test.describe("two-page spread in the PDF pane", () => {
  let score;

  test.beforeEach(async ({ request }) => {
    const existing = await (await request.get("/api/scores")).json();
    expect(
      existing,
      "refusing to run: this backend has scores in its library this spec did not put there",
    ).toEqual([]);
    score = await uploadPdf(request);
  });

  test.afterEach(async ({ request }) => {
    await emptyTheLibrary(request);
  });

  test("a wide pane shows pages 1 and 2 only, and the HUD reads the pair", async ({ page }) => {
    await open(page, score, { width: 1000, height: 900 });
    await expect(hud(page)).toHaveText("1-2 / 4");
    expect(await shownPages(page)).toEqual([1, 2]);
  });

  test("ArrowRight advances the pair by one page and ArrowLeft returns", async ({ page }) => {
    await open(page, score, { width: 1000, height: 900 });
    await page.keyboard.press("ArrowRight");
    await expect(hud(page)).toHaveText("2-3 / 4");
    expect(await shownPages(page)).toEqual([2, 3]);
    await page.keyboard.press("ArrowLeft");
    await expect(hud(page)).toHaveText("1-2 / 4");
    expect(await shownPages(page)).toEqual([1, 2]);
  });

  test("turning forward clamps so the last pair stays 3-4 of 4", async ({ page }) => {
    await open(page, score, { width: 1000, height: 900 });
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(hud(page)).toHaveText("3-4 / 4");
    expect(await shownPages(page)).toEqual([3, 4]);
  });

  test("the saved last_page is the left page of the pair after a turn", async ({ page, request }) => {
    await open(page, score, { width: 1000, height: 900 });
    await page.keyboard.press("ArrowRight");
    await expect(hud(page)).toHaveText("2-3 / 4");
    await page.waitForTimeout(1500);
    const saved = await (await request.get(`/api/scores/${score.id}`)).json();
    expect(saved.last_page).toBe(2);
  });

  test("narrowing to a single column after turning to 2-3 keeps the left page (HUD 2 / 4) and every canvas unhidden", async ({
    page,
  }) => {
    await open(page, score, { width: 1000, height: 900 });
    await page.keyboard.press("ArrowRight");
    await expect(hud(page)).toHaveText("2-3 / 4");
    await page.setViewportSize({ width: 600, height: 900 });
    await expect(hud(page)).toHaveText("2 / 4");
    await expect(page.locator(".pdf-page")).toHaveCount(4);
    const hidden = await page.evaluate(
      () => [...document.querySelectorAll(".pdf-page")].filter((c) => getComputedStyle(c).display === "none").length,
    );
    expect(hidden).toBe(0);
    expect(await shownPages(page)).toEqual([1, 2, 3, 4]);
  });

  test("the spread button cycles auto, on, off and the choice survives a reload", async ({ page }) => {
    await open(page, score, { width: 600, height: 900 });
    await expect(spreadButton(page)).toHaveAttribute("data-spread-mode", "auto");
    expect(await shownPages(page)).toEqual([1, 2, 3, 4]);
    await spreadButton(page).click();
    await expect(spreadButton(page)).toHaveAttribute("data-spread-mode", "on");
    await expect(spreadButton(page)).toHaveClass(/on/);
    await expect(hud(page)).toHaveText("1-2 / 4");
    expect(await shownPages(page)).toEqual([1, 2]);
    await spreadButton(page).click();
    await expect(spreadButton(page)).toHaveAttribute("data-spread-mode", "off");
    await expect(hud(page)).toHaveText("1 / 4");
    expect(await shownPages(page)).toEqual([1, 2, 3, 4]);
    await page.reload();
    await expect(page.locator(".pdf-page")).toHaveCount(PAGES, { timeout: 30_000 });
    await expect(spreadButton(page)).toHaveAttribute("data-spread-mode", "off");
    expect(await shownPages(page)).toEqual([1, 2, 3, 4]);
  });

  test("gig mode at 1000x900 renders no half-page button while a spread shows", async ({ page }) => {
    await open(page, score, { width: 1000, height: 900 });
    await page.keyboard.press("f");
    await expect(exitGig(page)).toBeVisible();
    await expect(page.locator('.hud button[title="Half-page turns"]')).toHaveCount(0);
  });

  test("gig mode at 600x900 keeps the half-page button in a single column", async ({ page }) => {
    await open(page, score, { width: 600, height: 900 });
    await page.keyboard.press("f");
    await expect(exitGig(page)).toBeVisible();
    await expect(page.locator('.hud button[title="Half-page turns"]')).toHaveCount(1);
  });
});
