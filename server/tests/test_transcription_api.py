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
import ast
import inspect
import json
import os
import textwrap

import pytest
from fastapi import HTTPException

from fermata import api, db

# A schema-valid minimal MusicXML document, used anywhere a test needs content
# that sniffs (or is declared) as "musicxml" and must therefore survive
# save_transcription's XSD check (#188) when FERMATA_MUSICXML_XSD is set -
# which CI's "Fetch the MusicXML schema" step does for the whole test job, not
# just tests/test_musicxml.py. `<score-partwise version="4.0"/>` alone is NOT
# schema-valid (part-list is required), so it is a trap here specifically.
MINIMAL_VALID_MUSICXML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Music</part-name></score-part></part-list>'
    '<part id="P1"><measure number="1"/></part>'
    "</score-partwise>"
)


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
    # standard six strings, recorded as what was actually used - and no printed
    # tuning instruction this fixture leaves unread, so nothing may be said
    # about its tuning at all.
    assert posted["tuning_label"] is None
    assert posted["tuning"] == ["E2", "A2", "D3", "G3", "B3", "E4"]
    assert posted["tuning_unread"] == []
    # And it is recorded as ASSUMED, not read (issue #80): the standard strings
    # here are a default nobody read off the page, and the word that says so has
    # to survive the reload the same way the meter's source does.
    assert posted["tuning_source"] == "assumed standard"

    fetched = api.get_transcription(score_id)
    for key in (
        "time_signature",
        "time_signature_source",
        "key_fifths",
        "key_signature_source",
        "tuning",
        "tuning_label",
        "tuning_source",
        "tuning_unread",
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
    for key in (
        "time_signature",
        "time_signature_source",
        "tuning",
        "tuning_label",
        "tuning_source",
        "tuning_unread",
    ):
        assert edited[key] is None, key


def test_an_assigned_instruments_tuning_is_used_over_the_page_and_survives_reload(
    app_env, engraved, monkeypatch, insert_score
):
    """Issue #72: scores.instrument_id was recorded and read by nobody, so a
    score was transcribed as a standard six-string whatever instrument it named.
    Here the drop_d fixture - which prints "Drop D" and would read that off the
    page on its own - is assigned a DADGAD instrument. The transcribe endpoint
    has to sound the frets against the instrument, mark the tuning as coming
    from it, and keep that answer on every later read."""
    drop_d = engraved("drop_d")
    monkeypatch.setattr(api, "LIBRARY_DIR", drop_d.parent)
    conn = db.connect()
    score_id = insert_score(conn, drop_d.name)

    instrument = api.create_instrument(api.InstrumentIn(
        name="My DADGAD guitar",
        fretted=True,
        string_count=6,
        string_pitches=["D2", "A2", "D3", "G3", "A3", "D4"],
        fret_count=22,
    ))
    conn.execute("UPDATE scores SET instrument_id = ? WHERE id = ?",
                 (instrument["id"], score_id))
    conn.commit()

    posted = api.transcribe(score_id, body=None)
    assert posted["tuning"] == ["D2", "A2", "D3", "G3", "A3", "D4"]
    assert posted["tuning_source"] == "instrument"
    # The instrument won; the page's own printed Drop D name is not adopted.
    assert posted["tuning_label"] is None

    fetched = api.get_transcription(score_id)
    assert fetched["tuning"] == posted["tuning"]
    assert fetched["tuning_source"] == "instrument"


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


def test_transcription_analysis_endpoint(app_env, score_a_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", score_a_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, score_a_pdf.name)
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
    saved = api.save_transcription(
        score_id, api.TranscriptionEditIn(content=MINIMAL_VALID_MUSICXML))
    assert saved["format"] == "musicxml"
    tex = ":4 0.1 |"
    saved = api.save_transcription(score_id, api.TranscriptionEditIn(content=tex))
    assert saved["format"] == "alphatex"


def test_an_explicit_edit_format_wins_over_the_sniff(app_env, insert_score):
    """The client's stated format overrides what the content would sniff to -
    content that starts with '<' and would otherwise sniff as musicxml is
    stored as alphatex when the client says so, not reinterpreted. (Content
    that sniffs the OTHER way and is forced to musicxml is exercised in
    test_pasting_alphatex_over_a_musicxml_row_stores_it_as_alphatex, which
    uses real MusicXML so it also clears the #188 schema check below.)"""
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")
    saved = api.save_transcription(
        score_id, api.TranscriptionEditIn(content="<not actually xml", format="alphatex"))
    assert saved["format"] == "alphatex"


def test_an_unknown_edit_format_is_rejected(app_env, insert_score):
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")
    with pytest.raises(HTTPException) as exc_info:
        api.save_transcription(
            score_id, api.TranscriptionEditIn(content=":4 0.1 |", format="lilypond"))
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# Server-side MusicXML validation of a hand edit (#188)
# ---------------------------------------------------------------------------
#
# The note editor already guards Rule 11 (octaves 0-9, among other things)
# client-side before a save is ever sent - this is belt-and-suspenders against
# a hand-crafted PUT that skips the editor entirely. Validating needs the real
# MusicXML 4.0 schema at runtime, which the repository does not carry (see
# config.MUSICXML_XSD's comment), so - exactly like
# tests/test_musicxml.py's test_validates_against_xsd - the test that proves
# the 422 skips unless FERMATA_MUSICXML_XSD names a local copy. The test that
# proves an unconfigured deployment is UNCHANGED needs no schema and never
# skips: that is the default-runtime behaviour target.

# A note whose octave is 10 - out of the schema's 0-9 range (issue #188's own
# example, and the same Rule 11 case #10 the note editor already refuses
# client-side) - inside an otherwise well-formed, schema-shaped document, so
# what fails validation is specifically the octave, not a missing part-list
# or the like.
INVALID_MUSICXML_OUT_OF_RANGE_OCTAVE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Music</part-name></score-part></part-list>'
    '<part id="P1"><measure number="1">'
    "<note><pitch><step>C</step><octave>10</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure></part>"
    "</score-partwise>"
)


def test_an_invalid_musicxml_edit_is_rejected_when_a_schema_is_configured(
    app_env, insert_score, monkeypatch
):
    """A hand-crafted PUT carrying an out-of-range octave is refused with 422
    and nothing is stored - the prior state (nothing) is unchanged - but only
    once FERMATA_MUSICXML_XSD actually names a schema to check against."""
    path = os.environ.get("FERMATA_MUSICXML_XSD")
    if not path or not os.path.isfile(path):
        pytest.skip("FERMATA_MUSICXML_XSD not set to a musicxml.xsd")
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")

    with pytest.raises(HTTPException) as exc_info:
        api.save_transcription(
            score_id, api.TranscriptionEditIn(content=INVALID_MUSICXML_OUT_OF_RANGE_OCTAVE))
    assert exc_info.value.status_code == 422

    row = conn.execute(
        "SELECT * FROM transcriptions WHERE score_id = ? AND source = 'edited'", (score_id,)
    ).fetchone()
    assert row is None


def test_a_valid_musicxml_edit_is_stored_when_a_schema_is_configured(app_env, insert_score):
    """The counterpart to the rejection test above: a well-formed, schema-valid
    edit is still accepted (and stored) once FERMATA_MUSICXML_XSD is set -
    the check does not merely reject everything."""
    path = os.environ.get("FERMATA_MUSICXML_XSD")
    if not path or not os.path.isfile(path):
        pytest.skip("FERMATA_MUSICXML_XSD not set to a musicxml.xsd")
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")

    saved = api.save_transcription(
        score_id, api.TranscriptionEditIn(content=MINIMAL_VALID_MUSICXML))
    assert saved["format"] == "musicxml"
    row = conn.execute(
        "SELECT * FROM transcriptions WHERE score_id = ? AND source = 'edited'", (score_id,)
    ).fetchone()
    assert row is not None


def test_an_invalid_musicxml_edit_is_accepted_when_no_schema_is_configured(
    app_env, insert_score, monkeypatch
):
    """The default-runtime target: with FERMATA_MUSICXML_XSD unset (or naming
    nothing readable), save_transcription behaves exactly as it did before
    #188 - the same out-of-range-octave body that a configured schema rejects
    above is accepted and stored here. Needs no schema, so it never skips."""
    monkeypatch.setattr(api.config, "MUSICXML_XSD", "")
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")

    saved = api.save_transcription(
        score_id, api.TranscriptionEditIn(content=INVALID_MUSICXML_OUT_OF_RANGE_OCTAVE))
    assert saved["format"] == "musicxml"
    row = conn.execute(
        "SELECT * FROM transcriptions WHERE score_id = ? AND source = 'edited'", (score_id,)
    ).fetchone()
    assert row is not None
    assert row["content"] == INVALID_MUSICXML_OUT_OF_RANGE_OCTAVE


def test_an_alphatex_edit_is_unaffected_by_the_schema_check(app_env, insert_score):
    """Only a MusicXML-formatted edit is ever run through the XSD - alphaTex
    has no schema to check against, so it must be unaffected either way. Uses
    whatever FERMATA_MUSICXML_XSD is (or is not) in this run rather than
    forcing one, because the point is that alphatex bypasses the check
    entirely - not that a schema happens to be present."""
    conn = db.connect()
    score_id = insert_score(conn, "x.pdf")
    saved = api.save_transcription(
        score_id, api.TranscriptionEditIn(content=":4 0.1 |", format="alphatex"))
    assert saved["format"] == "alphatex"


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
    saved = api.save_transcription(
        score_id, api.TranscriptionEditIn(content=MINIMAL_VALID_MUSICXML))
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
            "bars_padded", "bars_unread", "notes_no_stem", "staves_no_stem",
            # dots_unassigned / staves_dots_unassigned were added to the real
            # _BAR_KEYS in api.py alongside notes_no_stem/staves_no_stem, but
            # never added HERE - so nothing ever exercised their reload path.
            # dots_unassigned_no_candidate / dots_unassigned_eliminated split
            # dots_unassigned by why a dot went unbound (see
            # glyph._assign_dots) and belong in the same reload check.
            "dots_unassigned", "dots_unassigned_no_candidate",
            "dots_unassigned_eliminated", "staves_dots_unassigned",
            # Same gap, same fix, for the repeat/volta disclosure fields
            # (issue #134 adversarial review, blocker 3): they were added to
            # ExtractionResult and to_dict() but never to _BAR_KEYS in api.py
            # nor exercised HERE, so a reader of a reloaded transcription got
            # none of them even after api.py caught up.
            "repeats_unread", "endings_unread", "endings_truncated",
            "form_marks_unanchored", "endings_incomplete",
            # Same gap, same fix again, for issue #137's shared-unison digit:
            # added to _BAR_KEYS at the same time as ExtractionResult and
            # to_dict rather than after an adversarial review found it
            # missing, and exercised HERE so its reload path is real.
            "unison_digits_shared",
            # coincident_unsplit_pairs / staves_coincident_unsplit (issue
            # #116) were the opposite gap: reached ExtractionResult and
            # to_dict with #116 itself but never _BAR_KEYS in api.py nor
            # HERE (issue #143), so their reload path went unexercised until
            # now.
            "coincident_unsplit_pairs", "staves_coincident_unsplit",
            # And for issue #134 phase 2's navigation disclosures, added to
            # _BAR_KEYS and exercised here in the same change rather than
            # left for a later review to find missing.
            "nav_marks_unanchored", "nav_marks_unresolved",
            # And for issue #152's lost-system counter, in the same change
            # as _BAR_KEYS and the confidence_json write - the reload path
            # exercised here rather than assumed.
            "systems_unread",
            # staves_spacing_rhythm / staves_degraded_rhythm (issue #117):
            # the counts belonging to spacing_bars / degraded_bars below,
            # which have round-tripped since they existed while the counts
            # lived only in `rhythm_provenance` - a field this application
            # neither stores nor returns.
            "staves_spacing_rhythm", "staves_degraded_rhythm",
            # meter_digits_unreadable (issue #129): printed meters refused
            # because a glyph with no category sat among their digits.
            "meter_digits_unreadable",
            # tie_ends_unpaired (issue #81): an end of a tie whose other end
            # was not found, so the tie is not written. Added to _BAR_KEYS,
            # to the confidence_json write, to TranscriptionOut, to the
            # vendored key list and to DISCLOSURE_ROWS in the same change as
            # this line, rather than left for a later review to find missing
            # in one of the five.
            "tie_ends_unpaired")
