// The alphaTab Vite plugin only logs when it cannot find its assets, so a
// build can otherwise succeed and ship a score viewer that silently renders
// nothing. Fail the build instead.
import { existsSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dist = join(dirname(dirname(fileURLToPath(import.meta.url))), "dist");
const soundfont = join(dist, "soundfont", "sonivox.sf2");
const fontDir = join(dist, "font");

const problems = [];
if (!existsSync(soundfont)) problems.push("missing dist/soundfont/sonivox.sf2");
if (!existsSync(fontDir) || !readdirSync(fontDir).some((f) => f.startsWith("Bravura"))) {
  problems.push("missing Bravura font files in dist/font/");
}

if (problems.length) {
  console.error("asset check failed:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log("asset check passed");
