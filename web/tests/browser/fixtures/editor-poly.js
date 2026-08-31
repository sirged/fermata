// Polyphonic fixtures for the note editor's N-random-edits fuzz guard and its
// overlapping-voice selection (#189), a follow-on to the monophonic
// fixtures/editor-score.js the first increment (#10) shipped.
//
// Why a built fixture and not a library file: the editor only opens a one-part
// TAB score with a <staff-tuning> (see editor/document.js's createDocument
// throws), and the two committed .musicxml files under FERMATA_TEST_LIBRARY are
// single-voice lead sheets with no tuning at all - the real polyphonic tab
// transcriptions live only in the server's database, which this suite cannot
// reach (the browser suite stubs the HTTP transport; the live instance on 8080
// is off limits). So a genuinely polyphonic document is BUILT here in the
// emitter's exact shape - one part, one six-string TAB staff, <divisions>480,
// a Rule 17 id on every note (n{measure}-{voice}-{onset}-{chord}), two voices
// separated by a <backup> (Rule 6), chords (Rule 7), ties (#183) and mixed
// durations - and served through the same stub the monophonic suite uses. It is
// a real document the real alphaTab importer renders and the real
// editor/document.js parses; nothing is stubbed but the transport.
//
// The point of the polyphony is the reorder/add/remove surface the fuzz needs:
// two voices with backups is where a voice move (#182) or a delete (#186) can
// shift an ordinal rather than merely misread one, which is the divergence the
// N-edits guard exists to catch and the per-selection guard cannot.
import { spellPitch } from "../../../src/lib/editor/notes.js";

const DIVISIONS = 480;

// Standard six-string tuning, high string (1) to low (6). The <staff-tuning>
// line a MusicXML <string> maps to is stringCount + 1 - string (Rule 5), so
// string 1 (E4) reads line 6. Open-string MIDI, indexed by <string>.
const OPEN_MIDI = { 1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40 };

const STAFF_DETAILS = `
        <staff-details>
          <staff-lines>6</staff-lines>
          <staff-tuning line="1"><tuning-step>E</tuning-step><tuning-octave>2</tuning-octave></staff-tuning>
          <staff-tuning line="2"><tuning-step>A</tuning-step><tuning-octave>2</tuning-octave></staff-tuning>
          <staff-tuning line="3"><tuning-step>D</tuning-step><tuning-octave>3</tuning-octave></staff-tuning>
          <staff-tuning line="4"><tuning-step>G</tuning-step><tuning-octave>3</tuning-octave></staff-tuning>
          <staff-tuning line="5"><tuning-step>B</tuning-step><tuning-octave>3</tuning-octave></staff-tuning>
          <staff-tuning line="6"><tuning-step>E</tuning-step><tuning-octave>4</tuning-octave></staff-tuning>
        </staff-details>`;

const TYPE_DUR = { whole: 1920, half: 960, quarter: 480, eighth: 240, "16th": 120 };

function midiOf(string, fret) {
  return OPEN_MIDI[string] + fret;
}

// One <note> element, sounding or (when notes is null) a rest. `spelled` follows
// the C-major spelling the emitter writes (spellPitch(midi, 0)), so the pitch is
// consistent with the string+fret the divergence guard cross-checks it against.
function noteXml(id, { string, fret, type, chord, tie }) {
  const midi = midiOf(string, fret);
  const p = spellPitch(midi, 0);
  const alterEl = p.alter ? `<alter>${p.alter}</alter>` : "";
  const chordEl = chord ? "<chord/>" : "";
  const tieSound = tie ? tie.map((t) => `<tie type="${t}"/>`).join("") : "";
  const tiedNotation = tie ? tie.map((t) => `<tied type="${t}"/>`).join("") : "";
  const dur = TYPE_DUR[type];
  return `      <note id="${id}">
        ${chordEl}<pitch><step>${p.step}</step>${alterEl}<octave>${p.octave}</octave></pitch>
        <duration>${dur}</duration>${tieSound}
        <voice>{{VOICE}}</voice>
        <type>${type}</type>
        <notations>${tiedNotation}<technical><string>${string}</string><fret>${fret}</fret></technical></notations>
      </note>`;
}

function restXml(dur, type) {
  const typeEl = type ? `<type>${type}</type>` : "";
  return `      <note>
        <rest/>
        <duration>${dur}</duration>
        <voice>{{VOICE}}</voice>
        ${typeEl}
      </note>`;
}

