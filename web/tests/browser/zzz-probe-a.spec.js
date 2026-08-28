import { test, expect } from "@playwright/test";

test("probe a1 fails on purpose", async () => {
  expect(1).toBe(2);
});

test("probe a2 would pass", async () => {
  expect(1).toBe(1);
});