# Which bars, and how much silence - the figures that only exist as data. The
# warning prose caps its bar list, and the profile document promises a consumer
# that `inferred_rest_quarters` is the sum of the `<forward>` durations in the
# file, so the application has to actually offer the number.
#
# `spacing_bars` / `degraded_bars` are here for the same reason: the staff
# counts in the confidence string say how much of a score's rhythm was not read
# from its glyphs, and only these say which of it.
BAR_DETAIL_KEYS = ("padded_bars", "unread_bars", "inferred_rest_quarters",
                   "spacing_bars", "degraded_bars",
                   "repeats_unread_bars", "endings_unread_bars",
                   "endings_truncated_bars", "form_marks_unanchored_bars",
                   "nav_marks_unresolved_bars",
                   # Pages, not bars - a system that was never read has no
                   # bar numbers to report (issue #152).
                   "systems_unread_pages",
                   # Which bars hold an end of a tie whose other end was not
                   # found (issue #81).
                   "tie_ends_unpaired_bars")


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


def test_which_bars_were_not_read_from_glyphs_survives_a_reload(
        app_env, engraved, monkeypatch, insert_score):
    """`bars_padded`'s round trip is asserted against a score where the figures
    are non-zero, on purpose - all-zeros would have looked identical to a
    persistence bug that dropped everything. The same has to hold for which
    bars' durations came out of the gaps between noteheads rather than the
    noteheads themselves, so this runs against a tab-only edition, where all
    twelve bars did.

    A staff count alone cannot answer "which of this is in question", and the
    confidence string is where the staff counts live - so if these lists do not
    survive the reload, every visit to the score after the extracting one is
    back to being unable to say."""
    pdf = engraved("tab_only")
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    fetched = api.get_transcription(score_id)

    assert posted["spacing_bars"] == list(range(1, 13))
    assert fetched["spacing_bars"] == posted["spacing_bars"]
    assert fetched["degraded_bars"] == []
    # ...and the COUNT beside the list, asserted as a LITERAL rather than
    # against the other side of the same stored row (issue #117, and the #146
    # lesson about what a `posted[key] == fetched[key]` loop cannot catch: an
    # unwritten field compares None == None and passes). This edition is
    # tab-only, so both of its staff systems had to guess their durations from
    # the horizontal gaps between noteheads.
    assert posted["staves_spacing_rhythm"] == 2, posted["staves_spacing_rhythm"]
    assert fetched["staves_spacing_rhythm"] == 2
    assert fetched["staves_degraded_rhythm"] == 0
    assert fetched["notes_no_stem"] == 0, "no notation staff here to read a stem off"
    assert fetched["staves_no_stem"] == 0
    assert fetched["dots_unassigned"] == 0, "no notation staff here to read a dot off either"
    assert fetched["dots_unassigned_no_candidate"] == 0
    assert fetched["dots_unassigned_eliminated"] == 0
    assert fetched["staves_dots_unassigned"] == 0


