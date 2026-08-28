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
// by hand while building this. A directory sidesteps most of the problem
// structurally instead of by convention: a PR that adds a brand new file
// cannot conflict with another PR's brand new file - git only conflicts when
// both sides touch the same file, and two different filenames are never the
// same file. That leaves exactly one remaining way to collide: two PRs
// independently choosing the identical filename. The naming convention below
// closes that gap too - see the "add" case.
//
// One JSON file per concern, named <spec-file-basename>-<issue-or-slug>.json
// so a glance at the directory says which spec file and which change earned
// an entry, AND so two concurrent PRs adding entries for the same spec file
// still pick different filenames (different issue numbers, different slugs)
// rather than colliding on the bare basename. More than one file may claim
// the same "file" field - the guard (tests/spec-floor-guard.js) sums them -
// so a PR that adds tests to a spec file someone else is concurrently also
// adding to just adds its own new, distinctly-named file; it never has to
// edit theirs.
//
// Shape of each file:
//   {
//     "file": "tests/browser/x.spec.js",  // relative to web/, forward slashes
//     "count": 12,                         // tests this concern is claiming
//     "reason": "issue #NN: one line saying what earned these tests."
//   }
//
// Add: a new file, named <spec-file-basename>-<issue-or-slug>.json. Never
// edit someone else's file just to raise its number - that is the rewrite
// this design exists to avoid.
//
// Remove or reduce: if you are the one deleting or weakening tests in a
// file, you are already editing that file in this same commit - edit or
// delete the specific entry that becomes stale as part of that commit, the
// same way you would update any other comment that stopped being true. This
// is not the concurrent-PR collision the directory design solves (nobody
// else is fighting you for that file's line), so there is no need for a
// separate "correction" entry type; the loader rejects a non-positive count
// on purpose; there is no such thing as an entry that only subtracts.
//
// Skips: no test in this suite may be conditionally skipped (an environment-
// gated test.skip()) - a test that sometimes runs and sometimes does not is
// exactly the silent shrinkage this guard exists to catch, and the guard
// cannot tell "this environment legitimately can't run it" from "this
// quietly stopped running." If a test genuinely cannot run in some
// environment, delete it (or gate it out of the suite entirely, the same way
// tests/browser vs tests/unit are divided by environment already) and lower
// that file's entry in the same commit, per the "remove or reduce" rule
// above.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const floorsDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "spec-floors");

export function loadSpecFloors() {
  const entries = [];
  for (const name of fs.readdirSync(floorsDir).sort()) {
    const fullPath = path.join(floorsDir, name);
    // Every file in this directory counts toward the floor, no exceptions -
    // silently skipping anything (a stray README, a misnamed ".JSON", an
    // editor swap file) is exactly the failure mode this guard exists to
    // prevent, just moved one level up: a file that was meant to raise the
    // floor and quietly did not. So a file that is not a readable, exactly
    // lowercase-".json" file fails the build loudly instead.
    if (!fs.statSync(fullPath).isFile() || !name.endsWith(".json")) {
      throw new Error(
        `tests/spec-floors/${name} is not a readable, lowercase-".json" file. Every file under tests/spec-floors/ ` +
          `is expected to be one and to count toward the floor - rename it, or move it out of this directory.`,
      );
    }
    let parsed;
    try {
      parsed = JSON.parse(fs.readFileSync(fullPath, "utf8"));
    } catch (e) {
      throw new Error(`tests/spec-floors/${name} is not valid JSON: ${e.message}`);
    }
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      Array.isArray(parsed) ||
      typeof parsed.file !== "string" ||
      !Number.isInteger(parsed.count) ||
      parsed.count <= 0 ||
      typeof parsed.reason !== "string" ||
      !parsed.reason
    ) {
      throw new Error(
        `tests/spec-floors/${name} must contain a JSON object with a string "file", a positive integer "count", ` +
          `and a non-empty string "reason".`,
      );
    }
    entries.push({ file: parsed.file, count: parsed.count, reason: parsed.reason, source: name });
  }
  if (entries.length === 0) {
    throw new Error("tests/spec-floors/ has no entries - the floor would silently accept zero tests running.");
  }
  return entries;
}
