"""
Decode note durations and time signature from vector-engraved PDF pages by
identifying the music-font glyphs actually used for noteheads, flags, rests,
dots and time-signature digits - instead of guessing from x-spacing.

Used by tabextract.extract() as the primary rhythm/time-signature source
when a tab staff is paired with a standard-notation staff drawn in a
recognised music font; tabextract falls back to its own spacing heuristic
when that isn't the case (raster pages, a CFF-flavour embedding of Maestro
or Opus, an unrecognised font, or a recognised family whose glyph outlines
don't match the calibrated fingerprint - see MAESTRO_GLYF_DIGESTS).

There are THREE calibrations here, because there are three different things
a PDF can leave behind to identify a glyph by, and which one survives is the
exporter's choice, not ours:

  * Finale's "Maestro" keeps a stable glyph ID order -> MAESTRO_GID_MAP,
    guarded by an outline fingerprint because a GID means something only by
    convention of one export pipeline.
  * Sibelius's "Opus" keeps glyph NAMES (as PUA labels) -> OPUS_NAME_MAP.
    Names travel with the outline, so no fingerprint is needed.
  * A SMuFL font - MuseScore's Leland, Dorico's Bravura, any conforming
    font - keeps neither: names stripped, glyph order minted per file. What
    survives is the PDF's ToUnicode CMap, and for a SMuFL font the codepoint
    it maps to IS the glyph's published meaning -> SMUFL_CODE_MAP.

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

  A glyph's vertical position is its INK centre, read from the embedded
  outline - NOT the centre of the box the text trace reports, which is a
  metrics box whose top and bottom are the font's ascender and descender and
  therefore says the same thing about every glyph in the font. See
  GlyphEvent, _InkBoxes and half_or_whole_rest: the difference is about 0.4
  staff spaces, it is invisible to glyph-to-glyph comparisons because it
  cancels, and it was deciding whether a rest was a half or a whole - a
  twofold difference in duration - from a measurement that was never the
  rest's position.

  The horizontal edges a notehead's stem attaches at come from the same
  outline, for the same reason: one calibrated font puts its whole side
  bearing on one side, so against the metrics box "which of these two stems
  is closer" was a third of a space out for every up-stem on such a page.
  See GlyphEvent.stem_edges and _best_stem, which ranks candidate stems on
  both distances together - x alone, with y only breaking its ties, handed a
  melody note to the accompaniment's stem whenever the two voices shared a
  beat.
"""
import bisect
import collections
import hashlib
import io
import math
import struct
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
    # table's own "rendered and eyeballed" standard. Unlike Opus's PUA names,
    # a Maestro GID carries no naming rule to extrapolate from - it means
    # only what one export pipeline's glyph order says it means - so there is
    # no rule-derived fallback available here the way there is for Opus's
    # missing digits above.
    #
    # THIS IS STILL AN OPEN GAP, not a safe default: a numerator or
    # denominator that needs a '0' does not fall through to "not detected"
    # the way an earlier version of this docstring claimed. The unmapped '0'
    # glyph resolves to no category at all, so `_stacked_digit_pairs` simply
    # never sees it - a Finale 10/8 loses its '0' the same way a Sibelius
    # 12/8 used to lose its '1', and is returned as a confident (1, 8). Fixing
    # that for real needs the decoder to refuse whenever an UNCATEGORISED
    # glyph sits among the digits it did read, not another table entry - see
    # issue #84 - and is out of scope for this table-filling change, so it is
    # recorded here rather than silently left for the next reader to
    # rediscover.
    #
    # flag32, rest16 and rest32 are likewise NOT mapped for Maestro: the same
    # full-library GID rescan that turned up no evidence for digit0 also
    # hashed every OTHER glyph ID any Maestro resource in the library fills
    # (not only the ones already in this table) and found nine extra stable
    # outlines - none of them a third flag hook or a second/third rest shape.
    # No sampled Finale export in this library draws an unbeamed 32nd or a
    # sixteenth/thirty-second rest, so there is nothing to confirm a GID
    # against for any of the three.
    #
    # gid 174 (notehead_diamond, harmonics) is bucketed with notehead_filled
    # as of #115: `duration_needs_stem` is True for it, so an unfound stem
    # floors it at a quarter the same way a filled head does. Quarter-vs-half
    # is unpinned for a diamond - unlike notehead_half it carries no shape
    # that says which, so a diamond engraved as a half is read as a quarter
    # whenever its stem is not found, not just left undecided.
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
#
# THE DIGITS. Opus names its ten time-signature digits "uniF03X", where X is
# the ASCII character for the digit itself (uniF032 is '2' -> 0x32, uniF038
# is '8' -> 0x38, and so on) - a rule, not ten unrelated labels. Five of the
# ten (2, 3, 4, 6, 8) were directly observed filled in real library Opus
# subsets, which is what confirms the rule holds for this font rather than
# being a guess about it. The other five (0, 1, 5, 7, 9) are filled in below
# by the SAME rule rather than left absent: unlike a Maestro GID, which means
# nothing outside the accident of one export pipeline's glyph order and so
# can never be extrapolated, an Opus PUA name is the font's own fixed
# labelling scheme, and a rule confirmed on five of ten instances of it is
# sound to apply to the rest. A rescan of every Opus/OpusSpecial resource in
# the library (281 files with a hit) found uniF030/031/035/037/039 filled in
# none of them - no sampled Sibelius score prints a time signature needing a
# 0, 1, 5, 7 or 9 - so these five are RULE-DERIVED, not directly observed;
# say so if this table is ever re-justified from the library alone.
OPUS_NAME_MAP = {
    "uniF023": "sharp", "uniF026": "clef", "uniF02E": "dot",
    "uniF030": "digit0", "uniF031": "digit1", "uniF032": "digit2",
    "uniF033": "digit3", "uniF034": "digit4", "uniF035": "digit5",
    "uniF036": "digit6", "uniF037": "digit7", "uniF038": "digit8",
    "uniF039": "digit9",
    "uniF03E": "accent",
    "uniF043": "cut_time", "uniF04A": "flag8", "uniF055": "fermata",
    "uniF062": "flat", "uniF063": "common_time", "uniF065": "note_pictograph",
    "uniF068": "note_pictograph", "uniF06A": "note_pictograph",
    "uniF071": "note_pictograph", "uniF06E": "natural",
    "uniF077": "notehead_whole", "uniF0B2": "up_bow",
    "uniF0B3": "bracket", "uniF0B7": "rest_half_whole",
    "uniF0CE": "flag8_or_rest_quarter",  # disambiguated by stem proximity
    "uniF0CF": "notehead_filled", "uniF0DC": "notehead_x",
    "uniF0E4": "rest8", "uniF0EE": "rest_half_whole", "uniF0FA": "notehead_half",
    # flag16 is NOT mapped: the same full-library rescan that justified the
    # five rule-derived digits above also walked every uni-prefixed glyph
    # name any Opus resource in the library actually fills (not only the
    # names already in this table), and none of them is a second flag hook -
    # every sampled Sibelius sixteenth is beamed. There is no numeric
    # sibling of uniF04A ("flag8") the way there is for the digits, so this
    # is not a rule extension, it is a guess, and guessing a PUA name risks
    # colliding with some other real glyph's meaning. An unbeamed Sibelius
    # sixteenth still falls through to the flagless default until either a
    # library sample turns one up or Opus's flag-hook naming is confirmed
    # some other way (see issue #84).
    #
    # rest16 and rest32 are NOT mapped for the same reason: the rescan found
    # no Opus resource filling a second rest shape beyond the eighth
    # rest/rest_half_whole/flag8_or_rest_quarter glyphs already listed above
    # - no sampled Sibelius score prints a sixteenth or thirty-second rest.
}
OPUS_SPECIAL_NAME_MAP = {
    "uniF0AA": "dot", "uniF0DA": "tab_label", "uniF0A1": "tuplet_bracket",
    "uniF0A2": "tuplet_bracket", "uniF083": "down_stroke", "uniF089": "up_stroke",
    "uniF0DC": "digit8",
    "uniF0E1": "string1", "uniF0E2": "string2", "uniF0E3": "string3",
    "uniF0E4": "string4", "uniF0E5": "string5", "uniF0E6": "string6",
}

# SMuFL fonts ("Standard Music Font Layout"): keyed by the SMuFL CODEPOINT,
# which is neither a glyph ID nor a font-specific name but a number fixed by
# a published specification - so one table serves every conforming font.
#
# WHY A THIRD KEY. Free engravers use SMuFL fonts (MuseScore draws with
# Leland and falls back to Bravura for glyphs Leland omits; Dorico uses
# Bravura). Their PDF embeds are subsets with the `post` table dropped AND a
# glyph order minted per file - the worst of both the families above: no
# names to key on like Opus, and no stable GID order to key on like Maestro.
# What does survive is the PDF's own ToUnicode CMap, which maps each CID
# back to the codepoint the engraver drew, and pymupdf resolves it for us
# (GlyphEvent.code). For a SMuFL font that codepoint IS the glyph's
# published meaning.
#
# WHY THIS NEEDS NO FINGERPRINT. MAESTRO_GLYF_DIGESTS exists because a GID
# is only a meaning by convention of one export pipeline, so a font wearing
# the same family name can silently mean something else. A SMuFL codepoint
# is a meaning by specification, and it is carried by the PDF rather than
# inferred from the font's internals, so there is no equivalent
# same-name-different-meaning hazard to guard against. What IS guarded is
# whether a font is a SMuFL music font at all - see _smufl_font_names.
#
# A consequence worth knowing: nothing on this path opens the embedded font
# at all - no fontTools, no `glyf` table, no outline flavour check - because
# there is no outline to compare against. The two conditions the other
# families fail on (a CFF-flavour embedding, a missing glyph order) simply do
# not arise here.
#
# HOW THIS TABLE WAS DERIVED: by engraving scores that request each symbol
# explicitly and reading back which codepoint appeared. That is a stronger
# check than reading a rendered outline, because the input says which symbol
# was asked for: a bar of unbeamed 32nds yields exactly four flag32
# codepoints, ten one-bar meters yield each time-signature digit once, and
# so on. Every entry below was observed that way; nothing is here by
# analogy. Deliberately absent: the diamond noteheads MuseScore draws for
# harmonics (two distinct codepoints turned up for a half and a quarter
# diamond and this table will not guess which is which), and the notehead
# Bravura supplies for a half-value X head. Both stay unmapped, so a score
# using them is counted as partly unrecognised and degrades honestly rather
# than decoding at a duration nobody verified.
SMUFL_CODE_MAP = {
    0xE000: "brace",
    0xE050: "clef",            # gClef
    0xE052: "clef",            # gClef8vb - the octave-transposing guitar clef
    0xE062: "clef",            # fClef
    0xE06D: "clef",            # 6stringTabClef
    0xE080: "digit0", 0xE081: "digit1", 0xE082: "digit2", 0xE083: "digit3",
    0xE084: "digit4", 0xE085: "digit5", 0xE086: "digit6", 0xE087: "digit7",
    0xE088: "digit8", 0xE089: "digit9",
    0xE08A: "common_time", 0xE08B: "cut_time",
    0xE0A2: "notehead_whole",
    0xE0A3: "notehead_half",
    0xE0A4: "notehead_filled",
    0xE0A9: "notehead_x",
    0xE1E7: "dot",             # augmentationDot
    0xE240: "flag8", 0xE241: "flag8",
    0xE242: "flag16", 0xE243: "flag16",
    0xE244: "flag32", 0xE245: "flag32",
    0xE260: "flat", 0xE261: "natural", 0xE262: "sharp",
    0xE4E3: "rest_whole",
    0xE4E4: "rest_half",
    0xE4E5: "rest_quarter",
    0xE4E6: "rest8",
    0xE4E7: "rest16",
    0xE4E8: "rest32",
}

