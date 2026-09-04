// The fret-to-note drill's weak-positions panel (issue #235): which
// positions get answered incorrectly, read back from the same structured
// rows fretboard-trainer.spec.js already proves get written (#32), grouped
// client-side - see docs/practice-data.md and position-counts.js's own
// header on why there is no bespoke aggregate endpoint behind this.
//
// FILE NAME MATTERS HERE. fretboard-trainer.spec.js's own "structured,
// queryable results" test says plainly that trainer_attempts has no
// per-test reset - a real weak-spot query is meant to read across many
// sessions, and there is no DELETE for this table at all. playwright.config.js
// runs every spec file with a single worker, in one shared process against
// one shared database, in file-name order - this file is named to sort
// BEFORE fretboard-trainer.spec.js (and every other file in this directory)
// so its own first test, the one place this suite can honestly claim
// "nothing has been answered incorrectly yet", runs before anything else in
// the run has written a row to trainer_attempts. Nothing else sorts earlier
// and touches that table: chord-flashcards.spec.js writes to the separate
// trainer_chord_attempts table, and no other file answers a fret-to-note
// question at all.
import { expect, test } from "@playwright/test";

import { forbiddenWord, localDay } from "../../src/lib/practice.js";

const drill = (page) => page.locator("section.drill");
const startButton = (page) => page.locator(".start-drill");
const panel = (page) => page.locator(".position-counts");
const rows = (page) => panel(page).locator("li");

const today = localDay();

async function reset(request) {
  const existing = await (await request.get("/api/instruments")).json();
  for (const instrument of existing) await request.delete(`/api/instruments/${instrument.id}`);
  const goals = (await (await request.get(`/api/practice/goals?today=${today}`)).json()).goals;
  for (const goal of goals) await request.delete(`/api/practice/goals/${goal.id}`);
  const sessions = (
    await (await request.get("/api/practice/sessions?limit=1000")).json()
  ).sessions;
  for (const session of sessions) await request.delete(`/api/practice/sessions/${session.id}`);
}

test.beforeEach(async ({ page, request }) => {
  const scores = await (await request.get("/api/scores")).json();
  expect(
    scores,
    "refusing to run: this backend has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and these tests delete practice history",
  ).toEqual([]);
  await reset(request);
});

function question(page) {
  return drill(page).evaluate((el) => ({
    note: el.dataset.questionNote,
    string: el.dataset.questionString ? Number(el.dataset.questionString) : null,
    fret: el.dataset.questionFret === "" ? null : Number(el.dataset.questionFret),
  }));
}

// ---------------------------------------------------------------------------
// The empty state is real - see the file header on why this must be the
// first test that runs anywhere in this suite.
// ---------------------------------------------------------------------------

test("with no attempts anywhere yet, the panel shows its empty state", async ({
  page,
  request,
}) => {
  const { total } = await (await request.get("/api/trainer/attempts")).json();
  expect(
    total,
    "trainer_attempts already has rows - this test has to run before anything in " +
      "the suite answers a fret-to-note question; see this file's own header on ordering.",
  ).toBe(0);

  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
  await expect(panel(page)).toHaveAttribute("data-loaded", "1");
  await expect(panel(page)).toHaveAttribute("data-row-count", "0");
  await expect(panel(page).locator(".empty-state")).toBeVisible();

  const text = await panel(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
  expect(text).not.toMatch(/%/);
});

// ---------------------------------------------------------------------------
// Ten answers, three incorrect on one position (issue #235's own falsifiable
// claim), lists that position first with its count - ahead of a smaller
// count elsewhere - and keeps updating without a reload.
// ---------------------------------------------------------------------------

test("ten answers with three incorrect on one position lists that position first with its count, and keeps updating live", async ({
  page,
  request,
}) => {
  // A miss elsewhere, seeded directly rather than through the drill, so the
  // panel's sort order (largest count first) is checked against something
  // real rather than a list of exactly one entry.
  const noise = await request.post("/api/trainer/attempts", {
    data: {
      drill: "fret_to_note",
      direction: "position_to_note",
      target_string: 5,
      target_fret: 3,
      target_note: "A",
      given_note: "B",
    },
  });
  expect(noise.ok(), await noise.text()).toBe(true);

  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();

  // Before this test answers anything, the noise row is already the whole
  // list - proof the panel reads real stored rows, not only ones this test
  // itself goes on to write.
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toHaveAttribute("data-string", "5");
  await expect(rows(page).first()).toHaveAttribute("data-fret", "3");
  await expect(rows(page).first()).toHaveAttribute("data-count", "1");

  // Narrow the scope to exactly one position: with only (string 1, fret 7)
  // in the pool, pickQuestion's own "never repeat the last one" rule falls
  // back to the whole pool, so every question this drill asks from here on
  // is that same position.
  for (const n of [6, 5, 4, 3, 2]) {
    await page.locator(".string-choice", { hasText: new RegExp(`^${n}$`) }).click();
  }
  await page.selectOption(".scope-start-fret", "7");
  await page.selectOption(".scope-end-fret", "7");
  await expect(drill(page)).toHaveAttribute("data-askable", "1");

  await startButton(page).click();
  await expect(drill(page)).toHaveAttribute("data-direction", "position_to_note");

  async function answer(wantCorrect) {
    const q = await question(page);
    expect(q.string).toBe(1);
    expect(q.fret).toBe(7);
    const note = wantCorrect ? q.note : q.note === "C" ? "C#" : "C";
    await page.locator(`.choice[data-note="${note}"]`).click();
    await expect(drill(page)).toHaveAttribute("data-attempt-log-failures", "0");
    await page.locator(".next-question").click();
  }

  // Seven answered correctly, three answered incorrectly - ten answers,
  // three misses on one position, in as many words as the issue states it.
  for (let i = 0; i < 7; i++) await answer(true);
  for (let i = 0; i < 3; i++) await answer(false);

  await expect(rows(page)).toHaveCount(2);
  const first = rows(page).first();
  const second = rows(page).nth(1);
  await expect(first).toHaveAttribute("data-string", "1");
  await expect(first).toHaveAttribute("data-fret", "7");
  await expect(first).toHaveAttribute("data-count", "3");
  await expect(second).toHaveAttribute("data-string", "5");
  await expect(second).toHaveAttribute("data-count", "1");

  const firstText = await first.textContent();
  expect(firstText).toContain("String 1, fret 7");
  expect(firstText).toContain("3 times");
  expect(forbiddenWord(firstText), firstText).toBeNull();
  expect(firstText).not.toMatch(/%/);

  // One more incorrect answer on the same position updates the panel's
  // count live - nothing in this test reloads or navigates the page.
  await answer(false);
  await expect(first).toHaveAttribute("data-count", "4");
});
