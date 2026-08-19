"""
Decode note durations and time signature from vector-engraved PDF pages by
identifying the music-font glyphs actually used for noteheads, flags, rests,
dots and time-signature digits - instead of guessing from x-spacing.

Used by tabextract.extract() as the primary rhythm/time-signature source
when a tab staff is paired with a standard-notation staff drawn in a
recognised, TrueType-embedded music font; tabextract falls back to its own
spacing heuristic when that isn't the case (raster pages, CFF-flavor font
embeddings, an unrecognised font family, or a recognised family whose glyph
outlines don't match the calibrated fingerprint - see MAESTRO_GLYF_DIGESTS).

How this works (full validation detail):

  Finale exports embed a font called "Maestro" as a TrueType subset. The
  glyph *names* are stripped (post table format 3, "glyph00001" etc.) so
  they can't be read directly. BUT: across every Finale/Maestro PDF checked
  (548 distinct embedded Maestro font resources across 274 library files),
  the embedded subset is always the SAME reduced 204-slot Maestro subset,
  and a given glyph ID's outline coordinates are byte-for-byte identical
  file to file whenever that glyph is used - measured, not assumed: hashing
  the raw `glyf` bytes of every mapped GID across all 548 resources found
  36 distinct GIDs in use and ZERO GIDs with more than one distinct outline.
  So glyph ID (GID) *is* a stable key for Maestro even though the name
  table is gone.

  That measurement is also the safety check. A "Maestro" from a different
  Finale version, a different subsetting path, or another tool embedding the
  same family would keep the family NAME but need not keep the same GID
  order - and since GIDs 16-24 are the time-signature digits, a wrong GID
  order silently mis-decodes every notehead, rest, flag and digit AND can
  emit a confidently-wrong time signature. Gating on the family name alone
  cannot tell the two apart, so every Maestro resource is fingerprinted
  against MAESTRO_GLYF_DIGESTS at load time and rejected on mismatch (see
  maestro_fingerprint_ok); a rejected font is treated as unrecognised so
  tabextract's honest spacing fallback engages and says so.

  Sibelius exports embed "Opus"/"OpusSpecial"/"OpusText" as TrueType
  subsets whose post table format DOES retain names, but as PUA codepoint
  labels like "uniF0CF" rather than "noteheadBlack" - not semantically
  meaningful text, but a stable KEY (confirmed identical across 6 different
  Sibelius/Opus files sampled from the library, both Finale and Sibelius
  sub-vocabularies were visually verified by rendering every used
  glyph's actual outline to a contact-sheet PNG and eyeballing it against
  the rendered page). "OpusText" is embedded too but is NOT decoded here -
  checking its actual usage shows it holds fingering/annotation numerals
  (plain MacRomanEncoding text characters), not music-symbol glyphs, so
  there is nothing in it for this decoder to map.

  Opus subsets are minted per-resource (unlike Maestro's one fixed
  subset), so a page can embed two distinct "Opus"/"OpusSpecial" font
  resources with different glyph orders under the same family name -
  confirmed on Easy-Christmas-Songs-for-Guitar-Vol1-4.pdf, which has two
  differently-tagged OpusSpecial resources on a single page. Font
  resources are therefore tracked per xref, not collapsed to one per
  family name (see MusicFont / load_music_fonts). Because the Opus maps are
  keyed on a name that travels WITH the outline, they don't need Maestro's
  GID fingerprint - a remapped subset changes the GID, not the name.

  Every glyph->meaning mapping below was established by: (1) collecting the
  set of distinct GIDs/names actually used across a sample of library PDFs,
  (2) rendering each one's real vector outline from the embedded font
  (fontTools glyf parsing + quadratic curve flattening), (3) visually
  reading the rendered shape, and (4) cross-checking occurrence counts /
  aspect ratios / fill ratios against what the shape should look like
  (e.g. the dominant highest-frequency glyph on every Maestro page is, and
  should be, the plain filled notehead).

  Stems and beams are NOT font glyphs in either exporter - they are vector
  line/rectangle primitives (page.get_drawings()) - confirmed by inspecting
  drawings positioned exactly where a stem/beam should be. Duration is
  therefore decoded by combining: notehead glyph shape (filled/hollow/wide)
  + presence of a stem (vector) + flag glyph count at the stem's free end
  + stacked beam-rectangle count at the stem's x position + trailing
  augmentation-dot glyphs.

  Every geometric tolerance in this module is expressed in STAFF SPACES
  (the distance between two adjacent staff lines) and scaled by the staff
  actually being decoded - see _Tol. The absolute point values these
  constants replace were all calibrated against one staff size (the
  sampled library engraves its notation staves at a 5.125pt line spacing,
  measured across 254 staves), so a condensed multi-system score or a
  large-print edition silently dropped stems and beams and degraded every
  eighth and sixteenth to a quarter while still reporting high confidence.
"""
import bisect
import collections
import hashlib
import io
import math
import weakref


# fontTools is only needed to read an embedded music font's glyph order and
# outlines. It is imported LAZILY and behind a guard on purpose: this module
# is reachable from fermata.main -> api -> tabextract, so a top-level import
# turned a missing/broken fonttools install into a crash of the whole server
# at startup - taking down /api/health and plain PDF viewing, neither of
# which ever touches glyph decoding. Without it we simply report no music
# fonts, and tabextract degrades to its spacing heuristic and says so.
_TTFONT = None
_TTFONT_STATE = "unloaded"  # "unloaded" | "ok" | "missing"


def _ttfont_class():
    global _TTFONT, _TTFONT_STATE
    if _TTFONT_STATE == "unloaded":
        try:
            from fontTools.ttLib import TTFont
        except Exception:
            _TTFONT, _TTFONT_STATE = None, "missing"
        else:
            _TTFONT, _TTFONT_STATE = TTFont, "ok"
    return _TTFONT


# ---------------------------------------------------------------------------
# Calibrated glyph -> meaning tables (see module docstring for how these were
# derived; each entry was visually confirmed against a rendered outline).
# ---------------------------------------------------------------------------

# Finale "Maestro": keyed by glyph ID (GID), stable across files that use the
# same reduced-subset export pipeline - and verified per file at load time
# against MAESTRO_GLYF_DIGESTS rather than trusted on the family name alone.
MAESTRO_GID_MAP = {
    2: "sharp", 4: "simile", 13: "dot", 16: "digit1", 17: "digit2", 32: "flat_paren",
    18: "digit3", 19: "digit4", 20: "digit5", 21: "digit6", 22: "digit7",
    23: "digit8", 24: "digit9",
    29: "accent", 31: "tremolo", 40: "flag8", 44: "natural_paren",
    48: "flag16", 51: "fermata", 52: "clef", 63: "sharp_paren", 64: "flat",
    68: "trill", 71: "flag8", 75: "natural", 79: "flag16",
    84: "notehead_whole", 144: "notehead_x", 149: "rest8", 156: "rest_quarter",
    157: "notehead_filled", 171: "coda", 174: "notehead_diamond",
    177: "rest8", 187: "rest_half_whole", 199: "notehead_half",
    # digit7 (22) and digit9 (24) confirmed by rendering the actual glyph
    # outlines from real library files and eyeballing them: 22 from
    # "Moonlit Shadows (New World).pdf" (a 7/8 signature), 24 from
    # "The Butterfly (New World).pdf" (a 9/8 signature) - same visual
    # verification method the rest of this table was built with. digit0 is
    # NOT mapped: it never turned up in a scan of the whole library's
    # Maestro-subset pages (Finale only embeds glyphs actually used, and no
    # sampled piece has a time signature needing '0'), so there is no real
    # outline to confirm a GID against - guessing one would violate this
    # table's own "rendered and eyeballed" standard. A signature that would
    # need digit0 correctly falls through to "not detected" (see
    # decode_time_signature) rather than silently emitting a wrong value.
}

