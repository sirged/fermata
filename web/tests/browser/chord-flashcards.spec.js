// The chord flash card drill (issue #28), built on the neck (#25) and the
// constraint model (#26), against the real backend and the real build.
//
// The ground truth for "what chord is this" is read off the section's own
// data-question-root/data-question-quality attributes (the question this
// component actually picked), the same discipline fretboard-trainer.spec.js
// applies to data-question-note - never off anything a mock would make true
// for free. Which POSITIONS sound a required tone is read off the neck's
// own data-note attributes (Neck.svelte's rendered arithmetic, already
// proven correct in fretboard-trainer.spec.js), combined with an
// INDEPENDENTLY reimplemented chord formula below rather than anything
// imported from the app - so this suite would catch the app's own chord
// math breaking, not merely restate it.
import { expect, test } from "@playwright/test";

import { forbiddenWord, localDay } from "../../src/lib/practice.js";

const drill = (page) => page.locator("section.drill");
const startButton = (page) => page.locator(".start-drill");
const progress = (page) => page.locator(".progress");
const answerStatement = (page) => page.locator(".answer-statement");

const today = localDay();

// An independent copy of the chord-tone formula, NOT imported from the app
// - see this file's own header. Any drift between this and chord-theory.js
// would show up as this suite's own assertions failing against what the
// page renders, which is the point.
const PITCH_CLASSES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"];
const INTERVALS = {
  major: [0, 4, 7],
  minor: [0, 3, 7],
  dominant7: [0, 4, 7, 10],
  minor7: [0, 3, 7, 10],
  major7: [0, 4, 7, 11],
};
const SEVENTH_SUFFIX = { dominant7: "7", minor7: "m7", major7: "maj7" };
function chordTones(root, quality) {
  const i = PITCH_CLASSES.indexOf(root);
  return INTERVALS[quality].map((step) => PITCH_CLASSES[(i + step) % 12]);
}
function chordName(root, quality) {
  if (quality === "major" || quality === "minor") return `${root} ${quality}`;
  return `${root}${SEVENTH_SUFFIX[quality]}`;
}

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

  await page.goto("/#/chords");
  await expect(drill(page)).toBeVisible();
});

function question(page) {
  return drill(page).evaluate((el) => ({
    root: el.dataset.questionRoot,
    quality: el.dataset.questionQuality,
  }));
}

// Any answer choice other than the one naming (root, quality) - a `:not()`
// CSS selector, not `.filter({ hasNot: ... })`, because hasNot's locator is
// scoped to DESCENDANTS of each candidate and data-root/data-quality sit on
// the .choice element itself, not on a child of it.
function wrongChoice(page, root, quality) {
  return page.locator(`.choice:not([data-root="${root}"][data-quality="${quality}"])`).first();
}

// Taps one position per required tone, on a distinct string each time -
// picked from the neck's OWN data-note attributes, so this is a real
// rendered position and not an invented one.
async function tapChord(page, tones) {
  const usedStrings = new Set();
  for (const note of tones) {
    const candidates = page.locator(`g.position[data-note="${note}"]`);
    const count = await candidates.count();
    let tapped = false;
    for (let i = 0; i < count; i++) {
      const el = candidates.nth(i);
      const string = await el.getAttribute("data-string");
      if (usedStrings.has(string)) continue;
      usedStrings.add(string);
      await el.click();
      tapped = true;
      break;
    }
    expect(tapped, `a free string sounding ${note}`).toBe(true);
  }
}

// ---------------------------------------------------------------------------
// The drill: prompt, answer, feedback, next - both directions, both a
// correct and an incorrect answer, handled honestly.
// ---------------------------------------------------------------------------

