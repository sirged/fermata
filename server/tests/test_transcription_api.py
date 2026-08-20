import pytest
from fastapi import HTTPException

from fermata import api, db


def test_edited_transcription_survives_re_extraction(app_env, zanarkand_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)

    first = api.transcribe(score_id, body=None)
    assert first["source"] == "extracted"
    assert first["bars"] > 0
    assert first["notes"] > 0

    edited_content = '\\title "hand edited"\n.\n:4 0.1 |'
    edited = api.save_transcription(score_id, api.TranscriptionEditIn(content=edited_content))
    assert edited["source"] == "edited"
    assert edited["content"] == edited_content

    current = api.get_transcription(score_id)
    assert current["source"] == "edited"
    assert current["content"] == edited_content

    # Re-running extraction must update the extracted row only, never the
    # edited one - this is the whole point of keeping them as separate rows.
    second = api.transcribe(score_id, body=None)
    assert second["source"] == "extracted"

    still_current = api.get_transcription(score_id)
    assert still_current["source"] == "edited"
    assert still_current["content"] == edited_content

    rows = conn.execute(
        "SELECT source FROM transcriptions WHERE score_id = ? ORDER BY source", (score_id,)
    ).fetchall()
    assert {r["source"] for r in rows} == {"edited", "extracted"}


def test_get_transcription_404_when_none_exists(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "nope.pdf")
    with pytest.raises(HTTPException) as exc_info:
        api.get_transcription(score_id)
    assert exc_info.value.status_code == 404


def test_has_transcription_flag_is_batched_and_accurate(app_env, zanarkand_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)

    before = api.get_score(score_id)
    assert before["has_transcription"] is False

    api.transcribe(score_id, body=None)

    after = api.get_score(score_id)
    assert after["has_transcription"] is True

    listed = {row["id"]: row["has_transcription"] for row in api.list_scores()}
    assert listed[score_id] is True


def test_transcribe_rejects_non_extractable_pdf(app_env, tarrega_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", tarrega_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, tarrega_pdf.name)
    with pytest.raises(HTTPException) as exc_info:
        api.transcribe(score_id, body=None)
    assert exc_info.value.status_code == 422


def test_transcription_analysis_endpoint(app_env, zanarkand_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)
    analysis = api.get_transcription_analysis(score_id)
    assert analysis["extractable"] is True
    assert analysis["tab_staff_count"] >= 5


@pytest.mark.parametrize(
    "time_signature",
    [
        (0, 4),  # numerator below 1
        (33, 4),  # numerator above 32
        (4, 0),  # zero denominator
        (4, 3),  # denominator not a power of two
        (4, 6),  # denominator not a power of two
    ],
)
def test_transcribe_rejects_invalid_time_signature(
    app_env, zanarkand_pdf, monkeypatch, insert_score, time_signature
):
    """[0, 4] used to be accepted straight through to \\ts 0 4, which
    alphaTab rejects and which also zeroed the per-measure quarter-note
    budget so every duration snapped to :32."""
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)
    with pytest.raises(HTTPException) as exc_info:
        api.transcribe(score_id, body=api.TranscribeIn(time_signature=time_signature))
    assert exc_info.value.status_code == 422


def test_transcribe_accepts_valid_time_signature(app_env, zanarkand_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)
    result = api.transcribe(score_id, body=api.TranscribeIn(time_signature=(6, 8)))
    assert result["source"] == "extracted"
    assert result["time_signature"] == [6, 8]


def test_delete_transcription_reverts_to_extracted(app_env, zanarkand_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)

    api.transcribe(score_id, body=None)
    edited_content = '\\title "hand edited"\n.\n:4 0.1 |'
    api.save_transcription(score_id, api.TranscriptionEditIn(content=edited_content))

    current = api.get_transcription(score_id)
    assert current["source"] == "edited"

    # Deleting the edit must revert GET to the extracted transcription, not
    # remove the extracted row too.
    after_delete = api.delete_transcription(score_id)
    assert after_delete["source"] == "extracted"

    reverted = api.get_transcription(score_id)
    assert reverted["source"] == "extracted"
    assert reverted["content"] != edited_content

    rows = conn.execute(
        "SELECT source FROM transcriptions WHERE score_id = ?", (score_id,)
    ).fetchall()
    assert {r["source"] for r in rows} == {"extracted"}

    # A second delete finds no edited row to remove - that's a clean no-op
    # (the extracted transcription is still returned), not an error.
    again = api.delete_transcription(score_id)
    assert again["source"] == "extracted"


def test_delete_transcription_404_when_none_exists(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "nope.pdf")
    with pytest.raises(HTTPException) as exc_info:
        api.delete_transcription(score_id)
    assert exc_info.value.status_code == 404

    # Calling it again on a score that has genuinely never had any
    # transcription is still a clean 404, not a crash.
    with pytest.raises(HTTPException) as exc_info2:
        api.delete_transcription(score_id)
    assert exc_info2.value.status_code == 404


# ---------------------------------------------------------------------------
# The stored format
# ---------------------------------------------------------------------------


def test_extraction_is_stored_as_musicxml(app_env, zanarkand_pdf, monkeypatch, insert_score):
    """MusicXML is the canonical stored format. The row carries its own format
    rather than the reader assuming one, which is what lets this change land
    without a data migration."""
    import xml.etree.ElementTree as ET

    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)

    result = api.transcribe(score_id, body=None)
    assert result["format"] == "musicxml"
    root = ET.fromstring(result["content"])
    assert root.tag == "score-partwise"
    assert root.get("version") == "4.0"
    assert result["key_signature_source"] == "glyph-decoded"

    fetched = api.get_transcription(score_id)
    assert fetched["format"] == "musicxml"
    assert fetched["content"] == result["content"]


def test_an_alphatex_edit_is_stored_and_returned_as_alphatex(
    app_env, zanarkand_pdf, monkeypatch, insert_score
):
    """A row written in one format must keep saying so. The renderer dispatches
    on it, so a hand edit relabelled musicxml would simply fail to load."""
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)
    api.transcribe(score_id, body=None)

    tex = '\title "hand edited"\n.\n:4 0.1 |'
    edited = api.save_transcription(score_id, api.TranscriptionEditIn(content=tex))
    assert edited["format"] == "alphatex"
    assert api.get_transcription(score_id)["format"] == "alphatex"
    # the extracted row underneath is untouched and still MusicXML
    row = conn.execute(
        "SELECT format FROM transcriptions WHERE score_id = ? AND source = 'extracted'",
        (score_id,),
    ).fetchone()
    assert row["format"] == "musicxml"


def test_an_edit_format_is_sniffed_when_the_client_does_not_say(app_env, insert_score):
    """A client that does not send `format` gets it read off the content rather
    than assumed, because assuming wrong makes the transcription unrenderable."""
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<score-partwise version="4.0"/>'
    saved = api.save_transcription(score_id, api.TranscriptionEditIn(content=xml))
    assert saved["format"] == "musicxml"
    tex = ":4 0.1 |"
    saved = api.save_transcription(score_id, api.TranscriptionEditIn(content=tex))
    assert saved["format"] == "alphatex"


def test_an_explicit_edit_format_wins_over_the_sniff(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")
    saved = api.save_transcription(
        score_id, api.TranscriptionEditIn(content=":4 0.1 |", format="musicxml"))
    assert saved["format"] == "musicxml"


def test_an_unknown_edit_format_is_rejected(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")
    with pytest.raises(HTTPException) as exc_info:
        api.save_transcription(
            score_id, api.TranscriptionEditIn(content=":4 0.1 |", format="lilypond"))
    assert exc_info.value.status_code == 422