# Fingerprint for the calibrated Maestro subset: GID -> sha256 of that
# glyph's raw `glyf` table bytes, truncated to 32 hex chars.
#
# HOW THIS WAS DERIVED: every embedded font resource named "Maestro" in the
# library (548 resources across 274 PDFs) was extracted, its `glyf` table
# sliced per GID using the `loca` offsets, and each mapped GID's bytes
# hashed. 36 of the mapped GIDs were observed in use; NONE of them had more
# than one distinct outline across all 548 resources, which is what makes
# GID a legitimate key for this family at all. The digest below is that one
# observed outline per GID.
#
# HOW TO RECALIBRATE (after a Finale upgrade, or to admit a second export
# pipeline): run
#     python server/tools/tab_extract/maestro_fingerprint.py <library-root>
# which re-derives this table the same way and prints it ready to paste,
# along with any GID that disagreed across files. A GID that starts
# disagreeing means the subset is no longer stable and that GID must be
# dropped from MAESTRO_GID_MAP - not that the digest should be "updated" to
# whichever file was checked last.
#
# Note the subset keeps all 204 glyph slots but only fills the glyphs a
# given page actually uses (6-22 of the mapped GIDs per resource, median
# 14), so absent/empty glyphs are simply not evidence either way - see
# maestro_fingerprint_ok.
MAESTRO_GLYF_DIGESTS = {
    2: "b1a6a7f41a95299ae7e202f516bf4bc7",    # sharp
    4: "2059d6889199a9f0571c6e40fee0c290",    # simile
    13: "40ef7e3b3885505e494c4f0dec79658c",   # dot
    16: "152d31c9b5e40ab0539d160169275898",   # digit1
    17: "f9ca473a695b291e40c5bac5b7f3cfe6",   # digit2
    18: "cbced50db620d753006c042f084b5546",   # digit3
    19: "8c5ace11fbfc52c77dd81e54b38b06bb",   # digit4
    20: "72b2de92c1c416d4239d353296d9bfaa",   # digit5
    21: "ef7fec6580fcca8154d2f362144388d3",   # digit6
    22: "67600f18ae76e04fe717da268c261601",   # digit7
    23: "73b2bd7ce294af1c77aeb386a60f962a",   # digit8
    24: "6367756e6ac0cd6892dc4b6fd721fb01",   # digit9
    29: "7151fc1ceeb1aeed288e983dd5888747",   # accent
    31: "629849f3f098ce089d6650e3c60ce9ed",   # tremolo
    32: "15f7413bc8625899d004d718ad4b6f68",   # flat_paren
    40: "70dc78474f57a292667d3592396e2bfc",   # flag8
    44: "74c25bac50f95abc6209e8d41a842322",   # natural_paren
    48: "6a309bf9f214ccf8f422100d2b7ff895",   # flag16
    51: "adbf149070ffc18e23ba4fb6717e5c27",   # fermata
    52: "1fa841a0b3eb8a6673fc13d366e37df1",   # clef
    63: "bac9dfd483f772988b7e5358e6a71be4",   # sharp_paren
    64: "b00221382cd74b15334900b26553eecb",   # flat
    68: "b715d3151adf935bc682e468678fa03d",   # trill
    71: "10bb28c200130375856c77df9eb121f3",   # flag8
    75: "0673bb60e4da121a63dfa85729eb6c19",   # natural
    79: "c4359488ce017b1ad641b490da02de01",   # flag16
    84: "368cd201d95c7e04087f6149041599ad",   # notehead_whole
    144: "7fbeef923265d262e5bf99ad0bafbe2f",  # notehead_x
    149: "cf000d46da83213c7b63839dc833f1d3",  # rest8
    156: "d94168738746833369f25895b7b07c74",  # rest_quarter
    157: "edcabbe42ef4b5de459c509dc3369ad0",  # notehead_filled
    171: "d11286002e1745d8022cea364a72a9cd",  # coda
    174: "a067ef7952bb79db83022d7ad10340d7",  # notehead_diamond
    177: "ddb719cb7993fc9b6a8fcfbc24a647a4",  # rest8
    187: "9481ae81a0d54cdd4928b53fb2f5530c",  # rest_half_whole
    199: "981e44ec5ae96637cbb5d4449a3ea8b7",  # notehead_half
}

# How many mapped-and-present GIDs a Maestro resource must supply before its
# fingerprint counts as evidence at all. The library's thinnest real subset
# fills 6 of them (median 14), so 4 clears every genuine resource with
# margin while still refusing to bless a font that has almost nothing in
# common with the calibrated subset.
MAESTRO_FINGERPRINT_MIN_GLYPHS = 4

# Sibelius "Opus" / "OpusSpecial" / "OpusText": keyed by glyph NAME (the PUA
# label), stable across files even though GIDs are not (Opus subsets are
# tightly per-file, unlike Maestro's fixed-size subset).
OPUS_NAME_MAP = {
    "uniF023": "sharp", "uniF026": "clef", "uniF02E": "dot",
    "uniF032": "digit2", "uniF033": "digit3", "uniF034": "digit4",
    "uniF036": "digit6", "uniF038": "digit8", "uniF03E": "accent",
    "uniF043": "cut_time", "uniF04A": "flag8", "uniF055": "fermata",
    "uniF062": "flat", "uniF063": "common_time", "uniF065": "note_pictograph",
    "uniF068": "note_pictograph", "uniF06A": "note_pictograph",
    "uniF071": "note_pictograph", "uniF06E": "natural",
    "uniF077": "notehead_whole", "uniF0B2": "up_bow",
    "uniF0B3": "bracket", "uniF0B7": "rest_half_whole",
    "uniF0CE": "flag8_or_rest_quarter",  # disambiguated by stem proximity
    "uniF0CF": "notehead_filled", "uniF0DC": "notehead_x",
    "uniF0E4": "rest8", "uniF0EE": "rest_half_whole", "uniF0FA": "notehead_half",
}
OPUS_SPECIAL_NAME_MAP = {
    "uniF0AA": "dot", "uniF0DA": "tab_label", "uniF0A1": "tuplet_bracket",
    "uniF0A2": "tuplet_bracket", "uniF083": "down_stroke", "uniF089": "up_stroke",
    "uniF0DC": "digit8",
    "uniF0E1": "string1", "uniF0E2": "string2", "uniF0E3": "string3",
    "uniF0E4": "string4", "uniF0E5": "string5", "uniF0E6": "string6",
}

DIGIT_CATS = {f"digit{d}": d for d in range(10)}

NOTEHEAD_CATS = {"notehead_filled", "notehead_half", "notehead_whole", "notehead_x", "notehead_diamond"}
# How many duration halvings each flag glyph contributes. Opus draws an
# unbeamed eighth's hook with the same glyph it uses for a quarter rest
# (uniF0CE), so that category has to be countable as a flag too: the rest
# branch of decode_note_events skips it as "already counted via the
# notehead's stem" whenever a stem is next to it, and if it were not in
# here nothing would ever actually count it - every unbeamed Sibelius eighth
# would decode as a quarter. Its presence here is safe because
# _flag_count_near only counts a glyph sitting at a stem's FREE end.
FLAG_HOOKS = {"flag8": 1, "flag16": 2, "flag8_or_rest_quarter": 1}
FLAG_CATS = set(FLAG_HOOKS)
REST_CATS = {"rest8", "rest_quarter", "rest_half_whole", "flag8_or_rest_quarter"}
DOT_CATS = {"dot"}


# ---------------------------------------------------------------------------
# Geometry tolerances, in STAFF SPACES (see module docstring)
# ---------------------------------------------------------------------------

# The point values these replace were calibrated on a 5.125pt staff line
# spacing (the sampled library's notation staves, measured across 254
# staves); each constant below is the old absolute value converted at that
# reference spacing, so behaviour on the sampled library is unchanged while
# a condensed or large-print score now scales with its own staff.
REFERENCE_STAFF_SPACING = 5.125

