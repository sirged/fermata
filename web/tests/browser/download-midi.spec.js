// Issue #270: the tab viewer plays a score through the renderer's own
// synthesiser but never offered a way to take that performance out of the
// browser. score-render.js's createScoreView() now exposes exportMidi() -
// the renderer's own MidiFileGenerator, not a re-implementation - and
// TabViewer.svelte wires a "Download MIDI" toolbar button to it. This spec
// drives that whole path with a real upload: a real MIDI file lands on
// disk, its header says what MidiFileGenerator actually produced, and the
// filename is what the sanitiser actually did to the score's own title -
// not what the code is merely supposed to do.
//
// THE FIXTURE. web/test-fixtures/midi-export-fixture.musicxml is original,
// two parts (so the exported file's track count is a real check rather than
// the trivially-true "1" a single-part fixture gives - see its own header),
// with a work-title carrying a "/" on purpose: "Fermata MIDI export/fixture"
// sanitises to "Fermata MIDI exportfixture" (the slash removed outright, no
// space substituted - see TabViewer.svelte's sanitizeFilename), so the
// suggested filename is a direct, hand-computed assertion, not a guess at
// what "some path character got removed" might look like.
//
// WHY THE HEADER IS READ BYTE-FOR-BYTE. A standard MIDI file opens with the
// four-byte tag "MThd", a 32-bit big-endian chunk length (always 6), a
// 16-bit format (1 == MidiFileFormat.MultiTrack, what exportMidi() asks
// for), and a 16-bit track count - see the Standard MIDI File spec and
// score-render.js's own exportMidi() comment for why MultiTrack (not the
// SingleTrackMultiChannel default alphaTab's own api.downloadMidi() uses)
// is what makes that count equal the score's own track count rather than
// always reading 1.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "..", "..", "test-fixtures", "midi-export-fixture.musicxml");
const SCORE_NAME = "midi-export-fixture.musicxml";
const UNRENDERABLE_FIXTURE = path.join(here, "..", "..", "test-fixtures", "unrenderable.musicxml");
const UNRENDERABLE_NAME = "unrenderable-for-midi-export.musicxml";

const EXPECTED_TRACKS = "2";
// sanitizeFilename removes only /\:*?"<>| - the slash in the fixture's own
// title is the one character this proves gets stripped, not substituted.
const EXPECTED_FILENAME = "Fermata MIDI exportfixture.mid";

const OWN_PATHS = [`Uploads/${SCORE_NAME}`, `Uploads/${UNRENDERABLE_NAME}`];

const host = (page) => page.locator(".at-host");
const playButton = (page) => page.locator(".player button.primary");
const downloadButton = (page) => page.getByRole("button", { name: "Download MIDI", exact: true });

async function waitForScore(request, name) {
  // The scan runs in a background thread - see guitar-pro-import.spec.js's
  // own copy of this wait for the same reason.
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const scores = await (await request.get("/api/scores")).json();
    const found = scores.find((s) => s.path.endsWith(name));
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`the uploaded score ${name} never appeared in the library`);
}

async function uploadFixture(request, fixturePath, name) {
  const res = await request.post("/api/upload?folder=Uploads", {
    multipart: {
      file: { name, mimeType: "application/octet-stream", buffer: fs.readFileSync(fixturePath) },
    },
  });
  expect(res.ok(), await res.text()).toBe(true);
  return waitForScore(request, name);
}

async function openScore(page, id) {
  await page.goto(`/#/score/${id}`);
  await expect(playButton(page)).toBeEnabled({ timeout: 30_000 });
  await expect(host(page)).toHaveAttribute("data-score-render-ok", "true", { timeout: 30_000 });
}

// Same convention as guitar-pro-import.spec.js's emptyTheLibrary: a single
// DELETE moves a score to the trash, so the trash also has to be purged or
// the score would still show up (as a trashed row) to a later spec's "the
// library is empty" check.
async function emptyTheLibrary(request) {
  for (const score of await (await request.get("/api/scores")).json()) {
    await request.delete(`/api/scores/${score.id}`);
  }
  for (const score of await (await request.get("/api/trash")).json()) {
    await request.delete(`/api/trash/${score.id}`);
  }
}

