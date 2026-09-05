// The MusicXML document, as the editor's source of truth (#10).
//
// The renderer-evaluation on #10 settled the shape of this whole feature: the
// MusicXML document is the model, and alphaTab's Score is a VIEW of it. Every
// edit is written here, to the document; the renderer is then handed the new
// document and asked to draw it afresh. Nothing is written to the renderer's
// object graph and left for the document to catch up with later - that
// "two writes, one edited" divergence is the bug class the evaluation names as
// the one this design must not create, and the way this file avoids it is by
// being the ONLY writer. The screen is always a re-import of what is written
// here, so the two cannot drift.
//
// This runs in the browser (it uses DOMParser/XMLSerializer). The pure
// arithmetic it leans on - pitch from string+fret, the Rule 5 string/tuning
// mirror, duration from type - is in editor/notes.js, which has no DOM and is
// unit-tested on its own.
import {
  DURATION_TYPES,
  accidentalName,
  durationForDots,
  durationForType,
  enharmonicSpellings,
  isWritablePitch,
  keyAlter,
  midiForStringFret,
  midiOfPitch,
  spellPitch,
  spellWithAlter,
  stringToTuningLine,
} from "./notes.js";

export { DURATION_TYPES };

function firstChildTag(el, tag) {
  if (!el) return null;
  for (const child of el.children) if (child.tagName === tag) return child;
  return null;
}

function tagText(el, tag) {
  const found = firstChildTag(el, tag);
  return found ? found.textContent.trim() : null;
}

// A note is a rest exactly when it carries a <rest/> child. Both a rest and a
// sounding note carry a Rule 17 id and a <duration>; only a sounding note
// carries <pitch> and <notations><technical>. The editor selects and edits
// sounding notes; rests keep their place in document order (so an ordinal
// never shifts) but are not offered as edit targets in this increment.
function isRest(noteEl) {
  return !!firstChildTag(noteEl, "rest");
}

function technicalOf(noteEl) {
  const notations = firstChildTag(noteEl, "notations");
  return notations ? firstChildTag(notations, "technical") : null;
}

// A chord member carries <chord/> as its first child (Rule 7); only the first
// note of a chord advances the measure's time cursor.
function hasChord(noteEl) {
  return !!firstChildTag(noteEl, "chord");
}

// The <tie type=> sound element of the given type on a note, or null (#183). A
// note carries up to two: a note held into AND out of has both a stop (from the
// previous note) and a start (into the next). This is the SOUND half of a tie;
// the <tied> notation half lives in <notations>.
function firstTie(noteEl, type) {
  for (const child of noteEl.children) {
    if (child.tagName === "tie" && child.getAttribute("type") === type) return child;
  }
  return null;
}

function hasTie(noteEl, type) {
  return !!firstTie(noteEl, type);
}

// The integer <duration> of a <note>, <backup> or <forward> - what moves the
// measure's time cursor. 0 when absent or unreadable, so the walk never adds a
// NaN it can never recover from.
function intDuration(el) {
  const n = Number(tagText(el, "duration"));
  return Number.isFinite(n) ? n : 0;
}

// A note's (or forward's) <voice> as a number, or null. This profile writes
// voices as consecutive numeric strings from 1 (Rule 6), so a number is what
// the model compares and orders them by.
function voiceNumber(el) {
  const n = Number(tagText(el, "voice"));
  return Number.isFinite(n) ? n : null;
}

/**
 * Build the editable document from MusicXML text. Throws if the text is not
 * parseable XML or is not a score this profile writes (one part, a tab staff
 * with a tuning) - the caller shows that plainly rather than entering an edit
 * mode over something it cannot map.
 */
