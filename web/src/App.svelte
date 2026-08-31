<script>
  import Library from "./lib/Library.svelte";
  import Viewer from "./lib/Viewer.svelte";
  import Settings from "./lib/Settings.svelte";
  import Practice from "./lib/Practice.svelte";
  import MetronomePage from "./lib/MetronomePage.svelte";
  import EarTraining from "./lib/EarTraining.svelte";
  import ScoreProgress from "./lib/ScoreProgress.svelte";
  import FretToNote from "./lib/trainer/FretToNote.svelte";

  function parse(hash) {
    // Checked BEFORE the bare score route, which matches a prefix: without
    // this, #/score/7/practice opens the viewer for score 7 and the progress
    // page is unreachable by URL.
    const progress = hash.match(/^#\/score\/(\d+)\/practice/);
    if (progress) return { page: "score-progress", id: Number(progress[1]) };
    const m = hash.match(/^#\/score\/(\d+)/);
    if (m) return { page: "score", id: Number(m[1]) };
    if (hash.startsWith("#/demo")) return { page: "demo" };
    if (hash.startsWith("#/settings")) return { page: "settings" };
    if (hash.startsWith("#/practice")) return { page: "practice" };
    if (hash.startsWith("#/metronome")) return { page: "metronome" };
    if (hash.startsWith("#/ear-training")) return { page: "ear-training" };
    if (hash.startsWith("#/fretboard")) return { page: "fretboard" };
    return { page: "library" };
  }

  let route = $state(parse(location.hash));

  $effect(() => {
    const onHash = () => (route = parse(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });
</script>

{#if route.page === "score-progress"}
  <ScoreProgress id={route.id} />
{:else if route.page === "score"}
  <Viewer id={route.id} />
{:else if route.page === "demo"}
  <Viewer demo={true} />
{:else if route.page === "settings"}
  <Settings />
{:else if route.page === "practice"}
  <Practice />
{:else if route.page === "metronome"}
  <MetronomePage />
{:else if route.page === "ear-training"}
  <EarTraining />
{:else if route.page === "fretboard"}
  <FretToNote />
{:else}
  <Library />
{/if}