# The codepoint span the SMuFL specification reserves for music symbols:
# E000-F3FF for the standard glyphs and F400-F8FF for a font's optional
# additions. Codes a SMuFL font draws inside it but that SMUFL_CODE_MAP does
# not know are honest decode gaps and are counted as such; codes OUTSIDE it
# drawn by the same font are not music symbols at all (a music font can
# carry plain text characters) and are ignored rather than counted against
# the decode. The optional block is included deliberately - stopping at
# F3FF meant a notehead or flag drawn from it was neither decoded NOR
# counted, leaving its note at the base duration with a spotless honesty
# report.
SMUFL_RANGE = (0xE000, 0xF8FF)

# The blocks the specification assigns to symbols that carry a note's
# DURATION. An unrecognised codepoint in one of these is a duration this
# decoder had to invent; an unrecognised codepoint anywhere else in the
# SMuFL range is a symbol it did not need - an articulation, a fermata, a
# dynamic, a repeat dot - and saying "I could not read this score" because
# of one would be false. Both are reported; only these gate confidence.
#
# A codepoint in a block not listed here counts as duration-bearing, which
# is the fail-safe direction: an unrecognised glyph whose meaning is unknown
# is assumed to have mattered.
SMUFL_NOTEHEAD_BLOCK = (0xE0A0, 0xE0FF)
SMUFL_DURATION_BLOCKS = (
    SMUFL_NOTEHEAD_BLOCK,
    (0xE1D0, 0xE1FF),   # individual notes, including the augmentation dot
    (0xE240, 0xE25F),   # flags
    (0xE4E0, 0xE4FF),   # rests
)
SMUFL_FURNITURE_BLOCKS = (
    (0xE000, 0xE09F),   # staff brackets, barlines and repeats, clefs, time signatures
    (0xE260, 0xE28F),   # accidentals
    (0xE4A0, 0xE4DF),   # articulations, holds and pauses
    (0xE500, 0xE5FF),   # rehearsal marks, octave lines, dynamics, ornaments
    (0xE600, 0xE8FF),   # instrument-specific techniques, fingering, tuplet numerals
)

# How many recognised SMuFL codepoints a font must draw on a page before it
# is treated as that page's music font. One or two PUA codepoints could
# collide with anything; a handful landing on calibrated music symbols is
# what a real engraved staff looks like (the smallest single-system fixture
# here draws 9). Mirrors MAESTRO_FINGERPRINT_MIN_GLYPHS: enough to be
# evidence, low enough to clear every genuine case.
SMUFL_MIN_MAPPED_GLYPHS = 4


def _in_blocks(code, blocks):
    return any(lo <= code <= hi for lo, hi in blocks)


def smufl_unknown_kind(code):
    """What an unrecognised SMuFL codepoint would have told us.

    "notehead" is called out on its own because it is the one glyph whose
    absence is silently destructive: with no notehead to match, the note it
    stood for gets whatever duration the surrounding beats leave over, and
    its tab digits are attached to some other voice. Everything else
    duration-bearing is a "duration"; the rest is "furniture"."""
    if _in_blocks(code, (SMUFL_NOTEHEAD_BLOCK,)):
        return "notehead"
    if _in_blocks(code, SMUFL_FURNITURE_BLOCKS) and not _in_blocks(code, SMUFL_DURATION_BLOCKS):
        return "furniture"
    return "duration"

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
FLAG_HOOKS = {"flag8": 1, "flag16": 2, "flag32": 3, "flag8_or_rest_quarter": 1}
FLAG_CATS = set(FLAG_HOOKS)
# Maestro and Opus both draw the half and whole rest with ONE glyph, told
# apart by which staff line it hangs from ("rest_half_whole"). SMuFL gives
# them separate codepoints, so where the engraving says which it is outright
# there is no need to infer it from position - hence the exact categories
# beside the ambiguous one.
REST_VALUES = {"rest_whole": 4.0, "rest_half": 2.0, "rest_quarter": 1.0,
               "rest8": 0.5, "rest16": 0.25, "rest32": 0.125}
REST_CATS = set(REST_VALUES) | {"rest_half_whole", "flag8_or_rest_quarter"}
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
    # How far outside a stem's own y-span an inner chord notehead may sit and
    # still count as threaded onto it (see _stem_through_notehead). A third
    # of a staff space is under half a notehead height, so it forgives the
    # stroke ending a hair short of the outermost notehead's centre without
    # reaching the next staff position.
    "stem_span_slack": 0.35,
    # flags
    "flag_x_tol": 0.98,           # was 5.0pt
    "flag_y_tol": 1.76,           # was 9.0pt
    # How far outside a flag's own INK the stem end it is drawn on may fall -
    # see _at_stem_end. A flag is joined to its stem's tip, so the tip is
    # inside the ink; this is slack for a stroke that stops a hair short of
    # the hook it carries. Measured over the whole sampled library by the
    # hooks each value counts: no slack at all counts 4,984 and loses 166 of
    # the hooks a centre-distance test found, 0.1 and 0.25 both count 5,158,
    # and widening to a full space adds only 4 more. The value sits in the
    # middle of that plateau.
    "flag_ink_pad": 0.25,
    # augmentation dots
    "dot_x_tol": 1.17,            # was 6.0pt
    "dot_x_back": 0.20,           # was 1.0pt
    # A dot's vertical window is not a symmetric tolerance - see _DOT_TIERS.
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


# A glyph's ink never runs more than a couple of ems from its origin (the
# tallest thing measured in the calibrated vocabulary is a clef, at 2.2 em), so
# a `glyf` header claiming more than this is a stale or corrupt box rather than
# a shape - and a wrong ink box is worse than none, because none degrades to
# the metrics box while a wrong one is believed.
_INK_MAX_EM = 8.0


class _InkBoxes:
    """Where each glyph's INK is in one embedded font - as a fraction of the
    em, so it scales with whatever size the page drew at.

    A TrueType glyph record starts with its own bounding box
    (numberOfContours, xMin, yMin, xMax, yMax, five int16s), so this needs no
    outline parsing at all: slice the glyph's bytes out of `glyf` using the
    `loca` offsets and unpack the header. That is the same access the Maestro
    fingerprint already makes (see _glyf_digests), and it is why the ink box
    is available for every font this decoder reads glyphs from.
    """

    __slots__ = ("_glyf", "_loca", "_upem", "_cache")

    def __init__(self, tt):
        self._glyf = tt.getTableData("glyf")
        self._loca = tt["loca"]
        self._upem = tt["head"].unitsPerEm or 1000
        self._cache = {}

    def _header(self, gid):
        """This glyph's raw `glyf` bounding box as (xMin, yMin, xMax, yMax) in
        em fractions, or None where this font has nothing to say about that
        glyph: a gid past the end of `loca`, or a slot the subset left empty
        (which is most of them - a Maestro subset keeps all 204 slots and
        fills the handful a page uses).

        Whether the box is USABLE is asked per axis by span and xspan, and the
        two axes are refused independently BY CONSTRUCTION rather than
        because either has been seen to fail alone: a glyph drawn as a single
        vertical stroke could in principle have a degenerate x extent and a
        perfectly good y one. No glyph in the library actually splits that
        way - 0 of 1,855,076 header reads have one axis usable and the other
        not - but asking per axis costs nothing and is the right shape for a
        case the library just hasn't shown yet.
        """
        if gid in self._cache:
            return self._cache[gid]
        out = None
        loca = self._loca
        if gid >= 0 and gid + 1 < len(loca):
            seg = self._glyf[loca[gid]:loca[gid + 1]]
            if len(seg) >= 10:
                _n, xmin, ymin, xmax, ymax = struct.unpack(">hhhhh", seg[:10])
                upem = self._upem
                out = (xmin / upem, ymin / upem, xmax / upem, ymax / upem)
        self._cache[gid] = out
        return out

    def span(self, gid):
        """(yMin, yMax) in em fractions, or None where the box is missing (see
        _header) or its vertical extent is degenerate or implausible."""
        box = self._header(gid)
        if box is None:
            return None
        ymin, ymax = box[1], box[3]
        if ymax > ymin and abs(ymin) <= _INK_MAX_EM and abs(ymax) <= _INK_MAX_EM:
            return ymin, ymax
        return None

    def xspan(self, gid):
        """(xMin, xMax) in em fractions, or None - the horizontal twin of
        span, tested against the same implausibility limit."""
        box = self._header(gid)
        if box is None:
            return None
        xmin, xmax = box[0], box[2]
        if xmax > xmin and abs(xmin) <= _INK_MAX_EM and abs(xmax) <= _INK_MAX_EM:
            return xmin, xmax
        return None


def _ink_boxes(tt):
    """An _InkBoxes for this font, or None when its outlines cannot be read.
    Never raises: an unreadable ink box costs precision - see GlyphEvent.yc,
    which falls back to the text baseline - not the decode."""
    if tt is None:
        return None
    try:
        return _InkBoxes(tt)
    except Exception:
        return None


class MusicFont:
    """One embedded music-symbol font RESOURCE (one xref) on a page, with
    its glyph order resolved so GIDs (Maestro) or names (Opus family) can be
    mapped to a semantic category. Kept per-xref, not per-family: Opus
    subsets are minted fresh per resource (unlike Maestro's fixed 204-slot
    subset, which really is stable file to file), so two different Opus
    resources on the same page can have completely different glyph orders
    even though both are named "Opus"."""

    __slots__ = ("family", "xref", "tt", "glyph_order", "ink")

    def __init__(self, family, xref, tt):
        self.family = family  # "Maestro" | "Opus" | "OpusSpecial"
        self.xref = xref
        self.tt = tt
        self.glyph_order = tt.getGlyphOrder() if tt else []
        # This resource's own ink boxes. Kept on the resource rather than
        # looked up by family name for the same reason its glyph order is: a
        # gid means something only within ONE resource, so a box read from a
        # sibling resource that happens to share the name is a wrong number.
        self.ink = _ink_boxes(tt)

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
# The same, for the ink boxes of fonts that get no MusicFont - see
# _ink_boxes_by_name.
_INK_CACHE_ATTR = "_fermata_ink_box_cache"


