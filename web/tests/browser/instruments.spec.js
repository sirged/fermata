// The instruments editor, against the real backend and the real build.
//
// What these are for: the interesting parts of this feature cannot be reached
// from a unit test. Whether a click actually reaches the synthesiser depends on
// alphaTab constructing a player with no score loaded, on a soundfont arriving
// over HTTP, and on a hand-built MidiFile being one the synth accepts - none of
// which a mocked renderer would tell us anything about. So the assertions here
// are about the seams: the note names and frequencies that reach the screen,
// the values that survive a reload, and the fact that the audio path runs.
import { expect, test } from "@playwright/test";

const section = (page) => page.locator("section[data-instrument-count]");
const ownedRows = (page) => page.locator(".owned > li");
const editor = (page) => page.locator(".editor");
const editorRows = (page) => page.locator(".editor .strings li");
const savedRows = (page) => page.locator(".owned .strings li");
const editorLabels = (page) => page.locator(".editor label");
const presetPicker = (page) => page.locator(".start select");
const emptyNotice = (page) => page.locator(".empty");
// Shared so that every "there is no error" assertion uses the SAME selector one
// test proves can match something. A toHaveCount(0) built from an inline literal
// is permanently true the moment the class is renamed or mistyped, and reports
// nothing when it stops matching the thing it was written to watch.
const errors = (page) => section(page).locator(".error");

/** A string row as the interface presents it, from either the editor (where the
 * nominal pitch is an input) or a saved instrument (where it is text). */
function readRows(locator) {
  return locator.evaluateAll((rows) =>
    rows.map((r) => ({
      number: r.dataset.string,
      pitch:
        r.querySelector(".string-pitch")?.value.trim() ??
        r.querySelector(".string-pitch-fixed")?.textContent.trim() ??
        null,
      sounding: r.querySelector(".string-sounding")?.textContent.trim() ?? null,
      hz: r.querySelector(".string-hz")?.textContent.trim() ?? null,
      frequency: r.dataset.frequency,
    })),
  );
}

const STANDARD_AT_A440 = [
  { number: "6", pitch: "E2", hz: "82.41 Hz" },
  { number: "5", pitch: "A2", hz: "110.00 Hz" },
  { number: "4", pitch: "D3", hz: "146.83 Hz" },
  { number: "3", pitch: "G3", hz: "196.00 Hz" },
  { number: "2", pitch: "B3", hz: "246.94 Hz" },
  { number: "1", pitch: "E4", hz: "329.63 Hz" },
];

test.beforeEach(async ({ page, request }) => {
  // Refuses to touch anything that is not the throwaway instance this suite
  // starts. The cleanup below DELETES instruments, and the config is built to
  // never adopt an already-running server - but a hand-set baseURL or
  // FERMATA_TEST_PORT could still point these at a real install, and an empty
  // library is what a scratch instance has and a real one does not.
  const scores = await (await request.get("/api/scores")).json();
  expect(
    scores,
    "refusing to run: this backend has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and these tests delete instruments",
  ).toEqual([]);

  // An install has one set of instruments, so each test starts from none -
  // otherwise these pass only in the order they were written.
  const existing = await (await request.get("/api/instruments")).json();
  for (const instrument of existing) {
    await request.delete(`/api/instruments/${instrument.id}`);
  }

  // Independent evidence that a click reaches real audio machinery and not just
  // a counter in the component.
  await page.addInitScript(() => {
    window.__audioContexts = 0;
    for (const name of ["AudioContext", "webkitAudioContext"]) {
      const Original = window[name];
      if (!Original) continue;
      window[name] = class extends Original {
        constructor(...args) {
          super(...args);
          window.__audioContexts += 1;
        }
      };
    }
  });

  await page.goto("/#/settings");
  await expect(section(page)).toBeVisible();
});

async function choosePreset(page, key) {
  await page.selectOption(".start select", key);
  await expect(editor(page)).toBeVisible();
}

