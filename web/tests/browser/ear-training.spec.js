// Hear a note, name it - against the real backend, the real build and the real
// synthesiser.
//
// What these are for that tests/unit/ear-training.spec.js cannot do: whether a
// question exists at all depends on a soundfont arriving over HTTP and on
// alphaTab building a player with no score loaded, and the one property this
// exercise lives or dies by is that the question is built from WHAT WAS
// SOUNDED rather than from what the component meant to sound. A mocked audio
// path cannot tell us that; it is exactly the assertion a mock would make true
// for free.
//
// So every test below reads the correct answer out of data-sounded-midi, which
// the component sets from the value playPitch RESOLVED with - the number
// written into the note-on event - and never from the target it picked. Delete
// the audio path and there is no correct answer to read and no choices to
// click, which is what makes these tests fail rather than pass more quietly.
import { expect, test } from "@playwright/test";

import { spellMidi } from "../../src/lib/pitch.js";
import { forbiddenWord, localDay } from "../../src/lib/practice.js";

const drill = (page) => page.locator("section.drill");
const choices = (page) => page.locator(".choice");
const statement = (page) => page.locator(".round-statement");
const progress = (page) => page.locator(".progress");
const startButton = (page) => page.locator(".start-drill");
const referenceNote = (page) => page.locator(".reference-note");
// Shared so that every "nothing went wrong" assertion uses the SAME selector a
// test in this file proves can match something. A toHaveCount(0) built from an
// inline literal is permanently true the moment a class is renamed.
const notices = (page) => page.locator("section.drill .notice");

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

/** An instrument, created through the API rather than through the editor - the
 * editor has its own suite and what this file needs is a definition with known
 * strings. */
async function addInstrument(request, definition) {
  const res = await request.post("/api/instruments", { data: definition });
  expect(res.ok(), await res.text()).toBe(true);
  return res.json();
}

const BASS = {
  kind: "string",
  name: "Bass (four string)",
  fretted: true,
  string_count: 4,
  string_pitches: ["E1", "A1", "D2", "G2"],
  fret_count: 24,
  capo: 0,
  reference_pitch: 440,
};

test.beforeEach(async ({ page, request }) => {
  // Refuses to touch anything that is not the throwaway instance this suite
  // starts. The reset above DELETES instruments and practice sessions, and
  // practice history is the one thing in this application that cannot be
  // regenerated from the files on disk.
  const scores = await (await request.get("/api/scores")).json();
  expect(
    scores,
    "refusing to run: this backend has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and these tests delete practice history",
  ).toEqual([]);

  await reset(request);

  // Independent evidence that a click reaches real audio machinery and not a
  // counter beside it.
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

  await page.goto("/#/ear-training");
  await expect(drill(page)).toBeVisible();
});

/** The MIDI note the synthesiser was actually handed, off the section. Never
 * the note the component intended - that is the whole point. */
async function soundedMidi(page) {
  const value = await drill(page).getAttribute("data-sounded-midi");
  expect(value, "nothing has been sounded, so there is no answer to read").not.toBe("");
  return Number(value);
}

async function choiceMidis(page) {
  return choices(page).evaluateAll((els) => els.map((el) => Number(el.dataset.midi)));
}

async function startDrill(page) {
  await expect(drill(page)).toHaveAttribute("data-sounded-count", "0");
  await startButton(page).click();
  // The whole audio path has to run for this to move: a player built with no
  // score loaded, a soundfont fetched over HTTP, and a hand-built MidiFile the
  // synth accepts. Generous - the soundfont is about a megabyte.
  await expect(drill(page)).toHaveAttribute("data-sounded-count", "1", { timeout: 30_000 });
  await expect(choices(page)).toHaveCount(4);
}

async function nextNote(page, expectedSoundings) {
  await page.locator(".next-note").click();
  await expect(drill(page)).toHaveAttribute(
    "data-sounded-count",
    String(expectedSoundings),
    { timeout: 30_000 },
  );
  await expect(choices(page)).toHaveCount(4);
}

/** Answer the round on screen: the note that sounded, or a different one. */
async function answer(page, { as }) {
  const sounded = await soundedMidi(page);
  const midis = await choiceMidis(page);
  const pick = as === "heard" ? sounded : midis.find((m) => m !== sounded);
  await page.locator(`.choice[data-midi="${pick}"]`).click();
  await expect(statement(page)).toBeVisible();
  return { sounded, chosen: pick };
}

