// Fixture data + route stubs for tests/browser/navigation.spec.js (issue #151).
//
// Real MusicXML documents through the real importer and the real player, for
// the same reason fixtures/metronome-score.js states: what is being tested
// here is whether the renderer's own midi comes out in the right bar order,
// and nothing short of the real loader can answer that.
//
// THE HEADLINE FIXTURE IS NOT BUILT HERE. It is read, byte for byte, off
// server/tests/fixtures/engraved/navigation.musicxml - the transcription of
// the committed navigation.pdf, produced by the extractor and checked into
// the repository by issue #134. That file is the acceptance case, and a
// hand-written imitation of it would only prove that this file and the spec
// beside it agree with each other. The two built fixtures below cover the
// shapes that transcription does NOT have: a jump crossing a repeat, and a
// jump whose target the score never draws.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const here = path.dirname(fileURLToPath(import.meta.url));

/**
 * The committed transcription of navigation.pdf: eight 4/4 bars carrying a
 * segno on bar 1, a To Coda closing bar 2, a D.S. al Coda closing bar 4, a
 * coda opening bar 6, a Fine closing bar 7 and a D.C. al Fine closing bar 8.
 *
 * Read at run time rather than copied, so the spec cannot drift away from the
 * transcription it claims to be about - a change to the emitter that altered
 * this file would show up here as a failing order rather than as nothing at
 * all.
 */
export const NAVIGATION_MUSICXML = fs.readFileSync(
  path.join(here, "..", "..", "..", "..", "server", "tests", "fixtures", "engraved", "navigation.musicxml"),
  "utf-8",
);

const DIVISIONS = 480; // ticks per quarter note
const PITCHES = ["C", "D", "E", "F", "G", "A", "B", "C"];

// Four quarter notes, filling a 4/4 bar exactly. The fixtures below only ever
// need a bar to render and to hold its declared duration; which pitches it
// holds is what makes a played bar audible, not what makes it identifiable -
// the bar ORDER is read from the renderer's own tick lookup, not by ear.
// `interior`, where given, is markup spliced in after the bar's SECOND note -
// the only way to build a direction whose beat-text lands on an interior beat
// rather than on a bar's first one, which is the distinction the echo-clearing
// test below turns on.
function notesForBar(bar, interior = "") {
  return Array.from({ length: 4 }, (_, i) => {
    const step = PITCHES[(bar + i) % PITCHES.length];
    const note = `
      <note>
        <pitch><step>${step}</step><octave>4</octave></pitch>
        <duration>${DIVISIONS}</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>`;
    return i === 1 ? note + interior : note;
  }).join("");
}

const OPENING_ATTRIBUTES = `
      <attributes>
        <divisions>${DIVISIONS}</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <direction placement="above">
        <direction-type>
          <metronome><beat-unit>quarter</beat-unit><per-minute>120</per-minute></metronome>
        </direction-type>
        <sound tempo="120" />
      </direction>`;

// A sign - the segno or the coda - written the way docs/musicxml-tab-profile.md
// Rule 16 writes one: the element MusicXML has for it, BEFORE the measure's
// notes, because it marks that measure's downbeat.
function sign(symbol) {
  return `
      <direction placement="above">
        <direction-type><${symbol} /></direction-type>
        <sound ${symbol}="${symbol}" />
      </direction>`;
}

/**
 * An instruction - written as `<words>`, AFTER the measure's notes, because it
 * fires at the end of that measure (Rule 16 again).
 *
 * `sound` is the attribute pair to write inside the nested `<sound>`, or null
 * for the shape an instruction takes when its target is not in the file at
 * all: words alone, no `<sound>`, the bar counted in `nav_marks_unresolved`.
 * That branch is not a hypothetical - 86 of 297 library scores print "D.S."
 * and two of them draw no segno for it to name.
 */
function instruction(words, sound) {
  const soundElement = sound === null ? "" : `
        <sound ${sound} />`;
  return `
      <direction placement="above">
        <direction-type><words>${words}</words></direction-type>${soundElement}
      </direction>`;
}

function buildScore(bars) {
  const measures = bars.map((bar, i) => {
    const attrs = i === 0 ? OPENING_ATTRIBUTES : "";
    return `    <measure number="${i + 1}">${attrs}${bar.before ?? ""}${notesForBar(i, bar.interior ?? "")}${bar.after ?? ""}${bar.barline ?? ""}
    </measure>`;
  });
  return `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>Navigation test fixture</work-title></work>
  <part-list>
    <score-part id="P1">
      <part-name>Piano</part-name>
      <score-instrument id="P1-I1"><instrument-name>Piano</instrument-name></score-instrument>
      <midi-instrument id="P1-I1"><midi-channel>1</midi-channel><midi-program>1</midi-program></midi-instrument>
    </score-part>
  </part-list>
  <part id="P1">
${measures.join("\n")}
  </part>
</score-partwise>
`;
}

