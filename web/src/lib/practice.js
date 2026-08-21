// How practice is put into words, and the calendar arithmetic that decides
// which day a session belongs to.
//
// No runes and no imports, so the whole of it can be called directly from a
// test without a browser or a component - which matters more here than
// anywhere else in this project, because the words themselves are the feature.
// "Three of four planned days" and "you missed a day" carry the same
// information and only one of them makes a person want to open the app again
// tomorrow, and the difference lives entirely in these functions.
//
// THE RULES THESE FOLLOW, so that a later edit knows what it is breaking:
//
//   State the fact and stop.       Counts and totals, in the order they were
//                                 planned. No adverbs, no "only", no "just".
//   Never grade.                  No percentages, no marks, no "well done".
//   Never compare periods.        No best, no worst, no average, no run of
//                                 anything. A good month must not become the
//                                 standard a bad month is measured against.
//   A shortfall is not an error.   Nothing here returns a severity, a level,
//                                 or anything a caller could colour red.
//   Ask, do not conclude.         The only question put to a person about a
//                                 period is whether the goal was realistic.

/** Words that must never reach a person about their own practice. Exported so
 * the tests can check the phrasing this module produces rather than trusting
 * that whoever edits it remembers the rules above.
 *
 * Matched as WHOLE words by forbiddenWord below, which is not pedantry: "No
 * practice recorded this week" is exactly the plain statement this feature is
 * built on, and a substring check rejects it for containing "record". */
export const FORBIDDEN_WORDS = [
  "fail",
  "failed",
  "missed",
  "miss",
  "behind",
  "streak",
  "best",
  "worst",
  "record",
  "average",
  "should",
  "only",
  "just",
  "shame",
  "lazy",
  "poor",
  "bad",
];

const FORBIDDEN_PATTERN = new RegExp(`\\b(${FORBIDDEN_WORDS.join("|")})\\b`, "i");

/** The first forbidden word in some text, or null. The rule lives here rather
 * than in the tests so there is one list and one way of applying it. */
export function forbiddenWord(text) {
  const found = FORBIDDEN_PATTERN.exec(String(text ?? ""));
  return found ? found[1].toLowerCase() : null;
}

export const WEEK_STARTS = ["monday", "sunday"];

const DAY_MS = 86_400_000;

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** How each activity is written for a person to read. */
export const ACTIVITY_LABELS = {
  piece: "A piece",
  technique: "Technique",
  sight_reading: "Sight reading",
  ear_training: "Ear training",
  fretboard: "Fretboard",
  chords: "Chords",
  improvisation: "Improvisation",
  theory: "Theory",
  free: "Playing freely",
  other: "Something else",
};

export const MODE_LABELS = {
  section: "Section work",
  run_through: "Run-through",
};

export function activityLabel(activity) {
  return ACTIVITY_LABELS[activity] ?? "Practice";
}

/** Today's date in the BROWSER's timezone, as YYYY-MM-DD.
 *
 * Not toISOString().slice(0, 10), which is the UTC date: west of Greenwich
 * that is already tomorrow for every evening practice, so a session logged at
 * nine at night would be filed on the wrong day and, at a week boundary, in
 * the wrong week. This is the value the server stores as the practice day and
 * counts every goal against.
 */