test("every preset is offered", async ({ page }) => {
  const values = await page.locator(".start select option").evaluateAll((options) =>
    options.map((o) => o.value).filter(Boolean),
  );
  expect(values).toEqual([
    "guitar-standard",
    "guitar-drop-d",
    "guitar-dadgad",
    "guitar-open-g",
    "guitar-seven-string",
    "bass-four-string",
    "bass-five-string",
    "ukulele",
    "violin",
    "viola",
    "cello",
  ]);
});

test("a preset loads, with a note name and a frequency for every string", async ({ page }) => {
  await choosePreset(page, "guitar-standard");
  await expect(page.locator('.editor input[type="text"]').first()).toHaveValue(
    "Guitar (standard)",
  );
  await expect(editorRows(page)).toHaveCount(6);
  const rows = await readRows(editorRows(page));
  expect(rows.map((r) => ({ number: r.number, pitch: r.pitch, hz: r.hz }))).toEqual(
    STANDARD_AT_A440,
  );
  // string numbers run opposite to list order, as everywhere else
  expect(rows.map((r) => r.number)).toEqual(["6", "5", "4", "3", "2", "1"]);
});

test("the reference pitch moves every string", async ({ page }) => {
  await choosePreset(page, "guitar-standard");
  const reference = page.locator('.editor label:has-text("Reference") input');
  await reference.fill("415");
  // A415, a common Baroque pitch: concert A is 103.75, and the low E follows
  // it down rather than staying at 82.41.
  await expect(editorRows(page).first().locator(".string-hz")).toHaveText("77.72 Hz");
  await expect(editorRows(page).nth(1).locator(".string-hz")).toHaveText("103.75 Hz");
});

test("clicking a string reaches the synthesiser", async ({ page }) => {
  const soundfontRequests = [];
  page.on("request", (r) => {
    if (/soundfont|\.sf2/.test(r.url())) soundfontRequests.push(r.url());
  });

  await choosePreset(page, "guitar-standard");
  await expect(section(page)).toHaveAttribute("data-audition-count", "0");

  await editorRows(page).first().locator("button.play").click();

  // The whole audio path has to run for this to change: a player built with no
  // score loaded, a soundfont fetched, and a hand-built MidiFile the synth
  // accepts. Generous timeout - the soundfont is about a megabyte.
  await expect(section(page)).toHaveAttribute("data-audition-count", "1", { timeout: 30_000 });
  await expect(section(page)).toHaveAttribute("data-audition-midi", "40");
  await expect(section(page)).toHaveAttribute("data-audition-pitch", "E2");
  await expect(page.locator(".sounding")).toContainText("E2");
  await expect(errors(page)).toHaveCount(0);

  expect(soundfontRequests.length).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.__audioContexts)).toBeGreaterThan(0);
});

test("a definition saves and survives a reload", async ({ page, request }) => {
  await choosePreset(page, "guitar-standard");
  await page.locator('.editor input[type="text"]').first().fill("Reload guitar");
  await page.locator(".actions button.primary").click();

  await expect(section(page)).toHaveAttribute("data-instrument-count", "1");
  await expect(ownedRows(page).first().locator(".owned-summary")).toHaveText(
    "6 strings · 22 frets · A440",
  );

  await page.reload();
  await expect(ownedRows(page).first().locator(".owned-name")).toHaveText("Reload guitar");

  // and it is the same tuning, not merely the same name
  const rows = await readRows(savedRows(page));
  expect(rows.map((r) => ({ number: r.number, pitch: r.pitch, hz: r.hz }))).toEqual(
    STANDARD_AT_A440,
  );

  // The numbers on screen for a SAVED instrument are the server's own, not the
  // browser's arithmetic - so they have to agree with what the API returns.
  const [saved] = await (await request.get("/api/instruments")).json();
  expect(saved.strings.map((s) => s.frequency.toFixed(2))).toEqual(
    rows.map((r) => Number(r.frequency).toFixed(2)),
  );
  expect(rows.map((r) => r.hz)).toEqual(saved.strings.map((s) => `${s.frequency.toFixed(2)} Hz`));
});

