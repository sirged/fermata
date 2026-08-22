"""Unit tests for the glyph rhythm decoder's own primitives.

These build the geometry directly rather than going through a PDF: the
failure modes under test are all "which vector primitive did this notehead
attach to, and what did that make its duration", which is far clearer to pin
down from explicit coordinates than from a synthesised engraving.
"""
import io
import struct

import pytest

from fermata import glyph_rhythm as G, tabextract


class _P:
    """Stand-in for a pymupdf Point."""

    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


def _ev(category, x0, y0, x1, y1, gid=0, family="Maestro"):
    return G.GlyphEvent(family, gid, category, (x0, y0, x1, y1), 0)


REF = G.REFERENCE_STAFF_SPACING  # 5.125pt - the spacing the old constants assumed


def _tol(spacing=REF, staff_height=None, staff_width=500.0):
    if staff_height is None:
        staff_height = spacing * 4
    return G._Tol(spacing, staff_height, staff_width)


# ---------------------------------------------------------------------------
# Maestro fingerprint (finding 1)
# ---------------------------------------------------------------------------


def _embedded_maestro_bytes(pdf_path):
    """The raw bytes of the first embedded font named Maestro in a PDF."""
    import fitz

    doc = fitz.open(pdf_path)
    try:
        for pno in range(doc.page_count):
            for f in doc[pno].get_fonts(full=True):
                if f[3].split("+")[-1] != "Maestro":
                    continue
                content = doc.extract_font(f[0])
                if isinstance(content, tuple):
                    content = content[-1]
                if content:
                    return bytes(content)
    finally:
        doc.close()
    return None


def test_fingerprint_accepts_the_calibrated_library_font(zanarkand_pdf):
    """The real thing must pass, or the check is just an outage."""
    TTFont = pytest.importorskip("fontTools.ttLib").TTFont
    raw = _embedded_maestro_bytes(zanarkand_pdf)
    assert raw, "expected an embedded Maestro in the reference file"
    ok, detail = G.maestro_fingerprint_ok(TTFont(io.BytesIO(raw), fontNumber=0))
    assert ok, detail
    # and it must be based on real evidence, not an empty-set vacuous pass
    assert "match" in detail


def test_fingerprint_rejects_altered_glyph_outlines(zanarkand_pdf):
    """A font that keeps the name "Maestro" but not the calibrated outlines
    must be refused. Otherwise a Maestro from another Finale version or
    another subsetting path silently mis-decodes every notehead, rest, flag
    and time-signature digit while still reporting high confidence."""
    TTFont = pytest.importorskip("fontTools.ttLib").TTFont
    raw = _embedded_maestro_bytes(zanarkand_pdf)
    assert raw

    tt = TTFont(io.BytesIO(raw), fontNumber=0)
    order = tt.getGlyphOrder()
    glyf = tt["glyf"]
    # Swap two mapped, filled glyphs - exactly what a different subsetting
    # path produces: same family, same glyph count, different GID order.
    a, b = order[157], order[199]  # notehead_filled <-> notehead_half
    glyf[a], glyf[b] = glyf[b], glyf[a]
    buf = io.BytesIO()
    tt.save(buf)
    buf.seek(0)

    ok, detail = G.maestro_fingerprint_ok(TTFont(buf, fontNumber=0))
    assert not ok
    assert "differ from the calibrated Maestro subset" in detail


def test_fingerprint_rejects_when_every_digest_disagrees():
    """Pure comparison logic, no library needed."""
    class _FakeTT:
        pass

    bogus = {gid: "0" * 32 for gid in G.MAESTRO_GID_MAP}
    real = {157: "a" * 32, 13: "b" * 32, 52: "c" * 32, 199: "d" * 32, 40: "e" * 32}

    def fake_digests(tt, gids):
        return real

    original = G._glyf_digests
    G._glyf_digests = fake_digests
    try:
        ok, detail = G.maestro_fingerprint_ok(_FakeTT(), digests=bogus)
        assert not ok
        assert "differ" in detail
        # ...and accepts when they agree
        ok2, _ = G.maestro_fingerprint_ok(_FakeTT(), digests=dict(real))
        assert ok2
    finally:
        G._glyf_digests = original


def test_fingerprint_needs_enough_evidence_to_bless_a_font():
    """A font supplying only one or two calibrated outlines has not proved
    it is the calibrated subset, even if those happen to match."""
    class _FakeTT:
        pass

    real = {157: "a" * 32}
    original = G._glyf_digests
    G._glyf_digests = lambda tt, gids: real
    try:
        ok, detail = G.maestro_fingerprint_ok(_FakeTT(), digests=dict(real))
        assert not ok
        assert "need" in detail
    finally:
        G._glyf_digests = original


# ---------------------------------------------------------------------------
# Stem / barline discrimination (B6)
# ---------------------------------------------------------------------------


def test_barline_is_not_mistaken_for_a_stem():
    """A barline is drawn to the staff's own outer line coordinates. Treating
    it as a stem lets a rest beside it be misread as a flag (and vanish), and
    lets a notehead beside it inherit the barline's absent flag count."""
    tol = _tol()
    top, bottom = 100.0, 100.0 + REF * 4
    # real barline: both ends on the outer staff lines (measured offset ~0.03pt)
    assert G._is_barline(top + 0.03, bottom + 0.03, top, bottom, tol)
    # real down-stem from a note above the staff to a beam just below it -
    # spans almost the same range, but misses both lines by ~1pt
    assert not G._is_barline(top + 0.88, bottom + 1.30, top, bottom, tol)
    # ordinary stem inside the staff
    assert not G._is_barline(top + 5.0, bottom - 2.0, top, bottom, tol)


# ---------------------------------------------------------------------------
# Beam geometry (B3, B5, efficiency 10)
# ---------------------------------------------------------------------------


def _quad(x0, y0, x1, y1, thickness):
    """A beam-shaped filled parallelogram from (x0,y0) to (x1,y1)."""
    return [
        _P(x0, y0), _P(x1, y1), _P(x1, y1 + thickness), _P(x0, y0 + thickness), _P(x0, y0),
    ]


