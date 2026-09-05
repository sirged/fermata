// Wraps `playwright test` so the spec-floor guard (tests/minimum-tests.js,
// tests/spec-floors/) cannot be switched off by a `--reporter` flag on the
// command line - which npm run test:browser -- --reporter=list, or any CI
// step that grows one, would otherwise do silently.
//
// Playwright's own --reporter CLI flag REPLACES the config file's entire
// `reporter` array; it does not merge with it. minimum-tests.js is only
// wired in through that array, so a caller-supplied --reporter drops it
// entirely - Playwright never even loads the file, so nothing inside it can
// notice or object. That is the same shape of failure minimum-tests.js's own
// docstring already worries about for a stray --grep, just one layer further
// out: a --grep only had to be caught FROM INSIDE the reporter, because the
// reporter itself still ran. A --reporter override means the reporter does
// not run at all.
//
// So the guard is checked here instead, entirely outside Playwright's
// reporter system: this script owns the actual `playwright test` invocation,
// strips any --reporter the caller passed before it ever reaches Playwright,
// and always adds its own JSON summary reporter (which nothing here treats
// as optional). It then reads that summary itself, after the process exits,
// and applies tests/spec-floor-guard.js's rule to counts it derived on its
// own from the JSON tree - a belt to minimum-tests.js's suspenders, checked
// from a place a CLI flag has no way to reach. The two deliberately learn
// "what ran" two different ways - one from Playwright's in-process reporter
// callbacks, one from parsing the JSON file back out afterward - so a bug or
// an omission in one path is not also a bug in the other.
//
// Also not evaluated when the run itself stopped early - see
// bailedOnMaxFailures below - for the same reason tests/minimum-tests.js
// skips its own check on an interrupted or timed-out run: every file the run
// had not reached yet would otherwise look exactly like a deleted one.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import { loadSpecFloors } from "../tests/spec-floors.js";
import { checkSpecFloors } from "../tests/spec-floor-guard.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.dirname(here);

const passedArgs = process.argv.slice(2);
const filteredArgs = [];
for (let i = 0; i < passedArgs.length; i++) {
  const arg = passedArgs[i];
  if (arg === "--reporter") {
    i += 1; // also drop the value that follows ("--reporter" "list")
    continue;
  }
  if (arg.startsWith("--reporter=")) continue;
  filteredArgs.push(arg);
}
if (filteredArgs.length !== passedArgs.length) {
  console.error(
    "run-browser-tests: a --reporter argument was dropped - this script always " +
      "runs its own reporter set so the spec-floor guard cannot be bypassed.",
  );
}

// #110: a static, approximate check for the "click, then read out of band"
// race that has recurred three times (#82, #100, and a refused-scan test
// written after that sweep) despite two prior sweeps. Run before Playwright
// even starts, so a new instance of the pattern fails fast and cheaply
// rather than waiting on a browser job to flake under load. See
// check-out-of-band-reads.mjs for what it does and does not catch.
//
// process.execPath and shell: false, for the same reason the Playwright
// spawn below uses them: this script should not have a command line anywhere
// in it that an argument could be re-parsed by. These args happen to be
// fixed and space-free, so nothing was broken here - but "no shell" is the
// property worth being able to state about the whole file rather than about
// one call in it, and it also runs this guard under the same node binary
// that is running this script instead of whichever one a PATH lookup finds.
const guard = spawnSync(process.execPath, ["scripts/check-out-of-band-reads.mjs"], {
  cwd: webRoot,
  stdio: "inherit",
});
if (guard.status !== 0) {
  process.exit(guard.status ?? 1);
}

const summaryPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "fermata-test-summary-")), "summary.json");
const reporters = process.env.CI
  ? "list,github,html,./tests/minimum-tests.js,json"
  : "list,./tests/minimum-tests.js,json";

// Resolved directly and run through node (process.execPath) with shell: false
// rather than handed to `npx playwright test … --grep "<phrase>"` under
// shell: true. On Windows, spawnSync with shell: true does not invoke argv
// as separate process-creation arguments - it joins them into one cmd.exe
// command line, and Node's quoting only covers characters cmd.exe treats
// specially, not internal spaces. A multi-word --grep value came out on the
// far side as several bare words, so Playwright saw --grep followed by only
// its first word and ran the whole file instead of the one matching test.
// Resolving the CLI entry point and invoking it without a shell hands argv
// to the child process array-for-array on every platform - there is no
// command line for a space to get lost in, on Windows or POSIX.
const playwrightCli = createRequire(import.meta.url).resolve("@playwright/test/cli");

