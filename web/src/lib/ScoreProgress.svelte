<script>
  // How is this piece going (#57).
  //
  // The other half of the practice page. That one answers "how am I doing"
  // across the library; this one answers "how is THIS piece going", which the
  // issue is explicit is a different question - deciding what to practise next
  // needs the per-piece picture as much as the overall one.
  //
  // WHAT THIS PAGE OBEYS, since it is the page most tempted to break it:
  //
  //   Nothing here is computed. Every number on screen came off
  //   /api/scores/{id}/practice/progress already counted. The helpers in
  //   practice.js put a number into words and the chart helper turns a bpm
  //   into a y coordinate; nothing between here and the server adds anything
  //   up. That is issue #32's rule, and the reason it matters is that the same
  //   endpoint is the contract a second reader will use.
  //
  //   No trend line, and no direction. A piece is put down for a fortnight and
  //   picked up again, and an arrow through that is a claim about somebody's
  //   playing these numbers cannot support. The tempo points are drawn with
  //   their values printed beside them and joined so the eye can follow the
  //   order they happened in - which is not the same as a fitted line, and the
  //   difference is that this one goes exactly where the sessions went.
  //
  //   Nothing depends on a hover. This is read from a music stand, at arm's
  //   length, in whatever light the room has - so every figure that matters is
  //   in text at a readable size, and the charts are there to make a shape out
  //   of numbers that are already legible without them.
  //
  //   Nothing is styled as an error, and there is no --danger in this file.
  //   Same rule as Practice.svelte, and a test checks for it.
  import { api } from "./api.js";
  import {
    allTimeStatement,
    dayBars,
    formatDuration,
    goalStatements,
    lastPractisedStatement,
    localDay,
    modeLabel,
    periodLabel,
    ratingStatement,
    rangeLabel,
    shortDate,
    splitBars,
    targetStatement,
    tempoChart,
    tempoLabel,
    tempoStatement,
    uncountableStatement,
    windowStatement,
  } from "./practice.js";

  let { id } = $props();

  const HISTORY_DAYS = 90;

  // The browser's own date, not the server's - the same rule every other
  // practice call follows. Read once per load; see Practice.svelte.
  const today = localDay();

  let data = $state(null);
  let loading = $state(true);
  let error = $state("");

  async function refresh() {
    error = "";
    try {
      data = await api.scoreProgress(id, HISTORY_DAYS, today);
    } catch (e) {
      error = e?.message ?? "Could not load this piece's practice history.";
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    refresh();
  });

  // Scaled inside this window, like every other bar in this application: a bar
  // that shrank because some other stretch was busier is a comparison drawn in
  // pixels.
  let windowBars = $derived(dayBars(data?.window?.days ?? []));
  let chart = $derived(tempoChart(data?.tempo));
  // The line through the points, in the order the sessions happened. Not a fit
  // and not a smoothing - it passes through every point exactly, and exists so
  // the eye can follow the order rather than to assert a direction.
  let tempoPath = $derived(chart.points.map((p) => `${p.x},${p.y}`).join(" "));
  let modeBars = $derived(splitBars(data?.modes ?? []));
  let ratingBars = $derived(splitBars(data?.ratings?.counts ?? [], (r) => r.sessions));
</script>