_SP = {
    # stems / barlines
    "stem_min_height": 0.78,      # was 4.0pt
    "stem_max_height": 8.78,      # was 45.0pt
    "stem_line_max_dx": 0.03,     # was 0.15pt
    "stem_rect_max_width": 0.20,  # was 1.0pt
    # How exactly a vertical's ends must coincide with the staff's outer
    # lines to be a barline rather than a stem. Measured: real barlines land
    # within 0.03pt of both outer lines (they are drawn to the same
    # coordinates as the staff lines, so the only offset is the stroke's own
    # half-width), while the closest real stem - a down-stem from a note
    # above the staff to a beam just below it, which spans almost exactly
    # the same range - sits 0.88pt and 1.30pt out. 0.1 staff spaces
    # (~0.51pt) splits those two populations with an order of magnitude of
    # margin on the barline side.
    "barline_tol": 0.10,
    # beams
    "beam_min_width": 0.39,       # was 2.0pt
    "beam_min_thickness": 0.16,   # was 0.8pt
    "beam_max_thickness": 1.56,   # was 8.0pt
    "beam_rect_max_height": 1.56, # was 8.0pt
    "beam_level_gap": 0.39,       # was 2.0pt
    "beam_x_tol": 0.59,           # was 3.0pt
    "beam_y_tol": 1.17,           # was 6.0pt
    # notehead <-> stem attachment
    "stem_x_tol": 0.68,           # was 3.5pt
    "stem_y_tol": 1.17,           # was 6.0pt
    # flags
    "flag_x_tol": 0.98,           # was 5.0pt
    "flag_y_tol": 1.76,           # was 9.0pt
    # augmentation dots
    "dot_x_tol": 1.17,            # was 6.0pt
    "dot_x_back": 0.20,           # was 1.0pt
    "dot_y_tol": 0.78,            # was 4.0pt
    # ties
    "tie_gap_max": 7.80,          # was 40.0pt
    "tie_height_max": 1.56,       # was 8.0pt
    "tie_y_tol": 0.10,            # was 0.5pt
    # search bands
    "drawing_band_pad": 7.80,     # was 40.0pt
    "drawing_x_pad": 0.98,        # was 5.0pt
    "glyph_x_pad": 0.59,          # was 3.0pt
    # rest disambiguation (wider window - see decode_note_events)
    "rest_stem_x_tol": 1.17,      # was 6.0pt
    "rest_stem_y_tol": 2.34,      # was 12.0pt
    "rest_stem_pad": 0.59,        # was 3.0pt
}

# A beam never spans a large fraction of a whole system; a staff LINE does.
# tabextract._long_horizontal_segments documents that real exporters draw
# staff lines as thin filled rectangles, and such a rectangle otherwise
# sails through the "dark, thin, wide" beam test and becomes a Beam across
# the entire staff. Stems conventionally end near the middle line, so a
# staff-line-shaped beam would hand every quarter in the system one or two
# phantom beam levels and emit it as an eighth or sixteenth.
BEAM_MAX_STAFF_WIDTH_FRACTION = 0.5


class _Tol:
    """Every geometric tolerance for one staff, resolved from staff spaces
    into points using that staff's own line spacing."""

    __slots__ = ("spacing", "staff_height", "staff_width") + tuple(_SP)

    def __init__(self, spacing, staff_height=0.0, staff_width=0.0):
        spacing = float(spacing) if spacing and spacing > 0 else REFERENCE_STAFF_SPACING
        self.spacing = spacing
        self.staff_height = staff_height
        self.staff_width = staff_width
        for name, sp in _SP.items():
            setattr(self, name, sp * spacing)

    @property
    def beam_max_width(self):
        if self.staff_width > 0:
            return self.staff_width * BEAM_MAX_STAFF_WIDTH_FRACTION
        return float("inf")


def _spacing_from_lines(line_ys):
    if line_ys and len(line_ys) > 1:
        return (line_ys[-1] - line_ys[0]) / (len(line_ys) - 1)
    return REFERENCE_STAFF_SPACING


# ---------------------------------------------------------------------------
# Font handling
# ---------------------------------------------------------------------------


def _glyf_digests(tt, gids):
    """sha256 (32 hex chars) of each requested GID's RAW `glyf` bytes, for
    the GIDs this font actually fills. Raw bytes straight out of the table
    via the `loca` offsets - not a re-serialisation - so the digest is
    exactly the "byte-for-byte identical outline" property the Maestro GID
    map rests on."""
    out = {}
    glyf_raw = tt.getTableData("glyf")
    loca = tt["loca"]
    for gid in gids:
        if gid + 1 >= len(loca):
            continue
        seg = glyf_raw[loca[gid]:loca[gid + 1]]
        if not seg:
            continue  # slot present but unused by this subset - no evidence
        out[gid] = hashlib.sha256(seg).hexdigest()[:32]
    return out


def maestro_fingerprint_ok(tt, digests=None, min_glyphs=None):
    """Is this "Maestro" really the calibrated subset MAESTRO_GID_MAP was
    built against?

    Returns (ok, detail). The family name alone cannot answer this - a
    different Finale version or a different subsetting path keeps the name
    and changes the GID order, which silently mis-decodes every notehead,
    rest, flag and time-signature digit at "high confidence". So compare
    the actual outlines: every mapped GID this font FILLS must hash to the
    calibrated digest for that GID, and enough of them must be filled to be
    evidence at all (see MAESTRO_FINGERPRINT_MIN_GLYPHS). Absent/empty
    slots are not evidence either way - the subset keeps all 204 slots but
    only fills what a page uses.
    """
    expected = MAESTRO_GLYF_DIGESTS if digests is None else digests
    floor = MAESTRO_FINGERPRINT_MIN_GLYPHS if min_glyphs is None else min_glyphs
    try:
        found = _glyf_digests(tt, sorted(MAESTRO_GID_MAP))
    except Exception as exc:
        return False, f"could not read glyph outlines ({type(exc).__name__})"
    if not found:
        return False, "no mapped glyph outlines present"
    matched = []
    mismatched = []
    for gid, digest in sorted(found.items()):
        want = expected.get(gid)
        if want is None:
            # A mapped GID with no calibrated digest is an outline we have
            # never seen filled. It cannot confirm the subset, and it must
            # not silently condemn it either - record it and move on.
            continue
        if digest == want:
            matched.append(gid)
        else:
            mismatched.append(gid)
    if mismatched:
        return False, (
            f"{len(mismatched)} glyph outline(s) differ from the calibrated Maestro subset "
            f"(GIDs {mismatched[:6]})"
        )
    if len(matched) < floor:
        return False, (
            f"only {len(matched)} calibrated glyph outline(s) present, need {floor} to "
            "recognise the subset"
        )
    return True, f"{len(matched)} glyph outlines match the calibrated Maestro subset"


class MusicFont:
    """One embedded music-symbol font RESOURCE (one xref) on a page, with
    its glyph order resolved so GIDs (Maestro) or names (Opus family) can be
    mapped to a semantic category. Kept per-xref, not per-family: Opus
    subsets are minted fresh per resource (unlike Maestro's fixed 204-slot
    subset, which really is stable file to file), so two different Opus
    resources on the same page can have completely different glyph orders
    even though both are named "Opus"."""

    __slots__ = ("family", "xref", "tt", "glyph_order")

    def __init__(self, family, xref, tt):
        self.family = family  # "Maestro" | "Opus" | "OpusSpecial"
        self.xref = xref
        self.tt = tt
        self.glyph_order = tt.getGlyphOrder() if tt else []

    def category(self, gid):
        if self.family == "Maestro":
            return MAESTRO_GID_MAP.get(gid)
        gname = self.glyph_order[gid] if 0 <= gid < len(self.glyph_order) else None
        if gname is None:
            return None
        if self.family == "Opus":
            return OPUS_NAME_MAP.get(gname)
        if self.family == "OpusSpecial":
            return OPUS_SPECIAL_NAME_MAP.get(gname)
        return None


