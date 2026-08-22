<script>
  // Practice: the week you meant to have, and the weeks you have had.
  //
  // The whole design constraint of this page is in one sentence from the
  // issue it comes from: "Three of four planned days" is the same information
  // as "you missed a day", and only one of them makes a person want to open
  // the app again tomorrow. So:
  //
  //   - Nothing on this page is styled as an error. A goal not reached uses
  //     exactly the same colours, weight and order as one that was; the only
  //     difference is a small mark beside the ones that were reached. There is
  //     no --danger anywhere in this file, and a test checks for it.
  //   - Nothing compares one week to another. No best week, no run of weeks,
  //     no average. The per-day bars are scaled inside their own week for the
  //     same reason.
  //   - A finished week asks a question rather than reaching a verdict: was
  //     this goal realistic? Only the person practising knows whether the week
  //     was unusual, and that answer is the useful half of a review.
  //   - Every zero is stated plainly. A day with no practice is a fact about
  //     the week and is drawn as an empty bar, not as a gap and not in red.
  import { api } from "./api.js";
  import Metronome from "./Metronome.svelte";
  import {
    ACTIVITY_LABELS,
    activityLabel,
    dayBars,
    formatDuration,
    goalScopeLabel,
    goalStatements,
    localDay,
    periodLabel,
    formatDays,
    periodStatement,
    rangeLabel,
    sessionSubject,
    tempoLabel,
    timeLeftStatement,
    uncountableStatement,
  } from "./practice.js";

  const REVIEW_WEEKS = 8;
  const HISTORY_DAYS = 90;

  // The browser's own date, not the server's. Read once per load: a page left
  // open across midnight showing yesterday is a smaller problem than a page
  // that recomputes the week under a half-finished form.
  const today = localDay();

  let current = $state(null);
  let review = $state(null);
  let history = $state(null);
  let scores = $state([]);
  let sessions = $state([]);
  let loading = $state(true);
  let error = $state("");

  // The goal form, open either to set a first goal or to adjust one.
  let editing = $state(false);
  let form = $state(blankForm());
  let saving = $state(false);

  // Reflections in progress, keyed by goal id, so several past weeks can be
  // written about without one box clobbering another.
  let reflections = $state({});

  // The "log other practice" form - how a session that is not against a piece
  // gets recorded until the exercises that produce them exist.
  let otherActivity = $state("technique");
  let otherMinutes = $state(15);
  let otherNote = $state("");
  let logging = $state(false);

  // A click, available while working through a goal without having to open a
  // piece first. The general metronome (issue #97), pre-filled from the only
  // tempo this page actually knows: the one most recently practised at.
  let metronomeOn = $state(false);

  // The tempo to arrive at. A session records both what it was practised at
  // and what it was aiming for, and the TARGET is the better pre-fill of the
  // two - a tempo ladder exists to be climbed, so the number worth being
  // handed tomorrow is the one that was being worked towards, not the one
  // already managed. Falls back to the achieved tempo, then to the plain
  // default. Not a guess dressed up as a reading either way: both numbers
  // were entered by the person practising.
  //
  // $derived, not read once, because `sessions` arrives asynchronously - and
  // then again after logging a session. Metronome.svelte adopts a pre-fill
  // that arrives after it mounted, and stops adopting once anything has been
  // set by hand - so the first answer lands and a later one cannot overwrite a
  // tempo already dialled in.
  const practisedTempo = $derived.by(() => {
    for (const session of sessions ?? []) {
      const t = session.target_tempo_bpm ?? session.tempo_bpm;
      if (Number.isFinite(t) && t > 0) return t;
    }
    return null;
  });

  function blankForm() {
    return {
      id: null,
      target_days: 4,
      use_days: true,
      target_minutes: 150,
      use_minutes: true,
      scope: "all",
      score_id: null,
      activity: "technique",
      intent: "",
    };
  }

  async function refresh() {
    error = "";
    try {
      const [nextCurrent, nextReview, nextHistory, nextScores] = await Promise.all([
        api.currentGoal(today),
        api.practiceReview(REVIEW_WEEKS, today),
        api.practiceHistory(HISTORY_DAYS, today),
        api.scores(),
      ]);
      current = nextCurrent;
      review = nextReview;
      history = nextHistory;
      scores = nextScores;
      // Sent after the first call, because the week to ask for comes back from
      // it - the server decides which seven days "this week" is, from the
      // week-start preference, and asking for a week the client worked out
      // itself is how the two quietly disagree.
      const shown = nextCurrent.goal ?? nextCurrent;
      sessions = (
        await api.sessions({
          start: shown.period_start ?? nextCurrent.week_start,
          end: shown.period_end ?? nextCurrent.week_end,
        })
      ).sessions;
    } catch (e) {
      error = e?.message ?? "Could not load your practice history.";
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    refresh();
  });

  let goal = $derived(current?.goal ?? null);
  let statements = $derived(goalStatements(goal));
  // The period on screen is the GOAL's when there is one, and the canonical
  // week from the preference only when there is not. A goal stores the dates it
  // was set for, so after the week-start preference changes the two differ -
  // and showing the canonical week while displaying that goal's counts had the
  // header, the day strip and the Adjust button each talking about a different
  // seven days.
  let periodStart = $derived(goal?.period_start ?? current?.week_start);
  let periodEnd = $derived(goal?.period_end ?? current?.week_end);
  // The review's first entry is always the week containing today, which is
  // where a week with no goal gets its facts from.
  let thisWeek = $derived(review?.weeks?.[0] ?? null);
  let pastWeeks = $derived((review?.weeks ?? []).slice(1));
  // Nothing has been practised and nothing has been planned. Without this the
  // first thing a new install shows is fourteen statements of absence - seven
  // empty days and seven weeks that had no goal - which is a poor greeting for
  // somebody who has simply not started yet.
  let nothingYet = $derived(
    !!review && !goal && !history?.sessions && !(review.weeks ?? []).some((w) => w.goal),
  );
  // A scoped goal's own days when there is one, so the strip shows the
  // practice the goal is actually counted against; the week's whole practice
  // otherwise.
  let weekBars = $derived(dayBars(goal ? goal.progress.days : (thisWeek?.facts?.days ?? [])));

  function startEditing() {
    form = goal
      ? {
          id: goal.id,
          target_days: goal.target_days ?? 4,
          use_days: goal.target_days != null,
          target_minutes: goal.target_minutes ?? 150,
          use_minutes: goal.target_minutes != null,
          scope: goal.scope,
          score_id: goal.score_id,
          activity: goal.activity ?? "technique",
          intent: goal.intent ?? "",
        }
      : blankForm();
    editing = true;
  }

  async function saveGoal() {
    if (!form.use_days && !form.use_minutes) {
      error = "Choose a number of days, an amount of time, or both.";
      return;
    }
    saving = true;
    error = "";
    try {
      // POST rather than PATCH even when adjusting: setting a goal for a week
      // that already has one replaces its targets, so there is one code path
      // and no way to end up with two goals for one week.
      await api.setGoal(
        {
          period_start: periodStart,
          target_days: form.use_days ? Math.round(Number(form.target_days)) : null,
          target_minutes: form.use_minutes ? Math.round(Number(form.target_minutes)) : null,
          scope: form.scope,
          score_id: form.scope === "score" ? Number(form.score_id) || null : null,
          activity: form.scope === "activity" ? form.activity : null,
          intent: form.intent.trim() || null,
        },
        today,
      );
      editing = false;
      await refresh();
    } catch (e) {
      error = e?.message ?? "Could not save that goal.";
    } finally {
      saving = false;
    }
  }

  async function removePastGoal(week) {
    saving = true;
    error = "";
    try {
      await api.deleteGoal(week.goal.id);
      await refresh();
    } catch (e) {
      error = e?.message ?? "Could not remove that goal.";
    } finally {
      saving = false;
    }
  }

  async function removeGoal() {
    if (!goal) return;
    saving = true;
    try {
      await api.deleteGoal(goal.id);
      editing = false;
      await refresh();
    } catch (e) {
      error = e?.message ?? "Could not remove that goal.";
    } finally {
      saving = false;
    }
  }

  function draft(week) {
    const g = week.goal;
    return (
      reflections[g.id] ?? {
        realistic: g.realistic ?? "",
        reflection: g.reflection ?? "",
      }
    );
  }

  function setDraft(week, patch) {
    reflections = { ...reflections, [week.goal.id]: { ...draft(week), ...patch } };
  }

  async function saveReflection(week) {
    const d = draft(week);
    saving = true;
    error = "";
    try {
      await api.patchGoal(
        week.goal.id,
        { realistic: d.realistic || null, reflection: d.reflection.trim() || null },
        today,
      );
      await refresh();
    } catch (e) {
      error = e?.message ?? "Could not save that.";
    } finally {
      saving = false;
    }
  }

  async function logOther() {
    logging = true;
    error = "";
    try {
      await api.logSession({
        activity: otherActivity,
        seconds: Math.round(Number(otherMinutes) * 60),
        local_date: today,
        note: otherNote.trim() || null,
      });
      otherNote = "";
      await refresh();
    } catch (e) {
      error = e?.message ?? "Could not log that.";
    } finally {
      logging = false;
    }
  }
