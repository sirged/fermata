// How practice is put into words, and the calendar arithmetic behind it.
//
// These are the assertions that matter most in the whole practice feature, and
// not because the arithmetic is hard. The wording IS the feature: an interface
// that reports the same numbers as "you missed a day" instead of "3 of 4
// planned days" has failed at the one thing it was asked to do, and no amount
// of correct counting fixes it. So the strings are pinned character for
// character, and the vocabulary is checked against a list.
//
// No browser is needed for any of it - practice.js has no runes and no imports
// precisely so this can import it.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import {
  ACTIVITY_LABELS,
  FORBIDDEN_WORDS,
  MISSING_PIECE_LABEL,
  WEEK_STARTS,
  forbiddenWord,
  sessionSubject,
  uncountableStatement,
  activityLabel,
  addDays,
  dayBars,
  formatDays,
  formatDuration,
  formatMinutes,
  goalScopeLabel,
  goalStatements,
  localDay,
  periodLabel,
  periodStatement,
  rangeLabel,
  shortDayName,
  tempoLabel,
  timeLeftStatement,
  weekStart,
  allTimeStatement,
  lastPractisedStatement,
  modeLabel,
  ratingStatement,
  splitBars,
  targetStatement,
  tempoChart,
  tempoStatement,
  windowStatement,
} from "../../src/lib/practice.js";

const goal = (over = {}) => ({
  target_days: 4,
  target_minutes: null,
  scope: "all",
  score_title: null,
  activity: null,
  progress: {
    status: "past",
    days_practised: 3,
    minutes: 90,
    seconds: 5400,
    days_left: 0,
    met_days: false,
    met_minutes: null,
    met: false,
  },
  ...over,
});

// --------------------------------------------------------------- the calendar

test("the practice day is the browser's own day, not the UTC one", () => {
  // 23:30 local on 17 August is, in any timezone east of about UTC+1, already
  // the 18th in UTC - and toISOString().slice(0,10) would say so. The day the
  // practice happened on is the local one.
  const late = new Date(2026, 7, 17, 23, 30);
  expect(localDay(late)).toBe("2026-08-17");
  const early = new Date(2026, 7, 17, 0, 15);
  expect(localDay(early)).toBe("2026-08-17");
  // Months and days are zero-padded, because the server compares these as
  // text: "2026-8-7" sorts and ranges wrongly against "2026-08-17".
  expect(localDay(new Date(2026, 0, 5))).toBe("2026-01-05");
});

test("a monday-start week runs monday to sunday", () => {
  for (const day of ["2026-08-17", "2026-08-19", "2026-08-23"]) {
    expect(weekStart(day, "monday"), day).toBe("2026-08-17");
  }
  expect(weekStart("2026-08-16", "monday")).toBe("2026-08-10");
});

test("a sunday-start week runs sunday to saturday", () => {
  expect(weekStart("2026-08-17", "sunday")).toBe("2026-08-16");
  expect(weekStart("2026-08-16", "sunday")).toBe("2026-08-16");
  expect(weekStart("2026-08-22", "sunday")).toBe("2026-08-16");
  expect(weekStart("2026-08-23", "sunday")).toBe("2026-08-23");
});

test("every day of a year falls in exactly one week, under either setting", () => {
  // Not a restatement of the two tests above: this walks a whole year, across
  // month ends and a daylight-saving change in most timezones, and checks the
  // window actually contains the day - which is what an off-by-one in the
  // modulo breaks and a handful of spot checks can miss.
  for (const startsOn of WEEK_STARTS) {
    for (let d = new Date(2026, 0, 1); d.getFullYear() === 2026; d.setDate(d.getDate() + 1)) {
      const day = localDay(d);
      const start = weekStart(day, startsOn);
      const end = addDays(start, 6);
      expect(start <= day && day <= end, `${day} in ${start}..${end}`).toBe(true);
      expect(shortDayName(start), `${startsOn} week starts on`).toBe(
        startsOn === "monday" ? "Mon" : "Sun",
      );
    }
  }
});