# Sibelius exports also embed "OpusText" for fingering/annotation numerals
# (not music-symbol glyphs - confirmed by checking its actual usage: single
# characters like ASCII '3' rendered via MacRomanEncoding codepoints, i.e.
# plain text). It is deliberately NOT in this set: loading it only produced
# glyphs with no calibrated category (there is no note/rest/digit meaning to
# give them), which did nothing but inflate the "unknown_glyphs" honesty
# metric with irrelevant text characters that were never a real decode gap.
MUSIC_FONT_FAMILIES = ("Maestro", "Opus", "OpusSpecial")

# pymupdf's own extension marker for an embedded font's outline flavour.
# Only TrueType (`glyf`) outlines are covered: the Maestro GID map and the
# Opus name maps were both calibrated on TrueType subsets. The loaded font's
# real table set is checked too (see _load_one_font) rather than trusting
# this marker alone.
_TRUETYPE_EXTS = ("ttf",)


# Embedded font resources are a DOCUMENT-level object, not a page-level one:
# the same xref is referenced by every page that draws with it. Re-extracting
# and re-parsing it with fontTools once per page meant a 30-page score paid
# 30 identical TTFont parses. Cached per document, keyed on xref.
#
# The cache is stored ON the document rather than in a module-level map
# keyed by it: pymupdf's Document cannot be weak-referenced, so a
# WeakKeyDictionary is not available, and a module-level dict keyed on
# id(doc) would hand a later document the previous one's fonts as soon as
# CPython reused the address. Hanging it off the object ties its lifetime to
# exactly the right thing and needs no invalidation.
_FONT_CACHE_ATTR = "_fermata_music_font_cache"


def _font_cache(doc):
    cache = getattr(doc, _FONT_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        try:
            setattr(doc, _FONT_CACHE_ATTR, cache)
        except Exception:
            pass  # uncacheable document: still correct, just not memoised
    return cache


def _load_one_font(doc, xref, base, ext):
    """Extract, parse and validate one embedded music font resource.
    Returns (MusicFont | None, warning | None) and never raises: a truncated
    or otherwise unreadable embedded font must degrade this page to the
    spacing fallback, not fail the whole extraction. TTFont parses lazily,
    so glyph-order and outline access have to happen inside the guard too."""
    if ext not in _TRUETYPE_EXTS:
        # CFF-flavour embeds are reported by pymupdf as "cff" and are not
        # covered by either calibrated map.
        return None, f"{base} is embedded with {ext!r} outlines, which this decoder is not calibrated for"
    ttfont_cls = _ttfont_class()
    if ttfont_cls is None:
        return None, "fontTools is not installed - cannot read embedded music fonts"
    try:
        content = doc.extract_font(xref)
        if isinstance(content, tuple):
            content = content[-1]
        if not content:
            return None, f"{base} font resource is empty"
        tt = ttfont_cls(io.BytesIO(content), fontNumber=0)
        if "glyf" not in tt:
            return None, (
                f"{base} is embedded without TrueType `glyf` outlines, which this decoder "
                "is not calibrated for"
            )
        if base == "Maestro":
            ok, detail = maestro_fingerprint_ok(tt)
            if not ok:
                return None, (
                    f"a font named Maestro on this page is NOT the calibrated Maestro subset "
                    f"({detail}) - its glyph IDs cannot be trusted to mean what this decoder "
                    "thinks they mean, so rhythm falls back to note spacing for it"
                )
        mf = MusicFont(base, xref, tt)
        if not mf.glyph_order:
            return None, f"{base} font resource has no readable glyph order"
    except Exception as exc:
        return None, f"{base} font resource could not be read ({type(exc).__name__}) - ignored"
    return mf, None


def load_music_fonts(doc, page):
    """Return ({family_name: [MusicFont, ...]}, warnings) - one entry per
    distinct font RESOURCE (xref) using that family name on this page. Most
    pages have exactly one resource per family; when a page genuinely has
    more than one (see MusicFont's docstring), keeping all of them - rather
    than silently keeping only the first-seen xref and resolving every
    span's GIDs against it regardless of which resource actually drew them -
    lets extract_glyph_events try each candidate resource per glyph instead
    of committing to a possibly-wrong one.

    A resource that is rejected (wrong outline flavour, unreadable, or a
    "Maestro" that fails its fingerprint) is left out AND reported, so the
    caller can degrade honestly instead of decoding with a font whose glyph
    IDs mean something else."""
    cache = _font_cache(doc)
    by_family = collections.defaultdict(list)
    warnings = []
    seen_xrefs = set()
    try:
        fonts = page.get_fonts(full=True)
    except Exception:
        return {}, ["page font list could not be read"]
    for f in fonts:
        xref, ext, _ftype, basefont = f[0], f[1], f[2], f[3]
        base = basefont.split("+")[-1]
        if base not in MUSIC_FONT_FAMILIES or xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        if xref not in cache:
            cache[xref] = _load_one_font(doc, xref, base, ext)
        mf, warn = cache[xref]
        if warn:
            warnings.append(warn)
        if mf is not None:
            by_family[base].append(mf)
    return dict(by_family), warnings


# ---------------------------------------------------------------------------
# Glyph event extraction
# ---------------------------------------------------------------------------

class GlyphEvent:
    __slots__ = ("family", "gid", "category", "x0", "y0", "x1", "y1", "code")

    def __init__(self, family, gid, category, bbox, code):
        self.family = family
        self.gid = gid
        self.category = category
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.code = code

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2

    @property
    def yc(self):
        return (self.y0 + self.y1) / 2

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    def __repr__(self):
        return f"<{self.category or '?'} g{self.gid} @({self.x0:.1f},{self.y0:.1f})>"


class PageGlyphs:
    """Everything decode_* needs from one page's music-font text, decoded
    once. `warnings` carries font-level problems (an unreadable embed, a
    "Maestro" that failed its fingerprint) so a caller can degrade honestly
    instead of quietly finding no glyphs."""

    __slots__ = ("events", "fonts", "unknown", "warnings")

    def __init__(self, events, fonts, unknown, warnings):
        self.events = events
        self.fonts = fonts
        self.unknown = unknown
        self.warnings = warnings

    def __iter__(self):
        # kept tuple-unpackable: (events, fonts, unknown)
        return iter((self.events, self.fonts, self.unknown))


# Per-page caches. decode_note_events and decode_time_signature each run
# once per standard staff, and a page can have several systems, so without
# caching the same page's embedded fonts get re-parsed, the same
# page.get_texttrace() re-walked, and the same page.get_drawings() content
# stream re-parsed several times over. Keyed by the Page object itself via a
# WeakKeyDictionary so entries are dropped automatically once a caller is
# done with a page (no manual invalidation needed, and no risk of a stale
# hit if a page object's id() were ever reused).
_GLYPH_EVENTS_CACHE = weakref.WeakKeyDictionary()
_DRAWINGS_CACHE = weakref.WeakKeyDictionary()


def clear_caches():
    """Drop the per-page caches. Only needed by tests that mutate the
    calibration tables between runs; the per-document font cache lives on
    the document itself, so re-opening the file is enough to reset it."""
    _GLYPH_EVENTS_CACHE.clear()
    _DRAWINGS_CACHE.clear()


def page_drawings(page):
    """page.get_drawings(), memoised per page. get_drawings() re-parses the
    page's entire content stream, and this page's drawings are wanted once
    per standard staff by the stem/beam pass plus once each by tabextract's
    horizontal and vertical segment scans - a ~7-staves-per-page file
    re-parsed the same content stream ~9 times inside one synchronous
    request."""
    cached = _DRAWINGS_CACHE.get(page)
    if cached is None:
        try:
            cached = page.get_drawings()
        except Exception:
            cached = []
        _DRAWINGS_CACHE[page] = cached
    return cached


def extract_glyph_events(page):
    """Walk page.get_texttrace() and classify every char drawn in a known
    music font into a semantic category (category is None if the glyph
    wasn't in our calibrated table - reported, not silently dropped).
    Returns a PageGlyphs."""
    cached = _GLYPH_EVENTS_CACHE.get(page)
    if cached is not None:
        return cached

    fonts, warnings = load_music_fonts(page.parent, page)
    if not fonts:
        result = PageGlyphs([], fonts, [], warnings)
        _GLYPH_EVENTS_CACHE[page] = result
        return result

    events = []
    unknown = []
    try:
        trace = page.get_texttrace()
    except Exception:
        trace = []
        warnings = warnings + ["page text trace could not be read"]
    for span in trace:
        # get_texttrace()'s "font" is normally already subset-tag-stripped,
        # but strip defensively - a raw "ABCDEF+Family" would otherwise
        # never match a `fonts` key built from the stripped basefont name
        # and this whole span would silently degrade to "no music glyphs".
        fname = span.get("font", "").split("+")[-1]
        candidates = fonts.get(fname)
        if not candidates:
            continue
        for ch in span.get("chars", []):
            code, gid, origin, bbox = ch
            # try each font resource sharing this family name until one
            # yields a real category - almost always there's exactly one
            # candidate (no ambiguity); when a page genuinely has more than
            # one Opus resource, this picks whichever one actually knows
            # this GID instead of always trusting the first-loaded xref.
            cat = None
            for mf in candidates:
                c = mf.category(gid)
                if c is not None:
                    cat = c
                    break
            ev = GlyphEvent(fname, gid, cat, bbox, code)
            events.append(ev)
            if cat is None:
                unknown.append(ev)
    result = PageGlyphs(events, fonts, unknown, warnings)
    _GLYPH_EVENTS_CACHE[page] = result
    return result


# ---------------------------------------------------------------------------
# Vector primitives: stems, beams, ties/slurs
# ---------------------------------------------------------------------------

Stem = collections.namedtuple("Stem", "x y0 y1")
# A beam carries its CENTRELINE endpoints, not just a bbox centre: a steeply
# slanted beam's centre y is nowhere near its actual y at either end (an
# 85pt run with a 16.7pt-tall bbox is off by ~8.3pt at the ends, more than
# any sane attachment tolerance), so matching a stem tip against the centre
# rejected the first and last note of exactly the slanted groups this
# decoder went out of its way to detect. See beam_y_at.
Beam = collections.namedtuple("Beam", "x0 x1 y_at_x0 y_at_x1")
Curve = collections.namedtuple("Curve", "pts x0 x1 y0 y1")


def beam_y_at(beam, x):
    """The beam's centreline y where the stem at `x` meets it."""
    span = beam.x1 - beam.x0
    if span <= 0:
        return (beam.y_at_x0 + beam.y_at_x1) / 2
    t = (x - beam.x0) / span
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return beam.y_at_x0 + (beam.y_at_x1 - beam.y_at_x0) * t


def _iter_line_contours(items):
    """Group a drawing path's consecutive 'l' items into point-chains.
    PyMuPDF represents each straight-line segment as its own ('l', p1, p2)
    item; when several are drawn back-to-back to trace one outline (a
    beam's quadrilateral, a stem stroke, or the closing lines of a filled
    tie/slur), consecutive items share an endpoint. A single path can
    contain MORE THAN ONE such outline (e.g. a two-level beam group drawn
    as two separate quads in one fill call) - those must stay separate
    contours, never unioned into one bbox, or a real second beam level
    silently vanishes into an oversized/misplaced first one."""
    contours = []
    cur = []
    for item in items:
        if item[0] != "l":
            if cur:
                contours.append(cur)
                cur = []
            continue
        p1, p2 = item[1], item[2]
        if cur and abs(cur[-1].x - p1.x) < 0.05 and abs(cur[-1].y - p1.y) < 0.05:
            cur.append(p2)
        else:
            if cur:
                contours.append(cur)
            cur = [p1, p2]
    if cur:
        contours.append(cur)
    return contours


def _polygon_area(pts):
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i].x, pts[i].y
        x2, y2 = pts[(i + 1) % n].x, pts[(i + 1) % n].y
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