const REPEAT_FORWARD = `
      <barline location="left"><bar-style>heavy-light</bar-style><repeat direction="forward" /></barline>`;
const REPEAT_BACKWARD = `
      <barline location="right"><bar-style>light-heavy</bar-style><repeat direction="backward" /></barline>`;

/**
 * A jump that has to compose with the repeat structure #138 landed: eight 4/4
 * bars, with bars 1-3 inside a repeat, a segno on bar 1, a To Coda closing bar
 * 4, a D.S. al Coda closing bar 6 and a coda opening bar 7.
 *
 * The repeat and the segno deliberately open the same bar, which is where the
 * two features actually meet: the D.S. sends the playhead back to a bar that
 * is also a repeat opening, and what happens to the repeat on that second
 * visit is the whole question.
 */
export const NAVIGATION_REPEAT_MUSICXML = buildScore([
  { before: sign("segno"), barline: REPEAT_FORWARD },
  {},
  { barline: REPEAT_BACKWARD },
  { after: instruction("To Coda", 'tocoda="coda"') },
  {},
  { after: instruction("D.S. al Coda", 'dalsegno="segno"') },
  { before: sign("coda") },
  {},
]);

/**
 * Six 4/4 bars printing two instructions whose targets the score does not
 * draw, in the two shapes that reach the player differently:
 *
 *   - bar 3 closes with "D.S. al Coda" and NO `<sound>` at all - the shape the
 *     extractor writes when it read the words but no segno (Rule 16). Nothing
 *     in the player can act on it, and nothing should try to.
 *   - bar 5 closes with "D.C. al Fine" AND a live `<sound dacapo="yes"/>`,
 *     on a score with no Fine anywhere. The extractor writes `dacapo`
 *     unconditionally, because a D.C.'s target is the start of the score and
 *     that is always there - so this is the one case where a live jump
 *     attribute arrives naming a target that does not exist. Taken at face
 *     value it plays the whole piece twice and stops nowhere near where the
 *     page says to stop.
 *
 * Neither may become a jump, and the score must play 1-6 once.
 */
export const NAVIGATION_UNRESOLVED_MUSICXML = buildScore([
  {},
  {},
  { after: instruction("D.S. al Coda", null) },
  {},
  { after: instruction("D.C. al Fine", 'dacapo="yes"') },
  {},
]);

/**
 * The hang. Six 4/4 bars whose coda sits BEFORE the To Coda that names it,
 * on a score that also carries a resolvable D.S. al Coda:
 *
 *   1 segno · 2 coda · 3 · 4 To Coda · 5 · 6 D.S. al Coda
 *
 * Taken at face value this does not terminate. alphaTab's `_handleDaCoda`
 * searches forwards for the coda, finds none, falls back to a BACKWARDS
 * search, jumps to the coda on bar 2 and resets its state machine to neutral -
 * which re-arms the D.S. al Coda on bar 6, which enters the coda-seeking state
 * again, which finds the same backwards coda. `MidiFileGenerator.generate()`
 * never returns: the player never becomes ready, a core pegs, and the tab dies
 * of heap exhaustion with nothing on screen to say why.
 *
 * Nothing in the library does this - all 143 `tocoda` attributes across it were
 * measured resolving strictly forwards - but `.musicxml` and `.mxl` are file
 * types a person can upload, and a mis-anchored coda is exactly what the
 * extractor's own `nav_marks_unanchored` counter exists to report.
 */
export const NAVIGATION_BACKWARDS_CODA_MUSICXML = buildScore([
  { before: sign("segno") },
  { before: sign("coda") },
  {},
  { after: instruction("To Coda", 'tocoda="coda"') },
  {},
  { after: instruction("D.S. al Coda", 'dalsegno="segno"') },
]);

/**
 * Six 4/4 bars whose only segno sits AFTER the D.S. that names it (segno on
 * bar 5, D.S. closing bar 2). alphaTab's `_findJumpTarget` searches backwards
 * first and then FORWARDS, so this does not fail - it jumps forward, and the
 * score plays `1 2 5 6`, silently losing bars 3 and 4. Not a hang, and worse
 * for it: nothing at all says the piece was truncated.
 */
