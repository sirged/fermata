// The first exercise: a pitch sounds, and you name it from four candidates.
//
// Everything here is arithmetic and phrasing - no runes and no browser - so all
// of it is callable straight from tests/unit/ear-training.spec.js. The pattern
// is pitch.js's and practice.js's, and for the same reason: the part of a drill
// worth getting right is which four notes it offers and what it says about the
// answer, and neither of those needs a page to be checked.
//
// THIS IS ONE EXERCISE, NOT A FRAMEWORK. There is no notion of a generic drill,
// no registry, and no shared question type. A second exercise is what should
// motivate any abstraction here, and until one exists an abstraction would be a
// guess about what it needs. What IS shared is deliberately shared: the audio
// path is score-render.js's playPitch, the pitch spelling is pitch.js's, and
// the way practice is put into words is practice.js's.
//
// THE TONE RULES ARE practice.js's, and they bind harder here than anywhere
// else in this project. A drill is the easiest place in a practice tool to
// start grading somebody. So:
//
//   No accuracy percentage.       A count is a fact; a percentage out of a
//                                 hundred is a mark, and it invites a colour.
//   No streak, no run.            Nothing here counts consecutive anything.
//   A wrong answer is the point.  Naming a note you could not hear is what the
//                                 practice consists of. It is stated and
//                                 nothing else happens.
//   Replays are free.             Hearing a note five times is practising, not
//                                 cheating, so nothing counts them.
//
// tests/unit/ear-training.spec.js checks every string this module produces
// against practice.js's own FORBIDDEN_WORDS list, so the rules above are
// enforced rather than merely written down.
import { MAX_MIDI, MIN_MIDI, spellMidi } from "./pitch.js";
import { countOrNone, formatDuration } from "./practice.js";

/** Four: one right and three worth confusing. From the issue - the exercise is
 * "play sound pick correct note one of 4". */
export const CHOICE_COUNT = 4;

/** A range has to hold the answer and three others, so three semitones is the
 * narrowest one a question can be asked in. Reachable through the ordinary
 * interface: a one-string unfretted instrument's definition spans a single
 * note. */
export const MIN_RANGE_SEMITONES = CHOICE_COUNT - 1;

/** The range used when the drill is not following an instrument: C2 to C6, four
 * octaves. Wide enough that an octave confusion is always available and low
 * enough to sit inside a guitar's, a bass's and a cello's own ranges rather
 * than above them. */
export const DEFAULT_RANGE = { low: 36, high: 84, top: "chosen" };

// ------------------------------------------- what the drill sounds, and for how long
//
// Both are this exercise's own, passed to playPitch rather than inherited from
// it. They used to be its defaults, which were chosen for CHECKING A TUNING -
// a different task, and the reason a constant should not be shared between the
// two just because one of them was written first.

/** The voice the drill sounds, as a raw (0-based) midi program: 0, Acoustic
 * Grand Piano.
 *
 * NOT the nylon guitar (24) the tuning check uses, and this is measured rather
 * than preferred. In the soundfont Fermata ships (sonivox.sf2), program 24 has
 * **three** sample zones for the whole keyboard, so a note is a single recording
 * pitch-shifted a long way: over this drill's default C2-C6 the worst case is
 * **24 semitones - two full octaves** - from its sample's root, and over a
 * guitar's own E2-D6 it is 26. A note transposed two octaves is a thin,
 * formant-shifted artefact, so at the ends of the range the exercise would be
 * testing the soundfont rather than the ear.
 *
 * Program 0 has **eleven** zones, roughly one per octave: the same worst case is
 * 12 semitones, and 7 over a violin's range and 6 over a cello's. It is also the
 * right voice on its own merits - a piano is the reference instrument ear
 * training is taught on, its attack is unambiguous and its fundamental clear,
 * and this exercise is about the NAME of a pitch rather than the timbre of an
 * instrument somebody may not even play.
 *
 * The tuning check keeps the guitar deliberately: matching a string by ear wants
 * something like the instrument in hand, and its range is one instrument's
 * strings rather than four octaves. */
export const DRILL_VOICE = 0;

/** How long the note is held, in seconds.
 *
 * Longer than the tuning check's 1.6, which is enough to hear a string against
 * the one you are tuning and is not the same task: identifying a pitch cold
 * needs the attack to pass and the sustain to be heard AS a pitch rather than as
 * a transient, and it wants long enough to hum against. Short enough that four
 * notes in a row is not a wait.
 *
 * A round number chosen for stated reasons rather than a measured one - nobody
 * has listened to this yet, and it is a parameter precisely so that whoever
 * does can change it here without touching the tuning check. */
export const DRILL_SECONDS = 2.5;

/** The synthesiser's reference pitch, which is fixed. score-render.js's
 * playPitch says the same thing in prose: alphaTab's synth is equal-tempered
 * around A440 and takes no reference, so an instrument defined at A415 has its
 * frequencies SHOWN at A415 and is SOUNDED at A440. This drill does not pretend
 * otherwise - see referenceStatement. */