def test_beam_carries_interpolated_end_ys_not_a_bbox_centre():
    """A steeply slanted beam's bbox centre is nowhere near its actual y at
    either end, so matching a stem tip against the centre rejected the first
    and last note of exactly the slanted groups this decoder detects."""
    tol = _tol()
    beam = G._beam_from_contour(_quad(100.0, 200.0, 185.0, 216.7, 2.0), tol)
    assert beam is not None
    # centreline at each end, not the bbox centre (~209.35)
    assert beam.y_at_x0 == pytest.approx(201.0, abs=0.6)
    assert beam.y_at_x1 == pytest.approx(217.7, abs=0.6)
    assert G.beam_y_at(beam, 100.0) == pytest.approx(beam.y_at_x0, abs=0.01)
    assert G.beam_y_at(beam, 185.0) == pytest.approx(beam.y_at_x1, abs=0.01)
    mid = G.beam_y_at(beam, 142.5)
    assert beam.y_at_x0 < mid < beam.y_at_x1


def test_slanted_beam_is_counted_at_both_ends_of_the_group():
    """The stems at each end of a slanted beam group must both find it."""
    tol = _tol()
    beam = G._beam_from_contour(_quad(100.0, 200.0, 185.0, 216.7, 2.0), tol)
    assert beam is not None
    # up-stems: notehead below, free (beam) end above
    first = G.Stem(100.0, 201.0, 230.0)
    last = G.Stem(185.0, 217.7, 246.0)
    assert G._beam_count_near([beam], first, notehead_yc=230.0, tol=tol) == 1
    assert G._beam_count_near([beam], last, notehead_yc=246.0, tol=tol) == 1


def test_staff_line_shaped_rect_is_not_a_beam():
    """Real exporters draw staff lines as thin filled rectangles (see
    tabextract._long_horizontal_segments). One of those passing the beam test
    hands every quarter in the system phantom beam levels."""
    tol = _tol(staff_width=500.0)
    # a staff-line-wide dark rect must be rejected on width...
    wide = G._beam_from_contour(_quad(50.0, 200.0, 500.0, 200.0, 0.5), tol)
    assert wide is None
    # ...while a real beam of ordinary width is still accepted
    normal = G._beam_from_contour(_quad(100.0, 200.0, 113.0, 200.0, 2.0), tol)
    assert normal is not None


def test_beam_detection_short_circuits_oversized_contours():
    """A beam is one quad. A 200-point decorative contour is not, and must
    not cost an O(n^2) all-pairs scan just to be rejected."""
    tol = _tol()
    big = [_P(100.0 + i * 0.2, 200.0 + (i % 3)) for i in range(200)]
    assert G._beam_from_contour(big, tol) is None


# ---------------------------------------------------------------------------
# Stem selection (B4)
# ---------------------------------------------------------------------------


def test_note_keeps_its_own_plain_stem_against_a_neighbours_beam():
    """A genuine quarter whose neighbouring voice has a beamed stem within
    tolerance must stay a quarter. Selecting the candidate with the HIGHEST
    flag/beam count instead skipped the note's own (zero-count) stem
    outright, so the neighbour's beam always won."""
    tol = _tol()
    yc = 200.0
    # notehead spanning x 96..103; its own up-stem sits on the right edge
    own = G.Stem(103.0, 175.0, 200.5)
    # a second voice's beamed down-stem on the left edge, but attached to a
    # notehead a staff step lower - its near end is further away in y
    other = G.Stem(96.2, 206.0, 228.0)
    chosen = G._best_stem([other, own], [96.2, 103.0], 96.0, 103.0, yc, tol)
    assert chosen == own


def test_stem_selection_prefers_the_closest_candidate():
    tol = _tol()
    near = G.Stem(103.0, 176.0, 200.2)
    far = G.Stem(105.9, 176.0, 200.2)
    chosen = G._best_stem([near, far], [103.0, 105.9], 96.0, 103.0, 200.0, tol)
    assert chosen == near


def _best(stems, x0, x1, yc, tol):
    ordered = sorted(stems, key=lambda s: s.x)
    return G._best_stem(ordered, [s.x for s in ordered], x0, x1, yc, tol)


def test_a_melody_note_over_a_chord_keeps_its_own_stem_not_the_chords():
    """The commonest two-voice figure in this repertoire, and the geometry is
    measured off the page it was found wrong on: page 60 of the library's
    Christmas collection, bar 322, where a melody quarter sits directly above
    a stem-down bass chord on the same beat.

    Both voices' stems are at the SAME place horizontally - one at each side
    of the notehead - so the x distances differ by 0.16pt, a stem stroke's
    width. The y distances differ by 4.8pt, a whole staff space. Ranking on x
    and using y only to break its ties therefore handed this notehead to the
    bass chord's down-stem: the melody lost its third beat, the chord gained a
    fourth note, and the bar came out 3 quarters against 4."""
    tol = _tol(spacing=4.975)
    yc = 167.253
    ink_x0, ink_x1 = 417.19, 423.72
    own_up = G.Stem(423.398, 149.891, 166.322)       # ends 0.93pt from yc
    chords_down = G.Stem(417.333, 172.992, 206.938)  # ends 5.74pt from yc
    assert abs(chords_down.x - ink_x0) < abs(own_up.x - ink_x1), (
        "the losing stem really is the nearer one in x")
    assert _best([own_up, chords_down], ink_x0, ink_x1, yc, tol) == own_up


def test_an_up_stem_is_not_measured_from_the_advance_width(monkeypatch):
    """Measured off page 2 of Dalza's Recercar, score measure 11: a filled
    eighth with its own up-stem, one staff space above a stem-down half-note
    chord. This is the same figure as the test above with the font's side
    bearing added on top - Opus's notehead box overhangs its ink by 0.324
    staff spaces on the RIGHT and not at all on the left, so against the box
    the up-stem at the ink's right edge measures 1.75pt away while the other
    voice's down-stem at the left edge measures 0.16pt.

    With that bar read wrong the 2/2 measure emitted one voice of 6 half-note
    units and no second voice at all - a fourfold duration error."""
    tol = _tol(spacing=5.05)
    yc = 548.382
    box_x0, box_x1 = 89.16, 97.67
    ink_x0, ink_x1 = 89.16, 96.04
    own_up = G.Stem(95.92, 533.452, 547.606)
    chords_down = G.Stem(89.316, 554.211, 584.878)
    head = G.GlyphEvent("Opus", 210, "notehead_filled",
                        (box_x0, yc - 10.0, box_x1, yc + 10.0), 0,
                        baseline_y=yc, ink=(yc - 2.5, yc + 2.5),
                        ink_x=(ink_x0, ink_x1))
    assert head.stem_edges == (ink_x0, ink_x1)
    assert box_x1 - ink_x1 > 0.3 * tol.spacing, "the side bearing under test"
    assert _best([own_up, chords_down], *head.stem_edges, yc, tol) == own_up


