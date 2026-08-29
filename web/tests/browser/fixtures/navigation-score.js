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
function notesForBar(bar) {
  return Array.from({ length: 4 }, (_, i) => {
    const step = PITCHES[(bar + i) % PITCHES.length];
    return `
      <note>
        <pitch><step>${step}</step><octave>4</octave></pitch>
        <duration>${DIVISIONS}</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>`;
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
    return `    <measure number="${i + 1}">${attrs}${bar.before ?? ""}${notesForBar(i)}${bar.after ?? ""}${bar.barline ?? ""}
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