export function createDocument(xml) {
  // The parameter is `xml`, deliberately NOT `text`: this closure defines a
  // `text()` serializer method below, and a same-named function declaration
  // hoists over a parameter of that name - so a parameter called `text` would
  // be the function, and this parse would read the function's source ("function
  // ...") instead of the document, failing at column 1.
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  // DOMParser reports a malformed document as a <parsererror> element rather
  // than by throwing, so it has to be looked for.
  const parseError = doc.getElementsByTagName("parsererror")[0];
  if (parseError) {
    throw new Error("This transcription is not valid XML, so it cannot be opened in the note editor.");
  }
  const root = doc.documentElement;
  if (!root || root.tagName !== "score-partwise") {
    throw new Error("The note editor works on partwise MusicXML transcriptions only.");
  }

  // The tuning, read once. line -> MIDI of that open string. `<staff-tuning
  // line=>` numbers from the bottom staff line (the lowest string); a note's
  // <string> numbers from the top. midiForStringFret / stringToTuningLine keep
  // that mirror in one place (Rule 5).
  const tuningByLine = new Map();
  for (const st of doc.getElementsByTagName("staff-tuning")) {
    const line = Number(st.getAttribute("line"));
    const step = tagText(st, "tuning-step");
    const octave = Number(tagText(st, "tuning-octave"));
    const alter = Number(tagText(st, "tuning-alter") ?? 0) || 0;
    const midi = step != null ? midiOfPitch(step, octave, alter) : null;
    if (Number.isFinite(line) && midi != null) tuningByLine.set(line, midi);
  }
  const stringCount = tuningByLine.size;
  if (stringCount === 0) {
    throw new Error("This transcription has no string tuning, so its notes have no fretboard to edit on.");
  }

  const divisionsText = doc.getElementsByTagName("divisions")[0]?.textContent;
  const divisions = Number(divisionsText);

  // The key signature (`<key><fifths>`) in force at each measure, so a
  // recomputed pitch is spelled against it (Rules 12-13). This profile declares
  // the key once in the first measure (Rule 4), but a general reader tracks a
  // later change too: walk measures in document order, carrying the last
  // `<fifths>` seen forward. An absent or unreadable key is C major (fifths 0),
  // which MusicXML means by no key signature (Rule 13's stated default). The
  // <key> is read from the measure's OWN attributes, not a descendant walk, so a
  // measure with no key of its own inherits rather than resets.
  const fifthsByMeasure = new Map();
  let fifthsInForce = 0;
  for (const measureEl of doc.getElementsByTagName("measure")) {
    const attrs = firstChildTag(measureEl, "attributes");
    const keyEl = attrs ? firstChildTag(attrs, "key") : null;
    const f = keyEl ? Number(tagText(keyEl, "fifths")) : NaN;
    if (Number.isFinite(f)) fifthsInForce = f;
    fifthsByMeasure.set(measureEl, fifthsInForce);
  }
  // The document's opening key, for a caller that shows or tests it.
  const documentFifths = fifthsByMeasure.values().next().value ?? 0;

  function fifthsOf(el) {
    const measureEl = el?.closest ? el.closest("measure") : null;
    return (measureEl && fifthsByMeasure.get(measureEl)) ?? 0;
  }

  // The sounding notes, in document order (measure -> voice's onset -> chord
  // member), each paired with its <note> element. This is exactly the order
  // score-render.js walks the alphaTab model in to build the positional map,
  // so an index into this array IS the "ordinal" the two sides agree on. Rests
  // are skipped by both, consistently.
  const noteEls = [...doc.getElementsByTagName("note")].filter((n) => !isRest(n));

  // The RESTS in document order, addressed the same way (0..restEls.length-1)
  // - the exact complement of noteEls (#238). Rests were "not offered as edit
  // targets" until now; this is what lets one be selected and looked up on
  // its own, parallel to noteEls/ordinal, but in a namespace the RENDERER's
  // positional map (score-render.js's buildNoteOrdinals) has no notion of -
  // it skips rests exactly as it always has, unchanged - so a rest selection
  // carries no on-screen note-head bounds (see TabViewer's selectRest).
  const restEls = [...doc.getElementsByTagName("note")].filter(isRest);

  // The `<measure number>` each sounding note sits in, indexed by ordinal - the
  // model's coarse navigation unit. The keyboard core loop (#186) steps the
  // selection note-to-note along this array (an ordinal +/- 1) and bar-to-bar
  // across it (the next/previous measure), mirroring the transport's own two
  // arrow granularities (a beat, and a whole bar - see score-render.js's
  // moveCursorBeat / moveCursorBar). Read once here rather than walked on every
  // keypress. `closest` is a DOM method the parsed elements carry.
  const measureNums = noteEls.map((el) => {
    const m = el.closest ? el.closest("measure") : null;
    const n = m ? Number(m.getAttribute("number")) : NaN;
    return Number.isFinite(n) ? n : null;
  });
  // The distinct measures in document order, so bar-to-bar stepping works even
  // when measure numbers are not a contiguous 1..N run (a pickup numbered 0, a
  // repeat that re-uses a number): the neighbour is the adjacent entry HERE,
  // not measureNum +/- 1.
  const measureOrder = [];
  for (const n of measureNums) if (n != null && !measureOrder.includes(n)) measureOrder.push(n);

  // Each note's onset (in <divisions>) within its measure, and the set of
  // voices its measure sounds - both read once here from a single per-measure
  // time-cursor walk. MusicXML places notes on a per-measure cursor that each
  // note's <duration> advances; a <backup> rewinds it and a <forward> advances
  // it without a note. Because this profile returns the cursor to the measure
  // start between voices (Rule 6), a note's onset counted from measure-start IS
  // its onset within its own voice. The move-a-note-to-another-voice edit
  // (#182) leans on exactly this arithmetic; describe() exposes onset so a
  // moved note can be shown to keep the same onset in its new voice, and the
  // voice set so the control knows which voices a note may move to.
  const onsetByEl = new Map();
  const voicesByMeasure = new Map(); // measureEl -> Set<voiceNum that sounds>
  // measureEl -> the measure's total duration (its highest cursor). A tie whose
  // partner is the first note of the next measure (a cross-barline tie, #183)
  // needs to know this note reaches its own measure's end; read once here from
  // the same walk rather than re-derived per keypress.
  const measureDurByEl = new Map();
  for (const measureEl of doc.getElementsByTagName("measure")) {
    let cursor = 0;
    let headOnset = 0;
    let measureDur = 0;
    const voices = new Set();
    voicesByMeasure.set(measureEl, voices);
    for (const child of measureEl.children) {
      const tag = child.tagName;
      if (tag === "backup") {
        cursor -= intDuration(child);
        continue;
      }
      if (tag === "forward") {
        cursor += intDuration(child);
        if (cursor > measureDur) measureDur = cursor;
        continue;
      }
      if (tag !== "note") continue;
      const v = voiceNumber(child);
      if (v != null && !isRest(child)) voices.add(v);
      if (hasChord(child)) {
        // A chord member shares the onset of its beat's first note and does not
        // move the cursor (Rule 7).
        onsetByEl.set(child, headOnset);
      } else {
        onsetByEl.set(child, cursor);
        headOnset = cursor;
        cursor += intDuration(child);
        if (cursor > measureDur) measureDur = cursor;
      }
    }
    measureDurByEl.set(measureEl, measureDur);
  }

  function describe(el, ordinal) {
    const tech = technicalOf(el);
    const string = tech ? Number(tagText(tech, "string")) : null;
    const fret = tech ? Number(tagText(tech, "fret")) : null;
    const type = tagText(el, "type");
    const dots = [...el.children].filter((c) => c.tagName === "dot").length;
    const pitchEl = firstChildTag(el, "pitch");
    const step = pitchEl ? tagText(pitchEl, "step") : null;
    const alter = pitchEl ? Number(tagText(pitchEl, "alter") ?? 0) : null;
    const midi = pitchEl ? midiOfPitch(step, Number(tagText(pitchEl, "octave")), alter) : null;
    // The printed <accidental>, verbatim, or null when the note carries none -
    // so the panel can show which accidental is in force and a test can assert
    // <alter> and <accidental> stay mutually consistent (Rule 10).
    const accEl = firstChildTag(el, "accidental");
    const accidental = accEl ? accEl.textContent.trim() : null;
    const measureEl = el.closest ? el.closest("measure") : null;
    const voices = measureEl ? voicesByMeasure.get(measureEl) : null;
    return {
      ordinal,
      id: el.getAttribute("id"),
      string: Number.isFinite(string) ? string : null,
      fret: Number.isFinite(fret) ? fret : null,
      type,
      dots,
      midi,
      // The spelling as written: the letter, its alteration, and the printed
      // accidental if any. Same sounding pitch (midi) regardless of how these
      // read (Rules 12-13).
      step,
      alter,
      accidental,
      measure: measureNums[ordinal] ?? null,
      // The note's own voice, its onset within that voice (both #182's proof
      // that a moved note keeps its onset), and how many voices its measure
      // already sounds (what the move control offers as targets).
      voice: voiceNumber(el),
      onset: onsetByEl.has(el) ? onsetByEl.get(el) : null,
      measureVoices: voices ? voices.size : null,
      // Whether this note is tied INTO the next note (a tie starts here) and/or
      // OUT of the previous one (a tie stops here) - #183. The control reflects
      // and toggles the start; the stop is shown so a tied pair reads as one.
      tieStart: hasTie(el, "start"),
      tieStop: hasTie(el, "stop"),
    };
  }

  /**
   * The ordinal of the sounding note one step forward (direction > 0) or back
   * (direction < 0) in document order, or null at (or past) either end - the
   * keyboard core loop's note-to-note move (#186). Pure: it reports where the
   * caller's selection should go, it does not touch the document.
   */
  function stepNote(ordinal, direction) {
    if (!Number.isInteger(ordinal)) return null;
    const next = ordinal + (direction > 0 ? 1 : -1);
    return next >= 0 && next < noteEls.length ? next : null;
  }

  /**
   * The ordinal of the sounding note in the adjacent measure (direction > 0
   * next, < 0 previous) at the same position within that measure - the
   * keyboard core loop's bar-to-bar move (#186). When the target measure holds
   * fewer notes, the last note in it; when there is no adjacent measure with a
   * sounding note, null. Pure, like stepNote.
   */
  function stepMeasure(ordinal, direction) {
    const here = measureNums[ordinal];
    if (here == null) return null;
    const at = measureOrder.indexOf(here);
    const target = measureOrder[at + (direction > 0 ? 1 : -1)];
    if (target == null) return null;
    // The selected note's index within its own measure, and every ordinal in
    // the target measure - so the move lands on the same-numbered note there.
    let indexInMeasure = 0;
    const inTarget = [];
    for (let i = 0; i < noteEls.length; i++) {
      if (measureNums[i] === here && i < ordinal) indexInMeasure += 1;
      if (measureNums[i] === target) inTarget.push(i);
    }
    if (inTarget.length === 0) return null;
    return inTarget[Math.min(indexInMeasure, inTarget.length - 1)];
  }

  function soundingNotes() {
    return noteEls.map(describe);
  }

  function noteAt(ordinal) {
    const el = noteEls[ordinal];
    return el ? describe(el, ordinal) : null;
  }

  // Describe the rest at `restOrdinal` (index into restEls, document order) -
  // the read side of rest selection (#238). No pitch, string or fret (a rest
  // carries none of those); the fields a caller needs to preview what
  // restToNote is about to KEEP - duration, voice, onset, written type and
  // dots - and to place it the way describe() places a sounding note.
  function describeRest(el, restOrdinal) {
    const measureEl = el.closest ? el.closest("measure") : null;
    const mnum = measureEl ? Number(measureEl.getAttribute("number")) : NaN;
    return {
      restOrdinal,
      id: el.getAttribute("id"),
      type: tagText(el, "type"),
      dots: [...el.children].filter((c) => c.tagName === "dot").length,
      duration: intDuration(el),
      voice: voiceNumber(el),
      onset: onsetByEl.has(el) ? onsetByEl.get(el) : null,
      measure: Number.isFinite(mnum) ? mnum : null,
    };
  }

  function restCount() {
    return restEls.length;
  }

  function restAt(restOrdinal) {
    const el = restEls[restOrdinal];
    return el ? describeRest(el, restOrdinal) : null;
  }

  // Every <note> (sounding or rest) in document order - the combined stepping
  // surface a left/right arrow needs once a rest is a selectable stop (#238).
  // stepNote/stepMeasure above stay note-to-note only, unchanged - the
  // RENDERER's own positional map (score-render.js's buildNoteOrdinals) skips
  // rests exactly as it always has, so nothing here disturbs the ordinal the
  // two sides agree on for a sounding note. This is a SECOND, independent
  // address space just for the keyboard loop's selection: `{ ordinal }` for a
  // sounding note (the same ordinal noteAt/the renderer use) or
  // `{ restOrdinal }` for a rest (this file's own restEls list, meaningless to
  // the renderer - see TabViewer's selectRest for why a rest selection carries
  // no on-screen head bounds, only the values describeRest reports).
  const allEls = [...doc.getElementsByTagName("note")];

  function elementIndexOf(sel) {
    if (!sel) return -1;
    if (sel.ordinal != null) return allEls.indexOf(noteEls[sel.ordinal]);
    if (sel.restOrdinal != null) return allEls.indexOf(restEls[sel.restOrdinal]);
    return -1;
  }

  function selectionOf(el) {
    if (!el) return null;
    if (isRest(el)) {
      const idx = restEls.indexOf(el);
      return idx >= 0 ? { restOrdinal: idx } : null;
    }
    const idx = noteEls.indexOf(el);
    return idx >= 0 ? { ordinal: idx } : null;
  }

  /**
   * The selection (`{ ordinal }` for a sounding note, `{ restOrdinal }` for a
   * rest) one step forward (direction > 0) or back in document order,
   * INCLUDING rests - null past either end (#238's rest-selectable
   * navigation). Pure, like stepNote: it reports where the caller's selection
   * should go next, it does not touch the document.
   */
  function stepAny(sel, direction) {
    const at = elementIndexOf(sel);
    if (at < 0) return null;
    const next = at + (direction > 0 ? 1 : -1);
    if (next < 0 || next >= allEls.length) return null;
    return selectionOf(allEls[next]);
  }

  // Write a `{ step, alter, octave }` into a note's <pitch>, keeping the schema's
  // child order (step, alter?, octave). The <alter> is omitted for a natural, as
  // the emitter does. This touches ONLY the sound element; the printed
  // <accidental> is a separate child handled by writeAccidental.
  function putPitch(el, spelled) {
    let pitch = firstChildTag(el, "pitch");
    if (!pitch) {
      pitch = doc.createElement("pitch");
      el.insertBefore(pitch, el.firstChild);
    }
    while (pitch.firstChild) pitch.removeChild(pitch.firstChild);
    const step = doc.createElement("step");
    step.textContent = spelled.step;
    pitch.appendChild(step);
    if (spelled.alter) {
      const alter = doc.createElement("alter");
      alter.textContent = String(spelled.alter);
      pitch.appendChild(alter);
    }
    const octave = doc.createElement("octave");
    octave.textContent = String(spelled.octave);
    pitch.appendChild(octave);
  }

  // Set (alter a number) or clear (alter null) a note's printed <accidental>.
  // The schema puts <accidental> after <dot>* and before <time-modification> /
  // <stem> / <notations>, so it is inserted before the first of those and
  // appended only when the note has none of them.
  function writeAccidental(el, alter) {
    const existing = firstChildTag(el, "accidental");
    if (existing) el.removeChild(existing);
    if (alter == null) return;
    const name = accidentalName(alter);
    if (!name) return;
    const acc = doc.createElement("accidental");
    acc.textContent = name;
    const anchor =
      firstChildTag(el, "time-modification") || firstChildTag(el, "stem") || firstChildTag(el, "notations");
    if (anchor) el.insertBefore(acc, anchor);
    else el.appendChild(acc);
  }

  // The explicit accidentals printed EARLIER in a note's measure, as a map
  // "STEP|OCTAVE" -> alter. Only a note carrying an <accidental> element changes
  // the running state (that is what a printed accidental IS); a note without one
  // simply conforms to whatever is already in force. Document order within the
  // measure is time order for this profile, so the walk stops at the target note.
  // This is the "accidental in force in the bar" of standard notation: it carries
  // to a later same-step/octave note until the barline, which resets it (a fresh
  // measure starts this walk over).
  function accidentalsInForce(measureEl, targetEl) {
    const map = new Map();
    if (!measureEl) return map;
    for (const note of measureEl.getElementsByTagName("note")) {
      if (note === targetEl) break;
      const acc = firstChildTag(note, "accidental");
      const pitchEl = firstChildTag(note, "pitch");
      if (!acc || !pitchEl) continue;
      const step = tagText(pitchEl, "step");
      const octave = Number(tagText(pitchEl, "octave"));
      if (step == null || !Number.isFinite(octave)) continue;
      map.set(`${step}|${octave}`, Number(tagText(pitchEl, "alter") ?? 0));
    }
    return map;
  }

  // The spelling to write for a recomputed sounding pitch, honouring both the key
  // signature and any accidental already in force in the bar (Rules 12-13):
  //
  // - If an accidental printed earlier in the bar, on some step+octave, already
  //   sounds this exact MIDI, that spelling is reused - the accidental carries,
  //   and no fresh <accidental> is printed. (A bar that spelled a note G flat
  //   spells the same sounding pitch G flat again, not F sharp.)
  // - Otherwise the key signature decides, via spellPitch.
  //
  // The printed <accidental> is then whatever the note's own alter needs to
  // override what is in force for its step+octave - the earlier printed
  // accidental if there was one, else the key signature. A natural that cancels a
  // key sharp/flat, or a prior accidental, is written as a natural; a note that
  // matches what is already in force prints nothing.
  function spellForEdit(el, midi) {
    const measureEl = el.closest ? el.closest("measure") : null;
    const fifths = fifthsOf(el);
    const inForce = accidentalsInForce(measureEl, el);
    let spelled = null;
    for (const [key, alter] of inForce) {
      const [step, octave] = key.split("|");
      if (midiOfPitch(step, Number(octave), alter) === midi) {
        spelled = { step, alter, octave: Number(octave) };
        break;
      }
    }
    if (!spelled) spelled = spellPitch(midi, fifths);
    const held = inForce.get(`${spelled.step}|${spelled.octave}`);
    const priorAlter = held != null ? held : keyAlter(spelled.step, fifths);
    const accidental = spelled.alter !== priorAlter ? spelled.alter : null;
    return { spelled, accidental };
  }

  // Recompute a note's <pitch> and printed <accidental> from a sounding MIDI
  // number, key-aware (Rules 12-13). Called whenever a fret or string changes, so
  // Rule 10's <pitch> never disagrees with the Rule 9 position that determines
  // it - and now spells that pitch against the key and the bar rather than always
  // as a sharp.
  function writePitch(el, midi) {
    const { spelled, accidental } = spellForEdit(el, midi);
    if (!spelled) return;
    putPitch(el, spelled);
    writeAccidental(el, accidental);
  }

  function setTechText(el, tag, value) {
    const tech = technicalOf(el);
    if (!tech) return false;
    const target = firstChildTag(tech, tag);
    if (!target) return false;
    target.textContent = String(value);
    return true;
  }

  /**
   * Set a sounding note's fret, recomputing its <pitch>. Refuses a negative
   * fret, and refuses any fret whose resulting pitch cannot be written (Rule
   * 11: a <pitch> outside MIDI 12-131 has no valid <octave>, and writing it
   * anyway makes the whole document unreadable to a validating consumer). The
   * note is left exactly as it was rather than written as some other pitch.
   * Returns true when the document changed.
   */
  function setFret(ordinal, fret) {
    const el = noteEls[ordinal];
    if (!el || !Number.isInteger(fret) || fret < 0) return false;
    const string = Number(tagText(technicalOf(el), "string"));
    const midi = midiForStringFret(tuningByLine, stringCount, string, fret);
    if (midi == null || !isWritablePitch(midi)) return false;
    if (!setTechText(el, "fret", fret)) return false;
    writePitch(el, midi);
    dropBrokenTies(ordinal);
    return true;
  }

  /**
   * Move a sounding note to a different string, recomputing its <pitch>.
   * Refuses a string outside 1..stringCount - the invalid, un-drawable
   * position #165's guard exists to catch is never written in the first place.
   * Returns true when the document changed.
   */
  function setString(ordinal, string) {
    const el = noteEls[ordinal];
    if (!el || !Number.isInteger(string) || string < 1 || string > stringCount) return false;
    const fret = Number(tagText(technicalOf(el), "fret"));
    const midi = midiForStringFret(tuningByLine, stringCount, string, fret);
    // Same Rule 11 refusal as setFret: an unwritable pitch is not written.
    if (midi == null || !isWritablePitch(midi)) return false;
    if (!setTechText(el, "string", string)) return false;
    writePitch(el, midi);
    dropBrokenTies(ordinal);
    return true;
  }

  // Rewrite a note's <pitch>+<accidental> to a specific spelling of the SAME
  // sounding pitch, leaving <string>/<fret> and the MIDI untouched. The shared
  // engine behind the explicit-accidental control and the enharmonic cycle: both
  // are a musical choice of how to spell one sound, not a change to the sound.
  // `spelled` is a { step, alter, octave } whose midiOfPitch equals the note's
  // current MIDI (the callers guarantee this); its printed <accidental> always
  // names its own alter, so <alter> and <accidental> stay mutually consistent
  // (Rule 10) and the chosen spelling is shown rather than left for the renderer
  // to re-derive.
  function respell(el, spelled) {
    putPitch(el, spelled);
    writeAccidental(el, spelled.alter);
  }

  /**
   * Spell the sounding note at `ordinal` with a specific accidental - flat
   * (-1), natural (0), sharp (+1), or the double variants (-2/+2) - keeping the
   * SAME sounding pitch (#185). The letter and octave follow from the pitch and
   * the chosen accidental (spellWithAlter): asking for a natural spelling of a
   * black key, or an accidental that would push the octave out of MusicXML's
   * range, is refused with the note untouched. Returns true when the document
   * changed.
   *
   * This is where a note's accidental is a musical CHOICE rather than a
   * derivation: the sounding pitch is fixed, and the player says how to write it.
   */
  function setAccidental(ordinal, alter) {
    const el = noteEls[ordinal];
    if (!el || isRest(el)) return false;
    const midi = describe(el, ordinal).midi;
    if (midi == null) return false;
    const spelled = spellWithAlter(midi, alter);
    if (!spelled) return false;
    respell(el, spelled);
    return true;
  }

  /**
   * Cycle the sounding note at `ordinal` through its enharmonic spellings -
   * F sharp <-> G flat, and so on - one step forward (direction > 0) or back
   * (< 0), keeping the SAME sounding pitch at every step (#185). The alternatives
   * are the single-accidental spellings of the pitch (flat, natural, sharp) that
   * exist for it, in that order; the octave moves with the spelling where it must
   * (B sharp 3 is the same key as C natural 4). Returns true when the document
   * changed, false when the pitch has fewer than two such spellings (nothing to
   * cycle between).
   *
   * The sounding pitch is invariant by construction: every option is a spelling
   * of the note's own MIDI, so the Rule 10 mirror stays green across a cycle.
   */
  function cycleSpelling(ordinal, direction) {
    const el = noteEls[ordinal];
    if (!el || isRest(el)) return false;
    const here = describe(el, ordinal);
    if (here.midi == null) return false;
    const options = enharmonicSpellings(here.midi);
    if (options.length < 2) return false;
    let idx = options.findIndex((o) => o.step === here.step && o.alter === here.alter);
    if (idx < 0) idx = 0;
    const step = direction > 0 ? 1 : -1;
    const next = options[(idx + step + options.length) % options.length];
    if (next.step === here.step && next.alter === here.alter) return false;
    respell(el, next);
    return true;
  }

  /**
   * Set a sounding note's written duration to a plain (undotted) type,
   * updating both <duration> and <type> and dropping any <dot/> it carried.
   * Returns true when the document changed. Structural by nature - it changes
   * the bar's arithmetic - which is why the caller re-imports the whole
   * document rather than trusting an in-place tweak (see score-render.js).
   */
  function setDurationType(ordinal, type) {
    const el = noteEls[ordinal];
    if (!el) return false;
    const value = durationForType(type, divisions);
    if (value == null) return false;
    const durationEl = firstChildTag(el, "duration");
    if (!durationEl) return false;
    durationEl.textContent = String(value);
    let typeEl = firstChildTag(el, "type");
    if (!typeEl) {
      // <type> follows <voice> in the schema's sequence; insert it after
      // <duration> if the note somehow lacked one (a tab-only onset can).
      typeEl = doc.createElement("type");
      durationEl.insertAdjacentElement?.("afterend", typeEl) ?? el.appendChild(typeEl);
    }
    typeEl.textContent = type;
    for (const dot of [...el.children].filter((c) => c.tagName === "dot")) el.removeChild(dot);
    return true;
  }

  /**
   * Set a sounding note's augmentation dots to `dots` (0, 1 or 2), keeping its
   * written `<type>` and recomputing `<duration>` so the two stay consistent
   * (#183). A dot adds half the value again: a dotted quarter is 1.5x a quarter,
   * a double-dotted quarter 1.75x (see durationForDots). Writes exactly `dots`
   * `<dot/>` elements in their schema position (immediately after `<type>`,
   * before `<accidental>`/`<notations>`) and the matching `<duration>`.
   *
   * Structural like setDurationType - it changes the bar's arithmetic, so the
   * caller re-imports the whole document rather than trusting an in-place tweak.
   * Returns true when the document changed. Refuses (leaving the note untouched)
   * when the note has no `<type>` to scale, when the dotted value is not a whole
   * number of divisions, or when the note carries a `<time-modification>`: a
   * tuplet member's sounding duration is its written value scaled by the tuplet
   * ratio too, and writing a dot-only duration over that would be inconsistent -
   * tuplets are a stated follow-on (see the PR), so a dotted tuplet is refused
   * here rather than written wrong.
   */
  function setDots(ordinal, dots) {
    const el = noteEls[ordinal];
    if (!el || !Number.isInteger(dots) || dots < 0 || dots > 2) return false;
    if (firstChildTag(el, "time-modification")) return false;
    const type = tagText(el, "type");
    if (!type) return false;
    const value = durationForDots(type, divisions, dots);
    if (value == null) return false;
    const durationEl = firstChildTag(el, "duration");
    const typeEl = firstChildTag(el, "type");
    if (!durationEl || !typeEl) return false;
    // Drop whatever dots the note had, then write the new count immediately after
    // <type> (the schema's home for <dot>), so a change from two dots to one, or
    // to none, lands correctly rather than accreting.
    for (const dot of [...el.children].filter((c) => c.tagName === "dot")) el.removeChild(dot);
    let anchor = typeEl;
    for (let i = 0; i < dots; i++) {
      const dot = doc.createElement("dot");
      anchor.insertAdjacentElement?.("afterend", dot) ?? el.appendChild(dot);
      anchor = dot;
    }
    durationEl.textContent = String(value);
    return true;
  }

  // ------------------------------------------------------------------- ties (#183)
  //
  // A tie makes two same-pitch notes read as ONE held note: the first carries a
  // <tie type="start"/> (and <tied type="start"/> notation), the second a
  // matching stop. This profile writes BOTH the sound element (<tie>, a child of
  // <note>) and the notation (<tied>, in <notations>), start on the first note
  // and stop on the second, so a validating consumer and the renderer agree.

  // The sounding note this note would tie TO: the next note in document order in
  // the SAME voice that is contiguous with it (its onset is exactly where this
  // note ends) - either the immediately-following beat in the same measure, or
  // the first beat of the next measure when this note reaches its own measure's
  // end (a cross-barline tie). Chord beats are not offered (see setTie). Returns
  // { el, ordinal } or null. Pitch is NOT checked here - setTie adds that only
  // when starting a tie, so a tie can still be REMOVED after a fret edit spoils
  // the match.
  function tieNext(ordinal) {
    const el = noteEls[ordinal];
    if (!el || isRest(el) || hasChord(el)) return null;
    const voice = voiceNumber(el);
    const onset = onsetByEl.get(el);
    const dur = intDuration(el);
    const measureEl = el.closest ? el.closest("measure") : null;
    if (voice == null || onset == null || !measureEl) return null;
    for (let i = ordinal + 1; i < noteEls.length; i++) {
      const cand = noteEls[i];
      // Skip a chord member (it shares its head's onset); the head is what a tie
      // would attach to, and it precedes its members in document order.
      if (hasChord(cand)) continue;
      if (voiceNumber(cand) !== voice) continue;
      const candMeasure = cand.closest ? cand.closest("measure") : null;
      const candOnset = onsetByEl.get(cand);
      if (candMeasure === measureEl) {
        // Same measure: the partner must start exactly where this note ends. A
        // non-contiguous next note means a gap (a rest) sits between them, which
        // a tie must not span.
        return candOnset === onset + dur ? { el: cand, ordinal: i } : null;
      }
      // A later measure: only the first beat of it, and only when this note runs
      // to its own measure's end, is contiguous across the barline.
      if (candOnset === 0 && onset + dur === measureDurByEl.get(measureEl)) {
        return { el: cand, ordinal: i };
      }
      return null;
    }
    return null;
  }

  // Write (or ensure) the <tie> sound element and <tied> notation of `type`
  // ("start"/"stop") on a note. <tie> sits after <duration> (its schema home,
  // before <voice>); <tied> goes in <notations> (created if absent), before the
  // <technical> that carries string/fret.
  function writeTie(el, type) {
    if (!firstTie(el, type)) {
      const tie = doc.createElement("tie");
      tie.setAttribute("type", type);
      const durationEl = firstChildTag(el, "duration");
      if (durationEl) durationEl.insertAdjacentElement?.("afterend", tie) ?? el.appendChild(tie);
      else el.appendChild(tie);
    }
    let notations = firstChildTag(el, "notations");
    if (!notations) {
      notations = doc.createElement("notations");
      el.appendChild(notations);
    }
    const already = [...notations.children].some((c) => c.tagName === "tied" && c.getAttribute("type") === type);
    if (!already) {
      const tied = doc.createElement("tied");
      tied.setAttribute("type", type);
      notations.insertBefore(tied, notations.firstChild);
    }
  }

  // Break any tie on the note at `ordinal` whose two ends are no longer the SAME
  // FRETBOARD POSITION (#189). setTie ties one fretted position (same string AND
  // fret - see its contract); a fret or string edit MOVES the note to another
  // position, so a tie it was part of would now span two positions - invalid, and
  // the measured renderer trap alphaTab draws wrong (a tie-STOP note gets a fret
  // derived from its start, so the written stop and the drawn one disagree).
  // Rather than leave that broken tie, the edit drops it on BOTH ends,
  // structurally (by contiguity, like setTie's removal), so the note simply stops
  // being tied once it moves. Called from setFret/setString - the two ops that
  // change position; the respell paths keep string+fret fixed and cannot break a
  // tie.
  function samePosition(a, b) {
    return a.string != null && a.string === b.string && a.fret === b.fret;
  }
  function dropBrokenTies(ordinal) {
    const el = noteEls[ordinal];
    if (!el) return;
    const mine = describe(el, ordinal);
    // Forward: this note starts a tie into its contiguous same-voice successor.
    if (hasTie(el, "start")) {
      const partner = tieNext(ordinal);
      if (!partner || !samePosition(mine, describe(partner.el, partner.ordinal))) {
        removeTie(el, "start");
        if (partner) removeTie(partner.el, "stop");
      }
    }
    // Backward: this note stops a tie from its contiguous same-voice predecessor.
    // The partner is the immediately-preceding non-chord note in the same voice;
    // confirm it actually ties here (its tieNext is this note) before breaking.
    if (hasTie(el, "stop")) {
      const voice = voiceNumber(el);
      for (let i = ordinal - 1; i >= 0; i--) {
        const cand = noteEls[i];
        if (hasChord(cand)) continue;
        if (voiceNumber(cand) !== voice) continue;
        if (hasTie(cand, "start")) {
          const pn = tieNext(i);
          if (pn && pn.ordinal === ordinal && !samePosition(mine, describe(cand, i))) {
            removeTie(cand, "start");
            removeTie(el, "stop");
          }
        }
        break; // the contiguous predecessor is the only candidate
      }
    }
  }

  // Remove the <tie> sound element and <tied> notation of `type` from a note.
  function removeTie(el, type) {
    const tie = firstTie(el, type);
    if (tie) el.removeChild(tie);
    const notations = firstChildTag(el, "notations");
    if (notations) {
      for (const c of [...notations.children]) {
        if (c.tagName === "tied" && c.getAttribute("type") === type) notations.removeChild(c);
      }
    }
  }

  /**
   * Tie the sounding note at `ordinal` to the next note (`on` true), or remove
   * that tie (`on` false). Returns true when the document changed (#183).
   *
   * The decisions, stated so a reader need not infer them:
   * - A tie joins THIS note to the NEXT one. "Next" is the next sounding note in
   *   the same voice that is contiguous with this one - the following beat in the
   *   measure, or the first beat of the next measure when this note reaches the
   *   barline. A gap (a rest) between them, or the next note being in another
   *   voice, means there is nothing to tie to and the request is refused.
   * - The two notes must be the SAME FRETBOARD POSITION - the same <string> AND
   *   <fret>, not merely the same sounding pitch. A tie sustains one fretted
   *   note; on a fretboard that is one string held at one fret, so two positions
   *   that only happen to SOUND alike (E4 as string 1 fret 0 and as string 2 fret
   *   5) would still have to be re-fretted, which a tie cannot express. It is
   *   also a measured renderer trap: alphaTab draws a tie-STOP note at a fret
   *   derived from its tie-START partner, so a tie across two positions renders
   *   one pitch while the document holds the other (the whole-model fuzz guard's
   *   find, #189). Same string+fret implies same pitch, so this is the stricter,
   *   correct reading of "the same note". A mismatch is refused, untouched.
   * - Chord beats are not offered. A tie on a chord member (or from a chord) would
   *   have to pair every voice of the chord; this increment refuses it.
   * - It can be removed. Toggling a started tie off drops both the start on this
   *   note and the stop on its partner, found structurally (by the tie, not by
   *   re-matching the pitch), so a later fret edit cannot orphan the stop.
   */
  function setTie(ordinal, on) {
    const el = noteEls[ordinal];
    if (!el || isRest(el) || hasChord(el)) return false;
    const startsHere = hasTie(el, "start");
    if (on) {
      if (startsHere) return false; // already tied to the next note - a no-op
      const next = tieNext(ordinal);
      if (!next) return false;
      const here = describe(el, ordinal);
      const there = describe(next.el, next.ordinal);
      if (here.string == null || here.fret == null) return false;
      if (here.string !== there.string || here.fret !== there.fret) return false;
      writeTie(el, "start");
      writeTie(next.el, "stop");
      return true;
    }
    if (!startsHere) return false; // nothing to remove
    const next = tieNext(ordinal);
    removeTie(el, "start");
    // Remove the partner's stop too. tieNext finds it structurally (contiguity,
    // not pitch), so an edit that changed the note's pitch after it was tied
    // still lets the pair be untied cleanly.
    if (next) removeTie(next.el, "stop");
    return true;
  }

  /**
   * Delete a sounding note by turning it into a rest (#186's Backspace) - the
   * model's own notion of delete. This file's header pins rests as the notes
   * that "keep their place in document order" and are "not offered as edit
   * targets"; a deleted note becomes exactly that. Its `<duration>`, `<voice>`
   * and `<type>` are kept and a `<rest/>` put where its `<pitch>` was, so the
   * bar's arithmetic is unchanged - the note becomes the same length of silence
   * in the same place, rather than the note being removed and the bar left
   * short. On re-parse it drops out of `noteEls` (isRest filters it), so the
   * ordinals after it close up by one - the caller rebuilds the model from the
   * new text (as undo's restore does) rather than trust this in-place array.
   * Returns true when the document changed.
   */
  function deleteNote(ordinal) {
    const el = noteEls[ordinal];
    if (!el || isRest(el)) return false;
    // <rest> stands where <pitch>/<unpitched>/<rest> go in the schema: first,
    // before <duration>. Drop the sounding-note-only children (<pitch>, the
    // <notations> that carried <technical> string/fret, and any <accidental>)
    // and insert <rest/> at the front.
    for (const tag of ["pitch", "notations", "accidental"]) {
      const child = firstChildTag(el, tag);
      if (child) el.removeChild(child);
    }
    const rest = doc.createElement("rest");
    el.insertBefore(rest, el.firstChild);
    return true;
  }

  /**
   * Turn the rest at `restOrdinal` (an index into restEls, document order -
   * see restAt) into a sounding note on `string` at `fret` (#238) - the
   * inverse of deleteNote. Its `<duration>`, `<voice>`, `<type>` and any
   * `<dot>` elements are left exactly as they are - nothing here touches a
   * duration - only the `<rest/>` is replaced with a `<pitch>` and a
   * `<notations><technical>` carrying the new string/fret, so the bar's
   * arithmetic (deleteNote's own concern, mirrored) is unchanged by
   * construction.
   *
   * Refuses (leaving the rest untouched, returning null) exactly where
   * setFret/setString would refuse a position: an out-of-range string, a
   * negative fret, or a fret whose resulting pitch has no valid <octave>
   * (Rule 11). The pitch is spelled against the key and the bar in force at
   * the rest's own measure through the same spellForEdit/writePitch a fret or
   * string edit uses, so a note born from a rest is spelled exactly as if it
   * had always been a note there and its fret were just set.
   *
   * Structural like a delete or a voice move: it changes which array index is
   * which sounding note (every ordinal at or after the new note's position
   * shifts up by one), so the caller cannot trust noteEls/restEls/allEls in
   * place afterwards - it rebuilds the model from the new text, the same
   * convention deleteNote's and moveToVoice's own docstrings state. Returns
   * the note's ordinal in the CURRENT (already-mutated) document - found the
   * same way moveToVoice finds its new ordinal, by re-deriving the
   * sounding-note list from the live DOM rather than trusting noteEls - or
   * null when the edit was refused.
   */
  function restToNote(restOrdinal, string, fret) {
    const el = restEls[restOrdinal];
    if (!el || !isRest(el)) return null;
    if (!Number.isInteger(string) || string < 1 || string > stringCount) return null;
    if (!Number.isInteger(fret) || fret < 0) return null;
    const midi = midiForStringFret(tuningByLine, stringCount, string, fret);
    if (midi == null || !isWritablePitch(midi)) return null;

    const restEl = firstChildTag(el, "rest");
    if (restEl) el.removeChild(restEl);

    // <notations><technical><string>/<fret> is schema-last on this profile
    // (voice, type, dot*, accidental, notations - see musicxml.py's own
    // comment); appended here, BEFORE writePitch, it gives writeAccidental an
    // anchor to insert an <accidental> before, keeping that same element
    // order rather than appending the accidental after the notations it
    // belongs before.
    const notations = doc.createElement("notations");
    const technical = doc.createElement("technical");
    const stringEl = doc.createElement("string");
    stringEl.textContent = String(string);
    const fretEl = doc.createElement("fret");
    fretEl.textContent = String(fret);
    technical.appendChild(stringEl);
    technical.appendChild(fretEl);
    notations.appendChild(technical);
    el.appendChild(notations);

    // Puts <pitch> at the front - exactly where <rest/> was, since putPitch
    // inserts before el.firstChild when no <pitch> exists yet - and writes
    // the printed <accidental> the key/bar call for, if any.
    writePitch(el, midi);

    const nowSounding = [...doc.getElementsByTagName("note")].filter((n) => !isRest(n));
    const newOrdinal = nowSounding.indexOf(el);
    return newOrdinal >= 0 ? newOrdinal : null;
  }

  // ------------------------------------------------ move a note to another voice (#182)
  //
  // Polyphonic tab is written one voice at a time, each voice after the first
  // preceded by a <backup> that rewinds the cursor to the measure start (Rule
  // 6), and every voice's notes and rests summing to the measure (Rule 8).
  // Reassigning a note to another voice is therefore not a <voice> text swap: it
  // moves the note out of one per-measure timeline and into another at the SAME
  // onset, and both timelines have to stay full. The chosen mechanism rebuilds
  // the measure's whole note stream from its voices' timelines, which keeps the
  // backup arithmetic, Rule 8 and the onsets correct by construction rather than
  // by patching. See moveToVoice for the per-case decisions.

  // The plain type for a rest of `dur` divisions, or null when no undotted type
  // expresses it exactly (a dotted-length gap) - in which case the rest is
  // written with <duration> alone, which is valid and renders by its duration.
  function typeForDuration(dur) {
    for (const t of DURATION_TYPES) if (durationForType(t, divisions) === dur) return t;
    return null;
  }

  // A fresh rest <note> filling `dur` divisions of silence in `voiceNum`. The
  // schema's order for a rest note is (rest), duration, voice, type - the same
  // order the emitter writes and the one this keeps.
  function makeRest(dur, voiceNum) {
    const note = doc.createElement("note");
    note.appendChild(doc.createElement("rest"));
    const d = doc.createElement("duration");
    d.textContent = String(dur);
    note.appendChild(d);
    const v = doc.createElement("voice");
    v.textContent = String(voiceNum);
    note.appendChild(v);
    const type = typeForDuration(dur);
    if (type) {
      const typeEl = doc.createElement("type");
      typeEl.textContent = type;
      note.appendChild(typeEl);
    }
    return note;
  }

  // Set a note's <voice> (Rule 6), creating it in its schema position (before
  // <type>) if the note somehow lacked one.
  function setVoiceEl(el, voiceNum) {
    let v = firstChildTag(el, "voice");
    if (!v) {
      v = doc.createElement("voice");
      const typeEl = firstChildTag(el, "type");
      if (typeEl) el.insertBefore(v, typeEl);
      else el.appendChild(v);
    }
    v.textContent = String(voiceNum);
  }

  // Add or drop a note's leading <chord/> (Rule 7): the first note of a beat
  // carries none and every later member carries one, so a beat's noteheads are
  // normalised by their position when the beat is written.
  function setChordFlag(el, on) {
    const existing = firstChildTag(el, "chord");
    if (on && !existing) el.insertBefore(doc.createElement("chord"), el.firstChild);
    if (!on && existing) el.removeChild(existing);
  }

  // Re-derive every note id in a measure from its position (Rule 17:
  // n{measure}-{voice}-{onset}-{chord}, onset the 0-based beat index within the
  // voice, chord the 0-based member index within the beat). Ids name a POSITION,
  // not a note, so a structural edit that moves a note between voices has to
  // recompute them for the whole measure - the profile makes this the editor's
  // responsibility, not the emitter's.
  function renumberMeasure(measureEl) {
    const mnum = Number(measureEl.getAttribute("number"));
    if (!Number.isFinite(mnum)) return;
    const beatIdx = new Map();
    const chordIdx = new Map();
    for (const note of measureEl.getElementsByTagName("note")) {
      const v = voiceNumber(note);
      if (v == null) continue;
      if (hasChord(note) && beatIdx.has(v)) {
        chordIdx.set(v, (chordIdx.get(v) ?? 0) + 1);
      } else {
        beatIdx.set(v, beatIdx.has(v) ? beatIdx.get(v) + 1 : 0);
        chordIdx.set(v, 0);
      }
      note.setAttribute("id", `n${mnum}-${v}-${beatIdx.get(v)}-${chordIdx.get(v)}`);
    }
  }

  /**
   * Move the sounding note at `ordinal` into voice `targetVoice`, keeping its
   * onset. Returns the note's NEW ordinal (its voice block now sits after its
   * old one, so the ordinal generally changes) or null when the move is refused
   * or a no-op. The document is left untouched on a refusal.
   *
   * The decisions this makes, each stated so a reader need not reverse-engineer
   * them from the arithmetic:
   *
   * - Onset is preserved. The note lands at the SAME onset in the new voice - a
   *   note on beat 3 of voice 1 is on beat 3 of voice 2, not beat 1 - because
   *   the whole measure is rebuilt from per-voice timelines and the note keeps
   *   the onset its beat had.
   * - A lone note leaves a rest behind. Removing it from its source voice would
   *   leave a gap; that gap is filled with a rest of the same length, so the
   *   source voice still spans the measure (Rule 8) and every later note keeps
   *   its onset. The alternative - collapsing the gap - would move every note
   *   after it earlier, which is not what "move THIS note" means.
   * - A chord member splits off. Moving one notehead of a chord leaves the other
   *   members sounding at that onset (no rest is inserted - the onset is still
   *   occupied), and the moved note becomes a lone note at the same onset in the
   *   target voice. If the moved note was the chord's written head, the next
   *   member is promoted to head (its <chord/> dropped) so the source beat still
   *   advances the cursor.
   * - A new target voice is created full. When the target voice does not yet
   *   exist in the measure it is introduced with its <backup> and filled with
   *   rests around the moved note, so it too spans the measure. Voices stay
   *   numbered consecutively from 1 (Rule 6): a target more than one past the
   *   highest existing voice is refused.
   * - An occupied target onset is refused. If the target voice already sounds
   *   across the moved note's onset, the move is refused rather than guessing a
   *   merge - the note is left where it was.
   * - Inferred silence is not disturbed. A measure carrying a <forward> (Rule 14
   *   deduced silence, which must not become a counted rest) is refused, since
   *   the rest-filled rebuild cannot reproduce it faithfully.
   */
  function moveToVoice(ordinal, targetVoice) {
    const mv = noteEls[ordinal];
    if (!mv || isRest(mv)) return null;
    if (!Number.isInteger(targetVoice) || targetVoice < 1) return null;
    const measureEl = mv.closest ? mv.closest("measure") : null;
    if (!measureEl) return null;
    const srcVoice = voiceNumber(mv);
    if (srcVoice == null || targetVoice === srcVoice) return null;
    // Rule 14 inferred silence would be silently promoted to a counted rest by
    // the rebuild below - refuse rather than corrupt the measure's arithmetic.
    if (measureEl.getElementsByTagName("forward").length > 0) return null;

    // The measure's note stream - its <note> and <backup> children, in order.
    // A <direction>/<barline>/<print> before the first or after the last is left
    // untouched; one INTERLEAVED between notes is refused, because the rebuild
    // would not know where to put it back (a mid-voice mark is rare and this
    // increment does not move it).
    const kids = [...measureEl.children];
    const streamIdx = [];
    for (let i = 0; i < kids.length; i++) {
      const t = kids[i].tagName;
      if (t === "note" || t === "backup") streamIdx.push(i);
    }
    if (streamIdx.length === 0) return null;
    const first = streamIdx[0];
    const last = streamIdx[streamIdx.length - 1];
    for (let i = first + 1; i < last; i++) {
      const t = kids[i].tagName;
      if (t !== "note" && t !== "backup") return null;
    }

    // Walk the stream into per-voice sounding beats and the measure's duration.
    const soundingByVoice = new Map(); // vnum -> [{ onset, duration, notes: [el] }]
    let cursor = 0;
    let measureDur = 0;
    let lastBeat = null;
    for (let i = first; i <= last; i++) {
      const el = kids[i];
      if (el.tagName === "backup") {
        cursor -= intDuration(el);
        lastBeat = null;
        continue;
      }
      const rest = isRest(el);
      const dur = intDuration(el);
      const v = voiceNumber(el);
      if (hasChord(el) && lastBeat) {
        if (!rest) lastBeat.notes.push(el);
      } else {
        const beat = { onset: cursor, duration: dur, notes: rest ? [] : [el] };
        if (!rest && v != null) {
          if (!soundingByVoice.has(v)) soundingByVoice.set(v, []);
          soundingByVoice.get(v).push(beat);
        }
        cursor += dur;
        if (cursor > measureDur) measureDur = cursor;
        lastBeat = rest ? null : beat;
      }
    }
    if (!(measureDur > 0)) return null;

    // The moved note's beat in its source voice.
    const srcBeats = soundingByVoice.get(srcVoice) || [];
    const mvBeat = srcBeats.find((b) => b.notes.includes(mv));
    if (!mvBeat) return null;
    const onset = mvBeat.onset;
    const duration = mvBeat.duration;

    // Consecutive voice numbering (Rule 6): the target is at most one past the
    // highest voice that sounds today.
    let maxVoice = 0;
    for (const v of soundingByVoice.keys()) if (v > maxVoice) maxVoice = v;
    if (targetVoice > maxVoice + 1) return null;

    // The target must be silent across the moved note's onset.
    const tgtBeats = soundingByVoice.get(targetVoice) || [];
    for (const b of tgtBeats) {
      if (onset < b.onset + b.duration && b.onset < onset + duration) return null;
    }

    // Remove the note from its source beat; a beat left empty becomes silence
    // (refilled as a rest below), a chord simply loses one member.
    mvBeat.notes = mvBeat.notes.filter((n) => n !== mv);
    if (mvBeat.notes.length === 0) {
      soundingByVoice.set(
        srcVoice,
        srcBeats.filter((b) => b !== mvBeat),
      );
    }

    // Add it to the target voice as a lone note at the same onset.
    setChordFlag(mv, false);
    tgtBeats.push({ onset, duration, notes: [mv] });
    tgtBeats.sort((a, b) => a.onset - b.onset);
    soundingByVoice.set(targetVoice, tgtBeats);

    // Rebuild the whole note stream: every voice 1..max, each a full timeline of
    // its sounding beats with the gaps between them (and before/after) filled by
    // rests, and a <backup> to the measure start before every voice after the
    // first.
    const maxAfter = Math.max(maxVoice, targetVoice);
    const newSeq = [];
    for (let v = 1; v <= maxAfter; v++) {
      if (v > 1) {
        const backup = doc.createElement("backup");
        const bd = doc.createElement("duration");
        bd.textContent = String(measureDur);
        backup.appendChild(bd);
        newSeq.push(backup);
      }
      const beats = (soundingByVoice.get(v) || []).slice().sort((a, b) => a.onset - b.onset);
      let pos = 0;
      for (const beat of beats) {
        if (beat.onset > pos) newSeq.push(makeRest(beat.onset - pos, v));
        else if (beat.onset < pos) return null; // overlap - would corrupt the bar
        beat.notes.forEach((n, idx) => {
          setVoiceEl(n, v);
          setChordFlag(n, idx > 0);
        });
        for (const n of beat.notes) newSeq.push(n);
        pos = beat.onset + beat.duration;
      }
      if (pos < measureDur) newSeq.push(makeRest(measureDur - pos, v));
    }

    // Splice the rebuilt stream in where the old one was; leading/trailing
    // non-stream siblings keep their place around it. The reused sounding notes
    // are re-parented (detached then re-inserted) - insertBefore moves them.
    const anchor = kids[last].nextSibling;
    for (let i = first; i <= last; i++) measureEl.removeChild(kids[i]);
    for (const el of newSeq) measureEl.insertBefore(el, anchor);

    renumberMeasure(measureEl);

    const nowSounding = [...doc.getElementsByTagName("note")].filter((n) => !isRest(n));
    const newOrdinal = nowSounding.indexOf(mv);
    return newOrdinal >= 0 ? newOrdinal : null;
  }

  function text() {
    const body = new XMLSerializer().serializeToString(root);
    // DOMParser drops the XML declaration; the server sniffs "starts with <"
    // (a declaration does), and alphaTab reads either way - but a well-formed
    // MusicXML file the user might export deserves its declaration back.
    return `<?xml version="1.0" encoding="UTF-8"?>\n${body}`;
  }

  return {
    stringCount,
    divisions,
    fifths: documentFifths,
    soundingNotes,
    noteAt,
    count: () => noteEls.length,
    stepNote,
    stepMeasure,
    // Rest selection and rest-to-note (#238) - see restAt/restToNote/stepAny.
    restCount,
    restAt,
    stepAny,
    restToNote,
    setFret,
    setString,
    setAccidental,
    cycleSpelling,
    setDurationType,
    setDots,
    setTie,
    deleteNote,
    moveToVoice,
    text,
  };
}