test("adding days crosses a month, a year and a daylight-saving boundary", () => {
  expect(addDays("2026-08-31", 1)).toBe("2026-09-01");
  expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
  expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  // Spring forward in most of Europe and North America. Parsing a day at
  // midnight instead of midday is what makes this land on the wrong date.
  expect(addDays("2026-03-28", 1)).toBe("2026-03-29");
  expect(addDays("2026-03-29", 1)).toBe("2026-03-30");
  expect(addDays("2026-11-01", 1)).toBe("2026-11-02");
});

test("a period reads as a range, and says the month once when it can", () => {
  expect(periodLabel("2026-08-17", "2026-08-23")).toBe("17-23 Aug");
  expect(periodLabel("2026-09-28", "2026-10-04")).toBe("28 Sep - 4 Oct");
});

// ---------------------------------------------------------------- the lengths

test("a length of practice reads the way a person says it", () => {
  expect(formatDuration(0)).toBe("none");
  expect(formatDuration(30)).toBe("under a minute");
  expect(formatDuration(60)).toBe("1m");
  expect(formatDuration(2700)).toBe("45m");
  expect(formatDuration(3600)).toBe("1h");
  expect(formatDuration(5400)).toBe("1h 30m");
  expect(formatDuration(7500)).toBe("2h 5m");
  expect(formatMinutes(150)).toBe("2h 30m");
  expect(formatMinutes(0)).toBe("none");
});

test("a length is never rounded up into time that was not practised", () => {
  expect(formatDuration(119)).toBe("1m");
  expect(formatDuration(3599)).toBe("59m");
});

test("nonsense in gives none out rather than NaN", () => {
  for (const bad of [null, undefined, "", NaN, -50, {}]) {
    expect(formatDuration(bad), String(bad)).toBe("none");
  }
});

// ------------------------------------------------------------- the vocabulary

test("the forbidden vocabulary is matched as whole words", () => {
  expect(forbiddenWord("you missed a day")).toBe("missed");
  expect(forbiddenWord("your best week so far")).toBe("best");
  expect(forbiddenWord("you should practise more")).toBe("should");
  // The plain statements this feature is built on have to survive the check,
  // or the check is the thing that gets deleted.
  expect(forbiddenWord("No practice recorded this week")).toBeNull();
  expect(forbiddenWord("3 of 4 planned days")).toBeNull();
  expect(forbiddenWord("1h 30m of 2h 30m planned")).toBeNull();
  expect(forbiddenWord("5 days left in this week")).toBeNull();
  for (const word of FORBIDDEN_WORDS) {
    expect(forbiddenWord(`a sentence with ${word} in it`), word).toBe(word);
  }
  // The list itself, pinned - the vocabulary is the rule, so quietly emptying
  // it would switch every check above off while leaving them green.
  for (const required of ["missed", "failed", "streak", "best", "should", "only"]) {
    expect(FORBIDDEN_WORDS, required).toContain(required);
  }
});

// The user-facing text in the components, which the vocabulary check did not
// reach. practice.js owns the phrases it generates, but the pages carry
// literals of their own - and one of them ("your practice record") failed this
// on the whole word "record", in a file whose header says the words are the
// feature.
const HERE = path.dirname(fileURLToPath(import.meta.url));
const COMPONENTS = [
  "Practice.svelte",
  "Viewer.svelte",
  "Library.svelte",
  // The per-piece view (#57). A page about one piece is the one most tempting
  // to write a verdict on - there is a single subject and a row of numbers
  // about it - so it is the one that most needs this check.
  "ScoreProgress.svelte",
  // The fret-to-note drill's own weak-positions panel (#235) - a list of
  // counts about where a player keeps answering incorrectly is exactly the
  // kind of surface a verdict word slips into.
  "trainer/PositionCounts.svelte",
  // The named-scope picker both drills use (#236). A list of scopes a person
  // saved is a list of the things they are working on, which is one short
  // step from a list of the things they have not covered - so the words on
  // it are held to the same rule as everything above.
  "trainer/ScopePresets.svelte",
];

// The attributes a person actually reads. Every other attribute value is
// dropped before the check, because they are machine vocabulary and collide
// with it: loading="lazy" is a standard HTML value, and weakening the word list
// to accommodate it would be exactly the wrong way round.
const READ_BY_A_PERSON = /^(placeholder|title|aria-label|alt)=/;

/** A component's text with everything that is not shown to a person removed:
 * comments in all three syntaxes, the style block, and attribute values nobody
 * reads. */