// Build one voice's stream for a measure from a list of beats laid contiguously
// from onset 0. Each beat is either { rest: true, type } or { type, chord?,
// notes: [{string, fret}], tie? }. Rule 17 ids are derived from position:
// n{measure}-{voice}-{beatIndex}-{chordIndex}. Returns { xml, total } so the
// caller can assert Rule 8 (every voice sums to the measure).
function voiceStream(measureNumber, voice, beats) {
  const parts = [];
  let total = 0;
  let beatIndex = 0;
  for (const beat of beats) {
    const dur = TYPE_DUR[beat.type];
    if (beat.rest) {
      parts.push(restXml(dur, beat.type).replaceAll("{{VOICE}}", String(voice)));
      total += dur;
      beatIndex += 1;
      continue;
    }
    beat.notes.forEach((n, chordIndex) => {
      const id = `n${measureNumber}-${voice}-${beatIndex}-${chordIndex}`;
      const tie = chordIndex === 0 ? beat.tie : undefined;
      parts.push(
        noteXml(id, { ...n, type: beat.type, chord: chordIndex > 0, tie }).replaceAll("{{VOICE}}", String(voice)),
      );
    });
    total += dur;
    beatIndex += 1;
  }
  return { xml: parts.join("\n"), total };
}

// Assemble a whole measure from its voices. A <backup> rewinding the full
// measure precedes every voice after the first (Rule 6). Throws if any voice
// does not sum to measureDur - a fixture that broke Rule 8 would fail the
// importer quietly, so it fails loudly here instead.
function measureXml(measureNumber, voices, attributes, measureDur) {
  const chunks = [];
  const voiceNums = Object.keys(voices).map(Number).sort((a, b) => a - b);
  voiceNums.forEach((v, i) => {
    if (i > 0) {
      chunks.push(`      <backup><duration>${measureDur}</duration></backup>`);
    }
    const { xml, total } = voiceStream(measureNumber, v, voices[v]);
    if (total !== measureDur) {
      throw new Error(`fixture measure ${measureNumber} voice ${v} sums to ${total}, not ${measureDur}`);
    }
    chunks.push(xml);
  });
  return `    <measure number="${measureNumber}">${attributes}
${chunks.join("\n")}
    </measure>`;
}