# A beam stroke is one filled quadrilateral, so its contour is 4 points (5
# if explicitly closed). Every one of the 956 accepted beam contours in a
# sample of library pages had exactly 4. Contours with more points than a
# quad can have are not beams, and short-circuiting them keeps the exact
# all-pairs long-axis measurement below O(1) in practice - a 200-point
# decorative contour used to cost ~20k hypot calls just to be rejected.
_BEAM_MAX_CONTOUR_POINTS = 8


def _beam_from_contour(pts, tol):
    """A beam stroke is a thin, wide (and sometimes steeply slanted) filled
    quadrilateral. Measuring its y-bbox height as "thickness" only works
    when it's near-horizontal: a steeply-slanted beam spanning a wide
    x-range has a tall bbox despite being just as thin along its OWN axis,
    which is exactly why a real single beam used to get discarded by a
    height gate. Measure true perpendicular thickness instead: polygon
    area / long-axis length (area = length * thickness for a thin
    parallelogram, regardless of rotation) - confirmed against real
    rejected cases (e.g. Classical-Guitar-Method-Vol1-2020.pdf p92, a
    single beam with a 16.7pt-tall bbox over an 85pt run that a flat
    height<=14 gate always discarded)."""
    n = len(pts)
    if n < 3 or n > _BEAM_MAX_CONTOUR_POINTS:
        return None
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    x0, x1 = min(xs), max(xs)
    width = x1 - x0
    if width < tol.beam_min_width or width > tol.beam_max_width:
        return None
    long_axis = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            d = math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y)
            if d > long_axis:
                long_axis = d
    if long_axis <= 0:
        return None
    thickness = _polygon_area(pts) / long_axis
    if not (tol.beam_min_thickness <= thickness <= tol.beam_max_thickness):
        return None
    # Centreline y at each end: average the corner ys clustered at that end.
    edge = width * 0.25
    left = [p.y for p in pts if p.x <= x0 + edge]
    right = [p.y for p in pts if p.x >= x1 - edge]
    y_at_x0 = sum(left) / len(left) if left else (min(ys) + max(ys)) / 2
    y_at_x1 = sum(right) / len(right) if right else (min(ys) + max(ys)) / 2
    return Beam(x0, x1, y_at_x0, y_at_x1)


def _is_barline(y0, y1, staff_top, staff_bottom, tol):
    """A barline is a vertical whose two ends coincide with the staff's own
    outer lines; a stem starts at a notehead and runs about an octave, so it
    lands somewhere else at at least one end.

    Barlines are 13% of everything the stem gate accepts on the sampled
    library (2089 of 16052), and letting them through has two teeth: a rest
    within a flag's reach of one is misread as a flag and silently dropped,
    and a notehead beside one can pick it as its nearest stem and take its
    (absent) flag count.

    The test is deliberately tight at BOTH ends - see the barline_tol note.
    A merely "spans most of the staff" test also swallows the real down-stem
    of a note sitting above the staff whose beam sits just below it, which
    turns a beamed eighth into a quarter.
    """
    slack = tol.barline_tol
    return abs(y0 - staff_top) <= slack and abs(y1 - staff_bottom) <= slack


