// The multi-note selection (#251), end to end against the real alphaTab render
// and the real editor/document.js parse - the same stub every earlier editor
// increment uses (fixtures/editor-score.js), only the HTTP transport stubbed.
// Every assertion is on state the app publishes onto the DOM (data-editor-*),
// the read-only geometry hook window.__scoreEditor, the opt-in model harness
// window.__scoreEditorHarness (the same one the #189 fuzz drives), or the exact
// MusicXML persisted through the save path - never on an internal reached into.
//
// The editor edited one note at a time. This increment adds a contiguous
// selection: shift+ArrowLeft/Right extends it along the run from the anchor,
// and the operations that make sense over a run - duration type, dots, delete,
// respelling, a string change - apply to every note in it as ONE gesture, one
// undo entry. Fret entry, the tie and the voice move stay anchor-only. And
// setDurationType gains the <time-modification> refusal setDots has had since
// #183, so retyping a tuplet member no longer silently writes a bar that does
// not sum.
//
// The two decisions this suite pins, because they are choices and not
// derivations:
//   - The extend step STOPS at a rest and at a voice change (document.js's
//     stepContiguous). A run is what is drawn as one marked set of heads; it
//     does not jump a silence.
//   - A range gesture is ALL-OR-NOTHING, uniformly: one refused note rolls the
//     whole gesture back and pushes no undo entry, so an undo entry always
//     means "all N notes changed". The string change takes the same policy
//     rather than the per-note skip the issue's sketch describes - setString
//     refuses only a pitch outside MusicXML's writable octaves, which no real
//     tuning and fret span reaches, so a skip branch there would be
//     unreachable code (see the PR).
//
// The document-model arithmetic is asserted here rather than in tests/unit
// because editor/document.js parses with DOMParser, which the Node-side unit
// specs do not have (the same reason score-editor-rest-238.spec.js gives); the
// assertions below read the serialized document (harness.text()) directly, so
// they fail on the arithmetic itself and not only on what the renderer drew.
//
// What each mutation would turn red, so this suite is falsifiable, not green by
// construction:
//   - drop setDurationType's <time-modification> guard (main's behaviour): the
//     triplet member is retyped and its <duration> stops being the scaled 160
//     -> "a tuplet member's duration type is refused" and the whole-gesture
//     rollback test red.
//   - let stepContiguous cross a rest (drop the isRest stop): the extension
//     runs past the rest in measure 2 -> "the extension stops at a rest" red.
//   - let stepContiguous ignore the voice: nothing in THIS monophonic fixture
//     moves, which is why the voice half of the rule is asserted on the
//     polyphonic fixture instead ("the extension stops at a voice change").
//   - apply a range gesture to only the first note (drop the loop): the
//     duration/delete range tests red on the notes after the anchor.
//   - push one undo entry per note instead of per gesture: one undo would
//     restore only the last note -> "one undo step" red.
//   - apply a range gesture without the rollback (write the notes it could and
//     leave the refused one alone): the whole-gesture refusal test reds on the
//     note that would have changed, and on the undo stack.
import { test, expect } from "@playwright/test";

import { stubEditorApi } from "./fixtures/editor-score.js";
import { POLY_MUSICXML } from "./fixtures/editor-poly.js";
import {
  MEASURE_DURATION,
  RANGE_MUSICXML,
  RANGE_NOTE_COUNT,
  RANGE_REST_COUNT,
} from "./fixtures/editor-range.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");
const marks = (page) => page.locator(".staff-render .note-selection");

async function renderedOk(page) {
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true");
}

// Opens the editor over `content` with the model harness enabled, waiting until
// the whole score's `expected` sounding notes are laid out - the barrier every
// read below waits behind.
async function openEditor(page, content, expected) {
  await page.addInitScript(() => {
    window.__fermataEditorHarness = true;
  });
  const handle = await stubEditorApi(page, content);
  await page.goto("/#/score/1");
  await page.waitForSelector(".staff-render");
  // Full-width staff, so the whole score is one active pane whose window key
  // handler answers the keyboard (see TabViewer's `active` prop).
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(expected);
  await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness?.count() ?? 0)).toBe(expected);
  return handle;
}

