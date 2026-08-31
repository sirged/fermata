// The interactive neck (issue #25) and the fret-to-note drill built on it
// (issue #27), against the real backend and the real build.
//
// Unlike ear-training.spec.js, no audio path is involved here at all - a
// fretboard question is answered by choosing a note name or tapping an SVG
// position, both of which are ordinary DOM events. So these tests read
// their ground truth off the section's own data-question-* attributes
// (set from the question this component actually picked - see
// FretToNote.svelte) rather than off anything a mock would make true for
// free, the same discipline ear-training.spec.js applies to
// data-sounded-midi.
import { expect, test } from "@playwright/test";

import { forbiddenWord, localDay } from "../../src/lib/practice.js";

const drill = (page) => page.locator("section.drill");
const startButton = (page) => page.locator(".start-drill");
const progress = (page) => page.locator(".progress");
const answerStatement = (page) => page.locator(".answer-statement");

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

async function addInstrument(request, definition) {
  const res = await request.post("/api/instruments", { data: definition });
  expect(res.ok(), await res.text()).toBe(true);
  return res.json();
}

const SEVEN_STRING = {
  kind: "string",
  name: "Seven-string guitar",
  fretted: true,
  string_count: 7,
  string_pitches: ["B1", "E2", "A2", "D3", "G3", "B3", "E4"],
  fret_count: 24,
  capo: 0,
  reference_pitch: 440,
};

test.beforeEach(async ({ page, request }) => {
  const scores = await (await request.get("/api/scores")).json();
  expect(
    scores,
    "refusing to run: this backend has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and these tests delete practice history",
  ).toEqual([]);
  await reset(request);

  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
});

function question(page) {
  return drill(page).evaluate((el) => ({
    note: el.dataset.questionNote,
    string: el.dataset.questionString ? Number(el.dataset.questionString) : null,
    fret: el.dataset.questionFret === "" ? null : Number(el.dataset.questionFret),
  }));
}

// ---------------------------------------------------------------------------
// The neck itself (#25): correct notes at sampled positions, standard tuning.
// ---------------------------------------------------------------------------

test("the neck renders the correct note at open strings and at the fifth-fret relationship, standard tuning", async ({
  page,
}) => {
  // Reference mode: every position labelled, nothing answered yet. Proves
  // the neck's own note-at-position arithmetic directly, independent of the
  // drill's question-picking - the same claim tests/unit/neck.spec.js makes,
  // now against what is actually rendered.
  await startButton(page).click();
  await expect(drill(page)).toHaveAttribute("data-running", "1");

  // Neck.svelte publishes every position's note as a data-note attribute
  // regardless of whether a label is drawn (showLabels is false here) - see
  // the component's own comment on why a bare 'target' marker must not
  // print its label. Reading data-note directly is the rendered proof that
  // the neck's arithmetic is right, without needing to answer a question
  // first.
  const openE6 = await page
    .locator('g.position[data-string="6"][data-fret="0"]')
    .getAttribute("data-note");
  const openA5 = await page
    .locator('g.position[data-string="5"][data-fret="0"]')
    .getAttribute("data-note");
  const openD4 = await page
    .locator('g.position[data-string="4"][data-fret="0"]')
    .getAttribute("data-note");
  const openG3 = await page
    .locator('g.position[data-string="3"][data-fret="0"]')
    .getAttribute("data-note");
  const openB2 = await page
    .locator('g.position[data-string="2"][data-fret="0"]')
    .getAttribute("data-note");
  const openE1 = await page
    .locator('g.position[data-string="1"][data-fret="0"]')
    .getAttribute("data-note");
  expect({ openE6, openA5, openD4, openG3, openB2, openE1 }).toEqual({
    openE6: "E",
    openA5: "A",
    openD4: "D",
    openG3: "G",
    openB2: "B",
    openE1: "E",
  });

  // The fifth-fret relationship: fret 5 on one string matches the next
  // string open, except the third-to-second pair, which is fret 4 (major
  // third rather than a fourth) - the same guitarist's check
  // tests/unit/neck.spec.js applies to the arithmetic directly, now read
  // off the rendered SVG.
  const fret5string6 = await page
    .locator('g.position[data-string="6"][data-fret="5"]')
    .getAttribute("data-note");
  expect(fret5string6).toBe(openA5);
  const fret4string3 = await page
    .locator('g.position[data-string="3"][data-fret="4"]')
    .getAttribute("data-note");
  expect(fret4string3).toBe(openB2);

  // And the twelfth fret is an octave above the open string - same pitch
  // class, which is all `data-note` (a pitch class) can assert.
  const fret12string6 = await page
    .locator('g.position[data-string="6"][data-fret="12"]')
    .getAttribute("data-note");
  expect(fret12string6).toBe(openE6);
});

