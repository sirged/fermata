<script>
  // Hear a note, name it. The first exercise in Fermata (issue #61).
  //
  // WHAT MAKES THIS ONE HONEST, which is the whole of its design:
  //
  //   The question is built from what was SOUNDED, never from what was asked
  //   for. score-render.js's playPitch resolves with the MIDI note actually
  //   written into the note-on event, and that number - not the target this
  //   component picked - is what gets spelled, offered, and marked correct. So
  //   there is no state in which the interface can be confident about a note
  //   the synthesiser never played: delete the audio path and there is no
  //   question, rather than a question about silence. That is deliberate.
  //   Instruments.svelte publishes its audition the same way, for the same
  //   reason, and the note there says what went wrong when it did not.
  //
  //   Nothing here grades anybody. Two counts, stated. No percentage, no
  //   streak, no colour for a wrong answer - a wrong answer in ear training is
  //   the practice, not a shortfall, and the phrasing rules it follows are
  //   practice.js's and are tested against practice.js's own word list. The
  //   statement about what the note was is worded and styled identically
  //   whether or not it was named, and there is no --danger anywhere in this
  //   file.
  //
  //   The time goes in the same history as everything else. One
  //   practice_sessions row with activity 'ear_training' - which the vocabulary
  //   in docs/practice-data.md has always had a slot for, precisely so that the
  //   first trainer would not need a table of its own. It shows on the practice
  //   page and counts towards a weekly goal with no special case anywhere.
  //
  // NO METRONOME. The click is a general tool and this page could have one in
  // two lines, which is not a reason to put one here: naming a heard pitch has
  // no tempo, and a control that does nothing for the exercise is decoration
  // that has to be looked past every time.
  import { onDestroy } from "svelte";

  import { api } from "./api.js";
  import {
    DEFAULT_RANGE,
    DRILL_SECONDS,
    DRILL_VOICE,
    buildChoices,
    instrumentRange,
    loggedStatement,
    pickTarget,
    progressStatement,
    rangeIsAskable,
    rangeSourceStatement,
    rangeStatement,
    referenceStatement,
    roundStatement,
    sessionNote,
  } from "./ear-training.js";
  import { getInstruments, loadInstruments } from "./instruments.svelte.js";
  import { spellMidi } from "./pitch.js";
  import { formatDuration, localDay } from "./practice.js";
  // Straight from score-render.js rather than through instruments.svelte.js's
  // auditionPitch, which is the same call under a name about strings. There is
  // one audio path in this application and this is a second caller of it, not a
  // second implementation.
  import { playPitch } from "./score-render.js";

  const instruments = getInstruments();

  $effect(() => {
    loadInstruments();
  });

  // Which range the drill draws from: "" is the plain default, otherwise an
  // instrument's id as a string (the value a <select> gives back).
  let source = $state("");
  // Latched the first time the instruments land, so the adoption below happens
  // once and can never overwrite a range somebody has since picked by hand. A
  // plain `let` rather than $state because nothing renders from it.
  let sourceSettled = false;

  // ONE instrument gets adopted; two do not. Fermata has a list of instruments
  // and no notion of which one is in somebody's hands, so picking among several
  // would be a guess - and a drill silently fitted to the wrong instrument is
  // worse than one that says it is using a plain range. With exactly one
  // defined there is nothing to guess.
  $effect(() => {
    if (!instruments.loaded || sourceSettled) return;
    sourceSettled = true;
    if (instruments.list.length === 1) source = String(instruments.list[0].id);
  });

  const instrument = $derived(instruments.list.find((i) => String(i.id) === source) ?? null);
  const range = $derived(instrument ? instrumentRange(instrument) : DEFAULT_RANGE);
  const askable = $derived(rangeIsAskable(range));

  let running = $state(false);
  // How many notes have been answered, and how many were named as heard. Reset
  // per drill: they describe the stretch of practice that gets logged.
  let asked = $state(0);
  let named = $state(0);
  // { sounded, choices, chosen }. `sounded` is what came back from the
  // synthesiser; `choices` are built around it.
  let round = $state(null);
  let waiting = $state(false);
  let soundError = $state("");
  // How many times a pitch has actually been sounded this drill, and the last
  // note the synthesiser was handed. Published onto the section (data-sounded-*)
  // for the same reason the instruments editor publishes its audition: it is the
  // only way to see from outside that the audio path ran rather than that a
  // counter was incremented next to it. Replays are in here and deliberately not
  // in `asked` - hearing a note five times is practising, not cheating.
  let sounded = $state(0);
  let lastSounded = $state(null);
  let startedAt = 0;
  let previousTarget = null;

  let logged = $state(null);
  let logError = $state("");
  let logging = $state(false);
  // The length of a drill whose log failed, kept so the time can be sent again
  // rather than lost. Practice history is the one thing in Fermata that cannot
  // be regenerated from the files on disk.
  let unlogged = $state(null);

  function elapsed() {
    // Floored at one second because that is the shortest session the server
    // will store, and a drill that took less than a second still happened.
    return Math.max(1, Math.round((Date.now() - startedAt) / 1000));
  }

  function drillNote() {
    return sessionNote({
      asked,
      named,
      range,
      instrumentName: instrument?.name ?? "",
    });
  }

  async function sound(midi) {
    soundError = "";
    try {
      // The drill's OWN voice and note length, not the tuning check's defaults -
      // see DRILL_VOICE and DRILL_SECONDS for the measurement behind the voice.
      const heard = await playPitch(midi, { voice: DRILL_VOICE, seconds: DRILL_SECONDS });
      if (heard == null) {
        // Says what is true and stops there. Null means no note reached the
        // synthesiser, and this cannot tell whether that was the pitch being
        // unplayable or the note never being handed over - so it names neither
        // cause rather than asserting the likelier one.
        soundError = "No note was sounded, so there is nothing to name yet.";
        return null;
      }
      lastSounded = heard;
      sounded += 1;
      return heard;
    } catch (e) {
      // `||` not `??`: an Error with an empty message renders as nothing at all,
      // which is a failure that looks like a working click.
      soundError = e?.message || "The synthesiser could not be loaded.";
      return null;
    }
  }

  async function nextNote() {
    if (!askable) return;
    round = null;
    waiting = true;
    try {
      const heard = await sound(pickTarget(range, previousTarget));
      if (heard == null) return;
      previousTarget = heard;
      // Built around the note that SOUNDED, not the one that was asked for.
      round = { sounded: heard, choices: buildChoices(heard, range), chosen: null };
    } finally {
      waiting = false;
    }
  }

  // Available before AND after answering, because the point of the exercise is
  // attaching the sound to the name and the moment you learn what it was is the
  // moment worth hearing it again. Counted as a sounding and as nothing else.
  async function hearAgain() {
    if (round?.sounded == null) return;
    await sound(round.sounded);
  }

  function choose(midi) {
    if (!round || round.chosen != null) return;
    const asHeard = midi === round.sounded;
    round = { ...round, chosen: midi };
    asked += 1;
    if (asHeard) named += 1;
  }

  function start() {
    if (!askable) return;
    running = true;
    startedAt = Date.now();
    asked = 0;
    named = 0;
    sounded = 0;
    lastSounded = null;
    previousTarget = null;
    logged = null;
    logError = "";
    unlogged = null;
    nextNote();
  }

  async function stopAndLog() {
    if (!running) return;
    const seconds = elapsed();
    const heard = sounded;
    running = false;
    round = null;
    waiting = false;
    // Nothing was ever sounded, so nothing was practised - a start immediately
    // stopped is a mis-click, and a one-second row in the history is noise
    // somebody would have to delete by hand.
    if (!heard) return;
    await send(seconds);
  }

  async function send(seconds) {
    logging = true;
    logError = "";
    try {
      const session = await api.logSession({
        activity: "ear_training",
        seconds,
        local_date: localDay(),
        note: drillNote(),
      });
      // What the SERVER stored, not what was sent.
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

  // Leaving mid-drill logs what was done. A hash route change unmounts this
  // component, and the alternative is that walking away from a stopped-but-not-
  // logged drill silently throws away practice that happened - which is the one
  // thing this application promises never to do. Fire and forget: there is
  // nobody left to show a failure to, and the request is already on the wire.
  onDestroy(() => {
    if (!running || sounded < 1) return;
    api
      .logSession({
        activity: "ear_training",
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
    <h1>Hear a note, name it</h1>
  </header>

  <main>
    <section
      class="drill"
      data-running={running ? "1" : "0"}
      data-asked={asked}
      data-named={named}
      data-sounded-count={sounded}
      data-sounded-midi={lastSounded ?? ""}
      data-range={`${range?.low ?? ""}-${range?.high ?? ""}`}
    >
      <p class="hint">
        A note sounds; name it from four. The three you did not hear are chosen to be worth
        confusing — a semitone away, the same name an octave out, and one a step or two off —
        because four notes far apart teaches nothing.
      </p>

      <!-- ------------------------------------------------------ the range -->
      <div class="row range-row">
        <label>
          <span>Notes from</span>
          <select class="range-source" bind:value={source} disabled={running}>
            <option value="">C2 to C6</option>
            {#each instruments.list as owned (owned.id)}
              <option value={String(owned.id)}>{owned.name}</option>
            {/each}
          </select>
        </label>
        <span class="quiet range-label">{rangeStatement(range, instrument?.name ?? "")}</span>
      </div>

      {#if instruments.error}
        <p class="notice instruments-error">
          {instruments.error} The drill below uses its plain range instead.
        </p>
      {/if}

      {#if rangeSourceStatement(range)}
        <p class="quiet range-source-note">{rangeSourceStatement(range)}</p>
      {/if}

      {#if referenceStatement(instrument)}
        <!-- The synthesiser is fixed at A440 and this drill does not pretend
             otherwise. See ear-training.js and playPitch's own note. -->
        <p class="quiet reference-note">{referenceStatement(instrument)}</p>
      {/if}

      {#if !askable}
        <p class="statement narrow-range">
          This definition spans {range && range.low === range.high ? "one note" : "too few notes"}, so
          there are not four to choose between. Pick another range above.
        </p>
      {/if}

      <!-- ------------------------------------------------------- the drill -->
      {#if soundError}
        <p class="notice sound-error">
          {soundError}
          <button class="retry-sound" onclick={nextNote}>Try again</button>
        </p>
      {/if}

      {#if running}
        <p class="statement progress">{progressStatement({ asked, named })}</p>

        <!-- The statement about what the note was lives ABOVE the choices and
             its space is reserved whether or not there is one, so answering
             does not shift the four buttons out from under the cursor. A drill
             that moves as you use it is a drill that collects wrong answers it
             did not earn. -->
        <!-- The live region is the SLOT and not the statement, because a region
             announces changes to itself and one that arrives already populated
             announces nothing. An exercise about listening is the last place to
             leave the answer unspoken. -->
        <div class="statement-slot" role="status" aria-live="polite">
          {#if round?.chosen != null}
            <p class="statement round-statement">{roundStatement(round)}</p>
          {/if}
        </div>

        <ul class="choices">
          {#each round?.choices ?? [] as choice (choice)}
            <li>
              <button
                class="choice"
                class:correct={round.chosen != null && choice === round.sounded}
                class:picked={round.chosen === choice && choice !== round.sounded}
                data-midi={choice}
                disabled={round.chosen != null}
                onclick={() => choose(choice)}
              >
                <!-- Rendered empty rather than absent, so the mark appearing
                     cannot change the button's size and move the row. -->
                <span class="tick" aria-hidden="true"
                  >{round.chosen != null && choice === round.sounded ? "✓" : ""}</span
                >
                <span class="choice-pitch">{spellMidi(choice)}</span>
              </button>
            </li>
          {/each}
        </ul>

        <div class="row controls">
          <!-- First in the row in both states, so it does not move when Next
               appears beside it. -->
          <button class="hear-again" onclick={hearAgain} disabled={round?.sounded == null}>
            ♪ Hear it again
          </button>
          {#if round?.chosen != null}
            <button class="primary next-note" onclick={nextNote}>Next note</button>
          {/if}
          <button class="ghost stop-drill" onclick={stopAndLog} disabled={logging}>
            {logging ? "Logging…" : "Stop and log this practice"}
          </button>
        </div>

        {#if waiting}
          <p class="quiet waiting">Sounding a note…</p>
        {/if}
      {:else}
        {#if askable}
          <div class="row controls">
            <button class="primary start-drill" onclick={start}>Start</button>
          </div>
        {/if}

        {#if logged}
          <p class="statement logged">
            {loggedStatement(logged.seconds)}
            {progressStatement({ asked, named })}
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
      The time you spend here is logged as ear training, in the same history as everything
      else, so it counts towards a weekly goal like any other practice. Hearing a note again
      is not counted at all.
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
    max-width: 540px;
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

  .range-row label {
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

  /* Every statement on this page is the same statement. There is deliberately
     no variant for a note that was not named: the wording differs, the
     rendering does not, and nothing here is ever coloured by outcome. There is
     no --danger in this file and a test checks the two cases render
     identically. */
  .statement {
    margin: 0;
    font-size: 15px;
    color: var(--ink);
    line-height: 1.5;
  }

  /* Two lines' worth, held open whether or not there is a statement in it, so
     the choices below do not move when one appears. */
  .statement-slot {
    min-height: 46px;
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

  /* One geometry for every state of a choice: the border width, the padding and
     the font never change, so marking an answer cannot resize a button. Only
     colour does. */
  .choice {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 14px 10px;
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    font: inherit;
    font-size: 20px;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
  }

  .choice:hover:enabled {
    border-color: var(--brass);
  }

  /* NOT dimmed, unlike every other disabled control here. The four buttons stop
     being actionable the moment one is clicked, and the answer is the thing a
     person is reading at exactly that moment - fading it out along with the
     other three is the interface withdrawing the information it was asked for. */
  .choice:disabled {
    opacity: 1;
    cursor: default;
  }

  .choice.correct {
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  /* The note that was picked when it was a different one. Marked so it can be
     seen which it was, in the same brass as everything else on this page - a
     second colour here would be a verdict. */
  .choice.picked {
    border-color: var(--ink-dim);
  }

  .tick {
    display: inline-block;
    width: 1em;
    text-align: center;
    color: var(--brass);
    font-size: 16px;
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

  /* Not an error style. A notice on this page is something that did not happen
     yet - a synthesiser still loading badly, a log that has to be sent again -
     and it is worded and coloured as information. */
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
    max-width: 46ch;
    text-align: center;
  }
</style>
