// Fixtures for the note editor (#10), see ../score-editor.spec.js.
//
// The MusicXML below is the shape Fermata's own emitter writes - one part, one
// TAB staff, a six-string standard tuning, <divisions>480</divisions>, and a
// Rule 17 id on every note (n{measure}-{voice}-{onset}-{chord}). It is a real
// document the real alphaTab importer renders and the real editor/document.js
// parses; nothing here is stubbed but the HTTP transport. Kept monophonic (one
// note per beat) so a click lands unambiguously on a known note.
//
// The notes, in document order, are what the spec asserts against:
//
//   ord  id          string  fret   pitch  midi
//   0    n1-1-0-0    1       0      E4     64
//   1    n1-1-1-0    1       2      F#4    66
//   2    n1-1-2-0    1       3      G4     67
//   3    n1-1-3-0    1       5      A4     69
//   4    n2-1-0-0    2       0      B3     59
//   5    n2-1-1-0    2       1      C4     60
//   6    n2-1-2-0    2       3      D4     62
//   7    n2-1-3-0    2       5      E4     64
//
// <string> 1 is the highest string (E4); on this six-string staff it reads its
// tuning from <staff-tuning line="6"> - the Rule 5 mirror the editor has to get
// right, and the divergence guard checks.

export const SCORE = {
  id: 1,
  title: "Editor Test Score",
  file_type: "pdf",
  has_transcription: true,
  favorite: false,
  content_kind: "tab",
  tags: [],
};

export const MIN_PDF = Buffer.from(
  "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" +
    "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" +
    "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n" +
    "xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n" +
    "trailer<</Size 4/Root 1 0 R>>\nstartxref\n164\n%%EOF",
  "utf-8",
);

const STAFF_DETAILS = `
        <staff-details>
          <staff-lines>6</staff-lines>
          <staff-tuning line="1"><tuning-step>E</tuning-step><tuning-octave>2</tuning-octave></staff-tuning>
          <staff-tuning line="2"><tuning-step>A</tuning-step><tuning-octave>2</tuning-octave></staff-tuning>
          <staff-tuning line="3"><tuning-step>D</tuning-step><tuning-octave>3</tuning-octave></staff-tuning>
          <staff-tuning line="4"><tuning-step>G</tuning-step><tuning-octave>3</tuning-octave></staff-tuning>
          <staff-tuning line="5"><tuning-step>B</tuning-step><tuning-octave>3</tuning-octave></staff-tuning>
          <staff-tuning line="6"><tuning-step>E</tuning-step><tuning-octave>4</tuning-octave></staff-tuning>
        </staff-details>`;

function note(id, step, alter, octave, string, fret) {
  const alterEl = alter ? `<alter>${alter}</alter>` : "";
  return `      <note id="${id}">
        <pitch><step>${step}</step>${alterEl}<octave>${octave}</octave></pitch>
        <duration>480</duration>
        <voice>1</voice>
        <type>quarter</type>
        <notations><technical><string>${string}</string><fret>${fret}</fret></technical></notations>
      </note>`;
}

export const EDITOR_MUSICXML = `<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list>
    <score-part id="P1"><part-name>Guitar</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>480</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>TAB</sign><line>5</line></clef>${STAFF_DETAILS}
      </attributes>
${note("n1-1-0-0", "E", 0, 4, 1, 0)}
${note("n1-1-1-0", "F", 1, 4, 1, 2)}
${note("n1-1-2-0", "G", 0, 4, 1, 3)}
${note("n1-1-3-0", "A", 0, 4, 1, 5)}
    </measure>
    <measure number="2">
${note("n2-1-0-0", "B", 0, 3, 2, 0)}
${note("n2-1-1-0", "C", 0, 4, 2, 1)}
${note("n2-1-2-0", "D", 0, 4, 2, 3)}
${note("n2-1-3-0", "E", 0, 4, 2, 5)}
    </measure>
  </part>
</score-partwise>`;

// The same score with one note moved to a string the six-string staff does not
// have (<string>7</string>) - the under-specified tab a directly uploaded or
// hand-edited file can carry (issue #165). The editor never writes this; it is
// here to prove that loading it, with note bounds and the notation staff turned
// on for edit mode, still does not crash the renderer's paint - the
// disqualifyUnstrungTabStaves guard holds.
export const UNSTRUNG_MUSICXML = EDITOR_MUSICXML.replace(
  '<technical><string>1</string><fret>0</fret></technical>',
  '<technical><string>7</string><fret>0</fret></technical>',
);

export function transcriptionBody(content) {
  return {
    id: 1,
    score_id: 1,
    format: "musicxml",
    content,
    source: "extracted",
    confidence: JSON.stringify({ warnings: [], confidence: {} }),
    warnings: [],
  };
}

/**
 * Stubs every /api route the score view touches for score id 1, serving the
 * given MusicXML as the transcription. A PUT (the editor's save) captures the
 * body and, from then on, GET returns that saved content as a source='edited'
 * row - so "save, reload the page, and the edit is still there" is a real
 * round trip through the same client the app uses, not an assumption.
 *
 * Returns `{ saved }` - `saved.content` is the last PUT body, for a test that
 * wants to assert on exactly what was persisted.
 */
export async function stubEditorApi(page, initialContent) {
  const saved = { content: null };
  let current = transcriptionBody(initialContent);

  await page.route("**/api/scores/1", (route) => route.fulfill({ json: SCORE }));
  await page.route("**/api/scores/1/file", (route) =>
    route.fulfill({ body: MIN_PDF, contentType: "application/pdf" }),
  );
  await page.route("**/api/scores/1/practice", (route) =>
    route.fulfill({ json: { total_seconds: 0, sessions: [] } }),
  );
  await page.route("**/api/scores/1/transcription/analysis", (route) =>
    route.fulfill({ json: { extractable: true } }),
  );
  await page.route("**/api/scores/1/transcription", async (route) => {
    const method = route.request().method();
    if (method === "PUT") {
      const body = JSON.parse(route.request().postData() || "{}");
      saved.content = body.content;
      current = { ...transcriptionBody(body.content), source: "edited", confidence: null };
      return route.fulfill({ json: current });
    }
    if (method === "DELETE") {
      return route.fulfill({ json: { ...transcriptionBody(initialContent), source: "extracted" } });
    }
    return route.fulfill({ json: current });
  });

  return { saved };
}