test("a drill sounds a note and offers four notes built around what was heard", async ({
  page,
}) => {
  const soundfontRequests = [];
  page.on("request", (r) => {
    if (/soundfont|\.sf2/.test(r.url())) soundfontRequests.push(r.url());
  });

  await startDrill(page);

  const sounded = await soundedMidi(page);
  const midis = await choiceMidis(page);
  expect(midis).toHaveLength(4);
  expect(new Set(midis).size, `choices ${midis}`).toBe(4);
  // The note that ACTUALLY sounded is one of the four. Not the note the
  // component picked: this attribute is set from what playPitch resolved with,
  // so a drill that sounded something else would be offering choices around the
  // wrong note and this is where that shows.
  expect(midis, `sounded ${sounded}`).toContain(sounded);

  // And the other three are worth confusing rather than four notes far apart:
  // one a semitone away, one the same name in another octave, one a step or two
  // off. Checked on the notes the interface actually put on screen.
  const others = midis.filter((m) => m !== sounded);
  expect(others.filter((m) => Math.abs(m - sounded) === 1), `choices ${midis}`).toHaveLength(1);
  expect(others.filter((m) => (m - sounded) % 12 === 0), `choices ${midis}`).toHaveLength(1);
  expect(
    others.filter((m) => Math.abs(m - sounded) >= 2 && Math.abs(m - sounded) <= 5),
    `choices ${midis}`,
  ).toHaveLength(1);
  // Each is spelled where a person can read it.
  const shown = await choices(page)
    .locator(".choice-pitch")
    .evaluateAll((els) => els.map((el) => el.textContent.trim()));
  expect(shown).toEqual(midis.map((m) => spellMidi(m)));

  // Nothing on screen marks which is right until an answer is given. Anchored:
  // the test below proves this selector matches after one is.
  await expect(page.locator(".choice.correct")).toHaveCount(0);
  await expect(statement(page)).toHaveCount(0);
  await expect(notices(page)).toHaveCount(0);

  expect(soundfontRequests.length).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.__audioContexts)).toBeGreaterThan(0);
});

test("naming the note that sounded says what it was, and the counts move", async ({ page }) => {
  await startDrill(page);
  await expect(progress(page)).toHaveText("Nothing named yet.");

  const { sounded } = await answer(page, { as: "heard" });

  await expect(statement(page)).toHaveText(`That was ${spellMidi(sounded)}.`);
  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-named", "1");
  await expect(progress(page)).toHaveText("1 note, 1 named as heard.");

  // The mark is on the note that sounded, and on no other. Presence is not
  // placement: a tick rendered at section level reads as belonging to a row.
  const marked = page.locator(".choice.correct");
  await expect(marked).toHaveCount(1);
  await expect(marked).toHaveAttribute("data-midi", String(sounded));
  const tick = marked.locator(".tick");
  const inside = await tick.boundingBox();
  const button = await marked.boundingBox();
  expect(inside.x, JSON.stringify({ inside, button })).toBeGreaterThanOrEqual(button.x - 1);
  expect(inside.x + inside.width).toBeLessThanOrEqual(button.x + button.width + 1);
  expect(inside.y).toBeGreaterThanOrEqual(button.y - 1);
  expect(inside.y + inside.height).toBeLessThanOrEqual(button.y + button.height + 1);

  await expect(notices(page)).toHaveCount(0);
});

