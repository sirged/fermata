// Key-aware spelling and accidentals (#185), end to end against the real
// alphaTab render and the real editor/document.js parse - the same fixture shape
// and stub the earlier editor suites use (score-editor.spec.js,
// fixtures/editor-score.js), only the HTTP transport stubbed. Every assertion is
// on state the app publishes onto the DOM (data-editor-*), the read-only
// window.__scoreEditor hook, or the exact MusicXML persisted through the save
// path - never on an internal reached into.
//
// The knife-edge this whole increment turns on: re-spelling a note changes its
// <step>, <alter> and printed <accidental> but MUST leave the SOUNDING pitch
// (MIDI) identical - F sharp 4 and G flat 4 are one key on the instrument. Every
// test below asserts the sounding MIDI is unchanged across the re-spelling, and
// the divergence guard (the renderer's own read of the pitch against the
// document's) stays green - the Rule 10 mirror is the oracle for exactly this.
//
// What each mutation would turn red, so this suite is falsifiable, not green by
// construction:
//   - force a sharp spelling regardless of key (drop the key-aware path): the
//     flat-key test's <step>/<alter>/<accidental> assertions go red (it spells
//     F sharp where the key wants G flat).
//   - drop the accidental-in-force carry: the "carries in the bar" test reds (a
//     later same-sound note spells F sharp again instead of G flat).
//   - stop resetting at the barline: the "the barline resets it" assertion reds.
//   - let the enharmonic cycle change the alter without re-solving the octave, or
//     otherwise move the sound: the cycle test's MIDI-unchanged assertion reds.
//   - write an <accidental> that disagrees with <alter>: the consistency
//     assertions on the persisted MusicXML go red.
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, keyedEditorScore, stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");
const fretInput = (page) => page.locator(".edit-fields input");
const stringSelect = (page) => page.locator(".edit-fields label", { hasText: "String" }).locator("select");
const accidentalSelect = (page) => page.locator(".edit-fields label", { hasText: "Accidental" }).locator("select");
const enharmonicButton = (page) => page.locator(".edit-fields .enharmonic");

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

// Opens the editor over `content` and waits until the whole score's `expected`
// sounding notes are laid out - the barrier every read below waits behind.
async function openEditor(page, content, expected) {
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(expected);
  return handle;
}

async function selectNote(page, ordinal) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
}

async function setFret(page, value) {
  await fretInput(page).fill(String(value));
  await fretInput(page).blur();
}

// The spelling of a persisted note by its Rule 17 id: its <step>, its <alter>
// (0 when absent), its <octave>, its printed <accidental> (or null), and the
// MIDI those <step>/<alter>/<octave> sound - computed in the page so the test
// asserts the sound the SPELLING implies, not a number handed to it.
async function spellingById(page, xml, id) {
  return page.evaluate(
    ({ xml, id }) => {
      const STEP = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const note = [...doc.getElementsByTagName("note")].find((n) => n.getAttribute("id") === id);
      if (!note) return null;
      const pitch = [...note.children].find((c) => c.tagName === "pitch");
      const step = pitch?.getElementsByTagName("step")[0]?.textContent ?? null;
      const alterEl = pitch ? [...pitch.children].find((c) => c.tagName === "alter") : null;
      const alter = alterEl ? Number(alterEl.textContent) : 0;
      const octave = Number(pitch?.getElementsByTagName("octave")[0]?.textContent);
      const accEl = [...note.children].find((c) => c.tagName === "accidental");
      const midi = step != null ? 12 * (octave + 1) + STEP[step] + alter : null;
      return { step, alter, octave, accidental: accEl ? accEl.textContent.trim() : null, midi };
    },
    { xml, id },
  );
}

async function save(page) {
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
}

// The selected note's sounding MIDI, as the app publishes it (the renderer's own
// read when it has one) - what every test asserts is invariant across a
// re-spelling.
async function selectedMidi(page) {
  return Number(await wrap(page).getAttribute("data-editor-selected-midi"));
}