test("shape_to_name: naming the right chord says so plainly, and the count moves", async ({
  page,
}) => {
  await startButton(page).click();
  await expect(drill(page)).toHaveAttribute("data-direction", "shape_to_name");
  await expect(progress(page)).toHaveText("Nothing asked yet.");

  const q = await question(page);
  await page.locator(`.choice[data-root="${q.root}"][data-quality="${q.quality}"]`).click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "1");
  await expect(progress(page)).toHaveText("1 chord, 1 answered correctly.");
  await expect(answerStatement(page)).toContainText(chordName(q.root, q.quality));
  await expect(
    page.locator(`.choice[data-root="${q.root}"][data-quality="${q.quality}"]`),
  ).toHaveClass(/correct/);

  const text = await answerStatement(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
  expect(text).not.toMatch(/%/);
});

test("shape_to_name: naming a different chord names the right one, without a verdict word", async ({
  page,
}) => {
  await startButton(page).click();
  const q = await question(page);
  const wrongButton = wrongChoice(page, q.root, q.quality);
  await wrongButton.click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "0");
  await expect(progress(page)).toHaveText("1 chord, none answered correctly.");
  await expect(answerStatement(page)).toContainText(`That shape is ${chordName(q.root, q.quality)}.`);
  const text = await answerStatement(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
  expect(text).not.toMatch(/%/);
});

test("name_to_shape: tapping every required tone and only those grades correct", async ({
  page,
}) => {
  await page.locator(".direction-choice").nth(1).click();
  await startButton(page).click();
  await expect(drill(page)).toHaveAttribute("data-direction", "name_to_shape");
  const q = await question(page);
  const tones = chordTones(q.root, q.quality);

  await tapChord(page, tones);
  await expect(drill(page)).toHaveAttribute("data-tapped-count", String(tones.length));
  await page.locator(".check-shape").click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "1");
  await expect(answerStatement(page)).toContainText(tones.join(", "));
  const text = await answerStatement(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
});

test("name_to_shape: tapping too few tones grades incorrect and names the full chord", async ({
  page,
}) => {
  await page.locator(".direction-choice").nth(1).click();
  await startButton(page).click();
  const q = await question(page);
  const tones = chordTones(q.root, q.quality);

  // Every tone but the last one - a real triad/tetrad with a hole in it.
  await tapChord(page, tones.slice(0, -1));
  await page.locator(".check-shape").click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "0");
  await expect(answerStatement(page)).toContainText(tones.join(", "));
  const text = await answerStatement(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
});

test("a full cycle: prompt, answer, feedback, next, across a session", async ({ page }) => {
  await startButton(page).click();
  let q = await question(page);
  await page.locator(`.choice[data-root="${q.root}"][data-quality="${q.quality}"]`).click();
  await expect(drill(page)).toHaveAttribute("data-asked", "1");

  await page.locator(".next-question").click();
  await expect(progress(page)).toHaveText("1 chord, 1 answered correctly.");
  q = await question(page);
  expect(q.root).toBeTruthy();
  await expect(answerStatement(page)).toHaveCount(0);

  const wrongButton = wrongChoice(page, q.root, q.quality);
  await wrongButton.click();
  await expect(drill(page)).toHaveAttribute("data-asked", "2");
  await expect(drill(page)).toHaveAttribute("data-correct", "1");
  await expect(progress(page)).toHaveText("2 chords, 1 answered correctly.");

  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();
  await expect(page.locator(".logged")).toContainText("2 chords, 1 answered correctly.");
});

// ---------------------------------------------------------------------------
// The constraint model (#26): a narrowed region only offers what fits.
// ---------------------------------------------------------------------------

test("narrowing to open position drops a chord whose shape needs a higher fret, and keeps one that fits", async ({
  page,
}) => {
  // Region controls are only editable before the drill starts (like
  // fret-to-note's), so this is set first, then Start reveals the choices
  // it produced.
  await page.selectOption(".scope-end-fret", "2");
  await startButton(page).click();
  // The open G major shape frets as high as fret 3 - excluded from 0-2.
  await expect(page.locator('.choice[data-root="G"][data-quality="major"]')).toHaveCount(0);
  // The open E major shape fits entirely inside frets 0-2 - still offered.
  await expect(page.locator('.choice[data-root="E"][data-quality="major"]')).toHaveCount(1);
});