export const NAVIGATION_LATE_SEGNO_MUSICXML = buildScore([
  {},
  { after: instruction("D.S.", 'dalsegno="segno"') },
  {},
  {},
  { before: sign("segno") },
  {},
]);

/**
 * A `<sound>` written the OTHER way - as a direct child of `<measure>`, which
 * is the only place alphaTab's own importer reads a jump attribute from. It
 * turns this into an unguarded Direction.JumpDaCoda; this layer deliberately
 * leaves it alone (a second jump on one bar would be taken ahead of or behind
 * the first according to nothing but enum order).
 */
function measureLevelSound(attributes) {
  return `
      <sound ${attributes} />`;
}

// Eight 4/4 bars mixing the two conventions: a nested D.S. al Coda closing bar
// 6, and a MEASURE-LEVEL To Coda on bar 4 that the renderer's own importer
// reads and does not guard. `codaBar` is the 1-based bar the coda sign opens.
//
// With the coda AFTER the To Coda this is an ordinary, playable form. With it
// BEFORE, the two conventions together reproduce the wedge in full: the
// unguarded To Coda jumps backwards to the coda and resets the state machine,
// which re-arms the D.S. al Coda, which arms the coda hunt again. Measured at
// an 89.9s main-thread hang before the al-Coda flavour was made conditional on
// there being no such unguarded jump anywhere in the score.
function buildMixedConventionScore(codaBar) {
  return buildScore([
    { before: sign("segno") },
    codaBar === 2 ? { before: sign("coda") } : {},
    {},
    { after: measureLevelSound('tocoda="coda"') },
    {},
    { after: instruction("D.S. al Coda", 'dalsegno="segno"') },
    codaBar === 7 ? { before: sign("coda") } : {},
    {},
  ]);
}

/** The control: a measure-level To Coda on bar 4 with its coda on bar 7. */
export const NAVIGATION_MIXED_FORWARD_MUSICXML = buildMixedConventionScore(7);

/** The treatment: the same document with the coda on bar 2 instead. */
export const NAVIGATION_MIXED_BACKWARD_MUSICXML = buildMixedConventionScore(2);

/**
 * E-reg1: an instruction written PART-WAY THROUGH its measure, after two of
 * bar 2's four notes, rather than after all of them the way Rule 16 writes one.
 * The importer's beat-text echo then lands on an interior beat of that same
 * bar - not on the first beat of the next one - so a clearing pass that only
 * ever looks at a bar's first beat leaves it there and the label is drawn
 * twice. Nothing this project emits has this shape; third-party MusicXML
 * writes mid-measure directions routinely.
 */
export const NAVIGATION_MIDBAR_MARK_MUSICXML = buildScore([
  {},
  { interior: instruction("Fine", 'fine="yes"') },
  {},
  {},
]);

/**
 * E-reg2: an instruction written before a `<backup>`, so the next beat the
 * importer creates is the first beat of the SECOND VOICE of the same bar. Same
 * consequence as E-reg1 and a different route to it, which is why both are
 * here: the echo is not "somewhere in the next bar", it is wherever the
 * importer's own walk happens to be.
 *
 * Written out rather than built, because the builder above writes one voice.
 */
export const NAVIGATION_TWO_VOICE_MARK_MUSICXML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>Navigation test fixture</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">${OPENING_ATTRIBUTES}${notesForBar(0)}
    </measure>
    <measure number="2">${notesForBar(1)}${instruction("Fine", 'fine="yes"')}
      <backup><duration>${DIVISIONS * 4}</duration></backup>
      ${[0, 1]
        .map(
          (i) => `<note>
        <pitch><step>${PITCHES[i]}</step><octave>3</octave></pitch>
        <duration>${DIVISIONS * 2}</duration>
        <voice>2</voice>
        <type>half</type>
      </note>`,
        )
        .join("\n      ")}
    </measure>
    <measure number="3">${notesForBar(2)}
    </measure>
  </part>