def test_a_notehead_with_no_readable_outline_still_uses_its_metrics_box():
    """An unreadable outline must cost precision, not the attachment: the box
    edges are what the whole decoder used before the ink was available."""
    head = G.GlyphEvent("Maestro", 210, "notehead_filled",
                        (96.0, 190.0, 103.0, 210.0), 0, baseline_y=200.0)
    assert head.stem_edges == (96.0, 103.0)
    tol = _tol()
    own = G.Stem(103.0, 175.0, 200.5)
    assert _best([own], *head.stem_edges, 200.0, tol) == own


class _StubFont:
    """One glyph's `glyf` header, as _InkBoxes reads it: five int16s of
    (numberOfContours, xMin, yMin, xMax, yMax) at 1000 units per em."""

    def __init__(self, xmin, ymin, xmax, ymax, contours=1):
        self._data = struct.pack(">hhhhh", contours, xmin, ymin, xmax, ymax)

    def getTableData(self, tag):
        assert tag == "glyf"
        return self._data

    def __getitem__(self, tag):
        if tag == "loca":
            return [0, len(self._data)]
        if tag == "head":
            return type("_H", (), {"unitsPerEm": 1000})()
        raise KeyError(tag)


def test_the_ink_reader_answers_for_each_axis_separately():
    """A glyph drawn as a single vertical stroke has a degenerate x extent and
    a perfectly good y one, so the two axes have to be refused independently -
    folding them into one usability test would take the vertical reading, and
    the half-or-whole rest turns on that."""
    boxes = G._InkBoxes(_StubFont(-120, -250, 130, 260))
    assert boxes.xspan(0) == (-0.120, 0.130)
    assert boxes.span(0) == (-0.250, 0.260)
    flat = G._InkBoxes(_StubFont(40, -250, 40, 260))
    assert flat.xspan(0) is None
    assert flat.span(0) == (-0.250, 0.260)
    absurd = G._InkBoxes(_StubFont(-120, -32000, 130, 260))
    assert absurd.span(0) is None
    assert absurd.xspan(0) == (-0.120, 0.130)


def test_the_horizontal_ink_scales_out_from_the_glyphs_origin():
    """Page x and font x both grow rightward, so unlike the vertical twin this
    is a straight scale with no flip - getting that backwards would put every
    notehead's ink on the wrong side of where it was drawn."""
    boxes = G._InkBoxes(_StubFont(-120, -250, 130, 260))
    assert G._ink_x_on_page(boxes, 0, 400.0, 20.0) == (400.0 - 2.4, 400.0 + 2.6)
    assert G._ink_span_on_page(boxes, 0, 400.0, 20.0) == (400.0 - 5.2, 400.0 + 5.0)
    assert G._ink_x_on_page(boxes, 0, None, 20.0) is None
    assert G._ink_x_on_page(None, 0, 400.0, 20.0) is None


def test_a_stem_further_in_x_does_not_win_on_a_hair_of_y():
    """The other half of the ranking, and why y cannot decide alone. A
    NEIGHBOURING note's stem, a notehead-width away in x, can end nearer this
    notehead's centre than its own stem does - here by 0.02 staff spaces
    against 0.63 spaces of x. Weighting each distance by its own tolerance
    keeps the attachment; ranking on y first loses it."""
    tol = _tol()
    yc = 246.0
    ink_x0, ink_x1 = 232.08, 238.72
    own_up = G.Stem(238.8, 224.4, 250.5)        # touching the right edge
    neighbours_up = G.Stem(228.85, 224.4, 245.3)  # 3.2pt clear of the left edge
    assert abs(neighbours_up.y1 - yc) < abs(own_up.y1 - yc), (
        "the losing stem really is the nearer one in y")
    assert _best([own_up, neighbours_up], ink_x0, ink_x1, yc, tol) == own_up


# ---------------------------------------------------------------------------
# Flag counting (B7)
# ---------------------------------------------------------------------------


def test_opus_shared_flag_rest_glyph_counts_as_a_flag():
    """Opus draws an unbeamed eighth's hook with the same glyph it uses for a
    quarter rest (uniF0CE). decode_note_events skips it as a rest when a stem
    is beside it, on the stated grounds that it was "already counted via the
    notehead's stem" - so it has to actually be countable as a flag, or every
    unbeamed Sibelius eighth decodes as a quarter."""
    assert "flag8_or_rest_quarter" in G.FLAG_HOOKS
    assert G.FLAG_HOOKS["flag8_or_rest_quarter"] == 1
    tol = _tol()
    stem = G.Stem(103.0, 175.0, 200.5)  # up-stem, free end at y=175
    flag = _ev("flag8_or_rest_quarter", 103.0, 172.0, 108.0, 180.0, family="Opus")
    hooks = G._flag_count_near([flag], [flag.xc], stem, notehead_yc=200.0, tol=tol)
    assert hooks == 1
    # flag16 still counts as two halvings
    f16 = _ev("flag16", 103.0, 172.0, 108.0, 180.0)
    assert G._flag_count_near([f16], [f16.xc], stem, 200.0, tol) == 2


# ---------------------------------------------------------------------------
# Augmentation dots
# ---------------------------------------------------------------------------


def test_one_dot_belongs_to_exactly_one_note():
    """An augmentation dot is nudged half a staff space off its own line, so
    the y window cannot separate two voices a staff step apart. Counting
    every dot in a window per notehead therefore let one dot be claimed
    twice and let a neighbour's dot be claimed here - which is how a 3/4 bar
    came to hold a double-dotted half (3.5 quarters)."""
    tol = _tol()
    upper = _ev("notehead_half", 96.0, 197.0, 103.0, 203.0)   # yc 200
    lower = _ev("notehead_half", 96.0, 202.0, 103.0, 208.0)   # yc 205
    dot = _ev("dot", 105.0, 203.0, 107.0, 205.0)              # yc 204 - lower's
    counts = G._assign_dots([upper, lower], [dot], tol)
    assert counts[id(lower)] == 1
    assert counts.get(id(upper), 0) == 0
    assert sum(counts.values()) == 1


