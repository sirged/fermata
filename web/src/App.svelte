<script>
  import Library from "./lib/Library.svelte";
  import Viewer from "./lib/Viewer.svelte";
  import Settings from "./lib/Settings.svelte";
  import Practice from "./lib/Practice.svelte";
  import MetronomePage from "./lib/MetronomePage.svelte";

  function parse(hash) {
    const m = hash.match(/^#\/score\/(\d+)/);
    if (m) return { page: "score", id: Number(m[1]) };
    if (hash.startsWith("#/demo")) return { page: "demo" };
    if (hash.startsWith("#/settings")) return { page: "settings" };
    if (hash.startsWith("#/practice")) return { page: "practice" };
    if (hash.startsWith("#/metronome")) return { page: "metronome" };
    return { page: "library" };
  }

  let route = $state(parse(location.hash));

  $effect(() => {
    const onHash = () => (route = parse(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });
</script>

{#if route.page === "score"}
  <Viewer id={route.id} />
{:else if route.page === "demo"}
  <Viewer demo={true} />
{:else if route.page === "settings"}
  <Settings />
{:else if route.page === "practice"}
  <Practice />
{:else if route.page === "metronome"}
  <MetronomePage />
{:else}
  <Library />
{/if}