test("naming a different note says which one, in exactly the words and colours of the other case", async ({
  page,
}) => {
  // The tone rule, and the reason this exercise is the easiest place in a
  // practice tool to get it wrong. A wrong answer in ear training is the
  // practice, not a shortfall: it is stated, and nothing else happens to it.
  const style = (locator) =>
    locator.evaluate((el) => {
      const s = getComputedStyle(el);
      return {
        color: s.color,
        background: s.backgroundColor,
        weight: s.fontWeight,
        size: s.fontSize,
      };
    });

  await startDrill(page);
  const first = await answer(page, { as: "something else" });
  expect(first.chosen).not.toBe(first.sounded);

  await expect(statement(page)).toHaveText(
    `That was ${spellMidi(first.sounded)}. You chose ${spellMidi(first.chosen)}.`,
  );
  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(drill(page)).toHaveAttribute("data-named", "0");
  await expect(progress(page)).toHaveText("1 note, none named as heard.");
  // The mark still names what the note WAS - which is the information - and the
  // note that was picked is shown as picked rather than as an error.
  await expect(page.locator(".choice.correct")).toHaveAttribute(
    "data-midi",
    String(first.sounded),
  );
  await expect(page.locator(".choice.picked")).toHaveAttribute("data-midi", String(first.chosen));
  const unnamed = await style(statement(page));
  // Read now, while there is a picked-but-not-heard note to read: the next round
  // is answered as heard, and there is deliberately no such mark then.
  const pickedColour = await page
    .locator(".choice.picked")
    .evaluate((el) => getComputedStyle(el).borderColor);

  // ...and now the same page after a note that WAS named as heard.
  await nextNote(page, 2);
  const second = await answer(page, { as: "heard" });
  await expect(statement(page)).toHaveText(`That was ${spellMidi(second.sounded)}.`);
  const named = await style(statement(page));

  expect(unnamed, JSON.stringify({ unnamed, named })).toEqual(named);

  // And specifically not red: --danger is how this application spells a fault,
  // and nothing about a wrong answer here is one. The token is read off the page
  // and resolved through a probe, because it holds a hex string while
  // getComputedStyle answers in rgb().
  const danger = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--danger").trim(),
  );
  expect(danger, "--danger is expected to exist, or this assertion proves nothing").not.toBe("");
  const dangerRgb = await page.evaluate((hex) => {
    const probe = document.createElement("span");
    probe.style.color = hex;
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }, danger);
  expect(unnamed.color).not.toBe(dangerRgb);
  expect(pickedColour).not.toBe(dangerRgb);
  // And no mark at all on a round that was named as heard - "you chose this" is
  // information about a note you did not hear, and there is nothing to add when
  // the one you chose is the one that sounded.
  await expect(page.locator(".choice.picked")).toHaveCount(0);
});

test("the note can be heard again before and after answering, and that is not counted", async ({
  page,
}) => {
  // From the issue: "the option to hear it again before and after answering,
  // because the point is to attach the sound to the name". Hearing a note five
  // times is practising, not cheating, so nothing counts it - a number that only
  // ever rises when somebody struggles is a score wearing a different name.
  await startDrill(page);
  const sounded = await soundedMidi(page);

  await page.locator(".hear-again").click();
  await expect(drill(page)).toHaveAttribute("data-sounded-count", "2", { timeout: 30_000 });
  // The REPLAY sounded the same note, which is what makes it a replay: read off
  // what the synthesiser was handed the second time, not off what was stored.
  await expect(drill(page)).toHaveAttribute("data-sounded-midi", String(sounded));
  await expect(drill(page)).toHaveAttribute("data-asked", "0");
  // and the question is unchanged - a replay must not reshuffle the choices
  expect(await choiceMidis(page)).toContain(sounded);

  await answer(page, { as: "heard" });
  await expect(drill(page)).toHaveAttribute("data-asked", "1");

  // Again, now that the name is known - which is the more useful of the two.
  await page.locator(".hear-again").click();
  await expect(drill(page)).toHaveAttribute("data-sounded-count", "3", { timeout: 30_000 });
  await expect(drill(page)).toHaveAttribute("data-sounded-midi", String(sounded));
  await expect(drill(page)).toHaveAttribute("data-asked", "1");
  await expect(progress(page)).toHaveText("1 note, 1 named as heard.");
});

