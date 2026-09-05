// Turning a rest back into a note (#238), end to end against the real alphaTab
// render and the real editor/document.js parse - the same fixtures and stub the
// earlier editor increments use (score-editor.spec.js, fixtures/editor-score.js
// and fixtures/editor-poly.js), only the HTTP transport stubbed. Every
// assertion is on state the app publishes onto the DOM (data-editor-*), the
// read-only geometry hook window.__scoreEditor, the opt-in model harness
// window.__scoreEditorHarness (the same one the #189 fuzz drives), or the exact
// MusicXML persisted through the save path - never on an internal reached into.
//
// The editor could turn a note into a rest (Backspace -> deleteNote) and had no
// inverse. This increment adds one: a rest is a selectable stop for the arrow
// keys, and a digit typed on a selected rest replaces its <rest/> with a
// <pitch> and a <notations><technical> string/fret, keeping the rest's
// <duration>, <voice>, <type> and <dot>s untouched so the bar's arithmetic is
// unchanged.
//
// The document-model arithmetic is asserted here rather than in tests/unit
// because editor/document.js parses with DOMParser, which the Node-side unit
// specs do not have; the assertions below read the serialized document
// (harness.text()) directly, so they fail on the arithmetic itself and not only
// on what the renderer drew.
//
// DELIBERATE LIMITATION, tested for rather than papered over: a rest cannot be
// CLICKED. score-render.js's positional map indexes sounding notes only, so a
// rest has no note-head bounds to hit-test against; reaching one is arrow-key
// work here (the issue's "clicked or arrowed to"). Widening that map is #226's
// territory and score-render.js is untouched by this change.
//
// What each mutation would turn red, so this suite is falsifiable, not green by
// construction:
//   - drop the <duration> (or rewrite it) in document.js's restToNote: the
//     converted note's duration stops being the rest's 480 and the bar stops
//     summing to 1920 -> "keeps the rest's duration, type, dots and voice" and
//     "the bar still sums" red, and the whole-model audit reds too.
//   - derive the fret's pitch from the wrong string (e.g. ignore the panel's
//     string, or read the staff-tuning line without the Rule 5 mirror): the
//     converted note's <pitch>/<string> stop matching the tuning -> "the new
//     note's pitch is the tuning's pitch for the chosen string" red.
//   - make the arrow step skip rests (walk stepNote instead of stepAny): the
//     arrow never lands on the rest -> "arrow keys reach a rest" red, and every
//     test below it that starts by arrowing onto one.
//   - drop the digit-on-a-rest branch in typeFretDigit: the digit falls through
//     to the staff-profile switch and no note appears -> the conversion tests
//     red.
import { test, expect } from "@playwright/test";

import { EDITOR_MUSICXML, stubEditorApi } from "./fixtures/editor-score.js";
import { POLY_MUSICXML } from "./fixtures/editor-poly.js";

const wrap = (page) => page.locator(".staff-render .wrap");
const host = (page) => page.locator(".staff-render .at-host");
const restStringSelect = (page) => page.locator(".edit-fields label", { hasText: "String" }).locator("select");

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
  // handler answers the keyboard (side-by-side would cede the arrows to the PDF
  // pane - see TabViewer's `active` prop).
  await page.getByRole("button", { name: "Staff", exact: true }).click();
  await renderedOk(page);
  await page.getByRole("button", { name: "Edit notes" }).click();
  await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(expected);
  await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness?.count() ?? 0)).toBe(expected);
  return handle;
}

async function selectNote(page, ordinal) {
  const point = await page.evaluate((o) => window.__scoreEditor.headPoint(o), ordinal);
  expect(point, `note ${ordinal} has a clickable head`).toBeTruthy();
  await page.mouse.click(point.x, point.y);
  await expect(wrap(page)).toHaveAttribute("data-editor-selected", String(ordinal));
}

// Backspace the sounding note at `ordinal` into a rest (deleteNote), leaving
// `expectedRests` rests behind - the only way this profile can MAKE a rest, and
// so the setup for every conversion below. EDITOR_MUSICXML ships with none.
async function deleteNoteToRest(page, ordinal, expectedRests, expectedNotes) {
  await selectNote(page, ordinal);
  await page.keyboard.press("Backspace");
  await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(expectedRests);
  // The RENDER's own count, not just the model's: deleteNote rebuilds the model
  // before it awaits the re-render, so a head position read too early would be
  // the previous layout's.
  await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? -1)).toBe(expectedNotes);
}

