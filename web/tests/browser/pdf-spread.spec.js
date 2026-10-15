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

async function uploadPdf(request, pages = PAGES) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: { file: { name: OWN_NAME, mimeType: "application/pdf", buffer: buildPdf(pages) } },
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

// Auto mode needs a coarse pointer; touch emulation is what makes
// (pointer: coarse) match in Chromium. The no-touch test below turns it off.
test.use({ hasTouch: true });

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
    // the dependency the auto rule has, stated rather than assumed
    expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches)).toBe(true);
    await expect(hud(page)).toHaveText("1-2 / 4");
    expect(await shownPages(page)).toEqual([1, 2]);
  });

  // The PATCH bodies the page sends for this score, in order.
  function recordPatches(page) {
    const bodies = [];
    page.on("request", (req) => {
      if (req.method() === "PATCH" && new URL(req.url()).pathname === `/api/scores/${score.id}`) {
        bodies.push(req.postDataJSON());
      }
    });
    return bodies;
  }

  // Holds the document body so the pane is open with no page count and no
  // canvases, which is where a turn can arrive before anything exists.
  async function holdBody(page) {
    let release;
    const held = new Promise((resolve) => (release = resolve));
    await page.route(`**/api/scores/${score.id}/file`, async (route) => {
      await held;
      await route.continue();
    });
    return release;
  }

  async function reseed(request, pages, lastPage) {
    await emptyTheLibrary(request);
    score = await uploadPdf(request, pages);
    if (lastPage) {
      const res = await request.patch(`/api/scores/${score.id}`, { data: { last_page: lastPage } });
      expect(res.ok()).toBe(true);
    }
  }

  test("a turn pressed before the document has loaded is honoured when the spread activates", async ({
    page,
    request,
  }) => {
    await reseed(request, 6);
    const release = await holdBody(page);
    await page.setViewportSize({ width: 1000, height: 900 });
    await page.goto(`/#/score/${score.id}`);
    await expect(hud(page)).toHaveText("1 / …");
    await expect(page.locator(".pdf-page")).toHaveCount(0);
    await page.keyboard.press("ArrowRight");
    release();
    await expect(page.locator(".pdf-page")).toHaveCount(6, { timeout: 30_000 });
    await expect(hud(page)).toHaveText("2-3 / 6");
    expect(await shownPages(page)).toEqual([2, 3]);
  });

  test("a turn pressed before render beats the stored last_page and is saved", async ({ page, request }) => {
    await reseed(request, 6, 4);
    const patches = recordPatches(page);
    const release = await holdBody(page);
    await page.setViewportSize({ width: 1000, height: 900 });
    await page.goto(`/#/score/${score.id}`);
    await expect(hud(page)).toHaveText("1 / …");
    await page.keyboard.press("ArrowLeft");
    release();
    await expect(page.locator(".pdf-page")).toHaveCount(6, { timeout: 30_000 });
    await expect(hud(page)).toHaveText("1-2 / 6");
    await expect.poll(() => patches.map((b) => b.last_page).join(","), { timeout: 5000 }).toBe("1");
    expect((await (await request.get(`/api/scores/${score.id}`)).json()).last_page).toBe(1);
  });

  test("opening a score stored on its last page shows the clamped pair without writing it back", async ({
    page,
    request,
  }) => {
    await reseed(request, 4, 4);
    const patches = recordPatches(page);
    await open(page, score, { width: 1000, height: 900 });
    await expect(hud(page)).toHaveText("3-4 / 4");
    await page.waitForTimeout(1800);
    expect(patches).toEqual([]);
    expect((await (await request.get(`/api/scores/${score.id}`)).json()).last_page).toBe(4);
  });

  test("a clamped forward turn at the last pair sends no save", async ({ page, request }) => {
    await reseed(request, 4, 3);
    const patches = recordPatches(page);
    await open(page, score, { width: 1000, height: 900 });
    await expect(hud(page)).toHaveText("3-4 / 4");
    await page.keyboard.press("ArrowRight");
    await expect(hud(page)).toHaveText("3-4 / 4");
    await page.waitForTimeout(1800);
    expect(patches).toEqual([]);
  });

  test("a pane under 480 tall stays a single column at 1000x640", async ({ page }) => {
    await open(page, score, { width: 1000, height: 640 });
    await expect(hud(page)).toHaveText("1 / 4");
    expect(await shownPages(page)).toEqual([1, 2, 3, 4]);
  });

  test("a pane narrower than 0.95 of its height stays a single column at 780x1100", async ({ page }) => {
    await open(page, score, { width: 780, height: 1100 });
    await expect(hud(page)).toHaveText("1 / 4");
    expect(await shownPages(page)).toEqual([1, 2, 3, 4]);
  });

  test("the 830x690 inner screen of a foldable shows a spread outside gig mode", async ({ page }) => {
    await open(page, score, { width: 830, height: 690 });
    await expect(hud(page)).toHaveText("1-2 / 4");
    expect(await shownPages(page)).toEqual([1, 2]);
  });

  test.describe("without a touch device", () => {
    test.use({ hasTouch: false });
    test("a mouse-driven 1000x900 pane stays a single column and the HUD reads 1 / 4", async ({ page }) => {
      await open(page, score, { width: 1000, height: 900 });
      expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches)).toBe(false);
      await expect(hud(page)).toHaveText("1 / 4");
      expect(await shownPages(page)).toEqual([1, 2, 3, 4]);
    });
  });

  test("a turn that cannot move in a single column still restores data-settle-seq", async ({ page }) => {
    await open(page, score, { width: 600, height: 900 });
    const pane = page.locator(".pages");
    await expect(pane).toHaveAttribute("data-settle-seq", /\d+/, { timeout: 5000 });
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    await expect(hud(page)).toHaveText("1 / 4");
    await expect(pane).toHaveAttribute("data-settle-seq", /\d+/, { timeout: 5000 });
  });

  test("a clamped turn in a spread (ArrowLeft at 1-2) leaves data-settle-seq present", async ({ page }) => {
    await open(page, score, { width: 1000, height: 900 });
    const pane = page.locator(".pages");
    await page.keyboard.press("ArrowLeft");
    await expect(hud(page)).toHaveText("1-2 / 4");
    await expect(pane).toHaveAttribute("data-settle-seq", /\d+/, { timeout: 5000 });
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
