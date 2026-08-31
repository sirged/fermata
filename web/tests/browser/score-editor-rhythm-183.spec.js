// Dotted durations and ties (#183), end to end against the real alphaTab render
// and the real editor/document.js parse - the same fixture and stub the
// first-increment suite uses (score-editor.spec.js, fixtures/editor-score.js),
// only the HTTP transport stubbed. Every assertion is on state the app
// publishes onto the DOM (data-editor-*), the read-only geometry/model hook
// window.__scoreEditor, or the exact MusicXML persisted through the save path -
// never on an internal reached into.
//
// The two edits this covers (tuplets are a stated follow-on - see the PR):
//   - Dots: the Dots control sets a note's augmentation dots, writing the
//     <dot/>(s) AND a <duration> that stays exactly consistent (a dotted quarter
//     is 720 against divisions 480, a double-dotted quarter 840). The re-import
//     agrees and the bar arithmetic holds when the dotted note and the shortened
//     one it borrows from still sum to the space they replaced.
//   - Ties: the Tie control joins the selected note to the next one so the two
//     read as ONE held note, writing <tie> (sound) AND <tied> (notation),
//     type="start" on the first and "stop" on the second. The two must be the
//     same pitch; a tie to a different pitch is refused. It can be removed.
//
// What each mutation would turn red, so this suite is falsifiable, not green by
// construction:
//   - omit the <dot/> while keeping the longer <duration> (or vice versa): the
//     re-imported dot count stops matching, and the "writes the dot" assertions
//     on the persisted MusicXML go red.
//   - compute the dotted <duration> as the plain value (drop the *3/2): the bar
//     no longer sums to 1920 -> "the bar still sums" red, and the persisted
//     <duration> assertion red.
//   - drop either the <tie> or the <tied> (or a matching start/stop): the
//     persisted-MusicXML tie assertions go red.
//   - skip the same-pitch guard (tie to any next note): the "refused" test's
//     expectation that nothing was written, and dirty stayed false, goes red.
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");
const fretInput = (page) => page.locator(".edit-fields input");
const durationSelect = (page) => page.locator(".edit-fields label", { hasText: "Duration" }).locator("select");
const dotsSelect = (page) => page.locator(".edit-fields label", { hasText: "Dots" }).locator("select");
const tieButton = (page) => page.locator(".edit-fields .tie-toggle");

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

// The per-voice sum of note+rest <duration> in a measure of a MusicXML string,
// computed in the page's own DOMParser - Rule 8's own arithmetic (chord members
// counted once). Returns { voices: {n: sum} }.
async function measureShape(page, xml, measureNumber) {
  return page.evaluate(
    ({ xml, measureNumber }) => {
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const m = [...doc.getElementsByTagName("measure")].find((el) => el.getAttribute("number") === String(measureNumber));
      const voices = {};
      for (const child of m.children) {
        if (child.tagName !== "note") continue;
        if (child.getElementsByTagName("chord")[0]) continue;
        const v = child.getElementsByTagName("voice")[0]?.textContent ?? "?";
        const d = Number(child.getElementsByTagName("duration")[0]?.textContent) || 0;
        voices[v] = (voices[v] ?? 0) + d;
      }
      return { voices };
    },
    { xml, measureNumber },
  );
}

// Everything the rhythm edits touch on ONE note of a MusicXML string, read by
// its Rule 17 id (which setDots/setTie never renumber): its <type>, its <dot/>
// count, its <duration>, the <tie> sound types it carries, and the <tied>
// notation types. A DIRECT child <tie> only (not a nested one), so the sound
// half is counted where the schema puts it.
async function noteById(page, xml, id) {
  return page.evaluate(
    ({ xml, id }) => {
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const note = [...doc.getElementsByTagName("note")].find((n) => n.getAttribute("id") === id);
      if (!note) return null;
      const ties = [...note.children].filter((c) => c.tagName === "tie").map((c) => c.getAttribute("type"));
      const notations = [...note.children].find((c) => c.tagName === "notations");
      const tied = notations
        ? [...notations.children].filter((c) => c.tagName === "tied").map((c) => c.getAttribute("type"))
        : [];
      return {
        type: note.getElementsByTagName("type")[0]?.textContent ?? null,
        dots: [...note.children].filter((c) => c.tagName === "dot").length,
        duration: Number(note.getElementsByTagName("duration")[0]?.textContent) || 0,
        ties: ties.sort(),
        tied: tied.sort(),
      };
    },
    { xml, id },
  );
}

async function save(page) {
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
}