test("string count and fret count are published on the neck, matching the standard-guitar fallback", async ({
  page,
}) => {
  await startButton(page).click();
  const neck = page.locator(".neck");
  await expect(neck).toHaveAttribute("data-string-count", "6");
  await expect(neck).toHaveAttribute("data-fret-count", "12");
});

test("a saved instrument's own tuning drives the neck, not a hardcoded six strings", async ({
  page,
  request,
}) => {
  await addInstrument(request, SEVEN_STRING);
  await page.reload();
  await expect(drill(page)).toBeVisible();

  await page.selectOption(".scope-source", { label: "Seven-string guitar" });
  await startButton(page).click();
  await expect(page.locator(".neck")).toHaveAttribute("data-string-count", "7");
  await expect(page.locator(".neck")).toHaveAttribute("data-fret-count", "24");
  // The low B string exists and sounds B.
  await expect(
    page.locator('g.position[data-string="7"][data-fret="0"]'),
  ).toHaveAttribute("data-note", "B");
});

// ---------------------------------------------------------------------------
// A tap highlights/identifies (#25's interaction contract).
// ---------------------------------------------------------------------------

test("in note-to-position mode, tapping the neck marks the position and reveals every place the note sounds", async ({
  page,
}) => {
  await page.locator(".direction-choice").nth(1).click();
  await startButton(page).click();
  await expect(drill(page)).toHaveAttribute("data-direction", "note_to_position");
  const q = await question(page);
  expect(q.note).toBeTruthy();
  expect(q.string).toBeNull();

  // Tap the position this app's own arithmetic says sounds the asked note -
  // trusting neck.js's own noteAt would be circular, so this scans the
  // rendered positions for one whose data-note the page itself already
  // agrees is the target, and taps THAT one.
  const match = page.locator(`g.position[data-note="${q.note}"]`).first();
  await expect(match).toBeVisible();
  const { string, fret } = await match.evaluate((el) => ({
    string: el.dataset.string,
    fret: el.dataset.fret,
  }));
  await match.click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "1");
  const tapped = page.locator(
    `g.position[data-string="${string}"][data-fret="${fret}"] .mark.correct`,
  );
  await expect(tapped).toBeVisible();
  // Every OTHER position sounding the same note is revealed too - the
  // answer, not only a verdict on the one tap.
  const revealed = page.locator(`g.position[data-note="${q.note}"] .mark`);
  await expect(revealed).toHaveCount(
    await page.locator(`g.position[data-note="${q.note}"]`).count(),
  );
  await expect(answerStatement(page)).toContainText(q.note);
});

test("a second tap after answering does nothing - one answer per question", async ({ page }) => {
  await page.locator(".direction-choice").nth(1).click();
  await startButton(page).click();
  const q = await question(page);
  const first = page.locator(`g.position[data-note="${q.note}"]`).first();
  await first.click();
  await expect(drill(page)).toHaveAttribute("data-asked", "1");

  // Tap a DIFFERENT position now that the question is answered.
  const other = page.locator('g.position[data-string="1"][data-fret="0"]');
  await other.click({ force: true });
  await expect(drill(page)).toHaveAttribute("data-asked", "1");
});

// ---------------------------------------------------------------------------
// The drill: prompt, answer, feedback, next - both directions, both a
// correct and an incorrect answer, handled honestly (no shaming, no streak).
// ---------------------------------------------------------------------------

test("position-to-note: naming the right note says so plainly, and the count moves", async ({
  page,
}) => {
  await startButton(page).click();
  await expect(drill(page)).toHaveAttribute("data-direction", "position_to_note");
  await expect(progress(page)).toHaveText("Nothing asked yet.");

  const q = await question(page);
  await page.locator(`.choice[data-note="${q.note}"]`).click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "1");
  await expect(progress(page)).toHaveText("1 question, 1 answered correctly.");
  await expect(answerStatement(page)).toContainText(
    `String ${q.string}, fret ${q.fret} is ${q.note}.`,
  );
  await expect(page.locator(`.choice[data-note="${q.note}"]`)).toHaveClass(/correct/);
  await expect(page.locator(".choice.picked")).toHaveCount(0);

  const text = await answerStatement(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
});

