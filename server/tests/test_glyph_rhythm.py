"""Unit tests for the glyph rhythm decoder's own primitives.

These build the geometry directly rather than going through a PDF: the
failure modes under test are all "which vector primitive did this notehead
attach to, and what did that make its duration", which is far clearer to pin
down from explicit coordinates than from a synthesised engraving.
"""
import collections
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
# A renamed Maestro resource is still recognised (issue #154)
# ---------------------------------------------------------------------------


class _FakeFontDoc:
    """Just enough of a pymupdf Document for _load_one_font /
    load_music_fonts to extract a font resource's raw bytes from."""

    def __init__(self, content_by_xref):
        self._content = content_by_xref

    def extract_font(self, xref):
        return self._content[xref]


def test_a_renamed_maestro_is_still_loaded_by_its_fingerprint(zanarkand_pdf):
    """Issue #154, on the real calibrated bytes rather than a synthesised
    outline this test's own author might get wrong: 'Rito Village - Night
    (The Legend of Zelda Breath of the Wild)' embeds its Maestro subset as a
    PDF resource literally named 'CIDFont+F1' - every embedded font in that
    file was renamed generically - and load_music_fonts used to reject it by
    that name before maestro_fingerprint_ok ever ran, so the file read zero
    glyph events on all three of its pages.

    This takes real Maestro bytes from a DIFFERENT, correctly-named library
    file (zanarkand_pdf) and hands them to _load_one_font exactly the way
    load_music_fonts now does for a resource whose basefont it does not
    recognise: named=False. A font that passes maestro_fingerprint_ok is
    Maestro whatever it is called."""
    raw = _embedded_maestro_bytes(zanarkand_pdf)
    assert raw, "expected an embedded Maestro in the reference file"

    mf, warn = G._load_one_font(_FakeFontDoc({11: raw}), xref=11, base="F1",
                                ext="ttf", named=False)
    assert mf is not None, warn
    assert warn is None
    assert mf.family == "Maestro"
    # A real calibrated GID, not a guess - proves the recovered font's GIDs
    # are readable through the SAME map a correctly-named Maestro resource
    # uses, not some parallel path that only pretends to.
    assert mf.category(157) == "notehead_filled"


def test_load_music_fonts_recognises_a_renamed_maestro_resource(zanarkand_pdf):
    """The same fix one level up: load_music_fonts itself, given a page
    whose only font resource is named the way Rito Village's is, must still
    find the Maestro subset in it - and file it under the RENAMED key, since
    that is the same basefont-derived name extract_glyph_events's `fname`
    will look candidates up by (see load_music_fonts' own docstring)."""
    raw = _embedded_maestro_bytes(zanarkand_pdf)
    assert raw

    doc = _FakeFontDoc({11: raw})

    class _FakePage:
        parent = doc

        def get_fonts(self, full=True):
            # (xref, ext, ftype, basefont, name, encoding, flags) - the exact
            # shape Rito Village's own PDF reports for its Maestro resource.
            return [(11, "ttf", "Type0", "CIDFont+F1", "F1", "Identity-H", 0)]

    fonts, warnings = G.load_music_fonts(doc, _FakePage())
    assert warnings == []
    assert list(fonts) == ["F1"], f"expected the renamed resource as the key, got {list(fonts)}"
    assert len(fonts["F1"]) == 1
    assert fonts["F1"][0].family == "Maestro"


def test_an_unrelated_renamed_font_is_not_mistaken_for_maestro(engraved):
    """The other half of the same fix: a TrueType font that is NOT Maestro
    under a name this decoder does not recognise must stay silently ignored,
    not warned about - see _load_one_font's docstring on why noise here would
    bury the one warning that matters. Uses a real embedded text font from a
    committed fixture (FreeSans, from notation_and_tab.pdf) rather than a
    synthesised one, so this is exercising the same code path on real bytes
    the way the Maestro-recognition tests above do."""
    import fitz

    doc = fitz.open(engraved("notation_and_tab"))
    try:
        xref = None
        for f in doc[0].get_fonts(full=True):
            if f[3].split("+")[-1] == "FreeSans":
                xref = f[0]
                break
        assert xref is not None, "expected the notation_and_tab fixture to embed FreeSans"

        mf, warn = G._load_one_font(doc, xref=xref, base="F12", ext="ttf", named=False)
        assert mf is None
        assert warn is None
    finally:
        doc.close()


def test_rito_village_night_draws_glyph_events_on_every_page(rito_village_pdf):
    """The library-gated acceptance case for issue #154: before the fix,
    'Rito Village - Night (The Legend of Zelda Breath of the Wild)' read
    ZERO glyph events on every one of its 3 pages, because its Maestro
    subset is embedded as a PDF resource named 'CIDFont+F1' and
    load_music_fonts rejected it by that name before its fingerprint was
    ever consulted - no noteheads, no rhythm, nothing.

    Pinned directly against the real file, at the fixture level: every page
    now yields a non-zero count, and the segno sign issue #134 taught this
    decoder to read (also invisible while the font was rejected) is drawn on
    the first page."""
    import fitz

    doc = fitz.open(rito_village_pdf)
    try:
        assert doc.page_count == 3, (
            "the reference score is expected to be exactly 3 pages - if this "
            "fails, the wrong file is configured as the rito_village fixture")
        counts = [len(G.extract_glyph_events(doc[pno]).events)
                  for pno in range(doc.page_count)]
        assert all(c > 0 for c in counts), (
            f"expected non-zero glyph events on every page, got {counts}")
        categories = collections.Counter(
            ev.category
            for pno in range(doc.page_count)
            for ev in G.extract_glyph_events(doc[pno]).events)
        assert categories["notehead_filled"] > 0
        assert categories["segno"] == 1
    finally:
        doc.close()


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


def _stack(stem_x, tip_y, levels, pitch, width=40.0, thickness=1.9):
    """A beam GROUP: `levels` parallel strokes starting at the stem's tip and
    stacking inward at `pitch`, drawn the way an engraver draws them. `pitch`
    is signed, so a positive one stacks toward larger y (an up-stem's group)
    and a negative one toward smaller (a down-stem's)."""
    tol = _tol()
    out = []
    for i in range(levels):
        y = tip_y + i * pitch
        beam = G._beam_from_contour(
            _quad(stem_x, y, stem_x + width, y, thickness), tol)
        assert beam is not None
        out.append(beam)
    return sorted(out, key=lambda b: b.x0)


