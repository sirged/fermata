import { alphaTab } from "@coderline/alphatab-vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  // alphaTab() wires up the worker/audio-worklet bundling and copies its
  // font + soundfont assets into public/ — see src/lib/score-render.js for the
  // matching core.fontDirectory / player.soundFont paths.
  plugins: [svelte(), alphaTab()],
  server: {
    proxy: {
      "/api": "http://localhost:8080",
    },
  },
});
