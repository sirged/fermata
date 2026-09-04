<script>
  import { api } from "./api.js";
  import { formatDuration } from "./practice.js";
  import { keySignatureLabel } from "./provenance.js";
  import PdfViewer from "./PdfViewer.svelte";
  import TabViewer from "./TabViewer.svelte";
  import ScoreCompare from "./ScoreCompare.svelte";

  let { id = null, demo = false } = $props();

  let score = $state(null);
  let error = $state("");
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
  $effect(() => {
    void id;
    return () => {
      flushPractice();
      dismissDetail();
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
  const KEY_OPTIONS = [
    ["", "Key: unset"],
    ...Array.from({ length: 15 }, (_, i) => i - 7).map((fifths) => [
      String(fifths),
      keySignatureLabel(fifths),
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

  async function setTempo(ev) {
    const v = ev.target.value.trim();
    score = await api.patch(score.id, { tempo: v === "" ? null : Number(v) });
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
            min="20"
            max="400"
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
      <TabViewer {score} {gigMode} onToggleGig={toggleGigMode} {practiceLabel} onStopPractice={() => flushPractice()} />
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
  .practice-error {
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
</style>