def test_a_beam_group_is_counted_to_its_full_depth():
    """Issue #113. A beam group is a stack that starts at the stem's tip and
    grows toward the notehead, so its nth stroke is (n-1) pitches in.
    beam_y_tol is 1.17 staff spaces and the library's measured stack pitch is
    0.75, which puts the THIRD stroke at 1.5 - just outside it. Counting only
    what the window reached emitted every note under a three-stroke beam as a
    16th, at twice its written length.

    Measured on the library's own geometry: strokes at the tip, +0.75 and
    +1.5 staff spaces.
    """
    tol = _tol()
    pitch = 0.75 * REF
    # down-stem: notehead above, free end below, group stacking back up
    for levels in (1, 2, 3, 4):
        beams = _stack(100.0, 260.0, levels, -pitch)
        stem = G.Stem(100.0, 230.0, 260.0)
        assert G._beam_count_near(beams, stem, notehead_yc=230.0, tol=tol) == levels
    # and the same group under an up-stem, stacking the other way
    for levels in (1, 2, 3, 4):
        beams = _stack(100.0, 200.0, levels, pitch)
        stem = G.Stem(100.0, 200.0, 230.0)
        assert G._beam_count_near(beams, stem, notehead_yc=230.0, tol=tol) == levels


def test_a_beam_that_starts_nowhere_near_the_tip_is_still_not_this_stem_s():
    """Following a stack inward must not become "any beam over this stem".
    Two voices' beams cross each other's stems constantly, and 8,881 of the
    library's 77,047 (stem, covering beam) pairs sit more than 3.4 staff
    spaces in from the tip - a quarter note would become a 16th if any of
    them counted. The run has to be anchored at the tip to be followed at
    all."""
    tol = _tol()
    stem = G.Stem(100.0, 230.0, 260.0)
    # a lone stroke well inside the stem, with nothing at the tip
    stray = _stack(100.0, 260.0 - 3.5 * REF, 1, -0.75 * REF)
    assert G._beam_count_near(stray, stem, notehead_yc=230.0, tol=tol) == 0
    # and one that neither continues this stem's group nor sits at its tip is
    # not added to a group that IS anchored there
    anchored = _stack(100.0, 260.0, 2, -0.75 * REF)
    assert G._beam_count_near(anchored + stray, stem,
                              notehead_yc=230.0, tol=tol) == 2


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


def test_an_up_stem_is_not_measured_from_the_advance_width():
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
    # y failing its ORDERING test (not just the plausibility limit above)
    # must not couple into xspan either.
    inverted_y = G._InkBoxes(_StubFont(-120, 260, 130, -250))
    assert inverted_y.span(0) is None
    assert inverted_y.xspan(0) == (-0.120, 0.130)


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
    counts, no_cand, eliminated = G._assign_dots([upper, lower], [dot], tol)
    assert counts[id(lower)] == 1
    assert counts.get(id(upper), 0) == 0
    assert sum(counts.values()) == 1
    assert no_cand == 0 and eliminated == 0


def test_double_dot_is_still_read_when_two_dots_really_are_there():
    tol = _tol()
    head = _ev("notehead_half", 96.0, 197.0, 103.0, 203.0)  # yc 200
    d1 = _ev("dot", 104.0, 199.0, 106.0, 201.0)
    d2 = _ev("dot", 107.0, 199.0, 109.0, 201.0)
    counts, no_cand, eliminated = G._assign_dots([head], [d1, d2], tol)
    assert counts[id(head)] == 2
    assert no_cand == 0 and eliminated == 0


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
# Calibrated table completeness (issue #84): a glyph the decoder does not
# recognise must never become a confident wrong answer.
# ---------------------------------------------------------------------------


def test_opus_digit_map_covers_all_ten_digits_by_the_confirmed_naming_rule():
    """Opus names its time-signature digits "uniF03X", X the ASCII digit
    character - a rule confirmed by five real library instances (2, 3, 4, 6,
    8), not a guess. Before this fix, half the digits (0, 1, 5, 7, 9) were
    simply absent, so a Sibelius 12/8 lost its '1' and the surviving lone '2'
    read as a confident, wrong 2/8. Removing this loop's assertions and
    OPUS_NAME_MAP's five rule-derived entries reproduces exactly that."""
    for d in range(10):
        name = f"uniF03{d}"
        assert name in G.OPUS_NAME_MAP, f"{name} (digit{d}) missing from OPUS_NAME_MAP"
        assert G.OPUS_NAME_MAP[name] == f"digit{d}"


def test_opus_font_resolves_a_previously_unmapped_digit_by_gid():
    """The same lookup a real page's glyph events go through: a GID resolved
    to a glyph NAME via the font's own glyph order, then to a category via
    OPUS_NAME_MAP. tt=None mimics a resource this test builds by hand rather
    than reading a font file, per MusicFont's own fallback for a missing tt."""
    mf = G.MusicFont("Opus", xref=1, tt=None)
    mf.glyph_order = ["uniF030", "uniF031", "uniF039"]
    assert mf.category(0) == "digit0"
    assert mf.category(1) == "digit1"
    assert mf.category(2) == "digit9"


def test_opus_12_8_assembles_once_digit1_exists():
    """End to end for the Sibelius half of issue #84: the PUA names Opus
    actually embeds, resolved through MusicFont.category exactly as
    extract_glyph_events would, stacked into a numerator and read as a
    meter. Before uniF031 was mapped, event two below had category None -
    invisible to _stacked_digit_pairs - so only the '2' survived as the
    numerator and this returned (2, 8), not (12, 8)."""
    mf = G.MusicFont("Opus", xref=1, tt=None)
    mf.glyph_order = ["uniF031", "uniF032", "uniF038"]
    mid = 224.0
    window = [
        _ev(mf.category(0), 60.0, 212.0, 66.0, 220.0, family="Opus"),   # '1'
        _ev(mf.category(1), 66.2, 212.0, 74.0, 220.0, family="Opus"),   # '2'
        _ev(mf.category(2), 62.0, 226.0, 70.0, 234.0, family="Opus"),   # '8'
    ]
    assert None not in [e.category for e in window]
    ts, reason = G._signature_from_window(window, mid)
    assert ts == (12, 8)


