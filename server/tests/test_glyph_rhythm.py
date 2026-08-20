"""Unit tests for the glyph rhythm decoder's own primitives.

These build the geometry directly rather than going through a PDF: the
failure modes under test are all "which vector primitive did this notehead
attach to, and what did that make its duration", which is far clearer to pin
down from explicit coordinates than from a synthesised engraving.
"""
import io

import pytest

from fermata import glyph_rhythm as G


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
    assert big.dot_y_tol == pytest.approx(small.dot_y_tol * 10)
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
    up = G.Stem(x=50.0, y0=60.0, y1=100.0)      # tip above a notehead at y=100
    down = G.Stem(x=80.0, y0=100.0, y1=140.0)   # tip below a notehead at y=100
    notes = [_note(50.0, 100.0, "u"), _note(80.0, 100.0, "d")]
    G._assign_stem_directions(notes, {"u": up, "d": down})
    assert [n.stem_dir for n in notes] == ["up", "down"]


def test_a_chords_direction_is_not_read_off_one_of_its_noteheads():
    """A chord shares ONE stem that runs PAST all of its noteheads, so the
    end further from any given member is on the wrong side for every member
    but the outermost - reading "the free end is below me, therefore stem
    down" off the top note of an up-stemmed chord inverts it."""
    # up-stem chord: noteheads at y=100/110/120, stem from its tip at y=60
    # down to the lowest notehead at y=120.
    stem = G.Stem(x=50.0, y0=60.0, y1=120.0)
    notes = [_note(50.0, y, "c") for y in (100.0, 110.0, 120.0)]
    G._assign_stem_directions(notes, {"c": stem})
    assert [n.stem_dir for n in notes] == ["up", "up", "up"]

    # and the mirror image
    stem_d = G.Stem(x=50.0, y0=100.0, y1=160.0)
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
