<script>
  // The metronome on its own, because "I just want a metronome" is a real
  // thing to want from a practice tool and should not require opening a piece
  // first.
  //
  // There is almost nothing here, and that is the point: the metronome is a
  // general tool (Metronome.svelte over metronome-engine.js), and this page
  // is one of its call sites, not a second implementation. Compare
  // TabViewer's, which differs only in what it pre-fills from.
  import Metronome from "./Metronome.svelte";

  // With no piece and no goal to read a tempo from, the last setting IS the
  // context - so this is the one site that remembers. Per-device, via
  // localStorage; see the note on persistence in Metronome.svelte.
  const REMEMBER_KEY = "fermata:metronome:standalone";

  let enabled = $state(false);
</script>

<div class="page">
  <header>
    <a class="back" href="#/">← Library</a>
    <h1>Metronome</h1>
  </header>

  <main>
    <Metronome bind:enabled prominent={true} remember={REMEMBER_KEY} />

    <p class="quiet">
      Set the tempo, the time signature and how finely each beat is clicked. The first click
      of each bar is accented unless you turn that off. What you leave here is what you will
      find next time on this device.
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

  /* Centred and vertically generous, because a page whose whole content is
     one number belongs in the middle of the screen rather than tucked under
     the header - and because on a phone propped against a music stand the
     middle is where a glance lands. */
  main {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 28px;
    padding: 40px 20px;
  }

  .quiet {
    max-width: 46ch;
    margin: 0;
    text-align: center;
    color: var(--ink-dim);
    font-size: 13px;
    line-height: 1.5;
  }
</style>