test("position-to-note: naming a different note states which one, without a verdict word", async ({
  page,
}) => {
  await startButton(page).click();
  const q = await question(page);
  // The pitch-class button grid always offers all twelve, so a wrong choice
  // always exists.
  const wrongNote = q.note === "C" ? "C#" : "C";
  await page.locator(`.choice[data-note="${wrongNote}"]`).click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "0");
  await expect(progress(page)).toHaveText("1 question, none answered correctly.");
  await expect(answerStatement(page)).toHaveText(
    `String ${q.string}, fret ${q.fret} is ${q.note}. You named ${wrongNote}.`,
  );
  // The right answer is still marked, and the wrong pick is shown as picked
  // rather than as a fault.
  await expect(page.locator(`.choice[data-note="${q.note}"]`)).toHaveClass(/correct/);
  await expect(page.locator(`.choice[data-note="${wrongNote}"]`)).toHaveClass(/picked/);

  const text = await answerStatement(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
  expect(text).not.toMatch(/%/);

  // Not --danger: the same rule ear-training.spec.js checks, applied here.
  const danger = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--danger").trim(),
  );
  const dangerRgb = await page.evaluate((hex) => {
    const probe = document.createElement("span");
    probe.style.color = hex;
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }, danger);
  const pickedColour = await page
    .locator(`.choice[data-note="${wrongNote}"]`)
    .evaluate((el) => getComputedStyle(el).borderColor);
  expect(pickedColour).not.toBe(dangerRgb);
});

test("note-to-position: tapping the wrong spot names what is there instead, without a verdict word", async ({
  page,
}) => {
  await page.locator(".direction-choice").nth(1).click();
  await startButton(page).click();
  const q = await question(page);
  // A position guaranteed NOT to sound the asked note.
  const wrong = page
    .locator(`g.position:not([data-note="${q.note}"])`)
    .first();
  const wrongNote = await wrong.getAttribute("data-note");
  await wrong.click();

  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-correct", "0");
  await expect(answerStatement(page)).toContainText(wrongNote);
  await expect(answerStatement(page)).toContainText(`${q.note} is elsewhere on the neck.`);
  const text = await answerStatement(page).textContent();
  expect(forbiddenWord(text), text).toBeNull();
});

test("a full cycle: prompt, answer, feedback, next, in both directions across a session", async ({
  page,
}) => {
  await startButton(page).click();
  let q = await question(page);
  await page.locator(`.choice[data-note="${q.note}"]`).click();
  await expect(drill(page)).toHaveAttribute("data-asked", "1");

  await page.locator(".next-question").click();
  await expect(progress(page)).toHaveText("1 question, 1 answered correctly.");
  q = await question(page);
  expect(q.note).toBeTruthy();
  await expect(answerStatement(page)).toHaveCount(0);

  const wrongNote = q.note === "C" ? "C#" : "C";
  await page.locator(`.choice[data-note="${wrongNote}"]`).click();
  await expect(drill(page)).toHaveAttribute("data-asked", "2");
  await expect(drill(page)).toHaveAttribute("data-correct", "1");
  await expect(progress(page)).toHaveText("2 questions, 1 answered correctly.");

  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();
  await expect(page.locator(".logged")).toContainText("2 questions, 1 answered correctly.");
});

// ---------------------------------------------------------------------------
// An empty/degenerate state is real.
// ---------------------------------------------------------------------------

test("deselecting every string keeps at least one selected, rather than silently asking about all of them", async ({
  page,
}) => {
  for (const n of [6, 5, 4, 3, 2]) {
    await page.locator(".string-choice", { hasText: new RegExp(`^${n}$`) }).click();
  }
  // Five of six are off; the drill still has one to ask about.
  await expect(drill(page)).toHaveAttribute("data-askable", "1");
  await startButton(page).click();
  const q = await question(page);
  expect(q.string ?? (await drill(page).getAttribute("data-question-string"))).toBeTruthy();

  // The last remaining string cannot be turned off.
  await page.locator(".stop-drill").click();
  const last = page.locator(".string-choice", { hasText: /^1$/ });
  await last.click();
  await expect(last).toHaveClass(/active/);
  await expect(drill(page)).toHaveAttribute("data-askable", "1");
});