test.describe("Download MIDI", () => {
  test.beforeEach(async ({ request }) => {
    // The same refusal guitar-pro-import.spec.js makes: this suite must not
    // be pointed at a real library that already has scores in it.
    const existing = await (await request.get("/api/scores")).json();
    expect(
      existing.filter((s) => !OWN_PATHS.includes(s.path)),
      "refusing to run: this backend has scores in its library this spec did not put " +
        "there, so it is not the throwaway instance the suite creates",
    ).toEqual([]);
  });

  test.afterEach(async ({ request }) => {
    await emptyTheLibrary(request);
  });

  test("downloads a real MIDI file whose header and track count match the render, with a sanitised filename", async ({
    page,
    request,
  }) => {
    const consoleErrors = [];
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
    page.on("pageerror", (e) => consoleErrors.push(String(e)));

    const score = await uploadFixture(request, FIXTURE, SCORE_NAME);
    await openScore(page, score.id);

    // score-render.js's own account of the loaded score's track count - the
    // number exportMidi()'s SMF header is checked against below, not a
    // number this test invents on its own.
    await expect(host(page)).toHaveAttribute("data-score-tracks", EXPECTED_TRACKS);

    const button = downloadButton(page);
    await expect(button).toBeEnabled({ timeout: 10_000 });

    const downloadPromise = page.waitForEvent("download");
    await button.click();
    const download = await downloadPromise;

    // Chromium's own download dialog renders the suggested filename with
    // U+00A0 (no-break space) in place of every ordinary ASCII space -
    // confirmed by hand (charCodeAt on the raw value) against this exact
    // fixture, not assumed - which is a property of the browser's download
    // plumbing, not of TabViewer's sanitizeFilename(): the <a download> the
    // page sets carries an ordinary space (also confirmed - the same value
    // survives unmangled into the actually-saved file's own path below).
    // Normalised back before comparing so this assertion is about the
    // filename this app chose, not about that browser quirk.
    expect(download.suggestedFilename().replace(/\u00a0/g, " ")).toBe(EXPECTED_FILENAME);

    const savedPath = path.join(
      fs.mkdtempSync(path.join(os.tmpdir(), "fermata-midi-export-")),
      "downloaded.mid",
    );
    await download.saveAs(savedPath);
    const bytes = fs.readFileSync(savedPath);

    expect(bytes.length, "the downloaded file is suspiciously small").toBeGreaterThan(100);
    expect(bytes.subarray(0, 4).toString("ascii"), "missing the MThd header").toBe("MThd");
    // Bytes 8-9: the SMF format (big-endian uint16) - 1 == MidiFileFormat.MultiTrack.
    const format = bytes.readUInt16BE(8);
    expect(format, "expected MidiFileFormat.MultiTrack (1), not the Type-0 default").toBe(1);
    // Bytes 10-11: the header's own track-count field - has to equal the
    // score's track count as the renderer reports it (data-score-tracks
    // above), not just be present.
    const headerTrackCount = bytes.readUInt16BE(10);
    expect(headerTrackCount, "the SMF header's track count").toBe(Number(EXPECTED_TRACKS));

    expect(consoleErrors).toEqual([]);
  });

  test("the control is disabled for a score that fails to render", async ({ page, request }) => {
    const score = await uploadFixture(request, UNRENDERABLE_FIXTURE, UNRENDERABLE_NAME);
    // Not openScore(): this score is genuinely unrenderable (every staff
    // fails every profile's check - see score-render.js's supportedProfiles),
    // so data-score-render-ok never becomes "true" and the play button's own
    // readiness is a different question from the one this test asks.
    // publish() still writes data-score-profiles as soon as scoreLoaded runs
    // though - "" once profiles is known to be empty - so waiting on that
    // value is the correct sync point for "this score's degraded state has
    // settled".
    await page.goto(`/#/score/${score.id}`);
    await expect(host(page)).toHaveAttribute("data-score-profiles", "", { timeout: 30_000 });

    await expect(downloadButton(page)).toBeDisabled();
  });
});