def test_a_coincident_unsplit_pair_survives_a_reload(app_env, score_o_pdf, monkeypatch,
                                                     insert_score):
    """coincident_unsplit_pairs / staves_coincident_unsplit (issue #116) round
    trip against a score where they are non-zero, for the same reason the bar
    figures above are: all-zeros looks identical to a persistence bug that
    dropped them. score_o reads 15 unsplit pairs across 4 staves (see
    test_a_coincident_pair_with_no_second_stem_is_disclosed_not_silently_doubled
    in test_tabextract.py) - reaching ExtractionResult and to_dict() with
    #116 itself, but neither _BAR_KEYS in api.py nor the confidence blob
    transcribe() writes ever picked them up (issue #143), so a reloaded
    transcription reported every other disclosure the decoder made except
    this one."""
    monkeypatch.setattr(api, "LIBRARY_DIR", score_o_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, score_o_pdf.name)

    posted = api.transcribe(score_id, body=None)
    fetched = api.get_transcription(score_id)

    assert posted["coincident_unsplit_pairs"] == 15
    assert posted["staves_coincident_unsplit"] == 4
    assert fetched["coincident_unsplit_pairs"] == posted["coincident_unsplit_pairs"]
    assert fetched["staves_coincident_unsplit"] == posted["staves_coincident_unsplit"]