def test_opus_flag16_and_rests_remain_an_acknowledged_gap():
    """The coverage limit stated in OPUS_NAME_MAP's own comment: a
    full-library rescan found no Opus resource filling a second flag hook or
    a second/third rest shape, so none is mapped - guessing a PUA name risks
    colliding with some other glyph's real meaning. This pins that the gap is
    still there rather than silently guessed shut; closing it for real needs
    a library sample or another way to confirm the name (see issue #84)."""
    assert "flag16" not in G.OPUS_NAME_MAP.values()
    assert "rest16" not in G.OPUS_NAME_MAP.values()
    assert "rest32" not in G.OPUS_NAME_MAP.values()


def test_maestro_digit0_flag32_and_rests_remain_an_acknowledged_gap():
    """The Maestro-side twin of the Opus test above. A Maestro GID carries no
    naming rule to extrapolate from (unlike Opus's uniF03X), so an entry
    absent from every library sample cannot be rule-derived - only guessed,
    which this table's own discipline refuses to do. This is the residual
    gap the module docstring now states plainly: a Finale 10/8 still decodes
    as a confident (1, 8), because the '0' glyph resolves to no category and
    is invisible to the digit clustering rather than blocking it."""
    assert "digit0" not in G.MAESTRO_GID_MAP.values()
    assert "flag32" not in G.MAESTRO_GID_MAP.values()
    assert "rest16" not in G.MAESTRO_GID_MAP.values()
    assert "rest32" not in G.MAESTRO_GID_MAP.values()


def test_a_finale_ten_eight_still_silently_loses_its_zero():
    """Documents, rather than fixes, the residual Maestro gap: the digit0 GID
    genuinely has no library evidence to confirm (see MAESTRO_GID_MAP's
    comment), so this reproduces the exact wrong-not-refused shape the
    review on issue #84 measured, using the same category-level mechanism
    test_opus_12_8_assembles_once_digit1_exists proves fixed for Opus. If
    this ever starts returning (10, 8) or None, MAESTRO_GID_MAP grew a real,
    library-verified digit0 entry and this test's docstring is stale, not
    the assertion."""
    mid = 224.0
    window = [
        _ev("digit1", 60.0, 212.0, 66.0, 220.0),
        _ev(None, 66.2, 212.0, 74.0, 220.0),         # the unmapped '0'
        _ev("digit8", 62.0, 226.0, 70.0, 234.0),
    ]
    ts, reason = G._signature_from_window(window, mid)
    # The wrong, confident answer issue #84 measured - not the desired one.
    # A future fix (refusing whenever an uncategorised glyph sits among the
    # digits that WERE read, per the issue's review) should change this to
    # (None, ...); until then this pins the actual, documented behaviour.
    assert ts == (1, 8), (
        f"expected the documented (1, 8) mis-read - got {ts!r} ({reason!r}); "
        "if this changed, MAESTRO_GID_MAP or the refusal behaviour moved and "
        "this test's docstring needs updating too")


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


def test_the_chord_threading_lookup_also_narrows_on_opus(monkeypatch):
    """The chord-threading branch of decode_note_events - reached when the
    stem-END lookup beside it finds nothing, for an inner or far member of a
    chord - has to use the SAME ink edges as that lookup (see its "Same
    edges" comment). Using the metrics box there instead reopens Opus's side
    bearing (GlyphEvent.stem_edges / the class docstring): the box overhangs
    the ink by 0.324 staff spaces on the right, which can only WIDEN this
    window, so a stem too far away to thread onto by the ink measurement can
    wrongly qualify by the box one.

    This never fires against the current library - the box and the ink agree
    on every chord thread it holds today, which is exactly why it needs a
    synthetic case to stay covered: a stem placed so its distance from the
    ink edge just fails stem_x_tol while its distance from the wider box
    edge does not."""
    tol = _tol(spacing=5.0)
    ink_x0, ink_x1 = 100.0, 110.0
    box_x1 = ink_x1 + 0.324 * tol.spacing  # Opus's right-side bearing
    yc = 115.0
    ev = G.GlyphEvent("Opus", 210, "notehead_filled",
                      (ink_x0, yc - 10.0, box_x1, yc + 10.0), 0,
                      baseline_y=yc, ink=(yc - 2.0, yc + 2.0),
                      ink_x=(ink_x0, ink_x1))
    assert ev.stem_edges == (ink_x0, ink_x1)

    # A chord's shared stem: its own end is nowhere near this notehead (dy is
    # huge, so the stem-END lookup finds nothing and this falls to the
    # threading branch below), and it spans yc. x-wise it clears the box
    # window but not the ink one. y0/y1 are chosen so the stem's overhang
    # also agrees with which side of the notehead it sits on (up-stem, on
    # the right) - otherwise _assign_stem_directions would drop the
    # attachment for a reason unrelated to the one under test here.
    stem = G.Stem(x=114.5, y0=90.0, y1=130.0)
    assert min(abs(stem.x - ink_x0), abs(stem.x - ink_x1)) > tol.stem_x_tol
    assert min(abs(stem.x - ev.x0), abs(stem.x - ev.x1)) <= tol.stem_x_tol

    page = _BarePage()
    monkeypatch.setattr(
        G, "extract_glyph_events",
        lambda _page: G.PageGlyphs([ev], {"Opus": []}, [], []))
    monkeypatch.setattr(
        G, "extract_stems_beams_curves",
        lambda *a, **k: ([stem], [], []))
    notes, _stats = G.decode_note_events(
        page, 100.0, 120.0, 50.0, 150.0, [100.0, 105.0, 110.0, 115.0, 120.0],
        tol.spacing)
    assert len(notes) == 1
    assert notes[0].stem_key is None, (
        "the ink-edge window must not thread this notehead onto a stem the "
        "wider metrics box would have allowed")



# ---------------------------------------------------------------------------
# Coincident duplicate noteheads: a unison shared by two voices (issue #116)
# ---------------------------------------------------------------------------


