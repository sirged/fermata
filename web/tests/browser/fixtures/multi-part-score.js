// Fixture data + route stub for tests/browser/score-multi-part.spec.js
// (issue #93).
//
// The fixture itself lives at web/test-fixtures/multi-part.musicxml and is
// read here, byte for byte, rather than inlined - the same reasoning
// fixtures/navigation-score.js gives for reading its own committed
// transcription off disk: a hand-written copy in two places would only prove
// this file and the fixture agree with themselves, not that the real
// document renders correctly. It is a real MusicXML document through the
// real importer, for the same reason every other fixture in this directory
// is: what issue #93 is about (whether a second <part> reaches the renderer
// at all) is not something a stub of TabViewer's internals could ever answer.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = path.join(here, "..", "..", "..");

/**
 * Two <part>s - P1 "Upper Part" (treble, three bars), P2 "Lower Part" (bass,
 * three bars) - which is what becomes two alphaTab tracks, not two staves of
 * one track (contrast web/test-fixtures/multi-staff.musicxml, which is one
 * <part> with <staves>2</staves> and is not this bug: that file's second
 * staff already drew before this issue, because supportedProfiles() and the
 * renderer both walk every STAFF of the one track that was ever rendered.
 * Issue #93 is specifically that only the first TRACK renders, and a
 * multi-staff single-part file never exercises that).
 */
export const MULTI_PART_MUSICXML = fs.readFileSync(path.join(WEB_ROOT, "test-fixtures", "multi-part.musicxml"), "utf-8");

function scoreMeta(id, title) {
  // file_type not "pdf" routes Viewer.svelte to TabViewer directly, the same
  // choice fixtures/navigation-score.js's own scoreMeta() makes.
  return {
    id,
    title,
    composer: "",
    source: "",
    file_type: "musicxml",
    has_transcription: false,
    favorite: false,
    content_kind: "notation",
    tags: [],
  };
}

/** Score id 30: the two-part fixture above. */
export async function stubMultiPartScore(page) {
  await page.route(`**/api/scores/30`, (route) => route.fulfill({ json: scoreMeta(30, "Two-part fixture") }));
  await page.route(`**/api/scores/30/file`, (route) =>
    route.fulfill({ body: MULTI_PART_MUSICXML, contentType: "application/vnd.recordare.musicxml+xml" }),
  );
  await page.route(`**/api/scores/30/practice`, (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
}