</score-partwise>
`;

/**
 * Four 4/4 bars where a Fine mark on bar 2 is followed, in bar 3, by a
 * SEPARATE words-only `<direction><words>Fine</words></direction>` written
 * part-way through the bar - a real annotation an engraver might print, with
 * no `<sound>` and therefore no direction of its own.
 *
 * Both produce a `beat.text` reading "Fine": the mark's echo on bar 3's FIRST
 * beat (which the renderer now draws properly as a Fine at the end of bar 2,
 * so the echo is a duplicate and goes), and the annotation's own on bar 3's
 * THIRD beat (which is the only thing on the page saying it is there, and must
 * survive). An echo-clearing pass wide enough to take both destroys a fact
 * about the score to tidy a duplicate.
 */
export const NAVIGATION_ANNOTATION_MUSICXML = buildScore([
  {},
  { after: instruction("Fine", 'fine="yes"') },
  { interior: instruction("Fine", null) },
  {},
]);

// An annotation on the very beat the echo lands on - the first beat of the bar
// after a Rule 16 instruction - is deliberately NOT covered by a fixture,
// because there is nothing there to test. The importer holds beat text in one
// `_nextBeatText` slot, so the second assignment overwrites the first before
// any beat is created and only ONE text ever reaches the model. The two do not
// coexist to be told apart. See clearLateBeatText's own note.

/**
 * The same four bars as a `score-timewise` document - MusicXML's other
 * ordering, where `<measure>` is the outer element and `<part>` the inner one.
 * The renderer imports it (its own importer branches on the root element); this
 * layer refuses to read marks out of it, because a measure's position here is
 * not what it is in a part-wise file and indexing it the same way would put
 * jumps on the wrong bars.
 *
 * It carries a D.S. with a live `<sound>`, so there is genuinely something to
 * miss - which is the point: it must SAY it did not read it.
 */
export const NAVIGATION_TIMEWISE_MUSICXML = `<?xml version="1.0" encoding="UTF-8"?>
<score-timewise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
${[0, 1, 2, 3]
  .map(
    (i) => `  <measure number="${i + 1}">
    <part id="P1">${i === 0 ? OPENING_ATTRIBUTES : ""}${i === 0 ? sign("segno") : ""}${notesForBar(i)}${
      i === 3 ? instruction("D.S.", 'dalsegno="segno"') : ""
    }
    </part>
  </measure>`,
  )
  .join("\n")}
</score-timewise>
`;

const CONTAINER_XML = `<?xml version="1.0" encoding="UTF-8"?>
<container><rootfiles><rootfile full-path="score.musicxml"
    media-type="application/vnd.recordare.musicxml+xml"/></rootfiles></container>