def extract_stems_beams_curves(page, y_lo, y_hi, x_lo, x_hi, tol=None):
    if tol is None:
        tol = _Tol(REFERENCE_STAFF_SPACING, y_hi - y_lo, x_hi - x_lo)
    stems, beams, curves = [], [], []
    band_lo = y_lo - tol.drawing_band_pad
    band_hi = y_hi + tol.drawing_band_pad
    x_pad_lo = x_lo - tol.drawing_x_pad
    x_pad_hi = x_hi + tol.drawing_x_pad

    def add_stem(x, y0, y1):
        if _is_barline(y0, y1, y_lo, y_hi, tol):
            return
        stems.append(Stem(x, y0, y1))

    for d in page_drawings(page):
        rect = d.get("rect")
        if rect is None:
            continue
        if not (x_pad_lo <= rect.x0 and rect.x1 <= x_pad_hi):
            continue
        if not (band_lo <= rect.y0 and rect.y1 <= band_hi):
            continue
        items = d.get("items", [])
        fill = d.get("fill")
        is_dark_fill = bool(fill) and any(c is not None and c < 0.3 for c in fill)

        # 'l' items: group into contours so a path drawing MULTIPLE shapes
        # (a beam group's separate levels, or a stem's outline alongside a
        # tie's) yields one candidate per shape instead of one union bbox
        # for the whole path. This also means we never need to `continue`
        # past the rest of the path just because ONE of its shapes looked
        # like a beam - the 're'/'c' item pass below always runs, so a
        # filled tie/slur (2 curves + closing lines, black fill) sharing a
        # path with something beam-shaped still gets its curves recorded,
        # and a stem drawn as a filled outline in the same path still gets
        # found (previously a single blanket `continue` starved all of
        # this whenever the leading beam-shape test matched, or even just
        # partially matched and then failed its own size gate).
        for contour in _iter_line_contours(items):
            beam = _beam_from_contour(contour, tol) if is_dark_fill else None
            if beam is not None:
                beams.append(beam)
                continue
            p1, p2 = contour[0], contour[-1]
            dy = abs(p1.y - p2.y)
            if abs(p1.x - p2.x) < tol.stem_line_max_dx and tol.stem_min_height <= dy <= tol.stem_max_height:
                add_stem((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y))

        for item in items:
            if item[0] == "re":
                r = item[1]
                if r.width < tol.stem_rect_max_width and tol.stem_min_height <= r.height <= tol.stem_max_height:
                    add_stem((r.x0 + r.x1) / 2, r.y0, r.y1)
                elif (
                    is_dark_fill
                    and tol.beam_min_thickness <= r.height <= tol.beam_rect_max_height
                    and tol.beam_min_width <= r.width <= tol.beam_max_width
                ):
                    yc = (r.y0 + r.y1) / 2
                    beams.append(Beam(r.x0, r.x1, yc, yc))
            elif item[0] == "c":
                p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
                xs = [p0.x, p1.x, p2.x, p3.x]
                ys = [p0.y, p1.y, p2.y, p3.y]
                curves.append(Curve((p0, p1, p2, p3), min(xs), max(xs), min(ys), max(ys)))
    stems.sort(key=lambda s: s.x)
    beams.sort(key=lambda b: b.x0)
    return stems, beams, curves


# ---------------------------------------------------------------------------
# Duration model
# ---------------------------------------------------------------------------

DURATION_CODE = {4.0: 1, 2.0: 2, 1.0: 4, 0.5: 8, 0.25: 16, 0.125: 32}


class NoteEvent:
    __slots__ = ("x", "y", "base_units", "flags", "dotted", "is_rest",
                 "category", "notehead_kind", "tied_next")

    def __init__(self, x, y, base_units, flags, dotted, is_rest, category, notehead_kind=None):
        self.x = x
        self.y = y
        self.base_units = base_units
        self.flags = flags  # int hook/beam count found
        self.dotted = dotted  # 0, 1 or 2
        self.is_rest = is_rest
        self.category = category
        self.notehead_kind = notehead_kind
        self.tied_next = False  # best-effort: see _mark_ties()

    @property
    def quarter_units(self):
        u = self.base_units / (2 ** self.flags)
        if self.dotted == 1:
            u *= 1.5
        elif self.dotted == 2:
            u *= 1.75
        return u

    @property
    def duration_code(self):
        # snap to nearest known plain code, then report dots separately
        plain = self.base_units / (2 ** self.flags)
        return DURATION_CODE.get(plain, DURATION_CODE[min(DURATION_CODE, key=lambda k: abs(k - plain))])

    def __repr__(self):
        d = "." * self.dotted
        kind = "R" if self.is_rest else (self.notehead_kind or "?")
        return f"<{kind} 1/{self.duration_code}{d} @x={self.x:.1f}>"


def _bounds(sorted_keys, lo, hi):
    """Index range of sorted_keys entries within [lo, hi]."""
    return bisect.bisect_left(sorted_keys, lo), bisect.bisect_right(sorted_keys, hi)


def _best_stem(stems, stem_xs, x0, x1, yc, tol, x_tol=None, y_tol=None):
    """The stem this notehead actually hangs off.

    Stems attach at one SIDE of a notehead (left edge for a down-stem,
    right edge for an up-stem) at roughly the notehead's vertical centre -
    not at its bbox centre-x or top/bottom edge. In dense/chordal writing
    more than one stem can plausibly sit near a given notehead, so pick the
    single closest one: nearest in x to either notehead edge, then nearest
    in y to the notehead's centre.

    Selecting on "which candidate has the highest flag/beam count" instead
    is what silently upgraded genuine quarters to eighths and sixteenths -
    a note's OWN plain stem scores zero and was skipped outright, handing
    the note to whichever neighbouring voice's beamed stem happened to fall
    inside the tolerance.
    """
    xt = tol.stem_x_tol if x_tol is None else x_tol
    yt = tol.stem_y_tol if y_tol is None else y_tol
    lo, hi = _bounds(stem_xs, min(x0, x1) - xt, max(x0, x1) + xt)
    best = None
    best_key = None
    for i in range(lo, hi):
        s = stems[i]
        if not (abs(s.x - x0) <= xt or abs(s.x - x1) <= xt):
            continue
        near_end = s.y0 if abs(s.y0 - yc) < abs(s.y1 - yc) else s.y1
        dy = abs(near_end - yc)
        if dy > yt:
            continue
        dx = min(abs(s.x - x0), abs(s.x - x1))
        key = (round(dx, 3), round(dy, 3))
        if best_key is None or key < best_key:
            best, best_key = s, key
    return best


def _has_stem_near(stems, stem_xs, x0, x1, yc, tol):
    return _best_stem(stems, stem_xs, x0, x1, yc, tol,
                      x_tol=tol.rest_stem_x_tol, y_tol=tol.rest_stem_y_tol) is not None


def _flag_count_near(flag_events, flag_xs, stem, notehead_yc, tol):
    """Count flag hooks attached at the free end of a stem (the end further
    from the notehead)."""
    free_y = stem.y1 if abs(stem.y1 - notehead_yc) > abs(stem.y0 - notehead_yc) else stem.y0
    lo, hi = _bounds(flag_xs, stem.x - tol.flag_x_tol, stem.x + tol.flag_x_tol)
    hooks = 0
    for i in range(lo, hi):
        ev = flag_events[i]
        if abs(ev.yc - free_y) > tol.flag_y_tol:
            continue
        hooks += FLAG_HOOKS.get(ev.category, 1)
    return hooks


def _beam_count_near(beams, stem, notehead_yc, tol):
    """Count distinct stacked beam strokes whose x-span covers this stem AND
    whose y AT THIS STEM'S X sits near the stem's free (non-notehead) end.

    A beam attaches at the tip of a stem, not just anywhere along its x
    position, so the y check matters to avoid grabbing a neighboring voice's
    beam that happens to pass over this stem's x. Restricted to the free end
    (matching _flag_count_near's own free-end logic) rather than the whole
    stem span: accepting a beam anywhere along the full stem meant a second
    voice's beam crossing this stem anywhere - not just at its tip - could
    register, which in 2-voice guitar writing turned a real quarter note
    into a false 16th.

    The y is interpolated along the beam rather than taken from its bbox
    centre - see the Beam docstring.
    """
    free_y = stem.y1 if abs(stem.y1 - notehead_yc) > abs(stem.y0 - notehead_yc) else stem.y0
    levels = []
    for b in beams:
        if b.x0 - tol.beam_x_tol > stem.x:
            break  # beams are x0-sorted: nothing further right can cover this stem
        if stem.x > b.x1 + tol.beam_x_tol:
            continue
        y_here = beam_y_at(b, stem.x)
        if abs(y_here - free_y) > tol.beam_y_tol:
            continue
        levels.append(round(y_here, 1))
    if not levels:
        return 0
    levels.sort()
    clusters = [levels[0]]
    for y in levels[1:]:
        if y - clusters[-1] > tol.beam_level_gap:
            clusters.append(y)
    return len(clusters)