def test_a_shared_unison_digit_survives_a_reload(app_env, engraved, monkeypatch, insert_score):
    """unison_digits_shared (issue #137) never reached the stored blob at all
    (issue #146): present on ExtractionResult, in to_dict() and in _BAR_KEYS
    above, but absent from the confidence_json dict literal transcribe()
    actually writes - the only path into storage. to_dict() is never called
    in server/, so its presence there carried no production weight.

    The BAR_KEYS loop in test_bar_conformance_survives_a_reload did not catch
    this: both sides of `posted[key] == fetched[key]` come from the same
    stored row, so an unwritten field compares None == None and passes. This
    instead asserts the LITERAL value a fresh extraction independently
    produces (see test_a_unison_inside_a_chord_sounds_in_both_voices in
    test_engraved_fixtures.py) against the post-storage read, on both the
    POST response and a later GET - the shape #145 used to catch the same
    gap for #116's coincident_unsplit_pairs/staves_coincident_unsplit pair.

    Ungated: `unison_in_chord.pdf` is a committed fixture, so this needs no
    FERMATA_TEST_LIBRARY and fails rather than skips if the fixture goes
    missing (see conftest.engraved_pdf)."""
    pdf = engraved("unison_in_chord")
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    fetched = api.get_transcription(score_id)

    assert posted["unison_digits_shared"] == 32
    assert fetched["unison_digits_shared"] == 32