// Everything the conversion is supposed to keep or write on ONE <note> element
// of a MusicXML string, addressed by its position among ALL <note> elements
// (document order) - a rest carries no Rule 17 id to look it up by, and the
// element it becomes inherits that. Read in the page's own DOMParser.
async function noteElementAt(page, xml, index) {
  return page.evaluate(
    ({ xml, index }) => {
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const el = [...doc.getElementsByTagName("note")][index];
      if (!el) return null;
      const pitch = el.getElementsByTagName("pitch")[0];
      const technical = el.getElementsByTagName("technical")[0];
      return {
        // The element's own child ELEMENT names in order. MusicXML's <note> is
        // an ordered sequence, so this is what an XSD check on the saved
        // document is actually looking at - asserted here because the browser
        // suite stubs the transport and never reaches the server's own
        // schema validation.
        children: [...el.children].map((c) => c.tagName),
        isRest: !!el.getElementsByTagName("rest")[0],
        duration: Number(el.getElementsByTagName("duration")[0]?.textContent) || 0,
        voice: el.getElementsByTagName("voice")[0]?.textContent ?? null,
        type: el.getElementsByTagName("type")[0]?.textContent ?? null,
        dots: [...el.children].filter((c) => c.tagName === "dot").length,
        step: pitch?.getElementsByTagName("step")[0]?.textContent ?? null,
        alter: pitch ? Number(pitch.getElementsByTagName("alter")[0]?.textContent ?? 0) : null,
        octave: pitch ? Number(pitch.getElementsByTagName("octave")[0]?.textContent) : null,
        string: technical ? Number(technical.getElementsByTagName("string")[0]?.textContent) : null,
        fret: technical ? Number(technical.getElementsByTagName("fret")[0]?.textContent) : null,
      };
    },
    { xml, index },
  );
}

// The per-voice sum of note+rest <duration> in a measure (Rule 8's own
// arithmetic, chord members counted once) - the "bar still sums" check, the
// same shape score-editor-rhythm-183.spec.js uses.
async function measureShape(page, xml, measureNumber) {
  return page.evaluate(
    ({ xml, measureNumber }) => {
      const doc = new DOMParser().parseFromString(xml, "application/xml");
      const m = [...doc.getElementsByTagName("measure")].find(
        (el) => el.getAttribute("number") === String(measureNumber),
      );
      const voices = {};
      for (const child of m.children) {
        if (child.tagName !== "note") continue;
        if (child.getElementsByTagName("chord")[0]) continue;
        const v = child.getElementsByTagName("voice")[0]?.textContent ?? "?";
        const d = Number(child.getElementsByTagName("duration")[0]?.textContent) || 0;
        voices[v] = (voices[v] ?? 0) + d;
      }
      return voices;
    },
    { xml, measureNumber },
  );
}

const docText = (page) => page.evaluate(() => window.__scoreEditorHarness.text());

