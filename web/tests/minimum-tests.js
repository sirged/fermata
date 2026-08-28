// Fails the run when the suite has SHRUNK.
//
// Playwright errors loudly when it finds zero tests and says nothing at all
// when it finds some of them. So renaming a spec file, a `testMatch` edit, a
// stray `test.skip`, or a `--grep` added to a CI invocation can delete an
// entire area of coverage and still report "12 passed" and exit 0 - which is
// how a suite ends up green while the thing it was written to prove is no
// longer checked at all.
//
// The floor is per spec file, not one contested total (issue #126): every
// file that runs has to be named by at least one entry under
// tests/spec-floors/, and the entries naming a file are not allowed to sum
// to more than the tests that file actually ran. See tests/spec-floors.js
// for the format (and why it is a directory of small files rather than one
// shared file) and tests/spec-floor-guard.js for the comparison both this
// reporter and scripts/run-browser-tests.mjs apply to it.
//
// The floor is deliberate, not automatic: add an entry when tests are added.
// A mismatch here is not a broken test, it is a suite that is not the suite
// this repository expects.
//
// Deliberately not skipped when the run is filtered: "CI quietly grew a
// --grep" is one of the cases this exists to catch, so a filter cannot be the
// thing that switches the check off. Narrowing a run on purpose is what the
// escape hatch below is for, and CI never sets it.
//
// This is also read directly by scripts/run-browser-tests.mjs, which is the
// OTHER half of this guard - see that file's own comment for why counting
// here is not, by itself, enough. Both apply tests/spec-floor-guard.js to
// whatever they each independently learn ran.
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadSpecFloors } from "./spec-floors.js";
import { checkSpecFloors } from "./spec-floor-guard.js";

const webRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

// Playwright hands onTestEnd an absolute, OS-native path. The entries under
// tests/spec-floors/ are written relative to web/ with forward slashes, on
// every platform, so both sides speak the same key.
function relativeSpecPath(absoluteFile) {
  return path.relative(webRoot, absoluteFile).split(path.sep).join("/");
}

export default class MinimumTests {
  constructor() {
    // Tests that actually ran and were not skipped - NOT suite.allTests()'s
    // count, which was this class's original approach and has a real hole:
    // it counts a test.skip()'d test exactly the same as one that passed, so
    // an entire spec silently marked skip (the same class of failure as a
    // stray --grep, just spelled differently) sailed straight through this
    // guard. onTestEnd only fires for a test Playwright actually attempted,
    // so a --grep-filtered-out test is excluded automatically, same as
    // before - but now a skipped one is excluded too. Counted regardless of
    // pass/fail/flaky: a legitimately failing suite must not ALSO report
    // "tests have gone missing" on top of its real failure, which is exactly
    // how a guard teaches people to stop reading it.
    //
    // Kept per spec file rather than as one running total, which is the
    // whole point of #126: the guard can now name which file came up short
    // instead of only reporting a suite-wide number nobody can act on.
    this.executedByFile = new Map();
  }

  onTestEnd(test, result) {
    if (result.status === "skipped") return;
    const file = relativeSpecPath(test.location.file);
    this.executedByFile.set(file, (this.executedByFile.get(file) ?? 0) + 1);
  }

  async onEnd(result) {
    if (process.env.PLAYWRIGHT_ALLOW_PARTIAL) return;
    const { ok, problems } = checkSpecFloors(this.executedByFile, loadSpecFloors());
    if (ok) return;
    console.error(
      "\nTests have gone missing (skipped, filtered, deleted, or unwired), or " +
        "tests/spec-floors/ needs a new entry on purpose:\n" +
        problems.map((p) => `  - ${p}`).join("\n") +
        "\nTo run a deliberate subset, set PLAYWRIGHT_ALLOW_PARTIAL=1.",
    );
    return { status: result.status === "passed" ? "failed" : result.status };
  }
}
