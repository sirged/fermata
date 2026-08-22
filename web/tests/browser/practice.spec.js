// The practice page, against the real backend and the real build.
//
// What these are for that a unit test cannot do: the phrasing helpers are
// tested directly in tests/unit/practice.spec.js, but "a goal that was not
// reached is not styled as an error" is a claim about computed CSS on a real
// page, and "the week the interface offers is the week the server counts" is a
// claim about two independent pieces of calendar arithmetic agreeing. Neither
// survives being asserted against a mock.
//
// The assertions here are therefore about seams: the text that reaches the
// screen, the colours it reaches it in, the values that survive a reload, and
// the fact that a session logged from this page lands in the record the server
// keeps.
import { expect, test } from "@playwright/test";

import { addDays, forbiddenWord, localDay, weekStart } from "../../src/lib/practice.js";

const week = (page) => page.locator("section.week");
const statements = (page) => page.locator("section.week .statement");
const days = (page) => page.locator("section.week .strip .day");
const sessionRows = (page) => page.locator(".session-list .session");
const pastWeeks = (page) => page.locator(".past-week");
// Shared so that every "nothing went wrong" assertion uses the SAME selector
// one test proves can match something. A toHaveCount(0) built from an inline
// literal is permanently true the moment the class is renamed.
const notices = (page) => page.locator(".notice");

const today = localDay();

// A timezone whose calendar day is GUARANTEED not to be the UTC one, chosen
// from the clock when this file is loaded. Kiritimati is UTC+14, so it is a day
// ahead once UTC passes ten in the morning; Midway is UTC-11, so it is a day
// behind until UTC reaches eleven. One of the two always differs, whatever hour
// CI happens to run at - and a fixture that only sometimes differs is a fixture
// that only sometimes tests anything.
const SKEWED_ZONE = new Date().getUTCHours() < 11 ? "Pacific/Midway" : "Pacific/Kiritimati";

async function reset(request) {
  // The suite shares one database, and a goal is unique per week - so a test
  // that left one behind would decide what the next test sees.
  const goals = (await (await request.get(`/api/practice/goals?today=${today}`)).json()).goals;
  for (const goal of goals) await request.delete(`/api/practice/goals/${goal.id}`);
  const sessions = (
    await (await request.get("/api/practice/sessions?limit=1000")).json()
  ).sessions;
  for (const session of sessions) await request.delete(`/api/practice/sessions/${session.id}`);
  await request.put("/api/settings", { data: { week_starts_on: "monday" } });
}

async function logPractice(request, { day, minutes, activity = "technique", note = null }) {
  const res = await request.post("/api/practice/sessions", {
    data: { activity, seconds: minutes * 60, local_date: day, note },
  });
  expect(res.ok(), await res.text()).toBe(true);
}

test.beforeEach(async ({ page, request }) => {
  // Refuses to touch anything that is not the throwaway instance this suite
  // starts. The cleanup above DELETES practice sessions, and practice history
  // is the one thing in this application that cannot be regenerated from the
  // files on disk - so running these against a real install would destroy the
  // very data the feature exists to keep.
  const scores = await (await request.get("/api/scores")).json();
  expect(
    scores,
    "refusing to run: this backend has scores in its library, so it is not the " +
      "throwaway instance the suite creates - and these tests delete practice history",
  ).toEqual([]);

  await reset(request);
  await page.goto("/#/practice");
  await expect(week(page)).toBeVisible();
});

