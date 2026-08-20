// Verifies score-render.js's delegation to alphaTab's Environment internals
// degrades safely rather than answering silently wrong - see "How profile
// support is actually decided" in tab-profile-selection.md. Not wired into
// any runner (there isn't one yet - see that file's header), but plain
// Node/ESM and runnable directly:
//
//   cd web && node test-fixtures/environment-guard.mjs
//
// Each case monkeypatches alphaTab.Environment *before* importing
// score-render.js, since the module reads it once, at import time - this
// only works because Node's ESM loader treats a fresh dynamic import() with
// a cache-busting query as a new module instance, so each case gets its own
// clean read of a differently-broken Environment.
import * as alphaTab from "@coderline/alphatab";

let failures = 0;

function check(name, condition, detail) {
  if (condition) {
    console.log(`PASS: ${name}`);
  } else {
    failures += 1;
    console.error(`FAIL: ${name}${detail ? " - " + detail : ""}`);
  }
}

async function freshScoreRender() {
  return import(`../src/lib/score-render.js?case=${Math.random()}`);
}

const notationStaff = {
  showStandardNotation: true,
  showTablature: false,
  tuning: [],
  showSlash: false,
  showNumbered: false,
};
const tabStaff = {
  showStandardNotation: false,
  showTablature: true,
  tuning: [0, 5, 10, 15, 19, 24],
  showSlash: false,
  showNumbered: false,
};

// ---------------------------------------------------------------- healthy
{
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args[0]);
  const mod = await freshScoreRender();
  console.warn = originalWarn;

  check("healthy library: no fallback warning at import", warnings.length === 0, JSON.stringify(warnings));
  check(
    "healthy library: notation-only staff -> [score, scoretab]",
    JSON.stringify(mod.supportedProfiles([{ staves: [notationStaff] }])) === JSON.stringify(["score", "scoretab"]),
  );
  check(
    "healthy library: tab-only staff -> [tab, scoretab]",
    JSON.stringify(mod.supportedProfiles([{ staves: [tabStaff] }])) === JSON.stringify(["tab", "scoretab"]),
  );
  check(
    "healthy library: one track, two staves -> all three",
    JSON.stringify(mod.supportedProfiles([{ staves: [notationStaff, tabStaff] }])) ===
      JSON.stringify(["score", "tab", "scoretab"]),
  );
  check("healthy library: no tracks -> []", JSON.stringify(mod.supportedProfiles([])) === "[]");
}

// ------------------------------------------------------- renamed staff ids
// staveProfiles keeps the OLD ids; defaultRenderers now expose NEW ones -
// every container-shape check (Map of Sets, array of {staffId, canCreate})
// still passes, which is exactly what the shape-only guard used to miss.
{
  const original = alphaTab.Environment.defaultRenderers;
  alphaTab.Environment.defaultRenderers = original.map((f) => ({
    staffId: "renamed-" + f.staffId,
    canCreate: (track, staff) => f.canCreate(track, staff),
  }));

  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args[0]);
  const mod = await freshScoreRender();
  console.warn = originalWarn;

  check("renamed staff ids: a fallback warning fires", warnings.length > 0);
  const result = mod.supportedProfiles([{ staves: [notationStaff] }]);
  check(
    "renamed staff ids: still answers correctly via the fallback, not []",
    result.includes("score"),
    JSON.stringify(result),
  );

  alphaTab.Environment.defaultRenderers = original;
}

// --------------------------------------------------------- throwing canCreate
// Passes the shape guard (staffId is a string, canCreate is a function) but
// throws when actually called - a changed parameter list or return contract.
{
  const original = alphaTab.Environment.defaultRenderers;
  alphaTab.Environment.defaultRenderers = original.map((f) => ({
    staffId: f.staffId,
    canCreate: () => {
      throw new TypeError("simulated signature change");
    },
  }));

  const mod = await freshScoreRender();

  const warningsFirst = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warningsFirst.push(args[0]);
  const result1 = mod.supportedProfiles([{ staves: [notationStaff] }]);
  console.warn = originalWarn;

  check("throwing canCreate: first call recovers instead of throwing", result1.includes("score"));
  check("throwing canCreate: warns exactly once", warningsFirst.length === 1, JSON.stringify(warningsFirst));

  const warningsSecond = [];
  console.warn = (...args) => warningsSecond.push(args[0]);
  const result2 = mod.supportedProfiles([{ staves: [notationStaff] }]);
  console.warn = originalWarn;

  check("throwing canCreate: second call still answers correctly", result2.includes("score"));
  check("throwing canCreate: does not warn again (permanent, not re-armed)", warningsSecond.length === 0);

  alphaTab.Environment.defaultRenderers = original;
}

// ------------------------------------------------- unrelated throw inside
// supportedProfiles() itself (not from canCreate at all) must still degrade
// to an array answer, never escape and strand the caller at "unknown".
{
  const mod = await freshScoreRender();
  const malformedTrack = {
    get staves() {
      throw new Error("boom - unrelated to canCreate");
    },
  };
  let threw = false;
  let result;
  try {
    result = mod.supportedProfiles([malformedTrack]);
  } catch {
    threw = true;
  }
  check("unrelated throw inside supportedProfiles(): does not escape", !threw);
  check("unrelated throw inside supportedProfiles(): still returns an array", Array.isArray(result));
}

console.log(failures === 0 ? "\nAll checks passed." : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