</script>

<div class="practice">
  <header>
    <a class="back" href="#/">← Library</a>
    <h1>Practice</h1>
  </header>

  <main>
    {#if error}
      <p class="notice" role="status">{error}</p>
    {/if}

    {#if loading}
      <p class="quiet">Loading…</p>
    {:else if !current || !review || !history}
      <!-- The load failed, and the message above says why. Everything below
           reads from these three answers, and rendering it without them put
           "NaN undefined" on screen where the week should be - a page that
           looks broken rather than one that says what happened. -->
      <p class="quiet">
        Your practice history could not be loaded, so nothing below is shown rather than
        shown wrongly. Reload to try again.
      </p>
    {:else}
      <!-- ------------------------------------------------ this week -->
      <section class="week" data-week={periodStart}>
        <div class="section-head">
          <h2>This week</h2>
          <span class="quiet">{periodLabel(periodStart, periodEnd)}</span>
        </div>

        {#if goal && !editing}
          <p class="intent">
            <span class="scope">{goalScopeLabel(goal)}</span>
            {#if goal.intent}<span class="intent-text">— {goal.intent}</span>{/if}
          </p>

          {#if uncountableStatement(goal)}
            <p class="statement-text uncountable">{uncountableStatement(goal)}</p>
          {:else}
            <ul class="statements">
              {#each statements as statement (statement.key)}
                <li class="statement" class:reached={statement.met} data-statement={statement.key}>
                  <span class="tick" aria-hidden="true">{statement.met ? "✓" : ""}</span>
                  <span class="statement-text">{statement.text}</span>
                </li>
              {/each}
            </ul>
          {/if}

          {#if timeLeftStatement(goal.progress)}
            <p class="quiet days-left">{timeLeftStatement(goal.progress)}</p>
          {/if}
        {:else if !editing}
          <p class="statement-text no-goal">
            No goal set for this week. {periodStatement(thisWeek?.facts, true)}.
          </p>
        {/if}

        <div class="strip" aria-label="practice each day this week">
          {#each weekBars as bar (bar.date)}
            <div class="day" data-day={bar.date} data-seconds={bar.seconds}>
              <div class="bar-track">
                <div class="bar" style={`height:${Math.round(bar.fill * 100)}%`}></div>
              </div>
              <span class="day-label">{bar.label}</span>
              <span class="day-value">{bar.seconds ? formatDuration(bar.seconds) : "—"}</span>
            </div>
          {/each}
        </div>

        {#if editing}
          <div class="goal-form">
            <h3>{form.id ? "Adjust this week's goal" : "Set a goal for this week"}</h3>

            <label class="row">
              <input type="checkbox" bind:checked={form.use_days} />
              <span>Days of practice</span>
              <select bind:value={form.target_days} disabled={!form.use_days} class="days-target">
                {#each [1, 2, 3, 4, 5, 6, 7] as n}
                  <option value={n}>{n}</option>
                {/each}
              </select>
            </label>

            <label class="row">
              <input type="checkbox" bind:checked={form.use_minutes} />
              <span>Minutes in total</span>
              <input
                class="minutes-target"
                type="number"
                min="1"
                max="10080"
                bind:value={form.target_minutes}
                disabled={!form.use_minutes}
              />
            </label>

            <label class="row">
              <span>On</span>
              <select bind:value={form.scope} class="scope-select">
                <option value="all">any practice</option>
                <option value="score">one piece</option>
                <option value="activity">a kind of work</option>
              </select>
              {#if form.scope === "score"}
                <select bind:value={form.score_id} class="score-select">
                  <option value={null}>choose a piece…</option>
                  {#each scores as s (s.id)}
                    <option value={s.id}>{s.title}</option>
                  {/each}
                </select>
              {:else if form.scope === "activity"}
                <select bind:value={form.activity} class="activity-select">
                  {#each Object.entries(ACTIVITY_LABELS) as [value, label]}
                    <option {value}>{label}</option>
                  {/each}
                </select>
              {/if}
            </label>

            <label class="row wide">
              <span>What you mean to work on</span>
              <input
                class="intent-input"
                type="text"
                maxlength="200"
                placeholder="the awkward middle section"
                bind:value={form.intent}
              />
            </label>

            <div class="actions">
              <button class="primary save-goal" onclick={saveGoal} disabled={saving}>
                {saving ? "Saving…" : "Save goal"}
              </button>
              <button onclick={() => (editing = false)} disabled={saving}>Cancel</button>
              {#if form.id}
                <button class="ghost remove-goal" onclick={removeGoal} disabled={saving}>
                  Remove
                </button>
              {/if}
            </div>
          </div>
        {:else}
          <div class="actions">
            <button class="primary edit-goal" onclick={startEditing}>
              {goal ? "Adjust this goal" : "Set a goal for this week"}
            </button>
          </div>
        {/if}
      </section>

      <!-- ------------------------------------------------------ metronome -->
      <section class="metronome-section">
        <div class="section-head">
          <h2>Metronome</h2>
        </div>
        <p class="hint">
          {#if practisedTempo != null}
            Set to {practisedTempo} bpm, the tempo your most recent session was working
            towards. Change it to whatever today needs.
          {:else}
            For technique, scales, or working a passage from memory. Nothing here is tied to
            a piece.
          {/if}
        </p>
        <!-- Deliberately NOT wrapped in {#key practisedTempo}. Keying it would
             remount the control every time this number changed, which happens
             once on load and again after every logged session - and a remount
             throws away whatever tempo had been set by hand since, stops the
             click, and starts it again at the pre-fill. Passing the pre-fill as
             a prop the control adopts while untouched gets the late first
             answer to land without that. -->
        <Metronome bind:enabled={metronomeOn} prominent={true} initialBpm={practisedTempo} />
      </section>

      <!-- --------------------------------------------- log other practice -->
      <section class="log-other">
        <div class="section-head">
          <h2>Log practice that is not a piece</h2>
        </div>
        <p class="hint">
          Technique, ear training, or simply playing. Practice at a score is timed in the
          viewer; this is for everything else.
        </p>
        <div class="row">
          <select bind:value={otherActivity} class="other-activity">
            {#each Object.entries(ACTIVITY_LABELS) as [value, label]}
              {#if value !== "piece"}
                <option {value}>{label}</option>
              {/if}
            {/each}
          </select>
          <input class="other-minutes" type="number" min="1" max="1440" bind:value={otherMinutes} />
          <span class="quiet">minutes</span>
          <input
            class="other-note"
            type="text"
            maxlength="2000"
            placeholder="note (optional)"
            bind:value={otherNote}
          />
          <button class="log-other-button" onclick={logOther} disabled={logging}>
            {logging ? "Saving…" : "Log it"}
          </button>
        </div>
      </section>

      {#if nothingYet}
        <!-- Nothing practised and nothing planned. Everything below this point
             would be a statement of absence, and a page that opens with
             fourteen of them is not a good greeting for somebody who has
             simply not started yet. -->
        <section class="nothing-yet">
          <p class="statement-text">
            Nothing logged yet. Open a score and start the practice timer, or log a stretch
            of technique above — this page fills in as you go, and the weeks below it will
            show what you did against what you meant to do.
          </p>
        </section>
      {:else}
      <!-- ----------------------------------------------------- the review -->
      <section class="review">
        <div class="section-head">
          <h2>Recent weeks</h2>
          <span class="quiet">what happened, and room to say why</span>
        </div>

        <ul class="weeks">
          {#each pastWeeks as week (week.period_start)}
            <li class="past-week" data-week={week.period_start}>
              <div class="week-head">
                <span class="week-label">{periodLabel(week.period_start, week.period_end)}</span>
                <span class="week-facts">{periodStatement(week.facts, false)}</span>
              </div>

              {#if week.goal}
                {#if uncountableStatement(week.goal)}
                  <p class="statement-text uncountable">{uncountableStatement(week.goal)}</p>
                {:else}
                  <ul class="statements compact">
                    {#each goalStatements(week.goal) as statement (statement.key)}
                      <li class="statement" class:reached={statement.met}>
                        <span class="tick" aria-hidden="true">{statement.met ? "✓" : ""}</span>
                        <span class="statement-text">{statement.text}</span>
                      </li>
                    {/each}
                  </ul>
                {/if}

                <div class="reflection">
                  <p class="question">Was this goal realistic?</p>
                  <div class="row">
                    {#each ["yes", "no"] as answer}
                      <button
                        class="answer"
                        class:on={draft(week).realistic === answer}
                        data-answer={answer}
                        onclick={() => setDraft(week, { realistic: answer })}
                      >
                        {answer === "yes" ? "Yes" : "Not for this week"}
                      </button>
                    {/each}
                  </div>
                  <textarea
                    class="reflection-text"
                    rows="2"
                    maxlength="2000"
                    placeholder="anything worth remembering about this week"
                    value={draft(week).reflection}
                    oninput={(e) => setDraft(week, { reflection: e.target.value })}
                  ></textarea>
                  <div class="row">
                    <button class="save-reflection" onclick={() => saveReflection(week)} disabled={saving}>
                      Save
                    </button>
                    <button
                      class="ghost remove-past-goal"
                      onclick={() => removePastGoal(week)}
                      disabled={saving}
                      title="Forget this goal. The practice stays."
                    >
                      Remove goal
                    </button>
                  </div>
                </div>
              {:else}
                <p class="quiet no-goal">No goal was set for these days.</p>
              {/if}
            </li>
          {/each}
        </ul>
      </section>

      <!-- --------------------------------------------- where the time went -->
      <section class="where">
        <div class="section-head">
          <h2>Where the time went</h2>
          <span class="quiet">the last {HISTORY_DAYS} days</span>
        </div>

        <p class="statement-text totals">
          {formatDays(history?.days_practised)} of practice, {formatDuration(history?.seconds)} in
          total.
        </p>

        <div class="columns">
          <div>
            <h3>By piece</h3>
            {#if history?.by_score?.length}
              <ul class="spent by-score">
                {#each history.by_score as row (row.score_id)}
                  <li>
                    <a href={"#/score/" + row.score_id}>{row.title}</a>
                    <span class="quiet">{formatDuration(row.seconds)}</span>
                  </li>
                {/each}
              </ul>
            {:else}
              <p class="quiet">No practice against a piece in this window.</p>
            {/if}
          </div>
          <div>
            <h3>By kind of work</h3>
            {#if history?.by_activity?.length}
              <ul class="spent by-activity">
                {#each history.by_activity as row (row.activity)}
                  <li>
                    <span>{activityLabel(row.activity)}</span>
                    <span class="quiet">{formatDuration(row.seconds)}</span>
                  </li>
                {/each}
              </ul>
            {:else}
              <p class="quiet">Nothing recorded in this window.</p>
            {/if}
          </div>
        </div>
      </section>

      <!-- ------------------------------------------------- this week's work -->
      <section class="sessions">
        <div class="section-head">
          <h2>This week, session by session</h2>
        </div>
        {#if sessions.length}
          <ul class="session-list">
            {#each sessions as session (session.id)}
              <li class="session" data-session={session.id}>
                <span class="session-day">
                  <span class="day-date">{session.local_date}</span>
                  <!-- Whether that day was RECORDED or worked out from the
                       row's UTC timestamp, which is the difference between a
                       fact and an attribution. The server has always said
                       which (local_date_source) and nothing read it, so a day
                       that was inferred looked exactly like one somebody's own
                       clock reported - and for practice logged before the day
                       was stored at all, that is every row. Said next to the
                       date it qualifies, in visible text. -->
                  {#if session.local_date_source && session.local_date_source !== "recorded"}
                    <span class="day-inferred">inferred</span>
                  {/if}
                </span>
                <span class="session-what" class:orphaned={session.score_missing}>
                  {#if session.score_title}
                    <a href={"#/score/" + session.score_id}>{session.score_title}</a>
                  {:else}
                    {sessionSubject(session)}
                  {/if}
                </span>
                <span class="session-length">{formatDuration(session.seconds)}</span>
                <span class="quiet session-extra">
                  {[rangeLabel(session), tempoLabel(session), session.note]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="quiet">Nothing logged in this period yet.</p>
        {/if}
      </section>
      {/if}
    {/if}
  </main>
</div>

<style>
  .practice {
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

  /* Centred, because what is inside it is - see Metronome.svelte's prominent
     layout. Nothing else on this page is, so it needs saying here rather than
     being inherited. */
  .metronome-section {
    display: flex;
    flex-direction: column;
  }

  .metronome-section .hint {
    margin-bottom: 20px;
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

  h3 {
    font-size: 13px;
    color: var(--ink-dim);
    margin-bottom: 6px;
  }

  .quiet {
    color: var(--ink-dim);
    font-size: 13px;
  }

  .hint {
    color: var(--ink-dim);
    font-size: 13px;
    margin: 0 0 12px;
  }

  /* Deliberately NOT var(--danger). Nothing on this page is an error - a
     message here is something that did not save, or an amount that needs
     choosing, and it reads as information rather than as a fault. */
  .notice {
    background: var(--surface);
    border: 1px solid var(--line);
    border-left: 3px solid var(--brass);
    border-radius: 6px;
    padding: 10px 14px;
    margin: 0;
    font-size: 14px;
  }

  .intent {
    margin: 0 0 10px;
    font-size: 14px;
  }

  .scope {
    color: var(--brass-bright);
  }

  .intent-text {
    color: var(--ink-dim);
  }

  .statements {
    list-style: none;
    padding: 0;
    margin: 0 0 12px;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  /* One style for every statement, reached or not. A target that was not
     reached is not an error condition and is not coloured like one; the tick
     column is the only difference, and it is additive - something appears when
     a target IS met, rather than something being marked when it is not. */
  .statement {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 15px;
  }

  .statement .tick {
    width: 1em;
    color: var(--brass-bright);
  }

  /* Deliberately the colour it already inherits, which makes this rule a
     no-op - and it is meant to be. It exists so that "what does a reached
     target look like" has an answer in one place, and so that the answer is
     recorded as "the same as an unreached one" rather than looking like an
     oversight somebody should helpfully fill in with a green. The tick is the
     whole difference, and it is additive. */
  .statement.reached .statement-text {
    color: var(--ink);
  }

  .statement-text {
    font-variant-numeric: tabular-nums;
  }

  .statements.compact .statement {
    font-size: 14px;
  }

  .days-left {
    margin: 0 0 12px;
  }

  .no-goal {
    color: var(--ink-dim);
  }

  .strip {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 8px;
    margin: 14px 0 18px;
  }

  .day {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
  }

  .bar-track {
    width: 100%;
    height: 64px;
    display: flex;
    align-items: flex-end;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 5px;
    overflow: hidden;
  }

  .bar {
    width: 100%;
    background: var(--brass);
    border-radius: 4px 4px 0 0;
  }

  .day-label {
    font-size: 11px;
    color: var(--ink-dim);
  }

  .day-value {
    font-size: 11px;
    color: var(--ink-dim);
    font-variant-numeric: tabular-nums;
  }

  .goal-form {
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .goal-form h3 {
    color: var(--ink);
    font-family: var(--font-display);
    font-size: 15px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 14px;
  }

  .row.wide {
    align-items: stretch;
    flex-direction: column;
    gap: 5px;
  }

  .intent-input,
  .other-note {
    flex: 1;
    min-width: 180px;
  }

  .minutes-target,
  .other-minutes {
    width: 90px;
  }

  .actions {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }

  .primary {
    background: var(--brass);
    border-color: var(--brass);
    color: #241d0f;
  }

  .ghost {
    background: none;
    border-color: transparent;
    color: var(--ink-dim);
  }

  .ghost:hover {
    border-color: var(--line);
    color: var(--ink);
  }

  .weeks {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .past-week {
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 14px 16px;
  }

  .week-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 8px;
  }

  .week-label {
    font-family: var(--font-display);
    font-size: 15px;
  }

  .week-facts {
    color: var(--ink-dim);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }

  .reflection {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }

  .question {
    margin: 0;
    font-size: 14px;
    color: var(--ink);
  }

  .answer.on {
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  .reflection-text {
    width: 100%;
    resize: vertical;
    font: inherit;
    background: var(--surface);
    color: var(--ink);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 10px;
  }

  .columns {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 24px;
  }

  .spent {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 14px;
  }

  .spent li {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }

  .totals {
    margin: 0 0 14px;
  }

  .nothing-yet .statement-text {
    color: var(--ink-dim);
    max-width: 56ch;
    line-height: 1.6;
  }

  .session-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 14px;
  }

  .session {
    display: grid;
    /* 92px held the date and nothing else. The inferred badge has to sit
       BESIDE the date rather than under it - it qualifies that date, and a
       word wrapped onto its own line under a column of dates reads as a
       layout accident rather than as a note about the day. The two together
       need about 116px, measured rather than guessed, so the track is wide
       enough for both. A fixed width and not `auto`: each row is its own
       grid, so a track that sized to content would leave the middle column
       starting at a different x on the rows that carry a badge. */
    grid-template-columns: 120px 1fr 70px;
    gap: 10px;
    align-items: baseline;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--line);
  }

  .session-day,
  .session-length {
    color: var(--ink-dim);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }

  /* Quieter than the date it sits beside, and not styled as a fault - nothing
     went wrong, a day was attributed rather than recorded. nowrap on both
     halves, because "beside the date" is the whole point: without it the badge
     breaks onto its own line the moment the track is a pixel too narrow, and a
     test that asserts only the text cannot see that happen. */
  .session-day {
    white-space: nowrap;
  }

  .day-inferred {
    font-size: 11px;
    font-variant-numeric: normal;
    opacity: 0.75;
  }

  /* NOT .session-detail: that is the post-session panel in the viewer, and two
     components sharing a class name meant a test looking for the panel matched
     these rows instead - a locator that passed for the wrong reason. */
  .session-extra {
    grid-column: 2 / -1;
  }

  /* Dimmer than a title because there is no piece to click through to, and
     deliberately not otherwise different: this is practice that happened, not
     a row with something wrong with it. */
  .session-what.orphaned,
  .uncountable {
    color: var(--ink-dim);
  }

  .uncountable {
    margin: 0 0 12px;
    max-width: 52ch;
  }
</style>