def test_double_dot_is_still_read_when_two_dots_really_are_there():
    tol = _tol()
    head = _ev("notehead_half", 96.0, 197.0, 103.0, 203.0)  # yc 200
    d1 = _ev("dot", 104.0, 199.0, 106.0, 201.0)
    d2 = _ev("dot", 107.0, 199.0, 109.0, 201.0)
    counts = G._assign_dots([head], [d1, d2], tol)
    assert counts[id(head)] == 2


# ---------------------------------------------------------------------------
# Staff-space tolerances (finding 5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spacing", [2.0, 5.125, 12.0])
def test_geometry_decodes_the_same_at_any_staff_size(spacing):
    """Every tolerance is in staff spaces, so the same engraving scaled to a
    condensed multi-system score or a large-print edition must decode
    identically. With absolute point tolerances, a small staff dropped its
    stems and beams and degraded every eighth and sixteenth to a quarter -
    with events still present, so confidence stayed "high"."""
    k = spacing / REF
    tol = _tol(spacing, staff_width=500.0 * k)
    # a beam and its stem, both scaled by k
    beam = G._beam_from_contour(
        _quad(100.0 * k, 200.0 * k, 113.0 * k, 202.6 * k, 2.0 * k), tol)
    assert beam is not None, f"beam lost at spacing {spacing}"
    stem = G.Stem(100.0 * k, 201.0 * k, 226.0 * k)
    assert G._beam_count_near([beam], stem, notehead_yc=226.0 * k, tol=tol) == 1
    # ...and the stem attaches to its notehead at any size
    chosen = G._best_stem([stem], [stem.x], 94.0 * k, 100.0 * k, 226.0 * k, tol)
    assert chosen == stem


def test_tolerances_scale_linearly_with_spacing():
    small = _tol(2.0)
    big = _tol(20.0)
    assert big.stem_x_tol == pytest.approx(small.stem_x_tol * 10)
    assert big.dot_x_tol == pytest.approx(small.dot_x_tol * 10)
    assert big.beam_min_thickness == pytest.approx(small.beam_min_thickness * 10)


def test_tolerances_fall_back_to_the_reference_spacing_when_unknown():
    """A degenerate staff must not produce zero-width tolerances (which would
    match nothing) or a division by zero."""
    for bad in (0, None, -1):
        tol = G._Tol(bad)
        assert tol.spacing == G.REFERENCE_STAFF_SPACING
        assert tol.stem_x_tol > 0


# ---------------------------------------------------------------------------
# Time signature validation (A1)
# ---------------------------------------------------------------------------


def test_time_signature_validity_matches_the_api_rule():
    assert G.time_signature_is_valid((4, 4))
    assert G.time_signature_is_valid((12, 16))
    assert G.time_signature_is_valid((7, 8))
    # denominator must be a power of two to mean a note-duration unit, and
    # alphaTab throws outright on something like \ts 3 12
    assert not G.time_signature_is_valid((3, 12))
    assert not G.time_signature_is_valid((1, 28))
    assert not G.time_signature_is_valid((4, 0))
    assert not G.time_signature_is_valid((0, 4))
    assert not G.time_signature_is_valid((33, 4))
    assert not G.time_signature_is_valid(None)


def test_digit_clusters_do_not_merge_vertically_stacked_digits():
    """Clustering digits by x-gap alone merges a numerator digit that landed
    in the denominator band with the denominator's own digits, turning 1/16
    into 116 and 3/4 into 34 - which reaches \\ts as e.g. `3 12` and makes
    the stored transcription unrenderable."""
    # two digits at the same x but different rows: never one number
    top = _ev("digit1", 60.0, 210.0, 66.0, 224.0)
    bottom = _ev("digit6", 60.4, 220.0, 66.4, 234.0)
    clusters = G._group_digit_clusters([top, bottom])
    assert len(clusters) == 2
    # two digits side by side on the same row: one number
    left = _ev("digit1", 60.0, 210.0, 66.0, 224.0)
    right = _ev("digit2", 66.2, 210.0, 74.0, 224.0)
    clusters2 = G._group_digit_clusters([left, right])
    assert len(clusters2) == 1
    assert G._cluster_value(clusters2[0]) == 12


def test_unusable_signature_is_reported_as_not_detected():
    """A digit grouping that isn't a real meter must degrade to "not
    detected", never reach \\ts."""
    mid = 224.0
    window = [
        _ev("digit3", 60.0, 212.0, 66.0, 220.0),   # numerator, yc 216
        _ev("digit1", 60.0, 226.0, 66.0, 232.0),   # denominator "1"
        _ev("digit2", 66.2, 226.0, 72.2, 232.0),   # denominator "2" -> 12
    ]
    ts, reason = G._signature_from_window(window, mid)
    assert ts is None
    assert "not a usable time signature" in reason


def test_valid_stacked_signature_is_still_read():
    mid = 224.0
    window = [
        _ev("digit1", 60.0, 212.0, 66.0, 220.0),
        _ev("digit2", 66.2, 212.0, 74.0, 220.0),   # numerator 12
        _ev("digit8", 62.0, 226.0, 70.0, 234.0),   # denominator 8
    ]
    ts, reason = G._signature_from_window(window, mid)
    assert ts == (12, 8)


# ---------------------------------------------------------------------------
# fontTools availability (A2)
# ---------------------------------------------------------------------------


def test_missing_fonttools_degrades_instead_of_crashing(monkeypatch):
    """glyph_rhythm is reachable from fermata.main -> api -> tabextract, so a
    top-level fontTools import turned a missing install into a crash of the
    whole server at startup - taking down /api/health and plain PDF viewing,
    neither of which touches glyph decoding."""
    monkeypatch.setattr(G, "_TTFONT", None)
    monkeypatch.setattr(G, "_TTFONT_STATE", "missing")
    mf, warn = G._load_one_font(doc=None, xref=1, base="Maestro", ext="ttf")
    assert mf is None
    assert "fontTools" in warn


def test_non_truetype_embedding_is_refused_by_flavour():
    """The Maestro GID map and the Opus name maps were both calibrated on
    TrueType subsets; a CFF-flavour embed is not covered."""
    mf, warn = G._load_one_font(doc=None, xref=1, base="Maestro", ext="cff")
    assert mf is None
    assert "cff" in warn