export const SYNTH_REFERENCE_HZ = 440;

function clamp(midi) {
  return Math.min(MAX_MIDI, Math.max(MIN_MIDI, Math.round(midi)));
}

export function rangeWidth(range) {
  return (range?.high ?? 0) - (range?.low ?? 0);
}

/** Whether four distinct notes can be drawn from a range at all. A range that
 * cannot is not a broken definition and not an error - a single-string
 * unfretted instrument is a real thing to own - so the interface says what is
 * true of it rather than refusing to explain. */
export function rangeIsAskable(range) {
  if (!range || !Number.isFinite(range.low) || !Number.isFinite(range.high)) return false;
  return rangeWidth(range) >= MIN_RANGE_SEMITONES;
}

/** "E2 to D6" - the two ends, named. */
export function rangeLabel(range) {
  if (!range) return "";
  return `${spellMidi(range.low)} to ${spellMidi(range.high)}`;
}

/** The range, and the instrument it came from when it came from one. */
export function rangeStatement(range, instrumentName = "") {
  const label = rangeLabel(range);
  return instrumentName ? `${label}, ${instrumentName}` : label;
}

/** The notes an instrument's own definition says it sounds.
 *
 * The bottom is its lowest string. The top is its highest string plus the frets
 * it declares - which is the whole of what a definition knows about how high it
 * reaches, and on an unfretted one is nothing at all, so an unfretted range
 * stops at its top string and `top` says so. Inventing a couple of octaves
 * above a violin's E string would be this module guessing at a technique
 * ceiling it has no information about.
 *
 * Reads `sounding_midi`, so a capo counts: the capo decides what the instrument
 * sounds, which is the same rule the instruments editor auditions by. */
export function instrumentRange(instrument) {
  const midis = (instrument?.strings ?? [])
    .map((s) => Number(s?.sounding_midi))
    .filter((n) => Number.isFinite(n));
  if (!midis.length) return null;
  const frets = instrument?.fretted ? Math.max(0, Number(instrument.fret_count) || 0) : 0;
  return {
    low: clamp(Math.min(...midis)),
    high: clamp(Math.max(...midis) + frets),
    top: frets > 0 ? "frets" : "strings",
  };
}

/** Why an instrument's range stops where it does, when that needs saying.
 *
 * Only for an unfretted one, and only because the answer is surprising: a
 * cello's range on screen ends at its top string, which is nowhere near where a
 * cellist plays. Saying so is better than either silently narrowing the drill
 * or inventing a ceiling. */
export function rangeSourceStatement(range) {
  if (range?.top !== "strings") return "";
  return (
    "An unfretted definition says what its strings are tuned to and nothing about how high " +
    "it is played, so this drill stays inside what its strings sound."
  );
}

/** That the pitches sounded here are at A440 whatever the instrument says, when
 * the instrument says something else. Empty when there is nothing to disclose,
 * which is most instruments - a disclosure printed unconditionally is one
 * nobody reads. */
export function referenceStatement(instrument) {
  const hz = Number(instrument?.reference_pitch);
  if (!Number.isFinite(hz) || hz === SYNTH_REFERENCE_HZ) return "";
  const name = instrument?.name ? `${instrument.name} is` : "This instrument is";
  return (
    `${name} defined at A${Number(hz.toFixed(2))}, and the synthesiser here is fixed at ` +
    `A${SYNTH_REFERENCE_HZ}. The names are the ones you are naming; the reference is not yours.`
  );
}

/** Which note to sound next.
 *
 * Uniform across the range, except that it never repeats the note just heard:
 * the same pitch twice running reads as the drill having failed to advance
 * rather than as a second question, and it is the one repeat a person would
 * notice. Not a memory of everything asked so far - that would drift the drill
 * away from uniform towards whatever has not come up yet, which is a different
 * exercise. */
export function pickTarget(range, previous = null, rand = Math.random) {
  if (!range || !Number.isFinite(range.low) || rangeWidth(range) < 0) return null;
  const span = rangeWidth(range) + 1;
  const excluded = previous != null && previous >= range.low && previous <= range.high;
  const count = excluded ? span - 1 : span;
  if (count <= 0) return null;
  // Math.min guards the rand() === 1 case, which Math.random never returns but
  // a caller's own generator may.
  const index = Math.min(count - 1, Math.floor(rand() * count));
  const midi = range.low + index;
  return excluded && midi >= previous ? midi + 1 : midi;
}

// The three kinds of distractor, from the issue: "near neighbours, or the same
// note in another octave, rather than four notes far apart which teaches
// nothing". They are three different confusions and a drill that offers one of
// each asks about all three at once -
//
//   a semitone away  the hardest and the most useful: a semitone is the
//                    interval a beginner cannot hear at all and the one an
//                    intermediate player mishears under pressure;
//   an octave away    the same NAME in the wrong place, which is the error that
//                    survives longest because the note sounds right;
//   two to five away  a step or a third - close enough to need listening,
//                    far enough that getting it wrong means something
//                    different from the semitone case.
const SEMITONE_OFFSETS = [-1, 1];
const OCTAVE_OFFSETS = [-12, 12, -24, 24];
const NEARBY_OFFSETS = [-5, -4, -3, -2, 2, 3, 4, 5];

