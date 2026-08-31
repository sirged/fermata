<script>
  // Fret to note, both directions (issue #27) - the first drill built on the
  // neck component (#25). A real drill loop: prompt, answer, feedback, next.
  //
  // WHAT MAKES THIS ONE HONEST, following EarTraining.svelte's own lead:
  //
  //   Nothing here grades anybody. Two counts, stated - no percentage, no
  //   streak. A wrong answer is the practice, not a shortfall: the position
  //   or the note is stated plainly either way, in the same place and the
  //   same style. See fret-to-note.js's answerStatement and the tone rules
  //   at the top of that file.
  //
  //   The tapped position (not the note a player MEANT to tap) decides a
  //   note-to-position answer - mirrors ear-training.js's own rule that the
  //   question is built from what actually happened, not from intent.
  //
  //   Every question is a structured row (POST /api/trainer/attempts), not
  //   only a count folded into the session's note - issue #32's promise for
  //   this drill. The session logged at the end still carries a human-
  //   readable summary in its own `note`, the same as every other activity.
  import { onDestroy } from "svelte";

  import { api } from "../api.js";
  import { getInstruments, loadInstruments } from "../instruments.svelte.js";
  import { localDay } from "../practice.js";
  import Neck from "./Neck.svelte";
  import {
    DEFAULT_FRET_COUNT,
    PITCH_CLASSES,
    fretCountFromInstrument,
    stringsFromInstrument,
  } from "./neck.js";
  import {
    NOTE_TO_POSITION,
    POSITION_TO_NOTE,
    answerStatement,
    attemptPayload,
    checkPositionAnswer,
    checkTapAnswer,
    loggedStatement,
    pickQuestion,
    progressStatement,
    scopeIsAskable,
    sessionNote,
  } from "./fret-to-note.js";

  const instruments = getInstruments();

  $effect(() => {
    loadInstruments();
  });

  // Which instrument's tuning to draw the neck from: "" is the standard
  // six-string fallback, otherwise an instrument's id as a string. Adopted
  // automatically only when exactly one instrument is defined - the same
  // rule EarTraining.svelte follows and for the same reason: with more than
  // one, which is in somebody's hands is not knowable, so guessing would be
  // worse than a plain default that says what it is.
  let source = $state("");
  let sourceSettled = false;
  $effect(() => {
    if (!instruments.loaded || sourceSettled) return;
    sourceSettled = true;
    if (instruments.list.length === 1) source = String(instruments.list[0].id);
  });

  const instrument = $derived(instruments.list.find((i) => String(i.id) === source) ?? null);
  const strings = $derived(stringsFromInstrument(instrument));
  const fretCount = $derived(fretCountFromInstrument(instrument));

  let direction = $state(POSITION_TO_NOTE);

  // Scope: which strings and which frets a question may be drawn from. Both
  // default to "everything the current tuning offers" and stay there until a
  // person touches the controls - see the two effects below - which is what
  // lets a fret-range default follow a newly picked instrument's own fret
  // count rather than freezing at whatever the six-string fallback offered.
  let customizedStrings = false;
  let selectedStrings = $state([]);
  $effect(() => {
    if (customizedStrings) return;
    selectedStrings = strings.map((s) => s.number);
  });

  let customizedFretRange = false;
  let startFret = $state(0);
  let endFret = $state(DEFAULT_FRET_COUNT);
  $effect(() => {
    if (customizedFretRange) return;
    startFret = 0;
    endFret = fretCount;
  });

  function toggleString(number) {
    customizedStrings = true;
    if (selectedStrings.includes(number)) {
      // At least one string must stay selected - an empty list does not mean
      // "nothing asked", it means "no filter at all" (see fret-to-note.js's
      // scopePositions), so silently allowing zero would make the last
      // string's checkbox appear to narrow the drill to nothing while
      // actually widening it back to everything.
      if (selectedStrings.length <= 1) return;
      selectedStrings = selectedStrings.filter((n) => n !== number);
    } else {
      selectedStrings = [...selectedStrings, number];
    }
  }

  function setStartFret(value) {
    customizedFretRange = true;
    startFret = Number(value);
  }

  function setEndFret(value) {
    customizedFretRange = true;
    endFret = Number(value);
  }

  const scope = $derived({ startFret, endFret, stringNumbers: selectedStrings });
  const askable = $derived(scopeIsAskable(strings, scope));

  let running = $state(false);
  let asked = $state(0);
  let correctCount = $state(0);
  // { question, given, correct }. `given` is null until answered - see
  // chooseNote/tapPosition. `question.string`/`.fret` are set only for a
  // position-to-note question; `question.note` is always set.
  let round = $state(null);
  let questionStartedAt = 0;
  let attemptLogFailures = $state(0);

  let startedAt = 0;
  let logged = $state(null);
  let logError = $state("");
  let logging = $state(false);
  let unlogged = $state(null);

  function elapsed() {
    return Math.max(1, Math.round((Date.now() - startedAt) / 1000));
  }

  function drillNote() {
    return sessionNote({ asked, correct: correctCount, direction, strings, scope });
  }

  function nextQuestion() {
    const previous = round?.question ?? null;
    const question = pickQuestion(strings, scope, direction, previous);
    round = question ? { question, given: null, correct: null } : null;
    questionStartedAt = Date.now();
  }

  async function recordAttempt(question, given, correct) {
    const responseMs = Math.max(0, Date.now() - questionStartedAt);
    try {
      await api.logTrainerAttempt(attemptPayload({ question, given, responseMs }));
    } catch {
      // Best-effort: the drill's own counts (asked/correctCount, already
      // updated) and its session log are the record that must not be lost.
      // A failed per-question POST is noted quietly rather than interrupting
      // the loop a person is mid-question in.
      attemptLogFailures += 1;
    }
  }

  function chooseNote(note) {
    if (!round || round.given != null || round.question.direction !== POSITION_TO_NOTE) return;
    const correct = checkPositionAnswer(round.question, note);
    const given = { note };
    round = { ...round, given, correct };
    asked += 1;
    if (correct) correctCount += 1;
    recordAttempt(round.question, given, correct);
  }

  function tapPosition(stringNumber, fret) {
    if (!round || round.given != null || round.question.direction !== NOTE_TO_POSITION) return;
    const { correct, note } = checkTapAnswer(strings, round.question, stringNumber, fret);
    const given = { string: stringNumber, fret, note };
    round = { ...round, given, correct };
    asked += 1;
    if (correct) correctCount += 1;
    recordAttempt(round.question, given, correct);
  }

  function neckMarkers() {
    if (!round) return [];
    const { question, given, correct } = round;
    if (question.direction === POSITION_TO_NOTE) {
      const kind = given == null ? "target" : correct ? "correct" : "incorrect";
      return [{ string: question.string, fret: question.fret, kind }];
    }
    if (given == null) return [];
    return [{ string: given.string, fret: given.fret, kind: correct ? "correct" : "incorrect" }];
  }

  // Once a note-to-position question is answered, every OTHER place that
  // note sounds is revealed too - the neck's own "highlight a note across
  // the neck" primitive (Neck.svelte's highlightNote prop), reused rather
  // than this component recomputing positions by hand. This is what
  // "reveal the answer" (issue #27) means on this direction: not only right
  // or wrong, but where the note actually is.
  const highlightNote = $derived(
    round?.question?.direction === NOTE_TO_POSITION && round.given != null
      ? round.question.note
      : null,
  );

  const neckInteractive = $derived(
    running && round != null && round.given == null && direction === NOTE_TO_POSITION,
  );

  function start() {
    if (!askable) return;
    running = true;
    startedAt = Date.now();
    asked = 0;
    correctCount = 0;
    attemptLogFailures = 0;
    logged = null;
    logError = "";
    unlogged = null;
    nextQuestion();
  }

  async function stopAndLog() {
    if (!running) return;
    const seconds = elapsed();
    const totalAsked = asked;
    running = false;
    round = null;
    // Nothing was ever asked, so nothing was practised - a start immediately
    // stopped is a mis-click, not a session worth a row.
    if (!totalAsked) return;
    await send(seconds);
  }

  async function send(seconds) {
    logging = true;
    logError = "";
    try {
      const session = await api.logSession({
        activity: "fretboard",
        seconds,
        local_date: localDay(),
        note: drillNote(),
      });
      logged = { seconds: session?.seconds ?? seconds, id: session?.id ?? null };
      unlogged = null;
    } catch (e) {
      logError = e?.message || "That practice could not be logged.";
      unlogged = seconds;
    } finally {
      logging = false;
    }
  }

  function retryLog() {
    if (unlogged == null) return;
    send(unlogged);
  }

  // Leaving mid-drill still logs the practice - see EarTraining.svelte's
  // identical teardown and the comment there on why: a hash route change
  // unmounts this component, and practice history cannot be regenerated.
  onDestroy(() => {
    if (!running || asked < 1) return;
    api
      .logSession({
        activity: "fretboard",
        seconds: elapsed(),
        local_date: localDay(),
        note: drillNote(),
      })
      .catch(() => {});
  });