def test_an_incompletely_read_staff_survives_a_reload(app_env, engraved, monkeypatch,
                                                      insert_score):
    """`staves_spacing_rhythm`'s sibling (issue #117), against the one
    committed fixture where it is non-zero rather than against a score where
    it is 0 and a dropped field would be indistinguishable from success.

    harmonics_dense has one notation staff the decoder read from the engraving
    with something on it left unread; the four bars that staff produced are
    already carried as `degraded_bars`, and until now the count they are a
    count OF lived only in `rhythm_provenance`, which nothing stores."""
    pdf = engraved("harmonics_dense")
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    fetched = api.get_transcription(score_id)

    assert posted["staves_degraded_rhythm"] == 1, posted["staves_degraded_rhythm"]
    assert fetched["staves_degraded_rhythm"] == 1
    assert fetched["degraded_bars"] == [1, 2, 3, 4]
    assert fetched["staves_spacing_rhythm"] == 0


def test_a_refused_meter_digit_survives_a_reload(app_env, engraved, monkeypatch, insert_score):
    """meter_digits_unreadable (issue #129) round trips against the one
    fixture where it is non-zero, and as a LITERAL - the #146 lesson again: a
    `posted[key] == fetched[key]` loop over a field nothing wrote compares
    None == None and passes.

    The fixture is `multidigit_meter` re-cut so the '2' of its 12/8 is a SMuFL
    codepoint the decoder has no category for - what a Finale subset with an
    unmapped GID looks like from this side. Before the refusal, the '1' and
    the '8' assembled among themselves and the score reported a confident
    `(1, 8)` at `time_signature_source` "glyph-decoded"; both halves of that
    are asserted here, because the counter is only worth storing if the meter
    it qualifies actually declined."""
    pdf = engraved("unmapped_meter_digit")
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    fetched = api.get_transcription(score_id)

    assert posted["meter_digits_unreadable"] == 1, posted["meter_digits_unreadable"]
    assert fetched["meter_digits_unreadable"] == 1
    # The refusal itself, not just its count: no confident partial meter.
    assert fetched["time_signature"] != [1, 8], "the mis-read issue #129 is about"
    assert fetched["time_signature_source"].startswith("not detected")
    assert any("unrecognised glyph sits among the time-signature digits" in w
               for w in fetched["warnings"]), fetched["warnings"]


def test_an_unpaired_tie_end_survives_a_reload(app_env, score_p_pdf, monkeypatch,
                                               insert_score):
    """`tie_ends_unpaired` (issue #81) round trips as a LITERAL non-zero, for
    the reason the two tests above give: a `posted[key] == fetched[key]` loop
    over a field nothing wrote compares None == None and passes.

    It needs a real score because nothing engraved in this repository has a
    HALF-matched tie - `tuplet_and_tie`'s split one is matched at neither end,
    which counts nothing - so every committed fixture reports zero, and a
    counter only ever asserted at zero cannot tell a working round trip from a
    dropped field. score_p writes 6 complete ties and leaves 4 ends
    it could not close, in bars this asserts by number, and marks 19 harmonics
    beside them."""
    monkeypatch.setattr(api, "LIBRARY_DIR", score_p_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, score_p_pdf.name)

    posted = api.transcribe(score_id, body=None)
    fetched = api.get_transcription(score_id)

    assert posted["tie_ends_unpaired"] == 4, posted["tie_ends_unpaired"]
    assert fetched["tie_ends_unpaired"] == 4
    assert fetched["tie_ends_unpaired_bars"] == [19, 22, 24, 28]
    # The ties that WERE written are in the file itself, which is where a
    # reader gets them from - there is no counter for those and there should
    # not be one.
    assert fetched["content"].count("<tied ") == 12, "six ties, two ends each"
    assert fetched["content"].count("<tie ") == 12
    assert fetched["content"].count("<harmonic") == 19
    assert any("end(s) of a tie were found in the engraving" in w
               for w in fetched["warnings"]), fetched["warnings"]