def _doc_cache(doc, attr):
    cache = getattr(doc, attr, None)
    if cache is None:
        cache = {}
        try:
            setattr(doc, attr, cache)
        except Exception:
            pass  # uncacheable document: still correct, just not memoised
    return cache


def _font_cache(doc):
    return _doc_cache(doc, _FONT_CACHE_ATTR)


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
    """One music-font glyph the page drew, and where.

    THE BOX IS NOT THE INK. (x0, y0, x1, y1) is the box the text trace
    reports, and vertically that box is METRICS-based: its top and bottom come
    from the font's ascender and descender relative to the text baseline, so
    it says the same thing about every glyph in the font (measured: 4.0 staff
    spaces tall for Maestro, whatever the glyph, notehead or clef alike). Its
    centre therefore sits at a FIXED offset from the baseline rather than at
    the middle of the drawn shape - measured over the library: -0.39 staff
    spaces for Maestro, -0.49 for Opus, -0.27 for MuseScore's MScore, -0.67
    for OpusSpecial - and anything comparing it against staff geometry reads
    every glyph as most of half a space higher than it is.

    The exception is worth knowing, because it is what the committed fixtures
    are engraved with: Leland's embedded box is centred on the baseline
    (measured 0.000 over 475 glyphs), so on a Leland page the box centre and a
    notehead's ink centre coincide and this defect does not show. It shows for
    Leland's FLAGS, whose ink is nowhere near either.

    That bias cancels between two glyphs, which is why it survived: noteheads
    kept their relative positions, chords still grouped, dots still found
    their owners. It does NOT cancel against a staff line, a staff band, or
    the middle-line split between a numerator and a denominator, and it was
    deciding whether a one-glyph rest was a half or a whole - a twofold
    difference in duration - from a number that was never the rest's position.

    So `yc` is the INK centre, measured from the embedded outline (see
    _InkBoxes). `y0`/`y1` are kept as what the trace said, and `height` with
    them, because the metrics box is the right box for the two things that use
    it: the advance width for horizontal gaps, and a font-wide row height for
    grouping digits into a numeral.

    HORIZONTALLY the same warning applies, in one font and to one decision.
    x0/x1 come from the advance width, and a font's two side bearings need not
    match: measured over the library's noteheads, Maestro's and MScore's boxes
    sit within 0.02 staff spaces of the ink on both sides (97,489 and 8,254 of
    them), but OPUS overhangs the ink by 0.324 spaces on the RIGHT and not at
    all on the left (8,917 of them). A notehead's stem attaches at one side or
    the other, so on an Opus page "how far is this stem from the notehead" came
    out a third of a space larger for an up-stem, at its right edge, than for a
    down-stem at its left - which is the whole margin _best_stem was deciding
    two-voice writing on. Hence `stem_edges`.
    """

    __slots__ = ("family", "gid", "category", "x0", "y0", "x1", "y1", "code",
                 "smufl", "baseline_y", "ink_y0", "ink_y1", "ink_x0", "ink_x1")

    def __init__(self, family, gid, category, bbox, code, smufl=False,
                 baseline_y=None, ink=None, ink_x=None):
        self.family = family
        self.gid = gid
        self.category = category
        self.x0, self.y0, self.x1, self.y1 = bbox
        self.code = code
        self.smufl = smufl
        # The text baseline this glyph was drawn on - the point the engraver
        # placed it BY - and its measured ink extent, where the font could
        # supply one.
        self.baseline_y = baseline_y
        self.ink_y0, self.ink_y1 = ink if ink is not None else (None, None)
        self.ink_x0, self.ink_x1 = ink_x if ink_x is not None else (None, None)

    @property
    def calibration_key(self):
        """What a calibration table for this glyph's font would be keyed on -
        the codepoint for a SMuFL font, the glyph ID otherwise. Reported for
        unrecognised glyphs so the gap can actually be looked up later; a
        SMuFL subset's glyph ID is minted per file and would name nothing."""
        return f"U+{self.code:04X}" if self.smufl else self.gid

    @property
    def xc(self):
        return (self.x0 + self.x1) / 2

    @property
    def ink_measured(self):
        """Was this glyph's ink extent actually read from the font? Only an
        ink box can answer a question about sub-space POSITION (see
        half_or_whole_rest); the fallbacks below are good to a fraction of a
        space, which is not the same thing."""
        return self.ink_y0 is not None

    @property
    def stem_edges(self):
        """The two x positions a stem may attach at: the drawn shape's left and
        right edges, from the embedded outline where it could be read, and the
        metrics box's where it could not.

        A stem is drawn touching the notehead, so the distance from a stem to
        the nearer of these is a measurement of whether it attaches here - but
        only once both edges are the ink's. Against the advance-width box that
        distance carries the side bearing, which one calibrated font applies to
        one side only (see the class docstring)."""
        if self.ink_x0 is not None:
            return self.ink_x0, self.ink_x1
        return self.x0, self.x1

    @property
    def yc(self):
        """The middle of the drawn shape - see the class docstring.

        Falls back to the text baseline where the outline could not be read (a
        CFF embed, a rotated span, a subset slot with no glyph). That is the
        glyph's registration point, and for the glyphs whose position is
        compared against staff geometry it is very nearly the ink centre:
        measured over the library, a notehead's baseline sits within 0.013
        staff spaces of its ink centre and a meter digit's within 0.036, in
        Maestro, Opus, MScore and Leland alike. It is NOT good enough for the
        sub-space parity the half/whole rest turns on - those rests' baselines
        sit exactly ON the line grid in every one of those fonts, which is an
        offset of zero and evidence for neither answer - which is why that
        decision asks whether the ink was measured at all.

        Falls back further to the metrics box centre for an event built by
        hand from a box and nothing else, which is a test writing coordinates
        down rather than a page being read.
        """
        if self.ink_y0 is not None:
            return (self.ink_y0 + self.ink_y1) / 2
        if self.baseline_y is not None:
            return self.baseline_y
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


def _embedded_font_names(doc, page):
    """Basefont names on this page for which the PDF actually carries a font
    program. A font the reader has to substitute tells us nothing about what
    its codepoints mean."""
    names = set()
    try:
        fonts = page.get_fonts(full=True)
    except Exception:
        return names
    for f in fonts:
        xref, ext, _ftype, basefont = f[0], f[1], f[2], f[3]
        if not ext or ext in ("n/a", "none"):
            continue
        try:
            content = doc.extract_font(xref)
        except Exception:
            continue
        if isinstance(content, tuple):
            content = content[-1]
        if content:
            names.add(basefont.split("+")[-1])
    return names


def _ink_boxes_by_name(doc, page):
    """A function from a basefont NAME to that font's _InkBoxes, or None.

    The SMuFL path has no MusicFont to hang an ink box off - a SMuFL font is
    recognised from the codepoints it drew, and nothing on that path needs to
    open the font to decide what a glyph MEANS (see SMUFL_CODE_MAP). Where the
    glyph is is a different question: it needs the outline, because the text
    trace's box is a metrics box (see GlyphEvent). So the outline is opened
    here for measurement only, and failing to read it costs precision rather
    than the decode.

    Resolved lazily, and only for a name some span actually drew music glyphs
    in: a page carries half a dozen embedded text fonts that this decoder
    never reads a glyph from, and parsing those to measure glyphs nobody asks
    about is work for nothing. Each resource is parsed once per DOCUMENT, like
    the music fonts beside it.

    A name that more than one distinct resource on the page answers to
    resolves to None rather than to a guess: a SMuFL subset's glyph order is
    minted per file, so a box read from the wrong resource is a wrong number,
    and the text trace reports the font's NAME, not which resource drew the
    span. Those glyphs fall back to the baseline origin, which SMuFL fixes as
    each glyph's registration point.
    """
    cache = _doc_cache(doc, _INK_CACHE_ATTR)
    xrefs = collections.defaultdict(set)
    try:
        fonts = page.get_fonts(full=True)
    except Exception:
        fonts = []
    for f in fonts:
        xref, ext, _ftype, basefont = f[0], f[1], f[2], f[3]
        if not ext or ext in ("n/a", "none"):
            continue
        xrefs[basefont.split("+")[-1]].add(xref)
    resolved = {}

    def resolve(name):
        if name not in resolved:
            refs = xrefs.get(name) or ()
            boxes = None
            if len(refs) == 1:
                xref = next(iter(refs))
                if xref not in cache:
                    cache[xref] = _load_ink_boxes(doc, xref)
                boxes = cache[xref]
            resolved[name] = boxes
        return resolved[name]

    return resolve


def _load_ink_boxes(doc, xref):
    """The ink boxes of one embedded font resource, or None. Never raises -
    every reason this can fail (no font program, a CFF-flavour embed with no
    `glyf`, a truncated subset) is a font whose glyph positions are simply
    measured from the baseline instead."""
    ttfont_cls = _ttfont_class()
    if ttfont_cls is None:
        return None
    try:
        content = doc.extract_font(xref)
        if isinstance(content, tuple):
            content = content[-1]
        if not content:
            return None
        tt = ttfont_cls(io.BytesIO(content), fontNumber=0)
        if "glyf" not in tt:
            return None
    except Exception:
        return None
    return _ink_boxes(tt)


def _ink_scale(span):
    """The size to scale a glyph's em-relative ink box by for this text span,
    or None when the span's ink extent cannot be placed from it.

    The trace's own `size` is that scale: checked against the advance width in
    the embedded `hmtx` for every glyph a music font drew on a notation staff
    in the sampled library, the two agree to within 1% on 407,469 of 407,537.

    An em-relative box only maps onto page y for UPRIGHT text, so a rotated
    span declines here and its glyphs are measured from the baseline instead
    of being placed confidently wrong. The library holds 17 such characters
    against 407,728 upright ones, so this is nearly - but not quite - a guard
    against something that does not happen."""
    size = span.get("size")
    if not size or size <= 0:
        return None
    d = span.get("dir")
    if d is not None and (abs(d[0] - 1.0) > 1e-6 or abs(d[1]) > 1e-6):
        return None
    return size


def _ink_span_on_page(reader, gid, origin_y, size):
    """(ink top, ink bottom) in page points, or None. Page y grows downward
    and font y grows upward, so the glyph's yMax is its TOP."""
    if reader is None or size is None or origin_y is None:
        return None
    em = reader.span(gid)
    if em is None:
        return None
    ymin, ymax = em
    return origin_y - ymax * size, origin_y - ymin * size


def _ink_x_on_page(reader, gid, origin_x, size):
    """(ink left, ink right) in page points, or None. Page x and font x both
    grow rightward, so unlike the vertical twin above this is a straight scale
    out from the glyph's origin with no flip."""
    if reader is None or size is None or origin_x is None:
        return None
    em = reader.xspan(gid)
    if em is None:
        return None
    xmin, xmax = em
    return origin_x + xmin * size, origin_x + xmax * size


