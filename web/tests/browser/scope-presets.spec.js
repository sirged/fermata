// Named drill scopes (issue #236), against the real backend and the real
// build - the shared picker, both drills, and the practice row a drill run on
// a saved scope now writes.
//
// WHAT THESE READ AS GROUND TRUTH. The scope a drill is actually on is read
// off the section's own data-start-fret / data-end-fret / data-strings /
// data-key attributes, which the component sets from the state it really
// narrows questions with - never off the controls, which would only prove
// that a click moved a widget. And "the session carries the preset" is read
// back through the API rather than off the page, because the claim is about
// what was STORED: a component that shows the right id and posts nothing
// would pass any assertion made on the screen.
//
// The two drills are separate components with separate state, so "saved in
// one, offered in the other" is a real crossing rather than a re-render.
import { expect, test } from "@playwright/test";

import { forbiddenWord, localDay } from "../../src/lib/practice.js";

const drill = (page) => page.locator("section.drill");
const presets = (page) => page.locator(".presets");
const presetName = (page) => page.locator(".preset-name");
const savePreset = (page) => page.locator(".save-preset");

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
  // Presets last: a session referencing one is deleted above, so nothing here
  // depends on the ON DELETE SET NULL that the deletion test exercises.
  for (const preset of await (await request.get("/api/trainer/presets")).json()) {
    await request.delete(`/api/trainer/presets/${preset.id}`);
  }
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

/** Narrow the drill on screen by hand: a fret range, a string set, and a key.
 * Every control here is one a person uses; nothing is written into component
 * state from the test. */
async function narrowScope(page, { startFret, endFret, keepStrings, keyRoot, keyQuality }) {
  await page.locator(".scope-start-fret").selectOption(String(startFret));
  await page.locator(".scope-end-fret").selectOption(String(endFret));
  const buttons = page.locator(".string-choice");
  const count = await buttons.count();
  for (let i = 0; i < count; i += 1) {
    const button = buttons.nth(i);
    const number = Number(await button.textContent());
    const selected = (await button.getAttribute("class")).includes("active");
    if (selected && !keepStrings.includes(number)) await button.click();
  }
  await page.locator(".key-enabled").check();
  await page.locator(".key-root").selectOption(keyRoot);
  await page.locator(".key-quality").selectOption(keyQuality);
}

/** Save the scope on screen under a name, and wait for the save to land -
 * the id the drill then reports is the one the server actually assigned,
 * which is what every assertion downstream is about. */
async function saveScopeAs(page, name) {
  const before = Number(await presets(page).getAttribute("data-preset-count"));
  await presetName(page).fill(name);
  await savePreset(page).click();
  await expect(presets(page)).toHaveAttribute("data-preset-count", String(before + 1));
  await expect(drill(page)).not.toHaveAttribute("data-preset", "");
  return Number(await drill(page).getAttribute("data-preset"));
}

const NARROW = {
  startFret: 5,
  endFret: 9,
  keepStrings: [1, 2],
  keyRoot: "G",
  keyQuality: "major",
};

async function expectScopeOnScreen(page, scope) {
  await expect(drill(page)).toHaveAttribute("data-start-fret", String(scope.startFret));
  await expect(drill(page)).toHaveAttribute("data-end-fret", String(scope.endFret));
  await expect(drill(page)).toHaveAttribute(
    "data-strings",
    [...scope.keepStrings].sort((a, b) => a - b).join(","),
  );
  await expect(drill(page)).toHaveAttribute(
    "data-key",
    `${scope.keyRoot} ${scope.keyQuality}`,
  );
}

// ---------------------------------------------------------------------------
// Saved here, offered there.
// ---------------------------------------------------------------------------

test("a scope saved in one drill is offered in the other, and picking it restores the strings, the fret range and the key", async ({
  page,
}) => {
  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
  await narrowScope(page, NARROW);
  await expectScopeOnScreen(page, NARROW);

  await saveScopeAs(page, "Top two, fifth position");
  await expect(presets(page)).toHaveAttribute("data-preset-count", "1");
  // Saving is also choosing - the scope stored is the scope the drill is on.
  await expect(page.locator(".preset-choice")).toHaveClass(/active/);

  // The OTHER drill. A separate component with separate state, reached by a
  // real navigation, so what it shows is what it loaded from the server.
  await page.goto("/#/chords");
  await expect(drill(page)).toBeVisible();
  await expect(presets(page)).toHaveAttribute("data-preset-count", "1");
  const choice = page.locator('.preset-choice[data-preset-name="Top two, fifth position"]');
  await expect(choice).toBeVisible();

  // Before picking it, the chord drill is on its own untouched defaults -
  // which is what makes the assertions after the click mean something.
  await expect(drill(page)).toHaveAttribute("data-start-fret", "0");
  await expect(drill(page)).toHaveAttribute("data-key", "");

  await choice.click();
  await expectScopeOnScreen(page, NARROW);
  // The controls agree with the state, so a person sees what the drill is on
  // rather than a stale widget beside a narrowed drill.
  await expect(page.locator(".key-enabled")).toBeChecked();
  await expect(page.locator(".key-root")).toHaveValue("G");
  await expect(page.locator(".scope-start-fret")).toHaveValue("5");
  await expect(page.locator(".string-choice.active")).toHaveCount(2);
});