def _mark_ties(notes, curves, tol):
    """Best-effort tie detection: flag notes[i].tied_next when a shallow
    curve bridges notes[i] and notes[i+1] and both sit at the same pitch
    (same y - ties join equal pitches, unlike slurs which usually don't).
    This is NOT used to merge durations (each notehead's own notated value
    is kept as-is) - it is reported as a separate signal because tie
    handling is a known weak spot worth surfacing honestly rather than
    silently getting wrong.
    """
    pitched = [n for n in notes if not n.is_rest]
    for a, b in zip(pitched, pitched[1:]):
        if abs(a.y - b.y) > tol.tie_y_tol:
            continue
        gap = b.x - a.x
        if not (0 < gap <= tol.tie_gap_max):
            continue
        for c in curves:
            span = c.x1 - c.x0
            height = c.y1 - c.y0
            if height > tol.tie_height_max:
                continue
            if span < gap * 0.25 or span > gap * 1.3:
                continue
            mid = (c.x0 + c.x1) / 2
            if a.x - 2 <= mid <= b.x + 2:
                a.tied_next = True
                break


def _assign_dots(owners, dot_events, tol):
    """Assign each augmentation-dot glyph to exactly ONE owner (notehead or
    rest) and return {id(owner): dot_count}.

    Counting "every dot glyph in a window to my right" per notehead instead
    lets one dot be claimed by several noteheads and lets a neighbouring
    voice's dot be claimed here: an augmentation dot is nudged half a staff
    space off the line it belongs to, so the y window has to be about that
    wide and cannot itself separate two voices a staff step apart. That is
    how a 3/4 bar came to hold a `:2 ...{dd}` - 3.5 quarters, more than the
    bar can physically contain. One dot, one owner fixes it.
    """
    counts = collections.Counter()
    if not owners or not dot_events:
        return counts
    owners = sorted(owners, key=lambda e: e.x1)
    owner_x1s = [e.x1 for e in owners]
    for dot in dot_events:
        lo, hi = _bounds(owner_x1s, dot.xc - tol.dot_x_tol, dot.xc + tol.dot_x_back)
        best = None
        best_key = None
        for i in range(lo, hi):
            ev = owners[i]
            dy = abs(ev.yc - dot.yc)
            if dy > tol.dot_y_tol:
                continue
            key = (round(dy, 3), round(abs(dot.xc - ev.x1), 3))
            if best_key is None or key < best_key:
                best, best_key = ev, key
        if best is not None:
            counts[id(best)] += 1
    return counts


def decode_note_events(page, staff_top, staff_bottom, staff_x0, staff_x1, line_ys,
                       spacing=None):
    """Core decode for one standard-notation staff: returns (NoteEvent list
    sorted by x, stats).

    line_ys: sorted list of the 5 staff line y-coordinates (for half/whole
    rest disambiguation). spacing: that staff's line spacing, used to scale
    every geometric tolerance (defaults to the spacing implied by line_ys).

    `stats` is the decode's own honesty record and callers are expected to
    ACT on it, not just log it: unknown_glyphs / unknown_ratio say how much
    of this staff's music-font text fell outside the calibrated vocabulary,
    and unknown_at_flag_position counts unrecognised glyphs sitting exactly
    where a flag would attach - the shape of "this piece uses 32nd flags,
    grace notes or an articulation we never calibrated", which decodes as
    systematically wrong durations while looking perfectly healthy.
    """
    tol = _Tol(spacing if spacing else _spacing_from_lines(line_ys),
               staff_bottom - staff_top, staff_x1 - staff_x0)
    glyphs = extract_glyph_events(page)
    stats = {
        "unknown_glyphs": 0,
        "unknown_ratio": 0.0,
        "unknown_at_flag_position": 0,
        "unknown_gid_or_name_sample": [],
        "band_glyphs": 0,
        "note_events": 0,
        "stem_count": 0,
        "beam_segment_count": 0,
        "curve_count": 0,
        "font_warnings": list(glyphs.warnings),
    }
    if not glyphs.events:
        return [], stats

    pad = (staff_bottom - staff_top) * 1.6
    band_lo, band_hi = staff_top - pad, staff_bottom + pad
    x_pad = tol.glyph_x_pad
    staff_events = [e for e in glyphs.events
                    if band_lo <= e.yc <= band_hi and staff_x0 - x_pad <= e.xc <= staff_x1 + x_pad]

    stems, beams, curves = extract_stems_beams_curves(
        page, staff_top, staff_bottom, staff_x0, staff_x1, tol)
    stem_xs = [s.x for s in stems]

    # Partition the staff's glyphs by category ONCE, x-sorted, so each
    # proximity query below is a bisect-bounded window instead of a full
    # rescan. Previously every notehead rescanned all staff events for
    # flags (once per candidate stem) and again for dots, i.e.
    # O(noteheads * events) per staff.
    flag_events = sorted((e for e in staff_events if e.category in FLAG_CATS), key=lambda e: e.xc)
    flag_xs = [e.xc for e in flag_events]
    dot_events = sorted((e for e in staff_events if e.category in DOT_CATS), key=lambda e: e.xc)
    unknown_in_band = [e for e in staff_events if e.category is None]

    # Dots first: one dot glyph belongs to exactly one note (see _assign_dots).
    dot_owners = [e for e in staff_events if e.category in NOTEHEAD_CATS or e.category in REST_CATS]
    dot_counts = _assign_dots(dot_owners, dot_events, tol)

    notes = []
    for ev in staff_events:
        if ev.category in NOTEHEAD_CATS:
            if ev.category == "notehead_whole":
                # whole notes never take a stem, flag or beam by definition -
                # don't even look for one (a nearby unrelated stem in a dense
                # chord/2-voice passage would otherwise be a false positive).
                base, flags = 4.0, 0
            elif ev.category == "notehead_half":
                # half notes have a stem but categorically cannot carry a
                # flag or beam - counting one here would only ever be a
                # false positive from a neighboring voice's stem/beam sitting
                # nearby (2-voice writing), so don't even look.
                base, flags = 2.0, 0
            else:
                base = 1.0  # filled/x/diamond head: quarter-or-shorter
                flags = 0
                stem = _best_stem(stems, stem_xs, ev.x0, ev.x1, ev.yc, tol)
                if stem is not None:
                    hooks = _flag_count_near(flag_events, flag_xs, stem, ev.yc, tol)
                    beam_levels = _beam_count_near(beams, stem, ev.yc, tol)
                    flags = max(hooks, beam_levels)
            notes.append(NoteEvent(ev.xc, ev.yc, base, flags, min(dot_counts.get(id(ev), 0), 2),
                                   False, ev.category, notehead_kind=ev.category))
        elif ev.category in REST_CATS:
            # disambiguate flag8_or_rest_quarter by stem proximity: a real
            # stem near it means it's actually a flag glyph, not a rest.
            # (Barlines are excluded from `stems` - see _is_barline - or a
            # rest engraved next to one would vanish here.)
            if ev.category == "flag8_or_rest_quarter":
                if _has_stem_near(stems, stem_xs, ev.x0 - tol.rest_stem_pad,
                                  ev.x1 + tol.rest_stem_pad, ev.yc, tol):
                    continue  # it's a flag - counted via the notehead's stem (see FLAG_HOOKS)
                cat = "rest_quarter"
            else:
                cat = ev.category
            if cat == "rest_half_whole":
                # whole rest hangs below the 2nd line from top; half rest
                # sits on the middle (3rd) line - use nearest line index
                nearest_idx = min(range(len(line_ys)), key=lambda i: abs(line_ys[i] - ev.yc))
                base = 4.0 if nearest_idx <= 1 else 2.0
            elif cat == "rest8":
                base = 0.5
            else:
                base = 1.0
            notes.append(NoteEvent(ev.xc, ev.yc, base, 0, min(dot_counts.get(id(ev), 0), 2),
                                   True, cat))

    notes.sort(key=lambda n: n.x)
    _mark_ties(notes, curves, tol)

    # Unrecognised glyphs sitting where a flag attaches are the dangerous
    # ones: they mean this piece's flag/hook vocabulary is wider than the
    # calibrated table (32nd flags, grace notes), so durations are wrong in
    # a way nothing else in the decode would notice.
    suspect = 0
    for u in unknown_in_band:
        stem = _best_stem(stems, stem_xs, u.x0, u.x1, u.yc, tol,
                          x_tol=tol.flag_x_tol, y_tol=tol.flag_y_tol)
        if stem is None:
            continue
        free_y = stem.y1 if abs(stem.y1 - u.yc) > abs(stem.y0 - u.yc) else stem.y0
        if abs(u.yc - free_y) <= tol.flag_y_tol:
            suspect += 1

    stats.update({
        "unknown_glyphs": len(unknown_in_band),
        "unknown_ratio": (len(unknown_in_band) / len(staff_events)) if staff_events else 0.0,
        "unknown_at_flag_position": suspect,
        "unknown_gid_or_name_sample": sorted({(u.family, u.gid) for u in unknown_in_band})[:20],
        "band_glyphs": len(staff_events),
        "note_events": len(notes),
        "stem_count": len(stems),
        "beam_segment_count": len(beams),
        "curve_count": len(curves),
    })
    return notes, stats


