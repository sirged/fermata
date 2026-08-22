import { alphaTab } from "@coderline/alphatab-vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// The Svelte compiler's warnings are treated as build failures. This is not
// tidiness: four of them shipped silently in this repository, and one was
// load-bearing. `state_referenced_locally` on a `control` prop was the compiler
// correctly pointing out that reading a prop once, at init, captures whatever
// it happened to be on the first render - which for a caller that builds its
// object in an effect is null, and led to a second metronome engine being
// constructed that nobody wanted. The other three were deliberate initial-value
// reads, and saying so in the code (with `untrack`) is cheap; having the one
// real defect stand out is worth it.
//
// Deliberately a hard failure rather than a printed warning. A warning nobody
// has to act on is a warning that accumulates until the real one is invisible,
// which is exactly what happened. To silence a warning on purpose, say so at
// the site - `untrack(...)`, or a `<!-- svelte-ignore ... -->` comment with a
// reason next to it - rather than adding a code to a list here.
//
// Know what this does NOT buy, because it is narrower than the defect that
// prompted it. It catches a particular SHAPE - a reactive value read at the top
// level of a component - not the mistake of capturing a value that arrives
// late. The same defect written any of these ways compiles silently:
//
//   - the read moved inside a function body;
//   - the read wrapped in `untrack(...)`, which is how a deliberate one is
//     declared, so the declaration and the bug are indistinguishable here;
//   - a value captured into a plain `const` from somewhere the compiler does
//     not track at all.
//
// So a green build means no warning, not "nothing captured too early". The
// question to ask at a call site is still whether every value it reads once is
// actually available on the first render - and for a prop the caller builds in
// an effect, it is not.
function failOnSvelteWarnings(warning, defaultHandler) {
  // Not a defect in our source and not actionable from here: a dependency's own
  // compiled Svelte, if one ever appears in the graph.
  if (warning.filename && warning.filename.includes("node_modules")) {
    defaultHandler?.(warning);
    return;
  }
  const where = warning.filename
    ? `${warning.filename}${warning.start ? `:${warning.start.line}:${warning.start.column}` : ""}`
    : "unknown file";
  throw new Error(
    `Svelte compiler warning treated as an error (see vite.config.js for why):\n` +
      `  ${where}\n  [${warning.code}] ${warning.message}\n` +
      `Fix it, or silence it at the site with a reason - not by exempting the code here.`,
  );
}

export default defineConfig({
  // alphaTab() wires up the worker/audio-worklet bundling and copies its
  // font + soundfont assets into public/ — see src/lib/score-render.js for the
  // matching core.fontDirectory / player.soundFont paths.
  plugins: [svelte({ onwarn: failOnSvelteWarnings }), alphaTab()],
  server: {
    proxy: {
      "/api": "http://localhost:8080",
    },
  },
});
