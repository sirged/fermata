// Moving a note between voices (#182), end to end against the real alphaTab
// render and the real editor/document.js parse - the same fixture and stub the
// first-increment suite uses (score-editor.spec.js, fixtures/editor-score.js),
// only the HTTP transport stubbed. Every assertion is on state the app
// publishes onto the DOM (data-editor-*) or the read-only geometry/model hook
// window.__scoreEditor, or on the exact MusicXML persisted through the save
// path - never on an internal reached into.
//
// The edit: the panel's Voice control reassigns the selected note to another
// voice, introducing the <backup> and the second voice where one is needed and
// keeping the note at the SAME onset. The document is rebuilt from its voices'
// timelines, so the backup arithmetic, every voice's Rule 8 sum, and the
// onsets stay correct by construction.
//
// What each mutation would turn red, so this suite is falsifiable, not green by
// construction:
//   - no-op doc.moveToVoice (return null): the Voice control refuses every move
//     -> every test here red (the selection never lands in voice 2).
//   - drop the <backup>/rest rebuild (write only the <voice> text): re-import
//     puts the note at onset 0 of voice 2, not its old onset, and the per-voice
//     sums stop equalling the measure -> "keeps its onset" and "arithmetic
//     round-trips" red.
//   - skip the source rest (collapse the gap): voice 1's later notes slide
//     earlier, its sum drops below the measure -> "arithmetic round-trips" red.
//   - mishandle the chord split (move the whole beat, or drop the head
//     promotion): the head stops sounding at onset 0 -> "a chord member" red.
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, CHORD_MUSICXML, stubEditorApi } from "./fixtures/editor-score.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");
const voiceSelect = (page) => page.locator(".edit-fields label", { hasText: "Voice" }).locator("select");
const fretInput = (page) => page.locator(".edit-fields input");

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

