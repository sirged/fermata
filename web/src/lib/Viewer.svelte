<script>
  import { untrack } from "svelte";
  import { api, ApiError } from "./api.js";
  import { formatDuration } from "./practice.js";
  import { keySignatureLabel } from "./provenance.js";
  import PdfViewer from "./PdfViewer.svelte";
  import TabViewer from "./TabViewer.svelte";
  import ScoreCompare from "./ScoreCompare.svelte";

  let { id = null, demo = false } = $props();

  let score = $state(null);
  let error = $state("");
  // A refused or failed tempo change, shown beside the tempo control itself -
  // deliberately NOT the page-level `error` above, which gates an
  // {#if error}/{:else if score} chain that replaces the whole score view
  // with just its own message. Reusing it for one field's validation would
  // hide the score behind a tempo typo, which is a worse failure than the
  // uncaught ApiError this exists to fix. Same shape as detailError below,
  // scoped the same way: near the control it is about.
  let tempoError = $state("");
  let editingTags = $state(false);
  let tagsDraft = $state("");

  let viewerEl;
  let gigMode = $state(false);
  let wakeLock = null;
  // gig mode can end (Escape, tap exit) before an in-flight wakeLock.request
  // resolves; wantWakeLock says whether a lock should be held right now, so
  // the resolved request can release itself instead of pinning the screen on
  let wantWakeLock = false;

  async function acquireWakeLock() {
    if (wakeLock) return; // already held - don't overwrite the live sentinel
    if (!("wakeLock" in navigator)) return;
    wantWakeLock = true;
    let lock;
    try {
      lock = await navigator.wakeLock.request("screen");
    } catch {
      return;
    }
    if (!wantWakeLock) {
      // gig mode ended while the request was in flight
      lock.release().catch(() => {});
      return;
    }
    wakeLock = lock;
    wakeLock.addEventListener("release", () => {
      // a stale release from a since-replaced lock must not clobber a newer one
      if (wakeLock === lock) wakeLock = null;
    });
  }

  async function releaseWakeLock() {
    wantWakeLock = false;
    try {
      await wakeLock?.release();
    } catch {
      // already released
    }
    wakeLock = null;
  }

  // guards against a stale enter/exit continuation applying its effects
  // after a later call already changed gig mode (e.g. F then Escape fired
  // in quick succession while requestFullscreen was still pending)
  let gigOp = 0;

  async function enterGigMode() {
    const op = ++gigOp;
    gigMode = true;
    try {
      await viewerEl?.requestFullscreen?.();
    } catch {
      // fullscreen denied or unavailable; gig mode still works windowed
    }
    if (op !== gigOp) {
      // superseded by a later call while fullscreen was still engaging - only
      // undo it if gig mode actually ended up off; a newer enter may have
      // already taken over and must not be clobbered by this stale one
      if (!gigMode && document.fullscreenElement === viewerEl) {
        document.exitFullscreen().catch(() => {});
      }
      return;
    }
    acquireWakeLock();
  }

  async function exitGigMode() {
    ++gigOp;
    gigMode = false;
    if (document.fullscreenElement === viewerEl) {
      try {
        await document.exitFullscreen();
      } catch {
        // ignore
      }
    }
    releaseWakeLock();
  }

  function toggleGigMode() {
    if (gigMode) exitGigMode();
    else enterGigMode();
  }

  function onFullscreenChange() {
    // the browser may drop fullscreen without going through exitGigMode
    // (Escape, OS gesture, etc) - keep gig mode in sync so the header
    // doesn't stay hidden with no way back to it
    if (gigMode && document.fullscreenElement !== viewerEl) {
      gigMode = false;
      releaseWakeLock();
    }
  }

  function onVisibilityChange() {
    if (gigMode && document.visibilityState === "visible") acquireWakeLock();
  }

  function onKey(e) {
    const tag = e.target?.tagName;
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (e.ctrlKey || e.metaKey || e.altKey) return; // don't hijack Ctrl+F / Cmd+F
    if (e.repeat) return; // OS key auto-repeat must not spam toggleGigMode
    // #92: Esc closes whatever is open - checked BEFORE the typing guard
    // below, deliberately, and the one shortcut in this file exempt from it.
    // The guard exists to stop a stray CHARACTER landing in a field
    // somebody is typing into (see TabViewer's own onKey for the shortcuts
    // that actually risk that); Escape inserts nothing, and the most likely
    // place a player presses it is FROM INSIDE the very field it should
    // close - typing a tag, deciding against it, and hitting Esc without
    // first clicking away. Checked in the order a player would actually be
    // looking at it: gig mode is the most likely thing to be open (it is the
    // one this handler already knew how to close), then the two overlays
    // this header can have open at once, tag editing and the just-logged
    // session's detail panel. Only ever one of these closes per press:
    // dismissing the tag editor while the detail panel is ALSO open would
    // take both away in one keystroke, which is not "close whatever is
    // open" (singular) any more.
    if (e.key === "Escape") {
      if (gigMode) {
        e.preventDefault();
        exitGigMode();
      } else if (editingTags) {
        e.preventDefault();
        // Discards whatever is typed into tagsDraft, not merely closes -
        // there was no "cancel without saving" affordance here before this
        // issue at all (the tag editor's only exit was saveTags(), which
        // always saves), so this is genuinely new behaviour, not a
        // pre-existing Cancel this just wired a key to. Deliberately a
        // discard rather than a preserve-on-reopen: startTagEdit() already
        // re-seeds tagsDraft from score.tags every time it is opened, so
        // "preserve" would mean adding a second, separate persistence path
        // just for this one abandoned-edit case, and the ordinary meaning of
        // Cancel on a form - here or anywhere else on the web - is exactly
        // this: what you typed is gone, what was saved before is not
        // touched. See the browser test that types a draft, presses Esc,
        // reopens, and asserts the field is back to score.tags rather than
        // silently trusting "the editor closed" to also mean "as intended".
        editingTags = false;
      } else if (lastSession && detail) {
        e.preventDefault();
        dismissDetail();
      }
      return;
    }
    if (typing) return;
    if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      toggleGigMode();
    }
  }

  $effect(() => {
    return () => releaseWakeLock();
  });

  $effect(() => {
    if (demo || id == null) return;
    api
      .score(id)
      .then((s) => (score = s))
      .catch((e) => (error = String(e)));
  });

  // ------------------------------------------------------------------ #262
  // Editing a score that IS notation, rather than a transcription of one.
  //
  // A PDF is shown through ScoreCompare, which has always fetched a
  // transcription row and handed it to TabViewer as `tex`. A native MusicXML
  // score came straight here with no such fetch at all, so the editor had
  // nothing to save into and nothing to prefer over the file - it rendered
  // read-only. It now takes the same route a PDF's transcription does, with
  // one difference that is the whole of this feature: the row is not an
  // extraction of the file, it is an EDIT of it, and the file itself is never
  // written. So only a source='edited' row is preferred over the file here;
  // anything else (an extraction, which nothing can produce for a musicxml
  // score today) leaves the file showing.
  //
  // THE LOAD DECISION IS THIS ONE `tex` PROP. TabViewer's own source()
  // already chooses the transcription text over the file's bytes whenever
  // `tex` is non-null (that is how a PDF's staff pane has always worked), so
  // routing an edited musicxml score to its row is a matter of passing the
  // row rather than null - there is no second rendering path, and the edited
  // document goes through exactly the same importer the file did.
  let staffEdit = $state(null);
  // Where the lookup for THIS score has got to: "loading" until it lands, so
  // the panel never states which of the two documents is showing before it
  // knows, and "error" when it could not be answered at all. "error" is NOT
  // the 404 case - a score with no row is the ordinary state of nearly every
  // score, and it resolves to "file" - it is a lookup that FAILED, where "the
  // file is what is showing" would be a claim this page cannot make: an edited
  // row may well exist, and offering the editor over the file would let the
  // next save replace that row with a document its owner never saw.
  let staffEditState = $state("loading");
  let staffEditError = $state("");
  let revertingStaffEdit = $state(false);
  // Bumped by the retry button, and TRACKED by the lookup effect below - the
  // one thing besides the score id that re-runs it.
  let staffEditAttempt = $state(0);

  // Whose transcription row to look up - the score id when this score is drawn
  // by TabViewer from a library file, null otherwise. A $derived, not the
  // effect reading `score` directly: `score` is REASSIGNED by every patch on
  // this page (a tag, a tempo, a favourite), and an effect tracking the object
  // would refetch - and momentarily blow away - the edit being shown each time
  // somebody typed a tag. The id is a primitive, so the effect below re-runs
  // only when it actually changes.
  let staffScoreId = $derived(score && !demo && score.file_type === "musicxml" ? score.id : null);

  $effect(() => {
    const id = staffScoreId;
    // Tracked on purpose: pressing "Try again" re-runs this effect.
    void staffEditAttempt;
    let live = true;
    // NO REQUEST FOR A SCORE THE SERVER HAS ALREADY SAID HAS NO ROW.
    // `has_transcription` is on every ScoreOut (api._with_tags joins it for
    // list and detail alike), so the score object this page already holds
    // answers "is there anything to fetch" without asking. Fetching anyway and
    // reading the 404 was the obvious shape and the wrong one: the 404 is a
    // correct answer that Chromium logs as a console ERROR, and every spec in
    // this suite that asserts a clean console (navigation.spec.js,
    // score-multi-part.spec.js) went red on scores that had nothing to do with
    // this feature. The route's own 404 semantics are untouched; this is only
    // about not asking a question whose answer is already in hand.
    //
    // Read through untrack so a `score` reassignment (a tag, a favourite)
    // cannot re-run this effect - the id is what identifies the lookup, and
    // this flag is only ever consulted at the moment the lookup starts. It is
    // deliberately NOT kept in step with a save or a revert either: both set
    // `staffEdit` here directly, and a re-run mid-session would drop the
    // editor back to "loading" (and so out of `editable`) under the hands of
    // somebody who is typing frets into it.
    const mayHaveRow = untrack(() => id != null && score?.has_transcription === true);
    untrack(() => {
      staffEdit = null;
      staffEditError = "";
      staffEditState = mayHaveRow ? "loading" : "ready";
    });
    if (!mayHaveRow) return;
    api
      .transcription(id)
      .then((t) => {
        if (!live) return;
        // Only an edit is preferred over the file - see the note above.
        staffEdit = t?.source === "edited" ? t : null;
        staffEditState = "ready";
      })
      .catch((e) => {
        if (!live) return;
        staffEdit = null;
        if (e instanceof ApiError && e.status === 404) {
          // The row went away between the score being read and this asking for
          // it (a revert in another tab, a purge). Nothing to prefer over the
          // file, which is exactly what "file" means.
          staffEditState = "ready";
          return;
        }
        // ANY OTHER FAILURE IS NOT "THERE IS NO EDIT". Saying "showing the
        // file" here would state as fact the one thing this lookup failed to
        // establish, and - worse - would offer the editor seeded from the file,
        // so the next Save would overwrite a stored edit with a document that
        // never contained it. The panel says so and offers a retry instead,
        // and `editable` below stands down until the lookup has actually
        // landed.
        staffEditError = e?.message ?? "Could not check whether this score has an edit stored.";
        staffEditState = "error";
      });
    return () => {
      live = false;
    };
  });

  // Ask again after a lookup that could not be answered. The effect above is
  // the only thing that fetches, so this bumps its other dependency rather
  // than duplicating the request here - one code path, one set of states.
  function retryStaffEdit() {
    staffEditAttempt += 1;
  }

  // The note editor's save. Goes through the SAME PUT a PDF's hand edit uses
  // (stored verbatim as source='edited', never re-extracted); the library file
  // is not touched, and nothing here writes to it. Errors are left to
  // propagate: TabViewer's saveEdits() catches them and shows the message on
  // its own panel, which is where the person who pressed Save is looking.
  async function saveStaffEdit(content) {
    const res = await api.saveTranscription(score.id, content);
    staffEdit = { ...res, content, source: "edited" };
  }

  // Throw the edit away and go back to rendering the file. DELETE removes only
  // the edited row; for a native score there is no extraction underneath it, so
  // the server answers 404 ("no transcription for this score") - which here is
  // the SUCCESS case, not a failure: nothing left means the file is what shows.
  async function revertStaffEdit() {
    revertingStaffEdit = true;
    staffEditError = "";
    try {
      const t = await api.deleteTranscription(score.id);
      staffEdit = t?.source === "edited" ? t : null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        staffEdit = null;
      } else {
        staffEditError = e?.message ?? "Could not throw that edit away.";
      }
    } finally {
      revertingStaffEdit = false;
    }
  }

  const PRACTICE_MIN_SECONDS = 10;

  let practiceStart = $state(null);
  let practiceElapsed = $state(0);
  let practiceInterval;
  let practiceScoreId = null;

  // The session just logged, if it is still worth adding detail to. The length
  // is stored the moment the timer stops - see flushPractice - and this panel
  // patches that stored row afterwards. That order matters: a form standing
  // between a player and a stopped clock is a form that loses sessions, and a
  // session abandoned at the form would be a session that never happened.
  let lastSession = $state(null);
  let detail = $state(null);
  let savingDetail = $state(false);
  let detailError = $state("");
  // A session the server would not take. Shown until dismissed, because the
  // alternative is a stopwatch that ran and a record that does not mention it.
  let practiceError = $state("");

  function blankDetail() {
    return {
      rating: null,
      mode: "",
      from_bar: "",
      to_bar: "",
      tempo_bpm: "",
      target_tempo_bpm: "",
      note: "",
    };
  }

  const RATING_LABELS = {
    1: "rough",
    2: "getting there",
    3: "steady",
    4: "solid",
    5: "as I want it",
  };

  /** The day a session is filed under: the BROWSER's calendar day at the
   * moment the timer STARTED.
   *
   * The start and not the stop. A session from 23:40 to 00:20 is practice done
   * on the earlier day - that is when they sat down - and taking the day from
   * the clock at flush time filed the whole thing on the following one, which
   * at a week boundary counted it towards the next week's goal rather than the
   * one it was practised for. This is the single field the entire feature
   * counts, so which day it picks is chosen rather than incidental.
   *
   * Local and not UTC for the same reason: the server stores UTC timestamps,
   * and west of Greenwich the UTC date of an evening session is already
   * tomorrow. */
  function practiceDay(startedAt) {
    const when = new Date(startedAt ?? Date.now());
    const pad = (n) => String(n).padStart(2, "0");
    return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`;
  }

  function numberOrNull(value) {
    // An empty field means UNSET, and never zero. Svelte's bind:value on a
    // number input hands back null when the box is cleared, and Number(null)
    // is 0 - so clearing a tempo sent 0, which the server refuses, and the
    // whole save failed with a message about a field the person had just
    // emptied. Same shape of bug as the reference-pitch one in the instruments
    // editor, and the same rule fixes it.
    if (value === "" || value == null) return null;
    const n = Number(value);
    // Rounded, because the server takes whole numbers and nothing that merely
    // converts to one - a number input hands back "76.5" if it is typed.
    return Number.isNaN(n) ? null : Math.round(n);
  }

  async function saveDetail() {
    if (!lastSession) return;
    savingDetail = true;
    detailError = "";
    try {
      await api.patchSession(lastSession.id, {
        rating: detail.rating,
        mode: detail.mode || null,
        from_bar: numberOrNull(detail.from_bar),
        to_bar: numberOrNull(detail.to_bar),
        tempo_bpm: numberOrNull(detail.tempo_bpm),
        target_tempo_bpm: numberOrNull(detail.target_tempo_bpm),
        note: detail.note.trim() || null,
      });
      lastSession = null;
      detail = null;
    } catch (e) {
      detailError = e?.message ?? "Could not save that.";
    } finally {
      savingDetail = false;
    }
  }

  function dismissDetail() {
    // The session itself stays. Closing this only declines to say more about
    // it, which is the ordinary case and must never read as losing the
    // practice.
    lastSession = null;
    detail = null;
    detailError = "";
  }

  function formatElapsed(sec) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function startPractice() {
    if (!score) return;
    practiceScoreId = score.id;
    practiceStart = Date.now();
    practiceElapsed = 0;
    practiceInterval = setInterval(() => {
      practiceElapsed = Math.floor((Date.now() - practiceStart) / 1000);
    }, 1000);
  }

  // gig mode hides the header (and the timer button in it), but a running
  // session must stay visible and reachable rather than silently ticking
  // away off-screen - the gig HUDs in both viewers surface this
  let practiceLabel = $derived(practiceStart != null ? formatElapsed(practiceElapsed) : null);

  /** Store a stopped session.
   *
   * `leaving` is true when the page itself is going away. A normal fetch is
   * routinely CANCELLED by the browser once a page starts unloading, which
   * would silently drop the session - and this is the write path for the data
   * every goal is counted from, so losing one is losing part of somebody's
   * record. sendBeacon exists for exactly this: the browser takes ownership of
   * the request and completes it after the page is gone. Nothing can be read
   * back from it, so the detail panel is not offered in that case; the session
   * is stored either way, which is the part that matters.
   */
  function storePractice(scoreId, body, leaving) {
    const url = `/api/scores/${scoreId}/practice`;
    if (leaving && navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([JSON.stringify(body)], { type: "application/json" }));
      return;
    }
    api
      .logPractice(scoreId, body)
      .then((result) => {
        // Only offer the detail panel for the score still on screen. A flush
        // triggered by navigating to another score has nowhere to show one,
        // and the session is already stored either way.
        if (result?.session && score?.id === scoreId) {
          lastSession = result.session;
          detail = blankDetail();
        }
      })
      .catch((e) => {
        // Said out loud rather than swallowed. A timer that appears to stop and
        // stores nothing is the worst failure this feature has, because nothing
        // else in the app would ever show the gap.
        practiceError =
          `That session (${formatDuration(body.seconds)}) could not be saved: ` +
          `${e?.message ?? "the server did not answer"}.`;
      });
  }

  /** Stop the timer and store what it measured.
   *
   * `leaving` says the page itself is going away, which changes HOW the write
   * is sent - see storePractice. Every caller therefore has to pass it
   * deliberately: wired straight to an onclick, the DOM event arrives here as
   * the first argument and every ordinary stop took the page-unload path,
   * which cannot read the response back and so never offered the detail panel.
   */
  function flushPractice(leaving = false) {
    if (practiceStart == null) return;
    const startedAt = practiceStart;
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    const scoreId = practiceScoreId;
    clearInterval(practiceInterval);
    practiceStart = null;
    practiceElapsed = 0;
    practiceScoreId = null;
    if (seconds >= PRACTICE_MIN_SECONDS && scoreId != null) {
      practiceError = "";
      storePractice(
        scoreId,
        { seconds, activity: "piece", local_date: practiceDay(startedAt) },
        leaving,
      );
    }
  }

  // Flushes on switching to a different score too: the route swaps `id` on
  // this same component instance rather than remounting it.
  //
  // The detail panel is dismissed at the same time, and that is a correctness
  // fix rather than tidying: it survived the navigation, so a rating or a note
  // typed into it afterwards was PATCHed onto the previous score's session -
  // writing an opinion against practice that did not happen. The session it
  // belonged to is already stored; only the offer to say more about it ends.
  //
  // tempoError is cleared here for the same reason (#8 review): it is a
  // statement about the PREVIOUS score's tempo control ("500 was refused"),
  // and surviving the navigation left it sitting over a different score's
  // empty tempo box, describing a refusal that never happened there.
  $effect(() => {
    void id;
    return () => {
      flushPractice();
      dismissDetail();
      tempoError = "";
    };
  });

  $effect(() => {
    const onLeave = () => flushPractice(true);
    window.addEventListener("beforeunload", onLeave);
    // pagehide as well as beforeunload: a mobile browser backgrounding a tab
    // fires only this one, and a timer left running on a phone is an ordinary
    // way for a session to end.
    window.addEventListener("pagehide", onLeave);
    return () => {
      window.removeEventListener("beforeunload", onLeave);
      window.removeEventListener("pagehide", onLeave);
    };
  });

  async function setKind(ev) {
    score = await api.patch(score.id, { content_kind: ev.target.value });
  }

  // #8: the server's own closed ranges, mirrored the same way setKind's
  // <select> above already mirrors VALID_KINDS - see api.MIN_KEY_FIFTHS /
  // MAX_KEY_FIFTHS / MIN_DIFFICULTY / MAX_DIFFICULTY.
  //
  // keySignatureLabel(0) reads "no key signature" - true of the value, but
  // beside "Key: unset" it is one more way of saying the box is empty rather
  // than the exact, real value it is (fifths = 0). Same disambiguation
  // Library.svelte's own key-filter options need, for the same reason.
  function keyOptionLabel(fifths) {
    return fifths === 0 ? "No sharps or flats (0)" : keySignatureLabel(fifths);
  }

  const KEY_OPTIONS = [
    ["", "Key: unset"],
    ...Array.from({ length: 15 }, (_, i) => i - 7).map((fifths) => [
      String(fifths),
      keyOptionLabel(fifths),
    ]),
  ];
  const DIFFICULTY_OPTIONS = [
    ["", "Difficulty: unset"],
    ...[1, 2, 3, 4, 5].map((n) => [String(n), "★".repeat(n) + "☆".repeat(5 - n)]),
  ];

  // Every one of these three is independently clearable (an empty select /
  // input sends `null`, not 0 or "") - see ScorePatch on the server, where an
  // explicit null is the one way a wrong hand entry, or a key
  // _store_extraction_result filled in on its own, comes off again.
  async function setKey(ev) {
    const v = ev.target.value;
    score = await api.patch(score.id, { key: v === "" ? null : Number(v) });
  }

  async function setDifficulty(ev) {
    const v = ev.target.value;
    score = await api.patch(score.id, { difficulty: v === "" ? null : Number(v) });
  }

  // Mirrors api.MIN_TEMPO_BPM / MAX_TEMPO_BPM (20-400) - the same bounds
  // practice.MIN_TEMPO_BPM / MAX_TEMPO_BPM already uses, and the same range
  // the input's own min/max attributes below advertise. A number input's
  // min/max are advice, not enforcement: typing 500 leaves the box reading
  // 500 regardless of what the attribute says, so this is checked here too,
  // before anything is sent - the same wording pattern Instruments.svelte's
  // reference-pitch check uses.
  const MIN_TEMPO_BPM = 20;
  const MAX_TEMPO_BPM = 400;

  async function setTempo(ev) {
    // numberOrNull rounds - typing 76.5 saves as 77, the same rounding a
    // practice session's own tempo_bpm field already accepts silently
    // (saveDetail, above) rather than refusing a value the server would
    // otherwise reject outright for not being a whole number.
    const value = numberOrNull(ev.target.value);
    if (value !== null && (value < MIN_TEMPO_BPM || value > MAX_TEMPO_BPM)) {
      // Refused before it ever reaches the network, and the box is put back
      // to what the server actually holds - a rejected value must not sit in
      // the control looking saved. ev.target.value directly, not `score`:
      // this input is not bound to `score.tempo`, so reassigning `score` to
      // itself would not touch what the browser is showing.
      tempoError = `Tempo must be between ${MIN_TEMPO_BPM} and ${MAX_TEMPO_BPM} - it has not been changed.`;
      ev.target.value = score.tempo ?? "";
      return;
    }
    try {
      score = await api.patch(score.id, { tempo: value });
      tempoError = "";
    } catch (e) {
      // A rejection reaching here despite the check above means the server's
      // own bounds disagree with the ones mirrored here - report it exactly
      // like any other failed patch, rather than letting it surface as an
      // unhandled promise rejection, and put the box back the same way.
      tempoError = e?.message ?? "Could not save that.";
      ev.target.value = score.tempo ?? "";
    }
  }

  async function toggleFavorite() {
    score = await api.patch(score.id, { favorite: !score.favorite });
  }

  function startTagEdit() {
    tagsDraft = score.tags.join(", ");
    editingTags = true;
  }

  async function saveTags() {
    score = await api.patch(score.id, {
      tags: tagsDraft.split(",").map((t) => t.trim()).filter(Boolean),
    });
    editingTags = false;
  }
</script>

<svelte:window onkeydown={onKey} onfullscreenchange={onFullscreenChange} onvisibilitychange={onVisibilityChange} />

<div class="viewer" bind:this={viewerEl}>
  {#if !gigMode}
    <header>
      <a class="back" href="#/">← Library</a>
      {#if demo}
        <div class="titles">
          <span class="title">Notation & Tab Demo</span>
          <span class="sub">bundled sample</span>
        </div>
      {:else if score}
        <div class="titles">
          <span class="title">{score.title}</span>
          <span class="sub">
            {[score.composer, score.source].filter(Boolean).join(" · ")}
          </span>
        </div>
        <div class="controls">
          {#if editingTags}
            <input
              class="tags-input"
              bind:value={tagsDraft}
              placeholder="tag, another tag"
              onkeydown={(e) => e.key === "Enter" && saveTags()}
            />
            <button onclick={saveTags}>Save</button>
          {:else}
            <button class="ghost" onclick={startTagEdit}>
              {score.tags.length ? score.tags.join(" · ") : "+ tags"}
            </button>
          {/if}
          <select value={score.content_kind} onchange={setKind} title="Content type">
            <option value="unknown">unsorted</option>
            <option value="notation">notation</option>
            <option value="tab">tab</option>
            <option value="both">notation + tab</option>
          </select>
          <select
            class="key-select"
            value={score.key === null || score.key === undefined ? "" : String(score.key)}
            onchange={setKey}
            title="Key signature - filled in from a transcription's decoded key when one is transcribed, or set by hand"
          >
            {#each KEY_OPTIONS as [value, label]}
              <option {value}>{label}</option>
            {/each}
          </select>
          <select
            class="difficulty-select"
            value={score.difficulty === null || score.difficulty === undefined ? "" : String(score.difficulty)}
            onchange={setDifficulty}
            title="How hard this piece is - nothing here infers one, so it is always set by hand"
          >
            {#each DIFFICULTY_OPTIONS as [value, label]}
              <option {value}>{label}</option>
            {/each}
          </select>
          <input
            class="tempo-input"
            type="number"
            min={MIN_TEMPO_BPM}
            max={MAX_TEMPO_BPM}
            placeholder="bpm"
            value={score.tempo ?? ""}
            onchange={setTempo}
            title="Tempo (manual - the decoder's own reading has no confidence figure to trust)"
          />
          <button
            class="ghost timer"
            class:on={practiceStart != null}
            onclick={() => (practiceStart != null ? flushPractice() : startPractice())}
            title={practiceStart != null ? "Stop practice timer" : "Start practice timer"}
          >
            {practiceStart != null ? `■ ${formatElapsed(practiceElapsed)}` : "▶ Practice"}
          </button>
          <!-- Where the time this button records ends up. Issue #57 asks for
               this view to be reachable from the score itself as well as from
               a place of its own, and beside the timer is where somebody asks
               the question - "how is this one going" comes up when you have
               just sat down with it, not while browsing a library. -->
          <a
            class="ghost history-link"
            href={"#/score/" + score.id + "/practice"}
            title="How this piece is going: the time, the days, the tempo, and your notes"
          >
            ◴ History
          </a>
          <button class="ghost fav" class:on={score.favorite} onclick={toggleFavorite}>★</button>
          <button class="ghost" onclick={enterGigMode} title="Distraction-free performance view (F)">
            ⛶ Gig mode
          </button>
        </div>
      {/if}
    </header>
  {/if}

  {#if practiceError && !gigMode}
    <p class="practice-error" role="status">
      {practiceError}
      <button class="ghost" onclick={() => (practiceError = "")}>Dismiss</button>
    </p>
  {/if}

  {#if tempoError && !gigMode}
    <p class="tempo-error" role="status">
      {tempoError}
      <button class="ghost" onclick={() => (tempoError = "")}>Dismiss</button>
    </p>
  {/if}

  {#if lastSession && detail && !gigMode}
    <!-- What that session was, in the player's own words. Every field is
         optional and closing the panel keeps the session exactly as logged -
         this asks for detail, it does not require it. -->
    <section class="session-detail" data-session={lastSession.id}>
      <div class="detail-head">
        <span class="detail-title">
          Logged {formatDuration(lastSession.seconds)} on {lastSession.local_date}
        </span>
        <button class="ghost close-detail" onclick={dismissDetail} title="Close">✕</button>
      </div>

      <div class="detail-row">
        <span class="detail-label">How it went</span>
        {#each [1, 2, 3, 4, 5] as value}
          <button
            class="rating"
            class:on={detail.rating === value}
            data-rating={value}
            title={RATING_LABELS[value]}
            onclick={() => (detail.rating = detail.rating === value ? null : value)}
          >
            {value}
          </button>
        {/each}
        <span class="detail-hint">
          {detail.rating ? RATING_LABELS[detail.rating] : "optional"}
        </span>
      </div>

      <div class="detail-row">
        <select bind:value={detail.mode} class="detail-mode" title="What kind of work">
          <option value="">unstated</option>
          <option value="section">section work</option>
          <option value="run_through">run-through</option>
        </select>
        <span class="detail-label">bars</span>
        <input class="detail-bar" type="number" min="1" placeholder="from" bind:value={detail.from_bar} />
        <input class="detail-bar" type="number" min="1" placeholder="to" bind:value={detail.to_bar} />
        <span class="detail-label">tempo</span>
        <input class="detail-tempo" type="number" min="20" max="400" placeholder="bpm" bind:value={detail.tempo_bpm} />
        <span class="detail-label">aiming at</span>
        <input
          class="detail-target-tempo"
          type="number"
          min="20"
          max="400"
          placeholder="bpm"
          bind:value={detail.target_tempo_bpm}
        />
      </div>

      <div class="detail-row">
        <input
          class="detail-note"
          type="text"
          maxlength="2000"
          placeholder="what to pick up next time"
          bind:value={detail.note}
        />
        <button class="save-detail" onclick={saveDetail} disabled={savingDetail}>
          {savingDetail ? "Saving…" : "Save"}
        </button>
      </div>

      {#if detailError}
        <p class="detail-hint">{detailError}</p>
      {/if}
    </section>
  {/if}

  {#if error}
    <p class="error">{error}</p>
  {:else if demo}
    <TabViewer demo={true} {gigMode} onToggleGig={toggleGigMode} />
  {:else if score}
    {#if score.file_type === "pdf"}
      <ScoreCompare {score} {gigMode} onToggleGig={toggleGigMode} {practiceLabel} onStopPractice={() => flushPractice()} />
    {:else}
      <!-- Which of the two is on screen, stated rather than left to be
           inferred from the notes (#262) - the same job the transcription
           panel's provenance line does for extracted-versus-edited on a PDF.
           Stated in BOTH directions on purpose: a line that only ever appeared
           when an edit was showing would leave "no line" meaning both "the
           file" and "not looked up yet", and those are different claims. Gig
           mode drops it with the rest of the chrome. -->
      {#if !gigMode && staffScoreId != null && staffEditState !== "loading"}
        <div class="staff-source" data-staff-source={staffEditState === "error" ? "unknown" : staffEdit ? "edited" : "file"}>
          {#if staffEditState === "error"}
            <!-- The one state that must not be dressed up as either of the
                 other two. The staff below is drawing the file (that is all
                 there was to draw), but this page does not KNOW that is the
                 right document, so it does not say so - and the editor stays
                 shut, because a save from it would overwrite whatever the
                 lookup failed to read. -->
            <span class="staff-source-what">Could not tell whether this score has an edit stored.</span>
            <span class="staff-source-error">{staffEditError}</span>
            <button class="staff-source-retry" onclick={retryStaffEdit}>Try again</button>
          {:else if staffEdit}
            <span class="staff-source-what">Showing your edit of this score.</span>
            <span class="staff-source-why">
              The file in your library is untouched — the edit is stored alongside it.
            </span>
            <button class="staff-source-revert" onclick={revertStaffEdit} disabled={revertingStaffEdit}>
              {revertingStaffEdit ? "Throwing it away…" : "Revert to the file"}
            </button>
            {#if staffEditError}
              <span class="staff-source-error">{staffEditError}</span>
            {/if}
          {:else}
            <span class="staff-source-what">Showing the file as it is in your library.</span>
            {#if staffEditError}
              <span class="staff-source-error">{staffEditError}</span>
            {/if}
          {/if}
        </div>
      {/if}
      <TabViewer
        {score}
        tex={staffEdit?.content ?? null}
        format={staffEdit?.format ?? "musicxml"}
        staffSource={staffEdit ? "edited" : "file"}
        editable={staffScoreId != null && staffEditState === "ready"}
        onSaveEdit={staffScoreId != null ? saveStaffEdit : null}
        {gigMode}
        onToggleGig={toggleGigMode}
        {practiceLabel}
        onStopPractice={() => flushPractice()}
      />
    {/if}
  {/if}
</div>

<style>
  .viewer {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }

  header {
    display: flex;
    /* #8's three new controls (key, difficulty, tempo) are what pushed this
       row past what a tablet's portrait width can hold without something
       being squeezed or pushed off the page - the same failure TabViewer's
       .toolbar had (issue #106), fixed the same way: WRAP, never shrink.
       Once .titles and .controls no longer fit side by side, .controls
       drops to its own line below rather than either one being squeezed
       toward unreadable or pushed past the viewport's right edge with no
       horizontal scrollbar to reach it by. */
    flex-wrap: wrap;
    align-items: center;
    gap: 16px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
  }

  .back {
    color: var(--ink-dim);
    white-space: nowrap;
  }

  .back:hover {
    color: var(--brass-bright);
  }

  .titles {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
    flex: 1;
  }

  .title {
    font-family: var(--font-display);
    font-size: 18px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .sub {
    color: var(--ink-dim);
    font-size: 13px;
    white-space: nowrap;
  }

  .controls {
    display: flex;
    /* Its own second (or third) line once even a full-width row below
       .titles is not enough - see the header rule above for why wrap and
       not shrink. */
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .tempo-input {
    width: 4.5em;
  }

  .ghost {
    background: none;
    border-color: transparent;
    color: var(--ink-dim);
  }

  /* A link, not a button, because it goes somewhere - but it sits in a row of
     buttons and has to be the same size and shape as them or the toolbar
     develops a step in it. app.css styles `button, select, input` and an
     anchor is none of the three, so the box it would have inherited is
     restated here. */
  .history-link {
    font-family: var(--font-ui);
    font-size: 14px;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px 12px;
    white-space: nowrap;
  }

  .ghost:hover {
    border-color: var(--line);
    color: var(--ink);
  }

  .fav.on {
    color: var(--brass-bright);
  }

  .timer {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .timer.on {
    color: var(--brass-bright);
    border-color: var(--brass);
  }

  .tags-input {
    width: 220px;
  }

  /* Not styled as an alert of any kind: nothing here went wrong, a session was
     recorded. It sits under the header rather than over the score so it never
     covers the music. */
  .session-detail {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px 16px 12px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
  }

  .detail-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .detail-title {
    font-size: 14px;
    color: var(--brass-bright);
    font-variant-numeric: tabular-nums;
  }

  .detail-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .detail-label,
  .detail-hint {
    font-size: 13px;
    color: var(--ink-dim);
  }

  .rating {
    width: 32px;
    font-variant-numeric: tabular-nums;
  }

  .rating.on {
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  .detail-bar,
  .detail-tempo,
  .detail-target-tempo {
    width: 76px;
  }

  /* This one IS a failure - a session that did not save - so unlike anything on
     the practice page it is allowed to look like one. */
  .practice-error,
  .tempo-error {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 0;
    padding: 8px 16px;
    font-size: 14px;
    color: var(--danger);
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
  }

  .detail-note {
    flex: 1;
    min-width: 200px;
  }

  .error {
    color: var(--danger);
    text-align: center;
    margin-top: 60px;
  }

  /* One quiet line between the header and the staff saying which of the two
     documents is drawn (#262). flex-shrink: 0 so it never gives up its own
     height to the staff below it inside .viewer's column - a statement about
     what you are looking at that can be squeezed to nothing is worse than no
     statement. It WRAPS instead, the same way the header does at a tablet's
     portrait width (issue #106). */
  .staff-source {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 8px;
    flex-shrink: 0;
    padding: 6px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg-raised);
    font-size: 13px;
  }

  .staff-source-what {
    color: var(--ink);
  }

  .staff-source-why {
    color: var(--ink-dim);
  }

  .staff-source-error {
    color: var(--danger);
  }

  /* Unstyled beyond the app's own button rules - it is offered next to the
     failure it undoes, not competing with the score for attention. */
  .staff-source-retry {
    font-size: 13px;
  }
</style>