// Clicks the note-head the renderer reports for `ordinal` - the same gesture a
// player makes, hit-tested against score-render.js's own positional map.
async function selectNote(page, ordinal) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
}

// Every measure's total <duration> in voice 1, computed from the SERIALIZED
// document by the same per-measure time-cursor walk MusicXML defines (a note
// advances the cursor, a chord member does not, a backup rewinds). Run in the
// page because that is where DOMParser is. Returns { "1": 1920, ... }.
const measureSums = (page) =>
  page.evaluate(() => {
    const xml = window.__scoreEditorHarness.text();
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    const out = {};
    for (const m of doc.getElementsByTagName("measure")) {
      let cursor = 0;
      let total = 0;
      for (const child of m.children) {
        const dur = Number(child.getElementsByTagName("duration")[0]?.textContent ?? NaN);
        if (child.tagName === "backup") {
          cursor -= Number.isFinite(dur) ? dur : 0;
          continue;
        }
        if (child.tagName === "forward") {
          cursor += Number.isFinite(dur) ? dur : 0;
        } else if (child.tagName === "note") {
          if ([...child.children].some((c) => c.tagName === "chord")) continue;
          cursor += Number.isFinite(dur) ? dur : 0;
        } else {
          continue;
        }
        if (cursor > total) total = cursor;
      }
      out[m.getAttribute("number")] = total;
    }
    return out;
  });

// The written (<type>, <duration>, <time-modification> present) of every note in
// the serialized document, in document order, rests included - what a duration
// change is asserted against.
const written = (page) =>
  page.evaluate(() => {
    const xml = window.__scoreEditorHarness.text();
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    return [...doc.getElementsByTagName("note")].map((n) => ({
      id: n.getAttribute("id"),
      rest: !!n.getElementsByTagName("rest").length,
      type: n.getElementsByTagName("type")[0]?.textContent ?? null,
      duration: Number(n.getElementsByTagName("duration")[0]?.textContent ?? NaN),
      tuplet: !!n.getElementsByTagName("time-modification").length,
    }));
  });