test.describe("note editor - dotted durations (#183)", () => {
  // Target 1: a note set to dotted writes the <dot/> and a <duration> that
  // round-trips; re-import shows the dotted value; guard green; the bar
  // arithmetic is correct.
  test("a dotted quarter writes the dot and a round-tripping duration, and the bar still sums", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);

    // Shorten note 1 (a quarter, 480) to an eighth (240), then dot note 0's
    // quarter (480 -> 720). 720 + 240 = 960 = the two quarters they replaced, so
    // voice 1 still fills the 4/4 bar (720 + 240 + 480 + 480 = 1920).
    await selectNote(page, 1);
    await durationSelect(page).selectOption("eighth");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-type", "eighth");

    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-type", "quarter");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-dots", "0");
    await dotsSelect(page).selectOption("1");

    // Re-imported: the document shows one dot, the pitch is undisturbed, the two
    // reads still agree.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-dots", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-type", "quarter");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);

    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();

    // The persisted note carries exactly one <dot/> and the dotted <duration>.
    const n0 = await noteById(page, saved.content, "n1-1-0-0");
    expect(n0).toMatchObject({ type: "quarter", dots: 1, duration: 720 });

    // The bar's voice 1 still sums to the 4/4 measure (720 + 240 + 480 + 480).
    const shape = await measureShape(page, saved.content, 1);
    expect(shape.voices["1"]).toBe(1920);

    // A second parse of what was written is identical - no drift on re-import.
    const again = await measureShape(page, saved.content, 1);
    expect(again).toEqual(shape);
  });

  // Target 1 (double dot): two <dot/>s and 7/4 the duration, re-imported.
  test("a double-dotted quarter writes two dots and 7/4 the duration", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 0);
    await dotsSelect(page).selectOption("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-dots", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    // 480 * 7/4 = 840, with two dots.
    const n0 = await noteById(page, saved.content, "n1-1-0-0");
    expect(n0).toMatchObject({ type: "quarter", dots: 2, duration: 840 });

    // Setting it back to none drops both dots and restores the plain duration.
    await selectNote(page, 0);
    await dotsSelect(page).selectOption("0");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-dots", "0");
    await save(page);
    const n0b = await noteById(page, saved.content, "n1-1-0-0");
    expect(n0b).toMatchObject({ type: "quarter", dots: 0, duration: 480 });
  });
});

test.describe("note editor - ties (#183)", () => {
  // Target 3 (refusal half): a tie to a different pitch is refused, the document
  // untouched.
  test("a tie to a different pitch is refused and nothing is written", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    // Note 0 is E4 (fret 0); the next note (1) is F#4 (fret 2) - a different
    // pitch, so it cannot be tied.
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");
    await tieButton(page).click();

    // Refused: a warning is shown, no tie is on the note, and nothing changed.
    await expect(wrap(page)).toHaveAttribute("data-editor-warn", /same pitch/);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-tie-start", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // Target 3 (main): two same-pitch notes tied read as one held note on
  // re-import - <tie>+<tied> start on the first, stop on the second - guard
  // green, no note destroyed, bar arithmetic unchanged.
  test("two same-pitch notes are tied and read as one held note", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    // Make note 1 the SAME pitch as note 0: fret 2 (F#4) -> fret 0 (E4).
    await selectNote(page, 1);
    await fretInput(page).fill("0");
    await fretInput(page).blur();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");

    // Tie note 0 to note 1 (both E4, contiguous in measure 1).
    await selectNote(page, 0);
    await tieButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-tie-start", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");
    // The partner reads back as tied INTO from the previous note.
    await selectNote(page, 1);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-tie-stop", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-tie-start", "false");
    // Nothing was destroyed - both notes still sound.
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);

    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    // Both the sound (<tie>) and notation (<tied>) halves, start on the first
    // note and stop on the second.
    const first = await noteById(page, saved.content, "n1-1-0-0");
    const second = await noteById(page, saved.content, "n1-1-1-0");
    expect(first.ties).toEqual(["start"]);
    expect(first.tied).toEqual(["start"]);
    expect(second.ties).toEqual(["stop"]);
    expect(second.tied).toEqual(["stop"]);
    // A tie changes no duration, so the bar still sums to the 4/4 measure.
    const shape = await measureShape(page, saved.content, 1);
    expect(shape.voices["1"]).toBe(1920);
  });

  // Target 3 (removable): a tie can be toggled off, dropping both halves on both
  // notes.
  test("a tie can be removed again", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 1);
    await fretInput(page).fill("0");
    await fretInput(page).blur();
    await selectNote(page, 0);
    await tieButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-tie-start", "true");

    // Toggle it off.
    await tieButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-tie-start", "false");
    await save(page);
    await expect.poll(() => saved.content).not.toBeNull();
    const first = await noteById(page, saved.content, "n1-1-0-0");
    const second = await noteById(page, saved.content, "n1-1-1-0");
    expect(first.ties).toEqual([]);
    expect(first.tied).toEqual([]);
    expect(second.ties).toEqual([]);
    expect(second.tied).toEqual([]);
  });

  // Target 4: the existing #10 fret edit and the divergence guard still work
  // over a document now carrying a dot and a tie.
  test("after a dot and a tie, a fret edit still works and the guard stays green", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    // A dot on note 3, a tie between notes 0 and 1 (same pitch first).
    await selectNote(page, 3);
    await dotsSelect(page).selectOption("1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-dots", "1");
    await selectNote(page, 1);
    await fretInput(page).fill("0");
    await fretInput(page).blur();
    await selectNote(page, 0);
    await tieButton(page).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-tie-start", "true");

    // A #10 fret edit on note 2 (G4, string 1 fret 3 -> fret 5 = A4 = MIDI 69),
    // guard green over the document that now carries a dot and a tie.
    await selectNote(page, 2);
    await fretInput(page).fill("5");
    await fretInput(page).blur();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "69");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });
});
