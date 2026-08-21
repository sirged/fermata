// Fixture data + route stubs for tests/browser/metronome.spec.js.
//
// A real MusicXML document, not a stub of TabViewer's internals - the same
// reasoning as SCORE/SAMPLE_TEX in fixtures/transcription-warnings.js: the
// interesting part of this feature is whether a genuine alphaTab render and
// a genuine Web Audio click actually happen, which nothing short of the real
// importer and the real player can tell you. Modelled on
// docs/examples/monophonic.musicxml for the shape (divisions, <sound tempo>)
// and web/test-fixtures/notation-only.musicxml for a pitched, non-percussion
// part that renders under the "score" profile without a tab staff.
//
// 6/8 at a declared tempo of 96 BPM (always quarter-note BPM - see
// metronome.js's secondsPerClick) is the one fact these tests are built
// around: it is a compound meter (six eighth-note clicks per bar, accented
// on the 1st and 4th - see metronomePattern), and 96 is a base the
// proportion tests below multiply and divide by round numbers.
//
// MEASURE_COUNT repeats of the one bar, not one - the slow end of these
// tests (a click every 600ms+, several of them, sometimes under a halved
// playback speed) needs several real seconds of audio playing, and a single
// 1.875s bar would demand the tests lean on looping across many passes to
// get there. Enough measures that the piece's own natural length already
// covers every test's collection window keeps looping a belt-and-braces
// safety net rather than something the timing math depends on.
const MEASURE_COUNT = 8;

const PITCHES = ["C", "D", "E", "F", "G", "A"];

function measure(number) {
  const attributes =
    number === 1
      ? `
      <attributes>
        <divisions>480</divisions>
        <key>
          <fifths>0</fifths>
        </key>
        <time>
          <beats>6</beats>
          <beat-type>8</beat-type>
        </time>
        <clef>
          <sign>G</sign>
          <line>2</line>
        </clef>
      </attributes>
      <direction placement="above">
        <direction-type>
          <metronome>
            <beat-unit>quarter</beat-unit>
            <per-minute>96</per-minute>
          </metronome>
        </direction-type>
        <sound tempo="96" />
      </direction>`
      : "";
  const notes = PITCHES.map(
    (step) => `
      <note>
        <pitch><step>${step}</step><octave>4</octave></pitch>
        <duration>240</duration>
        <voice>1</voice>
        <type>eighth</type>
      </note>`,
  ).join("");
  return `    <measure number="${number}">${attributes}${notes}
    </measure>`;
}

export const METRONOME_MUSICXML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://www.musicxml.org/xsd/musicxml.xsd">
  <work>
    <work-title>Metronome test fixture</work-title>
  </work>
  <identification>
    <encoding>
      <software>Fermata test fixture</software>
      <encoding-date>2026-08-20</encoding-date>
    </encoding>
  </identification>
  <part-list>
    <score-part id="P1">
      <part-name>Piano</part-name>
      <score-instrument id="P1-I1">
        <instrument-name>Piano</instrument-name>
      </score-instrument>
      <midi-instrument id="P1-I1">
        <midi-channel>1</midi-channel>
        <midi-program>1</midi-program>
      </midi-instrument>
    </score-part>
  </part-list>
  <part id="P1">
${Array.from({ length: MEASURE_COUNT }, (_, i) => measure(i + 1)).join("\n")}
  </part>
</score-partwise>
`;

// file_type not "pdf" is what routes Viewer.svelte to TabViewer directly
// (see Viewer.svelte) rather than through ScoreCompare/PdfViewer.
export const METRONOME_SCORE = {
  id: 1,
  title: "Metronome test fixture",
  composer: "",
  source: "",
  file_type: "musicxml",
  has_transcription: false,
  favorite: false,
  content_kind: "notation",
  tags: [],
};

/** Stubs the /api routes TabViewer/Viewer touch for score id 1, serving
 * METRONOME_MUSICXML as the file content. */
export async function stubMetronomeScore(page) {
  await page.route("**/api/scores/1", (route) => route.fulfill({ json: METRONOME_SCORE }));
  await page.route("**/api/scores/1/file", (route) =>
    route.fulfill({ body: METRONOME_MUSICXML, contentType: "application/vnd.recordare.musicxml+xml" }),
  );
  await page.route("**/api/scores/1/practice", (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
}