// UTC, deliberately and specifically, because an inferred session is the one
// row whose week the server and this page derive DIFFERENTLY. A session with no
// recorded day is filed under date(started_at) - its UTC day - while the page
// asks for the week around its own local day (see api.js: every practice call
// carries `today=`). In any negative offset late on a Sunday those are
// different weeks, and the session the test just created is not in the one on
// screen: a real failure, not a theoretical one, and one that would have
// arrived as a mystery on somebody else's afternoon.
//
// Pinning the zone makes the test deterministic. It does not make the
// disagreement go away, and the disagreement is the more interesting finding:
// it is the same shape as the defect local_date was introduced to fix, which
// was fixed by STORING the day rather than deriving it. A row that predates the
// column cannot be fixed that way, so an inferred day is filed in whichever
// week UTC puts it in, and for a practiser west of Greenwich that can be the
// week after the one they practised in. Which is a reason to say "inferred"
// beside it rather than a reason to stop counting it - but it is worth knowing
// that the badge is marking two facts at once: the day was not recorded, and
// the week it landed in is not necessarily the practiser's own.
test.describe("with a practice day that was inferred rather than recorded", () => {
  test.use({ timezoneId: "UTC" });

  test("the day says so beside the date, and the week's total says how much of it rests on one", async ({
    page,
    request,
  }) => {
    // Issue #103. The server has always answered `local_date_source` on a
    // session and `sessions_inferred` on a period's facts, and nothing in the
    // interface read either - so a day worked out from a UTC timestamp looked
    // exactly like one a person's own clock reported. For practice logged
    // before the day was stored at all, that is every row an existing install
    // has.
    //
    // Logged with no local_date, which is what produces an inferred day - the
    // same shape those older rows have.
    const inferred = await request.post("/api/practice/sessions", {
      data: { activity: "technique", seconds: 1800 },
    });
    expect(inferred.ok(), await inferred.text()).toBe(true);
    const inferredSession = await inferred.json();
    expect(
      inferredSession.local_date_source,
      "the server is expected to call this day inferred, or this test proves nothing",
    ).toBe("utc_date");

    // The day the PAGE thinks it is, asked of the page rather than assumed, and
    // checked against UTC - so if the timezone pin above ever stops taking
    // effect this fails here with a clear reason instead of failing later as a
    // missing row.
    const browserDay = await page.evaluate(() => {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    });
    expect(browserDay, "the timezone pin is what makes this test deterministic").toBe(
      new Date().toISOString().slice(0, 10),
    );

    // ...and one whose day WAS recorded, on that same day, so the mark below is
    // known to be about one row rather than about every row.
    await logPractice(request, { day: browserDay, minutes: 20 });

    await page.reload();
    await expect(week(page)).toBeVisible();
    await expect(sessionRows(page)).toHaveCount(2);
    // On THAT row, identified by its own id rather than by position - both
    // sessions land on the same date here, so "one row is marked" would also be
    // satisfied by marking the wrong one.
    const marked = page.locator(
      `.session-list .session[data-session="${inferredSession.id}"] .session-day`,
    );
    await expect(marked.locator(".day-inferred")).toHaveText("inferred");
    // ...and on no other. Anchored on .session-day, which the row assertions
    // elsewhere in this file prove can match.
    await expect(page.locator(".session-list .session .session-day .day-inferred")).toHaveCount(1);

    // BESIDE the date, not under it. The badge shares a fixed grid track with
    // the date, so a track a few pixels too narrow drops it onto its own line -
    // and every assertion above still passes, because text is all they look at.
    // Same lesson as anchoring a negative assertion: presence is not placement.
    const box = async (locator) => locator.boundingBox();
    const date = await box(marked.locator(".day-date"));
    const badge = await box(marked.locator(".day-inferred"));
    expect(badge.x, `date ${JSON.stringify(date)} badge ${JSON.stringify(badge)}`).toBeGreaterThan(
      date.x + date.width - 1,
    );
    // Same line: their vertical midpoints are within one line of each other.
    expect(
      Math.abs(badge.y + badge.height / 2 - (date.y + date.height / 2)),
      `date ${JSON.stringify(date)} badge ${JSON.stringify(badge)}`,
    ).toBeLessThan(date.height);

    // And the total says how much of itself rests on such a day. Without this a
    // window spanning the upgrade adds two different kinds of day together with
    // nothing marking the join.
    await expect(week(page).locator(".no-goal")).toContainText("1 session on an inferred day");
    // Still said in the vocabulary this feature is allowed to use - nothing
    // here is a reprimand.
    const said = await week(page).locator(".no-goal").textContent();
    expect(forbiddenWord(said), said).toBeNull();
  });
});

test("with no goal set, the week says so and shows seven empty days", async ({ page }) => {
  const thisWeek = week(page).locator(".no-goal");
  await expect(thisWeek).toContainText("No goal set for this week");
  await expect(thisWeek).toContainText("No practice recorded this week");
  await expect(days(page)).toHaveCount(7);
  const fills = await days(page)
    .locator(".bar")
    .evaluateAll((bars) => bars.map((b) => b.style.height));
  expect(fills).toEqual(Array(7).fill("0%"));
  await expect(notices(page)).toHaveCount(0);
});