# ---------------------------------------------------------------------------
# Time signature
# ---------------------------------------------------------------------------

# alphaTab accepts a \ts numerator of 1-32 and needs a denominator that is a
# power of two to mean a note-duration unit at all - the same rule api.py
# enforces on a caller-supplied override. A glyph-decoded signature has to
# clear it too: it is written into the emitted \ts and STORED, and alphaTab
# throws outright on something like `\ts 3 12`, so an unvalidated decode
# produces a saved transcription that can never be rendered again.
VALID_TS_DENOMINATORS = (1, 2, 4, 8, 16, 32)


def time_signature_is_valid(ts):
    if not ts:
        return False
    num, den = ts
    return 1 <= num <= 32 and den in VALID_TS_DENOMINATORS


def _group_digit_clusters(digits, gap_ratio=0.6, row_ratio=0.35):
    """Group digit glyphs into runs of horizontally-adjacent digits ON THE
    SAME ROW - the multiple digits of a number like the '12' of a 12/8
    signature.

    Adjacency needs BOTH tests. Without the x-gap test, a numerator/
    denominator pairing that matches one glyph to one glyph pairs the "8"
    of 12/8 with whichever single digit happens to sit closest and returns a
    confidently-wrong (1, 8). Without the row test, two digits that are
    stacked rather than side by side - which is what a numerator and
    denominator are - merge into one run whenever they overlap in x, so a
    numerator digit that lands on the wrong side of the band split turns
    "1/16" into "116" or "3/4" into "34"; that reaches \\ts as e.g. `3 12`
    and makes the stored transcription unrenderable.
    """
    if not digits:
        return []
    digits = sorted(digits, key=lambda e: e.x0)
    clusters = [[digits[0]]]
    for prev, cur in zip(digits, digits[1:]):
        gap = cur.x0 - prev.x1
        width = max(prev.width, cur.width, 1.0)
        row = max(prev.height, cur.height, 1.0) * row_ratio
        if gap <= width * gap_ratio and abs(cur.yc - prev.yc) <= row:
            clusters[-1].append(cur)
        else:
            clusters.append([cur])
    return clusters


def _cluster_value(cluster):
    cluster = sorted(cluster, key=lambda e: e.x0)
    return int("".join(str(DIGIT_CATS[e.category]) for e in cluster))


def _cluster_span(cluster):
    return min(e.x0 for e in cluster), max(e.x1 for e in cluster)


def _signature_from_window(window, mid):
    """Resolve one x-window of glyphs into a time signature, or (None, why)."""
    for e in window:
        if e.category == "common_time":
            return (4, 4), "common_time symbol"
        if e.category == "cut_time":
            return (2, 2), "cut_time symbol"

    digits = [e for e in window if e.category in DIGIT_CATS]
    if len(digits) < 2:
        return None, f"only {len(digits)} time-signature digit glyph(s) found"

    num_digits = [e for e in digits if e.yc < mid]
    den_digits = [e for e in digits if e.yc >= mid]
    if not num_digits or not den_digits:
        return None, "digit glyphs found but not split across a numerator/denominator band"

    num_clusters = _group_digit_clusters(num_digits)
    den_clusters = _group_digit_clusters(den_digits)

    # A real time signature stacks its numerator and denominator digit runs
    # at the same x column - pick the numerator/denominator cluster pair
    # whose x-spans overlap most, and require that match to be unambiguous
    # (no near-tied second-best pair using a DIFFERENT cluster) before
    # trusting it. If nothing lines up cleanly, report "not detected"
    # rather than ever returning a confidently-wrong guess.
    pairs = []
    for nc in num_clusters:
        n0, n1 = _cluster_span(nc)
        for dc in den_clusters:
            d0, d1 = _cluster_span(dc)
            overlap = min(n1, d1) - max(n0, d0)
            if overlap > 0:
                pairs.append((overlap, nc, dc))
    if not pairs:
        return None, "digit glyphs found but numerator/denominator x-spans don't align"

    pairs.sort(key=lambda t: -t[0])
    best_overlap, best_nc, best_dc = pairs[0]
    for overlap, nc, dc in pairs[1:]:
        if overlap > best_overlap * 0.6 and (nc is not best_nc or dc is not best_dc):
            return None, "ambiguous numerator/denominator digit grouping"

    ts = (_cluster_value(best_nc), _cluster_value(best_dc))
    if not time_signature_is_valid(ts):
        # Reaching here means the digit runs grouped into something that
        # isn't a time signature at all. Emitting it would poison the
        # stored transcription (see VALID_TS_DENOMINATORS).
        return None, (
            f"digit glyphs grouped into {ts[0]}/{ts[1]}, which is not a usable time "
            "signature - treated as not detected"
        )
    return ts, "stacked digit glyphs (multi-digit aware)"


def decode_time_signature(page, staff_top, staff_bottom, staff_x0, spacing=None):
    """The time signature printed at the START of this staff, or (None, why).

    Only the leading window is read. A meter change engraved mid-system is
    not picked up here: the same digit glyphs also spell tuplet numbers and
    other numerals across a staff, and scanning the full width traded a
    known blind spot for confidently-wrong signatures. Changes engraved at a
    system's start - which is where the sampled library's changes land - are
    caught, because this runs per standard staff and tabextract carries the
    results forward as a timeline (see _time_signature_timeline).
    """
    tol = _Tol(spacing if spacing else (staff_bottom - staff_top) / 4.0)
    glyphs = extract_glyph_events(page)
    if not glyphs.events:
        return None, "no music glyphs found"
    lead = tol.spacing * 8.8  # was a flat 45pt at the reference staff size
    mid = (staff_top + staff_bottom) / 2
    window = [e for e in glyphs.events
              if staff_top - tol.spacing <= e.yc <= staff_bottom + tol.spacing
              and staff_x0 - tol.drawing_x_pad <= e.x0 <= staff_x0 + lead]
    return _signature_from_window(window, mid)
