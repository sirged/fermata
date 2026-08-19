// Copies the alphaTab runtime assets (script for workers, music font,
// soundfont) into public/ so they are served at /alphatab/*.
import { cpSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(root, "node_modules", "@coderline", "alphatab", "dist");
const dest = join(root, "public", "alphatab");

mkdirSync(dest, { recursive: true });
cpSync(join(src, "font"), join(dest, "font"), { recursive: true });
cpSync(join(src, "soundfont"), join(dest, "soundfont"), { recursive: true });
cpSync(join(src, "alphaTab.min.js"), join(dest, "alphaTab.min.js"));
console.log("alphaTab assets copied to public/alphatab");