test.describe("note editor - key-aware spelling (#185)", () => {
  // Target 1 (flat key): a recomputed pitch in a FLAT key spells with a flat, and
  // the sounding pitch is the same key on the instrument as its sharp spelling.
  test("a flat key spells a recomputed black key with a flat, same sound as the sharp", async ({ page }) => {
    // A flat major (fifths -4). MIDI 66 is F sharp / G flat; the key wants G flat.
    const { saved } = await openEditor(page, keyedEditorScore(-4), 8);
    await selectNote(page, 0); // E4, string 1 fret 0
    await setFret(page, 2); // E4 + 2 = MIDI 66

    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "G");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "-1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "flat");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    const n = await spellingById(page, saved.content, "n1-1-0-0");
    // Spelled a flat, and it sounds MIDI 66 - the very pitch F#4 (also 66) sounds.
    expect(n).toMatchObject({ step: "G", alter: -1, accidental: "flat", octave: 4, midi: 66 });
    expect(12 * (4 + 1) + 5 + 1).toBe(66); // F#4 = MIDI 66: same sound, different spelling
  });

  // Target 1 (sharp key): the same operation in a SHARP key spells with a sharp.
  test("a sharp key spells a recomputed black key with a sharp, same sound as the flat", async ({ page }) => {
    // D major (fifths +2). MIDI 68 is G sharp / A flat; the key wants G sharp.
    const { saved } = await openEditor(page, keyedEditorScore(2), 8);
    await selectNote(page, 0); // E4, string 1 fret 0
    await setFret(page, 4); // E4 + 4 = MIDI 68

    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "68");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "G");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "sharp");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    const n = await spellingById(page, saved.content, "n1-1-0-0");
    expect(n).toMatchObject({ step: "G", alter: 1, accidental: "sharp", octave: 4, midi: 68 });
    expect(12 * (4 + 1) + 9 - 1).toBe(68); // Ab4 = MIDI 68: same sound, different spelling
  });

  // Target 2: an accidental in force earlier in the bar carries to a later
  // same-step/octave note; the barline resets it. Same sounding pitch throughout.
  test("an accidental in force carries within the bar and resets at the barline", async ({ page }) => {
    // C major. Give note 1 (F#4) a printed flat, so G flat is in force on G4.
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 1);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66");
    await accidentalSelect(page).selectOption("-1"); // spell it G flat
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "G");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "flat");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66");

    // Note 3 (measure 1) recomputed to the SAME sound (MIDI 66): it carries the
    // flat - spelled G flat with NO fresh accidental - rather than F sharp.
    await selectNote(page, 3); // A4, string 1 fret 5
    await setFret(page, 2); // E4 + 2 = MIDI 66
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "G");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "-1");
    await expect(wrap(page)).not.toHaveAttribute("data-editor-selected-accidental"); // carries, none printed
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    // A note in the NEXT measure recomputed to the same sound spells F sharp with
    // a printed sharp - the barline reset the in-force flat.
    await selectNote(page, 4); // measure 2, B3 string 2 fret 0
    await setFret(page, 7); // B3 + 7 = MIDI 66
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "F");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "sharp");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    const carried = await spellingById(page, saved.content, "n1-1-3-0");
    const reset = await spellingById(page, saved.content, "n2-1-0-0");
    expect(carried).toMatchObject({ step: "G", alter: -1, accidental: null, midi: 66 });
    expect(reset).toMatchObject({ step: "F", alter: 1, accidental: "sharp", midi: 66 });
  });
});

test.describe("note editor - explicit accidental (#185)", () => {
  // Target 3: setting an accidental explicitly writes a consistent <alter> +
  // <accidental> and does not change the sounding pitch.
  test("an explicit accidental writes a consistent alter and accidental, same sound", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 1); // F#4, MIDI 66
    const before = await selectedMidi(page);
    expect(before).toBe(66);

    // Spell it as a flat.
    await accidentalSelect(page).selectOption("-1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "G");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "-1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "flat");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66"); // sound unchanged
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");

    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    let n = await spellingById(page, saved.content, "n1-1-1-0");
    expect(n).toMatchObject({ step: "G", alter: -1, accidental: "flat", midi: 66 });

    // Spell it back as a sharp - alter and accidental stay mutually consistent,
    // sound still MIDI 66.
    await selectNote(page, 1);
    await accidentalSelect(page).selectOption("1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "F");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "sharp");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66");
    await save(page);
    n = await spellingById(page, saved.content, "n1-1-1-0");
    expect(n).toMatchObject({ step: "F", alter: 1, accidental: "sharp", midi: 66 });
  });

  // Target 3 (refusal): an accidental that cannot spell the pitch (a natural of a
  // black key) is refused, the note untouched.
  test("an accidental that cannot spell the pitch is refused", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 1); // F#4 - no natural spelling of this black key
    await accidentalSelect(page).selectOption("0"); // ask for a natural

    await expect(wrap(page)).toHaveAttribute("data-editor-warn", /spelling/);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "F"); // unchanged
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "66");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });
});

test.describe("note editor - enharmonic cycle (#185)", () => {
  // Target 4: cycling the enharmonic alternatives moves through the spellings
  // (F sharp <-> G flat) with the sounding pitch unchanged at every step; the
  // re-import round-trips and the divergence guard stays green.
  test("cycling the enharmonic spelling keeps the sounding pitch and round-trips", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 1); // F#4, MIDI 66
    expect(await selectedMidi(page)).toBe(66);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "F");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "1");

    // Cycle once: F sharp -> G flat, same sound.
    await enharmonicButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "G");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "-1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "flat");
    expect(await selectedMidi(page)).toBe(66); // MIDI unchanged
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    // Nothing destroyed - the whole score still lays out.
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);

    // Cycle again: G flat -> F sharp, still the same sound.
    await enharmonicButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "F");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "sharp");
    expect(await selectedMidi(page)).toBe(66);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    // Persist a cycled spelling and confirm the round trip: <alter> and
    // <accidental> agree and still sound MIDI 66.
    await enharmonicButton(page).click(); // back to G flat
    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    const n = await spellingById(page, saved.content, "n1-1-1-0");
    expect(n).toMatchObject({ step: "G", alter: -1, accidental: "flat", midi: 66 });
  });
});

test.describe("note editor - existing operations stay green (#185)", () => {
  // Target 5: the existing #10 fret edit now spells key-aware and the Rule 10
  // mirror still agrees, and it coexists with an explicit accidental and a cycle
  // in the same document.
  test("a fret edit spells key-aware and the guard stays green alongside a cycle and an accidental", async ({ page }) => {
    await openEditor(page, keyedEditorScore(-4), 8); // A flat major
    // An explicit accidental on note 0, an enharmonic cycle on note 1.
    await selectNote(page, 0);
    await setFret(page, 2); // MIDI 66 -> G flat in this key
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "-1");
    await selectNote(page, 1);
    await enharmonicButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    // A plain #10 fret edit on note 2 (G4, string 1 fret 3 -> fret 5 = A4 = MIDI
    // 69): key-aware spelling, guard green over the edited document, note count
    // stable. A flat major flats A in its key signature, so an A NATURAL must be
    // written with a natural sign to cancel it - the "a natural the key would
    // alter is written as one" case.
    await selectNote(page, 2);
    await setFret(page, 5);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "69");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-step", "A");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-alter", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-accidental", "natural");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);
  });
});
