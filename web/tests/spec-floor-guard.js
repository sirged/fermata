// The comparison at the heart of the missing-test guard, factored out so the
// two independent places that need it - tests/minimum-tests.js (a Playwright
// reporter, which can be dropped by a caller-supplied --reporter flag) and
// scripts/run-browser-tests.mjs (which owns the actual `playwright test`
// invocation and reads the JSON summary back afterward specifically so it
// still catches that) - apply the identical rule to whatever counts they
// gathered on their own. What differs between the two callers is how they
// learn what ran; this file only knows the arithmetic.
//
// executedByFile: Map<string, number> - relative spec-file path (forward
// slashes, relative to web/) to the number of tests in it that actually ran
// and were not skipped.
//
// entries: the array returned by loadSpecFloors() (tests/spec-floors.js) - a
// list of { file, count, reason } contributions. More than one entry may
// name the same file; their counts are summed. That is what lets two PRs
// that both touch the same spec file each add their own new file instead of
// one of them having to edit the other's.
export function checkSpecFloors(executedByFile, entries) {
  const claimedByFile = new Map();
  for (const entry of entries) {
    claimedByFile.set(entry.file, (claimedByFile.get(entry.file) ?? 0) + entry.count);
  }

  const problems = [];

  // A file that ran but is not named by any entry - because an entry was
  // deleted while the file stayed, or because a new spec file showed up
  // with nobody having added its floor - is exactly the "unlisted file"
  // case the guard has to name.
  for (const [file, executed] of executedByFile) {
    if (!claimedByFile.has(file)) {
      problems.push(
        `${file} ran ${executed} test(s) but has no entry under tests/spec-floors/ - add one for whatever added those tests.`,
      );
    }
  }

  // An entry whose file ran FEWER tests than it claims - because the file
  // was deleted or unwired entirely (0 ran), because some of its tests are
  // now conditionally skipped (against policy - see tests/spec-floors.js),
  // or because they were otherwise filtered or ghosted - is the other half.
  for (const [file, claimed] of claimedByFile) {
    const executed = executedByFile.get(file) ?? 0;
    if (executed < claimed) {
      problems.push(
        `${file}: tests/spec-floors/ entries claim ${claimed}, only ${executed} ran - the file may be deleted, ` +
          `unwired from the runner, have some of its tests conditionally skipped, or otherwise run fewer than ` +
          `it used to.`,
      );
    }
  }

  const totalClaimed = sumValues(claimedByFile);
  const totalExecuted = sumValues(executedByFile);

  return { ok: problems.length === 0, problems, totalClaimed, totalExecuted };
}

function sumValues(map) {
  let total = 0;
  for (const value of map.values()) total += value;
  return total;
}
