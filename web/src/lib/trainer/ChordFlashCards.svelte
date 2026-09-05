<script>
  // Guitar chord flash cards, both directions (issue #28) - built on the
  // neck (#25) and the constraint model (#26), the same way fret-to-note
  // (#27) is. Show a shape, name the chord; or name a chord, place its
  // notes on the neck.
  //
  // WHAT MAKES THIS ONE HONEST, following FretToNote.svelte's own lead:
  //
  //   Nothing here grades anybody. Two counts, stated - no percentage, no
  //   streak. See chords.js's answerStatement and the tone rules at the top
  //   of that file.
  //
  //   name_to_shape is graded on what was actually TAPPED, worked out from
  //   the instrument's own tuning - never on matching one canonical
  //   fingering. There is more than one right way to play G major; see
  //   chords.js's module docstring.
  //
  //   Every question is a structured row (POST /api/trainer/chord-
  //   attempts), not only a count folded into the session's note - issue
  //   #32's promise for this drill, in a table of its own (see
  //   server/fermata/trainer.py's module comment on why).
  //
  //   THE NECK IS REUSED, NOT REBUILT. Both directions draw on Neck.svelte's
  //   existing `markers` and `highlightNote` props exactly as fret-to-note
  //   does - a shown shape or a set of taps is just another set of markers.
  import { onDestroy } from "svelte";

  import { api } from "../api.js";
  import { getInstruments, loadInstruments } from "../instruments.svelte.js";
  import { localDay } from "../practice.js";
  import { playChord } from "../score-render.js";
  import { ROOTS } from "./chord-theory.js";
  import {
    NAME_TO_SHAPE,
    SHAPE_TO_NAME,
    answerStatement,
    attemptPayload,
    checkNameAnswer,
    checkShapeAnswer,
    chordChoices,
    directionLabel,
    familyLabel,
    loggedStatement,
    pickQuestion,
    poolIsAskable,
    progressStatement,
    sessionNote,
    FAMILY_LIST,
  } from "./chords.js";
  import { KEY_QUALITY_LIST } from "./constraints.js";
  import Neck from "./Neck.svelte";
  import ScopePresets from "./ScopePresets.svelte";
  import { DEFAULT_FRET_COUNT, fretCountFromInstrument, noteAt, stringsFromInstrument } from "./neck.js";

  const instruments = getInstruments();

  $effect(() => {
    loadInstruments();
  });

  // Which instrument's tuning to draw the neck from - the same rule
  // FretToNote.svelte follows and for the same reason (see that file's own
  // comment): with more than one instrument defined, which is in
  // somebody's hands is not knowable, so only exactly one is adopted.
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

  let direction = $state(SHAPE_TO_NAME);
  let family = $state(FAMILY_LIST[0]);

  // Region scope: which strings and which frets - identical shape and
  // defaulting rule to FretToNote.svelte's, via constraints.js (#26).
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

  // Which saved scope is in force, or null (issue #236). Cleared by every
  // hand-turned scope control below: once somebody moves a fret selector the
  // scope is no longer the one that preset describes, and a session logged
  // under it must not claim otherwise. Identical to FretToNote.svelte's, on
  // purpose - the picker is one shared component, and so is the rule for
  // when a selection stops being true.
  let selectedPresetId = $state(null);

  function handTurned() {
    selectedPresetId = null;
  }

  /** Adopt a saved scope: its strings, its fret range and its key, all at
   * once. Marks each dimension customized so the "follow the instrument's
   * own defaults" effects above stop overwriting what was just restored.
   * The chord FAMILY is deliberately untouched - a preset is the shared
   * string/fret/key scope, not this drill's own idea of what to ask about. */
  function applyPreset(restored, id) {
    selectedPresetId = id;
    if (!restored) return;
    customizedStrings = true;
    customizedFretRange = true;
    selectedStrings = [...restored.stringNumbers];
    startFret = restored.startFret;
    endFret = restored.endFret;
    keyEnabled = Boolean(restored.key);
    if (restored.key) {
      keyRoot = restored.key.root;
      keyQuality = restored.key.quality;
    }
  }

  function toggleString(number) {
    customizedStrings = true;
    handTurned();
    if (selectedStrings.includes(number)) {
      if (selectedStrings.length <= 1) return;
      selectedStrings = selectedStrings.filter((n) => n !== number);
    } else {
      selectedStrings = [...selectedStrings, number];
    }
  }

  function setStartFret(value) {
    customizedFretRange = true;
    handTurned();
    startFret = Number(value);
  }

  function setEndFret(value) {
    customizedFretRange = true;
    handTurned();
    endFret = Number(value);
  }

  // Key scope (#26's addition): off by default (every chord in the family
  // preset is in play), and only a real constraint once a person turns it
  // on - the same "narrowed only on purpose" rule the string/fret scope
  // above follows.
  let keyEnabled = $state(false);
  let keyRoot = $state("C");
  let keyQuality = $state("major");

  function setKeyEnabled(value) {
    handTurned();
    keyEnabled = value;
  }

  function setKeyRoot(value) {
    handTurned();
    keyRoot = value;
  }

  function setKeyQuality(value) {
    handTurned();
    keyQuality = value;
  }

  const scope = $derived({
    startFret,
    endFret,
    stringNumbers: selectedStrings,
    key: keyEnabled ? { root: keyRoot, quality: keyQuality } : undefined,
  });
  const askable = $derived(poolIsAskable(strings, scope, family));
  const choices = $derived(chordChoices(strings, scope, family));

  let running = $state(false);
  let asked = $state(0);
  let correctCount = $state(0);
  // { question, given, correct }. `given` is null until answered.
  let round = $state(null);
  // name_to_shape only: positions tapped so far, before Check is pressed.
  let tapped = $state([]);
  let questionStartedAt = 0;
  let attemptLogFailures = $state(0);
  let soundError = $state("");
  let sounding = $state(false);

  let startedAt = 0;
  let logged = $state(null);
  let logError = $state("");
  let logging = $state(false);
  let unlogged = $state(null);

  function elapsed() {
    return Math.max(1, Math.round((Date.now() - startedAt) / 1000));
  }

  /** What the session carries about the scope it ran on - the same rule
   * FretToNote.svelte's own sessionFields states, and see chords.js's
   * sessionNote for why the chord family stays in the sentence even when the
   * region does not. */
  function sessionFields() {
    if (selectedPresetId != null) {
      return {
        preset_id: selectedPresetId,
        note: sessionNote({
          asked,
          correct: correctCount,
          direction,
          strings,
          scope: null,
          family,
        }),
      };
    }
    return {
      note: sessionNote({ asked, correct: correctCount, direction, strings, scope, family }),
    };
  }

  function nextQuestion() {
    const previous = round?.question ?? null;
    const question = pickQuestion(strings, scope, family, direction, previous);
    round = question ? { question, given: null, correct: null } : null;
    tapped = [];
    soundError = "";
    questionStartedAt = Date.now();
  }

  async function recordAttempt(question, given, correct) {
    const responseMs = Math.max(0, Date.now() - questionStartedAt);
    try {
      await api.logChordAttempt(attemptPayload({ question, given, responseMs }));
    } catch {
      // Best-effort: the drill's own counts and its session log are the
      // record that must not be lost - see FretToNote.svelte's identical
      // rule.
      attemptLogFailures += 1;
    }
  }

  function chooseName(root, quality) {
    if (!round || round.given != null || round.question.direction !== SHAPE_TO_NAME) return;
    const correct = checkNameAnswer(round.question, root, quality);
    const given = { root, quality };
    round = { ...round, given, correct };
    asked += 1;
    if (correct) correctCount += 1;
    recordAttempt(round.question, given, correct);
  }

  function toggleTap(stringNumber, fret) {
    if (!round || round.given != null || round.question.direction !== NAME_TO_SHAPE) return;
    const idx = tapped.findIndex((p) => p.string === stringNumber);
    if (idx >= 0 && tapped[idx].fret === fret) {
      tapped = tapped.filter((_, i) => i !== idx);
    } else if (idx >= 0) {
      tapped = tapped.map((p, i) => (i === idx ? { string: stringNumber, fret } : p));
    } else {
      tapped = [...tapped, { string: stringNumber, fret }];
    }
  }

  function checkShape() {
    if (!round || round.given != null || round.question.direction !== NAME_TO_SHAPE) return;
    if (!tapped.length) return;
    const { correct, notes } = checkShapeAnswer(strings, round.question, tapped);
    const given = { positions: tapped, notes };
    round = { ...round, given, correct };
    asked += 1;
    if (correct) correctCount += 1;
    recordAttempt(round.question, given, correct);
  }

  function neckMarkers() {
    if (!round) return [];
    const { question, given, correct } = round;
    const kind = given == null ? "target" : correct ? "correct" : "incorrect";
    const positions = question.direction === SHAPE_TO_NAME ? (question.shape?.frets ?? []) : tapped;
    return positions.map((p) => ({ string: p.string, fret: p.fret, kind }));
  }

  async function hear() {
    if (!round) return;
    soundError = "";
    sounding = true;
    try {
      const midis = round.question.sound
        .map(({ string, fret }) => noteAt(strings, string, fret))
        .filter((m) => m != null);
      const heard = await playChord(midis);
      if (!heard || !heard.length) {
        soundError = "No chord was sounded, so there is nothing to hear yet.";
      }
    } catch (e) {
      soundError = e?.message || "The synthesiser could not be loaded.";
    } finally {
      sounding = false;
    }
  }

  const neckInteractive = $derived(
    running && round != null && round.given == null && direction === NAME_TO_SHAPE,
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
    if (!totalAsked) return;
    await send(seconds);
  }

  async function send(seconds) {
    logging = true;
    logError = "";
    try {
      const session = await api.logSession({
        activity: "chords",
        seconds,
        local_date: localDay(),
        ...sessionFields(),
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

  onDestroy(() => {
    if (!running || asked < 1) return;
    api
      .logSession({
        activity: "chords",
        seconds: elapsed(),
        local_date: localDay(),
        ...sessionFields(),
      })
      .catch(() => {});
  });
</script>

<div class="page">
  <header>
    <a class="back" href="#/">← Library</a>
    <h1>Chord flash cards</h1>
  </header>

  <main>
    <section
      class="drill"
      data-running={running ? "1" : "0"}
      data-asked={asked}
      data-correct={correctCount}
      data-direction={direction}
      data-family={family}
      data-askable={askable ? "1" : "0"}
      data-question-root={round?.question?.root ?? ""}
      data-question-quality={round?.question?.quality ?? ""}
      data-given-correct={round?.given != null ? (round.correct ? "1" : "0") : ""}
      data-tapped-count={tapped.length}
      data-attempt-log-failures={attemptLogFailures}
      data-start-fret={startFret}
      data-end-fret={endFret}
      data-strings={[...selectedStrings].sort((a, b) => a - b).join(",")}
      data-key={keyEnabled ? `${keyRoot} ${keyQuality}` : ""}
      data-preset={selectedPresetId ?? ""}
    >
      <p class="hint">
        Shown a shape, name the chord - or name a chord, tap its notes onto the neck. Both
        directions are different skills, so pick the one to practise.
      </p>

      <!-- ------------------------------------------------------ direction -->
      <div class="row direction-row" role="group" aria-label="Direction">
        <button
          class="direction-choice"
          class:active={direction === SHAPE_TO_NAME}
          disabled={running}
          onclick={() => (direction = SHAPE_TO_NAME)}
        >
          Shape → name
        </button>
        <button
          class="direction-choice"
          class:active={direction === NAME_TO_SHAPE}
          disabled={running}
          onclick={() => (direction = NAME_TO_SHAPE)}
        >
          Name → shape
        </button>
      </div>

      <!-- ------------------------------------------------------ family -->
      <div class="row family-row" role="group" aria-label="Chord family">
        {#each FAMILY_LIST as key (key)}
          <button
            class="family-choice"
            class:active={family === key}
            disabled={running}
            onclick={() => (family = key)}
          >
            {familyLabel(key)}
          </button>
        {/each}
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

      <div class="row key-row">
        <label class="key-toggle">
          <input
            type="checkbox"
            checked={keyEnabled}
            disabled={running}
            class="key-enabled"
            onchange={(e) => setKeyEnabled(e.currentTarget.checked)}
          />
          <span>Key</span>
        </label>
        <select
          class="key-root"
          value={keyRoot}
          disabled={running || !keyEnabled}
          onchange={(e) => setKeyRoot(e.currentTarget.value)}
        >
          {#each ROOTS as root (root)}
            <option value={root}>{root}</option>
          {/each}
        </select>
        <select
          class="key-quality"
          value={keyQuality}
          disabled={running || !keyEnabled}
          onchange={(e) => setKeyQuality(e.currentTarget.value)}
        >
          {#each KEY_QUALITY_LIST as quality (quality)}
            <option value={quality}>{quality}</option>
          {/each}
        </select>
      </div>

      <ScopePresets
        {strings}
        {scope}
        selectedId={selectedPresetId}
        disabled={running}
        onSelect={applyPreset}
      />

      {#if instruments.error}
        <p class="notice instruments-error">
          {instruments.error} The drill below uses the standard guitar instead.
        </p>
      {/if}

      {#if !askable}
        <p class="statement narrow-scope">
          Nothing is selected to ask about - widen the region, the key, or the chord family.
        </p>
      {/if}

      <!-- ------------------------------------------------------- the drill -->
      {#if running && round}
        <p class="statement progress">{progressStatement({ asked, correct: correctCount })}</p>

        {#if direction === NAME_TO_SHAPE}
          <p class="statement prompt">
            Place <strong>{round.question.root} {round.question.quality === "dominant7" ? "7" : round.question.quality}</strong> on the neck.
          </p>
        {:else}
          <p class="statement prompt">Name the shape shown.</p>
        {/if}

        <div class="row hear-row">
          <button class="ghost hear-it" onclick={hear} disabled={sounding}>
            {sounding ? "Sounding…" : "♪ Hear it"}
          </button>
        </div>
        {#if soundError}
          <p class="notice sound-error">{soundError}</p>
        {/if}

        <Neck
          {strings}
          {fretCount}
          startFret={0}
          markers={neckMarkers()}
          interactive={neckInteractive}
          onTap={toggleTap}
        />

        <div class="statement-slot" role="status" aria-live="polite">
          {#if round.given != null}
            <p class="statement answer-statement">
              {answerStatement(round.question, round.given, round.correct)}
            </p>
          {/if}
        </div>

        {#if direction === SHAPE_TO_NAME}
          <ul class="choices">
            {#each choices as choice (choice.root + ":" + choice.quality)}
              <li>
                <button
                  class="choice"
                  class:correct={round.given != null && choice.root === round.question.root && choice.quality === round.question.quality}
                  class:picked={round.given?.root === choice.root && round.given?.quality === choice.quality && !(choice.root === round.question.root && choice.quality === round.question.quality)}
                  data-root={choice.root}
                  data-quality={choice.quality}
                  disabled={round.given != null}
                  onclick={() => chooseName(choice.root, choice.quality)}
                >
                  {choice.name}
                </button>
              </li>
            {/each}
          </ul>
        {:else}
          <div class="row controls">
            <button
              class="primary check-shape"
              onclick={checkShape}
              disabled={round.given != null || !tapped.length}
            >
              Check
            </button>
          </div>
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
          Nothing is selected to ask about - widen the region, the key, or the chord family.
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
      The time you spend here is logged as chord practice, in the same history as everything else.
      Every question is also kept on its own, so which chords need more time is something you can
      look back on - not only a count for the session it happened in.
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

  .scope-row label,
  .key-row .key-toggle {
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
    text-transform: capitalize;
  }

  .direction-choice,
  .string-choice,
  .family-choice,
  .choice,
  button {
    min-height: 44px;
  }

  .direction-choice,
  .string-choice,
  .family-choice {
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
  .string-choice.active,
  .family-choice.active {
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  .direction-choice:disabled,
  .string-choice:disabled,
  .family-choice:disabled {
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
    grid-template-columns: repeat(2, minmax(0, 1fr));
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
    font-size: 16px;
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
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }
</style>
