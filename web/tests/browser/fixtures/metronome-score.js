// Fixture data + route stubs for tests/browser/metronome.spec.js.
//
// Real MusicXML documents, not a stub of TabViewer's internals - the same
// reasoning as SCORE/SAMPLE_TEX in fixtures/transcription-warnings.js: the
// interesting part of this feature is whether a genuine alphaTab render and
// a genuine Web Audio click actually happen, which nothing short of the real
// importer and the real player can tell you. Modelled on
// docs/examples/monophonic.musicxml for the shape (divisions, <sound tempo>)
// and web/test-fixtures/notation-only.musicxml for a pitched, non-percussion
// part that renders under the "score" profile without a tab staff.
//
// 6/8 at a declared tempo of 96 BPM (always quarter-note BPM - see
// metronome.js's secondsPerClick) is the fact most of these tests are built
// around: it is a compound meter (six eighth-note clicks per bar, accented
// on the 1st and 4th - see metronomePattern), and 96 is a base the
// proportion tests multiply and divide by round numbers.
const DIVISIONS = 480; // ticks per quarter note
const PITCHES = ["C", "D", "E", "F", "G", "A", "B", "C"];

// `tempo` of null emits NO tempo direction at all - not a direction carrying
// 120, and not a <words>Andante</words> either. That is the shape of the
// editions this library is mostly made of, and the one the metronome used to
// describe as "marked ♩ = 120": alphaTab's Score.tempo is a getter that
// answers 120 when the first bar holds no tempo automation, so nothing
// downstream could tell it apart from a score that really did print 120.
function timeAndTempoBlock(numerator, denominator, tempo) {
  return `
      <attributes>
        <divisions>${DIVISIONS}</divisions>
        <key><fifths>0</fifths></key>
        <time>
          <beats>${numerator}</beats>
          <beat-type>${denominator}</beat-type>
        </time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>${
        tempo == null
          ? ""
          : `
      <direction placement="above">
        <direction-type>
          <metronome><beat-unit>quarter</beat-unit><per-minute>${tempo}</per-minute></metronome>
        </direction-type>
        <sound tempo="${tempo}" />
      </direction>`
      }`;
}

function notesForBar(numerator, denominator) {
  // One note per click unit (an eighth for .../8, a quarter for .../4),
  // filling the bar exactly - the fixture only needs to render and hold the
  // declared duration, never to be musically interesting.
  const unitDuration = DIVISIONS * (4 / denominator);
  const type = denominator === 8 ? "eighth" : "quarter";
  return Array.from({ length: numerator }, (_, i) => {
    const step = PITCHES[i % PITCHES.length];
    return `
      <note>
        <pitch><step>${step}</step><octave>4</octave></pitch>
        <duration>${unitDuration}</duration>
        <voice>1</voice>
        <type>${type}</type>
      </note>`;
  }).join("");
}

/**
 * Builds a score-partwise document of `measures` bars, all in one time
 * signature/tempo (declared once, on the first bar) unless `change` names a
 * later bar to switch at. `repeatAfter` puts a backward-repeat barline at
 * the end of that 1-based bar number, repeating from the start of the piece
 * (no forward repeat needed - the piece's own start is where a backward
 * repeat with nothing preceding it returns to).
 */
function buildMusicXml({ measures, numerator, denominator, tempo, repeatAfter = null, change = null }) {
  const bars = [];
  let curNum = numerator;
  let curDen = denominator;
  for (let i = 1; i <= measures; i++) {
    const isFirst = i === 1;
    const isChange = change && i === change.at;
    if (isChange) {
      curNum = change.numerator;
      curDen = change.denominator;
    }
    const attrs = isFirst
      ? timeAndTempoBlock(curNum, curDen, tempo)
      : isChange
        ? `
      <attributes>
        <time><beats>${curNum}</beats><beat-type>${curDen}</beat-type></time>
      </attributes>`
        : "";
    const barline =
      repeatAfter === i
        ? `
      <barline location="right">
        <bar-style>light-heavy</bar-style>
        <repeat direction="backward"/>
      </barline>`
        : "";
    bars.push(`    <measure number="${i}">${attrs}${notesForBar(curNum, curDen)}${barline}
    </measure>`);
  }
  return `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://www.musicxml.org/xsd/musicxml.xsd">
  <work><work-title>Metronome test fixture</work-title></work>
  <identification>
    <encoding><software>Fermata test fixture</software><encoding-date>2026-08-20</encoding-date></encoding>
  </identification>
  <part-list>
    <score-part id="P1">
      <part-name>Piano</part-name>
      <score-instrument id="P1-I1"><instrument-name>Piano</instrument-name></score-instrument>
      <midi-instrument id="P1-I1"><midi-channel>1</midi-channel><midi-program>1</midi-program></midi-instrument>
    </score-part>
  </part-list>
  <part id="P1">
${bars.join("\n")}
  </part>
</score-partwise>
`;
}

// The main fixture: 8 measures of 6/8 at 96 BPM, no repeats. Eight rather
// than one - the slow end of these tests (a click every 600ms+, several of
// them, sometimes under a halved playback speed) needs several real seconds
// of audio playing, and a single 1.875s bar would demand leaning on looping
// across many passes to get there. Enough measures that the piece's own
// natural length already covers every test's collection window keeps
// looping a belt-and-braces safety net rather than something the timing
// math depends on.
export const METRONOME_MUSICXML = buildMusicXml({ measures: 8, numerator: 6, denominator: 8, tempo: 96 });