def _confidence_json_keys() -> set:
    """The set of keys `_store_extraction_result()` actually writes into the
    stored blob - found by parsing the function's own source rather than
    duplicating the dict by hand, which would be exactly the kind of second
    copy that could drift from the first the way _BAR_KEYS and this dict
    already have three times (#134's fields, #116's pair via #143, #137's
    counter via #146).

    Locates the `confidence_json = json.dumps({...})` assignment inside
    _store_extraction_result() - the one place BOTH transcribe() and the
    issue #55 bulk pass write a transcription's confidence blob, see that
    function's own docstring - and returns the literal string keys of that
    dict."""
    source = inspect.getsource(api._store_extraction_result)
    # inspect.getsource returns the function starting at its own indentation
    # (possibly non-zero, e.g. if decorated or nested); dedent so ast.parse,
    # which requires a module-level indent of zero, does not choke on it.
    tree = ast.parse(textwrap.dedent(source))
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef), (
        "expected _store_extraction_result() to parse as one function")

    for node in ast.walk(func_def):
        if not (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "confidence_json"):
            continue
        call = node.value
        assert isinstance(call, ast.Call) and call.args, (
            "_store_extraction_result() no longer assigns "
            "confidence_json = json.dumps({...}) - update _confidence_json_keys to match "
            "its new shape")
        dict_node = call.args[0]
        assert isinstance(dict_node, ast.Dict), (
            "confidence_json's argument is no longer a dict literal - "
            "update _confidence_json_keys to match its new shape")
        keys = set()
        for key_node in dict_node.keys:
            assert isinstance(key_node, ast.Constant) and isinstance(key_node.value, str), (
                "confidence_json has a non-literal-string key - "
                "update _confidence_json_keys to match its new shape")
            keys.add(key_node.value)
        return keys

    raise AssertionError(
        "_store_extraction_result() no longer assigns a local named confidence_json - "
        "update _confidence_json_keys to match its new shape")


def test_every_bar_key_reaches_the_stored_blob():
    """The structural guard issue #146 asked for, closing the bug class
    rather than only this one instance of it. #146 was exactly this gap for
    one field: `unison_digits_shared` sat in _BAR_KEYS - the set
    _transcription_dict() reads back out of a stored blob - without a
    matching write line in the confidence_json dict transcribe() writes INTO
    that blob, so it round-tripped as None everywhere. This is the third
    field to go missing that way (#134's fields, #116's pair via #143, now
    #137's counter via #146), each caught only after it shipped.

    Asserting the two sets match here means the NEXT field added to
    _BAR_KEYS without a matching write line fails this test immediately,
    naming the missing key, instead of silently reading back as None until
    someone notices in production."""
    written = _confidence_json_keys()
    missing = set(api._BAR_KEYS) - written
    assert not missing, (
        f"_BAR_KEYS names a key transcribe() never writes into confidence_json: "
        f"{sorted(missing)}"
    )


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
    app_env, score_a_pdf, monkeypatch, insert_score
):
    """The whole path on material this project did not generate: a real
    Finale export, decoded through the Maestro glyph-ID fingerprint that no
    committed fixture can reach, stored, and read back.

    Everything above this line now runs in CI against engraved fixtures.
    This one cannot, and it is the reason the library fixtures stay."""
    monkeypatch.setattr(api, "LIBRARY_DIR", score_a_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, score_a_pdf.name)

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


def test_floored_note_durations_survive_the_api_round_trip(
    app_env, score_h_pdf, monkeypatch, insert_score
):
    """`test_which_bars_were_not_read_from_glyphs_survives_a_reload` above only
    ever checks `notes_no_stem` / `staves_no_stem` against zero, on a fixture
    with no notation staff to read a stem off at all. A persistence bug that
    unconditionally wrote 0 into that column would pass every test in this
    file and still be silently discarding the #115 disclosure on reload.

    That state - a genuinely non-zero floored-duration count - does not arise
    in anything engraved in this repository (see test_engraved_fixtures.py's
    "what these cannot reach" list), so this runs against the same real
    library score the extractor-level test does."""
    pdf = score_h_pdf
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    assert posted["notes_no_stem"] == 73
    assert posted["staves_no_stem"] == 4

    fetched = api.get_transcription(score_id)
    assert fetched["notes_no_stem"] == posted["notes_no_stem"]
    assert fetched["staves_no_stem"] == posted["staves_no_stem"]
    assert fetched["notes_no_stem"] > 0, "the whole point: this must not be the zero case"
    assert fetched["warnings"] == posted["warnings"]