test("a fresh install is greeted rather than shown fourteen absences", async ({ page }) => {
  // Nothing practised and nothing planned. Every section below the week would
  // be a statement of absence - seven "No practice recorded" rows and seven
  // "No goal was set" ones - which is a poor first impression for somebody who
  // has simply not started yet.
  await expect(page.locator(".nothing-yet")).toBeVisible();
  await expect(page.locator(".nothing-yet")).toContainText("Nothing logged yet");
  await expect(pastWeeks(page)).toHaveCount(0);
  await expect(page.locator("section.review")).toHaveCount(0);
  // The week itself, and the way to set a goal, are still there.
  await expect(days(page)).toHaveCount(7);
  await expect(page.locator(".edit-goal")).toBeVisible();
});

test("once there is practice, the review appears", async ({ page, request }) => {
  // The other half of the test above: the greeting has to give way, or it is
  // just a way of hiding the feature.
  await logPractice(request, { day: weekStart(today, "monday"), minutes: 20 });
  await page.reload();
  await expect(page.locator(".nothing-yet")).toHaveCount(0);
  await expect(page.locator("section.review")).toBeVisible();
  await expect(pastWeeks(page)).toHaveCount(7);
});

test("a goal is set from this page and its progress is stated as counts", async ({
  page,
  request,
}) => {
  const monday = weekStart(today, "monday");
  await logPractice(request, { day: monday, minutes: 30 });
  await logPractice(request, { day: addDays(monday, 1), minutes: 30 });
  await page.reload();

  await page.locator(".edit-goal").click();
  await page.locator(".days-target").selectOption("3");
  await page.locator(".minutes-target").fill("120");
  await page.locator(".intent-input").fill("the awkward middle section");
  await page.locator(".save-goal").click();

  await expect(statements(page)).toHaveCount(2);
  await expect(page.locator('[data-statement="days"]')).toContainText("2 of 3 planned days");
  await expect(page.locator('[data-statement="minutes"]')).toContainText("1h of 2h planned");
  await expect(page.locator(".intent-text")).toContainText("the awkward middle section");
  await expect(notices(page)).toHaveCount(0);

  // And it is the server's answer, not the form's - a reload reads it back.
  await page.reload();
  await expect(page.locator('[data-statement="days"]')).toContainText("2 of 3 planned days");
});

test("a target not reached is drawn exactly like one that is", async ({ page, request }) => {
  // The whole difference between accountable and shamed, as computed CSS. A
  // goal not met is not an error condition and must not be coloured like one.
  const monday = weekStart(today, "monday");
  await logPractice(request, { day: monday, minutes: 200 });
  await page.reload();

  await page.locator(".edit-goal").click();
  await page.locator(".days-target").selectOption("5");
  await page.locator(".minutes-target").fill("60");
  await page.locator(".save-goal").click();

  const unmet = page.locator('[data-statement="days"]');
  const met = page.locator('[data-statement="minutes"]');
  await expect(unmet).toContainText("1 of 5 planned days");
  await expect(met).toContainText("3h 20m of 1h planned");

  const style = (locator) =>
    locator.evaluate((el) => {
      const computed = getComputedStyle(el);
      const text = getComputedStyle(el.querySelector(".statement-text"));
      return {
        color: computed.color,
        textColor: text.color,
        weight: text.fontWeight,
        background: computed.backgroundColor,
        border: computed.borderColor,
      };
    });
  const unmetStyle = await style(unmet);
  const metStyle = await style(met);
  expect(unmetStyle).toEqual(metStyle);

  // Independent of the comparison above, which would also pass if BOTH were
  // red: the danger colour this app uses for real errors must not appear.
  const danger = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--danger").trim(),
  );
  expect(danger).not.toBe("");
  const dangerRgb = await page.evaluate((hex) => {
    const probe = document.createElement("span");
    probe.style.color = hex;
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }, danger);
  expect(unmetStyle.textColor).not.toBe(dangerRgb);

  // The only difference is additive: a tick appears beside what was reached.
  await expect(met.locator(".tick")).toHaveText("✓");
  await expect(unmet.locator(".tick")).toHaveText("");
});

test("a running week says how many days are left and nothing about catching up", async ({
  page,
}) => {
  await page.locator(".edit-goal").click();
  await page.locator(".save-goal").click();
  await expect(page.locator(".days-left")).toContainText(/\d+ days? left in this week/);
  const text = await page.locator("section.week").innerText();
  expect(text.toLowerCase()).not.toContain("catch up");
  expect(text.toLowerCase()).not.toContain("per day");
});