test("a capo changes what is sounded, not what is stored", async ({ page, request }) => {
  await choosePreset(page, "guitar-standard");
  await page.locator('.editor label:has-text("Capo") input').fill("5");

  // Both pitches, labelled: the nominal tuning a player works from and what
  // the capo actually makes. Sounding an open E with a capo at the fifth fret
  // would teach a reference wrong by five semitones.
  await expect(page.locator(".editor .capo-note")).toContainText("capo at fret 5");
  const rows = await readRows(editorRows(page));
  expect(rows.map((r) => `${r.pitch}->${r.sounding}`)).toEqual([
    "E2->A2",
    "A2->D3",
    "D3->G3",
    "G3->C4",
    "B3->E4",
    "E4->A4",
  ]);
  // the frequency shown is the one you hear
  expect(rows[0].hz).toBe("110.00 Hz");

  // and it is the sounding pitch that plays
  await editorRows(page).first().locator("button.play").click();
  await expect(section(page)).toHaveAttribute("data-audition-midi", "45", { timeout: 30_000 });
  await expect(section(page)).toHaveAttribute("data-audition-pitch", "A2");

  await page.locator('.editor input[type="text"]').first().fill("Capo guitar");
  await page.locator(".actions button.primary").click();
  await expect(section(page)).toHaveAttribute("data-instrument-count", "1");

  // Storage is the open, non-capo tuning - what <staff-tuning> records.
  const [saved] = await (await request.get("/api/instruments")).json();
  expect(saved.string_pitches).toEqual(["E2", "A2", "D3", "G3", "B3", "E4"]);
  expect(saved.capo).toBe(5);
  expect(saved.strings[0].pitch).toBe("E2");
  expect(saved.strings[0].sounding_pitch).toBe("A2");
});

test("an unfretted instrument has no fret fields and plays every string", async ({ page }) => {
  await choosePreset(page, "violin");
  // Anchored: the base selector is proved to match first, so these cannot pass
  // by matching nothing at all if a class is renamed.
  await expect(editorLabels(page)).not.toHaveCount(0);
  await expect(editorLabels(page).filter({ hasText: "Frets" })).toHaveCount(0);
  await expect(editorLabels(page).filter({ hasText: "Capo" })).toHaveCount(0);
  await expect(editorLabels(page).filter({ hasText: "Strings" })).toHaveCount(1);
  await expect(page.locator(".editor .fretless")).toContainText("no tablature");

  const rows = await readRows(editorRows(page));
  expect(rows.map((r) => `${r.pitch} ${r.hz}`)).toEqual([
    "G3 196.00 Hz",
    "D4 293.66 Hz",
    "A4 440.00 Hz",
    "E5 659.26 Hz",
  ]);

  // Playing each string on its own is the primary control here - with no fret
  // to aim at, a heard pitch is what a player matches against.
  for (const [index, midi] of [55, 62, 69, 76].entries()) {
    await editorRows(page).nth(index).locator("button.play").click();
    await expect(section(page)).toHaveAttribute("data-audition-midi", String(midi), {
      timeout: 30_000,
    });
  }
  await expect(section(page)).toHaveAttribute("data-audition-count", "4");
});

test("a saved instrument's strings play without opening the editor", async ({ page }) => {
  // The point of the audible reference is that it is one click away, not
  // something you have to enter an edit mode to reach.
  //
  // Capo'd on purpose, so this covers the same one-line boundary the capo test
  // covers but from the other direction - a saved instrument's row rather than
  // the editor. One test guarding it is a coverage depth of one.
  await choosePreset(page, "guitar-standard");
  await page.locator('.editor label:has-text("Capo") input').fill("3");
  // choosePreset asserted the editor visible, so the same locator going to zero
  // below is a real disappearance rather than a selector that never matched.
  await expect(editor(page)).toBeVisible();
  await page.locator(".actions button.primary").click();
  await expect(section(page)).toHaveAttribute("data-instrument-count", "1");
  await expect(editor(page)).toHaveCount(0);

  // string 6 is nominally E2 (40) and sounds G2 (43) with the capo at fret 3
  await savedRows(page).first().locator("button.play").click();
  await expect(section(page)).toHaveAttribute("data-audition-midi", "43", { timeout: 30_000 });
  await expect(section(page)).toHaveAttribute("data-audition-pitch", "G2");
  await expect(page.locator(".sounding")).toContainText("G2");
});

