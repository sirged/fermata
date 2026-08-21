import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));

// These tests drive the REAL backend serving the REAL build, because a passing
// build proves nothing about whether anything rendered - this project has
// already shipped a viewer that compiled cleanly and drew nothing. So the
// build has to exist first, and saying so plainly beats a suite that fails
// with a blank page.
if (!fs.existsSync(path.join(here, "dist", "index.html"))) {
  throw new Error("web/dist is missing - run `npm run build` before `npm run test:browser`.");
}

// A throwaway library and config per run, so the suite never sees a previous
// run's rows and never touches a real install's database.
// Created once per RUN, not once per process. This file is evaluated again in
// every worker, so an unconditional mkdtemp gave the worker a different scratch
// directory from the one the server was started with - which is invisible until
// a spec needs the real path, and then quietly does nothing. The runner
// evaluates this before it spawns any worker, so the variable is inherited.
const scratch =
  process.env.FERMATA_TEST_SCRATCH ??
  fs.mkdtempSync(path.join(os.tmpdir(), "fermata-browser-"));
process.env.FERMATA_TEST_SCRATCH = scratch;
// Published to the specs, so a test that needs a real score can put a file in
// the library and take it out again afterwards. Without a path, the only way to
// cover anything score-shaped was to weaken the guard that stops this suite
// running against a real install - and that guard is the reason it is safe to
// delete practice history in here at all.
process.env.FERMATA_TEST_LIBRARY_DIR = path.join(scratch, "library");
// Not 8080, and not a port anything else here uses. The suite starts its own
// server and never adopts one (see reuseExistingServer below), so a collision
// should fail loudly rather than quietly point these tests at whatever is
// already listening.
const port = Number(process.env.FERMATA_TEST_PORT || 8931);

export default defineConfig({
  // Both tests/unit (pure functions, no page fixture, so no browser is
  // launched) and tests/browser.
  //
  // NOT web/e2e. Those specs arrived before this runner existed and have never
  // been executed by anything; pulling them in here would mean this config
  // vouching for specs nobody has run, and a red suite would say nothing about
  // the change that turned it red. They are worth wiring up - deliberately, by
  // whoever owns them - and until that happens the honest state is that they
  // are not covered rather than that they are.
  testDir: "tests",
  // One worker. The tests share one server and one database, and instruments
  // are global to an install - two workers would delete each other's rows.
  workers: 1,
  forbidOnly: !!process.env.CI,
  // Above the sum of the per-assertion budgets a single test can ask for: the
  // unfretted test performs four sequential soundfont-dependent auditions, each
  // allowed 30s on a cold runner. Playwright's 30s default was smaller than one
  // test's stated needs, so a slow runner would have failed inside the product
  // rather than in the timeout, and read as a bug in the feature.
  timeout: 180_000,
  reporter: process.env.CI
    ? [["list"], ["github"], ["html", { open: "never" }], ["./tests/minimum-tests.js"]]
    : [["list"], ["./tests/minimum-tests.js"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // A click is a user gesture, so autoplay policy is not in the way; muted so
    // running these locally is not a faceful of low E every few seconds.
    launchOptions: { args: ["--mute-audio"] },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `python -m uvicorn fermata.main:app --host 127.0.0.1 --port ${port}`,
    cwd: path.join(here, "..", "server"),
    url: `http://127.0.0.1:${port}/api/health`,
    // NEVER reuse. The fixtures DELETE instruments, and a developer with
    // anything already on this port would have had those deletions land on a
    // real install - real library, real database - with no warning. Refusing to
    // adopt a server we did not start is the only version of this that is safe
    // to run locally; tests/browser also checks the instance looks like a
    // throwaway before it deletes anything.
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      FERMATA_WEB_DIST: path.join(here, "dist"),
      FERMATA_LIBRARY: path.join(scratch, "library"),
      FERMATA_CONFIG: path.join(scratch, "config"),
    },
  },
});