test("a fret range with nothing in it says so and offers no drill", async ({ page }) => {
  await page.selectOption(".scope-start-fret", "10");
  await page.selectOption(".scope-end-fret", "3");
  await expect(drill(page)).toHaveAttribute("data-askable", "0");
  await expect(page.locator(".narrow-scope")).toContainText("Nothing is selected");
  await expect(startButton(page)).toHaveCount(0);

  // Widening the range back out - both ends, not only the one that was
  // narrowed last, since an open-position chord's shape spans several
  // frets at once and needs the WHOLE range restored, not merely a wider
  // top - unlike a single fret-to-note position, which only ever needs one
  // fret in range.
  await page.selectOption(".scope-start-fret", "0");
  await page.selectOption(".scope-end-fret", "12");
  await expect(drill(page)).toHaveAttribute("data-askable", "1");
  await expect(startButton(page)).toBeVisible();
});

test("a key that shares no chord with the family preset is honestly unaskable", async ({
  page,
}) => {
  // Major & minor's open shapes only cover the roots E, A, D, G, C - and
  // F# major's own diatonic triads (F#, G#m, A#m, B, C#, D#m) share none of
  // those roots, so this combination has nothing to ask about.
  await page.locator(".key-enabled").check();
  await page.selectOption(".key-root", "F#");
  await page.selectOption(".key-quality", "major");
  await expect(drill(page)).toHaveAttribute("data-askable", "0");
  await expect(page.locator(".narrow-scope")).toContainText("Nothing is selected");

  // And turning the key off restores what it excluded.
  await page.locator(".key-enabled").uncheck();
  await expect(drill(page)).toHaveAttribute("data-askable", "1");
});

// ---------------------------------------------------------------------------
// Structured, queryable results (#32): every question is its own row.
// ---------------------------------------------------------------------------

test("every answered question is posted as a structured row, correct and incorrect alike", async ({
  page,
  request,
}) => {
  const before = (await (await request.get("/api/trainer/chord-attempts")).json()).total;

  await startButton(page).click();
  const q1 = await question(page);
  await page.locator(`.choice[data-root="${q1.root}"][data-quality="${q1.quality}"]`).click();
  await expect(drill(page)).toHaveAttribute("data-attempt-log-failures", "0");

  await page.locator(".next-question").click();
  const q2 = await question(page);
  const wrongButton = wrongChoice(page, q2.root, q2.quality);
  const wrongRoot = await wrongButton.getAttribute("data-root");
  const wrongQuality = await wrongButton.getAttribute("data-quality");
  await wrongButton.click();
  await expect(drill(page)).toHaveAttribute("data-attempt-log-failures", "0");

  await expect(async () => {
    const { total } = await (await request.get("/api/trainer/chord-attempts")).json();
    expect(total).toBe(before + 2);
  }).toPass({ timeout: 10_000 });

  const { attempts } = await (
    await request.get("/api/trainer/chord-attempts?limit=2")
  ).json();
  expect(attempts).toHaveLength(2);
  const [second, first] = attempts;

  expect(first.drill).toBe("chord_flashcards");
  expect(first.direction).toBe("shape_to_name");
  expect(first.target_root).toBe(q1.root);
  expect(first.target_quality).toBe(q1.quality);
  expect(first.given_root).toBe(q1.root);
  expect(first.given_quality).toBe(q1.quality);
  expect(first.correct).toBe(true);
  expect(first.given_notes).toBeNull();
  expect(first.given_shape).toBeNull();
  expect(Array.isArray(first.target_shape)).toBe(true);
  expect(first.target_shape.length).toBeGreaterThan(0);

  expect(second.target_root).toBe(q2.root);
  expect(second.target_quality).toBe(q2.quality);
  expect(second.given_root).toBe(wrongRoot);
  expect(second.given_quality).toBe(wrongQuality);
  expect(second.correct).toBe(false);

  const missed = await (
    await request.get("/api/trainer/chord-attempts?correct=false&limit=100")
  ).json();
  expect(missed.attempts.map((a) => a.id)).toContain(second.id);
  expect(missed.attempts.map((a) => a.id)).not.toContain(first.id);
});

