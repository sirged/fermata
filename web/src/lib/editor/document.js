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

// A chord member carries <chord/> as its first child (Rule 7); only the first
// note of a chord advances the measure's time cursor.
function hasChord(noteEl) {
  return !!firstChildTag(noteEl, "chord");
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
  for (const measureEl of doc.getElementsByTagName("measure")) {
    let cursor = 0;
    let headOnset = 0;
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
      }
    }
  }

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
      measure: measureNums[ordinal] ?? null,
      // The note's own voice, its onset within that voice (both #182's proof
      // that a moved note keeps its onset), and how many voices its measure
      // already sounds (what the move control offers as targets).
      voice: voiceNumber(el),
      onset: onsetByEl.has(el) ? onsetByEl.get(el) : null,
      measureVoices: voices ? voices.size : null,
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
    soundingNotes,
    noteAt,
    count: () => noteEls.length,
    stepNote,
    stepMeasure,
    setFret,
    setString,
    setDurationType,
    deleteNote,
    moveToVoice,
    text,
  };
}
