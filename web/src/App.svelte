<script>
  import Library from "./lib/Library.svelte";
  import Viewer from "./lib/Viewer.svelte";

  function parse(hash) {
    const m = hash.match(/^#\/score\/(\d+)/);
    if (m) return { page: "score", id: Number(m[1]) };
    if (hash.startsWith("#/demo")) return { page: "demo" };
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
{:else}
  <Library />
{/if}