def _coincident_pair(ink_x0=100.0, ink_x1=106.0, yc=115.0):
    """Two GlyphEvents identical in every field decode_note_events groups
    coincident duplicates on (family, gid, x0, y0) - the same glyph, drawn
    twice at the identical position, which is how a unison shared by two
    voices is engraved."""
    bbox = (ink_x0, yc - 10.0, ink_x1, yc + 10.0)
    kwargs = dict(baseline_y=yc, ink=(yc - 3.0, yc + 3.0), ink_x=(ink_x0, ink_x1))
    a = G.GlyphEvent("Maestro", 210, "notehead_filled", bbox, 0, **kwargs)
    b = G.GlyphEvent("Maestro", 210, "notehead_filled", bbox, 0, **kwargs)
    return a, b


def test_a_coincident_pair_with_two_candidate_stems_binds_one_copy_to_each(monkeypatch):
    """The fix, pinned at the decode level: for a coincident pair with two
    opposing candidate stems - one at the head's right edge overhanging
    upward, one at its left edge overhanging downward, the majority shape
    measured across the library - the two emitted NoteEvents must not share
    a stem_key. RED before this fix: _best_stem is a pure function of
    coordinates, so both copies always ranked the same stem best and the
    other stem's notehead was silently lost."""
    ink_x0, ink_x1, yc = 100.0, 106.0, 115.0
    up = G.Stem(x=106.1, y0=95.0, y1=115.3)      # right edge, tip above
    down = G.Stem(x=99.9, y0=114.7, y1=135.0)    # left edge, tip below
    a, b = _coincident_pair(ink_x0, ink_x1, yc)
    page = _BarePage()
    monkeypatch.setattr(
        G, "extract_glyph_events",
        lambda _page: G.PageGlyphs([a, b], {"Maestro": []}, [], []))
    monkeypatch.setattr(
        G, "extract_stems_beams_curves",
        lambda *a2, **k: ([down, up], [], []))
    notes, stats = G.decode_note_events(
        page, 100.0, 120.0, 50.0, 150.0, [100.0, 105.0, 110.0, 115.0, 120.0])
    assert len(notes) == 2
    assert notes[0].stem_key is not None and notes[1].stem_key is not None
    assert notes[0].stem_key != notes[1].stem_key, (
        "each copy of the coincident pair must hang off its OWN stem")
    assert {notes[0].stem_key, notes[1].stem_key} == {G._stem_key(up), G._stem_key(down)}
    assert stats["coincident_split_pairs"] == 1
    assert stats["coincident_unsplit_pairs"] == 0


def test_a_coincident_pair_with_only_one_candidate_stem_stays_bound_and_says_so(monkeypatch):
    """The residue (issue #116's "30 single-candidate-stem pairs"): where
    only ONE stem is anywhere near a coincident pair, nothing here can tell
    the two copies apart, so they stay bound to it exactly as an unmodified
    single-stem lookup would leave them - but that must be COUNTED rather
    than silently doubling one voice's note into two same-voice notes."""
    ink_x0, ink_x1, yc = 100.0, 106.0, 115.0
    up = G.Stem(x=106.1, y0=95.0, y1=115.3)
    a, b = _coincident_pair(ink_x0, ink_x1, yc)
    page = _BarePage()
    monkeypatch.setattr(
        G, "extract_glyph_events",
        lambda _page: G.PageGlyphs([a, b], {"Maestro": []}, [], []))
    monkeypatch.setattr(
        G, "extract_stems_beams_curves",
        lambda *a2, **k: ([up], [], []))
    notes, stats = G.decode_note_events(
        page, 100.0, 120.0, 50.0, 150.0, [100.0, 105.0, 110.0, 115.0, 120.0])
    assert len(notes) == 2
    assert notes[0].stem_key == notes[1].stem_key == G._stem_key(up)
    assert stats["coincident_unsplit_pairs"] == 1
    assert stats["coincident_split_pairs"] == 0


def test_a_runner_up_stem_at_a_different_onset_is_not_this_pairs_other_voice(monkeypatch):
    """A unison is two voices sounding the SAME PITCH AT THE SAME MOMENT, so
    a geometrically close second candidate stem is not enough on its own -
    it has to stand at the SAME onset as the winner. Measured on Spanish
    Romance and The Cosmic Wheel: a coincident pair's runner-up candidate can
    be a real, OTHER note's own stem - there, a bass note written far below
    the staff whose long stem the vector pass splits into abutting segments,
    one of which lands in the pair's search window at the SAME x as the
    bass's own (correctly resolved) segment. Binding the pair's second copy
    to it does not recover a lost voice; it invents a note at the bass's
    time, not the pair's. Here a separate real notehead, far from the
    coincident pair in y, has its OWN best stem at the SAME x as the pair's
    geometric runner-up (simulating that split-stem shape without needing
    two Stem objects to literally be one printed line) - the runner-up must
    be rejected and the pair must fall through to the unsplit path exactly
    as if only one candidate had ever existed.

    RED without the onset guard: remove it (accept the first geometrically
    close candidate regardless of who else's onset it belongs to) and this
    pair splits - the fixture stays green because unison_voices' two voices
    genuinely share an onset and nothing else on that page claims either
    stem, so only a case built specifically to have a foreign claim like
    this one can tell the guard apart from no guard at all."""
    ink_x0, ink_x1, yc = 100.0, 106.0, 115.0
    # up is unambiguously the closer (winning) candidate - tighter on both
    # axes than runner - so which one ranks first is not left to a tie.
    up = G.Stem(x=106.05, y0=95.0, y1=115.1)          # winner: the pair's own up-stem
    runner = G.Stem(x=99.9, y0=114.7, y1=135.0)       # geometrically valid 2nd candidate
    # A real, distant note (a different beat entirely - yc far below the
    # pair) whose own best stem sits at the SAME x as `runner`, standing in
    # for the abutting segment of one long printed stem line.
    claim_stem = G.Stem(x=99.9, y0=180.0, y1=200.0)
    other = G.GlyphEvent("Maestro", 210, "notehead_filled",
                         (93.0, yc + 75.0, 99.9, yc + 95.0), 0,
                         baseline_y=199.0, ink=(196.5, 201.5), ink_x=(93.0, 99.9))
    a, b = _coincident_pair(ink_x0, ink_x1, yc)
    page = _BarePage()
    monkeypatch.setattr(
        G, "extract_glyph_events",
        lambda _page: G.PageGlyphs([a, b, other], {"Maestro": []}, [], []))
    # stem_xs must be sorted ascending (see _bounds) - runner and claim_stem
    # share an x, so sort explicitly rather than hand-order them.
    monkeypatch.setattr(
        G, "extract_stems_beams_curves",
        lambda *a2, **k: (sorted([runner, up, claim_stem], key=lambda s: s.x), [], []))
    notes, stats = G.decode_note_events(
        page, 100.0, 220.0, 50.0, 150.0, [100.0, 105.0, 110.0, 115.0, 120.0])
    pair_notes = [n for n in notes if n.x == a.xc]
    assert len(pair_notes) == 2
    assert pair_notes[0].stem_key == pair_notes[1].stem_key == G._stem_key(up), (
        "the runner-up belongs to the other note's onset and must be refused")
    assert stats["coincident_unsplit_pairs"] == 1
    assert stats["coincident_split_pairs"] == 0


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
# Time signature - the notehead/rest clamp (F2)
# ---------------------------------------------------------------------------