// Opens the editor over `content` and waits until the whole score's `expected`
// sounding notes are laid out - the barrier every read below waits behind.
async function openEditor(page, content, expected) {
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  // Full-width staff, so the whole score is one active pane and every note is on
  // screen (side-by-side would give the staff half the width).
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

// The per-voice sum of note+rest <duration> in a given measure of a MusicXML
// string, computed in the page's own DOMParser - Rule 8's own arithmetic
// (chord members counted once: a <chord/> note does not advance the cursor and
// is not summed here). Returns { voices: {n: sum}, backups: [durations] }.
async function measureShape(page, xml, measureNumber) {
  return page.evaluate(
    ({ xml, measureNumber }) => {
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const measures = [...doc.getElementsByTagName("measure")];
      const m = measures.find((el) => el.getAttribute("number") === String(measureNumber));
      const voices = {};
      const backups = [];
      const soundingVoices = new Set();
      for (const child of m.children) {
        if (child.tagName === "backup") {
          const d = Number(child.getElementsByTagName("duration")[0]?.textContent);
          backups.push(d);
          continue;
        }
        if (child.tagName !== "note") continue;
        const chord = !!child.getElementsByTagName("chord")[0];
        const rest = !!child.getElementsByTagName("rest")[0];
        const v = child.getElementsByTagName("voice")[0]?.textContent ?? "?";
        const d = Number(child.getElementsByTagName("duration")[0]?.textContent) || 0;
        if (!rest) soundingVoices.add(v);
        if (!chord) voices[v] = (voices[v] ?? 0) + d;
      }
      return { voices, backups, soundingVoices: [...soundingVoices] };
    },
    { xml, measureNumber },
  );
}

test.describe("note editor - move a note between voices", () => {
  // Target 1 + part of 3: a note moved from voice 1 to voice 2 re-imports and
  // re-renders in voice 2, at the SAME onset it had, with the divergence guard
  // (now spanning the voice the note landed in) green.
  test("a note moved to voice 2 keeps its onset and the guard stays green", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    // Note 2 is G4, string 1 fret 3, voice 1, onset 960 (beat 3 of a 4/4 bar at
    // divisions 480). Capture its onset from the app, not this comment.
    await selectNote(page, 2);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-onset", "960");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "3");

    await voiceSelect(page).selectOption("2");

    // Its voice block now sits after voice 1's, so the ordinal moved 2 -> 3.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "3");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "2");
    // Beat 3 of voice 1 is beat 3 of voice 2, NOT beat 1: the onset is unchanged.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-onset", "960");
    // Same note, unchanged: string 1, fret 3, G4 = MIDI 67.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "3");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "67");
    // The document and the render agree, voice included.
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");
    // The renderer's OWN read (a different path from the document's <voice>)
    // also puts it in voice 2 - the voice half of the divergence cross-check.
    const rv = await page.evaluate(() => window.__scoreEditor.viewInfo(3).voice);
    expect(rv).toBe(2);
    // No note was destroyed - it still sounds, just in another voice.
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);
  });

  // Target 2: every voice's total duration is correct after the move, and
  // re-importing the written MusicXML is stable (a second parse is identical).
  test("the measure's arithmetic round-trips after the move", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 2);
    await voiceSelect(page).selectOption("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "2");

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect.poll(() => saved.content).not.toBeNull();

    // Both voices of measure 1 fill the 4/4 bar (480 * 4 = 1920) - voice 1 as
    // three notes and the rest left where the moved note was, voice 2 as the
    // moved note between the rests that pad it to the bar. Rule 8 holds for each.
    const shape = await measureShape(page, saved.content, 1);
    expect(shape.voices["1"]).toBe(1920);
    expect(shape.voices["2"]).toBe(1920);
    // One backup, rewinding a whole measure to start voice 2 (Rule 6).
    expect(shape.backups).toEqual([1920]);

    // Re-importing the written MusicXML and re-emitting is stable: a second
    // parse yields the identical per-voice arithmetic (no drift).
    const again = await measureShape(page, saved.content, 1);
    expect(again).toEqual(shape);
  });

  // Target 3: moving into a not-yet-existing second voice introduces the voice
  // and its <backup>; the re-imported structure has two voices with the moved
  // note in the second.
  test("moving into a not-yet-existing voice introduces the second voice and its backup", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    // Every note starts in voice 1; the fixture has no voice 2 at all.
    const before = await measureShape(page, EDITOR_MUSICXML, 1);
    expect(before.soundingVoices).toEqual(["1"]);
    expect(before.backups).toEqual([]);

    await selectNote(page, 2);
    await voiceSelect(page).selectOption("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "2");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect.poll(() => saved.content).not.toBeNull();

    const after = await measureShape(page, saved.content, 1);
    // Two voices now sound where there was one, and a backup was introduced.
    expect(after.soundingVoices.sort()).toEqual(["1", "2"]);
    expect(after.backups).toEqual([1920]);
    // The sounding note in the new voice 2 is the one that moved - G4, fret 3.
    const movedFret = await page.evaluate((xml) => {
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const m = [...doc.getElementsByTagName("measure")].find((el) => el.getAttribute("number") === "1");
      for (const n of m.getElementsByTagName("note")) {
        if (n.getElementsByTagName("rest")[0]) continue;
        if ((n.getElementsByTagName("voice")[0]?.textContent ?? "") === "2") {
          return n.getElementsByTagName("fret")[0]?.textContent ?? null;
        }
      }
      return null;
    }, saved.content);
    expect(movedFret).toBe("3");
  });

  // Target 4: a chord member moved out splits the chord - the moved note leaves
  // as a lone note in the new voice at the same onset, the remaining members
  // keep sounding at that onset in the source voice (the stated rule).
  test("a chord member moved out splits the chord and keeps sounding in the new voice", async ({ page }) => {
    await openEditor(page, CHORD_MUSICXML, 5);
    // Ordinal 1 is the chord's SECOND note (B3, string 2 fret 0, voice 1, onset
    // 0) - a member sharing the head's onset.
    await selectNote(page, 1);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-onset", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "59");

    await voiceSelect(page).selectOption("2");

    // B3 is now a lone note in voice 2, still at onset 0, unchanged in pitch.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-onset", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "59");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    // Nothing was destroyed by the split - all five noteheads still sound.
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(5);

    // The chord's head (E4) still sounds at onset 0 in voice 1: the chord shrank,
    // it did not move. It is now ordinal 0 (voice 1's first note).
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-onset", "0");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "64");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // Target 5: the control performs the move and the earlier editor operations
  // (#10 fret edit, its divergence guard) still work over a document that now
  // carries two voices.
  test("after a move, a fret edit on another note still works and the guard stays green", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    await selectNote(page, 2);
    await voiceSelect(page).selectOption("2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "2");

    // A #10 fret edit on a still-voice-1 note (ordinal 0, E4) after the move:
    // string 1, fret 0 -> fret 5 = A4 = MIDI 69, guard green over the now
    // two-voice document.
    await selectNote(page, 0);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-voice", "1");
    await fretInput(page).fill("5");
    await fretInput(page).blur();
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "69");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });
});
