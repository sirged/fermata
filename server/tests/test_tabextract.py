from fermata import tabextract


def test_finale_tab_pdf_extracts_notes_and_bars(zanarkand_pdf):
    result = tabextract.extract(zanarkand_pdf, time_signature=(3, 4))
    assert result.extractable
    assert result.reason is None
    assert result.tab_staff_count == 5
    assert result.standard_staff_count == 5
    # Validated against this exact file: page 1 alone extracts 185 notes
    # across 24 bars. The score is 2 pages, so the combined total can only
    # be more - use these as a floor rather than pinning an exact count.
    assert result.bars >= 24
    assert result.notes >= 185
    assert result.pages_processed == 2
    assert result.tuning_label == "Drop D"
    assert result.tuning == ["D2", "A2", "D3", "G3", "B3", "E4"]
    assert result.time_signature == (3, 4)
    assert result.time_signature_source == "manual override"
    assert result.tempo == 88
    assert result.alphatex is not None
    assert '\\tuning D2 A2 D3 G3 B3 E4' in result.alphatex
    assert '\\ts 3 4' in result.alphatex
    assert any("low confidence" in w for w in result.warnings)


def test_finale_tab_pdf_analyze(zanarkand_pdf):
    info = tabextract.analyze(zanarkand_pdf)
    assert info["extractable"] is True
    assert info["vector"] is True
    assert info["tab_staff_count"] >= 5
    assert info["standard_staff_count"] >= 5
    assert info["page_count"] == 2


def test_finale_tab_pdf_without_ts_override_still_extracts(zanarkand_pdf):
    # Auto time-signature detection is a known-broken heuristic (see module
    # docstring) - without an override the extractor must fall back to an
    # assumed 4/4 and say so, not fail or silently mis-report success.
    result = tabextract.extract(zanarkand_pdf)
    assert result.extractable
    assert result.time_signature == (4, 4)
    assert result.time_signature_source == "not detected (assumed 4/4)"
    assert any("time signature not detected" in w for w in result.warnings)


def test_notation_only_pdf_has_no_tab_staves(tarrega_pdf):
    info = tabextract.analyze(tarrega_pdf)
    assert info["extractable"] is False
    assert info["vector"] is True
    assert info["tab_staff_count"] == 0
    assert info["standard_staff_count"] > 0

    result = tabextract.extract(tarrega_pdf)
    assert result.extractable is False
    assert result.alphatex is None
    assert "standard-notation only" in result.reason


def test_raster_pdf_is_not_extractable(claire_de_lune_pdf):
    info = tabextract.analyze(claire_de_lune_pdf)
    assert info["extractable"] is False
    assert info["vector"] is False
    assert "raster" in info["reason"]

    result = tabextract.extract(claire_de_lune_pdf)
    assert result.extractable is False
    assert "raster" in result.reason


def test_malformed_pdf_never_raises(tmp_path):
    bogus = tmp_path / "not_a_pdf.pdf"
    bogus.write_bytes(b"this is not a pdf file, just garbage bytes")
    info = tabextract.analyze(bogus)
    assert info["extractable"] is False
    assert info["reason"]
    result = tabextract.extract(bogus)
    assert result.extractable is False
    assert result.reason


def test_missing_pdf_never_raises(tmp_path):
    missing = tmp_path / "does_not_exist.pdf"
    info = tabextract.analyze(missing)
    assert info["extractable"] is False
    result = tabextract.extract(missing)
    assert result.extractable is False