def test_unassigned_dots_survive_the_api_round_trip(
    app_env, score_k_pdf, monkeypatch, insert_score
):
    """The same gap as `test_floored_note_durations_survive_the_api_round_trip`
    above, but for `dots_unassigned` / `dots_unassigned_no_candidate` /
    `dots_unassigned_eliminated` / `staves_dots_unassigned`:
    `test_which_bars_were_not_read_from_glyphs_survives_a_reload` only ever
    checks them against zero. A persistence bug that unconditionally wrote 0
    into any one of those columns would pass every other test in this file
    and still be silently discarding the disclosure on reload.

    Nothing engraved in this repository reaches a genuinely non-zero count
    (see test_engraved_fixtures.py's stacked-chord fixture, which measures
    the split directly but at the extractor level, not through the API), so
    this runs against a real library score - all eight of its unassigned dots
    are the no-candidate kind, none eliminated.

    Eight, not the sixteen this asserted before #111/#112: half of them were
    the two halves of two displaced seconds pairs whose dots the engraver
    pushed down a step, and both members of both pairs now own the dot printed
    for them (see glyph_rhythm._pushed_down_pairs). The eight that remain are
    this score's repeat-barline dots and the like, which belong to no note and
    are meant to be counted."""
    pdf = score_k_pdf
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    assert posted["dots_unassigned"] == 8
    assert posted["dots_unassigned_no_candidate"] == 8
    assert posted["dots_unassigned_eliminated"] == 0
    assert posted["staves_dots_unassigned"] == 4

    fetched = api.get_transcription(score_id)
    assert fetched["dots_unassigned"] == posted["dots_unassigned"]
    assert fetched["dots_unassigned_no_candidate"] == posted["dots_unassigned_no_candidate"]
    assert fetched["dots_unassigned_eliminated"] == posted["dots_unassigned_eliminated"]
    assert fetched["staves_dots_unassigned"] == posted["staves_dots_unassigned"]
    assert fetched["dots_unassigned"] > 0, "the whole point: this must not be the zero case"
    assert fetched["warnings"] == posted["warnings"]
    assert any("no stem this decoder could find" in w for w in fetched["warnings"])


def test_repeat_and_volta_disclosures_survive_the_api_round_trip(
    app_env, score_z_pdf, monkeypatch, insert_score
):
    """The same gap as the two round-trip tests above, but for the nine
    repeat/volta disclosure fields (issue #134 adversarial review, blocker
    3): ExtractionResult grew `repeats_unread`, `endings_unread`,
    `endings_truncated`, `form_marks_unanchored`, `endings_incomplete` and
    their `*_bars` lists, and `to_dict()` carries them - but `to_dict()` is
    never called anywhere in server/, and neither api.py's `_BAR_KEYS` /
    `_BAR_LIST_KEYS` allowlists nor the POST /transcribe confidence blob ever
    named them. The prose warning reached a reader; the structured field
    reached nobody. `test_which_bars_were_not_read_from_glyphs_survives_a_reload`
    only ever checks these against zero (no volta content on that fixture at
    all) - a persistence bug that unconditionally wrote 0/[] would pass every
    other test in this file and still be silently discarding the disclosure
    on reload.

    This score's one barline group is two thick strokes ("tHHt") with no
    repeat dots found anywhere nearby - not resolved to a direction, and
    (since a fix landed alongside this one - item 6 of the same review) no
    longer dropped outright either: the bar-style is still written, only the
    repeat is disclosed as unread."""
    pdf = score_z_pdf
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    assert posted["repeats_unread"] == 1
    assert posted["repeats_unread_bars"] == [8]
    assert posted["endings_unread"] == 0
    assert posted["endings_truncated"] == 0
    assert posted["form_marks_unanchored"] == 0
    assert posted["endings_incomplete"] == 0

    fetched = api.get_transcription(score_id)
    for key in ("repeats_unread", "endings_unread", "endings_truncated",
                "form_marks_unanchored", "endings_incomplete"):
        assert fetched[key] == posted[key], key
    for key in ("repeats_unread_bars", "endings_unread_bars",
                "endings_truncated_bars", "form_marks_unanchored_bars"):
        assert fetched[key] == posted[key], key
    assert fetched["repeats_unread"] > 0, "the whole point: this must not be the zero case"
    assert fetched["repeats_unread_bars"] == [8]
    assert fetched["warnings"] == posted["warnings"]
    assert any("could not be resolved to a clean forward/backward" in w
               for w in fetched["warnings"])