<div class="score-progress">
  <header>
    <!-- Back to the library, always. The way into the SCORE is below and only
         when there is one - a piece in the trash is still counted here and is
         not somewhere to go (issue #56). -->
    <a class="back" href="#/">← Library</a>
    <h1>{data?.title ?? "Practice"}</h1>
    {#if data?.deleted}
      <span class="deleted-mark" title="This score is in the trash. The practice still counts."
        >deleted</span
      >
    {/if}
  </header>

  <main>
    {#if error}
      <p class="notice" role="status">{error}</p>
    {/if}

    {#if loading}
      <p class="quiet">Loading…</p>
    {:else if !data}
      <p class="quiet">
        This piece's practice history could not be loaded, so nothing below is shown rather
        than shown wrongly. Reload to try again.
      </p>
    {:else if !data.practised}
      <!-- A piece nobody has played yet. Everything below would be a screen of
           noughts, and a nought is not the same statement as "you have not
           started" - the server says which this is (`practised`) rather than
           leaving a page to guess it from a total of zero. -->
      <section class="nothing-yet">
        <p class="statement-text">
          No practice logged against this piece yet. Open it and start the practice timer, and
          this page fills in as you go — the time, the days, the tempo you played it at, and
          whatever you wrote down.
        </p>
        {#if !data.deleted}
          <p><a class="open-score" href={"#/score/" + data.score_id}>Open {data.title}</a></p>
        {/if}
      </section>
    {:else}
      <!-- ----------------------------------------------------- the record -->
      <section class="all-time">
        <p class="headline" data-seconds={data.all_time.seconds}>
          {allTimeStatement(data.all_time)}
        </p>
        <p class="quiet last-practised">{lastPractisedStatement(data.all_time)}</p>
        <p class="actions">
          {#if data.deleted}
            <span class="quiet deleted-note">
              This score is in the trash, so there is nothing to open. Every hour above was
              still spent and is still counted.
            </span>
          {:else}
            <a class="open-score" href={"#/score/" + data.score_id}>Open {data.title}</a>
            <a class="all-practice" href="#/practice">All your practice</a>
          {/if}
        </p>
      </section>

      <!-- ---------------------------------------------------- the window -->
      <section class="window">
        <div class="section-head">
          <h2>The last {HISTORY_DAYS} days</h2>
          <span class="quiet">{periodLabel(data.start, data.end)}</span>
        </div>
        <p class="statement-text window-total">{windowStatement(data.window, HISTORY_DAYS)}</p>
        <!-- One bar per day the server sent, including the empty ones. A day
             with no practice is a fact about the window and is drawn as an
             empty bar rather than left out - a gap is something a reader has
             to notice and then interpret. -->
        <div class="history-strip" aria-label={`practice on this piece over ${HISTORY_DAYS} days`}>
          {#each windowBars as bar (bar.date)}
            <div
              class="history-day"
              class:has-practice={bar.seconds > 0}
              data-day={bar.date}
              data-seconds={bar.seconds}
              style={`--fill:${Math.round(bar.fill * 100)}%`}
            ></div>
          {/each}
        </div>
        <div class="strip-ends quiet">
          <span>{shortDate(data.start)}</span>
          <span>{shortDate(data.end)}</span>
        </div>
      </section>

      <!-- ------------------------------------------------------- tempo -->
      <section class="tempo">
        <div class="section-head">
          <h2>Tempo, session by session</h2>
        </div>
        <p class="statement-text tempo-total">{tempoStatement(data.tempo)}</p>
        {#if targetStatement(data.tempo)}
          <p class="statement-text tempo-target">{targetStatement(data.tempo)}</p>
        {/if}

        {#if data.tempo.count}
          <!-- Every value printed beside its own dot. A chart whose numbers
               only appear on hover is a chart nobody standing at a music stand
               can read, and these are the numbers the section is about. -->
          <svg
            class="tempo-chart"
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            role="img"
            aria-label={tempoStatement(data.tempo)}
          >
            {#if chart.target}
              <line
                class="target-line"
                x1={chart.pad}
                x2={chart.width - chart.pad}
                y1={chart.target.y}
                y2={chart.target.y}
              />
              <text class="target-label" x={chart.width - chart.pad} y={chart.target.y - 8}>
                target {chart.target.bpm}
              </text>
            {/if}
            {#if chart.points.length > 1}
              <polyline class="tempo-line" points={tempoPath} />
            {/if}
            {#each chart.points as point (point.session_id)}
              <circle
                class="tempo-point"
                class:reached={point.reached_target === true}
                cx={point.x}
                cy={point.y}
                r="7"
                data-bpm={point.tempo_bpm}
                data-day={point.date}
              />
              <text class="tempo-value" x={point.x} y={point.y - 14}>{point.tempo_bpm}</text>
            {/each}
          </svg>
          <ul class="tempo-days quiet">
            {#each data.tempo.points as point (point.session_id)}
              <li data-tempo-day={point.date}>
                {shortDate(point.date)} · {point.tempo_bpm} bpm
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <!-- ------------------------------------------- section work vs runs -->
      <section class="modes">
        <div class="section-head">
          <h2>Section work and run-throughs</h2>
        </div>
        {#if modeBars.length}
          <ul class="split">
            {#each modeBars as row (row.mode ?? "unstated")}
              <li data-mode={row.mode ?? "unstated"}>
                <span class="split-label">{modeLabel(row.mode)}</span>
                <span class="split-track">
                  <span class="split-bar" style={`width:${Math.round(row.fill * 100)}%`}></span>
                </span>
                <span class="split-value">{formatDuration(row.seconds)}</span>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="quiet">No practice on this piece in this window to split up.</p>
        {/if}
      </section>

      <!-- ----------------------------------------------------- ratings -->
      <section class="ratings">
        <div class="section-head">
          <h2>How it went</h2>
          <span class="quiet">your own sense of it, session by session</span>
        </div>
        <p class="statement-text rating-total">{ratingStatement(data.ratings)}</p>
        {#if data.ratings.rated}
          <ul class="split ratings-split">
            {#each ratingBars as row (row.rating)}
              <li data-rating={row.rating}>
                <span class="split-label">{row.rating}</span>
                <span class="split-track">
                  <span class="split-bar" style={`width:${Math.round(row.fill * 100)}%`}></span>
                </span>
                <span class="split-value">{row.sessions}</span>
              </li>
            {/each}
          </ul>
        {/if}
      </section>

      <!-- ------------------------------------------------------- goals -->
      {#if data.goals.length}
        <section class="goals">
          <div class="section-head">
            <h2>Goals about this piece</h2>
          </div>
          <ul class="goal-list">
            {#each data.goals as goal (goal.id)}
              <li class="goal" data-goal-week={goal.period_start}>
                <div class="goal-head">
                  <span class="goal-label">{periodLabel(goal.period_start, goal.period_end)}</span>
                  {#if goal.intent}<span class="quiet goal-intent">{goal.intent}</span>{/if}
                </div>
                {#if uncountableStatement(goal)}
                  <p class="statement-text uncountable">{uncountableStatement(goal)}</p>
                {:else}
                  <ul class="statements">
                    {#each goalStatements(goal) as statement (statement.key)}
                      <li class="statement" class:reached={statement.met}>
                        <span class="tick" aria-hidden="true">{statement.met ? "✓" : ""}</span>
                        <span class="statement-text">{statement.text}</span>
                      </li>
                    {/each}
                  </ul>
                {/if}
                {#if goal.reflection}
                  <p class="quiet goal-reflection">{goal.reflection}</p>
                {/if}
              </li>
            {/each}
          </ul>
        </section>
      {/if}

      <!-- ------------------------------------------------------ sessions -->
      <section class="sessions">
        <div class="section-head">
          <h2>Session by session</h2>
          {#if data.sessions_truncated}
            <span class="quiet truncated">
              the most recent {data.sessions.length} of {data.session_total}
            </span>
          {/if}
        </div>
        {#if data.sessions.length}
          <ul class="session-list">
            {#each data.sessions as session (session.id)}
              <li class="session" data-session={session.id}>
                <span class="session-day">
                  <span class="day-date">{session.local_date}</span>
                  <!-- Whether that day was recorded or worked out from the
                       row's UTC timestamp - the same word this application uses
                       everywhere for a value it chose rather than read, and the
                       same note under the list explaining it once. -->
                  {#if session.local_date_source && session.local_date_source !== "recorded"}
                    <span class="day-inferred">assumed</span>
                  {/if}
                </span>
                <span class="session-length">{formatDuration(session.seconds)}</span>
                <span class="quiet session-extra">
                  {[modeLabel(session.mode), rangeLabel(session), tempoLabel(session)]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {#if session.note}
                  <p class="session-note">{session.note}</p>
                {/if}
              </li>
            {/each}
          </ul>
          {#if data.sessions.some((s) => s.local_date_source && s.local_date_source !== "recorded")}
            <p class="quiet day-note">
              A day marked <span class="day-inferred">assumed</span> was taken from the session's
              own timestamp rather than recorded when the practice happened.
            </p>
          {/if}
        {:else}
          <p class="quiet">
            Nothing logged against this piece in the last {HISTORY_DAYS} days.
          </p>
        {/if}
      </section>
    {/if}
  </main>
</div>

<style>
  .score-progress {
    height: 100vh;
    display: flex;
    flex-direction: column;
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

  header h1 {
    font-size: 18px;
  }

  main {
    flex: 1;
    overflow-y: auto;
    padding: 28px;
    display: flex;
    flex-direction: column;
    gap: 34px;
    max-width: 760px;
  }

  .section-head {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 12px;
  }

  h2 {
    font-size: 16px;
  }

  .quiet {
    color: var(--ink-dim);
    font-size: 13px;
  }

  /* Deliberately NOT var(--danger). Nothing on this page is an error - see
     Practice.svelte, which carries the same rule and the same test. */
  .notice {
    background: var(--surface);
    border: 1px solid var(--line);
    border-left: 3px solid var(--brass);
    border-radius: 6px;
    padding: 10px 14px;
    margin: 0;
    font-size: 14px;
  }

  .deleted-mark {
    font-size: 11px;
    letter-spacing: 0.04em;
    color: #e8b45c;
    border: 1px solid rgba(232, 180, 92, 0.5);
    border-radius: 99px;
    padding: 1px 7px;
  }

  /* The one number somebody came to this page for, at the size somebody
     reading from a music stand can take in without leaning towards the
     screen. */
  .headline {
    margin: 0 0 6px;
    font-family: var(--font-display);
    font-size: 26px;
    font-variant-numeric: tabular-nums;
  }

  .last-practised {
    margin: 0 0 14px;
  }

  .actions {
    display: flex;
    gap: 16px;
    align-items: baseline;
    flex-wrap: wrap;
    margin: 0;
    font-size: 14px;
  }

  .statement-text {
    font-variant-numeric: tabular-nums;
    font-size: 15px;
    margin: 0 0 10px;
  }

  .statements {
    list-style: none;
    padding: 0;
    margin: 6px 0 0;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  /* One style for every statement, reached or not - the tick is the whole
     difference and it is additive. Same rule as Practice.svelte. */
  .statement {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 14px;
  }

  .statement .tick {
    width: 1em;
    color: var(--brass-bright);
  }

  /* A no-op on purpose, and recorded as one: "the same as an unreached one" is
     the answer, rather than an oversight for somebody to fill in with a green. */
  .statement.reached .statement-text {
    color: var(--ink);
  }

  /* Ninety days across the page. Each day is a column of its own so a quiet
     fortnight is a visible gap in a texture rather than a number nobody
     reads - and the totals above it are what actually state the figures, so
     this never has to be measured against an axis. */
  .history-strip {
    display: flex;
    align-items: flex-end;
    gap: 1px;
    height: 84px;
    padding: 4px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 5px;
  }

  .history-day {
    flex: 1;
    min-width: 2px;
    height: var(--fill);
    background: var(--brass);
    border-radius: 1px 1px 0 0;
  }

  /* A day with nothing on it keeps its column and shows a floor, so the strip
     reads as ninety days rather than as however many had practice in them. */
  .history-day:not(.has-practice) {
    height: 2px;
    background: var(--line);
  }

  .strip-ends {
    display: flex;
    justify-content: space-between;
    margin-top: 5px;
  }

  .tempo-chart {
    width: 100%;
    height: auto;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 5px;
    margin: 6px 0 10px;
  }

  .tempo-line {
    fill: none;
    stroke: var(--brass);
    stroke-width: 2;
    stroke-linejoin: round;
  }

  .tempo-point {
    fill: var(--brass);
  }

  /* A session that reached what it was aiming at is marked, and one that did
     not is drawn exactly as it always was. Additive, like the tick on a goal:
     something appears when a target IS reached rather than something being
     marked when it is not. */
  .tempo-point.reached {
    fill: var(--brass-bright);
    stroke: var(--brass-bright);
    stroke-width: 4;
  }

  .tempo-value {
    fill: var(--ink);
    font-size: 15px;
    font-variant-numeric: tabular-nums;
    text-anchor: middle;
  }

  .target-line {
    stroke: var(--ink-dim);
    stroke-width: 1.5;
    stroke-dasharray: 5 5;
  }

  .target-label {
    fill: var(--ink-dim);
    font-size: 12px;
    text-anchor: end;
  }

  .tempo-days {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    font-variant-numeric: tabular-nums;
  }

  .split {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    font-size: 15px;
  }

  .split li {
    display: grid;
    grid-template-columns: 120px 1fr 80px;
    gap: 12px;
    align-items: center;
  }

  .split-track {
    height: 16px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 4px;
    overflow: hidden;
  }

  .split-bar {
    display: block;
    height: 100%;
    background: var(--brass);
  }

  .split-value {
    text-align: right;
    font-variant-numeric: tabular-nums;
    color: var(--ink-dim);
  }

  .ratings-split .split-label {
    font-variant-numeric: tabular-nums;
  }

  .goal-list,
  .session-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
  }

  .goal-list {
    gap: 14px;
  }

  .goal {
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 14px 16px;
  }

  .goal-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }

  .goal-label {
    font-family: var(--font-display);
    font-size: 15px;
  }

  .goal-reflection {
    margin: 8px 0 0;
  }

  .session-list {
    gap: 10px;
    font-size: 14px;
  }

  .session {
    display: grid;
    grid-template-columns: 120px 70px 1fr;
    gap: 10px;
    align-items: baseline;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--line);
  }

  .session-day,
  .session-length {
    color: var(--ink-dim);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }

  /* "beside the date" is the whole point - without this the badge breaks onto
     its own line the moment the track is a pixel too narrow, and a test that
     asserts only the text cannot see that happen. */
  .session-day {
    white-space: nowrap;
  }

  .day-inferred {
    font-size: 11px;
    font-variant-numeric: normal;
    opacity: 0.75;
  }

  .day-note {
    margin: 12px 0 0;
    font-size: 12px;
  }

  /* The note somebody wrote, at the size of something meant to be read rather
     than of an annotation on a row - it is half of what a person comes back to
     their own history for. */
  .session-note {
    grid-column: 2 / -1;
    margin: 4px 0 0;
    font-size: 14px;
    line-height: 1.5;
    max-width: 60ch;
  }

  .session-extra {
    font-variant-numeric: tabular-nums;
  }

  .nothing-yet .statement-text {
    color: var(--ink-dim);
    max-width: 56ch;
    line-height: 1.6;
  }

  .uncountable {
    color: var(--ink-dim);
    max-width: 52ch;
  }
</style>