test("practice that is not a piece is logged here and lands in the record", async ({ page }) => {
  await page.locator(".other-activity").selectOption("ear_training");
  await page.locator(".other-minutes").fill("20");
  await page.locator(".other-note").fill("intervals, ascending only");
  await page.locator(".log-other-button").click();

  await expect(sessionRows(page)).toHaveCount(1);
  await expect(sessionRows(page).first()).toContainText("Ear training");
  await expect(sessionRows(page).first()).toContainText("20m");
  await expect(sessionRows(page).first()).toContainText("intervals, ascending only");
  await expect(sessionRows(page).first()).toContainText(today);
  await expect(page.locator(".by-activity")).toContainText("Ear training");
  await expect(notices(page)).toHaveCount(0);

  // The day it was practised, as the browser's own date - a bar appears on
  // today and nowhere else.
  const withPractice = await days(page).evaluateAll((cells) =>
    cells.filter((c) => Number(c.dataset.seconds) > 0).map((c) => c.dataset.day),
  );
  expect(withPractice).toEqual([today]);
});

test("a finished week asks whether the goal was realistic, and remembers the answer", async ({
  page,
  request,
}) => {
  const lastWeek = addDays(weekStart(today, "monday"), -7);
  await logPractice(request, { day: lastWeek, minutes: 45 });
  const created = await request.post(`/api/practice/goals?today=${today}`, {
    data: { period_start: lastWeek, target_days: 4 },
  });
  expect(created.ok(), await created.text()).toBe(true);
  await page.reload();

  const card = page.locator(`.past-week[data-week="${lastWeek}"]`);
  await expect(card).toBeVisible();
  await expect(card).toContainText("1 of 4 planned days");
  // A question, not a verdict.
  await expect(card.locator(".question")).toHaveText("Was this goal realistic?");

  await card.locator('[data-answer="no"]').click();
  await card.locator(".reflection-text").fill("away for three days");
  await card.locator(".save-reflection").click();

  await expect(notices(page)).toHaveCount(0);
  await page.reload();
  const again = page.locator(`.past-week[data-week="${lastWeek}"]`);
  await expect(again.locator(".reflection-text")).toHaveValue("away for three days");
  await expect(again.locator('[data-answer="no"]')).toHaveClass(/on/);
});

test("a week nobody set a goal for still appears with what happened in it", async ({
  page,
  request,
}) => {
  const lastWeek = addDays(weekStart(today, "monday"), -7);
  await logPractice(request, { day: addDays(lastWeek, 2), minutes: 25 });
  await page.reload();

  const card = page.locator(`.past-week[data-week="${lastWeek}"]`);
  await expect(card).toContainText("1 day, 25m");
  await expect(card.locator(".no-goal")).toContainText("No goal was set for these days");
  // Not "this week" - every past row used to say that, so a quiet spell in
  // July rendered as three consecutive rows naming a week nowhere near them.
  await expect(card).not.toContainText("this week");
  // Every week in the window is listed, goal or no goal, so the review is not
  // a list of judged weeks.
  await expect(pastWeeks(page)).toHaveCount(7);
});

test("a past week with nothing in it does not call itself this week", async ({
  page,
  request,
}) => {
  // Rendered, not asserted on a string in isolation - which is how this got
  // through: every past row said "this week", so a gap after a good run showed
  // three consecutive rows naming a week nowhere near their own dates.
  await logPractice(request, { day: today, minutes: 20 });
  await page.reload();

  const empty = pastWeeks(page).first();
  await expect(empty).toContainText("No practice recorded");
  await expect(empty).not.toContainText("this week");
  // And the current week's panel still says it, where it is true.
  await expect(week(page)).toContainText("this week");
});