test.describe("note editor - a rest back to a note (#238)", () => {
  // Target 1: a rest is a stop the arrow keys land on - the only way to reach
  // one, since the renderer has no head to hit-test. Includes crossing a
  // barline onto a rest in the previous measure.
  test("arrow keys reach a rest and step off it again, including across a barline", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    // Make two rests: measure 1 beat 2 (ordinal 1) and measure 1 beat 4
    // (ordinal 3 before either delete; ordinal 2 after the first one closes the
    // ordinals up).
    await deleteNoteToRest(page, 1, 1, 7);
    await deleteNoteToRest(page, 2, 2, 6);

    // Six sounding notes left, two rests, and the render agrees with the model.
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(6);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    // From measure 1's first note, ArrowRight lands on the FIRST rest - the
    // note-to-note step would have skipped straight past it.
    await selectNote(page, 0);
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");
    // A rest selection is exclusive with a note selection: no note is selected.
    await expect(wrap(page)).not.toHaveAttribute("data-editor-selected", /.*/);
    // And it reports the rest's own written values, the ones the conversion
    // must keep.
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest-duration", "480");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest-type", "quarter");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest-voice", "1");

    // Stepping on again reaches the next sounding note, and stepping back
    // returns to the same rest - the walk is symmetric.
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "1");
    await page.keyboard.press("ArrowLeft");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");

    // Across the barline: measure 2's first sounding note is ordinal 2 now
    // (measure 1 kept two of its four notes). ArrowLeft from it steps back over
    // the barline onto measure 1's LAST element, the second rest.
    await selectNote(page, 2);
    await page.keyboard.press("ArrowLeft");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest-duration", "480");
  });

  // Target 2: the conversion itself keeps everything that decides the bar's
  // arithmetic, and the bar still sums afterwards.
  test("a digit on a selected rest makes a note that keeps the rest's duration, type, dots and voice, and the bar still sums", async ({
    page,
  }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    await deleteNoteToRest(page, 1, 1, 7);

    // The bar sums to 1920 in voice 1 with the rest in it - the baseline the
    // conversion must not disturb.
    const withRest = await measureShape(page, await docText(page), 1);
    expect(withRest).toEqual({ 1: 1920 });
    // The rest element sits second in document order, carrying the deleted
    // note's own duration and type.
    const restEl = await noteElementAt(page, await docText(page), 1);
    expect(restEl).toMatchObject({ isRest: true, duration: 480, voice: "1", type: "quarter", dots: 0 });

    await selectNote(page, 0);
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");

    // A digit turns it into a note, and the new note is what is now selected -
    // at the ordinal the rest's position occupies (1), with that fret.
    await page.keyboard.press("5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(8);
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(0);

    // The element in that same position is no longer a rest, and every value
    // that decides how long it is and which timeline it belongs to survived
    // untouched.
    const noteEl = await noteElementAt(page, await docText(page), 1);
    expect(noteEl).toMatchObject({ isRest: false, duration: 480, voice: "1", type: "quarter", dots: 0 });
    expect(noteEl.step, "the converted rest has a pitch").not.toBeNull();
    expect(noteEl.fret).toBe(5);

    // And the bar sums to exactly what it summed to with the rest in it.
    const afterConversion = await measureShape(page, await docText(page), 1);
    expect(afterConversion).toEqual(withRest);

    // The whole-model audit: the written MusicXML re-imports to the model on
    // screen, across every note.
    const audit = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(audit.divergences, JSON.stringify(audit.divergences)).toEqual([]);
    expect(audit.ok).toBe(true);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // Target 3: the pitch written is the one the instrument's tuning gives for
  // the CHOSEN string at that fret - the fret alone does not decide it.
  test("the new note's pitch is the tuning's pitch for the chosen string and fret", async ({ page }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    await deleteNoteToRest(page, 1, 1, 7);
    await selectNote(page, 0);
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");

    // Standard six-string tuning as this fixture writes it, with Rule 5's
    // mirror applied (<string> numbers from the top, <staff-tuning line> from
    // the bottom): <string>3 reads line 4, G3, MIDI 55 - so fret 5 is MIDI 60,
    // C4. The SAME fret on string 1 (E4, MIDI 64) would be A4 instead, which is
    // what makes this an assertion about the string and not just the fret.
    await restStringSelect(page).selectOption("3");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest-string", "3");
    await page.keyboard.press("5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "1");

    await expect(wrap(page)).toHaveAttribute("data-editor-selected-string", "3");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "5");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-midi", "60");

    const noteEl = await noteElementAt(page, await docText(page), 1);
    expect(noteEl).toMatchObject({ string: 3, fret: 5, step: "C", alter: 0, octave: 4 });

    // The same fret on a different string would have been a different pitch:
    // this is the claim a "fret derived from the wrong string" mutation breaks.
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
    const audit = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(audit.ok, JSON.stringify(audit.divergences)).toBe(true);
  });

  // Target 4: and back. The note the conversion made deletes to a rest again -
  // the round trip the issue asks for - with the bar summing and the render
  // agreeing at both ends.
  test("the converted note deletes back to a rest, with the bar summing and the render agreeing both ways", async ({
    page,
  }) => {
    await openEditor(page, EDITOR_MUSICXML, 8);
    await deleteNoteToRest(page, 1, 1, 7);
    const withRest = await measureShape(page, await docText(page), 1);

    await selectNote(page, 0);
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");
    await page.keyboard.press("7");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "1");
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(8);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    // Backspace on the note the conversion just made: back to a rest, the same
    // count and the same bar shape it started at.
    await page.keyboard.press("Backspace");
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(1);
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.count())).toBe(7);

    const backToRest = await noteElementAt(page, await docText(page), 1);
    expect(backToRest).toMatchObject({ isRest: true, duration: 480, voice: "1", type: "quarter" });
    expect(await measureShape(page, await docText(page), 1)).toEqual(withRest);

    // The renderer and the document still agree after the round trip.
    const audit = await page.evaluate(() => window.__scoreEditorHarness.audit());
    expect(audit.divergences, JSON.stringify(audit.divergences)).toEqual([]);
    expect(audit.ok).toBe(true);
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");

    // The rest is still reachable by arrow after the round trip, so the two
    // operations compose rather than leaving the selection stranded.
    await selectNote(page, 0);
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");
  });

  // Target 5: the converted document goes out through the real save path and
  // comes back with the note in it.
  test("a converted rest saves through the transcription path and is still a note after a reload", async ({ page }) => {
    const { saved } = await openEditor(page, EDITOR_MUSICXML, 8);
    await deleteNoteToRest(page, 1, 1, 7);
    await selectNote(page, 0);
    await page.keyboard.press("ArrowRight");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-rest", "0");
    await restStringSelect(page).selectOption("2");
    await page.keyboard.press("9");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected", "1");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "9");

    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-dirty", "false");
    await expect.poll(() => saved.content).not.toBeNull();

    // What was persisted: the element in the rest's old position is a note on
    // string 2 fret 9 (B3 + 9 = G#4/Ab4), still 480 long, still voice 1 - and
    // no <rest/> left in that measure.
    const persisted = await noteElementAt(page, saved.content, 1);
    expect(persisted).toMatchObject({ isRest: false, duration: 480, voice: "1", type: "quarter", string: 2, fret: 9 });
    expect(await measureShape(page, saved.content, 1)).toEqual({ 1: 1920 });
    // The children the conversion left behind are in MusicXML's own order for a
    // pitched note - <pitch> where the <rest/> was, the printed <accidental>
    // before the <notations> it belongs before, <notations> last - which is
    // what the server's schema check would reject if the new elements were
    // merely appended. A rest that never carried <notations> or an
    // <accidental> is exactly where an out-of-order insert would go unnoticed
    // until a validating save. (String 2 fret 9 is MIDI 68, which C major
    // spells A flat, so this note does carry a printed accidental.)
    expect(persisted.children).toEqual(["pitch", "duration", "voice", "type", "accidental", "notations"]);
    // Every <octave> stays inside MusicXML's 0-9 range, so the saved document
    // is not one a validating consumer would reject (Rule 11).
    expect(saved.content).not.toMatch(/<octave>(1\d|\d\d\d)<\/octave>/);

    // Reload the whole page: the stub serves the saved (source='edited')
    // content, so re-opening the editor shows eight sounding notes again with
    // the converted one among them.
    await page.reload();
    await page.waitForSelector(".staff-render");
    await page.getByRole("button", { name: "Staff", exact: true }).click();
    await renderedOk(page);
    await page.getByRole("button", { name: "Edit notes" }).click();
    await expect(wrap(page)).toHaveAttribute("data-editor-active", "true");
    await expect.poll(() => page.evaluate(() => window.__scoreEditor?.noteCount() ?? 0)).toBe(8);
    await expect.poll(() => page.evaluate(() => window.__scoreEditorHarness.restCount())).toBe(0);
    await selectNote(page, 1);
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-fret", "9");
    await expect(wrap(page)).toHaveAttribute("data-editor-selected-string", "2");
    await expect(wrap(page)).toHaveAttribute("data-editor-divergence-ok", "true");
  });

  // Target 6: the rest address space is the document's rests, in document
  // order, across measures and across voices - not a per-measure or
  // single-voice index. The polyphonic fixture ships two rests, in different
  // measures and different voices.
  test("rests are addressed in document order across measures and voices", async ({ page }) => {
    await openEditor(page, POLY_MUSICXML, 52);

    const rests = await page.evaluate(() => {
      const h = window.__scoreEditorHarness;
      return Array.from({ length: h.restCount() }, (_, i) => h.restAt(i));
    });
    expect(rests).toHaveLength(2);
    // Measure 3, voice 1: a quarter rest. Measure 8, voice 2: a half rest.
    expect(rests[0]).toMatchObject({ restOrdinal: 0, measure: 3, voice: 1, type: "quarter", duration: 480 });
    expect(rests[1]).toMatchObject({ restOrdinal: 1, measure: 8, voice: 2, type: "half", duration: 960 });
    // Each carries the onset its own voice's timeline puts it at (both fall on
    // beat 3 of their bar, 960 divisions in), so the address is the document's
    // rest list and the measure is what separates them.
    expect(rests[0].onset).toBe(960);
    expect(rests[1].onset).toBe(960);
    // Out of range is null on both sides rather than a throw or a wrap-around.
    const edges = await page.evaluate(() => {
      const h = window.__scoreEditorHarness;
      return { below: h.restAt(-1), above: h.restAt(h.restCount()) };
    });
    expect(edges.below).toBeNull();
    expect(edges.above).toBeNull();
  });
});