def _decode_ts(events, monkeypatch):
    """Run decode_time_signature over a synthetic glyph set, on the same
    staff geometry the key-signature tests use."""
    page = object()
    monkeypatch.setattr(
        G, "extract_glyph_events",
        lambda _page: G.PageGlyphs(list(events), {"Opus": []}, [], []))
    return G.decode_time_signature(page, _KS_TOP, _KS_BOTTOM, 48.0, 5.0)


def test_a_stray_digit_pair_beyond_a_ledger_line_note_is_refused(monkeypatch):
    """The notehead/rest clamp used to be read from the same +-1 staff-space
    band as the accidentals and the meter's own digits, so it never saw a
    note sitting on a ledger line - guitar's open low strings on a
    treble-8vb staff routinely do (see _SOUNDING_BAND_SPACINGS). Blind to
    that note, the old window kept reaching past it and could read a stray
    digit pair beyond it as a confident meter. The clamp's own band is wider
    now, so the same note still stops the window even though it never enters
    the staff-height band the digits themselves are read from.

    Both glyphs sit well inside the FLAT reach (8.8 spacings = 44pt past
    staff_x0=48, i.e. up to x=92) on purpose: if the clamp were not doing
    anything, the flat window alone would already reach the stray pair and
    accept it, which would make this test pass for the wrong reason."""
    ledger_note = _ev("notehead_filled", 70.0, 228.0, 76.0, 232.0)  # yc=230, 2.0 spacings below
    stray_pair = _ks_meter(x0=80.0)  # further right than the note, still inside the flat reach
    events = [_ks_clef(), ledger_note] + stray_pair
    ts, why = _decode_ts(events, monkeypatch)
    assert ts is None, (ts, why)


def test_a_meter_before_a_ledger_line_note_is_still_read(monkeypatch):
    """The other direction: widening the clamp's own band must not cost a
    meter that genuinely is the first thing on the staff. A note past it -
    even one deep enough on a ledger line to need the wider band to be seen
    at all - changes nothing, because the window never reaches that far."""
    meter = _ks_meter(x0=70.0)
    ledger_note = _ev("notehead_filled", 90.0, 228.0, 96.0, 232.0)  # after the meter
    events = [_ks_clef()] + meter + [ledger_note]
    ts, why = _decode_ts(events, monkeypatch)
    assert ts == (4, 4), why


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


def test_the_parity_reading_is_confidently_wrong_at_an_odd_half_space():
    """The limit of the rule, recorded rather than glossed over. Parity is
    measured modulo one staff space, so displacing a rest by an odd number of
    half spaces puts it exactly where the OTHER rest would sit and it is read
    as that other rest, with decided=True - a twofold duration error carrying
    no warning. Nothing in the three arguments can tell the cases apart.

    No engraving in the library needs the guard this cannot provide: every
    one-glyph rest the decode reads lands within 0.235-0.266 of a space of a
    line, which is the window the rule is calibrated for. This test exists so
    that stays a known limit instead of becoming a surprise."""
    on_line = _half_rest_on(_REST_LINES[2])
    assert G.half_or_whole_rest(on_line, _REST_LINES, _REST_SPACING) == (2.0, True)
    shifted = on_line + _REST_SPACING / 2
    assert G.half_or_whole_rest(shifted, _REST_LINES, _REST_SPACING) == (4.0, True)
    # ...and a whole space back is the original answer again, which is what
    # "holds at every line of the grid" means.
    assert G.half_or_whole_rest(
        on_line + _REST_SPACING, _REST_LINES, _REST_SPACING) == (2.0, True)


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
# A notehead with no stem has its duration floored, and says so (#115)
# ---------------------------------------------------------------------------


def _head_glyph(category, yc=207.5, x0=300.0):
    """A notehead as extract_glyph_events builds one, with its outline read."""
    return G.GlyphEvent("Maestro", 207, category,
                        (x0, yc - 4.0, x0 + 7.0, yc + 4.0), 0,
                        baseline_y=yc, ink=(yc - 2.5, yc + 2.5))


def test_a_filled_notehead_with_no_stem_is_counted_not_just_floored(monkeypatch):
    """A filled notehead can be a quarter or anything shorter, and the flag or
    beam that says which hangs off its stem. With no stem there is nothing to
    count, so it goes out at the LONGEST of the candidate readings - which
    means it reads long and overfills its bar. Emitting that is sometimes
    unavoidable; not counting it is what let a score be built on floored
    durations and still report its rhythm as read from the glyphs."""
    notes, stats = _decode_rests([_head_glyph("notehead_filled")], monkeypatch)
    assert [n.base_units for n in notes] == [1.0]
    assert [n.flags for n in notes] == [0], "no stem, so no flag could be counted"
    assert notes[0].stem_key is None
    assert stats["no_stem_noteheads"] == 1