test("the week this page offers is the week the server counts", async ({ page, request }) => {
  await request.put("/api/settings", { data: { week_starts_on: "sunday" } });
  await page.reload();

  const offered = await week(page).getAttribute("data-week");
  expect(offered).toBe(weekStart(today, "sunday"));

  await page.locator(".edit-goal").click();
  await page.locator(".save-goal").click();
  // Wait for the save to have LANDED before reading it back. click() resolves
  // when the click is dispatched, not when the request it triggers finishes,
  // and the read below goes out of band - through the request context rather
  // than the page - so it is not ordered against it. Without this the read
  // overtakes the POST and sees no goals; it failed in CI exactly that way.
  //
  // The edit form closing is a real barrier rather than a convenient one:
  // saveGoal() sets editing = false only AFTER awaiting the request, so this
  // cannot be true until the server has answered.
  //
  // This is the second time this pattern has bitten (see #82, which asked for
  // it to be grepped for). Every other out-of-band read in this suite waits on
  // the interface first - for a count attribute, a rendered row, a tick - and
  // those assertions are barriers as much as checks. Do not remove one.
  await expect(page.locator(".edit-goal")).toBeVisible();
  const goals = (await (await request.get(`/api/practice/goals?today=${today}`)).json()).goals;
  expect(goals).toHaveLength(1);
  expect(goals[0].period_start).toBe(weekStart(today, "sunday"));
});

test("a goal can be adjusted mid-week, and removing it keeps the practice", async ({
  page,
  request,
}) => {
  await logPractice(request, { day: today, minutes: 20 });
  await page.reload();

  await page.locator(".edit-goal").click();
  await page.locator(".days-target").selectOption("5");
  await page.locator(".save-goal").click();
  await expect(page.locator('[data-statement="days"]')).toContainText("1 of 5 planned days");

  // Seeing where you stand is only useful if the goal can still change the
  // week rather than only judge it afterwards.
  await page.locator(".edit-goal").click();
  await page.locator(".days-target").selectOption("1");
  await page.locator(".save-goal").click();
  await expect(page.locator('[data-statement="days"]')).toContainText("1 of 1 planned days");
  await expect(page.locator('[data-statement="days"] .tick')).toHaveText("✓");
  // Adjusting replaced the goal rather than adding a second one for the week.
  const goals = (await (await request.get(`/api/practice/goals?today=${today}`)).json()).goals;
  expect(goals).toHaveLength(1);

  await page.locator(".edit-goal").click();
  await page.locator(".remove-goal").click();
  await expect(page.locator("section.week .no-goal")).toContainText("No goal set for this week");
  await expect(sessionRows(page)).toHaveCount(1);
});

test.describe("in a timezone whose calendar day is not the UTC one", () => {
  test.use({ timezoneId: SKEWED_ZONE });

  test("a session is filed under the practiser's own day, not the server's", async ({
    page,
    request,
  }) => {
    // The reason local_date exists at all. The server stores UTC timestamps,
    // so leaving the day to be derived there files an evening's practice on
    // the wrong date - and at a week boundary in the wrong week, against a
    // goal counting days.
    const browserDay = await page.evaluate(() => {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, "0");
      return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    });
    const utcDay = new Date().toISOString().slice(0, 10);
    expect(
      browserDay,
      `this test is only meaningful when the browser's day differs from UTC's (zone ${SKEWED_ZONE})`,
    ).not.toBe(utcDay);

    await page.locator(".other-activity").selectOption("technique");
    await page.locator(".other-minutes").fill("18");
    await page.locator(".log-other-button").click();
    await expect(sessionRows(page)).toHaveCount(1);

    const stored = (
      await (await request.get("/api/practice/sessions?limit=10")).json()
    ).sessions;
    expect(stored).toHaveLength(1);
    expect(stored[0].local_date).toBe(browserDay);
    expect(stored[0].local_date_source).toBe("recorded");
    // And the page agrees with what was stored, rather than each having its
    // own idea of which day it was.
    await expect(sessionRows(page).first()).toContainText(browserDay);
  });
});

test("nothing on this page says anything that grades the person", async ({ page, request }) => {
  // The page in its least flattering state: a goal nowhere near reached, a
  // week with almost nothing in it, and a past week with no goal at all.
  const monday = weekStart(today, "monday");
  await logPractice(request, { day: monday, minutes: 2 });
  const created = await request.post(`/api/practice/goals?today=${today}`, {
    data: { period_start: monday, target_days: 7, target_minutes: 600 },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const lastWeek = addDays(monday, -7);
  await request.post(`/api/practice/goals?today=${today}`, {
    data: { period_start: lastWeek, target_days: 6 },
  });
  await page.reload();
  await expect(statements(page)).toHaveCount(2);

  const text = await page.locator("main").innerText();
  expect(forbiddenWord(text), text).toBeNull();
});
