// Chord shapes for the guitar (issue #28) - real fingerings, checked against
// chord-theory.js's own arithmetic rather than trusted by hand.
//
// TWO SOURCES OF SHAPES, both self-verifying:
//
//   OPEN shapes are the common open-position chords every beginner learns
//   first - hardcoded frets, because "which open chords exist" is not
//   arithmetic, it is a small fixed list a guitarist would recognise. Each
//   one's ACTUAL sounded notes (shapeNotes, worked out from neck.js's own
//   noteAt/pitchClass - the same arithmetic the neck draws with) is what
//   tests/unit/chord-shapes.spec.js checks against chord-theory.js's
//   chordTones for the shape's declared root and quality. A wrong fret in
//   the list below fails that test; it is not merely "looks about right".
//
//   BARRE shapes are generated, not listed - the E-shape and A-shape
//   moveable forms, at every fret a scope's region allows. A barre shape's
//   root is read off the neck itself (noteAt on the shape's own base
//   string), not looked up in a table, so every root from 1 to 12 fret
//   positions is correct BY CONSTRUCTION rather than by a list of twelve
//   entries somebody had to get right by hand. shapeNotes verifies it
//   anyway, the same as an open shape.
//
// BOTH KINDS ASSUME THE STANDARD GUITAR'S OWN STRING-INTERVAL PATTERN -
// isStandardGuitarTuning checks the SEMITONES between adjacent strings, not
// their absolute pitch, so a shape still fits a capo'd standard-tuned
// instrument (every string shifts together, sounding_midi already carries
// it - see neck.js's stringsFromInstrument) but returns nothing for a
// seven-string guitar, a dropped tuning, or any instrument that is not
// fretted at all. No shapes is the honest answer there, not an invented
// one - the drill built on this reports the same "nothing to ask" state a
// narrow fret range or an empty key reaches (see constraints.js).
import { noteAt, pitchClass } from "./neck.js";
import { chordTones } from "./chord-theory.js";

// Semitones from string N to string (N-1), for N = 6..2 - i.e. index 0 is
// string 6 to string 5, index 4 is string 2 to string 1. This is standard
// tuning's OWN interval pattern (fourths, except a major third between the
// third and second strings), independent of what the strings actually
// sound - see neck.spec.js's identical "fifth-fret relationship" check,
// which is this same fact stated the other way round.
const STANDARD_INTERVALS = [5, 5, 5, 4, 5];

/** Whether `strings` carries the standard guitar's six strings (numbers 1-6
 * present) tuned with the standard interval pattern between each adjacent
 * pair - the one fact every shape below assumes. True for a capo'd standard
 * tuning (every interval is preserved, only the absolute pitch moves);
 * false for anything else, including a partial or non-standard string set. */
export function isStandardGuitarTuning(strings) {
  const byNumber = new Map((strings ?? []).map((s) => [s.number, s.midi]));
  for (let n = 6; n >= 1; n--) {
    if (!byNumber.has(n) || !Number.isFinite(byNumber.get(n))) return false;
  }
  for (let n = 6; n >= 2; n--) {
    if (byNumber.get(n - 1) - byNumber.get(n) !== STANDARD_INTERVALS[6 - n]) return false;
  }
  return true;
}

// The common open-position chords. `frets` is [[string, fret], ...] for
// every string the shape actually sounds - a string simply absent from the
// list is muted, the same convention "x" marks in a chord diagram. Root and
// quality are the claim tests/unit/chord-shapes.spec.js checks shapeNotes
// against; see this module's own docstring.
const OPEN_SHAPE_TEMPLATES = [
  { root: "E", quality: "major", frets: [[6, 0], [5, 2], [4, 2], [3, 1], [2, 0], [1, 0]] },
  { root: "E", quality: "minor", frets: [[6, 0], [5, 2], [4, 2], [3, 0], [2, 0], [1, 0]] },
  { root: "E", quality: "dominant7", frets: [[6, 0], [5, 2], [4, 0], [3, 1], [2, 0], [1, 0]] },
  { root: "A", quality: "major", frets: [[5, 0], [4, 2], [3, 2], [2, 2], [1, 0]] },
  { root: "A", quality: "minor", frets: [[5, 0], [4, 2], [3, 2], [2, 1], [1, 0]] },
  { root: "A", quality: "dominant7", frets: [[5, 0], [4, 2], [3, 0], [2, 2], [1, 0]] },
  { root: "D", quality: "major", frets: [[4, 0], [3, 2], [2, 3], [1, 2]] },
  { root: "D", quality: "minor", frets: [[4, 0], [3, 2], [2, 3], [1, 1]] },
  { root: "D", quality: "dominant7", frets: [[4, 0], [3, 2], [2, 1], [1, 2]] },
  { root: "G", quality: "major", frets: [[6, 3], [5, 2], [4, 0], [3, 0], [2, 0], [1, 3]] },
  { root: "G", quality: "dominant7", frets: [[6, 3], [5, 2], [4, 0], [3, 0], [2, 0], [1, 1]] },
  { root: "C", quality: "major", frets: [[5, 3], [4, 2], [3, 0], [2, 1], [1, 0]] },
  { root: "B", quality: "dominant7", frets: [[5, 2], [4, 1], [3, 2], [2, 0], [1, 2]] },
];

