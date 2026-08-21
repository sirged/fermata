// Wraps `playwright test` so the minimum-test-count guard (tests/minimum-
// tests.js) cannot be switched off by a `--reporter` flag on the command
// line - which npm run test:browser -- --reporter=list, or any CI step that
// grows one, would otherwise do silently.
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
// and enforces the same floor minimum-tests.js does - a belt to its
// suspenders, checked from a place a CLI flag has no way to reach.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { MINIMUM_TESTS } from "../tests/minimum-tests.js";

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
      "runs its own reporter set so the minimum-test-count guard cannot be bypassed.",
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

// Counted the same way tests/minimum-tests.js counts it - executed and not
// skipped, regardless of pass/fail/flaky, so this can never itself invent a
// "tests have gone missing" complaint on top of an ordinary failing run. The
// two are independent implementations of the identical rule on purpose:
// this file's whole reason to exist is covering the case where
// minimum-tests.js's own reporter never got to run at all, so it cannot be
// the only place that rule is expressed.
function countExecuted(summary) {
  const stats = summary?.stats;
  if (!stats) return null;
  return (stats.expected ?? 0) + (stats.unexpected ?? 0) + (stats.flaky ?? 0);
}

let executed = null;
try {
  const summary = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
  executed = countExecuted(summary);
} catch (e) {
  console.error(`run-browser-tests: could not read the JSON summary at ${summaryPath} - ${e.message}`);
}

if (!process.env.PLAYWRIGHT_ALLOW_PARTIAL) {
  if (executed == null) {
    console.error("run-browser-tests: the minimum-test-count floor could not be checked at all - failing closed.");
    process.exit(1);
  }
  if (executed < MINIMUM_TESTS) {
    console.error(
      `run-browser-tests: expected at least ${MINIMUM_TESTS} tests to run, only ${executed} did. ` +
        "Tests have gone missing, or the floor needs raising on purpose - see tests/minimum-tests.js.",
    );
    process.exit(1);
  }
}

process.exit(result.status ?? 1);
