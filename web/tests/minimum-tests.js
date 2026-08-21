// Fails the run when the suite has SHRUNK.
//
// Playwright errors loudly when it finds zero tests and says nothing at all
// when it finds some of them. So renaming a spec file, a `testMatch` edit, a
// stray `test.skip`, or a `--grep` added to a CI invocation can delete an
// entire area of coverage and still report "12 passed" and exit 0 - which is
// how a suite ends up green while the thing it was written to prove is no
// longer checked at all.
//
// The floor is deliberate, not automatic: raise it when tests are added. A
// mismatch here is not a broken test, it is a suite that is not the suite this
// repository expects.
// Deliberately not skipped when the run is filtered: "CI quietly grew a --grep"
// is one of the cases this exists to catch, so a filter cannot be the thing that
// switches the check off. Narrowing a run on purpose is what the escape hatch is
// for, and CI never sets it.
//
// This is also read directly by scripts/run-browser-tests.mjs, which is the
// OTHER half of this guard - see that file's own comment for why counting
// here is not, by itself, enough. Keep the two numbers in sync.
export const MINIMUM_TESTS = 133;

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
    this.executed = 0;
  }

  onTestEnd(_test, result) {
    if (result.status !== "skipped") this.executed += 1;
  }

  async onEnd(result) {
    if (process.env.PLAYWRIGHT_ALLOW_PARTIAL) return;
    if (this.executed >= MINIMUM_TESTS) return;
    console.error(
      `\nExpected at least ${MINIMUM_TESTS} tests to run, only ${this.executed} did. ` +
        "Tests have gone missing (skipped, filtered, or deleted), or the floor in " +
        "web/tests/minimum-tests.js needs raising on purpose. To run a deliberate " +
        "subset, set PLAYWRIGHT_ALLOW_PARTIAL=1.",
    );
    return { status: result.status === "passed" ? "failed" : result.status };
  }
}