/** The candidates of each kind that a range actually allows. Exported because
 * "the offered notes span all three kinds" is the claim worth testing, and it
 * is far easier to check against the pools than to infer from a shuffled four. */
export function distractorKinds(sounded, range) {
  const inside = (midi) =>
    midi !== sounded &&
    midi >= MIN_MIDI &&
    midi <= MAX_MIDI &&
    midi >= (range?.low ?? MIN_MIDI) &&
    midi <= (range?.high ?? MAX_MIDI);
  const from = (offsets) => offsets.map((o) => sounded + o).filter(inside);
  return {
    semitone: from(SEMITONE_OFFSETS),
    octave: from(OCTAVE_OFFSETS),
    nearby: from(NEARBY_OFFSETS),
  };
}

/** The four notes to offer, given the note that actually SOUNDED.
 *
 * Takes the sounded note rather than the intended one on purpose. The caller
 * builds a question out of what came back from the synthesiser, so a drill that
 * sounded something other than what it meant to offers choices around what was
 * heard - and a drill that sounded nothing at all has no question to ask, which
 * is the only honest behaviour when the audio path is what the exercise is for.
 *
 * One of each kind where the range allows all three. Where it does not - a
 * range narrower than an octave has no octave to offer - the shortfall is
 * filled nearest-first from what is left, because the nearest is the most worth
 * confusing. Four distinct notes always come back when the range can hold them;
 * an empty array when it cannot. */
export function buildChoices(sounded, range, rand = Math.random) {
  if (!Number.isFinite(sounded) || !rangeIsAskable(range)) return [];
  const kinds = distractorKinds(sounded, range);
  const picked = [];
  const takeFrom = (list) => {
    const free = list.filter((m) => !picked.includes(m));
    if (!free.length) return;
    picked.push(free[Math.min(free.length - 1, Math.floor(rand() * free.length))]);
  };
  for (const kind of ["semitone", "octave", "nearby"]) takeFrom(kinds[kind]);
  if (picked.length < CHOICE_COUNT - 1) {
    const rest = [...new Set([...kinds.semitone, ...kinds.nearby, ...kinds.octave])]
      .filter((m) => !picked.includes(m))
      .sort((a, b) => Math.abs(a - sounded) - Math.abs(b - sounded));
    while (picked.length < CHOICE_COUNT - 1 && rest.length) picked.push(rest.shift());
  }
  return shuffle([sounded, ...picked], rand);
}

function shuffle(items, rand) {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.min(i, Math.floor(rand() * (i + 1)));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/** What happened, once an answer has been given.
 *
 * The note is named either way, in the same words and in the same place. When
 * the answer was a different note that is said too, because knowing WHICH note
 * you took it for is the whole of the information - "wrong" tells a person
 * nothing they can practise with, and a G heard as a G an octave down is a
 * different thing to work on from a G heard as an F sharp.
 *
 * There is no verdict word in either branch, and no third branch for "well
 * done". A wrong answer in ear training is not a shortfall, it is the practice. */
export function roundStatement(round) {
  const sounded = round?.sounded;
  const chosen = round?.chosen;
  if (!Number.isFinite(sounded) || !Number.isFinite(chosen)) return "";
  const was = `That was ${spellMidi(sounded)}.`;
  if (chosen === sounded) return was;
  return `${was} You chose ${spellMidi(chosen)}.`;
}

/** How the drill has gone so far. Two counts and nothing else: no percentage,
 * no ratio drawn as a bar, and no adjective. The practice page's "3 of 4
 * planned days" is the model. */
export function progressStatement({ asked = 0, named = 0 } = {}) {
  const total = Math.max(0, Math.floor(Number(asked) || 0));
  if (!total) return "Nothing named yet.";
  const notes = total === 1 ? "1 note" : `${total} notes`;
  return `${notes}, ${countOrNone(named)} named as heard.`;
}

/** What goes in the session's `note` when the drill is logged.
 *
 * docs/practice-data.md says per-attempt trainer results - which notes were
 * missed, and how long each took - are not in the schema, and that inventing
 * their shape before a trainer existed would be guessing. One now exists and
 * this is deliberately still not that: the counts go in the free-text note,
 * where they are readable in the practice history beside every other kind of
 * work, and the shape of a per-attempt table can be decided by the second and
 * third exercises rather than by this one alone. */
export function sessionNote({ asked = 0, named = 0, range, instrumentName = "" } = {}) {
  return `Hear a note, name it. ${progressStatement({ asked, named })} ${rangeStatement(
    range,
    instrumentName,
  )}.`;
}

/** What was logged, said back once the drill has stopped. States the length and
 * where it went, and links nothing to a verdict - the practice page is where
 * this now lives, and it counts towards a goal about ear training exactly as
 * any other session does. */
export function loggedStatement(seconds) {
  return `${formatDuration(seconds)} of ear training is in your practice history.`;
}
