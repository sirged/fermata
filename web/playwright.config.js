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
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "fermata-browser-"));
const port = Number(process.env.FERMATA_TEST_PORT || 8123);

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
  reporter: process.env.CI
    ? [["list"], ["github"], ["html", { open: "never" }]]
    : "list",
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
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    env: {
      FERMATA_WEB_DIST: path.join(here, "dist"),
      FERMATA_LIBRARY: path.join(scratch, "library"),
      FERMATA_CONFIG: path.join(scratch, "config"),
    },
  },
});