def test_a_stemless_notehead_that_cannot_carry_a_flag_is_not_counted(monkeypatch):
    """A half or whole notehead's value is settled by the head alone - neither
    shape takes a flag or a beam in any notation - so a missing stem costs the
    voice signal and nothing about the duration. Counting those here would
    inflate the figure with notes whose durations were read correctly, and the
    figure is only worth stating if it means what it says."""
    notes, stats = _decode_rests(
        [_head_glyph("notehead_half", x0=300.0),
         _head_glyph("notehead_whole", x0=400.0)], monkeypatch)
    assert [n.base_units for n in notes] == [2.0, 4.0]
    assert all(n.stem_key is None for n in notes), "no stems on this page at all"
    assert stats["no_stem_noteheads"] == 0


def test_the_stemless_count_is_zero_when_every_head_found_its_stem(zanarkand_pdf):
    """The counter has to be able to report nothing, or it says nothing. This
    score's noteheads all attach, so a counter wired to fire on every filled
    head - or on the wrong branch - shows up here rather than in the aggregate."""
    result = tabextract.extract(str(zanarkand_pdf))
    assert result.notes_no_stem == 0
    assert result.staves_no_stem == 0
    assert result.rhythm_provenance == {tabextract.PROV_GLYPHS: 10}
    assert not any("no stem this decoder could find" in w for w in result.warnings)


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
    counts, no_cand, eliminated = G._assign_dots([upper, lower], dots, tol)
    assert counts[id(upper)] == 1
    assert counts[id(lower)] == 1
    assert no_cand == 0 and eliminated == 0


def test_a_dot_in_the_space_below_its_note_still_belongs_to_it():
    """The lower voice's dotted whole note, whose dot is in the space BELOW it
    because the space above is the upper voice's. Refusing that reading
    dropped the dot and left the bar short by a third of its length."""
    tol = _tol(REF)
    head = _dotted(200.0)
    counts, no_cand, eliminated = G._assign_dots([head], [_dot_at(200.0 + REF / 2)], tol)
    assert counts[id(head)] == 1
    assert no_cand == 0 and eliminated == 0


def test_a_dot_a_whole_space_from_every_note_belongs_to_none_of_them():
    """No notehead ever fits this offset, so this is the no-candidate half of
    the split, not the eliminated half - see _assign_dots."""
    tol = _tol(REF)
    head = _dotted(200.0)
    counts, no_cand, eliminated = G._assign_dots([head], [_dot_at(200.0 + REF)], tol)
    assert sum(counts.values()) == 0
    assert no_cand == 1
    assert eliminated == 0


def test_an_eliminated_dot_is_distinct_from_one_with_no_candidate():
    """A dot that DOES reach a candidate, and then loses it during
    elimination, is a different fact from a dot that never reached one at
    all: a notehead WAS in reach; it had simply already been given its own
    dot at a different tier.

    One owner, two dots, each the sole candidate for its OWN dot event before
    anything commits: one dot fits only the note's own space (tier 0), the
    other fits only the space above it (tier 1). Both look forced, so the
    first one processed commits the owner to tier 0 - and the
    tier-exclusivity check then drops the second dot's only candidate, since
    that owner can no longer supply a different tier. That drop is what
    unassigned_eliminated counts."""
    tol = _tol(REF)
    head = _dotted(200.0)
    own_dot = _dot_at(200.0)              # tier 0: the note's own space
    above_dot = _dot_at(200.0 - REF / 2)  # tier 1: the space above it
    counts, no_cand, eliminated = G._assign_dots([head], [own_dot, above_dot], tol)
    assert counts[id(head)] == 1, "the note keeps its own, unambiguous dot"
    assert no_cand == 0, "the second dot DID reach a candidate"
    assert eliminated == 1, "...and lost it to the tier-exclusivity lock, not to having none"


def test_a_three_note_chord_gives_one_dot_to_each_notehead():
    """A three-note chord, geometry taken from "Courage" (Final Fantasy XVI)
    rather than invented: three half notes a third and a fourth apart, each
    with its own raised dot. The middle note's own dot and the bottom note's
    own dot are each unambiguous - only one note is close enough to fit
    either. The TOP note's dot is not: it fits the top note itself (the space
    BELOW it, deviation 0.0125 spaces) and it ALSO fits the middle note (the
    space ABOVE it - the same physical space, since the two notes are a
    third apart - deviation 0.0175 spaces) almost as well. Ranked in
    isolation, "space above" outranks "space below" regardless of which fits
    tighter, so this dot went to the middle note every time - leaving the
    middle note double-dotted and the top note with none.

    Fixed by refusing to let an owner already given a dot at one tier supply
    a SECOND, different tier to another dot: the middle note's OWN dot (the
    space BELOW it, which the bottom note is too far away to reach) is
    unambiguous and settles first, locking the middle note to "space below".
    That disqualifies the middle note from the top note's ambiguous dot,
    which would need "space above" from it instead, leaving the top note as
    the only owner still able to take it."""
    tol = _tol(5.125)
    top = _dotted(250.07, x0=232.19)
    middle = _dotted(255.20, x0=232.19)
    bottom = _dotted(262.89, x0=232.19)
    dots = [_dot_at(252.62, x0=238.96), _dot_at(257.75, x0=238.96),
            _dot_at(262.87, x0=238.96)]
    counts, no_cand, eliminated = G._assign_dots([top, middle, bottom], dots, tol)
    assert counts[id(top)] == 1
    assert counts[id(middle)] == 1
    assert counts[id(bottom)] == 1
    assert no_cand == 0 and eliminated == 0


# ---------------------------------------------------------------------------
# A second dot is reached from the FIRST dot, not from the notehead (#111)
# ---------------------------------------------------------------------------


def test_a_second_dot_out_of_the_noteheads_reach_is_still_that_notes():
    """Coordinates from issue #111's own measurement, in spaces: the notehead
    edge at 0, its first dot 0.695 past it, its second 1.342 - against a reach
    window of 1.17. The second dot can never be reached from the notehead, and
    widening the window to 1.35 would be reaching further than the distance to
    the next notehead along. What makes the two marks one note's is that they
    are side by side, which is what is read instead."""
    tol = _tol(REF)
    head = _dotted(200.0)                       # right edge at x=103
    first = _dot_at(200.0, x0=105.56)           # xc 106.56: 0.695 spaces past
    second = _dot_at(200.0, x0=108.88)          # xc 109.88: 1.345 spaces past
    counts, no_cand, eliminated = G._assign_dots([head], [first, second], tol)
    assert counts[id(head)] == 2, "a double dot, not a dot and an anomaly"
    assert no_cand == 0 and eliminated == 0


