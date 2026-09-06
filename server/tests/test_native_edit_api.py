"""Editing a score that IS notation, rather than a transcription of one (#262).

The web side of this bet is what changed: a native MusicXML score now opens in
the note editor, and the save goes through the transcription PUT that already
existed. This module covers what the SERVER has to be true for that to work,
none of which had a test before, because before #262 nothing ever wrote a
transcription row for a non-PDF score:

  - PUT /scores/{id}/transcription accepts a musicxml score that has NO
    extracted row underneath the edit (transcription rows had only ever been
    created by the PDF extractor, which always writes the extracted row first).
  - the library FILE is never written by any of it - asserted by hashing the
    bytes on disk before and after, not by reading the row back.
  - DELETE removes the edit and then answers 404, because for a native score
    there is no extraction to fall back to. That 404 is the viewer's success
    signal for "the file is what is showing now", so it is pinned here rather
    than left as an accident of the route's shape.
  - the edit TRAVELS: an export/import round trip carries it like any other
    transcription. `transcriptions` was already in the archive, so this asserts
    a property the round trip should already have - which is exactly why it is
    worth a test: "it already does" is the claim most likely to stop being true
    without anybody noticing.

Deliberately a separate module from test_portability_api.py: the round-trip
assertion here is about ONE new kind of row, and bolting it onto that file's
single headline test would make a failure there ambiguous between the two.
"""

import hashlib
import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db, scanner

# A real part-wise MusicXML document, in the shape Fermata's own emitter
# writes - one part, one six-string TAB staff, divisions 480. Small on purpose:
# what is under test is where the bytes GO, not what is in them.
NATIVE_MUSICXML = """<?xml version="1.0" encoding="UTF-8"?>
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
        <clef><sign>TAB</sign><line>5</line></clef>
      </attributes>
      <note id="n1-1-0-0">
        <pitch><step>E</step><octave>4</octave></pitch>
        <duration>1920</duration>
        <voice>1</voice>
        <type>whole</type>
        <notations><technical><string>1</string><fret>0</fret></technical></notations>
      </note>
    </measure>
  </part>
</score-partwise>
"""

# The same document with the one note moved to the fifth fret - what a fret
# change through the editor produces, and what the edited row must carry.
EDITED_MUSICXML = NATIVE_MUSICXML.replace("<fret>0</fret>", "<fret>5</fret>")


@pytest.fixture
def library(app_env, tmp_path, monkeypatch):
    """A throwaway library the routes actually read and write. api.py and
    scanner.py each bound LIBRARY_DIR by value at import, so both need
    repointing alongside app_env's own - the same fixture shape
    test_portability_api.py uses, for the same reason."""
    root = tmp_path / "library"
    monkeypatch.setattr(api, "LIBRARY_DIR", root)
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root)
    return root


@pytest.fixture
def client(library):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture
def add_native_score(library):
    """A REAL MusicXML file in the library with a real score row over it,
    hashed from the actual bytes. file_type 'musicxml' is the whole point: a
    score whose own file is the notation, which is what nothing had ever
    written a transcription row for."""

    def _add(rel: str = "Uploads/etude.musicxml", content: str = NATIVE_MUSICXML) -> int:
        path = library / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # write_BYTES, never write_text: on Windows the latter translates every
        # \n to \r\n, so the file on disk would not be the constant this module
        # compares it against - and a byte-for-byte claim asserted against a
        # newline-translated read is not the claim it looks like.
        path.write_bytes(content.encode("utf-8"))
        stat = path.stat()
        conn = db.connect()
        parts = rel.split("/")
        cur = conn.execute(
            """INSERT INTO scores(title, collection, path, file_type, hash, size, mtime)
               VALUES (?, ?, ?, 'musicxml', ?, ?, ?)""",
            (
                parts[-1].rsplit(".", 1)[0],
                parts[0] if len(parts) > 1 else None,
                rel,
                scanner.hash_file(path),
                stat.st_size,
                stat.st_mtime,
            ),
        )
        conn.commit()
        return cur.lastrowid

    return _add


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _switch_to_a_fresh_environment(monkeypatch, tmp_path, name: str):
    """Point config, db and the two modules that bind LIBRARY_DIR by value at a
    brand new, empty root - the target half of a round trip. The same steps
    test_portability_api.py takes by hand, and for the same reason: conftest's
    `app_env` yields ONE environment and this test needs two in one process."""
    from fermata import config

    root = tmp_path / name
    (root / "library").mkdir(parents=True)
    monkeypatch.setattr(config, "LIBRARY_DIR", root / "library")
    monkeypatch.setattr(config, "CONFIG_DIR", root / "config")
    monkeypatch.setattr(config, "CACHE_DIR", root / "config" / "cache")
    monkeypatch.setattr(api, "LIBRARY_DIR", root / "library")
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root / "library")
    monkeypatch.setattr(db, "DB_PATH", root / "config" / "fermata.db")
    (root / "config").mkdir(parents=True, exist_ok=True)
    db._local.conn = None
    db.init_db()


