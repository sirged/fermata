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