def test_two_dots_too_far_apart_are_not_one_notes_double_dot():
    """The other side of the same rule. Two dots at the same height that are
    NOT one dot-advance apart are two notes' dots, and grouping them would
    give one note a length it is not written with. Measured over the library,
    real second dots sit 0.5 to 0.917 spaces past the first and the nearest
    same-height dot that is another note's is 2.5 away; this pair is at 2.5."""
    tol = _tol(REF)
    head = _dotted(200.0)
    first = _dot_at(200.0, x0=105.56)
    far = _dot_at(200.0, x0=118.37)             # 2.5 spaces past the first
    counts, no_cand, eliminated = G._assign_dots([head], [first, far], tol)
    assert counts[id(head)] == 1, "only the dot this note is written with"
    assert (no_cand, eliminated) == (1, 0), "the far one reaches no notehead at all"


def test_a_repeat_barlines_dot_pair_is_not_taken_by_the_note_after_it():
    """Maestro and Opus draw a repeat barline's two dots with the very same
    glyph an augmentation dot uses (see REPEAT_DOT_CATS), so a repeat's dots
    arrive here indistinguishable from a note's except by geometry - and
    issue #138 reads them off this same stream. What keeps them apart is
    reach, and reach alone: these coordinates are the opening repeat of
    "Kaine Salvation", where the lower of the two dots sits at the exact
    height of the first chord's lowest notehead (0.003 spaces off its centre,
    a perfect tier-0 fit) and is saved from it only by sitting 2.92 spaces to
    its LEFT.

    So this is the test that says the reach window was not widened. #111 and
    #112 are both fixed by changing what reach is measured FROM; widen
    dot_x_back instead and this note swallows a repeat dot."""
    tol = _tol(5.125)
    head = _ev("notehead_half", 160.583, 145.427, 167.307, 151.427)  # yc 148.427
    upper = _ev("dot", 151.348, 142.288, 153.348, 144.288)           # yc 143.288
    lower = _ev("dot", 151.348, 147.413, 153.348, 149.413)           # yc 148.413
    counts, no_cand, eliminated = G._assign_dots([head], [upper, lower], tol)
    assert sum(counts.values()) == 0, "a repeat's dots belong to no note"
    assert (no_cand, eliminated) == (2, 0), "...and are reported, both of them"


def test_a_long_row_of_dots_is_not_one_notes_run():
    """Chaining is transitive, so without a cap a row of dots each one
    dot-advance from the last would read as one note's quintuple dot - a
    length no notation writes and no note can be given. The cap is structural
    rather than measured: no such row exists in the library, and the point is
    that if one ever appears it must not be swallowed whole. See
    _DOT_RUN_MAX."""
    tol = _tol(REF)
    head = _dotted(200.0)
    row = [_dot_at(200.0, x0=105.56 + 3.32 * k) for k in range(5)]
    counts, no_cand, eliminated = G._assign_dots([head], row, tol)
    assert counts[id(head)] <= 3, "a note takes at most a triple dot's worth"
    assert no_cand + eliminated == len(row) - counts[id(head)], \
        "and every mark not taken is reported, not dropped"


def test_two_dots_at_the_same_x_are_a_duplicate_pair_not_a_double_dot():
    """A run needs the marks to be BESIDE each other. The library also draws
    the same notehead-and-dot pair twice at identical coordinates, and reading
    those two dots as one note's double dot would double a length that is
    written once - so a gap of nothing is not a run (see _DOT_X_DUP_TOL)."""
    tol = _tol(REF)
    left = _dotted(200.0)
    right = _dotted(200.0)                      # the duplicate, same place
    dot = _dot_at(200.0, x0=105.56)
    same = _dot_at(200.0, x0=105.56)
    counts, no_cand, eliminated = G._assign_dots([left, right], [dot, same], tol)
    assert sorted(counts.values()) == [1, 1], "one dot each, not two on one"
    assert no_cand == 0 and eliminated == 0


# ---------------------------------------------------------------------------
# A displaced chord member reaches its dot from the column (#112)
# ---------------------------------------------------------------------------


# The seconds pair of the engraved seconds_interval_dots fixture, coordinates
# as measured off its second bar: staff spacing 4.975, two half-note heads
# 1.297 spaces wide whose boxes touch (the lower head's right edge is 0.101
# spaces from the upper head's left edge), the lower one 0.4985 spaces below
# the upper - a second - and the chord's two dots in one column 0.697 spaces
# past the RIGHT head's edge, which is 1.892 past the left head's own.
_SECONDS_SPACING = 4.975
_LOWER_HEAD = (172.260, 178.712, 109.489)   # x0, x1, yc
_UPPER_HEAD = (178.209, 184.661, 107.009)
_DOT_COLUMN_X = 188.127


def _head(box, y_shift=0.0):
    x0, x1, yc = box
    return _ev("notehead_half", x0, yc + y_shift - 3.0, x1, yc + y_shift + 3.0)


def _column_dot(yc):
    return _ev("dot", _DOT_COLUMN_X - 1.0, yc - 1.0, _DOT_COLUMN_X + 1.0, yc + 1.0)


# The stem the pair shares, which is the whole reason one of the two heads had
# to move off the column: it runs between the lower head's right edge and the
# upper head's left edge, and past both of them.
_SECONDS_STEM = [G.Stem(178.46, 107.0, 130.0)]


def test_a_seconds_interval_member_reaches_the_column_its_dot_sits_in():
    """Two heads a second apart cannot share a column, so the engraver moves
    one of them a whole notehead width off it - while both their dots stay in
    the chord's single dot column. The left, lower head's own dot is then 1.892
    spaces past its own right edge, against a 1.17-space window, and 0.697 past
    its partner's, which is the edge the column is actually set from.

    Only ONE dot is in the contested position here, with nothing a space below
    it, so nothing was pushed anywhere and the ordinary rules apply - see
    test_a_pushed_down_pair_gives_each_member_its_own_dot for the other
    arrangement."""
    tol = _tol(_SECONDS_SPACING)
    lower, upper = _head(_LOWER_HEAD), _head(_UPPER_HEAD)
    dots = [_column_dot(109.480), _column_dot(104.520)]
    counts, no_cand, eliminated = G._assign_dots(
        [lower, upper], dots, tol, _SECONDS_STEM)
    assert counts[id(lower)] == 1, "the head off the column still reaches its own dot"
    assert counts[id(upper)] == 1
    assert no_cand == 0 and eliminated == 0