# ---------------------------------------------------------------------------
# Stem direction: the signal voices are separated by
# ---------------------------------------------------------------------------


def _note(x, y, key):
    return G.NoteEvent(x, y, 1.0, 0, 0, False, "notehead_filled",
                       notehead_kind="notehead_filled", stem_key=key)


def test_stem_direction_comes_from_which_end_overhangs_the_notehead():
    """Page y grows DOWNWARD. An up-stem runs about an octave ABOVE its
    notehead and stops dead at it, so the overhang is on the small-y side."""
    # Stems attach at a notehead's EDGE, never its centre: an up-stem on the
    # right, a down-stem on the left (see _assign_stem_directions).
    up = G.Stem(x=54.0, y0=60.0, y1=100.0)      # tip above a notehead at y=100
    down = G.Stem(x=76.0, y0=100.0, y1=140.0)   # tip below a notehead at y=100
    notes = [_note(50.0, 100.0, "u"), _note(80.0, 100.0, "d")]
    G._assign_stem_directions(notes, {"u": up, "d": down})
    assert [n.stem_dir for n in notes] == ["up", "down"]


def test_a_chords_direction_is_not_read_off_one_of_its_noteheads():
    """A chord shares ONE stem that runs PAST all of its noteheads, so the
    end further from any given member is on the wrong side for every member
    but the outermost - reading "the free end is below me, therefore stem
    down" off the top note of an up-stemmed chord inverts it."""
    # up-stem chord: noteheads at y=100/110/120, stem on their RIGHT running
    # from its tip at y=60 down to the lowest notehead at y=120.
    stem = G.Stem(x=54.0, y0=60.0, y1=120.0)
    notes = [_note(50.0, y, "c") for y in (100.0, 110.0, 120.0)]
    G._assign_stem_directions(notes, {"c": stem})
    assert [n.stem_dir for n in notes] == ["up", "up", "up"]

    # and the mirror image: down-stem on their LEFT
    stem_d = G.Stem(x=46.0, y0=100.0, y1=160.0)
    notes_d = [_note(50.0, y, "c") for y in (100.0, 110.0, 120.0)]
    G._assign_stem_directions(notes_d, {"c": stem_d})
    assert [n.stem_dir for n in notes_d] == ["down", "down", "down"]


def test_a_notehead_partway_along_a_chord_stem_still_finds_it():
    """_best_stem only attaches a notehead at a stem's END, which is where
    the flag lives. A chord's inner members sit far outside that window and
    would each become a beat - and, at one onset, a phantom second voice."""
    tol = G._Tol(REF)
    stem = G.Stem(x=50.0, y0=60.0, y1=120.0)
    stems, stem_xs = [stem], [stem.x]
    # a notehead centred at y=100: halfway up the stem, nowhere near an end
    assert G._best_stem(stems, stem_xs, 44.0, 50.0, 100.0, tol) is None
    assert G._stem_through_notehead(stems, stem_xs, 44.0, 50.0, 100.0, tol) is stem
    # ...but a notehead the stem does not span is not on it
    assert G._stem_through_notehead(stems, stem_xs, 44.0, 50.0, 200.0, tol) is None


def test_a_stem_whose_side_contradicts_its_overhang_is_not_believed():
    """An up-stem leaves a notehead at its RIGHT edge and a down-stem at its
    left. _best_stem accepts any stem end within about a staff space, which a
    neighbouring voice's stem can satisfy in close-spaced two-voice writing,
    so a stem on the wrong side for the direction it implies is not this
    notehead's - and believing it would file the note in the wrong voice.
    Dropping it loses information; believing it inverts it."""
    contradictory = G.Stem(x=44.0, y0=65.0, y1=100.0)  # left of the head, points up
    n = _note(48.0, 100.0, "c")
    G._assign_stem_directions([n], {"c": contradictory})
    assert n.stem_dir is None
    assert n.stem_key is None, "and it must not group with that stem either"

    # a genuine right-edge up-stem is still believed
    own = G.Stem(x=52.0, y0=65.0, y1=100.0)
    m = _note(48.0, 100.0, "own")
    G._assign_stem_directions([m], {"own": own})
    assert m.stem_dir == "up"

    # ...as is a genuine left-edge down-stem
    down = G.Stem(x=44.0, y0=100.0, y1=135.0)
    d = _note(48.0, 100.0, "d")
    G._assign_stem_directions([d], {"d": down})
    assert d.stem_dir == "down"


def test_a_seconds_chord_keeps_its_stem_despite_one_displaced_notehead():
    """A chord containing a second displaces one notehead to the far side of
    the shared stem, so the side test has to be taken over the group's mean -
    per notehead it would throw the whole chord's stem away."""
    stem = G.Stem(x=52.0, y0=60.0, y1=110.0)
    members = [_note(48.0, 110.0, "c"), _note(48.0, 105.0, "c"),
               _note(57.0, 100.0, "c")]  # the displaced one, right of the stem
    G._assign_stem_directions(members, {"c": stem})
    assert [m.stem_dir for m in members] == ["up", "up", "up"]


# ---------------------------------------------------------------------------
# Key signature
# ---------------------------------------------------------------------------

# One synthetic notation staff: five lines 5pt apart, top 200, bottom 220,
# middle 210. A clef at x 50-66, then the key signature, then the meter.
_KS_TOP, _KS_BOTTOM, _KS_MID = 200.0, 220.0, 210.0


def _ks_clef(x0=50.0):
    return _ev("clef", x0, 200.0, x0 + 16.0, 220.0)


def _ks_meter(x0=110.0):
    """A stacked 4/4: numerator above the middle line, denominator below, at
    the same x."""
    return [
        _ev("digit4", x0, 200.0, x0 + 7.0, 209.0),
        _ev("digit4", x0, 211.0, x0 + 7.0, 220.0),
    ]


def _ks_sharps(count, x0=70.0, step=7.0):
    return [_ev("sharp", x0 + i * step, 203.0, x0 + i * step + 5.0, 213.0)
            for i in range(count)]


def _decode_ks(events, monkeypatch):
    """Run decode_key_signature over a synthetic glyph set."""
    page = object()
    monkeypatch.setattr(
        G, "extract_glyph_events",
        lambda _page: G.PageGlyphs(list(events), {"Opus": []}, [], []))
    return G.decode_key_signature(page, _KS_TOP, _KS_BOTTOM, 48.0, 5.0)


