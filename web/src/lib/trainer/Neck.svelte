<script>
  // The interactive fretboard (issue #25) - a reusable, hand-rolled SVG
  // component every fretboard trainer (#26, #27, #29) draws on. It is a pure
  // display component with no game logic and no server calls of its own,
  // same as this app's other primitives (Metronome, the tab renderer): what
  // note sounds where, and which position was tapped, are the whole of what
  // it knows.
  //
  // PROPS, chosen for reuse rather than for the one drill that exists today:
  //
  //   strings       [{ number, midi }], in ANY order - see neck.js's
  //                 stringsFromInstrument for how a caller gets one from a
  //                 saved instrument or the standard-guitar fallback. Sorted
  //                 here by number, ascending, so string 1 (the highest
  //                 pitch) always draws at the TOP - the same convention
  //                 server/fermata/musicxml.py documents ("string 1 is
  //                 assigned to the top tab line").
  //   fretCount     how many frets to draw.
  //   startFret     the lowest fret drawn - lets a drill scope itself to a
  //                 range (issue #27's "particular strings, a fret range")
  //                 without this component knowing anything about scoping.
  //   markers       [{ string, fret, kind }], kind one of 'target',
  //                 'correct', 'incorrect', 'hint' - explicit overlays a
  //                 caller places, keyed by position.
  //   highlightNote a pitch class ("C#") to mark, as 'hint', at EVERY
  //                 position that sounds it - "highlight a note across the
  //                 neck" as a built-in feature rather than something every
  //                 caller recomputes from neck.js's positionsForNote by
  //                 hand. An explicit marker at the same position always
  //                 wins (see markerFor), so a caller's own correct/incorrect
  //                 mark is never overwritten by this.
  //   showLabels    show every position's note name, not only marked ones -
  //                 a reference/explore mode. A drill leaves this false so
  //                 the labels cannot give an unanswered question away; only
  //                 its own markers reveal anything.
  //   interactive   whether a tap fires onTap at all.
  //   onTap         (stringNumber, fret, note) => void, called on a tap of a
  //                 playable position. Not called for a tap on a string
  //                 number this component was not given, or a fret beyond
  //                 MIDI's range - see neck.js's noteAt.
  //
  // GEOMETRY is plain SVG in a viewBox, scaled to whatever width the caller's
  // layout gives it (width: 100%, height: auto) - see the styles below for
  // how that stays legible and touch-target-sized down to tablet width
  // without a break-point, which issue #25 asks for directly.
  import { DEFAULT_FRET_COUNT, inlayDots, noteAt, pitchClass, posKey } from "./neck.js";

  let {
    strings = [],
    fretCount = DEFAULT_FRET_COUNT,
    startFret = 0,
    markers = [],
    highlightNote = null,
    showLabels = false,
    interactive = false,
    onTap = null,
  } = $props();

  const sorted = $derived([...strings].sort((a, b) => a.number - b.number));

  // ---- geometry, in SVG user units -----------------------------------------
  const NUT_GAP = 44; // space left of the nut wire, for open-string markers
  const FRET_WIDTH = 62;
  const STRING_GAP = 34;
  const MARGIN = 22;
  const DOT_R = 5;
  const MARKER_R = 14;

  const width = $derived(NUT_GAP + Math.max(1, fretCount) * FRET_WIDTH + MARGIN);
  const height = $derived(MARGIN * 2 + Math.max(0, sorted.length - 1) * STRING_GAP);

  function stringY(index) {
    return MARGIN + index * STRING_GAP;
  }

  function fretWireX(fret) {
    return NUT_GAP + fret * FRET_WIDTH;
  }

  function positionX(fret) {
    return fret === 0 ? NUT_GAP / 2 : NUT_GAP + (fret - 0.5) * FRET_WIDTH;
  }

  const fretWires = $derived(
    Array.from({ length: Math.max(1, fretCount) + 1 }, (_, i) => i),
  );

  const drawnFrets = $derived(
    Array.from(
      { length: Math.max(0, fretCount - Math.max(0, startFret) + 1) },
      (_, i) => Math.max(0, startFret) + i,
    ),
  );

  const dotFrets = $derived(drawnFrets.filter((f) => f > 0 && inlayDots(f) > 0));

  // markers, keyed by position - the last one wins if a caller ever passes
  // two for the same spot, which is a caller error but should not crash.
  const explicit = $derived(
    new Map((markers ?? []).map((m) => [posKey(m.string, m.fret), m.kind])),
  );

  const highlighted = $derived(
    highlightNote
      ? new Set(
          sorted.flatMap((s) =>
            drawnFrets
              .filter((fret) => {
                const midi = noteAt(sorted, s.number, fret);
                return midi != null && pitchClass(midi) === highlightNote;
              })
              .map((fret) => posKey(s.number, fret)),
          ),
        )
      : new Set(),
  );

  function markerFor(stringNumber, fret) {
    const key = posKey(stringNumber, fret);
    // Explicit markers always win over the auto-highlight - a caller's own
    // correct/incorrect mark on the position that was just answered must not
    // be swallowed by a highlightNote covering the same spot.
    return explicit.get(key) ?? (highlighted.has(key) ? "hint" : null);
  }

  function tap(stringNumber, fret, note) {
    if (!interactive || typeof onTap !== "function") return;
    onTap(stringNumber, fret, note);
  }