def _smufl_music_fonts(doc, page, trace):
    """Which fonts on this page may be read as SMuFL music fonts?

    Answered from what they drew rather than from their name, because keying
    on the name would need an allowlist of every SMuFL font anyone might
    engrave with (Leland, Bravura, Petaluma, MuseJazz, ...) and would still
    miss the next one, while the codepoints are fixed by the specification
    for all of them. A page can hand back more than one name: MuseScore
    draws with Leland and falls back to Bravura for glyphs Leland does not
    carry, and both are decoded by the same table.

    BUT A CODEPOINT IS A CLAIM, NOT A CREDENTIAL. The codepoints reach us
    through the PDF's ToUnicode CMap, which the producer wrote, and taking
    that on its own was too credulous - a page whose "music font" was an
    unembedded text font drawing the letters A-F, with a ToUnicode CMap as
    its only qualification, decoded as an engraved staff at high confidence.
    Three requirements, each aimed at a way that goes wrong:

      * The PDF must EMBED a font program under that name. A reader-supplied
        substitute cannot be the thing whose glyphs were measured, and this
        is the same standard the Maestro and Opus paths are held to. (Kills
        the base-14 text font above outright.)

      * The mapping must not be the synthetic identity `U+E000 + glyph id`.
        Producers emit exactly that as a fallback for a subset they could
        not read a cmap from, and the arithmetic alone lands on a dozen and
        a half of this table's keys, so a page of ordinary text in such a
        font would read as an engraved staff.

      * At least one recognised codepoint must be a NOTEHEAD. A notation
        staff without noteheads is not one, and there would be nothing for
        this decoder to do on it anyway. (Under the identity mapping above,
        ordinary ASCII lands only on clefs - never a notehead.)

    Anything that fails these is not refused outright: whatever it drew in
    the SMuFL range is still counted as unread rather than silently ignored,
    once some font on the page has qualified. See extract_glyph_events."""
    embedded = _embedded_font_names(doc, page)
    mapped = collections.Counter()
    noteheads = collections.Counter()
    identity_only = collections.defaultdict(lambda: True)
    for span in trace:
        chars = span.get("chars")
        if not chars:
            continue
        name = span.get("font", "").split("+")[-1]
        for code, gid, _origin, _bbox in chars:
            if code not in SMUFL_CODE_MAP:
                continue
            mapped[name] += 1
            if code != 0xE000 + gid:
                identity_only[name] = False
            if smufl_unknown_kind(code) == "notehead":
                noteheads[name] += 1
    return {name for name, count in mapped.items()
            if count >= SMUFL_MIN_MAPPED_GLYPHS
            and noteheads[name]
            and not identity_only[name]
            and name in embedded}


def extract_glyph_events(page):
    """Walk page.get_texttrace() and classify every char drawn in a known
    music font into a semantic category (category is None if the glyph
    wasn't in our calibrated table - reported, not silently dropped).
    Returns a PageGlyphs."""
    cached = _GLYPH_EVENTS_CACHE.get(page)
    if cached is not None:
        return cached

    fonts, warnings = load_music_fonts(page.parent, page)
    try:
        trace = page.get_texttrace()
    except Exception:
        trace = []
        warnings = warnings + ["page text trace could not be read"]
    # A SMuFL font is recognised from what it drew, not from the font table
    # alone, so this has to come after the trace is in hand - and the early
    # exit has to consider both kinds of font or a MuseScore page (which has
    # no Maestro/Opus resource at all) would report no music glyphs.
    smufl_names = _smufl_music_fonts(page.parent, page, trace)
    if not fonts and not smufl_names:
        result = PageGlyphs([], fonts, [], warnings)
        _GLYPH_EVENTS_CACHE[page] = result
        return result

    # Ink boxes for fonts that got no MusicFont (the SMuFL path). A
    # Maestro/Opus glyph takes its ink box from the resource that named it
    # instead - see MusicFont.ink.
    ink_for = _ink_boxes_by_name(page.parent, page)

    events = []
    unknown = []
    for span in trace:
        # get_texttrace()'s "font" is normally already subset-tag-stripped,
        # but strip defensively - a raw "ABCDEF+Family" would otherwise
        # never match a `fonts` key built from the stripped basefont name
        # and this whole span would silently degrade to "no music glyphs".
        fname = span.get("font", "").split("+")[-1]
        candidates = fonts.get(fname)
        if not candidates:
            # Once ANY font on this page has qualified as a SMuFL music font,
            # every font's SMuFL-range codepoints are taken - decoded where
            # recognised, counted as unread where not. Restricting this to
            # the qualifying fonts hid real glyphs: an engraver falls back to
            # a second font for symbols its main one lacks, and one library
            # page draws its single harmonic notehead that way. Excluding
            # that font for drawing too little to qualify on its own meant
            # the notehead was neither read nor reported.
            if not smufl_names:
                continue
            size = _ink_scale(span)
            reader = ink_for(fname)
            for ch in span.get("chars", []):
                code, gid, origin, bbox = ch
                if not SMUFL_RANGE[0] <= code <= SMUFL_RANGE[1]:
                    # a music font can also carry plain text characters
                    # (rehearsal marks, fingerings); those are not music
                    # symbols and must not count as unrecognised ones.
                    continue
                ox = origin[0] if origin else None
                oy = origin[1] if origin else None
                ev = GlyphEvent(fname, gid, SMUFL_CODE_MAP.get(code), bbox, code, smufl=True,
                                baseline_y=oy,
                                ink=_ink_span_on_page(reader, gid, oy, size),
                                ink_x=_ink_x_on_page(reader, gid, ox, size))
                events.append(ev)
                if ev.category is None:
                    unknown.append(ev)
            continue
        size = _ink_scale(span)
        for ch in span.get("chars", []):
            code, gid, origin, bbox = ch
            # try each font resource sharing this family name until one
            # yields a real category - almost always there's exactly one
            # candidate (no ambiguity); when a page genuinely has more than
            # one Opus resource, this picks whichever one actually knows
            # this GID instead of always trusting the first-loaded xref.
            cat = None
            reader = None
            for mf in candidates:
                c = mf.category(gid)
                if c is not None:
                    cat = c
                    # ...and the ink box comes from that same resource, which
                    # is the one whose glyph order this gid was read against.
                    reader = mf.ink
                    break
            if reader is None and len(candidates) == 1:
                # An unrecognised glyph still has a position, and with one
                # resource on the page there is no doubt about whose it is.
                reader = candidates[0].ink
            ox = origin[0] if origin else None
            oy = origin[1] if origin else None
            ev = GlyphEvent(fname, gid, cat, bbox, code, baseline_y=oy,
                            ink=_ink_span_on_page(reader, gid, oy, size),
                            ink_x=_ink_x_on_page(reader, gid, ox, size))
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

# A half rest SITS ON a staff line; a whole rest HANGS BELOW one. Both are
# drawn half a staff space deep, so each one's INK CENTRE lands a quarter of a
# space off the line grid - a quarter ABOVE a line for a half rest, a quarter
# BELOW one for a whole rest.
#
# Measured over every one-glyph rest in the sampled library plus every rest a
# SMuFL font spelled out (314 glyphs): the offset is 0.23 to 0.35 of a space
# either side of a line, never anything below 0.23 and never above 0.35. The
# window below is that population with room on both sides, and its two edges
# reject the two things that are not a reading: an offset near zero (the ink
# centre landing ON a line, which is where a rest whose ink extent could not be
# measured ends up, because these rests' BASELINES sit on the grid in Maestro,
# Opus and Leland alike) and an offset near half a space (the ink centre midway
# between two lines, which no engraver draws).
REST_PARITY_MIN = 0.125
REST_PARITY_MAX = 0.375


def half_or_whole_rest(yc, line_ys, spacing):
    """Is this one-glyph rest a HALF rest or a WHOLE one? Returns
    (base_units, decided) - 2.0 for a half rest, 4.0 for a whole one.

    Maestro and Opus draw both rests with a SINGLE glyph (294 of the 2,795
    rest glyphs in the sampled library are that one id), so geometry is the
    only discriminator there is, for a twofold difference in duration: a whole
    rest read as a half loses two quarter notes of silence out of the bar, a
    half read as a whole invents two, and either shifts everything after it.

    The discriminator is sub-space PARITY against the line grid - which side
    of a line the ink sits on - and not, as this used to ask, which staff line
    the glyph is NEAREST. The difference is not a tolerance, it is a kind:
      * Parity holds at every LINE of the grid, including the ledger positions
        above and below the staff that a second voice's rests use. Nearest-line
        put every rest below the middle line in the same bucket, so a whole
        rest hanging under the staff read as a half.

        It does NOT hold for an arbitrary displacement, and this is a real
        limit rather than a tolerance: the signal is which quarter of a space
        the ink sits in, measured modulo one space, so displacing a rest by an
        ODD number of half spaces lands it exactly where the other rest would
        be and returns that other answer with decided=True. Nothing in (yc,
        line_ys, spacing) can separate the two cases - a half rest sits a
        quarter space above a line and a whole one a quarter space below one,
        and a half-space shift maps each onto the other - so this is stated,
        not guarded. No engraving in the library needs the guard: all 256
        one-glyph rests the decode reads land between 0.235 and 0.266 of a
        space from a line, and every one is decided.
      * Parity is measured against the ink. Nearest-line was measured against
        the metrics box centre (see GlyphEvent), which is a fixed distance
        from the BASELINE - nearly half a space, most of the way to the other
        answer.
    Checked against the one population where the answer is known independently
    of geometry: the rests a SMuFL font spells out in the codepoint itself.
    Parity agrees with the engraving on all 20 of them in the library;
    nearest-line disagrees with it on 4.

    `decided` is False where the geometry says neither - no staff lines to
    measure against, no usable spacing, or an offset outside the measured
    window (see REST_PARITY_MIN). Those are read as a HALF rest, which is what
    254 of the 294 one-glyph rests in the library are, and the caller reports
    the count rather than passing the guess off as a reading. Nothing in the
    library needs it: all 294 are decided, and each one's ink was measured.
    """
    if not line_ys or not spacing or spacing <= 0:
        return 2.0, False
    offset = (yc - line_ys[0]) / spacing
    offset -= round(offset)
    if REST_PARITY_MIN <= abs(offset) <= REST_PARITY_MAX:
        # Page y grows downward, so a positive offset is ink BELOW the line.
        return (4.0 if offset > 0 else 2.0), True
    return 2.0, False