// The two moveable barre forms, as offsets from a base fret on the form's
// own root string - the E-shape barres on string 6, the A-shape on string
// 5 (and mutes string 6 entirely, the same as its open A-chord ancestor
// does). Each offset is relative to the base fret, not absolute, which is
// what makes the form moveable: barreShapesFor adds a base fret and reads
// the resulting root straight off the neck rather than from a second table.
const BARRE_TEMPLATES = {
  "barre-e-major": {
    rootString: 6,
    quality: "major",
    offsets: [[6, 0], [5, 2], [4, 2], [3, 1], [2, 0], [1, 0]],
  },
  "barre-e-minor": {
    rootString: 6,
    quality: "minor",
    offsets: [[6, 0], [5, 2], [4, 2], [3, 0], [2, 0], [1, 0]],
  },
  // The E-shape's minor-seventh and major-seventh forms (issue #252) - the
  // open Em7 (022030-style: 0 2 0 0 0 0) and Emaj7 (0 2 1 1 0 0) fingerings,
  // made movable the same way barre-e-major/minor already are: every fret
  // relative to a base that slides up the neck. shape-tones.spec.js checks
  // every instance this produces against chord-theory.js's own chordTones,
  // the same as every other shape here - these two are not exempt for being
  // new.
  "barre-e-minor7": {
    rootString: 6,
    quality: "minor7",
    offsets: [[6, 0], [5, 2], [4, 0], [3, 0], [2, 0], [1, 0]],
  },
  "barre-e-major7": {
    rootString: 6,
    quality: "major7",
    offsets: [[6, 0], [5, 2], [4, 1], [3, 1], [2, 0], [1, 0]],
  },
  "barre-a-major": {
    rootString: 5,
    quality: "major",
    offsets: [[5, 0], [4, 2], [3, 2], [2, 2], [1, 0]],
  },
  "barre-a-minor": {
    rootString: 5,
    quality: "minor",
    offsets: [[5, 0], [4, 2], [3, 2], [2, 1], [1, 0]],
  },
  // The A-shape's minor-seventh and major-seventh forms (issue #252) - the
  // open Am7 (x02010) and Amaj7 (x02120) fingerings, made movable the same
  // way barre-a-major/minor already are.
  "barre-a-minor7": {
    rootString: 5,
    quality: "minor7",
    offsets: [[5, 0], [4, 2], [3, 0], [2, 1], [1, 0]],
  },
  "barre-a-major7": {
    rootString: 5,
    quality: "major7",
    offsets: [[5, 0], [4, 2], [3, 1], [2, 2], [1, 0]],
  },
};

/** Every open-position shape the standard tuning offers - empty for any
 * other tuning (see isStandardGuitarTuning). Each entry: { id, root,
 * quality, family: "open", frets: [{string, fret}] }. */
export function openShapesFor(strings) {
  if (!isStandardGuitarTuning(strings)) return [];
  return OPEN_SHAPE_TEMPLATES.map((t) => ({
    id: `open:${t.root}:${t.quality}`,
    root: t.root,
    quality: t.quality,
    family: "open",
    frets: t.frets.map(([string, fret]) => ({ string, fret })),
  }));
}

/** Every barre-shape instance whose lowest fret is between `minBaseFret`
 * and `maxFret` inclusive of every fret the shape itself uses - empty for
 * any tuning that is not standard. One instance per (template, base fret),
 * its root read straight off the neck at the template's own root string and
 * that base fret, so every root the shape can name across the range is
 * produced without a lookup table to keep in step with chord-theory.js by
 * hand. `maxFret` defaults to 12 frets past minBaseFret when omitted -
 * callers scoping a region pass the neck's own fret count instead. */
export function barreShapesFor(strings, { minBaseFret = 1, maxFret } = {}) {
  if (!isStandardGuitarTuning(strings)) return [];
  const top = Number.isFinite(maxFret) ? maxFret : minBaseFret + 12;
  const out = [];
  for (const [family, tpl] of Object.entries(BARRE_TEMPLATES)) {
    const highestOffset = Math.max(...tpl.offsets.map(([, rel]) => rel));
    for (let base = Math.max(0, minBaseFret); base + highestOffset <= top; base++) {
      const midi = noteAt(strings, tpl.rootString, base);
      if (midi == null) continue;
      out.push({
        id: `${family}:${base}`,
        root: pitchClass(midi),
        quality: tpl.quality,
        family,
        frets: tpl.offsets.map(([string, rel]) => ({ string, fret: base + rel })),
      });
    }
  }
  return out;
}

/** Every shape (open and barre) `strings` can offer, up to `maxFret` -
 * everything allShapesInScope further narrows by region and by which
 * families/qualities a scope's chosen preset admits. */
export function allShapes(strings, { maxFret } = {}) {
  return [...openShapesFor(strings), ...barreShapesFor(strings, { maxFret })];
}

/** The pitch classes a shape ACTUALLY sounds, worked out from `strings`'
 * own tuning via neck.js's noteAt/pitchClass - independent of the shape's
 * declared root and quality, which is what makes this a real check rather
 * than restating the label. Frets outside the neck's MIDI range are simply
 * skipped, the same as neck.js's own positions(). */
export function shapeNotes(strings, shape) {
  return (shape?.frets ?? [])
    .map(({ string, fret }) => noteAt(strings, string, fret))
    .filter((midi) => midi != null)
    .map(pitchClass);
}

/** Whether a shape's actual sounded notes are exactly the tones of the
 * chord it claims (or of an explicit root/quality pair, for grading a
 * player's own tap-built shape against a target chord rather than against
 * one specific fingering) - same pitch classes, none missing, none extra.
 * Octave and how many strings double a tone are irrelevant: a chord is its
 * pitch-class SET, the same rule chord-theory.js's chordTones states. */
export function shapeMatchesChord(strings, shape, root = shape?.root, quality = shape?.quality) {
  const want = chordTones(root, quality);
  if (!want) return false;
  const got = new Set(shapeNotes(strings, shape));
  if (got.size !== want.length) return false;
  return want.every((note) => got.has(note));
}