def test_key_signature_reads_the_accidentals_between_clef_and_meter(monkeypatch):
    events = [_ks_clef()] + _ks_sharps(4) + _ks_meter()
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths == 4
    assert "4 accidental glyph" in why


def test_key_signature_counts_flats_as_negative_fifths(monkeypatch):
    flats = [_ev("flat", 70.0 + i * 7.0, 203.0, 75.0 + i * 7.0, 213.0) for i in range(3)]
    events = [_ks_clef()] + flats + _ks_meter()
    fifths, _why = _decode_ks(events, monkeypatch)
    assert fifths == -3


def test_an_octave_transposing_clef_digit_is_not_a_meter_boundary(monkeypatch):
    """The regression this guard exists for. An octave-transposing treble clef
    - routine in guitar notation - carries a small 8 below the staff, which is
    a digit glyph sitting AT the clef, left of the key signature. Treating any
    digit as the meter collapsed the window to nothing, so the accidental run
    came out empty and the reader returned a confident 0: a piece in E major
    silently spelled as C major and reported as glyph-decoded, high
    confidence. Only a stacked numerator/denominator pair is a meter."""
    transposing_8 = _ev("digit8", 54.0, 221.0, 60.0, 229.0)  # under the clef
    events = [_ks_clef(), transposing_8] + _ks_sharps(4) + _ks_meter()
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths == 4, f"transposing clef digit swallowed the key signature: {why}"


def test_a_stray_numeral_between_accidentals_does_not_truncate_the_run(monkeypatch):
    """Same failure in a different disguise: a lone digit part-way through the
    key signature used to end it, reading four sharps as two."""
    stray = _ev("digit3", 83.0, 221.0, 88.0, 229.0)
    events = [_ks_clef(), stray] + _ks_sharps(4) + _ks_meter()
    fifths, _why = _decode_ks(events, monkeypatch)
    assert fifths == 4


def test_no_printed_meter_declines_rather_than_answering_zero(monkeypatch):
    """Without the meter as a right-hand boundary there is nothing separating a
    key signature from an accidental on the first note, so the honest answer is
    "not detected" - not C major."""
    events = [_ks_clef()] + _ks_sharps(1)
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths is None
    assert "no meter is printed" in why


def test_a_lone_meter_digit_is_not_enough_to_establish_the_window(monkeypatch):
    events = [_ks_clef()] + _ks_sharps(2) + [_ev("digit4", 110.0, 200.0, 117.0, 209.0)]
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths is None
    assert "no meter is printed" in why


def test_a_common_time_symbol_is_a_valid_meter_boundary(monkeypatch):
    events = [_ks_clef()] + _ks_sharps(2) + [_ev("common_time", 110.0, 203.0, 118.0, 217.0)]
    fifths, _why = _decode_ks(events, monkeypatch)
    assert fifths == 2


def test_an_empty_key_signature_is_zero_not_a_failure(monkeypatch):
    """C major and A minor really do print no accidentals, and once the window
    is established that is an answer rather than a miss."""
    events = [_ks_clef()] + _ks_meter()
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths == 0
    assert "no accidentals" in why


def test_a_meter_left_of_the_clef_declines_instead_of_reading_zero(monkeypatch):
    """A degenerate window holds no accidentals for the same reason an empty
    key signature does. The two must not come back as the same answer."""
    events = [_ks_clef(x0=90.0)] + _ks_sharps(2, x0=70.0) + _ks_meter(x0=50.0)
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths is None
    assert "not to the right of the clef" in why


def test_mixed_sharps_and_flats_are_not_a_key_signature(monkeypatch):
    events = ([_ks_clef()] + _ks_sharps(1)
              + [_ev("flat", 80.0, 203.0, 85.0, 213.0)] + _ks_meter())
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths is None
    assert "both sharps and flats" in why


def test_more_accidentals_than_a_key_signature_can_hold_is_declined(monkeypatch):
    events = [_ks_clef()] + _ks_sharps(8, x0=70.0, step=4.0) + _ks_meter()
    fifths, why = _decode_ks(events, monkeypatch)
    assert fifths is None
    assert "more than a key signature can hold" in why


def test_meter_left_edge_takes_the_leftmost_stacked_pair():
    window = _ks_meter(x0=110.0) + _ks_meter(x0=200.0)
    edge, why = G._meter_left_edge(window, _KS_MID)
    assert edge == 110.0
    assert why is None


# ---------------------------------------------------------------------------
# Half rest or whole rest (finding 88)
# ---------------------------------------------------------------------------

# One synthetic notation staff: five lines 5pt apart, 200 to 220.
_REST_LINES = [200.0, 205.0, 210.0, 215.0, 220.0]
_REST_SPACING = 5.0
# Both rests are drawn half a space deep. A half rest SITS ON a line, so its
# ink is the half space above it; a whole rest HANGS BELOW one, so its ink is
# the half space below it. Depth in points at the spacing above.
_REST_DEPTH = _REST_SPACING / 2


def _half_rest_on(line_y):
    return line_y - _REST_DEPTH / 2


def _whole_rest_under(line_y):
    return line_y + _REST_DEPTH / 2


def test_a_rest_sitting_on_a_line_reads_as_a_half_and_one_hanging_below_as_whole():
    """The two engravings the single Maestro/Opus glyph has to be told apart
    by, at the two places the old nearest-line rule happened to agree: the
    half rest on the middle line, the whole rest under the line above it."""
    half, decided = G.half_or_whole_rest(
        _half_rest_on(210.0), _REST_LINES, _REST_SPACING)
    assert (half, decided) == (2.0, True)
    whole, decided = G.half_or_whole_rest(
        _whole_rest_under(205.0), _REST_LINES, _REST_SPACING)
    assert (whole, decided) == (4.0, True)


def test_a_whole_rest_hanging_below_the_staff_is_not_read_as_a_half():
    """A second voice's rests are engraved below the staff, and nearest-line
    put everything below the middle line in the "half rest" bucket: this rest
    hangs from a ledger position two spaces under the bottom line, which is
    nearest line index 4 and read as a half - losing two quarter notes of
    silence out of the bar."""
    base, decided = G.half_or_whole_rest(
        _whole_rest_under(230.0), _REST_LINES, _REST_SPACING)
    assert (base, decided) == (4.0, True)