def test_a_head_a_third_away_is_not_a_displaced_partner():
    """The extra anchor is what a SECOND forces and nothing else: two heads a
    third apart sit on one column untouched, so neither may borrow the other's
    edge to reach a dot a notehead width too far from it. Without that limit
    any tightly engraved neighbour would lend its edge to a note whose dot is
    genuinely out of reach - and be believed."""
    tol = _tol(_SECONDS_SPACING)
    # the same pair, pushed to a third apart (one whole space)
    lower = _head(_LOWER_HEAD, y_shift=+_SECONDS_SPACING / 2)
    upper = _head(_UPPER_HEAD)
    counts, no_cand, eliminated = G._assign_dots(
        [lower, upper], [_column_dot(109.480 + _SECONDS_SPACING / 2)], tol,
        _SECONDS_STEM)
    assert counts.get(id(lower), 0) == 0, "a third apart is not a displacement"
    assert (no_cand, eliminated) == (1, 0)


def test_two_notes_one_after_the_other_do_not_lend_each_other_an_anchor():
    """The same geometry a displaced pair has - boxes touching, a second
    apart, lower on the left - is also what two CONSECUTIVE notes look like
    when the engraving is tight enough. They must not exchange anchors: the
    earlier note would reach a dot belonging to the later one and take it at a
    tier of its own, which is the theft the column anchor exists to avoid
    rather than to enable.

    What separates them is the stem. A displaced pair shares one - that is why
    a head had to move at all - and here the two heads have their own stems,
    neither of which runs between them."""
    tol = _tol(_SECONDS_SPACING)
    earlier, later = _head(_LOWER_HEAD), _head(_UPPER_HEAD)
    own_stems = [G.Stem(172.10, 109.0, 130.0), G.Stem(184.80, 107.0, 130.0)]
    counts, no_cand, eliminated = G._assign_dots(
        [earlier, later], [_column_dot(109.480)], tol, own_stems)
    assert counts.get(id(earlier), 0) == 0, "the later note's dot is not this one's"
    assert counts[id(later)] == 1, "it is in the space below the note it is drawn for"
    assert (no_cand, eliminated) == (0, 0)


def test_a_pushed_down_pair_gives_each_member_its_own_dot():
    """Coordinates from "Storm's Past" (New World), rescaled to this fixture's
    column: a displaced seconds pair where BOTH members are dotted.

    Their dots cannot both be printed at the default offsets - the two heads
    are half a space apart, and there is only one space between them to put a
    dot in - so the engraver pushes the pair down a step together. The upper
    member's dot lands in the space below it, which is the space its partner
    occupies, and the lower member's own dot lands a full space below its own
    centre, out of every tier's reach.

    Read either dot alone and the answer is wrong twice over: the contested
    dot goes to the lower member (it is a perfect tier-0 fit for it) and the
    upper member - which is printed with a dot - reads bare, while the dot a
    space below is left unexplained. Read as a pair and both members get the
    one dot each that is printed for them."""
    tol = _tol(_SECONDS_SPACING)
    lower, upper = _head(_LOWER_HEAD), _head(_UPPER_HEAD)
    contested = _column_dot(109.480)                       # level with `lower`
    beneath = _column_dot(109.480 + _SECONDS_SPACING)      # a full space below
    counts, no_cand, eliminated = G._assign_dots(
        [lower, upper], [contested, beneath], tol, _SECONDS_STEM)
    assert counts[id(upper)] == 1, "the contested dot is the UPPER member's"
    assert counts[id(lower)] == 1, "and the lower member's own is the one below"
    assert (no_cand, eliminated) == (0, 0), "nothing is left over"


def test_a_dot_a_space_below_a_note_that_owns_it_is_not_a_pushed_pair():
    """The joint reading needs the whole signature. Where a notehead of the
    chord's own column sits at that lower height, the dot down there is that
    head's own at its own tier and nothing was pushed anywhere - so the pair
    is left to the ordinary rules rather than given a reading the engraving
    does not support."""
    tol = _tol(_SECONDS_SPACING)
    lower, upper = _head(_LOWER_HEAD), _head(_UPPER_HEAD)
    third = _ev("notehead_half", _UPPER_HEAD[0], 109.480 + _SECONDS_SPACING - 3.0,
                _UPPER_HEAD[1], 109.480 + _SECONDS_SPACING + 3.0)
    contested = _column_dot(109.480)
    beneath = _column_dot(109.480 + _SECONDS_SPACING)
    counts, no_cand, eliminated = G._assign_dots(
        [lower, upper, third], [contested, beneath], tol, _SECONDS_STEM)
    assert counts[id(third)] == 1, "the lower dot is that head's own"
    assert counts[id(lower)] == 1, "and the contested one the ordinary rules'"
    assert counts.get(id(upper), 0) == 0


def test_the_column_anchor_never_takes_a_dot_out_of_reach():
    """The partner's edge is ADDED to an owner's anchors, not substituted for
    its own, so a head that could already reach its dot still can whatever is
    engraved beside it. Here the lower head's dot sits at its OWN edge - 0.697
    past 178.712 - and reaching only from the partner's edge, a notehead width
    further right, would put it behind the window."""
    tol = _tol(_SECONDS_SPACING)
    lower, upper = _head(_LOWER_HEAD), _head(_UPPER_HEAD)
    own_x = _LOWER_HEAD[1] + 0.697 * _SECONDS_SPACING
    own_dot = _ev("dot", own_x - 1.0, 109.480 - 1.0, own_x + 1.0, 109.480 + 1.0)
    counts, no_cand, eliminated = G._assign_dots([lower, upper], [own_dot], tol)
    assert counts[id(lower)] == 1
    assert no_cand == 0 and eliminated == 0


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