test.describe("note editor - selecting and editing a run of notes", () => {
  // Done-when: "Shift+right from a selected note extends the selection to the
  // next note in the voice and the render marks both."
  test("shift+ArrowRight extends the selection to the next note, and the render marks both heads", async ({
    page,
  }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    await selectNote(page, 0);
    // One note selected: one head marked, no extent.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "1");
    await expect(marks(page)).toHaveCount(1);

    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "0"); // the anchor did not move
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-extent", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "0,1");
    // The render marks BOTH heads - one marker per selected ordinal, each
    // naming the ordinal it covers, and they are at different places on the
    // staff (a second marker stacked on the first would not be marking a
    // second note).
    await expect(marks(page)).toHaveCount(2);
    await expect(marks(page).nth(0)).toHaveAttribute("data-editor-selected-head", "0");
    await expect(marks(page).nth(1)).toHaveAttribute("data-editor-selected-head", "1");
    const boxes = await marks(page).evaluateAll((els) =>
      els.map((e) => ({ left: e.style.left, top: e.style.top })),
    );
    expect(boxes[0].left).not.toBe(boxes[1].left);

    // Shift+ArrowLeft walks the moving end back to the anchor: one note again.
    await page.keyboard.press("Shift+ArrowLeft");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "1");
    await expect(marks(page)).toHaveCount(1);

    // ... and once more past the anchor, extending backwards to ordinal -? no:
    // ordinal 0 is the document's first note, so the run ends there and the
    // selection stands rather than wrapping or clearing.
    await page.keyboard.press("Shift+ArrowLeft");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "0");
  });

  // Done-when: extend by two across a bar line within a voice. Also the rest
  // decision: the run STOPS at a rest.
  test("the extension crosses a bar line within the voice and stops at a rest", async ({ page }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    // Ordinal 6 is measure 1's last note; 7 and 8 are measure 2's first two.
    await selectNote(page, 6);
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "6,7,8");
    await expect(marks(page)).toHaveCount(3);
    // The three notes are in two different measures - the extension really did
    // cross the bar line rather than stopping at it.
    const measures = await page.evaluate(() =>
      window.__scoreEditorHarness.range().map((o) => window.__scoreEditorHarness.noteAt(o).measure),
    );
    expect(measures).toEqual([1, 2, 2]);

    // A rest follows ordinal 8 in the same voice. The run ends there: a further
    // shift+ArrowRight changes nothing, and the note after the rest (ordinal 9)
    // is NOT selected.
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "6,7,8");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "6,7,8");
    await expect(marks(page)).toHaveCount(3);
  });

  // The other half of stepContiguous's rule, on the polyphonic fixture: a run
  // stops where the next element in document order belongs to another voice.
  test("the extension stops at a voice change", async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);
    const out = await page.evaluate(() => {
      const h = window.__scoreEditorHarness;
      // Walk from ordinal 0 extending as far as the run allows, then report the
      // voices of everything it reached.
      h.select(0);
      let range = h.range();
      for (let i = 0; i < 60; i++) {
        const next = h.extend(1);
        if (next.length === range.length) break;
        range = next;
      }
      return {
        range,
        voices: [...new Set(range.map((o) => h.noteAt(o).voice))],
        // The note the run stopped BEFORE, if there is one.
        nextVoice: h.noteAt(range[range.length - 1] + 1)?.voice ?? null,
        anchorVoice: h.noteAt(0).voice,
      };
    });
    expect(out.range.length).toBeGreaterThan(1);
    // Everything reached is in the anchor's voice, and only that voice.
    expect(out.voices).toEqual([out.anchorVoice]);
    // It stopped because the next note in document order is in another voice
    // (or because a rest sits there - either way it is not the same voice
    // continuing, which is the claim).
    expect(out.nextVoice === null || out.nextVoice !== out.anchorVoice).toBe(true);
  });

  // Done-when: "a duration change over a three-note selection changes all three
  // in one undo step and the bar still sums".
  test("a duration change over three notes changes all three in one undo step, and the bar still sums", async ({
    page,
  }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    const before = await measureSums(page);
    expect(before["1"]).toBe(MEASURE_DURATION);

    // Ordinals 0,1,2 are a quarter and two 16ths - 480+120+120 = 720, which is
    // exactly three eighths, so retyping all three preserves the measure total.
    await selectNote(page, 0);
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "0,1,2");

    await page.locator(".edit-fields label", { hasText: "Duration" }).locator("select").selectOption("eighth");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");
    await expect.poll(async () => (await written(page)).slice(0, 3).map((n) => n.type)).toEqual([
      "eighth",
      "eighth",
      "eighth",
    ]);
    const afterNotes = await written(page);
    expect(afterNotes.slice(0, 3).map((n) => n.duration)).toEqual([240, 240, 240]);
    // Everything outside the selection is untouched.
    expect(afterNotes[3].duration).toBe(480);
    // The bar still sums to the 4/4 measure.
    expect((await measureSums(page))["1"]).toBe(MEASURE_DURATION);
    // The render and the written document still agree across every note.
    const audit = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(audit.ok, JSON.stringify(audit.divergences)).toBe(true);

    // ONE undo step brings all three back - not three.
    await page.getByRole("button", { name: "Undo" }).click();
    await expect.poll(async () => (await written(page)).slice(0, 3).map((n) => n.type)).toEqual([
      "quarter",
      "16th",
      "16th",
    ]);
    expect((await written(page)).slice(0, 3).map((n) => n.duration)).toEqual([480, 120, 120]);
    // ... and there is nothing left to undo: the gesture was one entry.
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    expect((await measureSums(page))["1"]).toBe(MEASURE_DURATION);
  });

  // Done-when: "delete over a selection turns each into a rest".
  test("Backspace over a selection turns every selected note into a rest, and one undo brings them all back", async ({
    page,
  }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    const sumsBefore = await measureSums(page);

    await selectNote(page, 3);
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "3,4,5");
    const ids = await page.evaluate(() =>
      window.__scoreEditorHarness.range().map((o) => window.__scoreEditorHarness.noteAt(o).id),
    );
    expect(ids).toEqual(["n1-1-3-0", "n1-1-4-0", "n1-1-5-0"]);

    await page.keyboard.press("Backspace");
    // Three notes gone, three rests gained - each note became a rest in place.
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(RANGE_NOTE_COUNT - 3);
    expect(await page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(RANGE_REST_COUNT + 3);
    const nowRests = (await written(page)).filter((n) => ids.includes(n.id));
    expect(nowRests.map((n) => n.rest)).toEqual([true, true, true]);
    // Each kept its own duration, so the bar is unchanged in length.
    expect(nowRests.map((n) => n.duration)).toEqual([480, 480, 120]);
    expect(await measureSums(page)).toEqual(sumsBefore);
    const audit = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(audit.ok, JSON.stringify(audit.divergences)).toBe(true);

    // One undo brings all three back at once.
    await page.getByRole("button", { name: "Undo" }).click();
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(RANGE_NOTE_COUNT);
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    expect(await measureSums(page)).toEqual(sumsBefore);
  });

  // The premise this bet re-derived: setDurationType had no <time-modification>
  // guard while setDots did, so retyping a tuplet member wrote a bar that no
  // longer summed. On main this test is red - the retype applies.
  test("a tuplet member's duration type is refused, the same way its dots already were", async ({ page }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    const before = await written(page);
    // Ordinal 10 is the first member of the 3:2 triplet: written `eighth`,
    // sounding 160 rather than 240 because of the <time-modification>.
    expect(before[11].tuplet).toBe(true); // document order includes the rest
    await selectNote(page, 10);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-type", "eighth");

    await page.locator(".edit-fields label", { hasText: "Duration" }).locator("select").selectOption("quarter");
    await expect(wrap(page)).toHaveAttribute(
      "data-editor-warn",
      "That duration can't be written for this note.",
    );
    // Nothing was written: the member keeps its scaled duration and its type,
    // the measure still sums, and the document is not dirty.
    const after = await written(page);
    expect(after).toEqual(before);
    expect((await measureSums(page))["3"]).toBe(MEASURE_DURATION);
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");

    // The refusal is the same one setDots has carried since #183 - shown here
    // so the pair reads as one rule rather than two coincidences.
    await page.locator(".edit-fields label", { hasText: "Dots" }).locator("select").selectOption("1");
    await expect(wrap(page)).toHaveAttribute(
      "data-editor-warn",
      "That dotted value can't be written for this note.",
    );
    expect(await written(page)).toEqual(before);
  });

  // The all-or-nothing decision, pinned: one refused note rolls the WHOLE
  // gesture back and pushes no undo entry.
  test("a range duration change containing a tuplet member is refused whole, leaving every note untouched", async ({
    page,
  }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    const before = await written(page);

    // Ordinals 12 and 13: the last triplet member and the plain quarter after
    // it. The quarter alone would take the change; the tuplet member cannot.
    await selectNote(page, 12);
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "12,13");

    await page.locator(".edit-fields label", { hasText: "Duration" }).locator("select").selectOption("half");
    await expect(wrap(page)).toHaveAttribute(
      "data-editor-warn",
      "That duration can't be written for this note. Nothing in the selection of 2 notes was changed.",
    );
    // Neither note changed - not even the one that could have taken it.
    expect(await written(page)).toEqual(before);
    expect((await measureSums(page))["3"]).toBe(MEASURE_DURATION);
    // And no undo entry was pushed: a refused gesture leaves the stack alone.
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");

    // The selection survives the refusal, so the player can pick a different
    // duration without re-selecting.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "12,13");
  });

  // The string change as a RANGE op: every note in the run moves to the new
  // string in one gesture and one undo entry, re-pitched by the tuning exactly
  // as the single-note control has always re-pitched one (setString keeps the
  // fret and recomputes the pitch - it re-frets the position, it does not
  // preserve the sound).
  test("a string change moves every note in the selection in one undo step", async ({ page }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    await selectNote(page, 0);
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "0,1");
    const before = await page.evaluate(() =>
      [0, 1]
        .map((o) => window.__scoreEditorHarness.noteAt(o))
        .map((n) => ({ string: n.string, fret: n.fret, midi: n.midi })),
    );
    expect(before).toEqual([
      { string: 1, fret: 0, midi: 64 },
      { string: 1, fret: 2, midi: 66 },
    ]);

    const stringSelect = page.locator(".edit-fields label", { hasText: "String" }).locator("select");
    await stringSelect.selectOption("2");
    await expect
      .poll(() =>
        page.evaluate(() =>
          window.__scoreEditorHarness.range().map((o) => window.__scoreEditorHarness.noteAt(o).string),
        ),
      )
      .toEqual([2, 2]);
    // Both kept their frets and both were re-pitched from string 2's open B3
    // (59): fret 0 -> 59, fret 2 -> 61.
    expect(
      await page.evaluate(() =>
        window.__scoreEditorHarness.range().map((o) => window.__scoreEditorHarness.noteAt(o).midi),
      ),
    ).toEqual([59, 61]);
    const audit = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(audit.ok, JSON.stringify(audit.divergences)).toBe(true);

    // One undo entry for the whole gesture.
    await page.getByRole("button", { name: "Undo" }).click();
    await expect
      .poll(() => page.evaluate(() => [0, 1].map((o) => window.__scoreEditorHarness.noteAt(o).string)))
      .toEqual([1, 1]);
    await expect(wrap(page)).toHaveAttribute("data-editor-can-undo", "false");
  });

  // Escape and a plain arrow both collapse the range - the two ways out of it.
  test("Escape collapses a range to its anchor, and a plain arrow collapses and moves", async ({ page }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    await selectNote(page, 3);
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "3,4,5");

    await page.keyboard.press("Escape");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "3"); // the ANCHOR stays selected
    await expect(marks(page)).toHaveCount(1);

    // Rebuild the range, then a plain ArrowRight: it collapses and steps from
    // the anchor, exactly as it would have without a range.
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "2");
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "4");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-count", "1");
    await expect(marks(page)).toHaveCount(1);
  });

  // Fret entry acts on the ANCHOR only, with the range still standing.
  test("a typed fret acts on the anchor alone, leaving the range standing", async ({ page }) => {
    await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    await selectNote(page, 0);
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "0,1,2");
    const before = await page.evaluate(() =>
      [0, 1, 2].map((o) => window.__scoreEditorHarness.noteAt(o).fret),
    );
    expect(before).toEqual([0, 2, 3]);

    await page.keyboard.press("7");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "7");
    // Only the anchor moved; the other two notes in the range kept their frets,
    // and the selection is still all three.
    expect(
      await page.evaluate(() => [0, 1, 2].map((o) => window.__scoreEditorHarness.noteAt(o).fret)),
    ).toEqual([7, 2, 3]);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-ordinals", "0,1,2");
  });

  // Done-when: "save round-trips through the transcription PUT". The captured
  // body is what the schema check in the server's own save path is fed; the
  // XSD run itself is a server-side command reported in the PR (the browser
  // suite stubs the HTTP transport, exactly as #244's own spec notes).
  test("a range edit saves through the transcription PUT and survives a reload", async ({ page }) => {
    const { saved } = await openEditor(page, RANGE_MUSICXML, RANGE_NOTE_COUNT);
    await selectNote(page, 0);
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await page.locator(".edit-fields label", { hasText: "Duration" }).locator("select").selectOption("eighth");
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "true");

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    expect(saved.content, "the editor PUT a body").toBeTruthy();
    // The persisted document holds all three retyped notes...
    expect(saved.content).toContain("<duration>240</duration>");
    expect((saved.content.match(/<duration>240<\/duration>/g) ?? []).length).toBe(3);
    // ... every <octave> is inside MusicXML's writable 0-9 (Rule 11, the bound
    // the schema itself enforces) ...
    for (const m of saved.content.matchAll(/<octave>(-?\d+)<\/octave>/g)) {
      expect(Number(m[1])).toBeGreaterThanOrEqual(0);
      expect(Number(m[1])).toBeLessThanOrEqual(9);
    }
    // ... and the tuplet members were left exactly as they were, <duration> 160
    // under their <time-modification>.
    expect((saved.content.match(/<duration>160<\/duration>/g) ?? []).length).toBe(3);

    // A full page reload re-opens the editor over the saved content: still
    // three eighths, and the bar still sums.
    await page.reload();
    await page.waitForSelector(".staff-render");
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    await renderedOk(page);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await expect
      .poll(() => page.evaluate(() => window.__scoreEditorHarness?.count() ?? 0))
      .toBe(RANGE_NOTE_COUNT);
    expect((await written(page)).slice(0, 3).map((n) => n.type)).toEqual(["eighth", "eighth", "eighth"]);
    expect((await measureSums(page))["1"]).toBe(MEASURE_DURATION);
  });
});
