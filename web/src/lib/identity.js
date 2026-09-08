// What the header shows about who a trusted reverse proxy vouched for on
// this request (issue #16, #293) - display only, and nothing here changes
// what the application does with what it reads.
//
// No runes and no imports, the same reason practice.js has none: this is a
// pure function of MeOut's own shape, so a test can call it directly with
// both shapes rather than mounting a component to prove the text.

/**
 * What to show for `GET /api/me`'s answer, or null to show nothing.
 *
 * `enabled` false means reverse-proxy auth is not configured at all - the
 * out-of-the-box state - and nothing is shown, the same as before this
 * feature existed. `enabled` true with `username` null means auth IS
 * configured but this particular request carried no identity, which is a
 * different fact from auth being off and is said as one.
 */
export function identityLabel(me) {
  if (!me || !me.enabled) return null;
  return me.username || "no identity on this request";
}