test("turning a scope control by hand stops the drill claiming it is still on the saved scope", async ({
  page,
}) => {
  // The rule a session's preset_id depends on: a preset is in force only
  // while the scope really is the one it describes.
  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
  await narrowScope(page, NARROW);
  await saveScopeAs(page, "Top two, fifth position");

  await page.locator(".scope-end-fret").selectOption("11");
  await expect(drill(page)).toHaveAttribute("data-preset", "");
  await expect(page.locator(".preset-choice.active")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// The practice row.
// ---------------------------------------------------------------------------

test("practice logged on a saved scope carries its id, and the note stops repeating what the id says", async ({
  page,
  request,
}) => {
  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
  await narrowScope(page, NARROW);
  const presetId = await saveScopeAs(page, "Top two, fifth position");
  expect(presetId).toBeGreaterThan(0);

  await page.locator(".start-drill").click();
  const note = await drill(page).getAttribute("data-question-note");
  await page.locator(`.choice[data-note="${note}"]`).click();
  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();

  const { sessions } = await (
    await request.get("/api/practice/sessions?limit=1000")
  ).json();
  expect(sessions.length).toBe(1);
  const [session] = sessions;
  expect(session.activity).toBe("fretboard");
  // The whole point of the bet: what was practised is a column.
  expect(session.preset_id).toBe(presetId);
  // And it is not ALSO a sentence. The counts stay - they say how the session
  // went, not what it was scoped to - but the fret range, the strings and the
  // key are no longer prose a reader would have to parse.
  expect(session.note).toContain("1 question");
  expect(session.note).not.toContain("frets");
  expect(session.note).not.toContain("key of");
  expect(forbiddenWord(session.note), session.note).toBeNull();
});

test("practice on a scope nobody named still says in words what it was narrowed to", async ({
  page,
  request,
}) => {
  // The other half of the same rule, and the reason the sentence was not
  // simply deleted: a session with no preset has no other trace of its scope.
  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
  await narrowScope(page, NARROW);
  await expect(drill(page)).toHaveAttribute("data-preset", "");

  await page.locator(".start-drill").click();
  const note = await drill(page).getAttribute("data-question-note");
  await page.locator(`.choice[data-note="${note}"]`).click();
  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();

  const { sessions } = await (
    await request.get("/api/practice/sessions?limit=1000")
  ).json();
  expect(sessions.length).toBe(1);
  expect(sessions[0].preset_id).toBeNull();
  expect(sessions[0].note).toContain("frets 5-9");
  expect(sessions[0].note).toContain("key of G major");
});

// ---------------------------------------------------------------------------
// Removing one.
// ---------------------------------------------------------------------------

test("removing a saved scope leaves the practice logged under it, without the reference", async ({
  page,
  request,
}) => {
  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
  await narrowScope(page, NARROW);
  const presetId = await saveScopeAs(page, "Top two, fifth position");

  await page.locator(".start-drill").click();
  const note = await drill(page).getAttribute("data-question-note");
  await page.locator(`.choice[data-note="${note}"]`).click();
  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();

  const before = (await (await request.get("/api/practice/sessions?limit=1000")).json())
    .sessions[0];
  expect(before.preset_id).toBe(presetId);

  await page.locator(".delete-preset").click();
  await expect(presets(page)).toHaveAttribute("data-preset-count", "0");
  await expect(drill(page)).toHaveAttribute("data-preset", "");
  expect(await (await request.get("/api/trainer/presets")).json()).toEqual([]);

  // The minutes are still there. Only the reference went.
  const after = (await (await request.get("/api/practice/sessions?limit=1000")).json())
    .sessions[0];
  expect(after.id).toBe(before.id);
  expect(after.seconds).toBe(before.seconds);
  expect(after.activity).toBe("fretboard");
  expect(after.preset_id).toBeNull();
});

test("a name already in use is refused in words rather than by making a second entry of it", async ({
  page,
}) => {
  await page.goto("/#/fretboard");
  await expect(drill(page)).toBeVisible();
  await saveScopeAs(page, "Top two, fifth position");
  await expect(presets(page)).toHaveAttribute("data-preset-count", "1");

  await presetName(page).fill("Top two, fifth position");
  await savePreset(page).click();
  await expect(page.locator(".preset-problem")).toBeVisible();
  await expect(presets(page)).toHaveAttribute("data-preset-count", "1");
  const problem = await page.locator(".preset-problem").textContent();
  expect(forbiddenWord(problem), problem).toBeNull();
});

test("the picker's own words are held to the practice vocabulary, saved and unsaved alike", async ({
  page,
}) => {
  await page.goto("/#/chords");
  await expect(drill(page)).toBeVisible();
  const empty = await presets(page).innerText();
  expect(forbiddenWord(empty), empty).toBeNull();

  await saveScopeAs(page, "Open position");
  const filled = await presets(page).innerText();
  expect(forbiddenWord(filled), filled).toBeNull();
});
