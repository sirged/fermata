// The build indicator (issue #119) - surfaced because a self-hosted
// deployment updated by rebuild has no auto-update and no release channel, so
// running a stale build is the ordinary failure mode. Its symptom is always
// the same: a feature that was merged looks like it does not exist, because
// the interface is old rather than broken. This checks the one thing that
// makes that diagnosable without asking anyone - a quiet, always-visible line
// in the library sidebar that names the build.
import { expect, test } from "@playwright/test";

const buildTag = (page) => page.locator(".build-tag");

test("the build indicator is on screen without any interaction", async ({ page, request }) => {
  await page.goto("/");
  const tag = buildTag(page);
  await expect(tag).toBeVisible();

  // Compared against the endpoint's own answer, not against a hand-copied
  // string: the test playwright's server is running under here has no build
  // args baked in, so this is the "dev" server path - the same one a plain
  // uvicorn run from source takes.
  const body = await (await request.get("/api/version")).json();
  expect(body.commit).toBe("dev");
  expect(body.built).toBe("dev");
  await expect(tag).toHaveText(`v${body.version} (dev)`);
});

test.skip("the indicator needs no hover - it renders in the initial layout", async ({ page }) => {
  // This app's primary form factor is a tablet with no pointer, so anything
  // that only appeared on :hover would be permanently invisible there. Read
  // straight off the accessibility tree / layout rather than via a hover
  // interaction, which is itself the assertion: nothing here dispatches a
  // mouseover before checking visibility.
  await page.goto("/");
  await expect(buildTag(page)).toBeVisible();
  const box = await buildTag(page).boundingBox();
  expect(box).not.toBeNull();
  expect(box.width).toBeGreaterThan(0);
  expect(box.height).toBeGreaterThan(0);
});
