<script>
  import { untrack } from "svelte";
  import { api } from "./api.js";
  import { createScoreView, UNRENDERABLE_MESSAGE, tabWithheldMessage } from "./score-render.js";
  import { getSettings, setSetting, STAFF_THEMES, STAFF_THEME_LABELS } from "./settings.svelte.js";
  import Metronome from "./Metronome.svelte";
  import { createDocument, DURATION_TYPES } from "./editor/document.js";

  const settings = getSettings();

  // Changing the theme here just writes the same setting the settings view
  // writes - it's the same store, so the choice still persists and follows
  // the user. What this buys over navigating to #/settings: App.svelte
  // routes with {#if}, so leaving this view for that one and coming back
  // would unmount and remount this component, rebuilding the renderer from
  // scratch - losing scroll position and stopping playback. A theme is
  // exactly the kind of thing you want to change mid-practice (a room going
  // dark, a stage light coming up), so it needs a way to change without
  // navigating away.
  function chooseTheme(ev) {
    setSetting("staff_theme", ev.target.value).catch(() => {
      // setSetting already rolled the optimistic value back (and the select
      // is bound to it), so the control itself already shows the truth
    });
  }

  let {
    score = null,
    demo = false,
    tex = null,
    // Which format `tex` holds. Transcriptions are stored as MusicXML, but a
    // row written before that change - or hand-edited in alphaTex - carries
    // its own format, so this is read from the row rather than assumed.
    format = "alphatex",
    gigMode = false,
    onToggleGig = () => {},
    practiceLabel = null,
    onStopPractice = () => {},
    // Whether THIS instance's single-key shortcuts (#92) should respond to
    // the keyboard at all. Defaults true, which is right everywhere this
    // component is mounted on its own (Viewer.svelte's plain notation/tab
    // path). ScoreCompare mounts a TabViewer and a PdfViewer at once and
    // only hides the one not on screen with CSS, not by unmounting it - see
    // ScoreCompare's own snippets - and PdfViewer already owns Space and the
    // plain arrow keys for turning pages. Left both listening unconditionally,
    // a single Space press in side-by-side or PDF-only layout would BOTH
    // toggle playback here and turn the page there - exactly the "does the
    // shortcut set stay sane" question #92 asks about gig mode, where a
    // pedal sends nothing but arrow keys. ScoreCompare passes this only
    // `true` while its staff pane is the one actually on screen - NOT in
    // "side" layout (two panes visible at once), which is deliberately left
    // to the PDF pane's own page-turn keys instead: those predate #92 (issue
    // #106's own gig-mode-pedal reasoning) and "side" is the default the
    // moment a score has a transcription at all, so ceding it here is what
    // keeps arrow keys turning pages in side-by-side the way they always
    // have on main - see the comment on PdfViewer's `active` prop in
    // ScoreCompare for the other half of this.
    active = true,
    // Whether this score can be edited note-by-note and saved (#10). Set by
    // ScoreCompare when a transcription is loaded and there is a score id to
    // save under; false for the demo and any read-only mount. Note editing
    // only works on a MusicXML document (that is the model the edits are
    // written to), so the affordance also depends on `format` below.
    editable = false,
    // Persist the edited MusicXML - `async (content) => void`. ScoreCompare
    // wires this to the existing edited-transcription save path (PUT
    // /api/scores/{id}/transcription, stored verbatim as source='edited',
    // never re-extracted). null when not editable.
    onSaveEdit = null,
  } = $props();

  let host;
  let scroller;
  let view = $state(null);
  let profile = $state("scoretab");
  // Which profile buttons are worth showing at all - null while unknown (no
  // score has finished loading yet), otherwise whatever score-render.js
  // found this score can actually be drawn with - possibly empty. null is
  // deliberately not "everything": a `score` prop loads over an async fetch,
  // so there is a real window between mount and the load actually landing
  // during which every button being live would let a click on "Tab" go
  // through for a notation-only file before its content is even known - and
  // then get silently walked back (the button vanishing out from under the
  // pointer) the moment the fetch resolves and corrects it. Starting at null
  // and rendering no buttons at all during that window is what avoids
  // offering a choice this component cannot yet vouch for.
  let profileOptions = $state(null);
  // How many staves score-render.js's disqualifyUnstrungTabStaves() turned
  // tablature off for on the current score (issue #165) - 0 for the ordinary
  // case. Read only where profileOptions.length === 0, to choose
  // tabWithheldMessage() over UNRENDERABLE_MESSAGE: a score that had a TAB
  // clef and mostly-fretted notes but lost its only drawable staff to one bad
  // note must not be told it never had notation or tablature at all.
  let tabWithheldCount = $state(0);
  let playing = $state(false);
  let playerReady = $state(false);
  let speed = $state(1);
  let looping = $state(false);
  // Whether the click is on. Owned here rather than inside Metronome.svelte
  // because gig mode strips the toolbar - and its metronome controls - while
  // still having to show the readout, so this component needs the answer too.
  // Bound to the component below; nothing else here sets it.
  //
  // None of the metronome's settings are server-persisted, deliberately, and
  // none of them are kept here either: they live in Metronome.svelte, which
  // re-pushes them whenever `view` is replaced. Every other transport control
  // here (speed, looping, count-in) is per-session state that resets with a
  // fresh load, and a click's tempo is exactly as tied to "the passage being
  // worked on right now" as those are.
  let metronome = $state(false);
  // The value the audio layer reports it is actually clicking at right now -
  // null until a score has loaded and it has one to report. This is read
  // back FROM the audio layer (see onMetronomeTempo below), not computed
  // here a second time: showing a value this component derived itself would
  // stay green through a bug in what the audio layer actually does with it.
  let metronomeTempo = $state(null);
  // "slowest"/"fastest" when the click's countable-range clamp - rather than
  // the chosen percentage - is what decided that rate, otherwise null. Passed
  // down so the control can say why the two have stopped agreeing.
  let metronomeLimit = $state(null);
  // The metronome's three visible settings, owned here rather than inside
  // Metronome.svelte for one reason: gig mode unmounts the whole toolbar, and
  // a setting that lived in the component would silently revert to its default
  // on the way back out. null means "not chosen yet" - the component fills each
  // one in from its own pre-fill chain and hands it back. Still not persisted
  // anywhere: see the note on `metronome` above.
  let metronomeMode = $state(null);
  let metronomeProportion = $state(null);
  let metronomeBpm = $state(null);
  // The tempo the percentages are percentages OF - see Metronome.svelte's
  // baseTempoLabel - and where it came from: "start", "later" or "none" (see
  // tempoProvenance in score-render.js). Two values, because `score.tempo`
  // alone cannot tell a declared tempo from the renderer's 120 fallback, which
  // is how a score printing no tempo came to be described as "marked ♩ = 120"
  // (issue #102). "none" until a score says otherwise: the number is
  // meaningless before then, and "not yet loaded" must not read as "declared".
  let scoreTempo = $state(null);
  let scoreTempoFrom = $state("none");
  let countIn = $state(false);
  let loadError = $state("");
  let ladder = $state(false);
  let ladderStart = $state(60);
  let ladderStep = $state(5);
  let ladderTarget = $state(100);

  // ---------------------------------------------------------- the note editor (#10)
  //
  // The document is the source of truth. Every edit is written to `doc` (a
  // MusicXML document model - see editor/document.js), and the alphaTab view is
  // then handed the new document text and asked to redraw it. Nothing is
  // written to the renderer's object graph and left for the document to catch
  // up with; the screen is always a fresh view of the document, which is what
  // makes the two impossible to diverge (the renderer evaluation on #10 names
  // that divergence as the one bug class this design must not create).
  //
  // `doc` is a plain (non-reactive) handle: it is mutated in place and its text
  // re-imported, and nothing renders FROM it directly - the derived, reactive
  // facts a reader sees (the selected note's fret, string, duration) are pulled
  // out into the $state below on each selection.
  let editMode = $state(false);
  let doc = null;
  let editStringCount = $state(6);
  let selectedOrdinal = $state(null);
  // A selected REST (#238), mutually exclusive with selectedOrdinal - at most
  // one of the two is ever non-null. `doc.restAt`'s own shape: { restOrdinal,
  // id, type, dots, duration, voice, onset, measure }. A rest has no note-head
  // bounds in the renderer (score-render.js's positional map skips rests, and
  // stays skipping them - see document.js's stepAny), so a rest selection
  // never sets `overlay`; the edit panel shows it as text instead.
  let selectedRest = $state(null);
  // The string offered for a rest-to-note conversion, editable in the panel
  // while a rest is selected. Initialised from lastEditString (the string the
  // player was last actually fretting) or the rest's voice's own default
  // (defaultStringForVoice) when there is none yet this session (#238's
  // "selected, or the rest's voice's default" string).
  let selRestString = $state(null);
  // The last string a real fret/string edit actually used, plain
  // (non-reactive) like fretEntry - nothing renders from it directly, it only
  // seeds selRestString the next time a rest is selected. null until the
  // first fret/string edit or rest conversion this session.
  let lastEditString = null;
  let selFret = $state(null);
  let selString = $state(null);
  let selType = $state(null);
  // The selected note's augmentation dots (0/1/2) and whether it is tied into
  // the next note (#183). Both are read back from the document on each selection
  // and drive the Dots select and the Tie toggle below.
  let selDots = $state(0);
  let selTieStart = $state(false);
  let selTieStop = $state(false);
  let selMidi = $state(null);
  // The selected note's spelling (#185): the letter, its alteration, and the
  // printed accidental (or null). Same sounding pitch (selMidi) however these
  // read - the Accidental control and the enharmonic-cycle button change them
  // without touching the sound.
  let selStep = $state(null);
  let selAlter = $state(null);
  let selAccidental = $state(null);
  let selNoteId = $state(null);
  // The selected note's voice, its onset within that voice (in the document's
  // divisions), and the voices its move control offers (#182). selRenderVoice is
  // the SAME fact read back from the renderer, the voice half of the divergence
  // cross-check below.
  let selVoice = $state(null);
  let selOnset = $state(null);
  let selRenderVoice = $state(null);
  let selVoiceOptions = $state([]);
  // The cross-check the evaluation asks for: the renderer's own read of the
  // selected note (through the importer and the positional map) against the
  // document's read of the same note. In this single-source-of-truth design
  // they must always agree; when they do not, the positional map, the string
  // mirror or the pitch recompute is wrong, and this goes false loudly.
  let divergenceOk = $state(true);
  let editError = $state("");
  // A transient "that edit was refused" message - distinct from editError,
  // which is a load-time parse failure that replaces the whole panel. A refused
  // edit leaves the fields in place so the value can be corrected, so its
  // message sits beside the actions instead.
  let editWarn = $state("");
  let dirty = $state(false);
  let saving = $state(false);
  let saveError = $state("");
  let undoStack = $state([]);
  let redoStack = $state([]);
  let overlay = $state(null);

  // The keyboard core loop's two-digit fret entry (#186). Typing a digit with a
  // note selected sets its fret immediately (so "1" then a pause commits fret
  // 1); a second digit that follows within TWO_DIGIT_MS on the SAME note
  // extends it to a two-digit fret ("1" then a quick "2" = fret 12). Plain
  // (non-reactive) state - nothing renders from it, it only remembers the last
  // digit long enough to decide extend-vs-fresh at the next keypress, decided
  // by a timestamp compare rather than a timer that could outlive the note.
  // null between entries.
  const TWO_DIGIT_MS = 600;
  let fretEntry = null;

  function canEditNotes() {
    return editable && format === "musicxml" && tex != null;
  }

  function initEditDoc(text) {
    editError = "";
    const source = text ?? tex;
    try {
      doc = createDocument(source);
      editStringCount = doc.stringCount;
      return true;
    } catch (e) {
      doc = null;
      editError = String(e?.message ?? e);
      return false;
    }
  }

  function clearSelection() {
    selectedOrdinal = null;
    selectedRest = null;
    selRestString = null;
    selFret = selString = selType = selMidi = selNoteId = null;
    selStep = selAlter = selAccidental = null;
    selDots = 0;
    selTieStart = selTieStop = false;
    selVoice = selOnset = selRenderVoice = null;
    selVoiceOptions = [];
    divergenceOk = true;
    overlay = null;
    editWarn = "";
    // A fresh selection (or none) starts a fresh two-digit fret window - a
    // digit typed on a new note must never extend the last note's fret.
    fretEntry = null;
  }

  function enterEdit() {
    if (!canEditNotes()) return;
    if (!initEditDoc()) return;
    undoStack = [];
    redoStack = [];
    dirty = false;
    saveError = "";
    clearSelection();
    editMode = true;
    // notation + doc are (re)applied by the $effect below, which also covers a
    // rebuild of the view underneath us after a save.
  }

  function exitEdit() {
    editMode = false;
    clearSelection();
    doc = null;
    editError = "";
    view?.editor?.setNotationShown(false);
    if (typeof window !== "undefined") {
      window.__scoreEditor = null;
      window.__scoreEditorHarness = null;
    }
  }

  function toggleEdit() {
    if (editMode) exitEdit();
    else enterEdit();
  }

  // Hit-tests against the renderer's own note-head bounds (score-render.js's
  // positional map), which - unchanged by #238 - covers sounding notes only;
  // a rest has no head of its own there to click. A rest is reached by arrow
  // navigation instead (see moveSelection/stepAny); clicking one is a gap
  // left open by that same boundary, not attempted here.
  function selectAt(clientX, clientY) {
    if (!editMode || !view || !doc) return;
    const ord = view.editor.hitTest(clientX, clientY);
    if (ord == null) return;
    editWarn = "";
    fretEntry = null;
    selectedRest = null;
    selectedOrdinal = ord;
    refreshSelection();
  }

  // The string offered to convert a rest into a note (#238): the string the
  // player was last actually fretting this session if there is one, else a
  // simple per-voice default (voice 1 -> string 1, voice 2 -> string 2, ...,
  // wrapping if the voice number runs past the string count) - "a sensible
  // default position" the issue asks for, not a claim about which string a
  // given voice is conventionally written on.
  function defaultStringForVoice(voice) {
    if (lastEditString != null) return lastEditString;
    const count = editStringCount || 1;
    const v = Number.isInteger(voice) && voice > 0 ? voice : 1;
    return ((v - 1) % count) + 1;
  }

  function selectRest(restOrdinal) {
    if (!doc) return;
    const d = doc.restAt(restOrdinal);
    if (!d) {
      clearSelection();
      return;
    }
    selectedOrdinal = null;
    selFret = selString = selType = selMidi = selNoteId = null;
    selStep = selAlter = selAccidental = null;
    selDots = d.dots ?? 0;
    selTieStart = selTieStop = false;
    selVoice = d.voice;
    selOnset = d.onset;
    selRenderVoice = null;
    selVoiceOptions = [];
    // No renderer read of a rest exists to cross-check against (it has no
    // ordinal in score-render.js's positional map) - true, not merely
    // unchecked, so it never reads as a false "out of sync" warning.
    divergenceOk = true;
    overlay = null;
    editWarn = "";
    fretEntry = null;
    selRestString = defaultStringForVoice(d.voice);
    selectedRest = d;
  }

  function refreshSelection() {
    if (selectedOrdinal == null || !doc || !view) return;
    selectedRest = null;
    const d = doc.noteAt(selectedOrdinal);
    if (!d) {
      clearSelection();
      return;
    }
    if (d.string != null) lastEditString = d.string;
    const v = view.editor.viewInfo(selectedOrdinal);
    selFret = d.fret;
    selString = d.string;
    selType = d.type;
    selDots = d.dots ?? 0;
    selTieStart = !!d.tieStart;
    selTieStop = !!d.tieStop;
    selNoteId = d.id;
    selMidi = v?.midi ?? d.midi;
    selStep = d.step;
    selAlter = d.alter;
    selAccidental = d.accidental;
    selVoice = d.voice;
    selOnset = d.onset;
    selRenderVoice = v?.voice ?? null;
    // The voices this note can move to (#182): its own, the others its measure
    // already sounds, and the next one up (so a new voice can be created), kept
    // consecutive from 1 (Rule 6). At least [1, 2] so a monophonic bar can gain
    // a second voice; capped so the list never runs away.
    const upto = Math.min(4, Math.max(2, (d.measureVoices ?? 1) + 1));
    selVoiceOptions = Array.from({ length: upto }, (_, i) => i + 1);
    // The divergence oracle now also spans the voice a note landed in: the
    // renderer's own read (v.voice) against the document's <voice> (d.voice).
    // Guarded on v.voice being known so a renderer that does not report it never
    // forces a false negative - it only ever tightens the check.
    divergenceOk =
      !!v &&
      v.mxString === d.string &&
      v.fret === d.fret &&
      v.midi === d.midi &&
      (v.voice == null || v.voice === d.voice);
    updateOverlay();
  }

  function updateOverlay() {
    if (selectedOrdinal == null || !view || !scroller) {
      overlay = null;
      return;
    }
    const r = view.editor.headRect(selectedOrdinal);
    if (!r) {
      overlay = null;
      return;
    }
    // The overlay lives inside the scroller and is positioned in its scroll
    // space, so it tracks the note when the staff is scrolled without any
    // scroll listener. headRect is in client space; convert once.
    const sr = scroller.getBoundingClientRect();
    overlay = {
      left: r.left - sr.left + scroller.scrollLeft,
      top: r.top - sr.top + scroller.scrollTop,
      width: r.width,
      height: r.height,
    };
  }

  // The N-random-edits fuzz guard's assertion (#189), stronger than the
  // per-selection divergence cross-check above because it spans EVERY note, not
  // just the selected one. The per-selection guard catches a MISREAD of the note
  // under the cursor; it cannot catch a positional-map SHIFT - a voice move or a
  // delete that leaves the renderer's ordinal N naming a different note than the
  // document's ordinal N - unless that exact note happens to be selected. This
  // re-imports the WRITTEN MusicXML (a fresh parse of doc.text()) and asserts the
  // on-screen model equals it across all ordinals, so a shift anywhere shows.
  //
  // The equality asserted, enumerated:
  //   - count: the renderer's sounding-note count equals the re-import's.
  //   - per ordinal, the note the renderer drew and the note the re-import reads
  //     at that SAME ordinal are the same note: pitch (midi), string (MusicXML
  //     numbering), fret and voice all agree. This is what a positional shift
  //     breaks.
  //   - a full written-document round-trip: the live model's own reads against a
  //     fresh re-parse of the written text, field by field - id, pitch/spelling,
  //     string, fret, voice, ONSET, duration (via type + dots) and TIES - so the
  //     serializer/parser round-trip is proven lossless over onsets, durations
  //     and ties too, not only the four the renderer exposes.
  //
  // Returns { ok, docCount, renderCount, divergences } where each divergence
  // NAMES what disagreed ({ ordinal, field, id, doc, render } or a round-trip
  // entry), so a failing fuzz run points at the note rather than only going red.
  function auditAllNotes() {
    if (!doc || !view) return { ok: false, docCount: 0, renderCount: 0, divergences: [{ field: "no-document" }] };
    let redoc;
    try {
      redoc = createDocument(doc.text());
    } catch (e) {
      return { ok: false, docCount: 0, renderCount: 0, divergences: [{ field: "reimport-failed", detail: String(e?.message ?? e) }] };
    }
    const docCount = redoc.count();
    const renderCount = view.editor.noteCount();
    const divergences = [];
    if (docCount !== renderCount) divergences.push({ field: "count", doc: docCount, render: renderCount });
    const n = Math.min(docCount, renderCount);
    for (let i = 0; i < n; i++) {
      const d = redoc.noteAt(i);
      const v = view.editor.viewInfo(i);
      if (!v) {
        divergences.push({ ordinal: i, field: "no-render-view", id: d?.id ?? null });
        continue;
      }
      if (v.midi !== d.midi) divergences.push({ ordinal: i, field: "midi", id: d.id, doc: d.midi, render: v.midi });
      if (v.mxString !== d.string)
        divergences.push({ ordinal: i, field: "string", id: d.id, doc: d.string, render: v.mxString });
      if (v.fret !== d.fret) divergences.push({ ordinal: i, field: "fret", id: d.id, doc: d.fret, render: v.fret });
      if (v.voice != null && v.voice !== d.voice)
        divergences.push({ ordinal: i, field: "voice", id: d.id, doc: d.voice, render: v.voice });
    }
    // The written-document round-trip: the live (edited-in-place) model vs a
    // fresh re-parse of its own serialization, so onset/duration/tie identity is
    // asserted on both sides even though the renderer seam does not expose them.
    const live = doc.soundingNotes();
    const round = redoc.soundingNotes();
    if (live.length !== round.length) {
      divergences.push({ field: "roundtrip-count", live: live.length, round: round.length });
    } else {
      const fields = ["id", "midi", "step", "alter", "string", "fret", "voice", "onset", "type", "dots", "tieStart", "tieStop"];
      for (let i = 0; i < live.length; i++) {
        for (const f of fields) {
          if (live[i][f] !== round[i][f])
            divergences.push({ ordinal: i, field: `roundtrip-${f}`, id: live[i].id, live: live[i][f], round: round[i][f] });
        }
      }
    }
    return { ok: divergences.length === 0, docCount, renderCount, divergences };
  }

  async function applyEdit(mutate, refusal) {
    if (selectedOrdinal == null || !doc || !view) return;
    const before = doc.text();
    // The document refuses an edit it cannot write - a fret whose pitch has no
    // valid <octave> (Rule 11), a string out of range. Say so plainly rather
    // than swallowing it: a silent no-op reads as "the app is broken", and a
    // silent bad save is exactly what this bound exists to prevent.
    if (!mutate()) {
      editWarn = refusal || "That change can't be applied to this note.";
      return;
    }
    editWarn = "";
    const after = doc.text();
    if (after === before) return;
    undoStack = [...undoStack, before];
    redoStack = [];
    dirty = true;
    await view.editor.reload(after);
    refreshSelection();
  }

  function changeFret(value) {
    const v = Number(value);
    if (!Number.isInteger(v) || v < 0) {
      editWarn = "A fret is a whole number, zero or more.";
      return;
    }
    applyEdit(
      () => doc.setFret(selectedOrdinal, v),
      "That fret would put the note outside the pitch range MusicXML can write (octaves 0–9).",
    );
  }

  function changeString(value) {
    const v = Number(value);
    if (!Number.isInteger(v)) return;
    applyEdit(
      () => doc.setString(selectedOrdinal, v),
      "That string would put the note outside the pitch range MusicXML can write (octaves 0–9).",
    );
  }

  function changeDuration(type) {
    applyEdit(() => doc.setDurationType(selectedOrdinal, type), "That duration can't be written for this note.");
  }

  // Set the selected note's augmentation dots (#183). Non-structural in the same
  // sense a duration change is - the ordinal is unchanged - so it goes through
  // the same applyEdit that a fret/string/duration change does: mutate the
  // document, re-import, re-render, re-read. setDots keeps <duration> exactly
  // consistent with <type> + the <dot/>(s), which is what the re-import agrees
  // with; a value that cannot be written as a whole number of divisions (or a
  // tuplet member, whose sounding duration is scaled) is refused.
  function changeDots(value) {
    const v = Number(value);
    if (!Number.isInteger(v) || v < 0 || v > 2) return;
    applyEdit(() => doc.setDots(selectedOrdinal, v), "That dotted value can't be written for this note.");
  }

  // Toggle a tie from the selected note to the next one (#183). Both notes
  // change (the start on this one, the stop on its partner) but neither is added
  // or removed, so the ordinal is stable and applyEdit's re-import/re-render is
  // enough. A tie to a different pitch, or across a gap, is refused (setTie
  // returns false) with the stated message.
  function toggleTie() {
    applyEdit(
      () => doc.setTie(selectedOrdinal, !selTieStart),
      "A tie needs the next note to be the same pitch and directly follow this one.",
    );
  }

  // Spell the selected note with a chosen accidental - flat, natural or sharp -
  // keeping the same sounding pitch (#185). Non-structural like a fret edit (the
  // ordinal is unchanged), so it goes through applyEdit: the document rewrites
  // <pitch>+<accidental>, re-imports and re-reads. setAccidental refuses a
  // spelling that does not exist for the pitch (a natural of a black key) or that
  // pushes the octave out of range, leaving the note as it was.
  function changeAccidental(value) {
    const v = Number(value);
    if (!Number.isInteger(v)) return;
    applyEdit(
      () => doc.setAccidental(selectedOrdinal, v),
      "That accidental can't spell this note (its pitch has no such spelling in a writable octave).",
    );
  }

  // Cycle the selected note through its enharmonic spellings (F sharp <-> G flat,
  // and so on), same sounding pitch at every step (#185). Same applyEdit path as
  // the accidental control; refused only when the pitch has a single spelling to
  // offer.
  function cycleEnharmonic(direction) {
    applyEdit(
      () => doc.cycleSpelling(selectedOrdinal, direction),
      "This note has no other enharmonic spelling to cycle to.",
    );
  }

  // Move the selected note into another voice (#182). Unlike a fret/string/
  // duration edit, this is STRUCTURAL - it introduces a <backup> and a second
  // voice, and the moved note's ordinal changes (its voice block now sits after
  // the one it left), so this follows deleteSelected's shape rather than
  // applyEdit's: rebuild the model from the new text and re-select the note at
  // its NEW ordinal (which doc.moveToVoice returns) rather than trusting the
  // in-place ordinal map. The move keeps the note's onset, so the same
  // correction can be heard the instant it is made (the transport stays live).
  async function changeVoice(value) {
    const v = Number(value);
    if (!Number.isInteger(v) || v < 1) return;
    if (selectedOrdinal == null || !doc || !view) return;
    if (v === selVoice) return; // choosing the note's own voice is a no-op
    fretEntry = null;
    const before = doc.text();
    const newOrdinal = doc.moveToVoice(selectedOrdinal, v);
    if (newOrdinal == null) {
      editWarn = "That note can't be moved to that voice.";
      return;
    }
    const after = doc.text();
    if (after === before) return;
    editWarn = "";
    undoStack = [...undoStack, before];
    redoStack = [];
    dirty = true;
    doc = createDocument(after);
    editStringCount = doc.stringCount;
    selectedOrdinal = newOrdinal;
    await view.editor.reload(after);
    if (doc.noteAt(selectedOrdinal)) refreshSelection();
    else clearSelection();
  }

  // ----------------------------------------------- the keyboard core loop (#186)
  //
  // Arrows move the selection, a digit sets the fret, Backspace deletes - all on
  // the window key handler beside the transport shortcuts (see onKey), claimed
  // only while a note is selected so the same keys still drive the transport and
  // the staff-profile switch when it is not. NO key here starts playback: moving
  // the selection is a document-model walk, not a transport seek, and the
  // transport stays live throughout (Space still plays, even mid-edit).

  // Move the selection note-to-note (kind "note") or bar-to-bar (kind
  // "measure"), the editor's two arrow granularities - the same split the
  // transport's own arrows make (a beat, and a whole bar). No playback: this
  // only re-reads a different note (or, for "note", possibly a rest - #238)
  // into the selection.
  //
  // "note" walks doc.stepAny - EVERY element in document order, rests
  // included - so a rest is a stop along the way, not skipped, the same way a
  // click cannot reach one but an arrow now can. "measure" stays
  // doc.stepMeasure, sounding notes only: bar-to-bar rest stepping is not this
  // increment's bet (the issue's own rabbit holes exclude the duration/
  // structure work a general rest timeline would need), so it is a no-op from
  // a rest, and unreachable from one via ArrowUp/Down.
  function moveSelection(kind, direction) {
    if ((selectedOrdinal == null && selectedRest == null) || !doc) return;
    // Moving off the current selection ends its two-digit fret window.
    fretEntry = null;
    if (kind === "measure") {
      if (selectedOrdinal == null) return;
      const target = doc.stepMeasure(selectedOrdinal, direction);
      if (target == null || target === selectedOrdinal) return;
      editWarn = "";
      selectedOrdinal = target;
      refreshSelection();
      return;
    }
    const sel = selectedRest != null ? { restOrdinal: selectedRest.restOrdinal } : { ordinal: selectedOrdinal };
    const target = doc.stepAny(sel, direction);
    if (!target) return;
    editWarn = "";
    if (target.ordinal != null) {
      selectedOrdinal = target.ordinal;
      refreshSelection();
    } else {
      selectRest(target.restOrdinal);
    }
  }

  // A digit typed with a note selected. Sets the fret to that digit at once; a
  // second digit within TWO_DIGIT_MS on the same note extends it to two digits
  // (see fretEntry's declaration). Both paths write through the same
  // applyEdit/doc.setFret the panel's Fret field uses, so the exact same
  // writable-pitch bound (Rule 11, MIDI 12-131 / octaves 0-9) refuses an
  // out-of-range fret here too - a two-digit value that would exceed it is
  // refused, the already-committed single digit left in place.
  //
  // With a REST selected instead (#238), the same digit turns it into a note -
  // see convertRestDigit - on selRestString at that fret; the two-digit
  // window then continues normally once it becomes a real, selected ordinal.
  const FRET_REFUSAL = "That fret would put the note outside the pitch range MusicXML can write (octaves 0–9).";
  function typeFretDigit(d) {
    if (selectedRest != null) {
      convertRestDigit(d);
      return;
    }
    if (selectedOrdinal == null || !doc) return;
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    const extend = fretEntry && fretEntry.ordinal === selectedOrdinal && now - fretEntry.at <= TWO_DIGIT_MS;
    if (extend) {
      const combined = fretEntry.value * 10 + d;
      // A two-digit fret is complete - a third digit starts a fresh entry
      // rather than building a three-digit number no fretboard has.
      fretEntry = null;
      applyEdit(() => doc.setFret(selectedOrdinal, combined), FRET_REFUSAL);
    } else {
      fretEntry = { ordinal: selectedOrdinal, value: d, at: now };
      applyEdit(() => doc.setFret(selectedOrdinal, d), FRET_REFUSAL);
    }
  }

  // Turn the rest at `restOrdinal` into a note on `string` at `fret` (#238),
  // through doc.restToNote - the shared body behind both convertRestDigit (the
  // keyboard/panel path) and the fuzz harness's "restToNote" op below, so the
  // two exercise exactly the same state transition. Structural like
  // deleteSelected/changeVoice: the rest's position gains a new sounding
  // ordinal, so the model is rebuilt from the new text and the NEW ordinal
  // (restToNote's own return) is what gets selected, not the rest's old
  // address. Returns the new ordinal, or null when the edit was refused
  // (leaving the current selection as restOrdinal's rest, reselected, so the
  // refusal message sits next to the rest the player was trying to fret).
  const REST_TO_NOTE_REFUSAL = "That fret can't be written as a note on this string (octaves 0–9).";
  async function applyRestToNote(restOrdinal, string, fret) {
    if (!doc || !view) return null;
    const before = doc.text();
    const newOrdinal = doc.restToNote(restOrdinal, string, fret);
    if (newOrdinal == null) {
      // Reselect first: selectRest clears editWarn, so the message has to be
      // set after it or it is never seen.
      selectRest(restOrdinal);
      editWarn = REST_TO_NOTE_REFUSAL;
      return null;
    }
    editWarn = "";
    const after = doc.text();
    undoStack = [...undoStack, before];
    redoStack = [];
    dirty = true;
    lastEditString = string;
    doc = createDocument(after);
    editStringCount = doc.stringCount;
    selectedRest = null;
    selRestString = null;
    selectedOrdinal = newOrdinal;
    // Seeds the two-digit fret window as if this digit had been typed on an
    // ordinary note, so a fast second digit right after extends the fret to
    // two digits exactly the way typeFretDigit's own extend path does.
    fretEntry = { ordinal: newOrdinal, value: fret, at: typeof performance !== "undefined" ? performance.now() : Date.now() };
    await view.editor.reload(after);
    if (doc.noteAt(selectedOrdinal)) refreshSelection();
    else clearSelection();
    return newOrdinal;
  }

  // A digit typed with a rest selected: fret it on selRestString (the panel's
  // own default - see selectRest/defaultStringForVoice).
  async function convertRestDigit(d) {
    if (!selectedRest) return;
    const string = selRestString ?? defaultStringForVoice(selectedRest.voice);
    await applyRestToNote(selectedRest.restOrdinal, string, d);
  }

  // The String select on the rest panel (#238) - changes selRestString only;
  // nothing is written to the document until a digit is typed (convertRestDigit).
  function changeRestString(value) {
    const v = Number(value);
    if (!Number.isInteger(v)) return;
    selRestString = v;
  }

  // Backspace deletes the selected note by turning it into a rest (doc.deleteNote
  // - see its comment for why a rest, not a removal). The delete shifts every
  // ordinal after it, so the model is rebuilt from the new text (as undo's
  // restore does) rather than trusting the in-place ordinal map; the note that
  // slid into the selected slot stays selected, or the selection clears if the
  // deleted note was the last one.
  async function deleteSelected() {
    if (selectedOrdinal == null || !doc || !view) return;
    fretEntry = null;
    const before = doc.text();
    if (!doc.deleteNote(selectedOrdinal)) {
      editWarn = "That note can't be deleted.";
      return;
    }
    const after = doc.text();
    if (after === before) return;
    editWarn = "";
    undoStack = [...undoStack, before];
    redoStack = [];
    dirty = true;
    doc = createDocument(after);
    editStringCount = doc.stringCount;
    await view.editor.reload(after);
    if (doc.noteAt(selectedOrdinal)) refreshSelection();
    else clearSelection();
  }

  async function restore(text) {
    // Undo and redo both work by re-importing a whole document snapshot - the
    // cheapest correct thing when the model IS the document text. createDocument
    // cannot fail here: the snapshot was produced by our own serializer.
    doc = createDocument(text);
    editStringCount = doc.stringCount;
    dirty = true;
    await view.editor.reload(text);
    refreshSelection();
  }

  async function undo() {
    if (!undoStack.length || !doc || !view) return;
    const prev = undoStack[undoStack.length - 1];
    undoStack = undoStack.slice(0, -1);
    redoStack = [...redoStack, doc.text()];
    await restore(prev);
  }

  async function redo() {
    if (!redoStack.length || !doc || !view) return;
    const next = redoStack[redoStack.length - 1];
    redoStack = redoStack.slice(0, -1);
    undoStack = [...undoStack, doc.text()];
    await restore(next);
  }

  async function saveEdits() {
    if (!doc || !onSaveEdit) return;
    saving = true;
    saveError = "";
    try {
      await onSaveEdit(doc.text());
      dirty = false;
    } catch (e) {
      saveError = String(e?.message ?? e);
    } finally {
      saving = false;
    }
  }

  // Keeps the edit session alive across a rebuild of the underlying view - a
  // save changes the `tex` prop, which tears down and recreates the alphaTab
  // view (see the load effect below); this re-applies the notation staff and
  // rebuilds `doc` from the new text so edit mode survives it. Runs on entering
  // edit mode too (that is what first turns the notation staff on).
  $effect(() => {
    if (!editMode) return;
    const v = view;
    const t = tex;
    void t;
    if (!v) return;
    untrack(() => {
      if (!doc) initEditDoc();
      v.editor.setNotationShown(true);
      if (selectedOrdinal != null) refreshSelection();
      // Test instrumentation, in the same spirit as window.__audioPeak and
      // window.__heartbeats elsewhere: the note-head geometry is the renderer's
      // to know, so a browser test that wants to click a SPECIFIC note asks the
      // live view where that note is rather than guessing at pixels. Read-only
      // - it exposes no way to mutate the score.
      if (typeof window !== "undefined") {
        window.__scoreEditor = {
          headRect: (ordinal) => v.editor.headRect(ordinal),
          headPoint: (ordinal) => v.editor.headPoint(ordinal),
          hitTest: (x, y) => v.editor.hitTest(x, y),
          viewInfo: (ordinal) => v.editor.viewInfo(ordinal),
          noteCount: () => v.editor.noteCount(),
          boundsCount: () => v.editor.boundsCount(),
          // The whole-model re-import cross-check (#189). Read-only like the rest
          // of this hook - it re-parses the written document and compares, it
          // does not write. Exposed here so the fuzz spec can assert on the same
          // audit the guard runs, and NAME a divergence when one is found.
          audit: () => auditAllNotes(),
        };
        // The N-random-edits fuzz DRIVER (#189) - test instrumentation, gated
        // behind an opt-in flag a test sets before load (addInitScript), so it
        // never exists in an ordinary session. Unlike __scoreEditor above it
        // MUTATES, but only by calling the very functions the panel's own
        // controls call, so it can drive nothing a user with the editor open
        // could not. It lets a seeded sequence apply real edits through the real
        // reload/re-render/divergence path (`apply`), and deliberately induce a
        // stale-render divergence for the guard to catch (`corrupt`).
        if (window.__fermataEditorHarness) {
          window.__scoreEditorHarness = {
            count: () => (doc ? doc.count() : 0),
            stringCount: () => (doc ? doc.stringCount : 0),
            noteAt: (ordinal) => (doc ? doc.noteAt(ordinal) : null),
            // Rest selection surface (#238), parallel to count/noteAt above -
            // restCount/restAt address the rests, not the sounding notes.
            restCount: () => (doc ? doc.restCount() : 0),
            restAt: (restOrdinal) => (doc ? doc.restAt(restOrdinal) : null),
            text: () => (doc ? doc.text() : null),
            select: (ordinal) => {
              if (doc == null || ordinal == null || ordinal < 0 || ordinal >= doc.count()) return null;
              selectedOrdinal = ordinal;
              refreshSelection();
              return selectedOrdinal;
            },
            selectRestOrdinal: (restOrdinal) => {
              if (doc == null || restOrdinal == null || restOrdinal < 0 || restOrdinal >= doc.restCount()) return null;
              selectRest(restOrdinal);
              return selectedRest?.restOrdinal ?? null;
            },
            audit: () => auditAllNotes(),
            // Apply one shipped edit op to `ordinal` through the real handler,
            // awaiting its reload/re-render, and report whether it applied, was
            // refused, and the per-edit divergence flag afterwards. `op ===
            // "restToNote"` is the one exception to "ordinal is a sounding
            // note" (#238): there `ordinal` addresses a REST (doc.restCount()),
            // and `arg` is `{ string, fret }`, since converting a rest starts
            // from no prior selection to cross the fuzz's uniform dispatch.
            async apply(ordinal, op, arg) {
              if (op === "restToNote") {
                if (doc == null || ordinal == null || ordinal < 0 || ordinal >= doc.restCount())
                  return { applied: false, refused: false, reason: "rest-ordinal-out-of-range", divergenceOk };
                editWarn = "";
                const before = doc.text();
                const { string, fret } = arg ?? {};
                const newOrdinal = await applyRestToNote(ordinal, string, fret);
                const after = doc ? doc.text() : before;
                return {
                  op,
                  arg: arg ?? null,
                  ordinal,
                  applied: after !== before,
                  refused: newOrdinal == null,
                  warn: editWarn || null,
                  selected: selectedOrdinal,
                  divergenceOk,
                };
              }
              if (doc == null || ordinal == null || ordinal < 0 || ordinal >= doc.count())
                return { applied: false, refused: false, reason: "ordinal-out-of-range", divergenceOk };
              selectedOrdinal = ordinal;
              refreshSelection();
              editWarn = "";
              const before = doc.text();
              switch (op) {
                case "fret": await changeFret(arg); break;
                case "string": await changeString(arg); break;
                case "duration": await changeDuration(arg); break;
                case "dots": await changeDots(arg); break;
                case "tie": await toggleTie(); break;
                case "accidental": await changeAccidental(arg); break;
                case "enharmonic": await cycleEnharmonic(arg); break;
                case "voice": await changeVoice(arg); break;
                case "delete": await deleteSelected(); break;
                default: return { applied: false, refused: false, reason: "unknown-op", divergenceOk };
              }
              const after = doc ? doc.text() : before;
              return {
                op,
                arg: arg ?? null,
                ordinal,
                applied: after !== before,
                refused: !!editWarn,
                warn: editWarn || null,
                selected: selectedOrdinal,
                divergenceOk,
              };
            },
            // Mutate the DOCUMENT but skip the re-render, leaving the on-screen
            // model stale against the written MusicXML - the exact positional-map
            // /stale-render divergence the audit exists to catch. Used by the
            // "the guard actually catches a divergence" spec (#189, target 2).
            corrupt(ordinal, op, arg) {
              if (doc == null || ordinal == null || ordinal < 0 || ordinal >= doc.count()) return { changed: false };
              const before = doc.text();
              let changed = false;
              switch (op) {
                case "fret": changed = doc.setFret(ordinal, arg); break;
                case "string": changed = doc.setString(ordinal, arg); break;
                case "duration": changed = doc.setDurationType(ordinal, arg); break;
                case "delete": changed = doc.deleteNote(ordinal); break;
                case "voice": changed = doc.moveToVoice(ordinal, arg) != null; break;
                default: return { changed: false, reason: "unknown-op" };
              }
              // Keep the in-place model consistent with its own text for the
              // round-trip half of the audit, but do NOT reload the view - that
              // is the whole point: the render now lags the written document.
              if (changed) doc = createDocument(doc.text());
              return { changed, before, after: doc.text() };
            },
          };
        } else {
          window.__scoreEditorHarness = null;
        }
      }
    });
  });

  const PROFILE_LABELS = [
    ["score", "Notation"],
    ["tab", "Tab"],
    ["scoretab", "Both"],
  ];

  const SPEEDS = [0.5, 0.75, 1, 1.25];

  const DEMO_TEX = `\\title "Fermata Demo"
\\subtitle "Estudio in E minor"
\\tempo 80
.
:8 0.1 3.2 2.3 0.1 3.2 2.3 0.1 3.2 |
:8 0.2 2.3 2.4 0.2 2.3 2.4 0.2 2.3 |
:8 1.1 0.2 2.3 1.1 0.2 2.3 1.1 0.2 |
:8 0.1 0.2 1.3 0.1 0.2 1.3 0.1 0.2 |
:2 (0.6 2.5 2.4 0.3 0.2 0.1) :2 (0.6 2.5 2.4 0.3 0.2 0.1)`;

  function source() {
    if (demo) return { kind: "alphatex", text: DEMO_TEX };
    if (tex != null) return { kind: format === "musicxml" ? "musicxml" : "alphatex", text: tex };
    if (score) return { kind: "file", url: api.fileUrl(score.id) };
    return null;
  }

  function advanceLadder() {
    if (!ladder) return;
    const next = Math.round(speed * 100) + ladderStep;
    if (next >= ladderTarget) {
      speed = ladderTarget / 100;
      ladder = false;
    } else {
      speed = next / 100;
    }
    view?.setSpeed(speed);
  }

  $effect(() => {
    // read tracked, outside the untrack below: a new tex/score/demo has to
    // rebuild the renderer, which is what makes "Save & render" re-render
    const src = source();
    // a stale error or transport state from a previous load (e.g. a bad
    // edit) must not linger once a new load starts - without this, "Save &
    // render" leaves the old Pause/enabled buttons showing while the new
    // player is still loading its soundfont
    loadError = "";
    playerReady = false;
    playing = false;
    // a new score has its own tempo - the old readout would otherwise show
    // the previous score's number until this one's first report arrives
    metronomeTempo = null;
    metronomeLimit = null;
    scoreTempo = null;
    scoreTempoFrom = "none";
    // unknown again for this score, not "same as the last one" - see the
    // comment on profileOptions's declaration
    profileOptions = null;
    tabWithheldCount = 0;
    // untrack: everything below is driven imperatively once the view exists;
    // tracking it here would tear down and rebuild the renderer (and stop
    // playback) on a profile switch or a toggle.
    const v = untrack(() =>
      createScoreView(host, {
        scroller,
        source: src,
        profile,
        preset: gigMode ? "stand" : "desk",
        theme: settings.staff_theme,
        transport: { speed, looping, countIn },
        onReady: () => (playerReady = true),
        onPlaying: (p) => (playing = p),
        onError: (m) => (loadError = m),
        onPassComplete: advanceLadder,
        onMetronomeTempo: (bpm, limit) => {
          metronomeTempo = bpm;
          metronomeLimit = limit;
        },
        onScoreTempo: (t, from) => {
          scoreTempo = t;
          scoreTempoFrom = from;
        },
        // Only which buttons to offer, not which one is highlighted - see
        // onProfileApplied for that. A score with nothing drawable reports an
        // empty array here, not a fallback list; the empty-state notice in
        // the markup below is what tells the profileOptions.length === 0
        // case apart from "still loading" (profileOptions still null).
        // `withheld` (issue #165) is how many staves lost tablature to
        // disqualifyUnstrungTabStaves() - kept so the empty-state notice can
        // tell "never had notation or tablature" apart from "had tablature,
        // withheld it"; see tabWithheldCount's own declaration.
        onProfiles: (profiles, _unrenderable, withheld) => {
          profileOptions = profiles;
          tabWithheldCount = withheld ?? 0;
        },
        // The highlighted button has to follow what actually finished
        // drawing, not what was most recently requested - setProfile() below
        // deliberately does not set `profile` itself, because a requested
        // profile that fails to render (or hasn't rendered yet) must not
        // move the highlight onto a staff that isn't the one on screen.
        // loadError is cleared here too: a render succeeding means whatever
        // problem it described is resolved, and it must not keep showing
        // (or keep the .error paragraph occupying space) once the view has
        // recovered - score-render.js's own setProfile() guard would
        // otherwise never get a chance to retry a *different* profile
        // switch while a stale error from an earlier one sat on screen.
        onProfileApplied: (p) => {
          profile = p;
          loadError = "";
        },
      }),
    );
    view = v;
    return () => v.destroy();
  });

  // Gig mode is the same width read from further away, so it wants a
  // different layout at that width rather than a different width.
  $effect(() => {
    view?.setPreset(gigMode ? "stand" : "desk");
  });

  $effect(() => {
    view?.setTheme(settings.staff_theme);
  });

  function setProfile(p) {
    // the buttons only ever offer a viable profile, but guard anyway rather
    // than trust that
    if (!profileOptions?.includes(p)) return;
    // `profile` (the highlighted button) is deliberately not set here. It is
    // set from onProfileApplied, above, once a render with this profile has
    // actually finished - setting it eagerly would move the highlight onto a
    // staff that is not yet, or never, actually on screen: a render that
    // fails for a reason other than an unsupported profile (still possible -
    // see onError) would otherwise leave the highlight on a profile whose
    // render just threw, with the previous, different render frozen
    // underneath it. That mismatch was the original bug's second half.
    view?.setProfile(p);
  }

  function setSpeed(ev) {
    speed = Number(ev.target.value);
    view?.setSpeed(speed);
  }

  function toggleLoop() {
    looping = !looping;
    view?.setLooping(looping);
    // the ladder advances on loop completions, so it cannot run unlooped
    if (!looping) ladder = false;
  }

  function toggleCountIn() {
    countIn = !countIn;
    view?.setCountIn(countIn);
  }

  function clamp(n, lo, hi) {
    if (Number.isNaN(n)) return lo;
    return Math.min(hi, Math.max(lo, n));
  }

  function toggleLadder() {
    ladder = !ladder;
    if (!ladder) return;
    // a target at or below the start would step downwards and quit at once
    if (ladderTarget <= ladderStart) ladderTarget = Math.min(200, ladderStart + ladderStep);
    looping = true;
    speed = ladderStart / 100;
    view?.setLooping(true);
    view?.setSpeed(speed);
  }

  function setLadderStart(ev) {
    // 13 is the floor the synth itself enforces; lower values would play
    // faster than the readout claims
    ladderStart = clamp(Number(ev.target.value), 13, 100);
  }

  function setLadderStep(ev) {
    ladderStep = clamp(Number(ev.target.value), 1, 25);
  }

  function setLadderTarget(ev) {
    ladderTarget = clamp(Number(ev.target.value), 10, 200);
  }

  // ---------------------------------------------------------- #92: shortcuts
  //
  // "S" changes playback speed by stepping through the same presets the
  // select already offers, wrapping past either end - a single key standing
  // in for opening Songsterr's speed panel, since this toolbar has no panel
  // to open, only a picker. Deliberately over SPEEDS only, not the ladder's
  // in-between values: a mid-ladder speed is something the ladder is
  // actively managing, and a key nudging it to the nearest preset would
  // fight the ladder rather than complement it.
  function cycleSpeed() {
    const at = SPEEDS.indexOf(speed);
    speed = SPEEDS[(at + 1 + SPEEDS.length) % SPEEDS.length] ?? SPEEDS[0];
    view?.setSpeed(speed);
  }

  // "T" cycles the staff theme - our own equivalent key (#92 asks for one;
  // Songsterr has nothing to model it on). Same write chooseTheme() above
  // makes by hand, just without an <select> change event to read from.
  function cycleTheme() {
    const at = STAFF_THEMES.indexOf(settings.staff_theme);
    const next = STAFF_THEMES[(at + 1 + STAFF_THEMES.length) % STAFF_THEMES.length] ?? STAFF_THEMES[0];
    setSetting("staff_theme", next).catch(() => {});
  }

  // Mirrors exactly what Metronome.svelte's own toggle() does when it is
  // driving a supplied `control` rather than owning an engine of its own
  // (ownsClick={false}, exactly how it is used below) - `enabled` bound back
  // here, and the control told directly. There is no click to prime from a
  // keyboard event the way toggle() primes one for a caller with no
  // transport of its own (see that function's own comment): the score's own
  // Play button is what gets audio going in this view, keyboard or not.
  function toggleMetronome() {
    metronome = !metronome;
    view?.metronome?.setEnabled(metronome);
  }

  function isTypingTarget(el) {
    const tag = el?.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable;
  }

  // Space is also how a focused BUTTON activates itself - a standard browser
  // default action this file does not own, and Enter does the same, but
  // nothing here binds Enter so only Space needs this exemption. A <button>
  // is none of INPUT/TEXTAREA/SELECT/contenteditable, so isTypingTarget alone
  // does not cover it: tabbing to the Loop button and pressing Space used to
  // preventDefault() the browser's own click-on-Space before it could fire,
  // starting playback instead of toggling Loop - and the same for every
  // other button in this toolbar, the viewer header, and the session-detail
  // panel, since this listener sits on the window and sees every keydown
  // regardless of which button has focus.
  function isButtonLikeTarget(el) {
    const tag = el?.tagName;
    return tag === "BUTTON" || el?.getAttribute?.("role") === "button";
  }

  /**
   * The single-key transport (#92). Guarded on: whether this instance is
   * even the one allowed to answer the keyboard right now (see `active`
   * above); whether the keypress is really meant for this view and not a
   * browser/OS shortcut (a held Ctrl/Meta - Alt is allowed through
   * deliberately, see below); whether a text field has focus - the one the
   * issue's own comment calls worse than no shortcut at all if it is wrong,
   * checked HERE, before the per-key switch, precisely because nothing below
   * it is safe to run while a field is focused, typing guard first; and,
   * narrower still and case-by-case rather than a blanket exemption, whether
   * the focused element already owns the specific key being pressed (Space
   * on a button - see isButtonLikeTarget).
   */
  function onKey(e) {
    if (!active) return;
    if (isTypingTarget(e.target)) return;
    // Never hijack a browser/OS shortcut. Alt is included even though #92's
    // reference comment builds its own fine-tempo shortcut on Alt+A/D - see
    // TabViewer's PR notes for why that one is not wired here: this view's
    // playback speed is a plain multiplier with no bpm of its own to step by
    // one, and inventing one is the preset-ladder rework the brief for this
    // issue says is explicitly not in scope.
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.repeat && e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "ArrowUp" && e.key !== "ArrowDown") {
      // held-key repeat is exactly what makes stepping the cursor or nudging
      // a loop boundary usable without tapping an arrow key twenty times -
      // every other shortcut here is a toggle or a one-shot action, where an
      // OS auto-repeat firing it a dozen times a second is just noise (or,
      // for L/N/C, a rapid double-toggle that looks like nothing happened).
      return;
    }
    // The keyboard core loop (#186) shares this one window handler with the
    // transport and the staff-profile switch, and must not fight them. The
    // arbitration rule: the editor claims arrows, digits and Backspace ONLY
    // while a note OR A REST is selected (editMode && (selectedOrdinal != null
    // || selectedRest != null) - #238 extends the same claim to a rest
    // selection); in every other state those keys fall through untouched to
    // the switch below. So a digit sets the selected note's fret (or turns a
    // selected rest into a note) when one of the two is selected, and switches
    // the staff profile when neither is (keys "1"/"2"/"3" there); Backspace
    // deletes the selected note (a no-op on a rest - deleteSelected's own
    // guard) when one is selected and stops the transport when neither is;
    // arrows move the selection when one is selected and move the playback
    // cursor when neither is. Every claimed key returns here, so it is handled
    // once and never also by the switch. Space, L, S, N, C, T are never
    // claimed - so the transport stays fully live while editing (a correction
    // can be heard the instant it is made), which is why a selection stays put
    // through it.
    if (editMode && (selectedOrdinal != null || selectedRest != null)) {
      switch (e.key) {
        case "ArrowLeft":
        case "ArrowRight":
          e.preventDefault();
          moveSelection("note", e.key === "ArrowRight" ? 1 : -1);
          return;
        case "ArrowUp":
        case "ArrowDown":
          e.preventDefault();
          moveSelection("measure", e.key === "ArrowDown" ? 1 : -1);
          return;
        case "Backspace":
          e.preventDefault();
          deleteSelected();
          return;
        default:
          if (e.key.length === 1 && e.key >= "0" && e.key <= "9") {
            e.preventDefault();
            typeFretDigit(Number(e.key));
            return;
          }
          // Not an editor key (Space, L, S, ...): end any open two-digit fret
          // window and let the transport/profile switch below handle it.
          fretEntry = null;
      }
    }
    switch (e.key) {
      case " ":
        // A focused BUTTON owns Space already - see isButtonLikeTarget.
        // Checked before preventDefault(), not after: calling
        // preventDefault() on this keydown at all suppresses the browser's
        // own default action for it regardless of who called it, which
        // includes "activate the focused button" - so this has to be a
        // return, not a fall-through that merely skips playPause().
        if (isButtonLikeTarget(e.target)) return;
        // preventDefault BEFORE the readiness check, always, for this one and
        // every case below that has a native default worth blocking: Space
        // scrolls the page and Backspace can navigate history when nothing
        // else claims them, and both would otherwise still happen during the
        // (brief, but real) window before playerReady - see PdfViewer's own
        // onKey, which does the same for its page-turn keys unconditionally.
        e.preventDefault();
        if (playerReady) view?.playPause();
        return;
      case "Backspace":
        e.preventDefault();
        if (playerReady) view?.stop();
        return;
      case "ArrowLeft":
      case "ArrowRight": {
        e.preventDefault();
        if (!playerReady) return;
        const dir = e.key === "ArrowRight" ? 1 : -1;
        if (e.shiftKey) view?.nudgeLoopBoundary(dir);
        else view?.moveCursorBeat(dir);
        return;
      }
      case "ArrowUp":
      case "ArrowDown": {
        e.preventDefault();
        if (!playerReady) return;
        view?.moveCursorBar(e.key === "ArrowDown" ? 1 : -1);
        return;
      }
      case "l":
      case "L":
        toggleLoop();
        return;
      case "s":
      case "S":
        cycleSpeed();
        return;
      case "n":
      case "N":
        toggleMetronome();
        return;
      case "c":
      case "C":
        toggleCountIn();
        return;
      case "t":
      case "T":
        cycleTheme();
        return;
      case "1":
      case "2":
      case "3":
        setProfile(["score", "tab", "scoretab"][Number(e.key) - 1]);
        return;
      default:
        return;
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div
  class="wrap"
  data-editor-available={canEditNotes()}
  data-editor-active={editMode}
  data-editor-error={editError || null}
  data-editor-warn={editWarn || null}
  data-editor-selected={selectedOrdinal}
  data-editor-selected-fret={selFret}
  data-editor-selected-string={selString}
  data-editor-selected-type={selType}
  data-editor-selected-dots={selectedOrdinal != null ? selDots : null}
  data-editor-selected-tie-start={selectedOrdinal != null ? selTieStart : null}
  data-editor-selected-tie-stop={selectedOrdinal != null ? selTieStop : null}
  data-editor-selected-midi={selMidi}
  data-editor-selected-step={selStep}
  data-editor-selected-alter={selectedOrdinal != null && selAlter != null ? selAlter : null}
  data-editor-selected-accidental={selAccidental}
  data-editor-selected-note-id={selNoteId}
  data-editor-selected-voice={selVoice}
  data-editor-selected-onset={selOnset}
  data-editor-render-voice={selRenderVoice}
  data-editor-divergence-ok={editMode ? divergenceOk : null}
  data-editor-dirty={dirty}
  data-editor-can-undo={undoStack.length > 0}
  data-editor-can-redo={redoStack.length > 0}
  data-editor-selected-rest={selectedRest?.restOrdinal}
  data-editor-selected-rest-voice={selectedRest?.voice}
  data-editor-selected-rest-duration={selectedRest?.duration}
  data-editor-selected-rest-type={selectedRest?.type}
  data-editor-selected-rest-dots={selectedRest ? selDots : null}
  data-editor-selected-rest-string={selectedRest ? selRestString : null}
>
  {#if gigMode}
    <!-- gig mode: hide the practice toolbar chrome, but playback and the way
    back out must stay reachable even with no keyboard (touch/tablet) -->
    <div class="gig-hud">
      <button class="primary" disabled={!playerReady} onclick={() => view?.playPause()}>
        {playing ? "❚❚ Pause ((Space))" : "▶ Play ((Space))"}
      </button>
      <button disabled={!playerReady} onclick={() => view?.stop()} aria-label="Stop — back to the start ((Backspace))">
        ■
      </button>
      {#if metronome && metronomeTempo != null}
        <!-- Read-only here on purpose - gig mode strips the toolbar down to
        large, glanceable touch targets, and choosing a mode or typing a
        number is neither. "The current value visible while playing" still
        has to hold at a music stand, though, so the readout comes along even
        though its controls stay behind in the full toolbar, set up before
        stepping into gig mode. -->
        <span class="metronome-indicator" title="Metronome, clicks per minute">♩ {metronomeTempo}</span>
      {/if}
      {#if practiceLabel}
        <button class="practice-indicator" onclick={onStopPractice} title="Stop practice timer">
          ● {practiceLabel}
        </button>
      {/if}
      <button onclick={onToggleGig} aria-label="Exit gig mode ((Esc))" title="Exit gig mode (Esc)">⤢</button>
    </div>
  {:else}
    <div class="toolbar">
      {#if profileOptions?.length}
        <div class="seg">
          {#each PROFILE_LABELS as [value, label], i}
            {#if profileOptions.includes(value)}
              <!-- aria-label, not appended text: toolbar-responsive.spec.js
                   matches this button by its exact text ("Tab", anchored) -
                   see the note on the theme/speed selects above for the same
                   reason applied to a <button> instead of a <select>. -->
              <button
                class:on={profile === value}
                onclick={() => setProfile(value)}
                aria-label={`${label} ((${i + 1}))`}
              >
                {label}
              </button>
            {/if}
          {/each}
        </div>
      {/if}
      <select
        class="theme-picker"
        value={settings.staff_theme}
        onchange={chooseTheme}
        title="Staff theme"
        aria-label="Staff theme ((T))"
      >
        {#each STAFF_THEMES as t}
          <option value={t}>{STAFF_THEME_LABELS[t]}</option>
        {/each}
      </select>
      {#if canEditNotes()}
        <button
          class="edit-toggle"
          class:on={editMode}
          onclick={toggleEdit}
          title="Correct notes on the staff — click a note, then change its fret, string or duration"
        >
          {editMode ? "Done editing" : "Edit notes"}
        </button>
      {/if}
      <div class="player">
        <button class="primary" disabled={!playerReady} onclick={() => view?.playPause()}>
          {playing ? "❚❚ Pause ((Space))" : "▶ Play ((Space))"}
        </button>
        <button disabled={!playerReady} onclick={() => view?.stop()} aria-label="Stop — back to the start ((Backspace))">
          ■
        </button>
        <select value={speed} onchange={setSpeed} title="Playback speed" aria-label="Playback speed ((S))">
          {#if !SPEEDS.includes(speed)}
            <!-- the ladder steps to speeds between the presets -->
            <option value={speed}>{Math.round(speed * 100)}%</option>
          {/if}
          {#each SPEEDS as s}
            <option value={s}>{s}×</option>
          {/each}
        </select>
        <div class="practice">
          <button
            class:on={looping}
            onclick={toggleLoop}
            title="Loop playback ((L)) — drag across bars on the score to loop a section"
          >
            Loop ((L))
          </button>
          <!-- The general metronome (issue #97), pre-filled from this score:
          its declared tempo as the base the percentages are of, and its own
          time signature (which the click reads live from the playhead, so a
          meter change mid-piece is followed rather than assumed). compact,
          because over a piece the meter and subdivision come from the score
          and there is nothing there for a player to set; and nothing here
          persists, for the reason on `metronome`'s declaration above.

          tempoSource is what the control is allowed to CALL that base tempo.
          "not declared at the start" beats both of the other two: a score that
          prints no tempo there hands the renderer's fallback to a
          transcription and to a MusicXML file alike, and neither of them read
          it anywhere. tempoElsewhere then separates "the document says nothing
          about its tempo" from "it says something, just not here" - only the
          first may be stated as a fact about the document. -->
          <Metronome
            ownsClick={false}
            control={view?.metronome ?? null}
            bind:enabled={metronome}
            tempo={metronomeTempo}
            limit={metronomeLimit}
            proportionBase={true}
            baseTempoLabel={scoreTempo}
            tempoSource={scoreTempoFrom !== "start" ? "default" : tex != null ? "transcribed" : "marked"}
            tempoElsewhere={scoreTempoFrom === "later"}
            bind:mode={metronomeMode}
            bind:proportion={metronomeProportion}
            bind:bpm={metronomeBpm}
            compact={true}
            keyHint="N"
          />
          <button class:on={countIn} onclick={toggleCountIn} title="Count-in before playback starts ((C))">
            Count-in ((C))
          </button>
          <button
            class:on={ladder}
            onclick={toggleLadder}
            title="Tempo ladder — loop a passage and step the speed up automatically"
          >
            Ladder
          </button>
          {#if ladder}
            <div class="ladder-controls">
              <label>
                Start
                <input type="number" min="13" max="100" step="1" value={ladderStart} onchange={setLadderStart} />
              </label>
              <label>
                Step
                <input type="number" min="1" max="25" step="1" value={ladderStep} onchange={setLadderStep} />
              </label>
              <label>
                Target
                <input type="number" min="10" max="200" step="1" value={ladderTarget} onchange={setLadderTarget} />
              </label>
              <span class="ladder-readout">{Math.round(speed * 100)}%</span>
            </div>
          {/if}
        </div>
      </div>
    </div>
  {/if}

  {#if loadError}
    <p class="error">{loadError}</p>
  {/if}

  {#if profileOptions && profileOptions.length === 0}
    <!-- Nothing about this score is drawable under any profile - see
    score-render.js's supportedProfiles(). The renderer's own attempt to
    render it anyway still runs (it cannot be cancelled from here) and still
    throws internally, but that failure is caught and suppressed at the
    source rather than shown, so this plain sentence - not a stack trace - is
    the only thing a guitarist sees. The staff area below stays in the DOM
    (score-render.js's `host` binding needs a stable element) but is hidden
    rather than left showing whatever the failed render left behind.

    Two different sentences share this slot (issue #165): a score that never
    had notation or tablature at all gets UNRENDERABLE_MESSAGE, and a score
    that had a TAB staff disqualifyUnstrungTabStaves() had to withhold gets
    tabWithheldMessage() instead - "no notation or tablature" is false for
    the second case, which can be a TAB clef, real tuning and every note but
    one correctly fretted. -->
    <p class="notice">
      {tabWithheldCount > 0 ? tabWithheldMessage(tabWithheldCount) : UNRENDERABLE_MESSAGE}
    </p>
  {/if}

  {#if editError && !editMode}
    <!-- enterEdit() (above) catches createDocument's refusal, sets editError
    and returns before editMode ever flips true - so the {#if editMode} panel
    below, which is the only other place editError renders, never mounts for
    this case. Without this paragraph the refusal was silent: the "Edit
    notes" button just stayed enabled and did nothing (#226 follow-up). -->
    <p class="error">{editError}</p>
  {/if}

  {#if editMode}
    <div class="edit-panel">
      {#if editError}
        <p class="hint warn">{editError}</p>
      {:else if selectedRest != null}
        <!-- A rest, selected by arrow navigation (#238) - not by click; see
             selectAt's own comment for why the renderer cannot hit-test one.
             No fret/duration/voice fields to edit here (the no-gos rule out
             structure editing) - only the target string for the note a digit
             is about to make it, and the rest's own duration/voice as context. -->
        <div class="edit-fields">
          <span class="hint">Rest ({selectedRest.type ?? "?"}{"".padStart(selDots, "·")}), voice {selectedRest.voice}</span>
          <label title="The string a typed digit will fret this rest onto, turning it into a note">
            String
            <select value={String(selRestString)} onchange={(e) => changeRestString(e.target.value)}>
              {#each Array.from({ length: editStringCount }, (_, i) => i + 1) as s}
                <option value={String(s)}>{s}</option>
              {/each}
            </select>
          </label>
          <span class="edit-hint">Type a fret digit to turn this rest into a note.</span>
        </div>
      {:else if selectedOrdinal == null}
        <p class="edit-hint">Click a note on the staff to select it, then change its fret, string or duration. Arrow to a rest to select it.</p>
      {:else}
        <div class="edit-fields">
          <label>
            Fret
            <input
              type="number"
              min="0"
              max="36"
              value={selFret}
              onchange={(e) => changeFret(e.target.value)}
            />
          </label>
          <label>
            String
            <select value={String(selString)} onchange={(e) => changeString(e.target.value)}>
              {#each Array.from({ length: editStringCount }, (_, i) => i + 1) as s}
                <option value={String(s)}>{s}</option>
              {/each}
            </select>
          </label>
          <label>
            Duration
            <select value={selType ?? ""} onchange={(e) => changeDuration(e.target.value)}>
              {#if !DURATION_TYPES.includes(selType)}
                <option value={selType ?? ""} disabled>{selType ?? "—"}</option>
              {/if}
              {#each DURATION_TYPES as t}
                <option value={t}>{t}</option>
              {/each}
            </select>
          </label>
          <label title="Augmentation dots — a dot adds half the note's value again (a dotted quarter is 1.5× a quarter)">
            Dots
            <select value={String(selDots)} onchange={(e) => changeDots(e.target.value)}>
              <option value="0">none</option>
              <option value="1">dotted</option>
              <option value="2">double</option>
            </select>
          </label>
          <label title="How this note is spelled — a flat, a natural or a sharp — for the same sounding pitch. The key signature and any accidental already in the bar choose the default; this overrides it.">
            Accidental
            <select value={String(selAlter ?? 0)} onchange={(e) => changeAccidental(e.target.value)}>
              {#if selAlter != null && ![-1, 0, 1].includes(selAlter)}
                <option value={String(selAlter)} disabled>{selAlter > 0 ? "𝄪" : "𝄫"}</option>
              {/if}
              <option value="-1">♭ flat</option>
              <option value="0">♮ natural</option>
              <option value="1">♯ sharp</option>
            </select>
          </label>
          <button
            class="enharmonic"
            onclick={() => cycleEnharmonic(1)}
            title="Cycle the enharmonic spelling (F♯ ↔ G♭) — the same sounding pitch, spelled the other way"
          >
            ♯/♭
          </button>
          <button
            class="tie-toggle"
            class:on={selTieStart}
            onclick={toggleTie}
            title="Tie this note to the next — they must be the same pitch and directly follow one another, so the two read as one held note"
          >
            {selTieStart ? "Tied →" : "Tie →"}
          </button>
          <label title="Move this note to another voice — the second voice (and its backup) is created if it does not exist yet">
            Voice
            <select value={String(selVoice ?? "")} onchange={(e) => changeVoice(e.target.value)}>
              {#if selVoice != null && !selVoiceOptions.includes(selVoice)}
                <option value={String(selVoice)}>{selVoice}</option>
              {/if}
              {#each selVoiceOptions as vopt}
                <option value={String(vopt)}>{vopt}</option>
              {/each}
            </select>
          </label>
        </div>
      {/if}
      <div class="edit-actions">
        {#if editMode && selectedOrdinal != null && !divergenceOk}
          <!-- The document and the rendered model disagree about the selected
               note. In this single-source-of-truth design that must never
               happen; it is surfaced rather than hidden (see divergenceOk). -->
          <span class="hint warn" title="The staff and the saved document disagree about this note.">
            ⚠ out of sync
          </span>
        {/if}
        {#if editWarn}<span class="hint warn">{editWarn}</span>{/if}
        {#if saveError}<span class="hint warn">{saveError}</span>{/if}
        <button class="ghost" disabled={!undoStack.length} onclick={undo} title="Undo">Undo</button>
        <button class="ghost" disabled={!redoStack.length} onclick={redo} title="Redo">Redo</button>
        <button class="primary" disabled={!dirty || saving} onclick={saveEdits}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  {/if}

  <!-- Selecting a note is a spatial gesture: the click is hit-tested against
       the rendered note-head positions, which have no keyboard analogue here.
       Note-to-note keyboard navigation (arrow keys, digit-sets-fret, Backspace)
       arrived in #186 and lives on the window key handler beside the transport
       shortcuts (see onKey's arbitration comment), not as a keydown twin of
       this container's click. The staff itself is not a control. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="score-scroll"
    class:hidden={profileOptions?.length === 0}
    class:editing={editMode}
    bind:this={scroller}
    onclick={editMode ? (e) => selectAt(e.clientX, e.clientY) : undefined}
  >
    <div class="at-host" bind:this={host}></div>
    {#if editMode && overlay}
      <div
        class="note-selection"
        style="left:{overlay.left}px; top:{overlay.top}px; width:{overlay.width}px; height:{overlay.height}px;"
      ></div>
    {/if}
  </div>
</div>

<style>
  .wrap {
    position: relative;
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  /* Below ~869px this row no longer fits in one line at all (issue #106):
     on a portrait tablet - 834 and 768 are ordinary widths for one, and the
     project's own stated primary form factor - the row used to hold its
     width and let flex-shrink squeeze individual controls instead. That
     shrinking hit `.seg` hardest, because `.seg` has its own overflow:hidden
     for its rounded corners: shrunk below its buttons' combined width, the
     buttons it couldn't show were not just squeezed, they were clipped away
     entirely - present in the DOM, invisible, and unreachable by touch or
     mouse alike. Narrower still (430px), the whole row stopped fitting even
     after every control had shrunk as far as it could, and the toolbar
     itself overflowed the page, taking Metronome/Count-in/Ladder off the
     right edge with no scrollbar reachable by touch.

     The fix is to never shrink a control at all and instead let the row
     wrap - vertical space is what a tablet on a stand has to spare, reach
     across a clipped or off-screen button is what it does not. `.player`
     and `.practice` below get the same treatment, so the wrap cascades:
     first the toolbar's own top-level groups, then the transport row's
     controls, then the loop/metronome/count-in/ladder cluster - each only
     drops to a new line once it, not something it contains, stops fitting. */
  .toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
  }

  /* Not part of .player (the transport row) on purpose - a persistent
     preference living among per-session playback toggles would read as one
     of them. It's still reachable without leaving this view, which is the
     whole point (see chooseTheme above). */
  .theme-picker {
    flex-shrink: 0;
  }

  /* Play, speed and Loop are what a player reaches for mid-piece (#106), so
     below the width where anything has to move to a second line, this is
     the row that stays first and whole - not the profile switch or the
     theme picker, neither of which anyone reaches for while a piece is
     running. flex-basis: 100% forces everything after it (`.seg`,
     `.theme-picker`) onto a line of their own rather than sharing this one
     and leaving less room for it to wrap internally in a sensible order. */
  @media (max-width: 900px) {
    .player {
      order: -1;
      flex-basis: 100%;
      margin-left: 0;
    }
  }

  .gig-hud {
    position: absolute;
    z-index: 2;
    bottom: 18px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 10px;
    background: rgba(32, 27, 19, 0.92);
    border: 1px solid var(--line);
    border-radius: 99px;
    padding: 6px 12px;
    backdrop-filter: blur(6px);
  }

  .gig-hud button {
    font-size: 16px;
  }

  .practice-indicator {
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    color: var(--brass-bright);
    white-space: nowrap;
  }

  .seg {
    display: inline-flex;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
  }

  .seg button {
    border: none;
    border-radius: 0;
    background: none;
    padding: 7px 16px;
  }

  .seg button.on {
    background: var(--brass);
    color: #241d0f;
    font-weight: 600;
  }

  .player {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    /* pushed to the far edge now that .toolbar no longer uses
       justify-content: space-between - the theme picker sits between it and
       .seg instead of splitting the row in two. Only holds at widths where
       the whole row fits on one line - the narrow-width rule above resets
       it, since a row forced flush left by wrapping has nothing to be
       pushed away from. */
    margin-left: auto;
  }

  .practice {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    padding-left: 8px;
    border-left: 1px solid var(--line);
  }

  .practice button.on {
    color: var(--brass-bright);
    border-color: var(--brass);
  }

  .ladder-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--surface);
  }

  .ladder-controls label {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--ink-dim);
  }

  .ladder-controls input {
    width: 44px;
    padding: 2px 4px;
  }

  .ladder-readout {
    font-size: 12px;
    font-weight: 600;
    color: var(--brass);
  }

  /* .metronome-readout and .metronome-controls moved into Metronome.svelte
     along with the controls themselves. What stays here is the gig-mode
     HUD's own read-only echo below, which is this component's markup.

     The gig-mode HUD's read-only echo of the same value - see the markup
     above for why the controls themselves do not follow it into gig mode. */
  .metronome-indicator {
    font-size: 14px;
    font-variant-numeric: tabular-nums;
    color: var(--brass-bright);
    white-space: nowrap;
  }

  .score-scroll {
    flex: 1;
    /* horizontal layout is one endless system, so the stage has to scroll
       sideways as well - page layout never overflows this way */
    overflow: auto;
    padding: 20px;
    /* the note-selection overlay is an absolutely-positioned child in the
       scroller's own scroll space (see updateOverlay), so it needs this as its
       containing block to track the note when the staff is scrolled */
    position: relative;
  }

  .score-scroll.editing {
    cursor: pointer;
  }

  /* Drawn over the selected note's head(s) - a plain outline, positioned by
     updateOverlay in the scroller's scroll space so it follows the note. It is
     not interactive (clicks pass through to select another note). */
  .note-selection {
    position: absolute;
    z-index: 1;
    border: 2px solid var(--brass-bright);
    border-radius: 4px;
    background: rgba(200, 160, 70, 0.16);
    pointer-events: none;
    box-sizing: border-box;
  }

  .edit-toggle {
    flex-shrink: 0;
  }

  .edit-toggle.on {
    background: var(--brass);
    color: #241d0f;
    font-weight: 600;
  }

  .edit-panel {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px 16px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
  }

  .edit-hint {
    margin: 0;
    color: var(--ink-dim);
    font-size: 13px;
  }

  .edit-fields {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }

  .edit-fields label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    color: var(--ink-dim);
  }

  .edit-fields input {
    width: 64px;
  }

  .edit-fields .tie-toggle,
  .edit-fields .enharmonic {
    align-self: center;
    padding: 5px 12px;
  }

  .edit-fields .tie-toggle.on {
    background: var(--brass);
    color: #241d0f;
    font-weight: 600;
  }

  .edit-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
  }

  .edit-panel .hint {
    margin: 0;
    font-size: 12.5px;
    color: var(--ink-dim);
  }

  .edit-panel .hint.warn {
    color: var(--danger);
  }

  .edit-panel .ghost {
    background: none;
    border-color: transparent;
    color: var(--ink-dim);
  }

  .edit-panel .ghost:hover {
    border-color: var(--line);
    color: var(--ink);
  }

  /* Stays in the DOM (score-render.js's `host` binding has to stay stable)
     for a score with nothing drawable - just not shown, so whatever the
     renderer's own suppressed failed render left behind is never visible. */
  .score-scroll.hidden {
    display: none;
  }

  .notice {
    color: var(--ink-dim);
    text-align: center;
    margin: 32px 8px;
  }

  .at-host {
    background: var(--score-surface);
    border-radius: 6px;
    /* a reading measure for page layout. score-render.js overrides it for
       horizontal layout, where the paper has to run the whole length of the
       score rather than stop at a comfortable column width. */
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.55);
  }

  /* score-render.js publishes its chosen theme onto the host as
     data-score-theme - reused here rather than a second, component-local
     record of which theme is active. Parchment is the unmarked default.
     Fully :global() because the attribute is written by that module's
     dataset assignment, not by anything in this component's markup - Svelte
     can't see it and would otherwise prune the rule as unused. */
  :global(.at-host[data-score-theme="noir"]) {
    background: var(--score-noir-surface);
  }

  :global(.at-host[data-score-theme="print"]) {
    background: var(--score-print-surface);
  }

  /* The renderer creates its cursors and selection with position only and no
     colour at all - they are invisible until styled here. */
  .at-host :global(.at-cursor-bar) {
    background: var(--score-accent);
    opacity: 0.1;
  }

  /* width is the renderer's: it writes an inline width with a matching scale
     transform, and overriding one without the other would scale our value
     down to nothing */
  .at-host :global(.at-cursor-beat) {
    background: var(--score-accent);
    opacity: 0.85;
  }

  .at-host :global(.at-selection div) {
    background: var(--score-accent);
    opacity: 0.16;
  }

  :global(.at-host[data-score-theme="noir"] .at-cursor-bar),
  :global(.at-host[data-score-theme="noir"] .at-cursor-beat),
  :global(.at-host[data-score-theme="noir"] .at-selection div) {
    background: var(--score-noir-accent);
  }

  :global(.at-host[data-score-theme="print"] .at-cursor-bar),
  :global(.at-host[data-score-theme="print"] .at-cursor-beat),
  :global(.at-host[data-score-theme="print"] .at-selection div) {
    background: var(--score-print-accent);
  }

  .error {
    color: var(--danger);
    text-align: center;
    margin: 8px;
  }
</style>