def test_a_half_rest_above_the_staff_is_not_read_as_a_whole():
    """The same in the other direction: nearest-line called anything near the
    top two lines a whole rest, so an upper voice's half rest sitting on a
    ledger position above the staff invented two quarter notes of silence."""
    base, decided = G.half_or_whole_rest(
        _half_rest_on(195.0), _REST_LINES, _REST_SPACING)
    assert (base, decided) == (2.0, True)


def test_the_reading_holds_at_every_line_of_the_grid():
    """Parity is a property of the line GRID, not of which line: every line
    from two above the staff to two below has to give the same two answers,
    because that is the whole difference from asking which line is nearest."""
    for i in range(-2, 8):
        line = _REST_LINES[0] + i * _REST_SPACING
        assert G.half_or_whole_rest(
            _half_rest_on(line), _REST_LINES, _REST_SPACING) == (2.0, True), line
        assert G.half_or_whole_rest(
            _whole_rest_under(line), _REST_LINES, _REST_SPACING) == (4.0, True), line


def test_a_non_positive_spacing_never_reaches_the_rest_reading():
    """half_or_whole_rest guards against a zero or negative spacing, and this
    records that the guard is DEFENCE and not a live path, because a test
    asserting the guard's return value would pass whether the guard worked or
    not - nothing can call it that way.

    decode_note_events resolves every tolerance through _Tol, which floors a
    missing, zero or negative spacing to the reference value before the rest
    reading ever sees it. That floor is the reachable behaviour, so it is what
    is pinned here."""
    for bad in (0, 0.0, -1.0, None):
        assert G._Tol(bad).spacing == G.REFERENCE_STAFF_SPACING
    # ...which is why the helper's own guard is unreachable from the decode:
    # the only argument it is ever passed is a floored _Tol.spacing.
    assert _tol().spacing > 0


@pytest.mark.parametrize("yc", [210.0, 207.5])
def test_a_rest_whose_position_says_neither_is_not_given_a_reading(yc):
    """A rest whose ink centre lands ON a line, or midway between two, is not
    a quarter space off the grid either way. Both are what an unmeasurable ink
    extent looks like rather than an engraving, and both are a quarter space
    from each of the two real answers - so the reading is declined and
    reported, not rounded to whichever side of nothing it fell."""
    base, decided = G.half_or_whole_rest(yc, _REST_LINES, _REST_SPACING)
    assert decided is False
    assert base == 2.0


def _rest_glyph(ink_top, ink_bottom, x0=300.0):
    """A one-glyph rest as extract_glyph_events builds one: the trace's
    metrics box, the baseline it was drawn on, and the measured ink."""
    ev = G.GlyphEvent(
        "Maestro", 187, "rest_half_whole",
        (x0, ink_top - 8.0, x0 + 6.0, ink_top + 12.0), 0,
        baseline_y=ink_top, ink=(ink_top, ink_bottom))
    return ev


class _BarePage:
    """A page with no drawings and no text of its own - the glyph events are
    injected, and the stem/beam pass finds nothing. Weak-referenceable, which
    the per-page caches need."""


def _decode_rests(events, monkeypatch, line_ys=_REST_LINES):
    page = _BarePage()
    monkeypatch.setattr(
        G, "extract_glyph_events",
        lambda _page: G.PageGlyphs(list(events), {"Maestro": []}, [], []))
    return G.decode_note_events(page, _REST_LINES[0], _REST_LINES[-1], 40.0, 500.0,
                                line_ys, _REST_SPACING)


def test_the_decode_reads_both_rests_from_the_one_glyph(monkeypatch):
    events = [_rest_glyph(207.5, 210.0, x0=300.0),      # sits on the middle line
              _rest_glyph(205.0, 207.5, x0=400.0)]      # hangs below the one above
    notes, stats = _decode_rests(events, monkeypatch)
    assert [n.base_units for n in notes] == [2.0, 4.0]  # x order: 300 then 400
    assert [n.is_rest for n in notes] == [True, True]
    assert stats["undecided_rests"] == 0


def test_a_rest_whose_outline_could_not_be_read_is_counted_not_guessed(monkeypatch):
    """Without the outline a glyph's position falls back to the baseline it was
    drawn on, and for this glyph the baseline sits ON the line grid in every
    calibrated font - an offset of zero, which is not evidence for either
    reading. It is read as the commoner of the two and SAID to have been,
    because being wrong here is a twofold error in one rest's duration."""
    ev = G.GlyphEvent("Maestro", 187, "rest_half_whole",
                      (300.0, 199.5, 306.0, 219.5), 0, baseline_y=210.0)
    assert not ev.ink_measured
    notes, stats = _decode_rests([ev], monkeypatch)
    assert [n.base_units for n in notes] == [2.0]
    assert stats["undecided_rests"] == 1


def test_a_staff_with_no_detected_lines_decodes_undecided_rather_than_failing(monkeypatch):
    """There is no grid to measure the rest's parity against. This is the
    reachable route into that: decode_note_events tolerates an empty line list
    the way the spacing helper beside it does, and the rest reading used to
    raise on it - `min(range(0))` - so a staff whose lines were not detected
    failed the whole page instead of degrading to a counted guess."""
    notes, stats = _decode_rests([_rest_glyph(205.0, 207.5)], monkeypatch, line_ys=[])
    assert [n.base_units for n in notes] == [2.0]
    assert stats["undecided_rests"] == 1


# ---------------------------------------------------------------------------
# A flag is joined to its stem's tip, not centred on it (finding 88)
# ---------------------------------------------------------------------------


def _flag_glyph(category, ink_top, ink_bottom, x=200.0, measured=True):
    """A flag as it is really drawn: ink reaching a long way DOWN from the
    stem tip it is joined to, and a metrics box that says nothing about
    either."""
    bbox = (x - 1.0, ink_top - 8.0, x + 5.0, ink_top + 12.0)
    return G.GlyphEvent("Leland", 40, category, bbox, 0xE244,
                        baseline_y=ink_top,
                        ink=(ink_top, ink_bottom) if measured else None)