// A short loop, deliberately: 2 measures of 6/8 at 96 BPM whose own real
// playback length (2 * 1.875s = 3.75s) does NOT divide evenly by the click
// period a 70%-proportion metronome runs at (~0.446s) - see the loop-wrap
// test, which relies on that mismatch to tell "phase derived from the
// playhead" apart from "phase carried as a counter that only resets when
// the scheduler starts".
export const METRONOME_MUSICXML_SHORT_LOOP = buildMusicXml({
  measures: 2,
  numerator: 6,
  denominator: 8,
  tempo: 96,
});

// Two bars of 4/4 at 120 BPM, repeated once (a backward-repeat barline on
// bar 2), followed by one bar of 6/8 - three NOTATED bars that play as five
// (4/4, 4/4, 4/4, 4/4, 6/8). A tick->meter index built by summing NOTATED
// bar durations would place the 6/8 bar right after the first pass of bar 2
// - i.e. DURING the repeat's second pass - and misreport the meter (and
// therefore the click rate) for the whole rest of the repeated section. See
// the "a repeat sign must not desync the click's meter" test.
export const METRONOME_MUSICXML_REPEAT = buildMusicXml({
  measures: 3,
  numerator: 4,
  denominator: 4,
  tempo: 120,
  repeatAfter: 2,
  change: { at: 3, numerator: 6, denominator: 8 },
});

// A second, plainly different score (140 BPM, 4/4) - used only to prove the
// metronome's own dataset state resets when a mounted TabViewer switches to
// a DIFFERENT score, rather than a fresh view inheriting whatever a
// previous score's clicks left sitting on the shared host element.
export const METRONOME_MUSICXML_OTHER = buildMusicXml({ measures: 4, numerator: 4, denominator: 4, tempo: 140 });

// 6/8 at 144 - an ordinary jig tempo, and fast enough that running it ABOVE
// tempo reaches the click's ceiling: 150% of 144 converted onto the eighth-note
// unit asks for 432 clicks a minute, which MAX_METRONOME_BPM holds at 400.
// Nothing exotic about it, which is the point - a sweep of the preset ladder
// against real meters found the ceiling roughly eighteen times more reachable
// than the floor, so this is the end of the range a player meets first.
export const METRONOME_MUSICXML_FAST = buildMusicXml({
  measures: 8,
  numerator: 6,
  denominator: 8,
  tempo: 144,
});

// 4/4 with NO tempo direction anywhere - the fixture the two existing honesty
// tests for the tempo control did not have. Both of those declare a tempo (96
// and 90), which is why the `?? null` guard meant to catch an undeclared one
// was never exercised and was in fact dead code (issue #102).
//
// 4/4 rather than 6/8 so the click rate and the quarter-note tempo are the
// same number: the base tempo IS the readout, and a test can tell "the
// fallback was used" from "something else was used" without a meter
// conversion in the way.
export const METRONOME_MUSICXML_NO_TEMPO = buildMusicXml({
  measures: 8,
  numerator: 4,
  denominator: 4,
  tempo: null,
});

function scoreMeta(id, title) {
  // file_type not "pdf" is what routes Viewer.svelte to TabViewer directly
  // (see Viewer.svelte) rather than through ScoreCompare/PdfViewer.
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

async function stubOneScore(page, id, meta, xml) {
  await page.route(`**/api/scores/${id}`, (route) => route.fulfill({ json: meta }));
  await page.route(`**/api/scores/${id}/file`, (route) =>
    route.fulfill({ body: xml, contentType: "application/vnd.recordare.musicxml+xml" }),
  );
  await page.route(`**/api/scores/${id}/practice`, (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
}

/** Stubs the /api routes TabViewer/Viewer touch for score id 1 (the main,
 * 8-measure 6/8 fixture). */
export async function stubMetronomeScore(page) {
  await stubOneScore(page, 1, scoreMeta(1, "Metronome test fixture"), METRONOME_MUSICXML);
}

/** Score id 2: the second, plainly different score used for the
 * dataset-reset-on-switch test. */
export async function stubMetronomeScoreOther(page) {
  await stubOneScore(page, 2, scoreMeta(2, "A different metronome fixture"), METRONOME_MUSICXML_OTHER);
}

/** Score id 5: a fast 6/8, for the click's own ceiling on an ordinary meter. */
export async function stubMetronomeScoreFast(page) {
  await stubOneScore(page, 5, scoreMeta(5, "Fast 6/8 metronome fixture"), METRONOME_MUSICXML_FAST);
}

/** Score id 3: the short 2-measure loop used for the loop-wrap phase test. */
export async function stubMetronomeScoreShortLoop(page) {
  await stubOneScore(page, 3, scoreMeta(3, "Short loop metronome fixture"), METRONOME_MUSICXML_SHORT_LOOP);
}

/** Score id 4: the 4/4-repeat-then-6/8 fixture used for the repeat-desync
 * regression test. */
export async function stubMetronomeScoreRepeat(page) {
  await stubOneScore(page, 4, scoreMeta(4, "Repeat metronome fixture"), METRONOME_MUSICXML_REPEAT);
}

/** Score id 6: 4/4 with no tempo direction at all. */
export async function stubMetronomeScoreNoTempo(page) {
  await stubOneScore(page, 6, scoreMeta(6, "No-tempo metronome fixture"), METRONOME_MUSICXML_NO_TEMPO);
}
