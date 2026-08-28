// Loads the additive spec-file test floor (issue #126) from the small JSON
// files in tests/spec-floors/.
//
// Why a directory of small files, and not one shared array or map in a
// single module: tried that first, and it does not actually solve the
// problem. A single file where every PR appends its own object literal
// right before the closing `];` still puts two concurrent PRs' insertions on
// adjacent lines with no unchanged line between them, and git's merge - even
// though the two edits do not touch the same entry, or even overlap in what
// they mean - reported CONFLICT (content) on exactly that shape, reproduced
// by hand while building this. A directory sidesteps the problem
// structurally instead of by convention: two PRs that each ADD A NEW FILE
// cannot conflict with each other, full stop, because git only conflicts
// when both sides touch the same file.
//
// One JSON file per concern, named <spec-file-basename>-<issue-or-slug>.json
// so a glance at the directory says which spec file and which change earned
// an entry. More than one file may claim the same "file" field - the guard
// (tests/spec-floor-guard.js) sums them - so a PR that adds tests to a spec
// file someone else is concurrently also adding to just adds its own new
// file; it never has to edit theirs.
//
// Shape of each file:
//   {
//     "file": "tests/browser/x.spec.js",  // relative to web/, forward slashes
//     "count": 12,                         // tests this concern is claiming
//     "reason": "issue #NN: one line saying what earned these tests."
//   }
//
// Add a new file when you add tests. Never edit someone else's file just to
// change its number - if a count needs correcting, that correction is its
// own new file too, with its own reason.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const floorsDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "spec-floors");

export function loadSpecFloors() {
  const entries = [];
  for (const name of fs.readdirSync(floorsDir).sort()) {
    if (!name.endsWith(".json")) continue;
    const fullPath = path.join(floorsDir, name);
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(fullPath, "utf8"));
    } catch (e) {
      throw new Error(`tests/spec-floors/${name} is not valid JSON: ${e.message}`);
    }
    if (
      typeof parsed.file !== "string" ||
      !Number.isInteger(parsed.count) ||
      parsed.count <= 0 ||
      typeof parsed.reason !== "string" ||
      !parsed.reason
    ) {
      throw new Error(
        `tests/spec-floors/${name} must have a string "file", a positive integer "count", and a non-empty ` +
          `string "reason".`,
      );
    }
    entries.push({ file: parsed.file, count: parsed.count, reason: parsed.reason, source: name });
  }
  if (entries.length === 0) {
    throw new Error("tests/spec-floors/ has no entries - the floor would silently accept zero tests running.");
  }
  return entries;
}