export function localDay(date = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** A YYYY-MM-DD string as a Date at local midday.
 *
 * Midday, not midnight: new Date("2026-08-17") is parsed as UTC and can land
 * on the previous local day, and midnight local is a daylight-saving edge in
 * some zones. Nothing here needs a time, only a calendar day that survives
 * being read back.
 */
export function dayDate(day) {
  const [y, m, d] = String(day).split("-").map(Number);
  return new Date(y, m - 1, d, 12);
}

export function addDays(day, n) {
  const date = dayDate(day);
  date.setDate(date.getDate() + n);
  return localDay(date);
}

/** The first day of the week `day` falls in - the same rule the server
 * applies, so the week a client offers to set a goal for and the week the
 * server counts are the same seven days. */
export function weekStart(day, startsOn = "monday") {
  const weekday = dayDate(day).getDay(); // 0 is Sunday
  const offset = startsOn === "sunday" ? weekday : (weekday + 6) % 7;
  return addDays(day, -offset);
}

export function shortDayName(day) {
  return DAY_NAMES[dayDate(day).getDay()];
}

/** "17 Aug" - a day, for a label. */
export function shortDate(day) {
  const date = dayDate(day);
  return `${date.getDate()} ${MONTH_NAMES[date.getMonth()]}`;
}

/** "17-23 Aug", or "28 Sep - 4 Oct" across a month boundary. */
export function periodLabel(start, end) {
  const from = dayDate(start);
  const to = dayDate(end);
  if (from.getMonth() === to.getMonth()) {
    return `${from.getDate()}-${to.getDate()} ${MONTH_NAMES[to.getMonth()]}`;
  }
  return `${shortDate(start)} - ${shortDate(end)}`;
}

/** A length of practice, as a person says it: "45m", "1h 30m", "2h".
 *
 * Zero is "none", not "0m": a week with no practice in it is a fact about the
 * week and reads better as a word than as a number with a unit.
 */
export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) return total ? "under a minute" : "none";
  const minutes = Math.floor(total / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${minutes}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

/** The same, from minutes - which is the unit a goal's target is set in. */
export function formatMinutes(minutes) {
  return formatDuration(Math.max(0, Math.floor(Number(minutes) || 0)) * 60);
}

/** What a goal's targets came to, one statement per target that was set.
 *
 * Each is `{ key, text, met }`. `met` is there so a caller can mark what was
 * reached - and only what was reached; there is deliberately no value meaning
 * "this one went badly", because a caller given one will eventually colour it.
 *
 * A target that was not set produces no statement at all, rather than a
 * statement about a target of zero.
 */
export function goalStatements(goal) {
  if (!goal) return [];
  const progress = goal.progress ?? {};
  const out = [];
  if (goal.target_days != null) {
    out.push({
      key: "days",
      text: `${progress.days_practised ?? 0} of ${goal.target_days} planned days`,
      met: progress.met_days === true,
    });
  }
  if (goal.target_minutes != null) {
    out.push({
      key: "minutes",
      text: `${formatMinutes(progress.minutes ?? 0)} of ${formatMinutes(goal.target_minutes)} planned`,
      met: progress.met_minutes === true,
    });
  }
  return out;
}

/** What a goal is scoped to, in words: "any practice", "Study in C",
 * "ear training". */
export function goalScopeLabel(goal) {
  if (!goal) return "";
  if (goal.scope === "score") return goal.score_title ?? "one piece";
  if (goal.scope === "activity") return activityLabel(goal.activity).toLowerCase();
  return "any practice";
}

/** How much of a running period is left, or nothing at all once it has ended.
 *
 * Days remaining and not a rate: "you need forty minutes a day to catch up" is
 * a verdict wearing arithmetic, and it is the one thing a person looking at a
 * half-finished week does not need told.
 */
export function timeLeftStatement(progress) {
  if (!progress || progress.status !== "running") return "";
  const left = progress.days_left ?? 0;
  if (left <= 0) return "";
  if (left === 1) return "1 day left in this week";
  return `${left} days left in this week`;
}

/** What a period came to, whether or not a goal was set for it. */
export function periodStatement(facts) {
  if (!facts || !facts.days_practised) return "No practice recorded this week";
  const days = facts.days_practised;
  return `${days} ${days === 1 ? "day" : "days"}, ${formatDuration(facts.seconds)}`;
}

/** The bars of a week's per-day strip, scaled against the busiest day IN THAT
 * WEEK.
 *
 * Scaled within the week and never against another week or an all-time high:
 * a bar that shrinks because last month was busier is a comparison, and a
 * comparison is the thing this feature must not make. A week with no practice
 * has no tallest day, so every bar is empty rather than every bar being full.
 */
export function dayBars(days = []) {
  const most = days.reduce((max, d) => Math.max(max, d.seconds ?? 0), 0);
  return days.map((d) => ({
    date: d.date,
    label: shortDayName(d.date),
    seconds: d.seconds ?? 0,
    sessions: d.sessions ?? 0,
    // A day with any practice at all gets a visible floor, so "a little" never
    // renders as "nothing".
    fill: most > 0 && d.seconds > 0 ? Math.max(0.12, d.seconds / most) : 0,
  }));
}

/** A session's worked range, as a person wrote it: "bars 17-32", "page 2". */
export function rangeLabel(session) {
  if (!session) return "";
  const parts = [];
  const span = (from, to, one, many) => {
    if (from == null) return null;
    return to == null || to === from ? `${one} ${from}` : `${many} ${from}-${to}`;
  };
  const bars = span(session.from_bar, session.to_bar, "bar", "bars");
  const pages = span(session.from_page, session.to_page, "page", "pages");
  if (bars) parts.push(bars);
  if (pages) parts.push(pages);
  return parts.join(", ");
}

/** A session's tempo, and whether a ladder run reached what it was aiming at.
 *
 * "reached 120" only when it did; when it did not, the two numbers are stated
 * and nothing is said about the gap. `reached_target` is null when either
 * number is missing, which is not the same as not having reached it.
 */
export function tempoLabel(session) {
  if (!session || session.tempo_bpm == null) return "";
  const at = `${session.tempo_bpm} bpm`;
  if (session.target_tempo_bpm == null) return at;
  if (session.reached_target === true) {
    return `${at}, reached the ${session.target_tempo_bpm} target`;
  }
  return `${at}, aiming at ${session.target_tempo_bpm}`;
}
