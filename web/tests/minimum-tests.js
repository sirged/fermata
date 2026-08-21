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
const MINIMUM_TESTS = 78;

export default class MinimumTests {
  onBegin(_config, suite) {
    this.found = suite.allTests().length;
  }

  async onEnd(result) {
    if (process.env.PLAYWRIGHT_ALLOW_PARTIAL) return;
    if (this.found >= MINIMUM_TESTS) return;
    console.error(
      `\nExpected at least ${MINIMUM_TESTS} tests, collected ${this.found}. ` +
        "Tests have gone missing, or the floor in web/tests/minimum-tests.js " +
        "needs raising on purpose. To run a deliberate subset, set " +
        "PLAYWRIGHT_ALLOW_PARTIAL=1.",
    );
    return { status: result.status === "passed" ? "failed" : result.status };
  }
}