</script>

<div class="neck" data-string-count={sorted.length} data-fret-count={fretCount}>
  <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Fretboard">
    <!-- the fingerboard itself -->
    <rect
      class="board"
      x={NUT_GAP}
      y={MARGIN - STRING_GAP / 2}
      width={Math.max(1, fretCount) * FRET_WIDTH}
      height={Math.max(0, sorted.length - 1) * STRING_GAP + STRING_GAP}
    />

    <!-- inlay dots, centred vertically (or symmetric around centre for a
         double dot at 12/24) -->
    {#each dotFrets as fret (fret)}
      {#if inlayDots(fret) === 1}
        <circle
          class="inlay"
          cx={positionX(fret)}
          cy={MARGIN + (Math.max(0, sorted.length - 1) * STRING_GAP) / 2}
          r={DOT_R}
        />
      {:else}
        <circle class="inlay" cx={positionX(fret)} cy={MARGIN + STRING_GAP * 0.9} r={DOT_R} />
        <circle
          class="inlay"
          cx={positionX(fret)}
          cy={MARGIN + Math.max(0, sorted.length - 1) * STRING_GAP - STRING_GAP * 0.9}
          r={DOT_R}
        />
      {/if}
    {/each}

    <!-- fret wires; the nut (fret 0) drawn thicker -->
    {#each fretWires as fret (fret)}
      <line
        class="wire"
        class:nut={fret === 0}
        x1={fretWireX(fret)}
        x2={fretWireX(fret)}
        y1={MARGIN - STRING_GAP / 2}
        y2={MARGIN + Math.max(0, sorted.length - 1) * STRING_GAP + STRING_GAP / 2}
      />
    {/each}

    <!-- strings -->
    {#each sorted as string, i (string.number)}
      <line
        class="string"
        x1={NUT_GAP - 6}
        x2={fretWireX(Math.max(1, fretCount))}
        y1={stringY(i)}
        y2={stringY(i)}
      />
      <text class="string-label" x={4} y={stringY(i) + 4}>{string.number}</text>
    {/each}

    <!-- positions: one tappable target per (string, drawn fret) -->
    {#each sorted as string, i (string.number)}
      {#each drawnFrets as fret (fret)}
        {@const midi = noteAt(sorted, string.number, fret)}
        {#if midi != null}
          {@const note = pitchClass(midi)}
          {@const kind = markerFor(string.number, fret)}
          <!-- role is static ("button", not conditional on `interactive`) so
               the compiler can see this is genuinely a button - a role that
               only sometimes applies is what triggered
               a11y_no_noninteractive_tabindex here. tabindex still varies:
               -1 pulls a non-interactive neck (the ordinary case while a
               position-to-note question is showing) out of tab order
               entirely, so a display-only fretboard is not 400 tab stops. -->
          <g
            class="position"
            class:interactive
            data-string={string.number}
            data-fret={fret}
            data-note={note}
            data-marker={kind ?? ""}
            role="button"
            aria-label={`String ${string.number}, fret ${fret}`}
            aria-disabled={!interactive}
            tabindex={interactive ? 0 : -1}
            onclick={() => tap(string.number, fret, note)}
            onkeydown={(e) => {
              if (interactive && (e.key === "Enter" || e.key === " ")) {
                e.preventDefault();
                tap(string.number, fret, note);
              }
            }}
          >
            <!-- a large, invisible hit area - the touch target is bigger
                 than what is drawn, per this app's tablet-at-a-music-stand
                 sizing (issue #25's "big touch targets"). -->
            <circle class="hit" cx={positionX(fret)} cy={stringY(i)} r={MARKER_R + 8} />
            {#if kind}
              <circle class="mark {kind}" cx={positionX(fret)} cy={stringY(i)} r={MARKER_R} />
            {/if}
            <!-- Never shown for a bare 'target' marker - that marker names
                 the position a position-to-note question is ASKING about,
                 and printing the note on it would answer the question for
                 free. Every other kind is either revealed already
                 (correct/incorrect/hint) or explicitly requested
                 (showLabels). -->
            {#if showLabels || (kind && kind !== "target")}
              <text class="note-label {kind ?? ''}" x={positionX(fret)} y={stringY(i) + 4}
                >{note}</text
              >
            {/if}
          </g>
        {/if}
      {/each}
    {/each}
  </svg>
</div>

<style>
  .neck {
    width: 100%;
  }

  svg {
    display: block;
    width: 100%;
    height: auto;
  }

  .board {
    fill: var(--surface);
    stroke: var(--line);
  }

  .wire {
    stroke: var(--ink-dim);
    stroke-width: 1.5;
  }

  .wire.nut {
    stroke: var(--ink);
    stroke-width: 4;
  }

  .string {
    stroke: var(--ink-dim);
    stroke-width: 1.5;
  }

  .string-label {
    fill: var(--ink-dim);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }

  .inlay {
    fill: var(--line);
  }

  .position.interactive {
    cursor: pointer;
  }

  .hit {
    fill: transparent;
    /* No stroke: this is a touch target, not a drawn element - see the
       markers below for what is actually visible. */
  }

  .position.interactive:focus-visible .hit {
    fill: none;
    stroke: var(--brass);
    stroke-width: 2;
  }

  .mark {
    stroke-width: 2;
  }

  /* The question being asked right now - waiting for an answer. Brass, like
     everything this app already uses for "here" (the metronome's beat, the
     current page). Not yet a verdict. */
  .mark.target {
    fill: var(--bg-raised);
    stroke: var(--brass-bright);
    stroke-width: 3;
  }

  .mark.correct {
    fill: var(--brass);
    stroke: var(--brass-bright);
  }

  /* Deliberately NOT --danger. A wrong answer here is information, the same
     rule ear-training.js and practice.js apply throughout this application -
     see FretToNote.svelte's own note. Marked so it can be told apart from a
     correct one, in the same muted ink the rest of this page uses for "not
     the thing", never in the colour this app reserves for a fault. */
  .mark.incorrect {
    fill: var(--bg-raised);
    stroke: var(--ink-dim);
  }

  /* A revealed position that was not itself tapped - part of the answer,
     shown after the fact, or a highlighted note. Quiet on purpose: this is
     not something to answer, only to notice. */
  .mark.hint {
    fill: var(--line);
    stroke: var(--ink-dim);
    stroke-width: 1.5;
  }

  .note-label {
    fill: var(--ink);
    font-size: 12px;
    font-weight: 600;
    text-anchor: middle;
    pointer-events: none;
    font-variant-numeric: tabular-nums;
  }

  /* Dark text on the brass fill 'correct' uses - the same pairing
     button.primary uses elsewhere in this application. */
  .note-label.correct {
    fill: #1a1509;
  }

  .note-label.target {
    fill: var(--brass-bright);
  }
</style>