// The polyphonic score, eight measures, two voices throughout: an upper melodic
// voice (voice 1, on the higher strings) and a lower voice (voice 2, on the
// bass strings), with chords, a within-bar tie and a cross-barline tie. Every
// voice fills its 4/4 bar (1920 divisions). Enough sounding notes (~50) that N
// random voice moves, deletes and duration changes genuinely reorder and
// add/remove within the model.
function buildPolyScore() {
  const attrs = `
      <attributes>
        <divisions>${DIVISIONS}</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>TAB</sign><line>5</line></clef>${STAFF_DETAILS}
      </attributes>`;

  // Compact per-measure spec. Voice 1: melody on strings 1-3. Voice 2: bass on
  // strings 4-6. `q`/`e`/`h` shorthand kept explicit for readability.
  const measures = [
    // m1: melody four quarters over two half-note bass notes.
    {
      1: [
        { type: "quarter", notes: [{ string: 1, fret: 0 }] },
        { type: "quarter", notes: [{ string: 1, fret: 2 }] },
        { type: "quarter", notes: [{ string: 1, fret: 3 }] },
        { type: "quarter", notes: [{ string: 2, fret: 1 }] },
      ],
      2: [
        { type: "half", notes: [{ string: 5, fret: 0 }] },
        { type: "half", notes: [{ string: 4, fret: 0 }] },
      ],
    },
    // m2: melody with an opening two-note chord (Rule 7), then eighths; bass a
    // whole note.
    {
      1: [
        { type: "quarter", notes: [{ string: 1, fret: 0 }, { string: 2, fret: 1 }] },
        { type: "quarter", notes: [{ string: 2, fret: 3 }] },
        { type: "eighth", notes: [{ string: 1, fret: 0 }] },
        { type: "eighth", notes: [{ string: 1, fret: 2 }] },
        { type: "quarter", notes: [{ string: 1, fret: 3 }] },
      ],
      2: [{ type: "whole", notes: [{ string: 6, fret: 3 }] }],
    },
    // m3: a within-bar tie in voice 1 (two tied quarters, same string+fret), a
    // rest, then a quarter; bass two half notes.
    {
      1: [
        { type: "quarter", notes: [{ string: 1, fret: 5 }], tie: ["start"] },
        { type: "quarter", notes: [{ string: 1, fret: 5 }], tie: ["stop"] },
        { type: "quarter", rest: true },
        { type: "quarter", notes: [{ string: 2, fret: 0 }] },
      ],
      2: [
        { type: "half", notes: [{ string: 5, fret: 2 }] },
        { type: "half", notes: [{ string: 4, fret: 2 }] },
      ],
    },
    // m4: a cross-barline tie starts here (voice 1's last note ties into m5's
    // first). Melody eighths; bass a whole note.
    {
      1: [
        { type: "eighth", notes: [{ string: 1, fret: 0 }] },
        { type: "eighth", notes: [{ string: 1, fret: 2 }] },
        { type: "eighth", notes: [{ string: 1, fret: 3 }] },
        { type: "eighth", notes: [{ string: 1, fret: 5 }] },
        { type: "eighth", notes: [{ string: 2, fret: 1 }] },
        { type: "eighth", notes: [{ string: 2, fret: 3 }] },
        { type: "quarter", notes: [{ string: 3, fret: 2 }], tie: ["start"] },
      ],
      2: [{ type: "whole", notes: [{ string: 6, fret: 0 }] }],
    },
    // m5: the cross-barline tie stops on the first note; then quarters. Bass two
    // half notes.
    {
      1: [
        { type: "quarter", notes: [{ string: 3, fret: 2 }], tie: ["stop"] },
        { type: "quarter", notes: [{ string: 2, fret: 3 }] },
        { type: "quarter", notes: [{ string: 1, fret: 0 }] },
        { type: "quarter", notes: [{ string: 1, fret: 2 }] },
      ],
      2: [
        { type: "half", notes: [{ string: 5, fret: 3 }] },
        { type: "half", notes: [{ string: 5, fret: 0 }] },
      ],
    },
    // m6: a three-note chord opens the bar (Rule 7), then rests filled around a
    // melody note; bass a whole note.
    {
      1: [
        {
          type: "half",
          notes: [{ string: 1, fret: 0 }, { string: 2, fret: 1 }, { string: 3, fret: 0 }],
        },
        { type: "quarter", notes: [{ string: 1, fret: 3 }] },
        { type: "quarter", notes: [{ string: 1, fret: 5 }] },
      ],
      2: [{ type: "whole", notes: [{ string: 4, fret: 0 }] }],
    },
    // m7: melody eighths; bass quarters walking down.
    {
      1: [
        { type: "eighth", notes: [{ string: 2, fret: 0 }] },
        { type: "eighth", notes: [{ string: 2, fret: 1 }] },
        { type: "eighth", notes: [{ string: 2, fret: 3 }] },
        { type: "eighth", notes: [{ string: 1, fret: 0 }] },
        { type: "quarter", notes: [{ string: 1, fret: 2 }] },
        { type: "quarter", notes: [{ string: 1, fret: 3 }] },
      ],
      2: [
        { type: "quarter", notes: [{ string: 4, fret: 2 }] },
        { type: "quarter", notes: [{ string: 5, fret: 0 }] },
        { type: "quarter", notes: [{ string: 5, fret: 2 }] },
        { type: "quarter", notes: [{ string: 6, fret: 3 }] },
      ],
    },
    // m8: a closing two-note melody chord over a bass half note and a rest.
    {
      1: [
        { type: "half", notes: [{ string: 1, fret: 0 }, { string: 2, fret: 1 }] },
        { type: "half", notes: [{ string: 1, fret: 3 }] },
      ],
      2: [
        { type: "half", notes: [{ string: 5, fret: 0 }] },
        { type: "half", rest: true },
      ],
    },
  ];

  const body = measures
    .map((voices, i) => measureXml(i + 1, voices, i === 0 ? attrs : "", 1920))
    .join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Guitar</part-name></score-part>
  </part-list>
  <part id="P1">
${body}
  </part>
</score-partwise>`;
}

export const POLY_MUSICXML = buildPolyScore();

// A one-measure score whose voice 1 and voice 2 each sound the SAME pitch at the
// SAME onset - a unison across two voices (string 1 fret 0 = E4 in both). On the
// tab staff both draw the digit "0" at the same x on the same line, and on the
// notation staff both draw an E4 head at the same y: the ~1.5% genuinely
// OVERLAPPING note-head case the #10 evaluation flagged. Selecting between them
// is what the click-cycle disambiguation (score-render.js hitTestNote) exists
// for - a second click at the same spot cycles to the other voice's note.
//
//   ord  id          voice  onset  string  fret  pitch  midi
//   0    n1-1-0-0    1      0      1       0     E4     64   (voice 1, overlaps)
//   1    n1-1-1-0    1      960    1       3     G4     67
//   2    n1-2-0-0    2      0      1       0     E4     64   (voice 2, overlaps 0)
//   3    n1-2-1-0    2      960    4       0     D3     50
export const OVERLAP_MUSICXML = (() => {
  const attrs = `
      <attributes>
        <divisions>${DIVISIONS}</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>TAB</sign><line>5</line></clef>${STAFF_DETAILS}
      </attributes>`;
  const voices = {
    1: [
      { type: "half", notes: [{ string: 1, fret: 0 }] },
      { type: "half", notes: [{ string: 1, fret: 3 }] },
    ],
    2: [
      { type: "half", notes: [{ string: 1, fret: 0 }] },
      { type: "half", notes: [{ string: 4, fret: 0 }] },
    ],
  };
  return `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Guitar</part-name></score-part>
  </part-list>
  <part id="P1">
${measureXml(1, voices, attrs, 1920)}
  </part>
</score-partwise>`;
})();
