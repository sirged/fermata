"""The transcription endpoints.

These are about the API's own behaviour - which row is written, what format
it claims, what a reload returns - so they run against committed engraved
fixtures rather than a score from the maintainer's library. They used to need
that library and therefore skipped in CI, which meant nothing outside a
developer's machine checked that transcribing a score stored anything at all.

The one thing an engraved fixture cannot stand in for is real Finale or
Sibelius output, so the end-to-end test at the bottom of this module still
runs against the library and still skips without it.
"""
import json

import pytest
from fastapi import HTTPException

from fermata import api, db


def test_edited_transcription_survives_re_extraction(app_env, extractable_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

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


def test_how_the_meter_key_and_tuning_were_obtained_survives_a_reload(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    """Whether each of those three was READ or ASSUMED has to outlive the
    response that extracted it.

    It did not. The extractor works the distinction out, POST /transcribe
    echoed it, and every later read of the same row - which is every ordinary
    visit to a score - handed back the assumed value with nothing saying it was
    assumed. An interface cannot show what it is never told, and this is the
    half of issue #103 that had to move on the server for the other half to be
    possible at all.

    Reading them back through GET and comparing against POST is the point: it
    fails if the keys are echoed but not stored, which is exactly the state
    this was in.
    """
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

    posted = api.transcribe(score_id, body=None)
    # The fixture's meter and key really are decoded off the engraving, so
    # "read" is the true answer here and a test that only ever saw "assumed"
    # could not tell the two apart.
    assert posted["time_signature_source"] == "glyph-decoded"
    assert posted["time_signature"] == [4, 4]
    assert posted["key_signature_source"] == "glyph-decoded"
    # Nothing labelled a tuning, which is the assumption #80 is about - the
    # standard six strings, recorded as what was actually used.
    assert posted["tuning_label"] is None
    assert posted["tuning"] == ["E2", "A2", "D3", "G3", "B3", "E4"]

    fetched = api.get_transcription(score_id)
    for key in (
        "time_signature",
        "time_signature_source",
        "key_fifths",
        "key_signature_source",
        "tuning",
        "tuning_label",
    ):
        assert fetched[key] == posted[key], key

    # A hand edit measures nothing, so it states "not recorded" rather than
    # keeping the extraction's answer - the same rule the bar counts follow,
    # and for the same reason: a client that merges this response over the one
    # it already holds must not go on reporting provenance for content that has
    # been replaced.
    edited = api.save_transcription(
        score_id, api.TranscriptionEditIn(content='\\title "hand edited"\n.\n:4 0.1 |')
    )
    for key in ("time_signature", "time_signature_source", "tuning", "tuning_label"):
        assert edited[key] is None, key


def test_get_transcription_404_when_none_exists(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "nope.pdf")
    with pytest.raises(HTTPException) as exc_info:
        api.get_transcription(score_id)
    assert exc_info.value.status_code == 404


def test_has_transcription_flag_is_batched_and_accurate(app_env, extractable_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

    before = api.get_score(score_id)
    assert before["has_transcription"] is False

    api.transcribe(score_id, body=None)

    after = api.get_score(score_id)
    assert after["has_transcription"] is True

    listed = {row["id"]: row["has_transcription"] for row in api.list_scores()}
    assert listed[score_id] is True


def test_transcribe_rejects_non_extractable_pdf(app_env, non_extractable_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", non_extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, non_extractable_pdf.name)
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
    app_env, extractable_pdf, monkeypatch, insert_score, time_signature
):
    """[0, 4] used to be accepted straight through to \\ts 0 4, which
    alphaTab rejects and which also zeroed the per-measure quarter-note
    budget so every duration snapped to :32."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
    with pytest.raises(HTTPException) as exc_info:
        api.transcribe(score_id, body=api.TranscribeIn(time_signature=time_signature))
    assert exc_info.value.status_code == 422


def test_transcribe_accepts_valid_time_signature(app_env, extractable_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
    result = api.transcribe(score_id, body=api.TranscribeIn(time_signature=(6, 8)))
    assert result["source"] == "extracted"
    assert result["time_signature"] == [6, 8]


def test_delete_transcription_reverts_to_extracted(app_env, extractable_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

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


def test_extraction_is_stored_as_musicxml(app_env, extractable_pdf, monkeypatch, insert_score):
    """MusicXML is the canonical stored format. The row carries its own format
    rather than the reader assuming one, which is what lets this change land
    without a data migration."""
    import xml.etree.ElementTree as ET

    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

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
    app_env, extractable_pdf, monkeypatch, insert_score
):
    """A row written in one format must keep saying so. The renderer dispatches
    on it, so a hand edit relabelled musicxml would simply fail to load."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)
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


def test_pasting_alphatex_over_a_musicxml_row_stores_it_as_alphatex(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    """The format of an edit is decided by what was TYPED, not by the format of
    the row it replaces. Storing the loaded row's format meant a user who
    pasted alphaTex into the source editor of a MusicXML transcription got a
    row labelled musicxml; the viewer dispatched on that label, handed alphaTex
    to the MusicXML loader, and the staff never appeared."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

    extracted = api.transcribe(score_id, body=None)
    assert extracted["format"] == "musicxml"

    tex = '\title "hand edited"\n.\n:4 0.1 |'
    saved = api.save_transcription(score_id, api.TranscriptionEditIn(content=tex))
    assert saved["format"] == "alphatex"
    assert api.get_transcription(score_id)["format"] == "alphatex"

    # and the reverse: MusicXML pasted over an alphatex row
    xml = '<?xml version="1.0"?>\n<score-partwise version="4.0"/>'
    saved = api.save_transcription(score_id, api.TranscriptionEditIn(content=xml))
    assert saved["format"] == "musicxml"


@pytest.mark.parametrize("content,expected", [
    ('<?xml version="1.0"?><score-partwise/>', "musicxml"),
    ("<score-partwise version=\"4.0\"/>", "musicxml"),
    ("<!DOCTYPE score-partwise><score-partwise/>", "musicxml"),
    ("<!-- a comment first --><score-partwise/>", "musicxml"),
    ("\n\n  <score-partwise/>", "musicxml"),
    ('\title "x"\n.\n:4 0.1 |', "alphatex"),
    (":4 0.1 |", "alphatex"),
    ("\tempo 88\n.\n:8 3.4{d} |", "alphatex"),
])
def test_edit_format_is_read_off_the_content(content, expected):
    """alphaTex has no form that begins with '<' - metadata lines begin with a
    backslash and beats with a colon or a fret number - so the leading
    character settles it."""
    assert api._sniff_transcription_format(content) == expected


# ---------------------------------------------------------------------------
# Rule 8 conformance survives a reload
# ---------------------------------------------------------------------------


BAR_KEYS = ("bars_overfull", "bars_short", "bars_defective", "bars_measured",
            "bars_padded", "bars_unread")
# Which bars, and how much silence - the figures that only exist as data. The
# warning prose caps its bar list, and the profile document promises a consumer
# that `inferred_rest_quarters` is the sum of the `<forward>` durations in the
# file, so the application has to actually offer the number.
BAR_DETAIL_KEYS = ("padded_bars", "unread_bars", "inferred_rest_quarters")


def test_bar_conformance_survives_a_reload(app_env, engraved, monkeypatch, insert_score):
    """A reloaded transcription must report the same bar figures the extraction
    did. They are not derivable from the warning prose - a bar wrong in both
    directions at once counts into overfull AND short, so their sum can exceed
    the bars measured - which means a client without these numbers can only
    guess. It must not have to.

    Deliberately run against the score whose bars do NOT add up: on a clean
    score every figure but `bars_measured` is zero, and a persistence bug that
    dropped them all would have looked identical to success."""
    pdf = engraved("defective_bars")
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    fetched = api.get_transcription(score_id)

    for key in BAR_KEYS:
        assert posted[key] == fetched[key], key
    assert fetched["bars_measured"] > 0
    assert fetched["bars_overfull"] > 0, "this fixture has bars over their meter"
    assert fetched["bars_short"] > 0, "...and bars under it"
    assert fetched["bars_padded"] > 0, "...and bars filled out with inferred silence"
    for key in BAR_DETAIL_KEYS:
        assert posted[key] == fetched[key], key
    assert fetched["padded_bars"] and all(
        isinstance(n, int) for n in fetched["padded_bars"])
    assert len(fetched["padded_bars"]) == fetched["bars_padded"]
    assert fetched["unread_bars"] == []
    assert fetched["inferred_rest_quarters"] > 0
    # The warning prose names the same bars the data does, so a reader of either
    # gets the same answer - and the number in it is the exact one.
    padded_warning = next(w for w in fetched["warnings"]
                          if "deduced from the time signature" in w)
    named = padded_warning.split("The bars are: ")[1].split(".")[0]
    assert [int(n) for n in named.split(", ")] == fetched["padded_bars"]
    # The invariant that makes `defective` the only figure comparable against
    # the total: it never exceeds the bars measured, whereas overfull + short
    # may. A client comparing the wrong pair can print "13 of 12 bars".
    assert fetched["bars_defective"] <= fetched["bars_measured"]
    assert max(fetched["bars_overfull"], fetched["bars_short"]) <= fetched["bars_defective"]
    assert fetched["warnings"] == posted["warnings"]


def test_a_row_stored_before_the_bar_figures_reports_them_unrecorded(app_env, insert_score):
    """Rows written before these were persisted carry a blob without them.
    Unrecorded must read as None, not as zero: zero would claim every bar was
    measured and every one of them added up, which is a stronger statement
    than the row can support."""
    conn = db.connect()
    score_id = insert_score(conn, "legacy.pdf")
    conn.execute(
        """INSERT INTO transcriptions(score_id, format, content, source, confidence, updated_at)
           VALUES (?, 'alphatex', ':4 0.1 |', 'extracted', ?, datetime('now'))""",
        (score_id, json.dumps({"warnings": ["something was odd"], "confidence": {"rhythm": "low"}})),
    )
    conn.commit()

    fetched = api.get_transcription(score_id)
    assert fetched["warnings"] == ["something was odd"]
    for key in BAR_KEYS + BAR_DETAIL_KEYS:
        assert key in fetched, key
        assert fetched[key] is None, key


def test_an_edit_states_that_nothing_measured_it(app_env, extractable_pdf, monkeypatch, insert_score):
    """A hand edit replaces the content the figures describe, so it has none of
    its own - and it must SAY so rather than leave the keys out.

    A client that merges this response over the transcription it already held
    would keep the pre-edit figures if they were merely absent, and go on
    reporting bars as defective after the edit that fixed them. Carrying the
    extraction's figures forward would be worse still: it would assert
    measurements of content that nothing has measured."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

    extracted = api.transcribe(score_id, body=None)
    assert extracted["bars_measured"] > 0
    assert extracted["warnings"]

    edited = api.save_transcription(
        score_id, api.TranscriptionEditIn(content=":4 0.1 |", format="alphatex")
    )
    for state in (edited, api.get_transcription(score_id)):
        assert state["source"] == "edited"
        assert state["warnings"] == []
        for key in BAR_KEYS + BAR_DETAIL_KEYS:
            assert key in state, key
            assert state[key] is None, key

    # Reverting restores the extracted row, and with it the real figures.
    api.delete_transcription(score_id)
    reverted = api.get_transcription(score_id)
    assert reverted["bars_measured"] == extracted["bars_measured"]
    assert reverted["bars_defective"] == extracted["bars_defective"]


def test_a_corrupt_blob_does_not_yield_a_bar_count(app_env, insert_score):
    """The figures are only ever numbers. A blob carrying something else in
    those keys must read as unrecorded rather than passing a string or a bool
    through to a caller that will do arithmetic on it."""
    conn = db.connect()
    score_id = insert_score(conn, "odd.pdf")
    conn.execute(
        """INSERT INTO transcriptions(score_id, format, content, source, confidence, updated_at)
           VALUES (?, 'alphatex', ':4 0.1 |', 'extracted', ?, datetime('now'))""",
        (
            score_id,
            json.dumps(
                {
                    "warnings": "not a list",
                    "bars_measured": "twelve",
                    "bars_defective": None,
                    "bars_overfull": True,
                    "bars_short": 2,
                    "padded_bars": [1, "two", 3],
                    "unread_bars": [4, 5],
                    "inferred_rest_quarters": "a lot",
                }
            ),
        ),
    )
    conn.commit()

    fetched = api.get_transcription(score_id)
    assert fetched["warnings"] == []
    assert fetched["bars_measured"] is None
    assert fetched["bars_defective"] is None
    assert fetched["bars_overfull"] is None, "a bool is not a bar count"
    assert fetched["bars_short"] == 2
    assert fetched["padded_bars"] is None, "a list with a string in it is not bar numbers"
    assert fetched["unread_bars"] == [4, 5]
    assert fetched["inferred_rest_quarters"] is None, "a quarter count is a number"


# ---------------------------------------------------------------------------
# End to end on real engraving (needs FERMATA_TEST_LIBRARY)
# ---------------------------------------------------------------------------


def test_the_api_transcribes_a_real_engraved_score(
    app_env, zanarkand_pdf, monkeypatch, insert_score
):
    """The whole path on material this project did not generate: a real
    Finale export, decoded through the Maestro glyph-ID fingerprint that no
    committed fixture can reach, stored, and read back.

    Everything above this line now runs in CI against engraved fixtures.
    This one cannot, and it is the reason the library fixtures stay."""
    monkeypatch.setattr(api, "LIBRARY_DIR", zanarkand_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, zanarkand_pdf.name)

    posted = api.transcribe(score_id, body=None)
    assert posted["source"] == "extracted"
    assert posted["format"] == "musicxml"
    assert posted["bars"] > 0
    assert posted["notes"] > 0
    assert posted["key_signature_source"] == "glyph-decoded"
    assert posted["bars_measured"] == posted["bars"]

    fetched = api.get_transcription(score_id)
    assert fetched["content"] == posted["content"]
    assert fetched["warnings"] == posted["warnings"]