test("answering does not move the choices out from under the cursor", async ({ page }) => {
  // Placement, and only geometry can see it. The statement about what the note
  // was appears above the four buttons, so unless its space is held open the
  // whole question slides down at the exact moment a hand is over it - and every
  // assertion about the wording still passes. The tick inside a button is the
  // same problem one level down, which is why it is rendered empty rather than
  // absent.
  await startDrill(page);
  const boxes = async () =>
    choices(page).evaluateAll((els) =>
      els.map((el) => {
        const r = el.getBoundingClientRect();
        return { midi: el.dataset.midi, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
      }),
    );
  const before = await boxes();
  const hearBefore = await page.locator(".hear-again").boundingBox();
  // Anchored: this is the element whose arrival is what would move things, and
  // the assertion after the click proves the selector matches.
  await expect(statement(page)).toHaveCount(0);

  await answer(page, { as: "heard" });
  await expect(statement(page)).toHaveCount(1);

  expect(await boxes()).toEqual(before);
  const hearAfter = await page.locator(".hear-again").boundingBox();
  expect(Math.round(hearAfter.x)).toBe(Math.round(hearBefore.x));
  expect(Math.round(hearAfter.y)).toBe(Math.round(hearBefore.y));
});

test("stopping the drill logs one ear-training session, and the note says what was done", async ({
  page,
  request,
}) => {
  await startDrill(page);
  await answer(page, { as: "heard" });
  await nextNote(page, 2);
  await answer(page, { as: "something else" });

  await page.locator(".stop-drill").click();
  const logged = page.locator(".logged");
  await expect(logged).toBeVisible();
  await expect(logged).toContainText("of ear training is in your practice history");
  await expect(logged).toContainText("2 notes, 1 named as heard.");
  await expect(notices(page)).toHaveCount(0);
  // The drill is over, so there is nothing left to answer. Anchored on a
  // selector the tests above prove can match.
  await expect(choices(page)).toHaveCount(0);

  const { sessions, total } = await (
    await request.get("/api/practice/sessions?limit=1000")
  ).json();
  expect(total).toBe(1);
  const [session] = sessions;
  // The vocabulary docs/practice-data.md has always carried a slot for, so this
  // lands in the same table as a piece and a stretch of technique.
  expect(session.activity).toBe("ear_training");
  expect(session.score_id).toBeNull();
  expect(session.mode).toBeNull();
  // The practiser's own calendar day, not the server's UTC one.
  expect(session.local_date).toBe(today);
  expect(session.local_date_source).toBe("recorded");
  expect(session.seconds).toBeGreaterThanOrEqual(1);
  // Nothing invented beside the time: no rating, no tempo. A drill has neither
  // and a stored zero would be a number somebody could later average.
  expect(session.rating).toBeNull();
  expect(session.tempo_bpm).toBeNull();
  expect(session.target_tempo_bpm).toBeNull();

  // The counts and the range go in the free-text note. docs/practice-data.md
  // says a per-attempt table is not being invented for the first trainer, and
  // this is where that decision lands.
  expect(session.note).toBe("Hear a note, name it. 2 notes, 1 named as heard. C2 to C6.");
  expect(forbiddenWord(session.note), session.note).toBeNull();
  expect(session.note).not.toMatch(/%/);
});

test("that session shows on the practice page and counts towards a goal about ear training", async ({
  page,
  request,
}) => {
  // The claim this exercise is worth building at all rests on: a drill's time
  // is practice, in the same history, against the same goals, with no special
  // case anywhere.
  const goal = await request.post(`/api/practice/goals?today=${today}`, {
    data: {
      target_days: 1,
      target_minutes: null,
      scope: "activity",
      activity: "ear_training",
      intent: "hear the difference between a semitone and nothing",
    },
  });
  expect(goal.ok(), await goal.text()).toBe(true);

  await startDrill(page);
  await answer(page, { as: "heard" });
  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();

  await page.goto("/#/practice");
  await expect(page.locator("section.week")).toBeVisible();

  // The goal it was scoped to, counted from the session the drill just wrote.
  await expect(page.locator("section.week .scope")).toHaveText("ear training");
  await expect(page.locator('section.week .statement[data-statement="days"]')).toContainText(
    "1 of 1 planned days",
  );
  // Stated, not graded: the reached mark is the only difference between a target
  // met and one not, and this is the same tick the rest of the page uses.
  await expect(page.locator('section.week .statement[data-statement="days"]')).toHaveClass(
    /reached/,
  );

  // And the session itself, named as the kind of work it was.
  const rows = page.locator(".session-list .session");
  await expect(rows).toHaveCount(1);
  await expect(rows.first().locator(".session-what")).toHaveText("Ear training");
  await expect(rows.first().locator(".session-extra")).toContainText("Hear a note, name it.");
  // The day was recorded rather than worked out from a timestamp.
  await expect(rows.first().locator(".day-inferred")).toHaveCount(0);
  // Where the time went knows what kind of work it was, too.
  await expect(page.locator(".by-activity")).toContainText("Ear training");
});

test("leaving the page in the middle of a drill still logs the practice", async ({
  page,
  request,
}) => {
  // Practice history is the one thing in Fermata that cannot be regenerated
  // from the files on disk, so walking away from a drill must not throw away
  // the time it took. The route change unmounts the component, and the log goes
  // out from its teardown.
  await startDrill(page);
  await answer(page, { as: "heard" });

  await page.locator(".back").click();
  await expect(page.locator("section.drill")).toHaveCount(0);

  await expect(async () => {
    const { total } = await (await request.get("/api/practice/sessions?limit=1000")).json();
    expect(total).toBe(1);
  }).toPass({ timeout: 10_000 });

  const { sessions } = await (await request.get("/api/practice/sessions?limit=1000")).json();
  expect(sessions[0].activity).toBe("ear_training");
  expect(sessions[0].note).toBe("Hear a note, name it. 1 note, 1 named as heard. C2 to C6.");
});

test("with one instrument defined the drill follows it, and never leaves its range", async ({
  page,
  request,
}) => {
  // A range that follows the instrument is the version of this exercise worth
  // doing - and with exactly one instrument defined there is nothing to guess
  // about which one is in somebody's hands.
  await addInstrument(request, BASS);
  await page.reload();
  await expect(drill(page)).toBeVisible();

  await expect(page.locator(".range-source")).toHaveValue(/^\d+$/);
  // E1 (28) is the lowest string; the top string G2 (43) plus the 24 frets the
  // definition declares is G4 (67). Reading the strings alone would offer a
  // drill two octaves narrower than the instrument.
  await expect(page.locator(".range-label")).toHaveText("E1 to G4, Bass (four string)");
  await expect(drill(page)).toHaveAttribute("data-range", "28-67");

  await startDrill(page);
  for (let round = 1; round <= 4; round++) {
    if (round > 1) await nextNote(page, round);
    const sounded = await soundedMidi(page);
    expect(sounded, `round ${round}`).toBeGreaterThanOrEqual(28);
    expect(sounded, `round ${round}`).toBeLessThanOrEqual(67);
    for (const midi of await choiceMidis(page)) {
      expect(midi, `round ${round} choices`).toBeGreaterThanOrEqual(28);
      expect(midi, `round ${round} choices`).toBeLessThanOrEqual(67);
    }
    await answer(page, { as: "heard" });
  }
  await expect(progress(page)).toHaveText("4 notes, 4 named as heard.");

  // ...and the range is what the session says it was practised in.
  await page.locator(".stop-drill").click();
  await expect(page.locator(".logged")).toBeVisible();
  const { sessions } = await (await request.get("/api/practice/sessions?limit=1000")).json();
  expect(sessions[0].note).toBe(
    "Hear a note, name it. 4 notes, 4 named as heard. E1 to G4, Bass (four string).",
  );
});

test("with two instruments defined neither is adopted, because which one is in your hands is not known", async ({
  page,
  request,
}) => {
  // Fermata has a list of instruments and no notion of a current one. Adopting
  // the first alphabetically would be a guess, and a drill silently fitted to
  // the wrong instrument is worse than one that says it is using a plain range.
  await addInstrument(request, BASS);
  await addInstrument(request, {
    ...BASS,
    name: "Violin",
    fretted: false,
    string_count: 4,
    string_pitches: ["G3", "D4", "A4", "E5"],
    fret_count: null,
    capo: null,
  });
  await page.reload();
  await expect(drill(page)).toBeVisible();

  const options = page.locator(".range-source option");
  // Proved to have loaded, so the empty selection below is a choice and not a
  // failed fetch.
  await expect(options).toHaveCount(3);
  await expect(options.nth(1)).toHaveText("Bass (four string)");
  await expect(options.nth(2)).toHaveText("Violin");
  await expect(page.locator(".range-source")).toHaveValue("");
  await expect(page.locator(".range-label")).toHaveText("C2 to C6");
  await expect(drill(page)).toHaveAttribute("data-range", "36-84");

  // Choosing one takes effect, and an unfretted definition says why its range
  // stops where it does - a violinist looking at a range that ends on their top
  // string deserves to know it is the definition talking.
  await page.selectOption(".range-source", { label: "Violin" });
  await expect(page.locator(".range-label")).toHaveText("G3 to E5, Violin");
  await expect(drill(page)).toHaveAttribute("data-range", "55-76");
  await expect(page.locator(".range-source-note")).toContainText("unfretted definition");
  // and a fretted one has nothing to explain
  await page.selectOption(".range-source", { label: "Bass (four string)" });
  await expect(page.locator(".range-source-note")).toHaveCount(0);
});

test("a definition that spans one note says so instead of asking an impossible question", async ({
  page,
  request,
}) => {
  // Reachable through the ordinary interface: a one-string unfretted instrument
  // is a real thing to own, and its definition names exactly one pitch.
  await addInstrument(request, {
    kind: "string",
    name: "One string",
    fretted: false,
    string_count: 1,
    string_pitches: ["G3"],
    fret_count: null,
    capo: null,
    reference_pitch: 440,
  });
  await page.reload();
  await expect(drill(page)).toBeVisible();

  await expect(page.locator(".narrow-range")).toContainText("spans one note");
  await expect(page.locator(".narrow-range")).toContainText("not four to choose between");
  // No drill is offered, and the same selector is proved to match a moment
  // later - so this is a button that is genuinely absent rather than a class
  // that never existed.
  await expect(startButton(page)).toHaveCount(0);

  await page.selectOption(".range-source", "");
  await expect(startButton(page)).toBeVisible();
  await expect(page.locator(".narrow-range")).toHaveCount(0);
});

test("an instrument defined away from A440 is told it is not being sounded at its own reference", async ({
  page,
  request,
}) => {
  // playPitch's own note: the synthesiser is equal-tempered around A440 and
  // takes no reference, so an instrument defined at A415 has its frequencies
  // SHOWN at A415 and is SOUNDED at A440. A drill that used such a definition
  // silently would be teaching names against pitches that are not the player's.
  await addInstrument(request, { ...BASS, name: "Baroque bass", reference_pitch: 415 });
  await page.reload();
  await expect(drill(page)).toBeVisible();

  await expect(referenceNote(page)).toContainText("Baroque bass is defined at A415");
  await expect(referenceNote(page)).toContainText("fixed at A440");

  // And nothing is said about an instrument that agrees with it - a disclosure
  // printed unconditionally is one nobody reads. Anchored on the assertion
  // above, which proves this selector matches something.
  await addInstrument(request, { ...BASS, name: "Modern bass", reference_pitch: 440 });
  await page.reload();
  await expect(drill(page)).toBeVisible();
  await page.selectOption(".range-source", { label: "Modern bass" });
  await expect(page.locator(".range-label")).toHaveText("E1 to G4, Modern bass");
  await expect(referenceNote(page)).toHaveCount(0);
});

test("a synthesiser that will not load says so rather than leaving a silent drill, and can be retried", async ({
  page,
}) => {
  // Without this the click looks like it worked: no sound, no question, no
  // explanation. The audition resets itself on failure precisely so the next
  // attempt is a fresh one rather than a cached rejection for the life of the
  // page.
  let failing = true;
  await page.route(/soundfont|\.sf2/, (route) =>
    failing ? route.abort("failed") : route.continue(),
  );

  await startButton(page).click();
  const failure = page.locator(".sound-error");
  await expect(failure).toBeVisible({ timeout: 30_000 });
  await expect(failure).toContainText("soundfont");
  // No question is asked about a note that never sounded. Anchored: the retry
  // below proves this selector matches when there is one.
  await expect(choices(page)).toHaveCount(0);
  await expect(drill(page)).toHaveAttribute("data-sounded-count", "0");
  await expect(statement(page)).toHaveCount(0);

  failing = false;
  await failure.locator(".retry-sound").click();
  await expect(drill(page)).toHaveAttribute("data-sounded-count", "1", { timeout: 30_000 });
  await expect(choices(page)).toHaveCount(4);
  await expect(notices(page)).toHaveCount(0);
});
