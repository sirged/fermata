// The PDF reader's everyday basics (issue #309): the invert toggle, the keys
// that turn pages, the HUD buttons, the remembered page, and the keys staying
// out of a text field.
//
// A four-page PDF is uploaded through the real /api/upload and opened through
// the real viewer. Every assertion reads a state the component itself
// publishes (the container's `dark` class, the HUD text, a canvas's computed
// filter, the stored last_page) after waiting for it, never a value sampled
// right after a keypress.
//
// Each test uploads its own copy and purges it afterwards, so the remembered
// page of one can never be the starting page of the next, and the library is
// empty again for the specs that sort after this one and insist on that.
import { expect, test } from "@playwright/test";

const SCORE_NAME = "viewer-basics-fixture.pdf";
const OWN_PATH = `Uploads/${SCORE_NAME}`;
const PAGES = 4;
// The computed filter of a canvas while the container is inverted.
const INVERTED = "invert(0.92) hue-rotate(180deg)";

// A minimal valid multi-page PDF, built so the xref offsets are right for any
// page count.
function buildMultiPagePdf(pageCount) {
  const objects = ["1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"];
  const kids = Array.from({ length: pageCount }, (_, i) => `${3 + i} 0 R`).join(" ");
  objects.push(`2 0 obj<</Type/Pages/Kids[${kids}]/Count ${pageCount}>>endobj\n`);
  for (let i = 0; i < pageCount; i++) {
    objects.push(`${3 + i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n`);
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

const pages = (page) => page.locator(".pages");
const canvas = (page, n) => page.locator(`canvas.pdf-page[data-page="${n}"]`);
const hud = (page) => page.locator(".hud span", { hasText: / \/ / });
const invertButton = (page) => page.getByTitle("Invert for practice in the dark");
const nextButton = (page) => page.getByTitle("Next page");
const prevButton = (page) => page.getByTitle("Previous page");

async function waitForScore(request, name) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(name));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared in the library`);
}

async function emptyTheLibrary(request) {
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

async function openScore(page, id) {
  await page.goto(`/#/score/${id}`);
  for (let n = 1; n <= PAGES; n += 1) {
    await expect(canvas(page, n)).toHaveCount(1, { timeout: 30_000 });
  }
  await expect(hud(page)).toHaveText(`1 / ${PAGES}`);
}

// A key handler here runs synchronously, but the HUD is a rendered value: wait
// two animation frames so a "nothing moved" assertion is made after a turn
// would have shown, not before. (data-settle-seq is not usable for this: a
// turn onto the page already shown scrolls nothing, fires no scroll event, and
// so never re-stamps it after the turn clears it.)
async function atRest(page) {
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
}

// Whether the page-n canvas intersects the .pages viewport: the HUD alone can
// say "3 / 4" while the pane never moved.
async function expectInView(page, n) {
  await expect
    .poll(() =>
      page.evaluate((num) => {
        const box = document.querySelector(".pages").getBoundingClientRect();
        const r = document.querySelector(`canvas.pdf-page[data-page="${num}"]`).getBoundingClientRect();
        return r.bottom > box.top && r.top < box.bottom;
      }, n),
    )
    .toBe(true);
}

const pageOf = (text) => Number(text.split(" / ")[0]);

async function expectPage(page, text) {
  await expect(hud(page)).toHaveText(text);
  await expectInView(page, pageOf(text));
}

async function pressAndExpect(page, key, text) {
  await page.keyboard.press(key);
  await expectPage(page, text);
}

async function pressAndExpectUnchanged(page, key, text) {
  await page.keyboard.press(key);
  await atRest(page);
  await expectPage(page, text);
}

let score;