def test_a_lost_system_survives_the_api_round_trip(
    app_env, score_ac_pdf, monkeypatch, insert_score
):
    """Issue #152's counter, on a score whose count is genuinely nonzero.

    Every other figure this API reports describes the systems that WERE
    read. `systems_unread` is the one that says how far that qualification
    reaches - music ABSENT from the transcription rather than imperfect in
    it - so a reader who reloads a transcription and gets None for it is
    holding a set of numbers that silently excludes a page's worth of music,
    and cannot know.

    Run against a nonzero score deliberately, for the reason the tests above
    state: a persistence bug that unconditionally wrote 0 and [] would pass
    every zero-valued assertion in this file. score_ac loses one system on
    page 1 to a 7-line group (see the conftest note), and it is one of only
    two scores in the library that still lose one at all.

    This is the round trip #146 was filed for after `unison_digits_shared`
    reached ExtractionResult, to_dict() and _BAR_KEYS but never the
    confidence_json dict that is the only path into storage - so the prose
    reached a reader and the number read back as None. Asserted here, in the
    same change that added the field, rather than left for a later review.
    """
    pdf = score_ac_pdf
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    assert posted["systems_unread"] == 1
    assert posted["systems_unread_pages"] == [1]

    fetched = api.get_transcription(score_id)
    for key in ("systems_unread", "systems_unread_pages"):
        assert fetched[key] == posted[key], key
        assert fetched[key] is not None, key
    assert fetched["systems_unread"] > 0, (
        "the whole point: this must not be the zero case")
    # The prose half of the same disclosure, which is what a reader who never
    # looks at the figures actually sees.
    assert any("systems_unread=1" in w for w in fetched["warnings"]), \
        fetched["warnings"]
    # And a lost system caps the structure claim, because the marks that were
    # read may be complete and still describe a form built out of bars this
    # file does not contain.
    assert fetched["confidence"]["confidence"]["structure"].startswith("low"), \
        fetched["confidence"]["confidence"]


def test_navigation_disclosures_survive_the_api_round_trip(
    app_env, score_af_pdf, monkeypatch, insert_score
):
    """The same check for issue #134 phase 2's two navigation disclosures.
    Against a fixture with a genuinely nonzero count rather than the zero
    case, for the reason the test above states: a persistence bug that
    unconditionally wrote 0/[] would pass every zero-valued assertion in
    this file.

    This score prints "To Coda" closing bar 22 on a page that draws no coda
    sign and prints no coda label anywhere at all, so the instruction is
    written as the words the page prints, with no `<sound tocoda=>` beside
    it, and the bar is reported here.

    It used to be score_v, on the strength of its "D.S." having no
    segno to name - which was never true of that score, or of 82 others. Its
    segno was drawn in Finale's Maestro at the glyph ID this project's table
    labelled "simile", so the count this test needed to be nonzero was
    nonzero because of a bug, and the fix took it to 0. Picked for having
    nothing to do with the segno at all."""
    pdf = score_af_pdf
    monkeypatch.setattr(api, "LIBRARY_DIR", pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, pdf.name)

    posted = api.transcribe(score_id, body=None)
    assert posted["nav_marks_unresolved"] == 1
    assert len(posted["nav_marks_unresolved_bars"]) == 1
    assert posted["nav_marks_unanchored"] == 0

    fetched = api.get_transcription(score_id)
    for key in ("nav_marks_unresolved", "nav_marks_unanchored",
                "nav_marks_unresolved_bars"):
        assert fetched[key] == posted[key], key
    assert fetched["nav_marks_unresolved"] > 0, (
        "the whole point: this must not be the zero case")
    assert any("naming a jump this transcription holds no target for" in w
               for w in fetched["warnings"])