def test_a_flag_counts_when_the_stem_tip_is_inside_its_ink():
    """A 32nd's three hooks reach two staff spaces below the tip they hang
    from, so the flag's ink CENTRE is nowhere near the stem end - which is why
    a centre-distance test dropped it and read the note as a quarter, a
    32-fold error. The tip is inside the ink, and that is the test."""
    tol = _tol()
    tip = 100.0
    flag = _flag_glyph("flag32", tip, tip + 4 * REF)  # ink centre 2 spaces down
    assert abs(flag.yc - tip) > tol.flag_y_tol, "a centre test would refuse this"
    assert G._at_stem_end(flag, tip, tol)
    stem = G.Stem(200.0, tip, tip + 6 * REF)
    assert G._flag_count_near([flag], [flag.xc], stem, stem.y1, tol) == 3


def test_a_flag_on_another_voices_stem_is_not_counted():
    """The reason the y test is there at all: two voices' stems can pass the
    same x, and taking the other one's hook turns a quarter into a sixteenth.
    Containment is tighter than the centre window it replaces, not looser."""
    tol = _tol()
    flag = _flag_glyph("flag16", 100.0, 100.0 + 2 * REF)
    far_tip = 100.0 + 6 * REF
    assert not G._at_stem_end(flag, far_tip, tol)
    stem = G.Stem(200.0, far_tip, far_tip + 6 * REF)
    assert G._flag_count_near([flag], [flag.xc], stem, stem.y1, tol) == 0


def test_a_flag_with_no_measured_ink_falls_back_to_the_centre_window():
    tol = _tol()
    flag = _flag_glyph("flag8", 100.0, 100.0 + 2 * REF, measured=False)
    assert not flag.ink_measured
    assert G._at_stem_end(flag, flag.yc + tol.flag_y_tol * 0.9, tol)
    assert not G._at_stem_end(flag, flag.yc + tol.flag_y_tol * 1.1, tol)


# ---------------------------------------------------------------------------
# Which note an augmentation dot belongs to (finding 88)
# ---------------------------------------------------------------------------

# A dot always sits in the middle of a space, so relative to its own note it
# is at the note's level, half a space above it, or half a space below it -
# and the two neighbouring readings are exactly equidistant. These are the two
# real engravings from the library that a nearest-distance test gets wrong in
# opposite directions.


def _dotted(y, x0=96.0):
    return _ev("notehead_half", x0, y - 3.0, x0 + 7.0, y + 3.0)


def _dot_at(y, x0=105.0):
    return _ev("dot", x0, y - 1.0, x0 + 2.0, y + 1.0)


def test_a_chords_stacked_dots_go_one_to_each_notehead():
    """Two chord noteheads a space apart, each on a line, each with its dot
    raised into the space above it. The lower dot is exactly as far from the
    upper notehead as from its own, and picking the wrong one gave the upper
    note TWO vertically stacked dots - which is not what a double dot is -
    while the lower note lost half its length."""
    tol = _tol(REF)
    upper = _dotted(200.0)
    lower = _dotted(200.0 + REF)
    dots = [_dot_at(200.0 - REF / 2), _dot_at(200.0 + REF / 2)]
    counts = G._assign_dots([upper, lower], dots, tol)
    assert counts[id(upper)] == 1
    assert counts[id(lower)] == 1


def test_a_dot_in_the_space_below_its_note_still_belongs_to_it():
    """The lower voice's dotted whole note, whose dot is in the space BELOW it
    because the space above is the upper voice's. Refusing that reading
    dropped the dot and left the bar short by a third of its length."""
    tol = _tol(REF)
    head = _dotted(200.0)
    counts = G._assign_dots([head], [_dot_at(200.0 + REF / 2)], tol)
    assert counts[id(head)] == 1


def test_a_dot_a_whole_space_from_every_note_belongs_to_none_of_them():
    tol = _tol(REF)
    head = _dotted(200.0)
    counts = G._assign_dots([head], [_dot_at(200.0 + REF)], tol)
    assert sum(counts.values()) == 0


# ---------------------------------------------------------------------------
# A glyph's position is its ink, not its metrics box (finding 88)
# ---------------------------------------------------------------------------


def _off_grid(y, staff):
    """How far this y is from the staff's own half-space grid, in spaces.

    Notation puts every notehead either ON a line or centred in a space, so a
    notehead's real vertical position is always a multiple of half a staff
    space from the top line - which makes the distance from that grid a
    measurement of the reading itself, with no ground truth needed."""
    steps = (y - staff.line_ys[0]) / staff.spacing * 2
    return abs(steps - round(steps)) / 2


def test_glyph_positions_land_on_the_staffs_own_grid(zanarkand_pdf):
    """The defect behind finding 88, measured in the domain that shows it.

    The box the text trace reports is metrics-based: its top and bottom are
    the font's ascender and descender, so its centre is a fixed distance from
    the BASELINE rather than the middle of the shape - 0.39 of a staff space
    for Maestro. Against another glyph that cancels, which is why every
    duration still decoded; against the staff it does not, and the reading of
    a rest as a half or a whole turns on exactly this.

    So: read a real Maestro score and check that every notehead's position
    lands on the staff's own grid, and that the metrics-box centre does not.
    """
    import fitz

    doc = fitz.open(zanarkand_pdf)
    ink, metrics = [], []
    try:
        for page in doc:
            staves, _anomalies = tabextract._detect_staves(page)
            glyphs = G.extract_glyph_events(page)
            for staff in [s for s in staves if s.kind == "standard"]:
                pad = (staff.bottom - staff.top) * 1.6
                for ev in glyphs.events:
                    if ev.category not in G.NOTEHEAD_CATS:
                        continue
                    if not staff.top - pad <= ev.yc <= staff.bottom + pad:
                        continue
                    assert ev.ink_measured
                    ink.append(_off_grid(ev.yc, staff))
                    metrics.append(_off_grid((ev.y0 + ev.y1) / 2, staff))
    finally:
        doc.close()

    assert len(ink) > 300, f"only {len(ink)} noteheads - the file did not decode"
    assert max(ink) < 0.03, f"worst notehead sits {max(ink):.3f} spaces off the grid"
    # ...and the box centre this replaced is off it by about a fifth of a space
    # everywhere. (0.39 of a space, folded onto a grid whose step is half a
    # space, is 0.11 - the bias is bigger than this number, not smaller.)
    assert min(metrics) > 0.09, f"metrics centres were on the grid after all: {min(metrics)}"
