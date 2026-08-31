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
  durationForType,
  isWritablePitch,
  midiForStringFret,
  midiOfPitch,
  pitchFromMidi,
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

  // The sounding notes, in document order (measure -> voice's onset -> chord
  // member), each paired with its <note> element. This is exactly the order
  // score-render.js walks the alphaTab model in to build the positional map,
  // so an index into this array IS the "ordinal" the two sides agree on. Rests
  // are skipped by both, consistently.
  const noteEls = [...doc.getElementsByTagName("note")].filter((n) => !isRest(n));

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

  function describe(el, ordinal) {
    const tech = technicalOf(el);
    const string = tech ? Number(tagText(tech, "string")) : null;
    const fret = tech ? Number(tagText(tech, "fret")) : null;
    const type = tagText(el, "type");
    const dots = [...el.children].filter((c) => c.tagName === "dot").length;
    const pitchEl = firstChildTag(el, "pitch");
    const midi = pitchEl
      ? midiOfPitch(tagText(pitchEl, "step"), Number(tagText(pitchEl, "octave")), Number(tagText(pitchEl, "alter") ?? 0))
      : null;
    return {
      ordinal,
      id: el.getAttribute("id"),
      string: Number.isFinite(string) ? string : null,
      fret: Number.isFinite(fret) ? fret : null,
      type,
      dots,
      midi,
      measure: measureNums[ordinal] ?? null,
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

  // Rewrite a sounding note's <pitch> to match a MIDI number, keeping the
  // schema's child order (step, alter?, octave). Called whenever a fret or a
  // string changes, so Rule 10's <pitch> never disagrees with the Rule 9
  // position that determines it.
  function writePitch(el, midi) {
    const spelled = pitchFromMidi(midi);
    if (!spelled) return;
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
    soundingNotes,
    noteAt,
    count: () => noteEls.length,
    stepNote,
    stepMeasure,
    setFret,
    setString,
    setDurationType,
    deleteNote,
    text,
  };
}
