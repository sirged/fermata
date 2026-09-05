// The fixture for the multi-note selection (#251), see
// ../score-editor-range-251.spec.js. Same profile as fixtures/editor-score.js -
// one part, one TAB staff, standard six-string tuning, <divisions>480</divisions>,
// a Rule 17 id on every note - built to carry the three things a RANGE has to be
// exercised against and the monophonic fixture does not have:
//
//   1. A bar whose total survives a range retype. Measure 1 opens with a
//      quarter (480) and two 16ths (120 + 120) = 720, which is exactly three
//      eighths. Setting all three to `eighth` in one gesture leaves the measure
//      summing to the same 1920 it did before - so "the bar still sums" can be
//      asserted as literal equality rather than as a weaker consistency claim.
//      That is the only shape in which it CAN be asserted: setDurationType
//      deliberately does not refill the bar (see its docstring), so a run whose
//      new total differs from its old one necessarily changes the measure's
//      total, exactly as one note at a time always has.
//   2. A REST inside a voice (measure 2), so the extend step can be shown to
//      stop at it.
//   3. A TRIPLET (measure 3): three eighths under a 3:2 <time-modification>,
//      <duration> 160 each rather than 240. This is what setDurationType's new
//      refusal is about, and what a range gesture containing one is rolled back
//      over.
//
// The notes, in document order - the ordinals the spec asserts against:
//
//   ord  id          measure  written   duration  string  fret  pitch
//   0    n1-1-0-0    1        quarter   480       1       0     E4
//   1    n1-1-1-0    1        16th      120       1       2     F#4
//   2    n1-1-2-0    1        16th      120       1       3     G4
//   3    n1-1-3-0    1        quarter   480       1       5     A4
//   4    n1-1-4-0    1        quarter   480       2       0     B3
//   5    n1-1-5-0    1        16th      120       2       1     C4
//   6    n1-1-6-0    1        16th      120       2       3     D4
//   7    n2-1-0-0    2        quarter   480       1       0     E4
//   8    n2-1-1-0    2        quarter   480       1       2     F#4
//        n2-1-2-0    2        (rest)    480                     -
//   9    n2-1-3-0    2        quarter   480       1       5     A4
//   10   n3-1-0-0    3        eighth*   160       1       0     E4
//   11   n3-1-1-0    3        eighth*   160       1       2     F#4
//   12   n3-1-2-0    3        eighth*   160       1       3     G4
//   13   n3-1-3-0    3        quarter   480       1       5     A4
//   14   n3-1-4-0    3        half      960       2       0     B3
//
//   * a 3:2 tuplet member - <time-modification>, hence <duration> 160.
//
// Every measure sums to 1920, the 4/4 bar at divisions 480 (Rule 8):
//   m1 480+120+120+480+480+120+120, m2 480+480+480+480, m3 160*3+480+960.
//
// Fifteen sounding notes and one rest.

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

function note(id, step, alter, octave, duration, type, string, fret) {
  const alterEl = alter ? `<alter>${alter}</alter>` : "";
  return `      <note id="${id}">
        <pitch><step>${step}</step>${alterEl}<octave>${octave}</octave></pitch>
        <duration>${duration}</duration>
        <voice>1</voice>
        <type>${type}</type>
        <notations><technical><string>${string}</string><fret>${fret}</fret></technical></notations>
      </note>`;
}

// A 3:2 tuplet member: the same note shape plus <time-modification>, which the
// schema puts after <accidental> and before <notations>. `tuplet` is "start",
// "stop" or null - the bracket notation, on the first and last member only.
function tripletNote(id, step, alter, octave, string, fret, tuplet) {
  const alterEl = alter ? `<alter>${alter}</alter>` : "";
  const bracket = tuplet ? `<tuplet type="${tuplet}"/>` : "";
  return `      <note id="${id}">
        <pitch><step>${step}</step>${alterEl}<octave>${octave}</octave></pitch>
        <duration>160</duration>
        <voice>1</voice>
        <type>eighth</type>
        <time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>
        <notations>${bracket}<technical><string>${string}</string><fret>${fret}</fret></technical></notations>
      </note>`;
}

export const RANGE_MUSICXML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Guitar</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>480</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>TAB</sign><line>5</line></clef>${STAFF_DETAILS}
      </attributes>
${note("n1-1-0-0", "E", 0, 4, 480, "quarter", 1, 0)}
${note("n1-1-1-0", "F", 1, 4, 120, "16th", 1, 2)}
${note("n1-1-2-0", "G", 0, 4, 120, "16th", 1, 3)}
${note("n1-1-3-0", "A", 0, 4, 480, "quarter", 1, 5)}
${note("n1-1-4-0", "B", 0, 3, 480, "quarter", 2, 0)}
${note("n1-1-5-0", "C", 0, 4, 120, "16th", 2, 1)}
${note("n1-1-6-0", "D", 0, 4, 120, "16th", 2, 3)}
    </measure>
    <measure number="2">
${note("n2-1-0-0", "E", 0, 4, 480, "quarter", 1, 0)}
${note("n2-1-1-0", "F", 1, 4, 480, "quarter", 1, 2)}
      <note id="n2-1-2-0">
        <rest/>
        <duration>480</duration>
        <voice>1</voice>
        <type>quarter</type>
      </note>
${note("n2-1-3-0", "A", 0, 4, 480, "quarter", 1, 5)}
    </measure>
    <measure number="3">
${tripletNote("n3-1-0-0", "E", 0, 4, 1, 0, "start")}
${tripletNote("n3-1-1-0", "F", 1, 4, 1, 2, null)}
${tripletNote("n3-1-2-0", "G", 0, 4, 1, 3, "stop")}
${note("n3-1-3-0", "A", 0, 4, 480, "quarter", 1, 5)}
${note("n3-1-4-0", "B", 0, 3, 960, "half", 2, 0)}
    </measure>
  </part>
</score-partwise>`;

// The sounding-note count and the rest count of the fixture above, so a spec
// waits on the number the fixture actually has rather than a hand-copied one.
export const RANGE_NOTE_COUNT = 15;
export const RANGE_REST_COUNT = 1;
// The ordinals of the three tuplet members, and the 4/4 measure total.
export const TRIPLET_ORDINALS = [10, 11, 12];
export const MEASURE_DURATION = 1920;