test("stopping the drill also logs one chords practice session, with a human-readable summary", async ({
  page,
  request,
}) => {
  await startButton(page).click();
  const q = await question(page);
  await page.locator(`.choice[data-root="${q.root}"][data-quality="${q.quality}"]`).click();
  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();

  const { sessions, total } = await (
    await request.get("/api/practice/sessions?limit=1000")
  ).json();
  expect(total).toBe(1);
  expect(sessions[0].activity).toBe("chords");
  expect(sessions[0].local_date).toBe(today);
  expect(sessions[0].note).toBe(
    "Chord flash cards, shape to name. 1 chord, 1 answered correctly. Major & minor, frets 0-12.",
  );
  expect(forbiddenWord(sessions[0].note), sessions[0].note).toBeNull();
  expect(sessions[0].note).not.toMatch(/%/);
});

// ---------------------------------------------------------------------------
// The new seventh qualities (issue #252): the sevenths preset now offers
// minor7 and major7 alongside the dominant, drawn entirely from the new
// movable barre shapes - each one answered for real and read back through
// the API under its own quality string.
// ---------------------------------------------------------------------------

test("sevenths: a minor7 and a major7 card are each offered, answered, and logged with their own quality", async ({
  page,
  request,
}) => {
  await page.locator(".family-choice", { hasText: "Sevenths" }).click();
  await startButton(page).click();

  const answered = { minor7: null, major7: null };
  for (let i = 0; i < 40 && (!answered.minor7 || !answered.major7); i++) {
    const q = await question(page);
    if ((q.quality === "minor7" || q.quality === "major7") && !answered[q.quality]) {
      answered[q.quality] = q.root;
      await page.locator(`.choice[data-root="${q.root}"][data-quality="${q.quality}"]`).click();
      await expect(answerStatement(page)).toContainText(chordName(q.root, q.quality));
    } else {
      // Any other card (a dominant7, or a quality already captured) is
      // still answered so the drill advances - naming the shown chord
      // itself, which is always correct, since the point here is reaching
      // both new qualities, not grading every card along the way.
      await page.locator(`.choice[data-root="${q.root}"][data-quality="${q.quality}"]`).click();
    }
    await page.locator(".next-question").click();
  }

  expect(answered.minor7, "a minor7 card was drawn and answered within 40 questions").toBeTruthy();
  expect(answered.major7, "a major7 card was drawn and answered within 40 questions").toBeTruthy();

  for (const quality of ["minor7", "major7"]) {
    // The attempt POST is fire-and-forget from the page's own side (#110) -
    // nothing here ordered it after the click above, so this read must
    // retry rather than race it.
    await expect(async () => {
      const { attempts, total } = await (
        await request.get(
          // A root like F# has to be encoded - a bare "#" starts a URL
          // fragment and silently truncates everything after it, which is
          // exactly the intermittent failure a sharp root drew here first.
          `/api/trainer/chord-attempts?quality=${quality}&root=${encodeURIComponent(answered[quality])}`,
        )
      ).json();
      expect(total, `a logged ${quality} attempt`).toBeGreaterThan(0);
      expect(attempts[0].target_quality).toBe(quality);
      expect(attempts[0].target_root).toBe(answered[quality]);
      expect(attempts[0].correct).toBe(true);
    }).toPass({ timeout: 10_000 });
  }
});

test("leaving the page mid-drill still logs the practice", async ({ page, request }) => {
  await startButton(page).click();
  const q = await question(page);
  await page.locator(`.choice[data-root="${q.root}"][data-quality="${q.quality}"]`).click();

  await page.locator(".back").click();
  await expect(page.locator("section.drill")).toHaveCount(0);

  await expect(async () => {
    const { total } = await (await request.get("/api/practice/sessions?limit=1000")).json();
    expect(total).toBe(1);
  }).toPass({ timeout: 10_000 });
});