`;

/**
 * A real compressed MusicXML container (`.mxl`): a ZIP holding
 * `META-INF/container.xml` and the score itself, written with node's own
 * deflate and a proper central directory.
 *
 * Built rather than committed as a binary blob for two reasons: it stays the
 * same document as whatever is passed in, and a checked-in ZIP is a fixture
 * nobody can read in a diff. Deliberately DEFLATED rather than stored, since
 * a stored entry would not exercise the inflate path at all - and deliberately
 * given a manifest, since that is the only correct way to pick the score out
 * of a container that holds several files.
 */
function buildMxl(scoreXml) {
  const files = [
    { name: "META-INF/container.xml", data: Buffer.from(CONTAINER_XML, "utf-8") },
    { name: "score.musicxml", data: Buffer.from(scoreXml, "utf-8") },
  ];
  const locals = [];
  const central = [];
  let offset = 0;
  for (const file of files) {
    const name = Buffer.from(file.name, "utf-8");
    const deflated = zlib.deflateRawSync(file.data);
    const crc = zlib.crc32 ? zlib.crc32(file.data) : crc32(file.data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(0, 6); // flags - no data descriptor, so sizes are real
    local.writeUInt16LE(8, 8); // deflate
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(deflated.length, 18);
    local.writeUInt32LE(file.data.length, 22);
    local.writeUInt16LE(name.length, 26);
    locals.push(local, name, deflated);

    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4); // version made by
    entry.writeUInt16LE(20, 6); // version needed
    entry.writeUInt16LE(0, 8);
    entry.writeUInt16LE(8, 10);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(deflated.length, 20);
    entry.writeUInt32LE(file.data.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);
    offset += local.length + name.length + deflated.length;
  }
  const centralBuffer = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(files.length, 8);
  eocd.writeUInt16LE(files.length, 10);
  eocd.writeUInt32LE(centralBuffer.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, centralBuffer, eocd]);
}

// node's zlib.crc32 only arrived in 22.2; this is the same polynomial, for
// older runners. A ZIP reader that checks the crc would reject a wrong one,
// so it is computed rather than zeroed.
function crc32(buffer) {
  let crc = ~0;
  for (const byte of buffer) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (~crc) >>> 0;
}

function scoreMeta(id, title) {
  // file_type not "pdf" is what routes Viewer.svelte to TabViewer directly
  // rather than through ScoreCompare/PdfViewer - see metronome-score.js's own
  // scoreMeta() for the same choice.
  return {
    id,
    title,
    composer: "",
    source: "",
    file_type: "musicxml",
    has_transcription: false,
    favorite: false,
    content_kind: "notation",
    tags: [],
  };
}

async function stubOneScore(page, id, title, xml) {
  await page.route(`**/api/scores/${id}`, (route) => route.fulfill({ json: scoreMeta(id, title) }));
  await page.route(`**/api/scores/${id}/file`, (route) =>
    route.fulfill({ body: xml, contentType: "application/vnd.recordare.musicxml+xml" }),
  );
  await page.route(`**/api/scores/${id}/practice`, (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
}

/** Score id 11: the committed navigation.pdf transcription. */
export async function stubNavigationScore(page) {
  await stubOneScore(page, 11, "Navigation fixture", NAVIGATION_MUSICXML);
}

/** Score id 12: repeats and a D.S. al Coda in one piece. */
export async function stubNavigationRepeatScore(page) {
  await stubOneScore(page, 12, "Navigation repeat fixture", NAVIGATION_REPEAT_MUSICXML);
}

/** Score id 13: two instructions naming targets the score does not draw. */
export async function stubNavigationUnresolvedScore(page) {
  await stubOneScore(page, 13, "Navigation unresolved fixture", NAVIGATION_UNRESOLVED_MUSICXML);
}

/** Score id 14: a coda before the To Coda that names it - the hang. */
export async function stubNavigationBackwardsCodaScore(page) {
  await stubOneScore(page, 14, "Navigation backwards-coda fixture", NAVIGATION_BACKWARDS_CODA_MUSICXML);
}

/** Score id 15: a segno after the D.S. that names it. */
export async function stubNavigationLateSegnoScore(page) {
  await stubOneScore(page, 15, "Navigation late-segno fixture", NAVIGATION_LATE_SEGNO_MUSICXML);
}

/** Score id 16: a words-only annotation sharing a mark's text. */
export async function stubNavigationAnnotationScore(page) {
  await stubOneScore(page, 16, "Navigation annotation fixture", NAVIGATION_ANNOTATION_MUSICXML);
}

/** Score id 19: mixed conventions, coda after the measure-level To Coda. */
export async function stubNavigationMixedForwardScore(page) {
  await stubOneScore(page, 19, "Navigation mixed-convention fixture", NAVIGATION_MIXED_FORWARD_MUSICXML);
}

/** Score id 20: the same, with the coda before it - the residual wedge. */
export async function stubNavigationMixedBackwardScore(page) {
  await stubOneScore(page, 20, "Navigation backwards mixed fixture", NAVIGATION_MIXED_BACKWARD_MUSICXML);
}

/** Score id 21: an instruction written part-way through its measure. */
export async function stubNavigationMidbarMarkScore(page) {
  await stubOneScore(page, 21, "Navigation mid-bar mark fixture", NAVIGATION_MIDBAR_MARK_MUSICXML);
}

/** Score id 22: an instruction written before a `<backup>`. */
export async function stubNavigationTwoVoiceMarkScore(page) {
  await stubOneScore(page, 22, "Navigation two-voice mark fixture", NAVIGATION_TWO_VOICE_MARK_MUSICXML);
}

/** Score id 18: a part-wise document's other ordering, which is not read. */
export async function stubNavigationTimewiseScore(page) {
  await stubOneScore(page, 18, "Navigation timewise fixture", NAVIGATION_TIMEWISE_MUSICXML);
}

/**
 * Score id 17: the committed navigation transcription inside a real compressed
 * `.mxl` container - a ZIP with the `META-INF/container.xml` manifest that
 * names its root file, exactly as an exporter writes one. Built here with
 * node's own deflate rather than committed as a binary, so it stays the same
 * document as NAVIGATION_MUSICXML by construction.
 */
export async function stubNavigationContainerScore(page) {
  await page.route(`**/api/scores/17`, (route) => route.fulfill({ json: scoreMeta(17, "Navigation .mxl fixture") }));
  await page.route(`**/api/scores/17/file`, (route) =>
    route.fulfill({ body: buildMxl(NAVIGATION_MUSICXML), contentType: "application/vnd.recordare.musicxml" }),
  );
  await page.route(`**/api/scores/17/practice`, (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
}