def test_edit_saves_for_a_musicxml_score_with_no_extraction_under_it(
    client, library, add_native_score
):
    score_id = add_native_score()
    path = library / "Uploads/etude.musicxml"
    before = _sha256(path)

    # Nothing stored yet: this score is its file and nothing else.
    assert client.get(f"/api/scores/{score_id}/transcription").status_code == 404

    resp = client.put(
        f"/api/scores/{score_id}/transcription", json={"content": EDITED_MUSICXML}
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    # An EDIT, never an extraction - transcribe() is what writes 'extracted',
    # and it refuses a non-PDF score outright.
    assert saved["source"] == "edited"
    assert saved["format"] == "musicxml"

    read_back = client.get(f"/api/scores/{score_id}/transcription").json()
    assert read_back["source"] == "edited"
    assert "<fret>5</fret>" in read_back["content"]

    # Exactly one row, and it is the edit - a save that had also written an
    # 'extracted' row would leave the revert below with something to fall back
    # to that the user never asked for.
    rows = db.connect().execute(
        "SELECT source FROM transcriptions WHERE score_id = ?", (score_id,)
    ).fetchall()
    assert [r["source"] for r in rows] == ["edited"]

    # THE FILE IS NOT WRITTEN.
    assert _sha256(path) == before
    assert path.read_bytes() == NATIVE_MUSICXML.encode("utf-8")


def test_revert_removes_the_edit_and_404s_with_no_extraction_left(
    client, library, add_native_score
):
    score_id = add_native_score()
    path = library / "Uploads/etude.musicxml"
    before = _sha256(path)
    client.put(f"/api/scores/{score_id}/transcription", json={"content": EDITED_MUSICXML})

    # 404 here is the SUCCESS case, and the viewer reads it as one: the edited
    # row was deleted and there is no extraction underneath it, so the file is
    # what shows from now on.
    resp = client.delete(f"/api/scores/{score_id}/transcription")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no transcription for this score"

    assert client.get(f"/api/scores/{score_id}/transcription").status_code == 404
    assert db.connect().execute(
        "SELECT COUNT(*) AS n FROM transcriptions WHERE score_id = ?", (score_id,)
    ).fetchone()["n"] == 0

    # Still never written, through the save AND the revert.
    assert _sha256(path) == before
    assert path.read_bytes() == NATIVE_MUSICXML.encode("utf-8")


def test_a_native_scores_edit_survives_an_export_and_import(
    client, library, add_native_score, tmp_path, monkeypatch
):
    score_id = add_native_score()
    client.put(f"/api/scores/{score_id}/transcription", json={"content": EDITED_MUSICXML})
    expected = client.get(f"/api/scores/{score_id}/transcription").json()
    assert expected["source"] == "edited"

    export_resp = client.get("/api/export")
    assert export_resp.status_code == 200, export_resp.text
    archive = export_resp.content
    # The row really is IN the archive, not merely re-derivable at import time -
    # read out of the manifest rather than inferred from the import succeeding.
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    carried = [
        t for t in manifest["tables"]["transcriptions"] if t["source"] == "edited"
    ]
    assert len(carried) == 1
    assert carried[0]["format"] == "musicxml"
    assert "<fret>5</fret>" in carried[0]["content"]

    _switch_to_a_fresh_environment(monkeypatch, tmp_path, "target")
    assert client.get("/api/scores").json() == []

    # dry_run=false, explicitly: the route defaults to a dry run, and an import
    # asserted against a dry run would be asserting against nothing at all.
    import_resp = client.post(
        "/api/import",
        params={"dry_run": "false"},
        files={"file": ("export.zip", archive, "application/zip")},
    )
    assert import_resp.status_code == 200, import_resp.text
    summary = import_resp.json()
    assert summary["dry_run"] is False
    assert summary["transcriptions_imported"] == 1

    scores = client.get("/api/scores").json()
    assert len(scores) == 1
    imported = scores[0]
    assert imported["file_type"] == "musicxml"
    assert imported["has_transcription"] is True

    actual = client.get(f"/api/scores/{imported['id']}/transcription").json()
    assert actual["source"] == "edited"
    assert actual["format"] == "musicxml"
    assert actual["content"] == expected["content"]
    assert "<fret>5</fret>" in actual["content"]

    # And the file that came across is the ORIGINAL, unedited one - the archive
    # carries the library file and the edit as two separate things, which is
    # the whole shape of this feature.
    landed = client.get(f"/api/scores/{imported['id']}/file")
    assert landed.status_code == 200
    assert landed.content.decode("utf-8") == NATIVE_MUSICXML
