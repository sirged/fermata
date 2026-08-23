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
// 146 before issue #97, plus 37 for the metronome becoming a general tool: 18
// in tests/browser/metronome-everywhere.spec.js (the standalone page, the
// practice page, the widened tempo control, the honesty rule on an inferred
// tempo, what the interface says at BOTH ends of the countable range, and the
// four lifecycle cases where a setting either has to survive a component being
// unmounted or must deliberately not) and 19 in
// tests/unit/metronome-engine.spec.js (the engine's own rate arithmetic, what
// it reports when the range runs out, and the tempo seeded when a mode changes
// - all callable without a browser). Raised deliberately, and every one of the
// 37 was shown to fail against a mutation of the behaviour it claims - see the
// pull request. One further test was written, measured against every mutation
// it was meant to catch, found to discriminate nothing, and deleted rather than
// counted here.
//
// Plus 2 for issue #95, in tests/browser/zz-library-missing.spec.js: that a
// score whose file has gone is shown as missing rather than disappearing, and
// that a refused reconciliation says so on the page and can be confirmed. Both
// were shown to fail against a mutation of the behaviour they claim - the whole
// point of them is that the guard #95 added was invisible until they existed.
// Plus 20 for issues #102 and #103 - the application knowing it guessed and
// not saying so.
//
// 3 in tests/browser/metronome-everywhere.spec.js: a score that prints no
// tempo, and a transcription whose document declares none, both of which used
// to read "marked ♩ = 120" (the two honesty tests already there both use
// fixtures that DECLARE a tempo, which is why the guard meant to catch this was
// never exercised and was in fact dead code) - plus the opposite error, a score
// whose tempo mark sits in a later bar, which the first version of that fix
// affirmatively told the reader had no tempo at all.
//
// 7 in tests/browser/score-compare-warnings.spec.js: an assumed meter saying so
// beside the staff and first in the line, a read one saying that instead, a row
// that records neither claiming neither, a recognised tuning NAME reported as a
// name rather than as a tuning that was read, a printed tuning instruction the
// extractor discards being stated, and the two gig-mode cases - the mark kept
// for an unread tuning instruction, and NOT kept for a tuning merely assumed
// standard, which is two thirds of the library.
//
// 1 in tests/browser/practice.spec.js (a practice day assumed rather than
// recorded), 1 in tests/browser/zz-library-missing.spec.js (a scan that found a
// missing file again saying so), 7 in tests/unit/provenance.spec.js (the
// read-versus-assumed sorting, what it does with a source string it does not
// recognise, and the three things it may say about a tuning - name recognised,
// instruction unread, nothing) and 1 in tests/unit/practice.spec.js (how much
// of a week's total rests on an assumed day).
//
// Raised deliberately, and every one of the 20 was shown to fail against a
// mutation of the behaviour it claims - see the pull request. Two of them also
// assert PLACEMENT and not only text, because both of those facts were rendered
// in the wrong place while every assertion about their wording passed.
// Plus 31 for issue #61, the first exercise - hear a note, name it.
//
// 18 in tests/unit/ear-training.spec.js: which four notes get offered (that the
// note heard is one of them exactly once, that the other three are one of each
// kind worth confusing rather than four notes far apart, that which candidate of
// each kind is taken is the caller's randomness rather than fixed, that no
// choice ever leaves the range, and that a range with no octave in it still
// offers four); what an instrument's own definition says its range is (strings
// plus declared frets, moved by a capo, stopping at the strings when there are
// no frets to declare, and absent rather than wrong when there are no strings);
// a range too narrow to ask a question in at all; the note to sound never being
// the note just heard; and every string the module can produce, checked against
// practice.js's own forbidden-word list and against carrying a percentage - a
// drill is the easiest place in a practice tool to start grading somebody, and
// the rule against it is only real if something checks.
//
// 13 in tests/browser/ear-training.spec.js, which drive the real synthesiser
// because the one property this exercise lives or dies by is that the question
// is built from what was SOUNDED and not from what the component meant to
// sound: the four choices are read back against data-sounded-midi, which is set
// from the value playPitch resolved with, so deleting the audio path leaves no
// answer to read and no choices to click. Then what happens on an answer either
// way - the same words in the same place and, asserted on computed style, the
// same colours, because a wrong answer in ear training is the practice and not a
// shortfall; hearing a note again before and after answering and that not being
// counted; the drill following a single defined instrument and never leaving its
// range, not adopting one of two because which is in somebody's hands is not
// known, and saying so when a definition spans one note or is pitched away from
// the synthesiser's fixed A440; a soundfont that will not load saying so rather
// than leaving a silent drill; and the session itself - one practice_sessions
// row with activity 'ear_training', on the practice page, counted against a
// weekly goal about ear training with no special case, and still logged when
// somebody walks away from the drill mid-way.
//
// One of the 13 asserts PLACEMENT and not only text, because the statement about
// what the note was renders above the four buttons: unless its space is held
// open the whole question slides down at the moment a hand is over it, and every
// assertion about the wording still passes.
//
// Raised deliberately, and every one of the 31 was shown to fail against a
// mutation of the behaviour it claims - see the pull request.
//
// Plus 2 for issue #119, in tests/browser/version.spec.js: the build
// indicator is on screen with no interaction, and its text agrees with what
// GET /api/version actually reports rather than a hand-copied string. Both
// were shown to fail against a mutation of the behaviour they claim - hiding
// the indicator, and breaking the endpoint - see the pull request.
//
// Plus 13 for issue #106, in tests/browser/toolbar-responsive.spec.js: the
// score toolbar hard-clipping below its ~869px intrinsic width, which on a
// portrait tablet - 834 and 768 are ordinary widths for one, and the
// project's own stated primary form factor - left some practice controls
// unreachable with nothing on screen to say they existed.
//
// 10 check for zero clipping (and the page never growing a horizontal
// scrollbar) at each of 1280/1024/834/768/430, in both the ordinary toolbar
// and gig mode's HUD - checked separately because gig mode is the layout
// most likely to be running on a stand, and it turned out unaffected by
// this bug both before and after the fix, which is itself worth a standing
// check rather than an assumption.
//
// 2 drive every control - the profile switch, theme picker, play, speed,
// loop, metronome, count-in, ladder and its own follow-on inputs - with a
// REAL click at 768 and 430, and not via Playwright's own `locator.click()`:
// that method still fired a covered button's handler in a state where
// `document.elementFromPoint()` at the button's own center resolved to a
// different control sitting on top of it - confirmed by hand against the
// pre-fix build, and exactly the gap a "does the element exist" assertion
// would also have missed. Each of these two checks first that the point a
// finger would land on really does hit the control before ever clicking it.
//
// 1 checks placement, not just presence: below the wrap breakpoint, the
// transport row (Play/Speed/Loop - what a player reaches for mid-piece) has
// to render above the profile switch and theme picker, not merely
// somewhere on screen. This one is not a bare "which y is smaller" check -
// pre-fix, `.seg` and `.player` sit in the very same unwrapped row, and
// `align-items: center` alone put `.player`'s top a few pixels above
// `.seg`'s alone (`.player` was simply the taller item that row, its own
// "Count-in" button wrapped onto two lines) - a bare top-vs-top comparison
// passed against the pre-fix layout for that wrong reason, which is exactly
// the shape of test this project does not want. The assertion that
// actually distinguishes "two separate rows" from "one row, uneven
// heights" is that they must not vertically overlap at all.
//
// 6 of the 13 (the toolbar's own clipping checks at 834/768/430, both
// reachability checks at 768/430, and the placement check) were each shown
// to fail against the pre-fix layout - see the pull request. The other 7
// (the toolbar's clipping checks at 1280/1024, and gig mode's clipping
// check at all five widths) stay green throughout, deliberately: they
// guard behaviour - gig mode's HUD, and the toolbar above the wrap
// breakpoint - that this fix was never meant to change, and a fix that
// turned any of those red would itself be a regression.
//
// Plus 11 for issue #121 - a compound meter's bar had no sound of its own,
// and the subdivision labels named the wrong note.
//
// 3 in tests/browser/metronome-everywhere.spec.js, all driven through the
// standalone page's own controls: 6/8 sounding three distinct levels rather
// than the two that made every dotted-quarter pulse identical; 6/8 and 9/8
// no longer producing byte-identical click streams (the sharpest of the
// three - satisfiable only by the bar itself becoming audible); and the
// subdivision option that names the eighth actually clicking six times a bar
// in 6/8, paired with a rate assertion so a relabel that leaves the audio
// untouched cannot pass it.
//
// 6 in tests/unit/metronome.spec.js, for clickLevel: the three sounds it
// gives 6/8, a simple meter never reaching the beat tier, subdivision
// scaling the beat's spacing along with the downbeat's, phase normalised
// into range the same way clickPhaseInBar's own input is, and two guarding
// a degenerate bar and a non-integer subdivision from corrupting either.
//
// 2 in tests/unit/metronome-engine.spec.js: changing the subdivision, and
// separately the meter, while the click is running puts the very next click
// back on the downbeat rather than at whatever offset the free-running
// counter had reached.
//
// Raised deliberately, and every one of the 11 was shown to fail against a
// mutation of the behaviour it claims - see the pull request.
export const MINIMUM_TESTS = 262;

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