</script>

<div class="page">
  <header>
    <a class="back" href="#/">← Library</a>
    <h1>Fret to note</h1>
  </header>

  <main>
    <section
      class="drill"
      data-running={running ? "1" : "0"}
      data-asked={asked}
      data-correct={correctCount}
      data-direction={direction}
      data-askable={askable ? "1" : "0"}
      data-question-note={round?.question?.note ?? ""}
      data-question-string={round?.question?.string ?? ""}
      data-question-fret={round?.question?.fret ?? ""}
      data-given-note={round?.given?.note ?? ""}
      data-attempt-log-failures={attemptLogFailures}
    >
      <p class="hint">
        Shown a position, name its note - or shown a note, tap where it lives on the neck. Both
        directions are different skills, so pick the one to practise.
      </p>

      <!-- ------------------------------------------------------ direction -->
      <div class="row direction-row" role="group" aria-label="Direction">
        <button
          class="direction-choice"
          class:active={direction === POSITION_TO_NOTE}
          disabled={running}
          onclick={() => (direction = POSITION_TO_NOTE)}
        >
          Position → note
        </button>
        <button
          class="direction-choice"
          class:active={direction === NOTE_TO_POSITION}
          disabled={running}
          onclick={() => (direction = NOTE_TO_POSITION)}
        >
          Note → position
        </button>
      </div>

      <!-- ------------------------------------------------------ source & scope -->
      <div class="row scope-row">
        <label>
          <span>Tuning</span>
          <select class="scope-source" bind:value={source} disabled={running}>
            <option value="">Standard guitar</option>
            {#each instruments.list as owned (owned.id)}
              <option value={String(owned.id)}>{owned.name}</option>
            {/each}
          </select>
        </label>
        <label>
          <span>Frets</span>
          <select
            class="scope-start-fret"
            value={startFret}
            disabled={running}
            onchange={(e) => setStartFret(e.currentTarget.value)}
          >
            {#each Array.from({ length: fretCount + 1 }, (_, f) => f) as f (f)}
              <option value={f}>{f}</option>
            {/each}
          </select>
          <span>to</span>
          <select
            class="scope-end-fret"
            value={endFret}
            disabled={running}
            onchange={(e) => setEndFret(e.currentTarget.value)}
          >
            {#each Array.from({ length: fretCount + 1 }, (_, f) => f) as f (f)}
              <option value={f}>{f}</option>
            {/each}
          </select>
        </label>
      </div>

      <div class="row strings-row" role="group" aria-label="Strings">
        {#each [...strings].sort((a, b) => b.number - a.number) as string (string.number)}
          <button
            class="string-choice"
            class:active={selectedStrings.includes(string.number)}
            disabled={running}
            onclick={() => toggleString(string.number)}
          >
            {string.number}
          </button>
        {/each}
      </div>

      {#if instruments.error}
        <p class="notice instruments-error">
          {instruments.error} The drill below uses the standard guitar instead.
        </p>
      {/if}

      {#if !askable}
        <p class="statement narrow-scope">
          Nothing is selected to ask about - choose at least one fret in range.
        </p>
      {/if}

      <!-- ------------------------------------------------------- the drill -->
      {#if running && round}
        <p class="statement progress">{progressStatement({ asked, correct: correctCount })}</p>

        {#if direction === NOTE_TO_POSITION}
          <p class="statement prompt">
            Find <strong>{round.question.note}</strong> on the neck.
          </p>
        {:else}
          <p class="statement prompt">Name the highlighted note.</p>
        {/if}

        <Neck
          {strings}
          {fretCount}
          startFret={0}
          markers={neckMarkers()}
          highlightNote={highlightNote}
          interactive={neckInteractive}
          onTap={tapPosition}
        />

        <div class="statement-slot" role="status" aria-live="polite">
          {#if round.given != null}
            <p class="statement answer-statement">
              {answerStatement(round.question, round.given, round.correct)}
            </p>
          {/if}
        </div>

        {#if direction === POSITION_TO_NOTE}
          <ul class="choices">
            {#each PITCH_CLASSES as note (note)}
              <li>
                <button
                  class="choice"
                  class:correct={round.given != null && note === round.question.note}
                  class:picked={round.given?.note === note && note !== round.question.note}
                  data-note={note}
                  disabled={round.given != null}
                  onclick={() => chooseNote(note)}
                >
                  {note}
                </button>
              </li>
            {/each}
          </ul>
        {/if}

        <div class="row controls">
          {#if round.given != null}
            <button class="primary next-question" onclick={nextQuestion}>Next question</button>
          {/if}
          <button class="ghost stop-drill" onclick={stopAndLog} disabled={logging}>
            {logging ? "Logging…" : "Stop and log this practice"}
          </button>
        </div>
      {:else if running && !round}
        <p class="statement narrow-scope">
          Nothing is selected to ask about - choose at least one fret in range.
        </p>
        <div class="row controls">
          <button class="ghost stop-drill" onclick={stopAndLog} disabled={logging}>
            {logging ? "Logging…" : "Stop"}
          </button>
        </div>
      {:else}
        {#if askable}
          <div class="row controls">
            <button class="primary start-drill" onclick={start}>Start</button>
          </div>
        {/if}

        {#if logged}
          <p class="statement logged">
            {loggedStatement(logged.seconds)}
            {progressStatement({ asked, correct: correctCount })}
            <a href="#/practice">Practice &amp; goals →</a>
          </p>
        {/if}

        {#if logError}
          <p class="notice log-error">
            {logError}
            <button class="retry-log" onclick={retryLog} disabled={logging}>
              {logging ? "Trying…" : "Try again"}
            </button>
          </p>
        {/if}
      {/if}
    </section>

    <p class="quiet footnote">
      The time you spend here is logged as fretboard practice, in the same history as everything
      else. Every question is also kept on its own, so which positions and which notes need more
      time is something you can look back on - not only a count for the session it happened in.
    </p>
  </main>
</div>

<style>
  .page {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  header {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--line);
  }

  h1 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
  }

  .back {
    color: var(--ink-dim);
    font-size: 14px;
  }

  .back:hover {
    color: var(--brass);
  }

  main {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 20px;
    padding: 32px 20px 48px;
  }

  .drill {
    width: 100%;
    max-width: 720px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 22px;
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }

  .hint,
  .quiet {
    margin: 0;
    color: var(--ink-dim);
    font-size: 13px;
    line-height: 1.5;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .scope-row label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
  }

  select {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 5px 8px;
    font: inherit;
    font-size: 14px;
  }

  /* Big touch targets throughout - this app's tablet-at-a-music-stand rule
     (issue #25/#119): every tappable control here is at least 44px tall. */
  .direction-choice,
  .string-choice,
  .choice,
  button {
    min-height: 44px;
  }

  .direction-choice,
  .string-choice {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 10px 16px;
    font: inherit;
    font-size: 15px;
    cursor: pointer;
  }

  .string-choice {
    min-width: 44px;
    padding: 10px 0;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .direction-choice.active,
  .string-choice.active {
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  .direction-choice:disabled,
  .string-choice:disabled {
    opacity: 0.55;
    cursor: default;
  }

  .statement {
    margin: 0;
    font-size: 15px;
    color: var(--ink);
    line-height: 1.5;
  }

  .statement-slot {
    min-height: 30px;
    display: flex;
    align-items: center;
  }

  .choices {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
  }

  .choice {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 14px 6px;
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    font: inherit;
    font-size: 18px;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }

  .choice:hover:enabled {
    border-color: var(--brass);
  }

  .choice:disabled {
    opacity: 1;
    cursor: default;
  }

  .choice.correct {
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  /* NOT --danger - see Neck.svelte's identical rule for the same marker
     kind. A wrong choice is shown as picked, in this app's ordinary "not the
     thing" ink, never in the colour reserved for a fault. */
  .choice.picked {
    border-color: var(--ink-dim);
  }

  button {
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 14px;
    font: inherit;
    font-size: 14px;
    cursor: pointer;
  }

  button:hover:enabled {
    border-color: var(--brass);
  }

  button:disabled {
    opacity: 0.55;
    cursor: default;
  }

  button.primary {
    background: var(--brass);
    border-color: var(--brass);
    color: #1a1509;
    font-weight: 600;
  }

  button.ghost {
    background: none;
    color: var(--ink-dim);
  }

  .notice {
    margin: 0;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 14px;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 12px;
  }

  .logged a {
    color: var(--brass);
  }

  .footnote {
    max-width: 52ch;
    text-align: center;
  }

  @media (min-width: 640px) {
    .choices {
      grid-template-columns: repeat(6, minmax(0, 1fr));
    }
  }
</style>