class NoteEvent:
    __slots__ = ("x", "y", "base_units", "flags", "dotted", "is_rest",
                 "category", "notehead_kind", "tied_next", "stem_dir", "stem_key")

    def __init__(self, x, y, base_units, flags, dotted, is_rest, category,
                 notehead_kind=None, stem_key=None):
        self.x = x
        self.y = y
        self.base_units = base_units
        self.flags = flags  # int hook/beam count found
        self.dotted = dotted  # 0, 1 or 2
        self.is_rest = is_rest
        self.category = category
        self.notehead_kind = notehead_kind
        self.tied_next = False  # best-effort: see _mark_ties()
        # Voice signals. stem_key identifies the ONE engraved stem this
        # notehead hangs off, so the several noteheads of a chord - which
        # share a single stem - can be recognised as one beat rather than
        # several. stem_dir is "up"/"down" (see _assign_stem_directions), the
        # signal engravers use to separate an upper from a lower voice. Both
        # are None for a notehead with no stem at all (a whole note).
        self.stem_key = stem_key
        self.stem_dir = None

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
    """The stem this notehead actually hangs off. x0/x1 are the notehead's ink
    edges where the font could supply them - see GlyphEvent.stem_edges.

    Stems attach at one SIDE of a notehead (left edge for a down-stem, right
    edge for an up-stem) at roughly the notehead's vertical centre - not at its
    bbox centre-x or top/bottom edge. In dense/chordal writing more than one
    stem can plausibly sit near a given notehead, so pick the single closest
    one, BY BOTH DISTANCES TOGETHER, each measured as a fraction of the slack
    its own axis is allowed.

    RANKING ON x FIRST AND USING y ONLY TO BREAK ITS TIES IS WRONG, and gets
    the commonest two-voice figure in this repertoire backwards. Where a melody
    note sits above a stem-down chord on the same beat, the two voices' stems
    are at the SAME place horizontally - one at each side of the notehead - so
    the x distances differ by the width of a stem stroke, 0.03 staff spaces,
    which is noise; the y distances differ by a whole space, which is the
    answer. Measured over the library's 98,737 notehead-to-stem lookups, the
    attaching stem sits 0.037 spaces from the notehead's edge at the median
    (0.348 at the 99th percentile) and its end sits 0.175 spaces from the ink
    centre (0.179 at the 90th). Both are sharp; neither decides alone, and
    letting the noisier margin overrule the other picked a stem further away in
    y than the alternative for 90 noteheads across 20 scores, in 49 cases the
    other VOICE's stem. That is a duration error of up to fourfold and a lost
    voice, not a near miss - see the two-voice bars of Dalza's Recercar.

    Nor can y rank them alone: a neighbouring note's stem one notehead-width
    away in x can end nearer this notehead's centre than its own stem does, by
    a few thousandths of a space. Weighting each axis by its own tolerance -
    the numbers already calibrated for how much slack each direction needs -
    picks the note's own stem in both figures without a third constant to tune.

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
        # Rounded so two candidates a floating-point hair apart cannot swap on
        # the platform's last bit. The second key element exists to make the
        # ordering TOTAL rather than merely partial - worth keeping even
        # though it has not once had to decide anything: 0 exact ties on the
        # rounded hypot across 6,203 multi-candidate noteheads in the
        # library, and deleting it changes nothing measured.
        key = (round(math.hypot(dx / xt, dy / yt), 6), round(dy, 3))
        if best_key is None or key < best_key:
            best, best_key = s, key
    return best


def _stem_through_notehead(stems, stem_xs, x0, x1, yc, tol):
    """The stem this notehead is THREADED ONTO, for a notehead sitting part
    way along a chord's shared stem rather than at its end.

    _best_stem deliberately only attaches a notehead at a stem's END, which
    is where the flag or beam that decides the duration lives, and keeping
    that window tight is what stops a neighbouring voice's stem being read
    for flags. But a chord is several noteheads on ONE stem, and every member
    except the one at the stem's end sits far outside that end window - up to
    an octave away. Those members still have to be recognised as the same
    beat, so accept a stem whose x is beside this notehead and whose span
    covers the notehead's centre.

    This does not confuse the two voices of conventional polyphonic writing:
    there the upper voice's stem goes UP and the lower voice's goes DOWN, so
    each points AWAY from the other and neither spans the other's notehead.
    """
    lo, hi = _bounds(stem_xs, min(x0, x1) - tol.stem_x_tol, max(x0, x1) + tol.stem_x_tol)
    slack = tol.stem_span_slack
    best, best_dx = None, None
    for i in range(lo, hi):
        s = stems[i]
        dx = min(abs(s.x - x0), abs(s.x - x1))
        if dx > tol.stem_x_tol:
            continue
        if not (s.y0 - slack <= yc <= s.y1 + slack):
            continue
        if best_dx is None or dx < best_dx:
            best, best_dx = s, dx
    return best


def _has_stem_near(stems, stem_xs, x0, x1, yc, tol):
    return _best_stem(stems, stem_xs, x0, x1, yc, tol,
                      x_tol=tol.rest_stem_x_tol, y_tol=tol.rest_stem_y_tol) is not None


def _at_stem_end(ev, free_y, tol):
    """Is this glyph drawn ON that stem end?

    A flag is joined to the tip of its stem, so the tip falls INSIDE the
    flag's ink - true for 4,669 of the 4,843 flag attachments in the sampled
    library, with the rest inside the pad - and that is the test to make,
    because a flag's ink reaches a long way from the point it attaches at.
    Its centre does not reach it: a 32nd hook's ink centre sits two staff
    spaces from the tip in Leland, and a centre-distance test with a tolerance
    wide enough to cover that would have to reach four spaces to either side,
    far enough to take a neighbouring voice's hook. At the tolerance it did
    have, it read the fixture's 32nds as quarters - a 32-fold error in four
    durations - which is what the flag_ink_pad note measures from the other
    side.

    Falls back to the centre-distance test where the glyph's ink extent is
    unknown (see GlyphEvent.yc) - which is what the whole decoder used
    before the ink was available, tolerance included.
    """
    if ev.ink_measured:
        return ev.ink_y0 - tol.flag_ink_pad <= free_y <= ev.ink_y1 + tol.flag_ink_pad
    return abs(ev.yc - free_y) <= tol.flag_y_tol


def _flag_count_near(flag_events, flag_xs, stem, notehead_yc, tol):
    """Count flag hooks attached at the free end of a stem (the end further
    from the notehead)."""
    free_y = stem.y1 if abs(stem.y1 - notehead_yc) > abs(stem.y0 - notehead_yc) else stem.y0
    lo, hi = _bounds(flag_xs, stem.x - tol.flag_x_tol, stem.x + tol.flag_x_tol)
    hooks = 0
    for i in range(lo, hi):
        ev = flag_events[i]
        if not _at_stem_end(ev, free_y, tol):
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


def _stem_key(stem):
    """Hashable identity for one engraved stem, so every notehead attached to
    it can be recognised as belonging to the same beat."""
    return (round(stem.x, 2), round(stem.y0, 2), round(stem.y1, 2))


def _assign_stem_directions(notes, stems_by_key):
    """Resolve each stem's direction ONCE, from every notehead hanging off it.

    Direction cannot be read from a single notehead's own position on its
    stem: a chord shares one stem that runs PAST all of its noteheads, so
    the end further from any given notehead is on the wrong side for every
    member but the outermost one - reading "the free end is below me,
    therefore stem down" off the top note of an up-stemmed chord inverts it.

    What identifies the direction is which end OVERHANGS the notehead group:
    a stem sticks out roughly an octave beyond the note it points away from
    and stops dead at the note it points toward. Page y grows DOWNWARD, so
    the overhang above the group is (topmost notehead y - stem y0).

    The answer is then CHECKED against which side of the noteheads the stem
    sits on, because an up-stem leaves a notehead at its right edge and a
    down-stem at its left - a convention the library keeps on 99.7% of
    stemmed filled noteheads and 97.7% of half notes (measured over 21700 of
    them). A stem whose side and overhang contradict each other is not this
    notehead's: _best_stem accepts any stem end within about a staff space,
    which in close-spaced two-voice writing the OTHER voice's stem can
    satisfy. Such a stem is dropped rather than believed - the notehead keeps
    no stem at all and is placed by position instead, which can lose
    information but cannot invert it.

    This check is a net, not a ranking, and it cannot be moved into
    _best_stem to do the choosing: the side test needs the MEAN x of every
    notehead on the stem, which is exactly what one candidate notehead does
    not have. It was tried anyway. Two independent attempts to measure how
    many noteheads it leaves with no consistent candidate at all, and how
    many selections it changes, disagreed with each other, so neither count
    is published here - but the selections it does change are not better:
    some individual noteheads move closer to their true stem and others move
    away, and the change is not an improvement on net. The structural reason
    above, which both measurements agree on, is why this is not retried
    rather than the numbers.

    This catches only the contradictory subset. A neighbouring voice's stem
    that happens to sit where this notehead's own stem WOULD sit is
    indistinguishable from it here, and still yields a wrong direction; see
    the residual-risk note on the half-note branch of decode_note_events.
    """
    by_stem = collections.defaultdict(list)
    for n in notes:
        if n.stem_key is not None:
            by_stem[n.stem_key].append(n)
    for key, members in by_stem.items():
        stem = stems_by_key.get(key)
        if stem is None:
            continue
        ys = [m.y for m in members]
        overhang_up = min(ys) - stem.y0
        overhang_down = stem.y1 - max(ys)
        direction = "up" if overhang_up > overhang_down else "down"
        # Mean over the members, so the one notehead a second-interval chord
        # displaces to the far side of the stem cannot outvote the rest.
        mean_x = sum(m.x for m in members) / len(members)
        on_right = stem.x > mean_x
        if on_right != (direction == "up"):
            for m in members:
                m.stem_key = None
            continue
        for m in members:
            m.stem_dir = direction


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


# An augmentation dot is drawn in the MIDDLE OF A SPACE, never on a line -
# measured over every dot in the sampled library (11,405 of them), 11,350 sit
# within a tenth of a space of a space's centre. So there are exactly three
# places a dot can be relative to the note it belongs to, and the engraving
# convention ranks them: the note's OWN space (a note sitting in a space), the
# space ABOVE it (a note on a line - the default), or the space BELOW it (the
# same note, where the space above is taken by another voice).
#
# The ranking is the discriminator, because the distances are not: a dot in
# the space above a note on a line is exactly as far from the note one space
# higher as it is from its own, and a nearest-|dy| test therefore decides
# which by rounding. It did, and getting it wrong gave one notehead two
# vertically stacked dots - which is not what a double dot is; a double dot is
# two dots side by side - while the note the second dot belonged to lost half
# its length. Both real cases are in the library: a chord of noteheads a space
# apart, each with its own raised dot, and a lower voice's dotted whole note
# whose dot is in the space below it.
_DOT_TIERS = (0.0, -0.5, 0.5)
# Engraving jitter around each of those positions. A quarter space is also the
# most it can be: the three positions are half a space apart, so a wider window
# would make them overlap and there would be nothing to rank. Measured over the
# library, the widest deviation from one of them is 0.239 of a space, which this
# just covers.
_DOT_Y_SLACK = 0.25


def _dot_fit(offset, spacing):
    """Which of the three places a dot can sit relative to a note is this, and
    how far off it - (tier, deviation), or None for an offset that is none of
    them. Lower tier is the likelier engraving; see _DOT_TIERS."""
    for tier, expected in enumerate(_DOT_TIERS):
        deviation = abs(offset - expected * spacing)
        if deviation <= _DOT_Y_SLACK * spacing:
            return tier, deviation
    return None


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

    WHICH owner is then decided by where a dot is allowed to be relative to
    its note, in the order an engraver puts it there (see _DOT_TIERS), rather
    than by which notehead's centre is nearest - a question two noteheads a
    space apart answer with a tie.
    """
    counts = collections.Counter()
    if not owners or not dot_events:
        return counts
    # Still the metrics box's x1 here, not an ink edge - left inconsistent
    # with the notehead-to-stem lookups above on purpose, since it was
    # measured against the library and found to have no current effect on
    # which owner a dot is given, the same way the flag8_or_rest_quarter
    # window beside it was.
    owners = sorted(owners, key=lambda e: e.x1)
    owner_x1s = [e.x1 for e in owners]
    for dot in dot_events:
        lo, hi = _bounds(owner_x1s, dot.xc - tol.dot_x_tol, dot.xc + tol.dot_x_back)
        best = None
        best_key = None
        for i in range(lo, hi):
            ev = owners[i]
            # Page y grows downward, so a dot ABOVE its notehead is negative.
            fit = _dot_fit(dot.yc - ev.yc, tol.spacing)
            if fit is None:
                continue
            key = (fit[0], round(fit[1], 3), round(abs(dot.xc - ev.x1), 3))
            if best_key is None or key < best_key:
                best, best_key = ev, key
        if best is not None:
            counts[id(best)] += 1
    return counts