test.describe("the PDF reader basics", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test.beforeEach(async ({ request, page }) => {
    const existing = await (await request.get("/api/scores")).json();
    expect(
      existing.filter((s) => s.path !== OWN_PATH),
      "refusing to run: this backend has scores in its library this spec did not put " +
        "there, so it is not the throwaway instance the suite creates",
    ).toEqual([]);
    await emptyTheLibrary(request);
    const res = await request.post("/api/upload?folder=Uploads", {
      multipart: {
        file: { name: SCORE_NAME, mimeType: "application/pdf", buffer: buildMultiPagePdf(PAGES) },
      },
    });
    expect(res.ok(), await res.text()).toBe(true);
    score = await waitForScore(request, SCORE_NAME);
    await openScore(page, score.id);
  });

  test.afterEach(async ({ request }) => {
    await emptyTheLibrary(request);
    expect(await (await request.get("/api/scores")).json()).toEqual([]);
  });

  test("the invert button toggles the container class and the canvas filter", async ({ page }) => {
    const filter = () => canvas(page, 1).evaluate((el) => getComputedStyle(el).filter);
    // The reader opens inverted: darkMode starts true in the component.
    await expect(pages(page)).toHaveClass(/\bdark\b/);
    await expect.poll(filter).toBe(INVERTED);

    await invertButton(page).click();
    await expect(pages(page)).not.toHaveClass(/\bdark\b/);
    await expect.poll(filter).toBe("none");

    await invertButton(page).click();
    await expect(pages(page)).toHaveClass(/\bdark\b/);
    await expect.poll(filter).toBe(INVERTED);
  });

  test("ArrowRight, PageDown and Space each turn forward one page and stop at the last", async ({ page }) => {
    await pressAndExpect(page, "ArrowRight", `2 / ${PAGES}`);
    await pressAndExpect(page, "PageDown", `3 / ${PAGES}`);
    await pressAndExpect(page, "Space", `4 / ${PAGES}`);
    await pressAndExpectUnchanged(page, "ArrowRight", `4 / ${PAGES}`);
  });

  test("ArrowLeft and PageUp turn back one page and stop at the first", async ({ page }) => {
    for (let n = 2; n <= PAGES; n += 1) await pressAndExpect(page, "ArrowRight", `${n} / ${PAGES}`);
    await pressAndExpect(page, "ArrowLeft", `3 / ${PAGES}`);
    await pressAndExpect(page, "PageUp", `2 / ${PAGES}`);
    await pressAndExpect(page, "ArrowLeft", `1 / ${PAGES}`);
    await pressAndExpectUnchanged(page, "ArrowLeft", `1 / ${PAGES}`);
  });

  test("the Next page and Previous page buttons move the HUD like the keys do", async ({ page }) => {
    await nextButton(page).click();
    await expectPage(page, `2 / ${PAGES}`);
    await nextButton(page).click();
    await expectPage(page, `3 / ${PAGES}`);
    await prevButton(page).click();
    await expectPage(page, `2 / ${PAGES}`);
    await prevButton(page).click();
    await expectPage(page, `1 / ${PAGES}`);
    await prevButton(page).click();
    await atRest(page);
    await expectPage(page, `1 / ${PAGES}`);
  });

  test("the page turned to is stored after the debounce and restored on reopening", async ({ page, request }) => {
    await pressAndExpect(page, "ArrowRight", `2 / ${PAGES}`);
    await pressAndExpect(page, "ArrowRight", `3 / ${PAGES}`);
    // setPage() debounces the write by 1.2s; poll for the stored value.
    await expect
      .poll(async () => (await (await request.get(`/api/scores/${score.id}`)).json()).last_page, {
        timeout: 10_000,
      })
      .toBe(3);

    await page.goto("/#/");
    await expect(pages(page)).toHaveCount(0);
    await page.goto(`/#/score/${score.id}`);
    await expect(canvas(page, 3)).toHaveCount(1, { timeout: 30_000 });
    await expectPage(page, `3 / ${PAGES}`);
  });

  test("keys do not turn the page while a text field has focus", async ({ page }) => {
    // The tempo field in the score header is a number input reachable
    // without leaving the score page.
    const field = page.locator("input.tempo-input");
    await expect(field).toBeVisible();
    await field.focus();
    await expect(field).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await atRest(page);
    await expect(hud(page)).toHaveText(`1 / ${PAGES}`);
    // The same key, outside the field, does turn: the page was not simply
    // unresponsive.
    await field.evaluate((el) => el.blur());
    await pressAndExpect(page, "ArrowRight", `2 / ${PAGES}`);
  });
});