test("the audition readout does not outlive what it describes", async ({ page }) => {
  await choosePreset(page, "violin");
  await page.locator(".actions button.primary").click();
  await expect(section(page)).toHaveAttribute("data-instrument-count", "1");

  await savedRows(page).first().locator("button.play").click();
  const readout = page.locator(".sounding");
  await expect(readout).toContainText("G3", { timeout: 30_000 });
  // and it names WHICH instrument sounded - the premise of the feature is
  // owning the same guitar in two tunings
  await expect(readout).toContainText("Violin");

  await ownedRows(page).first().locator("button", { hasText: "Delete" }).click();
  await expect(section(page)).toHaveAttribute("data-instrument-count", "0");
  // it used to still say "Sounding Violin, string 4" beside "No instruments yet"
  await expect(readout).toHaveCount(0);
});

test("a failed delete says so", async ({ page }) => {
  await choosePreset(page, "ukulele");
  await page.locator(".actions button.primary").click();
  await expect(ownedRows(page)).toHaveCount(1);

  await page.route("**/api/instruments/*", (route) =>
    route.request().method() === "DELETE" ? route.abort("failed") : route.continue(),
  );
  await ownedRows(page).first().locator("button", { hasText: "Delete" }).click();

  // Delete is reachable with no editor open, so an error rendered inside the
  // editor was invisible and the row just sat there unexplained.
  await expect(errors(page)).toHaveCount(1);
  await expect(ownedRows(page)).toHaveCount(1);
});

test("a reference pitch outside the bounds is refused rather than silently defaulted", async ({
  page,
  request,
}) => {
  await choosePreset(page, "guitar-standard");
  await page.locator('.editor label:has-text("Reference") input').fill("-100");

  // No frequency is invented for a reference that cannot be used, and Save is
  // not offered - it used to show every string at -18.73 Hz and then store 440.
  await expect(errors(page)).toContainText("between 300 and 600");
  await expect(editorRows(page).first().locator(".string-hz")).toHaveText("—");
  await expect(page.locator(".actions button.primary")).toBeDisabled();

  await page.locator('.editor label:has-text("Reference") input').fill("415");
  await expect(errors(page)).toHaveCount(0);
  await expect(editorRows(page).first().locator(".string-hz")).toHaveText("77.72 Hz");
  await page.locator(".actions button.primary").click();

  const [saved] = await (await request.get("/api/instruments")).json();
  expect(saved.reference_pitch).toBe(415);
});

test("a failed load is retryable", async ({ page }) => {
  // One hiccup must not leave the section permanently empty. A preset is the
  // only way to start a definition, so a cached rejection would mean an empty
  // list above an empty dropdown until a full page reload - and navigating away
  // and back would hand back the same failure.
  let failing = true;
  await page.route("**/api/instruments/presets", (route) =>
    failing ? route.abort("failed") : route.continue(),
  );
  await page.reload();

  // Built from the same `.error` helper the negative assertions use, so that
  // selector is proved to match something somewhere in this suite.
  const failure = errors(page).filter({ has: page.locator("button") });
  await expect(failure).toBeVisible();
  await expect(errors(page)).toHaveCount(1);
  // and it does not claim there is simply nothing here
  // Both anchored: the picker is asserted visible further down this same test,
  // and .empty is asserted visible in the delete test through the same helper.
  await expect(presetPicker(page)).toHaveCount(0);
  await expect(emptyNotice(page)).toHaveCount(0);

  failing = false;
  await failure.locator("button").click();
  await expect(presetPicker(page)).toBeVisible();
  await expect(failure).toHaveCount(0);
  await expect(presetPicker(page).locator("option")).toHaveCount(12);
});

test("a definition can be deleted", async ({ page }) => {
  await choosePreset(page, "ukulele");
  await page.locator(".actions button.primary").click();
  await expect(section(page)).toHaveAttribute("data-instrument-count", "1");

  await ownedRows(page).first().locator("button", { hasText: "Delete" }).click();
  await expect(section(page)).toHaveAttribute("data-instrument-count", "0");
  await expect(emptyNotice(page)).toBeVisible();
});