def decode_note_events(page, staff_top, staff_bottom, staff_x0, staff_x1, line_ys,
                       spacing=None):
    """Core decode for one standard-notation staff: returns (NoteEvent list
    sorted by x, stats).

    line_ys: sorted list of the 5 staff line y-coordinates. They are the grid
    a one-glyph rest's half-or-whole reading is measured against (see
    half_or_whole_rest), and an empty list is tolerated the way the spacing
    helper beside it tolerates one - such a staff reports its rests as
    undecided instead of raising. spacing: that staff's line spacing, used to
    scale every geometric tolerance (defaults to the spacing implied by
    line_ys).

    `stats` is the decode's own honesty record and callers are expected to
    ACT on it, not just log it: unknown_glyphs / unknown_ratio say how much
    of this staff's music-font text fell outside the calibrated vocabulary,
    unknown_at_flag_position counts unrecognised glyphs sitting exactly
    where a flag would attach - the shape of "this piece uses 32nd flags,
    grace notes or an articulation we never calibrated", which decodes as
    systematically wrong durations while looking perfectly healthy - and
    undecided_rests counts rests whose value was guessed rather than read,
    and no_stem_noteheads counts filled noteheads that came out of the decode
    with no stem at all, whose duration is therefore a floor rather than a
    reading.
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
        # One-glyph rests whose half-or-whole reading the geometry could not
        # give (see half_or_whole_rest). Read as half rests, and reported so
        # the caller can say the durations on this staff were partly guessed
        # rather than read - see _resolve_rhythm_source.
        "undecided_rests": 0,
        # Filled (or x / diamond) noteheads that reached the end of the decode
        # with NO stem - neither a stem end near the head nor a stem passing
        # through it. Such a head can be a quarter, an eighth, a sixteenth or
        # shorter, and the flag or beam that would say which attaches to the
        # stem that was not found, so nothing here can count one: the head is
        # emitted at its unflagged floor value. The floor is the longest of
        # the possible readings, so one of these reads LONG, never short, and
        # takes its bar's arithmetic with it. Counted for the same reason
        # undecided_rests is - so the caller can say the durations on this
        # staff were partly floored rather than read (see
        # tabextract._resolve_rhythm_source).
        #
        # A half or whole notehead is deliberately NOT counted here even
        # though its stem can be missing just as easily: neither shape can
        # carry a flag or a beam in any notation, so its value is fully
        # determined by the head and a missing stem costs only the voice
        # signal, not the duration.
        "no_stem_noteheads": 0,
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
    # Split "a glyph I could not read" from "a DURATION I could not read".
    # One ratio over both was wrong in each direction at once: it downgraded
    # a perfectly decoded score over two repeat dots and an articulation,
    # while two unrecognised harmonic noteheads on a dense system stayed
    # under the threshold and reported nothing at all - and those two
    # noteheads had had their durations invented and their tab digits
    # attached to the wrong voice. A codepoint-keyed font can be asked which
    # kind it was; a glyph-ID-keyed one cannot, so for Maestro and Opus
    # everything unrecognised still counts (see smufl_unknown_kind).
    unknown_kinds = [(e, smufl_unknown_kind(e.code) if e.smufl else "duration")
                     for e in unknown_in_band]
    unknown_noteheads = [e for e, kind in unknown_kinds if kind == "notehead"]
    unknown_furniture = [e for e, kind in unknown_kinds if kind == "furniture"]
    unknown_meaningful = [e for e, kind in unknown_kinds if kind != "furniture"]

    # Dots first: one dot glyph belongs to exactly one note (see _assign_dots).
    dot_owners = [e for e in staff_events if e.category in NOTEHEAD_CATS or e.category in REST_CATS]
    dot_counts = _assign_dots(dot_owners, dot_events, tol)

    notes = []
    stems_by_key = {}
    undecided_rests = 0
    no_stem_noteheads = 0
    for ev in staff_events:
        if ev.category in NOTEHEAD_CATS:
            stem = None
            # Whether this head's DURATION depends on finding its stem. Set
            # only for the quarter-or-shorter shapes; see no_stem_noteheads.
            duration_needs_stem = False
            if ev.category == "notehead_whole":
                # whole notes never take a stem, flag or beam by definition -
                # don't even look for one (a nearby unrelated stem in a dense
                # chord/2-voice passage would otherwise be a false positive).
                base, flags = 4.0, 0
            elif ev.category == "notehead_half":
                # A half note has a stem but categorically cannot carry a
                # flag or beam, so none is ever counted for one - any that
                # turned up would be a neighbouring voice's.
                #
                # Its stem IS looked up, for the stem's DIRECTION only, which
                # is what says which voice the note belongs to: a lower voice
                # in this repertoire is very often written in half notes, and
                # without its stem it carries no voice signal at all.
                #
                # KNOWN RESIDUAL RISK, accepted deliberately: _best_stem
                # accepts any stem end within about a staff space of the
                # notehead's ink centre, and a real stem end is measured at up
                # to the full 1.17 spacings from it (median 0.175, 90th
                # percentile 0.179, over 86819 attachments), so the window
                # cannot be tightened to the bulk of that distribution without
                # losing the tail of real attachments. Where this note's own
                # stem is missing from the vector pass entirely, a neighbouring
                # voice's stem can therefore be picked, and if it sits in a
                # position consistent with the engraving convention nothing
                # here can tell. That yields a wrong voice for the note and
                # breaks both voices' arithmetic for that bar, which shows up
                # in the overfull-bar count. What _best_stem no longer does is
                # PREFER such a stem to this note's own: it ranks on both
                # distances together rather than on x alone.
                # _assign_stem_directions rejects the subset where the
                # stem's side and its overhang contradict each other; the
                # consistent-looking case remains.
                base, flags = 2.0, 0
                ex0, ex1 = ev.stem_edges
                stem = _best_stem(stems, stem_xs, ex0, ex1, ev.yc, tol)
            else:
                base = 1.0  # filled/x/diamond head: quarter-or-shorter
                flags = 0
                duration_needs_stem = True
                ex0, ex1 = ev.stem_edges
                stem = _best_stem(stems, stem_xs, ex0, ex1, ev.yc, tol)
                if stem is not None:
                    hooks = _flag_count_near(flag_events, flag_xs, stem, ev.yc, tol)
                    beam_levels = _beam_count_near(beams, stem, ev.yc, tol)
                    flags = max(hooks, beam_levels)
            if stem is None and ev.category != "notehead_whole":
                # An inner or far member of a chord: no stem END is near it,
                # but its chord's stem runs through it. Voice grouping only -
                # the duration it was given above stands, and a chord's
                # duration is recomposed from all its members together (see
                # tabextract._stem_group_duration).
                # Same edges as the stem-END lookup above, so a notehead cannot
                # pass one x gate and fail the other.
                ex0, ex1 = ev.stem_edges
                stem = _stem_through_notehead(stems, stem_xs, ex0, ex1, ev.yc, tol)
                if stem is None and duration_needs_stem:
                    # Both stem lookups came up empty on a head whose value
                    # only its stem could have settled. `flags` is still 0 and
                    # will stay 0, so this head goes out at its floor.
                    no_stem_noteheads += 1
            key = None
            if stem is not None:
                key = _stem_key(stem)
                stems_by_key[key] = stem
            notes.append(NoteEvent(ev.xc, ev.yc, base, flags, min(dot_counts.get(id(ev), 0), 2),
                                   False, ev.category, notehead_kind=ev.category,
                                   stem_key=key))
        elif ev.category in REST_CATS:
            # disambiguate flag8_or_rest_quarter by stem proximity: a real
            # stem near it means it's actually a flag glyph, not a rest.
            # (Barlines are excluded from `stems` - see _is_barline - or a
            # rest engraved next to one would vanish here.)
            if ev.category == "flag8_or_rest_quarter":
                # Still the metrics box here, not ev.stem_edges - left
                # inconsistent with the notehead lookups above on purpose,
                # since it was measured rather than assumed: the ink and box
                # windows agree on all 67 of the library's flag8_or_rest_quarter
                # glyphs, so switching this one has no current effect.
                if _has_stem_near(stems, stem_xs, ev.x0 - tol.rest_stem_pad,
                                  ev.x1 + tol.rest_stem_pad, ev.yc, tol):
                    continue  # it's a flag - counted via the notehead's stem (see FLAG_HOOKS)
                cat = "rest_quarter"
            else:
                cat = ev.category
            if cat == "rest_half_whole":
                base, decided = half_or_whole_rest(ev.yc, line_ys, tol.spacing)
                if not decided or not ev.ink_measured:
                    # Both halves of the reading have to hold: the parity has
                    # to be one of the two an engraver draws, AND it has to
                    # have been measured on the ink. Without the outline the
                    # position falls back to the baseline, which sits ON the
                    # grid for exactly this glyph in every calibrated font -
                    # an offset of zero, which is not evidence for either
                    # answer even when rounding lands it inside the window.
                    base, undecided_rests = 2.0, undecided_rests + 1
            else:
                # A font that spells the rest's value in the glyph itself
                # (see REST_VALUES) needs no positional guess.
                base = REST_VALUES.get(cat, 1.0)
            notes.append(NoteEvent(ev.xc, ev.yc, base, 0, min(dot_counts.get(id(ev), 0), 2),
                                   True, cat))

    notes.sort(key=lambda n: n.x)
    _assign_stem_directions(notes, stems_by_key)
    _mark_ties(notes, curves, tol)

    # Unrecognised glyphs sitting where a flag attaches are the dangerous
    # ones: they mean this piece's flag/hook vocabulary is wider than the
    # calibrated table (32nd flags, grace notes), so durations are wrong in
    # a way nothing else in the decode would notice. An accent or a fermata
    # sits at a stem's free end too, which is why furniture is excluded -
    # counting it here reported a missed flag on scores that had none.
    suspect = 0
    for u in unknown_meaningful:
        stem = _best_stem(stems, stem_xs, u.x0, u.x1, u.yc, tol,
                          x_tol=tol.flag_x_tol, y_tol=tol.flag_y_tol)
        if stem is None:
            continue
        free_y = stem.y1 if abs(stem.y1 - u.yc) > abs(stem.y0 - u.yc) else stem.y0
        if _at_stem_end(u, free_y, tol):
            suspect += 1

    stats.update({
        "unknown_glyphs": len(unknown_meaningful),
        "unknown_ratio": (len(unknown_meaningful) / len(staff_events)) if staff_events else 0.0,
        "unknown_noteheads": len(unknown_noteheads),
        "unknown_furniture": len(unknown_furniture),
        "unknown_at_flag_position": suspect,
        "unknown_gid_or_name_sample": sorted(
            {(u.family, u.calibration_key) for u in unknown_meaningful}, key=repr)[:20],
        "unknown_notehead_sample": sorted(
            {(u.family, u.calibration_key) for u in unknown_noteheads}, key=repr)[:20],
        "band_glyphs": len(staff_events),
        "note_events": len(notes),
        "stem_count": len(stems),
        "beam_segment_count": len(beams),
        "curve_count": len(curves),
        "undecided_rests": undecided_rests,
        "no_stem_noteheads": no_stem_noteheads,
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


_METER_SYMBOL_CATS = ("common_time", "cut_time")


def _stacked_digit_pairs(window, mid):
    """Numerator/denominator digit-run pairs in this window: runs above and
    below `mid` whose x-spans OVERLAP. Returns (pairs, why_not), pairs sorted
    widest overlap first.

    Overlapping x is what makes two digits a METER rather than two unrelated
    numerals, and it is the only structural test available - which is why both
    readers of a printed meter share this one function.
    _signature_from_window needs the meter's VALUE; _meter_left_edge, for
    decode_key_signature, needs only its POSITION. Neither may treat a LONE
    digit as a meter: the small 8 of an octave-transposing treble clef is a
    digit glyph engraved at the clef, and it is routine in guitar notation.
    """
    digits = [e for e in window if e.category in DIGIT_CATS]
    if len(digits) < 2:
        return [], f"only {len(digits)} time-signature digit glyph(s) found"

    num_digits = [e for e in digits if e.yc < mid]
    den_digits = [e for e in digits if e.yc >= mid]
    if not num_digits or not den_digits:
        return [], "digit glyphs found but not split across a numerator/denominator band"

    pairs = []
    for nc in _group_digit_clusters(num_digits):
        n0, n1 = _cluster_span(nc)
        for dc in _group_digit_clusters(den_digits):
            d0, d1 = _cluster_span(dc)
            overlap = min(n1, d1) - max(n0, d0)
            if overlap > 0:
                pairs.append((overlap, nc, dc))
    if not pairs:
        return [], "digit glyphs found but numerator/denominator x-spans don't align"

    pairs.sort(key=lambda t: -t[0])
    return pairs, None


def _meter_left_edge(window, mid):
    """The x of the leftmost printed meter in this window, or (None, why).

    A meter is a common/cut-time symbol, or a numerator digit run stacked over
    a denominator run - see _stacked_digit_pairs for why a lone digit is not
    one. Used as the right-hand boundary of the key signature, where getting
    it too far left silently empties the key signature instead of failing.
    """
    edges = [e.x0 for e in window if e.category in _METER_SYMBOL_CATS]
    pairs, why_not = _stacked_digit_pairs(window, mid)
    for _overlap, nc, dc in pairs:
        edges.append(min(_cluster_span(nc)[0], _cluster_span(dc)[0]))
    if not edges:
        return None, why_not or "no common-time symbol and no stacked meter digits"
    return min(edges), None


def _signature_from_window(window, mid):
    """Resolve one x-window of glyphs into a time signature, or (None, why)."""
    for e in window:
        if e.category == "common_time":
            return (4, 4), "common_time symbol"
        if e.category == "cut_time":
            return (2, 2), "cut_time symbol"

    # A real time signature stacks its numerator and denominator digit runs at
    # the same x column - take the pair whose x-spans overlap most, and require
    # that match to be unambiguous (no near-tied second-best pair using a
    # DIFFERENT cluster) before trusting it. If nothing lines up cleanly,
    # report "not detected" rather than ever returning a confidently-wrong
    # guess.
    pairs, why_not = _stacked_digit_pairs(window, mid)
    if not pairs:
        return None, why_not

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


# How far into the staff a clef and the meter behind it reach when nothing is
# engraved between them.
TS_LEAD_SPACINGS = 8.8  # was a flat 45pt at the reference staff size
# The furthest the search may reach even so. Seven accidentals plus the meter
# fit inside this - it is the window decode_key_signature reads for the same
# run of glyphs (_KEY_LEAD_SPACINGS).
_TS_MAX_LEAD_SPACINGS = 20.0
# How far past the last key-signature accidental the meter's own digits can
# start. Measured on the library's four-sharp staves, the gap from the last
# sharp's right edge to the numerator's left edge is about 1.6 staff spaces;
# this is generous against that and is capped by the first note anyway.
_TS_AFTER_ACCIDENTALS_SPACINGS = 4.0
# How far off the staff a SOUNDING glyph (notehead or rest) is still looked
# for, when deciding where the "nothing may be read past what already
# sounds" clamp falls. A note is not always drawn ON the staff: guitar's open
# strings on a treble-8vb staff sit low - E on the bottom line itself, A a
# space below it, D one ledger line below that - and the deepest of those
# measured on the sampled library (an open D) reads at up to 2.02 staff
# spaces off the staff. Reading the clamp from the same +-1 spacing band as
# the accidentals and the meter's own digits made it blind to exactly that
# note, so it never fired for a part whose leading music is an open low
# string - which is routine for guitar. This cannot widen the window itself,
# only clamp it earlier, so there is no matching risk of pulling in a
# neighbouring staff's key signature or meter the way widening `band` would.
_SOUNDING_BAND_SPACINGS = 2.5


def _first_sounding_x(events, staff_top, staff_bottom, x_lo, x_hi, tol):
    """The x of the leftmost notehead or rest in [x_lo, x_hi], read from a
    y-band wider than the staff itself - see _SOUNDING_BAND_SPACINGS - or
    None if nothing sounds there."""
    lo = staff_top - tol.spacing * _SOUNDING_BAND_SPACINGS
    hi = staff_bottom + tol.spacing * _SOUNDING_BAND_SPACINGS
    played = [e.x0 for e in events
              if lo <= e.yc <= hi and x_lo <= e.x0 <= x_hi
              and (e.category in NOTEHEAD_CATS or e.category in REST_CATS)]
    return min(played) if played else None


def decode_time_signature(page, staff_top, staff_bottom, staff_x0, spacing=None):
    """The time signature printed at the START of this staff, or (None, why).

    The window has to hold the clef, the KEY SIGNATURE and the meter, in that
    order, and a flat distance from the staff's left edge cannot: three or
    four accidentals push the digits past 8.8 staff spaces and the meter is
    simply never looked at. Measured on the sampled library, that lost the
    printed meter on 49 of 292 first pages - every one of them a score whose
    key signature has three or more accidentals - and each of those scores was
    then barred as 4/4 from end to end.

    So the window ends at whichever comes first of: a fixed reach past the
    last key-signature accidental, and the first thing on the staff that
    SOUNDS. The second half is what makes widening safe rather than merely
    wider: a meter is always engraved before the music it governs, so nothing
    beyond the first notehead or rest can be one, and simply enlarging the
    flat distance would instead start reading numerals out of the music -
    a fingering, a tuplet number, a string number - and returning them as a
    confident meter, which is a worse failure than the one being fixed. This
    IS measured rather than assumed safe: removing both the clamp and the
    accidental-keyed reach changes nothing on the sampled library (0 of 297
    scores differ), which says the two guards have not yet been caught doing
    their job here - not that either is free to drop. The clamp specifically
    reads a wider band than the staff itself off to the side (see
    _SOUNDING_BAND_SPACINGS) so a ledger-line note - an open low string on
    guitar - still clamps it.

    Only the leading window is read. A meter change engraved part-way along a
    system is read by decode_meter_after_barline, which shares this
    construction and adds its own guard against reading a courtesy signature
    for the NEXT system as a change at THIS barline; tabextract carries both
    forward as a timeline (see _build_time_signature_timeline).
    """
    tol = _Tol(spacing if spacing else (staff_bottom - staff_top) / 4.0)
    glyphs = extract_glyph_events(page)
    if not glyphs.events:
        return None, "no music glyphs found"
    mid = (staff_top + staff_bottom) / 2
    band = [e for e in glyphs.events
            if staff_top - tol.spacing <= e.yc <= staff_bottom + tol.spacing
            and staff_x0 - tol.drawing_x_pad <= e.x0
            <= staff_x0 + tol.spacing * _TS_MAX_LEAD_SPACINGS]
    lead = staff_x0 + tol.spacing * TS_LEAD_SPACINGS
    accidentals = [e for e in band if e.category in KEY_ACCIDENTAL_CATS]
    if accidentals:
        lead = max(lead, max(e.x1 for e in accidentals)
                   + tol.spacing * _TS_AFTER_ACCIDENTALS_SPACINGS)
    first_sound = _first_sounding_x(
        glyphs.events, staff_top, staff_bottom,
        staff_x0 - tol.drawing_x_pad, staff_x0 + tol.spacing * _TS_MAX_LEAD_SPACINGS, tol)
    if first_sound is not None:
        lead = min(lead, first_sound)
    return _signature_from_window([e for e in band if e.x0 <= lead], mid)


# How far past a barline a meter change printed at it can start when nothing
# is engraved between the barline and the meter. It is the first thing after
# the barline, so this is small on purpose - see decode_meter_after_barline.
_MID_SYSTEM_LEAD_SPACINGS = 5.0
# The furthest a mid-system reach may extend even so, for the same reason and
# at the same size as the opening window's cap: a key change at the same
# barline can push the meter's digits out exactly as it does at a staff's own
# start (_TS_MAX_LEAD_SPACINGS), and this is the window decode_key_signature
# would need if a key ever changed mid-system too.
_MID_SYSTEM_MAX_LEAD_SPACINGS = _TS_MAX_LEAD_SPACINGS
# How close the DECODED meter's own right edge may sit to the staff's right
# edge before it is refused as a courtesy signature for the NEXT system
# rather than a change at THIS barline. An end-of-system courtesy signature -
# the key and meter for the system that follows, engraved as the last thing
# on this one - sits well within a key-signature-anchored reach of a barline
# several spaces earlier, so accepting it here would start that change a bar
# early. Measured on the two scores this guard exists for: Kaine Salvation's
# courtesy 6/8 (four sharps, then the meter, printed after the system's last
# barline) ends 0.83 staff spaces short of the staff's own right edge; Into
# the Wilderness's real mid-system 6/4 (three flats, printed the same way)
# ends 55.8 spaces short of it. Two orders of magnitude apart, so this sits
# generously between them.
_END_OF_SYSTEM_GUARD_SPACINGS = 4.0


def decode_meter_after_barline(page, staff_top, staff_bottom, barline_x, staff_x1,
                                spacing=None):
    """The time signature printed immediately after a barline part-way along
    this staff, or (None, why).

    A meter change is engraved where it takes effect, and that can be a
    barline in the middle of a system - the bars before it in the same system
    are still in the previous meter. This window shares decode_time_signature's
    construction, for the same reason that reader needs it: a key change at
    the SAME barline pushes the meter's digits out past a flat reach exactly
    the way it does at a staff's own start, and a window sized only for
    "nothing between the barline and the meter" drops the meter silently when
    something is. So this reaches past the last accidental printed just after
    the barline, capped, and clamped by the first thing that sounds (read from
    a band wider than the staff itself - see _SOUNDING_BAND_SPACINGS).

    That reach is not safe here on its own, the way it is at the opening: a
    courtesy signature for the NEXT system - printed as the last thing on
    THIS one, to preview a key or meter change before the system break - sits
    well within reach of a barline several spaces earlier once the window can
    see past a key change. Accepting it would start that change a bar early,
    which is worse than not reading it at all. So a candidate whose own
    decoded position lands within _END_OF_SYSTEM_GUARD_SPACINGS of the
    staff's right edge is refused as a courtesy instead - see that constant
    for the measurement this is keyed on.

    The digit glyphs also spell tuplet numbers, and the numerator/denominator
    overlap test on its own would accept an UNEQUAL pair of those as a meter:
    a triplet "3" in one voice stacked against a "4" or "8" from a different
    tuplet bracket, fingering, or string number in another. (An equal pair
    such as two triplet "3"s cannot pass this far - 3/3 is not a usable
    denominator and time_signature_is_valid rejects it upstream.) A meter is
    always printed before the music it governs, so a notehead or rest to the
    left of the candidate proves the digits found are not one.

    That guard is blind on the UP-STEM side: what `_detect_barlines` offers as
    a candidate position is often not a barline at all but a stem, and an
    up-stem attaches at its own notehead's RIGHT edge, so the notehead that
    owns the stem sits to the LEFT of the candidate x - outside the window
    this function ever looks at (`barline_x < e.x0`, strictly right). A stem
    mistaken for a barline is therefore never caught by its own note.

    Measured rather than extended on a guess: across the library, 152 of
    21,680 candidate positions are accepted as a meter, and 23 of those 152
    have a notehead within one staff spacing to the LEFT of the candidate.
    That is not 23 hazards - it is what a REAL barline looks like too, since
    the previous bar's last note routinely ends close to the barline that
    follows it. Proximity on the left does not distinguish "this candidate is
    that note's own stem" from "this candidate is a genuine barline with an
    ordinary note before it", so a look-back guard built on it would refuse
    correct meters about as often as it caught wrong ones. Left undone for
    that reason rather than fixed; a real fix needs a way to tell a stem from
    a barline that does not yet exist at this layer.
    """
    tol = _Tol(spacing if spacing else (staff_bottom - staff_top) / 4.0)
    glyphs = extract_glyph_events(page)
    if not glyphs.events:
        return None, "no music glyphs found"
    mid = (staff_top + staff_bottom) / 2
    band = [e for e in glyphs.events
            if staff_top - tol.spacing <= e.yc <= staff_bottom + tol.spacing
            and barline_x < e.x0
            <= barline_x + tol.spacing * _MID_SYSTEM_MAX_LEAD_SPACINGS]
    if not band:
        return None, "nothing is printed just after this barline"
    lead = barline_x + tol.spacing * _MID_SYSTEM_LEAD_SPACINGS
    accidentals = [e for e in band if e.category in KEY_ACCIDENTAL_CATS]
    if accidentals:
        lead = max(lead, max(e.x1 for e in accidentals)
                   + tol.spacing * _TS_AFTER_ACCIDENTALS_SPACINGS)
    first_sound = _first_sounding_x(
        glyphs.events, staff_top, staff_bottom, barline_x,
        barline_x + tol.spacing * _MID_SYSTEM_MAX_LEAD_SPACINGS, tol)
    if first_sound is not None:
        lead = min(lead, first_sound)
    window = [e for e in band if e.x0 <= lead]
    if not window:
        return None, "nothing is printed just after this barline"
    ts, why = _signature_from_window(window, mid)
    if ts is None:
        return None, why
    meter_x0, why_no_meter = _meter_left_edge(window, mid)
    if meter_x0 is None:
        return None, why_no_meter
    played = [e.x0 for e in window
              if e.category in NOTEHEAD_CATS or e.category in REST_CATS]
    if played and min(played) < meter_x0:
        return None, (
            "digit glyphs just after this barline sit behind a note or a rest, so they are "
            "not a meter printed at the barline"
        )
    meter_glyphs = [e for e in window
                    if e.category in DIGIT_CATS or e.category in _METER_SYMBOL_CATS]
    meter_x1 = max(e.x1 for e in meter_glyphs)
    if staff_x1 - meter_x1 < tol.spacing * _END_OF_SYSTEM_GUARD_SPACINGS:
        return None, (
            "the printed meter sits at the end of the system, so it reads as a courtesy "
            "signature for the system that follows rather than a change at this barline"
        )
    return ts, why


# ---------------------------------------------------------------------------
# Key signature
# ---------------------------------------------------------------------------

# The accidental glyphs a key signature is built from. The parenthesised
# variants are cautionary accidentals printed over a note and are never part
# of one.
KEY_ACCIDENTAL_CATS = {"sharp": 1, "flat": -1}
# How far into the staff the clef + up to seven accidentals + the meter
# reach. The same reach decode_time_signature allows itself for the same run
# of glyphs, and for the same reason: a seven-accidental signature pushes the
# meter well past the 8.8 spacings a clef and a meter alone need.
_KEY_LEAD_SPACINGS = _TS_MAX_LEAD_SPACINGS
# A key signature holds at most seven accidentals (C flat major / C sharp
# major). More than that in the leading window is not a key signature.
_MAX_KEY_ACCIDENTALS = 7


def decode_key_signature(page, staff_top, staff_bottom, staff_x0, spacing=None):
    """The key signature printed at the START of this staff, as a MusicXML
    `fifths` count (positive for sharps, negative for flats), or (None, why).

    Only ever read from a staff that also prints its METER, and only from the
    accidentals engraved to the LEFT of that meter. That restriction is what
    makes the reading safe rather than merely plausible: an accidental
    applying to a single note is engraved immediately before its notehead,
    which puts it to the RIGHT of the meter, while a key signature is always
    between the clef and the meter. Without the meter as a right-hand
    boundary, a piece in C major whose first note happens to be an F sharp
    reads as G major.

    "Meter" here means a real one - a common/cut-time symbol, or a numerator
    digit run stacked over a denominator run (_meter_left_edge). Accepting any
    digit as the boundary is not good enough, and fails in the one direction
    that cannot be noticed: the small 8 of an octave-transposing treble clef is
    a digit glyph engraved AT the clef, so it collapses the window to nothing,
    the accidental run comes out empty, and the function returns a confident 0.
    A piece in E major would be spelled as C major and reported as decoded. The
    same goes for any stray numeral between two accidentals, which would
    truncate the run and read four sharps as two.

    Engravers print the meter once, at the start of the piece and again only
    where it changes, but reprint the key signature on every system - so in
    practice this answers on the first system and declines on the rest, which
    is all a document-level key needs.

    A wrong answer here costs nothing but an odd enharmonic spelling: fret,
    string and sounding pitch are computed from the tuning and are unaffected
    by the key (see musicxml.spell_pitch). That asymmetry is why declining
    is cheap and why the default when nothing is found is plain `fifths` 0.
    """
    tol = _Tol(spacing if spacing else (staff_bottom - staff_top) / 4.0)
    glyphs = extract_glyph_events(page)
    if not glyphs.events:
        return None, "no music glyphs found"

    lead = tol.spacing * _KEY_LEAD_SPACINGS
    band = [e for e in glyphs.events
            if staff_top - tol.spacing <= e.yc <= staff_bottom + tol.spacing
            and staff_x0 - tol.drawing_x_pad <= e.x0 <= staff_x0 + lead]
    if not band:
        return None, "no music glyphs at the start of this staff"

    mid = (staff_top + staff_bottom) / 2
    meter_x0, why_no_meter = _meter_left_edge(band, mid)
    if meter_x0 is None:
        return None, (
            "no meter is printed at the start of this staff, so there is no boundary "
            f"separating a key signature from an accidental on the first note ({why_no_meter})"
        )

    # Right of the clef, left of the meter. The LEFTMOST clef's right edge: a
    # courtesy clef further along the system would otherwise push the window's
    # left wall past the accidentals and empty it.
    clefs = [e for e in band if e.category == "clef"]
    left = min(clefs, key=lambda e: e.x0).x1 if clefs else (staff_x0 - tol.drawing_x_pad)

    if meter_x0 <= left:
        # A degenerate window has no accidentals in it for the same reason an
        # empty key signature does, and the two must not be confused - "no
        # sharps or flats" is an answer, "I could not look" is not.
        return None, (
            "the printed meter is not to the right of the clef, so no key-signature "
            "window can be established on this staff"
        )

    run = [e for e in band
           if e.category in KEY_ACCIDENTAL_CATS and left <= e.x0 < meter_x0]
    if not run:
        return 0, "no accidentals between the clef and the meter"

    signs = {KEY_ACCIDENTAL_CATS[e.category] for e in run}
    if len(signs) > 1:
        return None, "both sharps and flats between the clef and the meter"
    if len(run) > _MAX_KEY_ACCIDENTALS:
        return None, (
            f"{len(run)} accidentals between the clef and the meter - more than a key "
            "signature can hold"
        )
    return signs.pop() * len(run), (
        f"{len(run)} accidental glyph(s) between the clef and the printed meter"
    )