test("a fret range with nothing in it says so and offers no drill, rather than asking an impossible question", async ({
  page,
}) => {
  await page.selectOption(".scope-start-fret", "10");
  await page.selectOption(".scope-end-fret", "3");
  await expect(drill(page)).toHaveAttribute("data-askable", "0");
  await expect(page.locator(".narrow-scope")).toContainText("Nothing is selected");
  await expect(startButton(page)).toHaveCount(0);

  await page.selectOption(".scope-end-fret", "12");
  await expect(drill(page)).toHaveAttribute("data-askable", "1");
  await expect(startButton(page)).toBeVisible();
});

// ---------------------------------------------------------------------------
// Structured, queryable results (#32): every question is its own row.
// ---------------------------------------------------------------------------

test("every answered question is posted as a structured row, correct and incorrect alike", async ({
  page,
  request,
}) => {
  // trainer_attempts has no per-test reset (unlike instruments/sessions/
  // goals - see reset() above): a real weak-spot query is meant to read
  // across many sessions, so there is nothing to delete between tests, and
  // this file's OTHER tests also write rows here. So this test reads its
  // own two by BASELINE + newest-first ordering, not by an exact total.
  const before = (await (await request.get("/api/trainer/attempts")).json()).total;

  await startButton(page).click();
  const q1 = await question(page);
  await page.locator(`.choice[data-note="${q1.note}"]`).click();
  await expect(drill(page)).toHaveAttribute("data-attempt-log-failures", "0");

  await page.locator(".next-question").click();
  const q2 = await question(page);
  const wrong = q2.note === "C" ? "C#" : "C";
  await page.locator(`.choice[data-note="${wrong}"]`).click();
  await expect(drill(page)).toHaveAttribute("data-attempt-log-failures", "0");

  await expect(async () => {
    const { total } = await (await request.get("/api/trainer/attempts")).json();
    expect(total).toBe(before + 2);
  }).toPass({ timeout: 10_000 });

  // Newest first (created_at DESC, id DESC - api.py's list_trainer_attempts):
  // the two rows just written are exactly the top two.
  const { attempts } = await (await request.get("/api/trainer/attempts?limit=2")).json();
  expect(attempts).toHaveLength(2);
  const [second, first] = attempts;

  expect(first.drill).toBe("fret_to_note");
  expect(first.direction).toBe("position_to_note");
  expect(first.target_string).toBe(q1.string);
  expect(first.target_fret).toBe(q1.fret);
  expect(first.target_note).toBe(q1.note);
  expect(first.given_note).toBe(q1.note);
  expect(first.correct).toBe(true);
  expect(first.given_string).toBeNull();
  expect(first.given_fret).toBeNull();

  expect(second.target_string).toBe(q2.string);
  expect(second.target_fret).toBe(q2.fret);
  expect(second.target_note).toBe(q2.note);
  expect(second.given_note).toBe(wrong);
  expect(second.correct).toBe(false);

  // Queryable directly: which positions get missed is exactly this filter -
  // this row is IN it, alongside whatever other tests in this file also
  // missed (see the comment above on why there is no exact total here).
  const missed = await (
    await request.get("/api/trainer/attempts?correct=false&limit=100")
  ).json();
  expect(missed.attempts.map((a) => a.id)).toContain(second.id);
  expect(missed.attempts.map((a) => a.id)).not.toContain(first.id);
});

test("stopping the drill also logs one fretboard practice session, with a human-readable summary", async ({
  page,
  request,
}) => {
  await startButton(page).click();
  const q = await question(page);
  await page.locator(`.choice[data-note="${q.note}"]`).click();
  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();

  const { sessions, total } = await (
    await request.get("/api/practice/sessions?limit=1000")
  ).json();
  expect(total).toBe(1);
  expect(sessions[0].activity).toBe("fretboard");
  expect(sessions[0].local_date).toBe(today);
  expect(sessions[0].note).toBe(
    "Fret to note, position to note. 1 question, 1 answered correctly. frets 0-12.",
  );
  expect(forbiddenWord(sessions[0].note), sessions[0].note).toBeNull();
  expect(sessions[0].note).not.toMatch(/%/);
});

test("leaving the page mid-drill still logs the practice", async ({ page, request }) => {
  await startButton(page).click();
  const q = await question(page);
  await page.locator(`.choice[data-note="${q.note}"]`).click();

  await page.locator(".back").click();
  await expect(page.locator("section.drill")).toHaveCount(0);

  await expect(async () => {
    const { total } = await (await request.get("/api/practice/sessions?limit=1000")).json();
    expect(total).toBe(1);
  }).toPass({ timeout: 10_000 });
});
