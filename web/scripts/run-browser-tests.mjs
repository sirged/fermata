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
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
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

const summaryPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "fermata-test-summary-")), "summary.json");
const reporters = process.env.CI
  ? "list,github,html,./tests/minimum-tests.js,json"
  : "list,./tests/minimum-tests.js,json";

const result = spawnSync("npx", ["playwright", "test", `--reporter=${reporters}`, ...filteredArgs], {
  cwd: webRoot,
  stdio: "inherit",
  shell: true,
  env: { ...process.env, PLAYWRIGHT_JSON_OUTPUT_NAME: summaryPath },
});

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

let byFile = null;
try {
  const summary = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
  byFile = countByFile(summary);
} catch (e) {
  console.error(`run-browser-tests: could not read the JSON summary at ${summaryPath} - ${e.message}`);
}

if (!process.env.PLAYWRIGHT_ALLOW_PARTIAL) {
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