function visibleText(source) {
  return source
    .replace(/<style[\s\S]*?<\/style>/g, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ")
    .replace(/[\w-]+="[^"]*"/g, (attr) => (READ_BY_A_PERSON.test(attr) ? attr : " "));
}

test("the practice interface's own words pass the same vocabulary check", () => {
  for (const name of COMPONENTS) {
    const source = fs.readFileSync(path.join(HERE, "..", "..", "src", "lib", name), "utf8");
    const found = forbiddenWord(visibleText(source));
    expect(found, `${name} contains the word ${found}`).toBeNull();
  }
});

// ---------------------------------------------------------------------------
// One piece: how is this piece going (#57).
//
// Same rule as everything above - the wording IS the feature - and one more
// that only applies here. A page about a single piece is where a direction is
// most tempting to state, so these pin that none of it does: no "faster than",
// no "up from", no arrow, and a single tempo point saying outright that it is
// not a progression.
// ---------------------------------------------------------------------------

const allTime = (over = {}) => ({
  sessions: 12,
  seconds: 12_000,
  minutes: 200,
  first_practised: "2026-06-03",
  last_practised: "2026-08-14",
  sessions_inferred: 0,
  ...over,
});

test("what a piece amounts to over its whole record is sessions and time, never a per-session length", () => {
  expect(allTimeStatement(allTime())).toBe("12 sessions, 3h 20m in total");
  expect(allTimeStatement(allTime({ sessions: 1, seconds: 600 }))).toBe(
    "1 session, 10m in total",
  );
  // A piece nobody has played. Said in words rather than as "0 sessions, none
  // in total", which is a row of noughts pretending to be a measurement.
  expect(allTimeStatement(allTime({ sessions: 0, seconds: 0 }))).toBe(
    "No practice logged against this piece yet",
  );
  expect(allTimeStatement(null)).toBe("No practice logged against this piece yet");
  // Nothing anywhere in this statement divides one number by the other. An
  // average session length is a standard a short evening then falls short of.
  expect(allTimeStatement(allTime())).not.toContain("per");
});

test("when a piece was last played comes from the whole record and not from a window", () => {
  expect(lastPractisedStatement(allTime())).toBe("Last practised 14 Aug, first on 3 Jun");
  // One day is one day, and "first on" the same date would read as two.
  expect(lastPractisedStatement(allTime({ first_practised: "2026-08-14" }))).toBe(
    "Last practised 14 Aug, which is the one day it has been",
  );
  expect(lastPractisedStatement(allTime({ last_practised: null }))).toBe("");
  expect(lastPractisedStatement(null)).toBe("");
});

test("a window states its own length, so an empty one is about the days and not about the piece", () => {
  expect(windowStatement({ days_practised: 8, seconds: 12_000, sessions_inferred: 0 }, 90)).toBe(
    "8 days, 3h 20m in the last 90 days",
  );
  expect(windowStatement({ days_practised: 1, seconds: 600, sessions_inferred: 0 }, 30)).toBe(
    "1 day, 10m in the last 30 days",
  );
  expect(windowStatement({ days_practised: 0, seconds: 0 }, 90)).toBe(
    "No practice on this piece in the last 90 days",
  );
  expect(windowStatement(null, 7)).toBe("No practice on this piece in the last 7 days");
  // The same disclosure a week's total makes: how much of it rests on a day
  // nobody recorded.
  expect(windowStatement({ days_practised: 2, seconds: 3600, sessions_inferred: 1 }, 90)).toBe(
    "2 days, 1h in the last 90 days (1 session on an assumed day)",
  );
  expect(windowStatement({ days_practised: 2, seconds: 3600, sessions_inferred: 2 }, 90)).toBe(
    "2 days, 1h in the last 90 days (2 sessions on an assumed day)",
  );
});

test("one tempo point says outright that it is not a progression", () => {
  // Issue #57 in as many words: a week of history is a week of history, and
  // the view should say so rather than drawing a confident trend through three
  // points. The server decides how many points are enough (`comparable`), so
  // this and any other reader of that API cannot disagree about it.
  expect(tempoStatement({ count: 1, comparable: false, sessions_without_tempo: 0 })).toBe(
    "One session with a tempo. One session is not a progression",
  );
  expect(tempoStatement({ count: 1, comparable: false, sessions_without_tempo: 3 })).toBe(
    "One session with a tempo, and 3 sessions without one. One session is not a progression",
  );
  expect(tempoStatement({ count: 4, comparable: true, sessions_without_tempo: 0 })).toBe(
    "4 sessions with a tempo",
  );
  expect(tempoStatement({ count: 4, comparable: true, sessions_without_tempo: 1 })).toBe(
    "4 sessions with a tempo, and 1 session without one",
  );
  expect(tempoStatement({ count: 0, comparable: false, sessions_without_tempo: 2 })).toBe(
    "No session on this piece wrote down a tempo",
  );
  expect(tempoStatement(null)).toBe("No session on this piece wrote down a tempo");
});

test("the tempo target reported is the one most recently written down", () => {
  expect(targetStatement({ latest_target: 120 })).toBe("Working towards 120 bpm");
  expect(targetStatement({ latest_target: null })).toBe("");
  expect(targetStatement(null)).toBe("");
});

test("a session that did not say how it was approached reads as not stated", () => {
  expect(modeLabel("section")).toBe("Section work");
  expect(modeLabel("run_through")).toBe("Run-through");
  // Not folded into either, and not blank - the column exists so this is never
  // guessed from whether a bar range happens to be present.
  expect(modeLabel(null)).toBe("Not stated");
  expect(modeLabel(undefined)).toBe("Not stated");
  // A mode a newer server knows about and this build does not must not render
  // as "undefined" beside a real one.
  expect(modeLabel("sight_read_through")).toBe("Not stated");
});

test("ratings are counted and never averaged", () => {
  expect(ratingStatement({ rated: 3, unrated: 1 })).toBe("3 sessions rated, 1 without a rating");
  expect(ratingStatement({ rated: 1, unrated: 0 })).toBe("1 session rated");
  expect(ratingStatement({ rated: 0, unrated: 4 })).toBe("No session on this piece was rated");
  expect(ratingStatement(null)).toBe("No session on this piece was rated");
  // No decimal point anywhere: a number out of five with one on it is a grade.
  expect(ratingStatement({ rated: 3, unrated: 1 })).not.toMatch(/\d\.\d/);
});

test("a split's bars are scaled inside their own split and a zero stays empty", () => {
  const bars = splitBars([
    { mode: "section", seconds: 1500 },
    { mode: "run_through", seconds: 750 },
    { mode: null, seconds: 0 },
  ]);
  expect(bars.map((b) => b.fill)).toEqual([1, 0.5, 0]);
  // The row's own fields survive, so a template does not have to zip two lists.
  expect(bars[0].mode).toBe("section");
  // A different value to scale by - the ratings count sessions, not seconds.
  const ratings = splitBars(
    [
      { rating: 1, sessions: 0 },
      { rating: 4, sessions: 2 },
    ],
    (r) => r.sessions,
  );
  expect(ratings.map((r) => r.fill)).toEqual([0, 1]);
  // Nothing at all: no bar is full rather than every bar being full.
  expect(splitBars([{ mode: "section", seconds: 0 }])[0].fill).toBe(0);
  expect(splitBars([])).toEqual([]);
  // A tiny share still shows, so "a little" never renders as "nothing".
  expect(splitBars([{ seconds: 1000 }, { seconds: 1 }])[1].fill).toBe(0.06);
});

test("the tempo chart maps points into the axis the server sent, and nothing else", () => {
  const chart = tempoChart(
    {
      points: [
        { session_id: 1, date: "2026-06-03", tempo_bpm: 80 },
        { session_id: 2, date: "2026-07-01", tempo_bpm: 100 },
        { session_id: 3, date: "2026-08-14", tempo_bpm: 120 },
      ],
      axis_low: 80,
      axis_high: 120,
      latest_target: 120,
    },
    { width: 100, height: 100, pad: 10 },
  );
  // Evenly spaced across the box, first at the left pad and last at the right.
  expect(chart.points.map((p) => p.x)).toEqual([10, 50, 90]);
  // The axis is axis_low..axis_high exactly - not widened, rounded or
  // recentred. Two readers deriving an axis differently is two charts that
  // disagree about the same history.
  expect(chart.points.map((p) => p.y)).toEqual([90, 50, 10]);
  expect(chart.target).toEqual({ bpm: 120, y: 10 });

  // Every session at the same tempo is a real answer, and dividing by a span
  // of zero would put every dot at the top as though it were a climb.
  const flat = tempoChart(
    {
      points: [
        { session_id: 1, tempo_bpm: 90 },
        { session_id: 2, tempo_bpm: 90 },
      ],
      axis_low: 90,
      axis_high: 90,
      latest_target: null,
    },
    { width: 100, height: 100, pad: 10 },
  );
  expect(flat.points.map((p) => p.y)).toEqual([50, 50]);
  expect(flat.target).toBeNull();

  // A lone dot sits in the middle rather than pinned to the left edge, where
  // it reads as the start of a line somebody has yet to draw.
  const one = tempoChart(
    {
      points: [{ session_id: 1, tempo_bpm: 90 }],
      axis_low: 90,
      axis_high: 90,
      latest_target: null,
    },
    { width: 100, height: 100, pad: 10 },
  );
  expect(one.points[0].x).toBe(50);

  expect(tempoChart({ points: [] }).points).toEqual([]);
  expect(tempoChart(null).points).toEqual([]);
});

test("printed tempo values thin out when the points crowd, and the newest is always one of them", () => {
  const points = (n) =>
    Array.from({ length: n }, (_, i) => ({ session_id: i, tempo_bpm: 80 + i }));
  const chart = (n, width) =>
    tempoChart(
      { points: points(n), axis_low: 80, axis_high: 80 + n, latest_target: null },
      { width, height: 100, pad: 10 },
    );

  // Room for every one: 5 points across 580 units is 145 apart.
  expect(chart(5, 600).points.every((p) => p.label)).toBe(true);

  // Packed: 40 points across 580 units is under 15 apart, so three digits
  // printed at every dot would sit on each other.
  const crowded = chart(40, 600);
  const labelled = crowded.points.filter((p) => p.label);
  expect(labelled.length).toBeLessThan(40);
  expect(labelled.length).toBeGreaterThan(0);
  // Whatever the spacing works out to, the most recent session is labelled -
  // it is the one number somebody opening this came to see.
  expect(crowded.points[crowded.points.length - 1].label).toBe(true);
  // And the ones that are labelled are far enough apart to be read.
  const xs = labelled.map((p) => p.x);
  for (let i = 1; i < xs.length - 1; i += 1) {
    expect(xs[i] - xs[i - 1], "two printed values are too close together").toBeGreaterThanOrEqual(
      34,
    );
  }
  // Nothing is lost by thinning: every point is still a point, with its own
  // value, and the list the page draws under the chart reads from these.
  expect(crowded.points).toHaveLength(40);
  expect(crowded.points.every((p) => Number.isFinite(p.tempo_bpm))).toBe(true);
});

test("every sentence the per-piece view produces passes the vocabulary check", () => {
  // The same list the rest of this feature is held to, applied to the phrases
  // #57 adds. A per-piece page is where "your best tempo" and "behind on this
  // one" would arrive if they ever did, so the guard is on the output rather
  // than on somebody remembering the rules at the top of practice.js.
  const sentences = [
    allTimeStatement(allTime()),
    allTimeStatement(allTime({ sessions: 0, seconds: 0 })),
    lastPractisedStatement(allTime()),
    lastPractisedStatement(allTime({ first_practised: "2026-08-14" })),
    windowStatement({ days_practised: 8, seconds: 12_000, sessions_inferred: 1 }, 90),
    windowStatement({ days_practised: 0, seconds: 0 }, 90),
    tempoStatement({ count: 1, comparable: false, sessions_without_tempo: 2 }),
    tempoStatement({ count: 5, comparable: true, sessions_without_tempo: 0 }),
    tempoStatement({ count: 0 }),
    targetStatement({ latest_target: 120 }),
    ratingStatement({ rated: 3, unrated: 1 }),
    ratingStatement({ rated: 0, unrated: 4 }),
    modeLabel(null),
    modeLabel("section"),
    modeLabel("run_through"),
  ];
  for (const sentence of sentences) {
    const found = forbiddenWord(sentence);
    expect(found, `"${sentence}" contains the word ${found}`).toBeNull();
  }
});

test("the vocabulary check would notice if one of those files slipped", () => {
  // The check above is worth nothing unless it can fail on the files it reads,
  // and a comment-stripper is exactly the sort of thing that quietly stops
  // matching anything.
  expect(forbiddenWord(visibleText('<p>you missed a day</p>'))).toBe("missed");
  expect(forbiddenWord(visibleText('<script>const s = "your best week";</script>'))).toBe("best");
  // ...and that it is the stripping, not luck, that lets the real files pass.
  expect(forbiddenWord(visibleText("<!-- a comment mentioning your best week -->"))).toBeNull();
  expect(forbiddenWord(visibleText("<style>.missed { color: red }</style>"))).toBeNull();
  expect(forbiddenWord(visibleText('<img loading="lazy" alt="" />'))).toBeNull();
  // An attribute a person DOES read is still checked.
  expect(forbiddenWord(visibleText('<input placeholder="you missed a day" />'))).toBe("missed");
  expect(forbiddenWord(visibleText('<button title="your best week">x</button>'))).toBe("best");
});

// ------------------------------------------------------------- the statements

test("a partly met goal states both numbers and reaches no verdict", () => {
  const [days] = goalStatements(goal());
  expect(days.text).toBe("3 of 4 planned days");
  expect(days.met).toBe(false);
});

test("a met goal is marked as reached and worded identically otherwise", () => {
  const [days] = goalStatements(
    goal({ progress: { ...goal().progress, days_practised: 4, met_days: true } }),
  );
  expect(days.text).toBe("4 of 4 planned days");
  expect(days.met).toBe(true);
});

test("both targets produce one statement each, in the order they were set", () => {
  const statements = goalStatements(
    goal({
      target_minutes: 150,
      progress: { ...goal().progress, minutes: 90, met_minutes: false },
    }),
  );
  expect(statements.map((s) => s.key)).toEqual(["days", "minutes"]);
  expect(statements[1].text).toBe("1h 30m of 2h 30m planned");
});

test("a week with no practice in it says none, not nought", () => {
  // The rule formatDuration already applies to a length, applied to the other
  // target. "0 of 5 planned days" beside "none of 5h planned" is the rule kept
  // in one place and dropped in the other, and a bare nought beside a target
  // reads like a mark out of five.
  const statements = goalStatements(
    goal({
      target_days: 5,
      target_minutes: 300,
      progress: {
        ...goal().progress,
        days_practised: 0,
        minutes: 0,
        met_days: false,
        met_minutes: false,
      },
    }),
  );
  expect(statements[0].text).toBe("none of 5 planned days");
  expect(statements[1].text).toBe("none of 5h planned");
  expect(formatDays(0)).toBe("no days");
  expect(formatDays(1)).toBe("1 day");
  expect(formatDays(4)).toBe("4 days");
});

test("a target that was not set produces no statement at all", () => {
  // Not "0 of 0 minutes", which would read as a target nobody chose and could
  // be marked unmet.
  expect(goalStatements(goal({ target_days: null, target_minutes: null }))).toEqual([]);
  const minutesOnly = goalStatements(
    goal({ target_days: null, target_minutes: 60, progress: { ...goal().progress, minutes: 60, met_minutes: true } }),
  );
  expect(minutesOnly.map((s) => s.key)).toEqual(["minutes"]);
});

test("nothing said about a goal uses a word that grades the person", () => {
  const cases = [
    goal(),
    goal({ progress: { ...goal().progress, days_practised: 0 } }),
    goal({ target_minutes: 600, progress: { ...goal().progress, minutes: 5 } }),
    goal({ target_days: 7, progress: { ...goal().progress, days_practised: 7, met_days: true } }),
  ];
  for (const g of cases) {
    const words = goalStatements(g)
      .map((s) => s.text)
      .join(" ");
    expect(forbiddenWord(words), words).toBeNull();
  }
});

test("a running week says how much of it is left and nothing about pace", () => {
  const running = { status: "running", days_left: 5 };
  expect(timeLeftStatement(running)).toBe("5 days left in this week");
  expect(timeLeftStatement({ status: "running", days_left: 1 })).toBe("1 day left in this week");
  // Nothing at all once it has ended - a finished week has no time left to
  // report, and saying "0 days left" about it is a shape of nagging.
  expect(timeLeftStatement({ status: "past", days_left: 0 })).toBe("");
  expect(timeLeftStatement({ status: "running", days_left: 0 })).toBe("");
  expect(timeLeftStatement(null)).toBe("");
});

test("a week states its days and its total, or says plainly there was none", () => {
  expect(periodStatement({ days_practised: 4, seconds: 12000 })).toBe("4 days, 3h 20m");
  expect(periodStatement({ days_practised: 1, seconds: 600 })).toBe("1 day, 10m");
  expect(periodStatement({ days_practised: 0, seconds: 0 })).toBe(
    "No practice recorded this week",
  );
  expect(periodStatement(null)).toBe("No practice recorded this week");
});

test("a week says how much of its total rests on a day nobody recorded, and says nothing when none does", () => {
  // Issue #103. A single session has always said whether its day was recorded
  // or taken from its UTC timestamp; the total said nothing, so a window
  // spanning the upgrade added two different kinds of day together with
  // nothing marking the join - and the server had been reporting the figure to
  // nobody ever since.
  expect(periodStatement({ days_practised: 4, seconds: 12000, sessions_inferred: 2 })).toBe(
    "4 days, 3h 20m (2 sessions on an assumed day)",
  );
  expect(periodStatement({ days_practised: 1, seconds: 600, sessions_inferred: 1 })).toBe(
    "1 day, 10m (1 session on an assumed day)",
  );
  // Silent when there is nothing to disclose, which on any install that has
  // only ever run this version is always - a note that appears every week
  // stops being read.
  expect(periodStatement({ days_practised: 4, seconds: 12000, sessions_inferred: 0 })).toBe(
    "4 days, 3h 20m",
  );
  expect(periodStatement({ days_practised: 4, seconds: 12000 })).toBe("4 days, 3h 20m");
  // And it is still said in the vocabulary this feature is allowed to use.
  expect(
    forbiddenWord(periodStatement({ days_practised: 4, seconds: 12000, sessions_inferred: 2 })),
  ).toBeNull();
});

test("a past week does not call itself this week", () => {
  // It did, for every row in the review - so a quiet spell in July rendered as
  // three consecutive rows reading "No practice recorded this week" beside
  // dates nowhere near the current one. The row already carries its dates; the
  // sentence does not need to name the week, only to avoid naming the wrong
  // one.
  expect(periodStatement({ days_practised: 0, seconds: 0 }, false)).toBe("No practice recorded");
  expect(periodStatement(null, false)).toBe("No practice recorded");
  expect(periodStatement({ days_practised: 2, seconds: 600 }, false)).toBe("2 days, 10m");
  for (const facts of [null, { days_practised: 0, seconds: 0 }, { days_practised: 3, seconds: 1 }]) {
    expect(periodStatement(facts, false)).not.toContain("this week");
  }
});

test("nothing said about a week compares it to another week", () => {
  const said = [
    periodStatement({ days_practised: 0, seconds: 0 }),
    periodStatement({ days_practised: 7, seconds: 40000 }),
    timeLeftStatement({ status: "running", days_left: 3 }),
  ].join(" ");
  expect(forbiddenWord(said), said).toBeNull();
});

test("a goal says what it is about", () => {
  expect(goalScopeLabel(goal())).toBe("any practice");
  expect(goalScopeLabel(goal({ scope: "score", score_title: "Study in C" }))).toBe("Study in C");
  expect(goalScopeLabel(goal({ scope: "activity", activity: "ear_training" }))).toBe(
    "ear training",
  );
  // A title that did not come back, but the piece is still there.
  expect(goalScopeLabel(goal({ scope: "score", score_id: 4, score_title: null }))).toBe(
    "one piece",
  );
  // A piece that has left the library says so, rather than showing a blank
  // where a title goes.
  expect(goalScopeLabel(goal({ scope: "score", score_id: null, score_title: null }))).toBe(
    MISSING_PIECE_LABEL.toLowerCase(),
  );
});

// ------------------------------------------------------------------- the bars

test("a week's bars are scaled inside that week and never against another", () => {
  const bars = dayBars([
    { date: "2026-08-17", seconds: 1800, sessions: 1 },
    { date: "2026-08-18", seconds: 3600, sessions: 2 },
    { date: "2026-08-19", seconds: 0, sessions: 0 },
  ]);
  expect(bars.map((b) => b.label)).toEqual(["Mon", "Tue", "Wed"]);
  expect(bars[1].fill).toBe(1);
  expect(bars[0].fill).toBe(0.5);
  expect(bars[2].fill).toBe(0);

  // The same shape of week at ten times the scale draws identically. If these
  // were scaled against anything outside the week - a personal best, a
  // rolling maximum - a quiet week would render as flat next to a busy one,
  // which is a comparison drawn in pixels.
  const tenfold = dayBars([
    { date: "2026-08-17", seconds: 18000, sessions: 1 },
    { date: "2026-08-18", seconds: 36000, sessions: 2 },
    { date: "2026-08-19", seconds: 0, sessions: 0 },
  ]);
  expect(tenfold.map((b) => b.fill)).toEqual(bars.map((b) => b.fill));
});

test("a day with a little practice still shows something", () => {
  const bars = dayBars([
    { date: "2026-08-17", seconds: 36000, sessions: 4 },
    { date: "2026-08-18", seconds: 60, sessions: 1 },
  ]);
  expect(bars[1].seconds).toBe(60);
  expect(bars[1].fill).toBeGreaterThan(0);
});

test("a week with no practice draws seven empty bars, not seven full ones", () => {
  const week = Array.from({ length: 7 }, (_, i) => ({
    date: addDays("2026-08-17", i),
    seconds: 0,
    sessions: 0,
  }));
  const bars = dayBars(week);
  expect(bars).toHaveLength(7);
  expect(bars.every((b) => b.fill === 0)).toBe(true);
});

// --------------------------------------------------------- a session's detail

test("a worked range reads as bars and pages, singular when it is one", () => {
  expect(rangeLabel({ from_bar: 17, to_bar: 32 })).toBe("bars 17-32");
  expect(rangeLabel({ from_bar: 17, to_bar: 17 })).toBe("bar 17");
  expect(rangeLabel({ from_bar: 17, to_bar: null })).toBe("bar 17");
  expect(rangeLabel({ from_page: 2, to_page: 3 })).toBe("pages 2-3");
  expect(rangeLabel({ from_bar: 1, to_bar: 8, from_page: 1, to_page: 1 })).toBe(
    "bars 1-8, page 1",
  );
  expect(rangeLabel({})).toBe("");
  expect(rangeLabel(null)).toBe("");
});

test("a tempo says it reached the target only when it did", () => {
  expect(tempoLabel({ tempo_bpm: 120, target_tempo_bpm: 120, reached_target: true })).toBe(
    "120 bpm, reached the 120 target",
  );
  // Short of the target: both numbers, and nothing about the gap.
  expect(tempoLabel({ tempo_bpm: 88, target_tempo_bpm: 120, reached_target: false })).toBe(
    "88 bpm, aiming at 120",
  );
  expect(tempoLabel({ tempo_bpm: 88, target_tempo_bpm: null })).toBe("88 bpm");
  // Two numbers with no verdict attached to them - an older or newer server,
  // or a session assembled by something that did not compute the comparison.
  // Claiming the target was reached because nothing said it was not is the one
  // answer this must never give: it is the only field here that could tell a
  // player they are further on than they are.
  expect(tempoLabel({ tempo_bpm: 88, target_tempo_bpm: 120 })).toBe("88 bpm, aiming at 120");
  expect(tempoLabel({ tempo_bpm: 88, target_tempo_bpm: 120, reached_target: null })).toBe(
    "88 bpm, aiming at 120",
  );
  expect(tempoLabel({ tempo_bpm: null, target_tempo_bpm: 120 })).toBe("");
  expect(tempoLabel(null)).toBe("");
});

test("every activity has a label, and an unknown one still reads as practice", () => {
  for (const key of Object.keys(ACTIVITY_LABELS)) {
    expect(activityLabel(key), key).toBe(ACTIVITY_LABELS[key]);
    expect(ACTIVITY_LABELS[key].length).toBeGreaterThan(0);
  }
  // A kind of practice this build has never heard of - a newer server, an
  // exercise added later - must not render as "undefined" beside a real one.
  expect(activityLabel("time_travel")).toBe("Practice");
  expect(activityLabel(undefined)).toBe("Practice");
});
