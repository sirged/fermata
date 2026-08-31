// #110: a browser test performs a user action, then reads the result through
// a channel that is not ordered against it - the request context rather than
// the page, most often - so the read can and sometimes does overtake the
// write it is checking. Diagnosed three times (#82/#83, #100, and the
// refused-scan test #110 itself names), fixed each time, and back within one
// new file each time: a point-in-time grep does not hold, because the wrong
// version is the natural thing to write and the right one needs knowing this
// specific hazard.
//
// This is that grep, kept running. It is crude on purpose, the same way
// check-warning-patterns.mjs is crude on purpose: a plain, line-based scan
// of tests/browser/*.spec.js's SOURCE TEXT, not a real parse of it. It looks
// for a page action (a click, a fill, a key press...) followed - with
// nothing that could order the two in between - by a read through the
// request context. That is the exact shape #82, #100 and #110's third
// instance shared.
//
// APPROXIMATE ON PURPOSE. A false positive here costs a moment and a
// `// out-of-band-ok: <reason>` on the flagged line; a false negative costs a
// random CI failure that trains everyone to re-run rather than look, which is
// how a genuine one eventually gets waved through. So this leans toward
// flagging: it does not understand scope, only resets its idea of "an action
// happened with no barrier since" at each `test(...)`/`test.beforeEach(...)`
// boundary, and treats an `expect.poll(...)`/`expect(async () => {...}).toPass(...)`
// block as ALL barrier - the retrying-read idiom this project already uses
// correctly (see zz-library-missing.spec.js) is exactly what a guard like
// this must not punish.
//
// WHAT THIS DOES NOT CATCH, stated plainly rather than implied by a green
// run: practice.spec.js:343's flake before it was fixed. That test never
// touched `request.*` at all - it clicked Save, checked `.notice` (a barrier
// that read true before the click as well as after, so it was never a wait
// on anything), then called `page.reload()` too early and read the reloaded
// PAGE's own DOM back, correctly wrapped in a retrying `toHaveValue`. The
// read channel was fine; the reload itself ran before the write it was
// testing had landed. Telling that apart from the very common, entirely
// correct "act, reload, assert" shape needs knowing WHICH barrier is
// causally tied to WHICH write - semantic knowledge a text scan does not
// have. A green run of this script is evidence against the request-context
// shape specifically, not against every way a test can outrun its own write.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const browserDir = path.join(here, "..", "tests", "browser");

// A page action whose effect a later read might be racing. Deliberately not
// `request.*` calls, which are the OUT-OF-BAND side of the race, not the
// action that starts it.
const ACTION_RE =
  /\.(click|fill|press|check|uncheck|selectOption|dragTo|type|setInputFiles|dispatchEvent)\(/;
// Something that orders a later read against whatever the action triggered:
// a retrying assertion, an explicit wait on the network, or a hard resync
// (reload/goto navigate the page fresh, so nothing before them is live
// evidence of anything after).
const BARRIER_RE =
  /\b(await expect\(|\.toPass\(|expect\.poll\(|waitForResponse\(|waitForFunction\(|waitForLoadState\(|waitForSelector\(|waitForURL\(|page\.reload\(|page\.goto\()/;
// The out-of-band read #82/#100/#110 all were: state fetched through
// Playwright's APIRequestContext, which is not ordered against the page's
// own click/fill at all.
const READ_RE = /\brequest\.(get|post|patch|put|delete)\(/;
// Enters a retrying-read block: everything inside is presumed ordered
// correctly BY CONSTRUCTION (it is retried until it passes), so reads in here
// are the fix, not the defect.
const RETRY_BLOCK_RE = /(expect\.poll\(|expect\(async \(\)|expect\(async function)/;
const TEST_BOUNDARY_RE = /^\s*test(\.\w+)?\(/;
const ACK_RE = /\/\/\s*out-of-band-ok\b/;

function netBrackets(line) {
  // Comments and strings are not stripped, so a `//` line or a string
  // containing a brace can misdirect this a little - acceptable at the
  // approximate level this tool works at. Deliberately not "" the char
  // class, just counted directly.
  const opens = (line.match(/[{(]/g) ?? []).length;
  const closes = (line.match(/[})]/g) ?? []).length;
  return opens - closes;
}

function scanFile(file) {
  const text = fs.readFileSync(file, "utf8");
  const lines = text.split("\n");
  const problems = [];

  let sinceBarrier = false;
  let inRetryBlock = false;
  let retryDepth = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const lineNo = i + 1;

    if (TEST_BOUNDARY_RE.test(line)) {
      sinceBarrier = false;
      inRetryBlock = false;
      retryDepth = 0;
    }

    if (inRetryBlock) {
      retryDepth += netBrackets(line);
      if (retryDepth <= 0) {
        inRetryBlock = false;
        sinceBarrier = false; // the retry block itself is a barrier
      }
      continue;
    }

    if (RETRY_BLOCK_RE.test(line)) {
      inRetryBlock = true;
      retryDepth = netBrackets(line);
      sinceBarrier = false;
      continue;
    }

    if (BARRIER_RE.test(line)) {
      sinceBarrier = false;
    }

    if (READ_RE.test(line)) {
      // The marker may sit on the read's own line or on the comment line
      // immediately above it - whichever reads naturally for the call shape.
      const acknowledged = ACK_RE.test(line) || (i > 0 && ACK_RE.test(lines[i - 1]));
      if (sinceBarrier && !acknowledged) {
        problems.push(
          `${path.relative(browserDir, file)}:${lineNo}: a request.* read follows a page ` +
            "action with nothing ordering it after that action's write - see #110. Add a " +
            "barrier (await expect(...), waitForResponse(...), or reload/goto) between the " +
            "action and this read, or mark it `// out-of-band-ok: <reason>` if the read " +
            "genuinely cannot race (e.g. it runs before any action in this test).",
        );
      }
      // A read is not itself a barrier for what comes after it.
      continue;
    }

    if (ACTION_RE.test(line) && !BARRIER_RE.test(line)) {
      sinceBarrier = true;
    }
  }

  return problems;
}

const files = fs
  .readdirSync(browserDir)
  .filter((f) => f.endsWith(".spec.js"))
  .map((f) => path.join(browserDir, f));

const problems = files.flatMap(scanFile);

if (problems.length) {
  console.error("out-of-band read check failed (#110):\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log(`out-of-band read check passed (${files.length} browser spec files)`);
