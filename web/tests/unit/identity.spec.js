// The header's identity display (issue #293) - a pure function of MeOut's
// own shape, checked directly with both shapes rather than through a
// mounted component. See fermata/authproxy.py and GET /api/me for what
// produces the shape this checks.
import { expect, test } from "@playwright/test";

import { identityLabel } from "../../src/lib/identity.js";

test("nothing is shown when reverse-proxy auth is off", () => {
  expect(identityLabel({ enabled: false, username: null })).toBeNull();
  // A response that never arrived (still loading) reads the same as off,
  // never as a name - a null answer must not flash a stale prior value.
  expect(identityLabel(null)).toBeNull();
});

test("the vouched name is shown when auth is on and the request carried one", () => {
  expect(identityLabel({ enabled: true, username: "alex" })).toBe("alex");
});

test("a request that carried no identity says so, distinctly from auth being off", () => {
  expect(identityLabel({ enabled: true, username: null })).toBe("no identity on this request");
});