const result = spawnSync(process.execPath, [playwrightCli, "test", `--reporter=${reporters}`, ...filteredArgs], {
  cwd: webRoot,
  stdio: "inherit",
  env: { ...process.env, PLAYWRIGHT_JSON_OUTPUT_NAME: summaryPath },
});

// spec.file inside the JSON tree is relative to config.rootDir (Playwright's
// testDir), and every entry in tests/spec-floors/ is written as
// "tests/browser/x.spec.js" relative to web/. rootDir is normally
// .../web/tests (playwright.config.js sets testDir: "tests"), which is where
// the hardcoded "tests/" prefix below comes from - asserted here rather than
// assumed, because if testDir ever moved, this script would silently start
// comparing the wrong keys and every file would look deleted.
function assertRootDirIsTests(summary) {
  const rootDir = summary?.config?.rootDir ?? "";
  if (!rootDir.endsWith("/tests")) {
    throw new Error(
      `run-browser-tests: expected the JSON summary's config.rootDir to end in "/tests" (playwright.config.js's ` +
        `testDir), got ${JSON.stringify(rootDir)} - the "tests/" prefix this script builds spec-floor keys with ` +
        `would be wrong.`,
    );
  }
}

// Walks the JSON reporter's suite tree (one top-level suite per spec file,
// possibly with further suites nested inside for a test.describe block) and
// counts, per spec file, the tests whose outcome was not "skipped" - the
// same rule minimum-tests.js applies from inside the run, arrived at
// independently from the file Playwright wrote rather than from its own
// onTestEnd callbacks.
function countByFile(summary) {
  const byFile = new Map();
  function walk(suite) {
    for (const spec of suite.specs ?? []) {
      const file = `tests/${spec.file}`;
      for (const test of spec.tests ?? []) {
        if (test.status === "skipped") continue;
        byFile.set(file, (byFile.get(file) ?? 0) + 1);
      }
    }
    for (const sub of suite.suites ?? []) walk(sub);
  }
  for (const suite of summary?.suites ?? []) walk(suite);
  return byFile;
}

// The in-process reporter's onEnd sees FullResult.status directly, which is
// "interrupted" for a real SIGINT but - confirmed by hand, not assumed -
// "failed" for a run that stopped early because --max-failures/-x was
// reached; nothing in that status distinguishes the two. This script has no
// access to FullResult at all (it only reads the JSON back afterward), so it
// uses the JSON summary's own signal instead: config.maxFailures is the
// configured ceiling, and stats.unexpected is exactly the count Playwright
// stops scheduling more tests against once it is reached. Reaching it means
// every spec file the run had not gotten to yet has zero recorded tests,
// which looks identical to that file being deleted or unwired - the real
// failure is already on screen from the run itself, so the floor is not
// worth evaluating (and would only bury it) this time.
function bailedOnMaxFailures(summary) {
  const maxFailures = summary?.config?.maxFailures ?? 0;
  const unexpected = summary?.stats?.unexpected ?? 0;
  return maxFailures > 0 && unexpected >= maxFailures;
}

let summary = null;
let byFile = null;
try {
  summary = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
  assertRootDirIsTests(summary);
  byFile = countByFile(summary);
} catch (e) {
  console.error(`run-browser-tests: could not read the JSON summary at ${summaryPath} - ${e.message}`);
}

if (!process.env.PLAYWRIGHT_ALLOW_PARTIAL && !(summary && bailedOnMaxFailures(summary))) {
  if (byFile == null) {
    console.error("run-browser-tests: the spec-floor guard could not be checked at all - failing closed.");
    process.exit(1);
  }
  const { ok, problems } = checkSpecFloors(byFile, loadSpecFloors());
  if (!ok) {
    console.error(
      "run-browser-tests: tests have gone missing (skipped, filtered, deleted, or unwired), or " +
        "tests/spec-floors/ needs a new entry on purpose:\n" +
        problems.map((p) => `  - ${p}`).join("\n"),
    );
    process.exit(1);
  }
}

process.exit(result.status ?? 1);
