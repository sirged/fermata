"""Guitar tablature extraction from vector-engraved PDF scores.

Staff systems are located from vector line primitives (page.get_drawings()):
a run of 6 evenly spaced long horizontal lines is a tab staff, 5 is a
standard staff. Fret numbers come from text spans that are pure digits,
assigned to a tab staff and string by proximity - font name is deliberately
not used as a filter, since different engravers (Finale, Sibelius, Opus)
pick different fonts for fret numbers. Barlines come from vertical line
primitives spanning a staff's height.

Rhythm and time signature: when a tab staff is paired with a standard-
notation staff above it IN ITS OWN SYSTEM (score+tab layout) drawn in a
music font glyph_rhythm knows how to read, durations and the time signature
are decoded directly from the engraved notehead/stem/flag/beam/dot and digit
glyphs (see glyph_rhythm.py) - not guessed. That covers the common case
(Finale Maestro and Sibelius Opus exports, the bulk of the library).

Every tab staff resolves to exactly one rhythm SOURCE (see the PROV_*
constants and _resolve_rhythm_source), and the document's rhythm warnings
and confidence are derived from the collected set of those in one place
(_rhythm_report). The source is the decoder only when it can be trusted:

  - the notation staff must be in the tab staff's own system, within a
    spacing-relative distance, over the same horizontal extent, and read by
    no other tab staff (_pair_standard_staves);
  - the music font must be a recognised family AND match its calibrated
    glyph fingerprint (glyph_rhythm.maestro_fingerprint_ok);
  - the decoder's own unknown-glyph stats must be low enough that its
    durations mean what they say (_UNKNOWN_RATIO_*).

Where any of that fails - a raster page, a CFF-flavor font embedding, an
unrecognised or un-fingerprinted font, an ambiguous or absent notation
staff, or a vocabulary the decoder was never calibrated for - rhythm falls
back to a weaker heuristic: durations inferred from the horizontal spacing
between note columns, normalized to a measure's quarter-note budget and
snapped to the nearest plain duration (no dotted notes or ties modeled), and
the time signature falls back to a best-effort scan for stacked plain-text
digits that frequently fails outright. An honest fallback that says so is
always preferred to plausible-looking wrong rhythm at high confidence.

Time signature is a TIMELINE, not one document-wide value: it is read at the
start of every notation system and carried forward, so a score that changes
meter part-way through (the library contains several) gets the change
emitted at the bar where it happens rather than having every later bar
measured against the opening meter. Any signature that reaches the emitted
\\ts is validated first - it is also what gets stored, and alphaTab throws
outright on something like `\\ts 3 12`.

Which path was used, and any resulting gaps, are surfaced through
ExtractionResult.warnings, .confidence and .rhythm_provenance rather than
papered over; callers can also offer a manual time-signature override (see
extract()'s time_signature argument) for when auto-detection comes up empty.

Every one of those gaps is stated TWICE, and both halves are required. The
count is a field on ExtractionResult, so the data stays queryable; the same
fact is also written into .warnings as a sentence, because that is the only
one of the two anything downstream reads on its own - a list of strings gets
looped over and displayed, a new field gets ignored by every consumer written
before it existed. A count added without its sentence is a measurement nobody
is ever told; see .bars_padded / "bar(s) contain silence" for the shape both
halves take.

VOICES: classical and fingerstyle guitar is polyphonic - a melody sounding
OVER an independent bass line - so a bar's notes are not one sequence. Each
notehead is grouped with the others on ITS OWN STEM (a chord is one beat),
and where two stems genuinely sound at the same onset the bar is split into
concurrent voices by stem direction, which is the signal engravers use for
exactly this (see _assign_group_voices). Each voice is then filled out with
rests so it accounts for the bar on its own, and the bar is emitted with
alphaTex's `\\voice` separator. Assembling every note into one sequence
instead made a bar hold the SUM of its voices - typically about double its
meter - while every individual duration in it was decoded correctly.

INFERRED SILENCE: those filling rests are silence nothing on the page said was
there, and they are marked as such (musicxml.InferredRest). They exist because
a voice that entered late needs its leading silence or every note in it sounds
early, so removing them would move notes rather than tell the truth - but they
are not evidence of anything, so they do not count towards a bar adding up
(_bar_conformance), and the MusicXML writes them as `<forward>` rather than as
a rest (profile Rule 14). Counting them was how a score missing ninety notes
came to report every bar conformant at high confidence.

Stem direction alone is not enough and is not used alone: in ordinary
single-voice writing stems also flip with pitch around the middle line, so
splitting on direction wherever it changes would shred a monophonic melody
into two voices at every crossing. Simultaneity is what distinguishes the
two, because one voice never has two stems at one onset.

OUTPUT: the canonical format is MusicXML (see musicxml.py and the profile in
docs/musicxml-tab-profile.md), with the same music also emitted as alphaTex
for the transcription editor to work in. The measure arithmetic that used to
be checkable only by a script written here is a conformance rule of that
profile - every sounding voice's durations sum to the measure's duration - so
any MusicXML tool can now find a bar that does not add up. _bar_conformance
counts them from the same beats model the emitters read, in both directions,
counting only what was actually read off the page, and nothing is padded or
trimmed to make the sums come out.

KNOWN LIMITATIONS, deliberately not modeled: tuplets, ties, and any bar
whose voices the stems do not separate. Bars that still do not add up are
counted and reported (see _bar_conformance) rather than smoothed over.
"""

from __future__ import annotations

import bisect
import collections
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import fitz  # PyMuPDF

from . import glyph_rhythm as glyph
from . import musicxml as mxl

DEFAULT_TUNING = ["E2", "A2", "D3", "G3", "B3", "E4"]
DROP_D_TUNING = ["D2", "A2", "D3", "G3", "B3", "E4"]

# TUNING INSTRUCTIONS THIS EXTRACTOR RECOGNISES AND DOES NOT APPLY.
#
# Detection only, and deliberately nothing more. `tuning` is not adjusted, the
# emitted MusicXML is unchanged, and no sounding pitch moves - parsing these
# properly is issue #80's job. What is here is the narrower thing that has to
# exist before the interface may describe a tuning at all: knowing that the page
# carries an instruction we did not read, so the reading can be reported as
# INCOMPLETE rather than as a reading.
#
# Measured across the library: of the 100 scores that carry a "Drop D" label,
# 41 also carry one of these - 9 saying to tune every string down a half step
# (so the recorded tuning array is a semitone out) and 32 naming a capo (so
# every sounding pitch is out). That is 14% of the library, and until this
# existed all 41 were describable as a tuning that had been read off the page.
# Recognising a NAME is not reading a tuning.
_HALF_STEP_DOWN_RE = re.compile(
    r"(?:tune[d]?\s+)?(?:down|lower)\s+(?:a\s+|one\s+)?(?:half|1/2|½)[\s-]*(?:step|tone)"
    r"|(?:half|1/2|½)[\s-]*(?:step|tone)\s+(?:down|lower|flat)",
    re.IGNORECASE,
)
# "capo", but never the "da capo" of a repeat instruction, which is about where
# to go back to and nothing to do with the left hand. Classical sheet music is
# full of them, and matching one would be its own false statement: claiming the
# page carries a tuning instruction it does not.
#
# The trailing number is for the message only - horizontal whitespace, so a
# bare "Capo" at the end of a line cannot adopt a number from the next one.
_CAPO_RE = re.compile(
    r"(?<!da )(?<!dal )\bcapo\b[ \t]*[:\-]?[ \t]*(?:at[ \t]*|on[ \t]*)?(?:fret[ \t]*)?"
    r"(\d{1,2}|[IVX]{1,4})?",
    re.IGNORECASE,
)


def unread_tuning_instructions(text: str) -> list[str]:
    """Tuning instructions present in this page's text that we do not apply.

    Short phrases, written to be shown to a person as they are - each one names
    what was seen, not what it would have meant.
    """
    found = []
    if _HALF_STEP_DOWN_RE.search(text):
        found.append("tune down a half step")
    capo = _CAPO_RE.search(text)
    if capo:
        found.append(f"capo {capo.group(1)}" if capo.group(1) else "capo")
    return found

# Plain (non-dotted) duration budgets, in quarter-note units, used to snap
# spacing-derived durations to an alphaTex duration code.
_PLAIN_DURATIONS = [(4.0, 1), (2.0, 2), (1.0, 4), (0.5, 8), (0.25, 16), (0.125, 32)]


@dataclass
class ExtractionResult:
    extractable: bool
    reason: str | None = None
    # The canonical output: a MusicXML 4.0 score-partwise document following
    # the profile in docs/musicxml-tab-profile.md. `alphatex` is the same
    # music in the renderer's own text format, kept because it is what the
    # transcription editor is comfortable to hand-edit in.
    musicxml: str | None = None
    alphatex: str | None = None
    title: str | None = None
    tempo: int | None = None
    tuning: list[str] = field(default_factory=lambda: list(DEFAULT_TUNING))
    tuning_label: str | None = None
    # Tuning instructions found printed on the page and NOT applied to `tuning`
    # - see unread_tuning_instructions. Non-empty means `tuning` is known to be
    # incomplete, so no reader may describe it as having been read.
    tuning_unread: list[str] = field(default_factory=list)
    time_signature: tuple[int, int] | None = None
    time_signature_source: str = "not detected"
    # MusicXML's `fifths`: positive for sharps, negative for flats. Only ever
    # used to choose between enharmonic spellings of the same sounding pitch
    # (see musicxml.spell_pitch), so 0 is a safe default rather than a claim.
    key_fifths: int = 0
    key_signature_source: str = "not detected"
    bars: int = 0
    beats: int = 0
    notes: int = 0
    # How many bars fail the MusicXML profile's Rule 8, and how - the same
    # numbers the warnings state in prose, as data. `bars_defective` counts a
    # bar once whichever way it is wrong, so it is the one to compare against
    # what an independent MusicXML tool reports; overfull + short double-counts
    # a bar that is wrong in both directions at once. See _bar_conformance.
    bars_overfull: int = 0
    bars_short: int = 0
    bars_defective: int = 0
    bars_measured: int = 0
    # Bars holding silence that was deduced from the time signature instead of
    # read from a rest printed on the page, the bar numbers (1-based, as the
    # emitted measures are numbered), and how many quarter notes of it there
    # are. Reported as data and not only in the warning prose, because "which
    # bars are partly invented" is the question a reader comparing the
    # transcription against the PDF actually has. See _pad_voice_to_budget.
    bars_padded: int = 0
    padded_bars: list[int] = field(default_factory=list)
    inferred_rest_quarters: float = 0.0
    # Bars nothing was read from at all, and which ones. These are NOT counted
    # into bars_defective: the bar of rests they emit does add up to its meter,
    # so folding them in would make these figures disagree with what a consumer
    # computes from the emitted file. They are a separate statement - "nothing
    # here was read" - and they do count towards the reported confidence.
    bars_unread: int = 0
    unread_bars: list[int] = field(default_factory=list)
    # Total tab / standard staff systems found across the whole document
    # (summed across pages) - same definition analyze() uses, not a
    # per-page maximum.
    tab_staff_count: int = 0
    standard_staff_count: int = 0
    pages_processed: int = 0
    confidence: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # How many tab staves resolved to each rhythm source (see the PROV_*
    # constants). Reported so a caller can see the mix directly instead of
    # having to parse it back out of the confidence string.
    rhythm_provenance: dict = field(default_factory=dict)
    # WHICH bars the staves behind those counts produced. A count of staves
    # says how much of a score's rhythm was not read from its glyphs; only
    # these say which music that was, and a bar number is the one coordinate a
    # reader can carry back to the PDF - same reason `padded_bars` exists
    # beside `bars_padded`. `spacing_bars` are bars whose durations came from
    # the horizontal gaps between noteheads rather than from the noteheads;
    # `degraded_bars` are bars read from the engraving with something on their
    # staff left unread. Both are stated in the warning prose as well, because
    # a field on its own reaches nobody.
    spacing_bars: list[int] = field(default_factory=list)
    degraded_bars: list[int] = field(default_factory=list)
    # The same two facts as COUNTS, and the reason they are separate fields
    # rather than left inside `rhythm_provenance` is issue #117 (see also #103
    # and #115, which are the same failure): `rhythm_provenance` is not stored,
    # is not on the API response, and is read by no interface code, so 110
    # staff systems' worth of durations that were guessed from the horizontal
    # gaps between noteheads presented identically to durations that were read.
    # The warning prose does say it - and a bare count field on its own would
    # have reached nobody either, which is exactly how that got missed - so
    # these two travel the whole way: through api._BAR_KEYS into the stored
    # confidence blob, out through TranscriptionOut, and into the disclosure
    # panel's DISCLOSURE_ROWS, where each is shown beside the bar numbers
    # `spacing_bars` / `degraded_bars` already carried.
    #
    # `staves_spacing_rhythm` is how many staff systems' durations came from
    # note spacing rather than from glyphs; `staves_degraded_rhythm` is how
    # many were read from the engraving with something on them left unread.
    # Both also cap the reported rhythm confidence - see _rhythm_report - so a
    # score with any spacing-derived staff cannot present as fully read.
    staves_spacing_rhythm: int = 0
    staves_degraded_rhythm: int = 0
    # Notation staves whose printed meter was REFUSED because a glyph with no
    # category sat among the digits it did read (issue #129). An unmapped GID
    # in a Finale subset or an unmapped PUA name in a Sibelius one used to be
    # dropped from the meter window rather than blocking it, so a 10/8 whose
    # '0' the decoder does not know assembled from the digits that were left
    # and came out as a confident (1, 8), labelled as read directly from the
    # digits. A partial digit read is now not a meter at all; this counts how
    # often that refusal fired, because a refusal that is never counted is
    # indistinguishable from a staff that simply printed no meter.
    meter_digits_unreadable: int = 0
    # Filled noteheads that came out of the decode with no stem, and how many
    # notation staves carried at least one. Such a head can be a quarter or
    # anything shorter, and the flag or beam that would say which attaches to
    # the stem that was not found, so it is emitted at its unflagged floor - a
    # duration that is a guess, and one that always errs LONG. Counted here for
    # the same reason `bars_padded` is: it is the size of what was invented,
    # and it cannot be recovered from any other figure on this result.
    notes_no_stem: int = 0
    staves_no_stem: int = 0
    # Augmentation-dot glyphs that bound to no note, and how many notation
    # staves carried at least one. Left unattached rather than bound to the
    # nearest notehead - see glyph._assign_dots - so this is reported but
    # affects no note's duration. `dots_unassigned` is the total; the two
    # counts beside it split it by WHY, since the two are not the same claim:
    # `dots_unassigned_no_candidate` never had a notehead or rest at an
    # offset an engraver would use anywhere in reach, while
    # `dots_unassigned_eliminated` reached one but lost it to an owner
    # already given a dot at a different, conflicting position - a note that
    # already has its own dot, not one with nothing nearby.
    dots_unassigned: int = 0
    dots_unassigned_no_candidate: int = 0
    dots_unassigned_eliminated: int = 0
    staves_dots_unassigned: int = 0
    # A unison shared by two voices is engraved as the same notehead glyph
    # drawn twice at the identical position, one copy per voice's stem (issue
    # #116) - and where a SECOND, distinct candidate stem exists for the
    # pair, one copy is bound to each rather than both losing to the other
    # for the single best-ranked stem (see glyph.decode_note_events). Where
    # only ONE candidate stem was found for the pair, nothing can tell the
    # two copies apart, and coincident_unsplit_pairs counts that residue
    # rather than silently leaving both copies bound to the one voice - the
    # same honesty pattern as notes_no_stem / dots_unassigned above.
    coincident_unsplit_pairs: int = 0
    staves_coincident_unsplit: int = 0
    # Noteheads that were given the fret number the tab printed for their
    # COINCIDENT TWIN rather than one printed for them (issue #137). A unison
    # shared between two voices is one plucked string, so the tab names it
    # once and the second voice's notehead has no digit of its own; handing
    # it the twin's is what lets both voices sound it, and it is an
    # INFERENCE - the tab did not print a number for that notehead - so it
    # is counted here rather than left unsaid, the same honesty pattern as
    # coincident_unsplit_pairs above. Expected to be small and specific: 16
    # across the library's 293 extractable scores, 12 of them on The Cosmic
    # Wheel (FF XI) and 4 on Castti, the Apothecary, and 0 everywhere else.
    unison_digits_shared: int = 0
    # Repeat barlines and volta brackets that were read only partly, and so
    # were omitted from the emitted MusicXML rather than written as a guess
    # (issue #134 Rule 15 / S5). None of these move any Rule 8 figure: a form
    # mark carries no duration.
    #   repeats_unread: a dot pair was found beside a barline group but did
    #     not resolve to a clean forward/backward/both - the bar-style for
    #     the strokes found is still emitted, the repeat is not.
    #   endings_unread: a volta bracket's left hook lands on a barline but no
    #     readable ending number was found nearby.
    #   endings_truncated: an ending's last bar could not be established (no
    #     backward repeat, and the drawn right end snaps to no boundary) -
    #     emitted over its first bar only.
    #   form_marks_unanchored: a mark (of either kind) with no bar boundary
    #     to anchor to at all - a guard, not a path; 0 in the library this
    #     profile was developed against.
    #   endings_incomplete: 1 if any numbered endings were read but do not
    #     form a run starting at 1 (e.g. only a "2." found anywhere), else 0.
    repeats_unread: int = 0
    repeats_unread_bars: list[int] = field(default_factory=list)
    endings_unread: int = 0
    endings_unread_bars: list[int] = field(default_factory=list)
    endings_truncated: int = 0
    endings_truncated_bars: list[int] = field(default_factory=list)
    form_marks_unanchored: int = 0
    form_marks_unanchored_bars: list[int] = field(default_factory=list)
    endings_incomplete: int = 0
    # Navigation marks - D.C., D.S., To Coda, Fine and the segno/coda signs
    # (issue #134 phase 2, Rule 16). Every mark that was read IS written, as
    # the words or the sign the page carries; these two count what could not
    # be written in full beside it, and neither moves a Rule 8 figure either.
    #   nav_marks_unanchored: a mark read off the page with no bar to name -
    #     it sits farther than NAV_BAND_SPACES from any staff, on a staff
    #     with no fret numbers on it at all, or entirely outside the x span
    #     of the staff whose bars it would otherwise be clamped onto (see
    #     _apply_nav_marks). There is no bar number to report for one, which
    #     is exactly what makes it unanchored.
    #   nav_marks_unresolved: a mark written as words with no <sound> jump
    #     beside it, because this transcription holds nothing for the words
    #     to name - a "D.S." on a score that draws no segno (3 of the
    #     library's 297 files), a "To Coda" or an "al Coda" with no coda
    #     read, an "al Fine" with no "Fine". The instruction is on the page
    #     and is written; the jump is not asserted.
    nav_marks_unanchored: int = 0
    nav_marks_unresolved: int = 0
    nav_marks_unresolved_bars: list[int] = field(default_factory=list)
    # A SYSTEM whose bars were not read at all (issue #152). Every other
    # figure on this result describes music that reached the transcription
    # and says how well it was read; this one says how much music never
    # reached it, which is the only defect none of the others can express.
    # A staff-sized group of staff lines was found on the page, could not be
    # read as a staff (its line count was neither 5 nor 6 - see
    # _detect_staves and _STAFF_SIZED_GROUP), and so contributed no bars.
    #
    # THERE IS NO `*_bars` LIST FOR IT, and that is the point rather than an
    # omission: a system that was never read has no bar numbers to report,
    # because bar numbers are assigned by the grid these bars never entered.
    # `systems_unread_pages` is the coordinate that does exist - a page a
    # reader can turn to and compare against - the same role `padded_bars`
    # plays for bars that were read.
    #
    # WHY IT MUST BE COUNTED. Bars that vanish are as likely as any to be the
    # ones that did not add up, so losing a system can move `bars_defective`
    # DOWN: a figure that improves when music disappears is worse than no
    # figure. `bars`, `notes` and every Rule 8 count here describe only the
    # systems that were read, and this is the number that says so.
    systems_unread: int = 0
    systems_unread_pages: list[int] = field(default_factory=list)
    # Ties the decoder MATCHED in the engraving and this could not write,
    # because the note they are held into was not found (issue #81), and which
    # bars they start in. A written tie needs both ends - see _resolve_ties -
    # so a start with no partner is dropped from the emitted score rather than
    # written as half a tie, and this is the count of what was dropped.
    #
    # WHY IT IS COUNTED. It is the one figure that says the transcription
    # re-strikes a note the page holds. Every other tie the decoder found IS in
    # the file and can be counted from it; these are in neither the file nor
    # any other figure here, and the music they describe plays wrong in a way
    # nothing on the page-facing side would show - the bar still adds up, the
    # note is still there, and it is struck twice where the score strikes it
    # once. The commonest cause is a tie drawn across a SYSTEM break, which
    # glyph._mark_ties cannot match at all.
    tie_ends_unpaired: int = 0
    tie_ends_unpaired_bars: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "extractable": self.extractable,
            "reason": self.reason,
            "musicxml": self.musicxml,
            "alphatex": self.alphatex,
            "title": self.title,
            "tempo": self.tempo,
            "tuning": self.tuning,
            "tuning_label": self.tuning_label,
            "tuning_unread": self.tuning_unread,
            "time_signature": list(self.time_signature) if self.time_signature else None,
            "time_signature_source": self.time_signature_source,
            "key_fifths": self.key_fifths,
            "key_signature_source": self.key_signature_source,
            "bars": self.bars,
            "beats": self.beats,
            "notes": self.notes,
            "bars_overfull": self.bars_overfull,
            "bars_short": self.bars_short,
            "bars_defective": self.bars_defective,
            "bars_measured": self.bars_measured,
            "bars_padded": self.bars_padded,
            "padded_bars": list(self.padded_bars),
            "inferred_rest_quarters": self.inferred_rest_quarters,
            "bars_unread": self.bars_unread,
            "unread_bars": list(self.unread_bars),
            "tab_staff_count": self.tab_staff_count,
            "standard_staff_count": self.standard_staff_count,
            "pages_processed": self.pages_processed,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "rhythm_provenance": self.rhythm_provenance,
            "spacing_bars": list(self.spacing_bars),
            "degraded_bars": list(self.degraded_bars),
            "staves_spacing_rhythm": self.staves_spacing_rhythm,
            "staves_degraded_rhythm": self.staves_degraded_rhythm,
            "meter_digits_unreadable": self.meter_digits_unreadable,
            "notes_no_stem": self.notes_no_stem,
            "staves_no_stem": self.staves_no_stem,
            "dots_unassigned": self.dots_unassigned,
            "dots_unassigned_no_candidate": self.dots_unassigned_no_candidate,
            "dots_unassigned_eliminated": self.dots_unassigned_eliminated,
            "staves_dots_unassigned": self.staves_dots_unassigned,
            "coincident_unsplit_pairs": self.coincident_unsplit_pairs,
            "staves_coincident_unsplit": self.staves_coincident_unsplit,
            "unison_digits_shared": self.unison_digits_shared,
            "repeats_unread": self.repeats_unread,
            "repeats_unread_bars": list(self.repeats_unread_bars),
            "endings_unread": self.endings_unread,
            "endings_unread_bars": list(self.endings_unread_bars),
            "endings_truncated": self.endings_truncated,
            "endings_truncated_bars": list(self.endings_truncated_bars),
            "form_marks_unanchored": self.form_marks_unanchored,
            "form_marks_unanchored_bars": list(self.form_marks_unanchored_bars),
            "endings_incomplete": self.endings_incomplete,
            "nav_marks_unanchored": self.nav_marks_unanchored,
            "nav_marks_unresolved": self.nav_marks_unresolved,
            "nav_marks_unresolved_bars": list(self.nav_marks_unresolved_bars),
            "systems_unread": self.systems_unread,
            "systems_unread_pages": list(self.systems_unread_pages),
            "tie_ends_unpaired": self.tie_ends_unpaired,
            "tie_ends_unpaired_bars": list(self.tie_ends_unpaired_bars),
        }


# ---------------------------------------------------------------------------
# Staff detection
# ---------------------------------------------------------------------------


class _Staff:
    def __init__(self, kind, line_ys, x0, x1, band=0):
        self.kind = kind  # "tab" (6 lines) or "standard" (5 lines)
        self.line_ys = line_ys  # sorted top->bottom
        self.x0 = x0
        self.x1 = x1
        # Which horizontal band of the page this staff was ruled on. Staves
        # printed SIDE BY SIDE share a band and have nearly equal `top` in
        # an order the engraving decides, so (band, x0) is the reading order
        # and `top` is not - see _detect_staves.
        self.band = band

    @property
    def reading_order(self):
        return (self.band, self.x0)

    @property
    def top(self):
        return self.line_ys[0]

    @property
    def bottom(self):
        return self.line_ys[-1]

    @property
    def spacing(self):
        return (self.bottom - self.top) / (len(self.line_ys) - 1)

    def string_for_y(self, y):
        """Nearest line index -> string number (1 = top line = high string)."""
        best_i, best_d = 0, abs(y - self.line_ys[0])
        for i, ly in enumerate(self.line_ys):
            d = abs(y - ly)
            if d < best_d:
                best_i, best_d = i, d
        return best_i + 1


# Two collinear pieces of one broken staff line touch: measured at 0.0000pt
# across the whole sampled library, with the closest positive gap between
# pieces that DO belong together at 0.85pt and the closest gap between pieces
# that do not at 1.07pt. The clearance is therefore about a fifth of a point,
# which is why the tolerance is not what makes joining safe - see
# STAFF_LINE_SIBLINGS_REQUIRED for what does.
STAFF_LINE_JOIN_GAP = 1.0

# A joined run has to look like part of a staff before it is believed: at
# least this many OTHER rows must carry a run spanning the same x extent.
# Five rows is the smallest staff there is, so four siblings is the weakest
# evidence that can still be evidence.
STAFF_LINE_SIBLINGS_REQUIRED = 4

# How closely two rows' extents must agree to count as the same staff's
# lines. Measured at 0.0pt across every engraver sampled - the lines of one
# staff are drawn to the same x - so this is slack, not a threshold.
STAFF_LINE_SIBLING_TOLERANCE = 2.0

# A SECOND, lower length floor, for staff lines that are short because the
# system they belong to is short (issue #152).
#
# The primary floor above is a quarter of the page width. That is the right
# size for a system that runs the width of the page, and it is why a
# right-hand system printed on the same band as the last full one was not
# merely misread but INVISIBLE: on "1 AM (Animal Crossing New Leaf)" the coda
# system's six tab lines run x 441.2-575.7, which is 134.5pt on a 612pt page
# - 0.220 of the width, under the 0.25 floor - so they were dropped before
# staff detection ever saw them, no anomaly was reported, and the page's bar
# 18 simply did not exist. "Kakariko Village" is the same shape at 133.5pt.
# Measured over the library, 54 systems are drawn this way.
#
# A lower floor on its own is exactly how a volta bracket or a chord grid
# becomes a staff, so a run admitted by it must ALSO look like a staff line
# in the two ways a decoration does not: it must have
# STAFF_LINE_SIBLINGS_REQUIRED siblings at its own extent (as any short run
# must - see _has_staff_siblings), and those siblings must be spaced far
# enough apart to be staff lines at all (STAFF_LINE_MIN_SPACING).
#
# THE VALUE IS SET BY A MEASUREMENT, and the measurement says the length is
# not what is doing the work. Scanning the whole library for every run
# between 0.04 and 0.25 of the page width that passes both of those tests
# finds 54 groups, and all 54 are real: every one comes back with exactly 11
# rows - a 5-line notation staff and a 6-line tab staff ruled to the same x -
# which is a side-by-side system and cannot be anything else. Their lengths
# run from 0.1235 (Fond Memories, 75.6pt) to 0.2488 (Melodies of Life), and
# BELOW 0.1235 the scan finds nothing whatsoever down to 0.04. So the floor
# is not separating staves from decorations - the sibling and spacing tests
# are - and 0.10 is placed in the empty band under the smallest real staff,
# where it admits all 54 and cannot be the thing that decides.
#
# It was 0.15 first, which is inside the real range and cost 12 of the 54 -
# among them "Rito Village - Night" at 0.1461, whose coda system is a
# perfectly ordinary 5+6 staff pair 89.4pt wide.
SHORT_STAFF_LEN_RATIO = 0.10

# The closest two staff lines ever are, and THE TEST THAT ACTUALLY SEPARATES
# A STAFF FROM A DECORATION. Notation staves in the library are ruled 5.1pt
# apart and tablature staves 7.7pt; everything else that clears the length
# floor and the sibling test is much tighter than either.
#
# Measured over the library at the floor above: 89 same-extent sibling groups
# clear both of those tests and are refused here, from six files. Four draw a
# title-block ornament whose rows alternate 2.5 and 1.3pt (Troian Beauty,
# Moonlit Shadows, Carcelera, Celes's Theme); two method books draw
# chord-grid rows at 2.4-2.7pt (Recuerdos de la Alhambra, Classical Guitar
# Method Vol. 1). Every real staff admitted alongside them has a minimum row
# gap of 5.00pt or more, so 3.0 sits in a clear band: worst admitted 5.00,
# closest refused 2.60.
#
# THE LENGTHS OVERLAP AND THE SPACINGS DO NOT, which is why this is the
# load-bearing test and the length floor is not. The refused groups run up to
# 0.2145 of the page width while the real short staves start at 0.1235 - so
# no length floor anywhere could separate these two sets, and one drawn
# through the overlap would throw away real systems to catch ornaments it
# would still miss. A future tuner should move this constant, not that one.
#
# Admitting them is not merely noisy, it costs real music: on "Troian Beauty"
# p3 the ornament's rows fall in the same 15.0pt band as the page's first
# notation staff and swallow it into an 11-line group, which is then
# discarded.
STAFF_LINE_MIN_SPACING = 3.0

# A rule drawn along the page's own edge is page furniture, not a staff.
PAGE_EDGE_TOLERANCE = 1.0

# How close two vertical strokes have to be to count as one barline. A
# repeat pair (thin + thick stroke) is drawn 3.6-4.0pt apart on the engravers
# sampled; a genuine adjacent measure is never that close.
#
# TAB STAVES ONLY: measured over 12,228 consecutive-vertical gaps, the band
# between 0.526 and 2.273 staff spaces is EMPTY (0 gaps), with every
# within-barline-group gap at or below 0.525 spaces and every real
# inter-measure gap at or above 2.274. 1.0 staff space sits in the middle of
# that empty band - 1.9x above the widest stroke pair observed and 2.3x below
# the narrowest genuine measure - so it is not a judgement call there. In
# absolute points this is far looser than it looks: on a typical ~7.7pt tab
# staff spacing, 1.0 space is 7.7pt, comfortably wider than any repeat-pair
# gap measured.
#
# NOTATION STAVES ARE A DIFFERENT DISTRIBUTION - the band is not empty there.
# 3,410 of the same consecutive-vertical gaps, measured on notation staves
# instead (of 31,060 total), land INSIDE the 0.526-2.273 band this
# threshold's tab-staff calibration treats as impossible - narrower staff
# spacing packs genuine inter-measure gaps closer in staff-space terms. The
# worst of those 3,410 still merges correctly (0.995 spaces, under the 1.0
# threshold), but with a margin of 0.005 spaces rather than the tab side's
# 1.9x/2.3x clearance - not a judgement call on tab staves, a close one on
# notation staves that happens to still land on the right side of 1.0.
BARLINE_STROKE_MERGE_SPACES = 1.0


def _long_horizontal_segments(page, min_len_ratio=0.25,
                              short_len_ratio=SHORT_STAFF_LEN_RATIO):
    """Near-horizontal vector primitives long enough to plausibly be staff
    lines, as opposed to beams, ledger lines, or stems.

    Collinear pieces are joined before the length test, because whether a
    staff line arrives as ONE primitive per system is an exporter's choice,
    not a property of staves: Finale and Sibelius draw one line across the
    system, MuseScore draws a separate piece per measure - abutting exactly -
    so a system of six narrow bars presented six pieces of which none was a
    quarter of the page wide, and the whole system was invisible here.
    Detection then depended on a score happening to have wide enough bars,
    which is how a tab staff could be found on one system of a page and not
    the next.

    WHAT KEEPS JOINING FROM INVENTING A LINE. Not the gap tolerance: the
    real clearance between pieces that belong together and pieces that do
    not is about 0.2pt, far too narrow to rest anything on. Instead a run
    assembled from more than one piece has to look like part of a staff -
    STAFF_LINE_SIBLINGS_REQUIRED other rows spanning the same x extent -
    while a run that arrived as a single primitive is passed through exactly
    as it always was.

    That distinction is what the joining actually costs, and it was measured:
    the Oeth arrangements draw "1." / "2." repeat brackets below the tab
    staff as two abutting strokes meeting at a barline. Each piece is under
    the length floor, so joining is the only reason such a run exists at
    all; welded, it cleared the floor and landed 10-15pt below the staff,
    inside the cluster gap, turning a 6-line tab group into a 7-line group
    that was then discarded whole. Six scores lost 33 bars and 264 notes
    between them. A volta has no sibling at its extent; every real staff
    line has four or five.

    A rule drawn along the page's own edge is dropped (MuseScore draws one at
    the top and bottom of every page, which otherwise showed up as a phantom
    one-line "staff group" in the anomaly report). Note this is a test of
    POSITION, not of length: a length ceiling would have thrown away every
    staff line on a page cropped to its content, refusing a perfectly
    readable tab score with a reason that said it held no tablature.
    """
    min_len = page.rect.width * min_len_ratio
    top, bottom = page.rect.y0, page.rect.y1
    rows = collections.defaultdict(list)
    for d in glyph.page_drawings(page):
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) >= 0.08:
                    continue
                y, span = (p1.y + p2.y) / 2, (min(p1.x, p2.x), max(p1.x, p2.x))
            elif item[0] == "re":
                r = item[1]
                if r.height >= 1.0:
                    continue
                y, span = (r.y0 + r.y1) / 2, (r.x0, r.x1)
            else:
                continue
            if y - top <= PAGE_EDGE_TOLERANCE or bottom - y <= PAGE_EDGE_TOLERANCE:
                continue
            rows[round(y, 1)].append(span)

    # Every maximal run of touching pieces, as (y, x0, x1, pieces). The
    # length floor is applied once, below, rather than at each of the two
    # places a run can end - which is also what makes it possible to test
    # that the floor is doing anything.
    runs = []
    for y, spans in rows.items():
        spans.sort()
        run_x0, run_x1, pieces = spans[0][0], spans[0][1], 1
        for x0, x1 in spans[1:]:
            if x0 - run_x1 <= STAFF_LINE_JOIN_GAP:
                run_x1 = max(run_x1, x1)
                pieces += 1
                continue
            runs.append((y, run_x0, run_x1, pieces))
            run_x0, run_x1, pieces = x0, x1, 1
        runs.append((y, run_x0, run_x1, pieces))

    long_enough = [r for r in runs if r[2] - r[1] >= min_len]
    kept = [r for r in long_enough
            if r[3] == 1 or _has_staff_siblings(r[0], r[1], r[2], long_enough)]

    # Runs too short for the primary floor, kept only where they look like the
    # lines of a short system rather than a decoration - see
    # SHORT_STAFF_LEN_RATIO. Their siblings are searched among the SHORT runs
    # alone, not among all of them: a half-width system's lines are all short
    # together, so nothing is lost by it, and it means this cannot reach back
    # and change which long runs are kept. The long path above is therefore
    # byte-for-byte the behaviour it always had.
    short_len = page.rect.width * short_len_ratio
    short = [r for r in runs if short_len <= r[2] - r[1] < min_len]
    for r in short:
        rows = _staff_sibling_rows(r[0], r[1], r[2], short)
        if len(rows) - 1 < STAFF_LINE_SIBLINGS_REQUIRED:
            continue
        if not _rows_are_staff_spaced(rows):
            continue
        kept.append(r)

    return [(y, x0, x1) for y, x0, x1, pieces in kept]


def _staff_sibling_rows(y, x0, x1, runs):
    """Every row carrying a run at this run's x extent, this row included."""
    tol = STAFF_LINE_SIBLING_TOLERANCE
    rows = {other_y for other_y, ox0, ox1, _pieces in runs
            if abs(ox0 - x0) <= tol and abs(ox1 - x1) <= tol}
    rows.add(y)
    return sorted(rows)


def _has_staff_siblings(y, x0, x1, runs):
    """Do enough other rows span this run's extent for it to be a staff line?"""
    return len(_staff_sibling_rows(y, x0, x1, runs)) - 1 >= STAFF_LINE_SIBLINGS_REQUIRED


def _rows_are_staff_spaced(rows):
    """Are consecutive rows far enough apart to be lines of a staff?

    The gap BETWEEN two staves in the set is large and passes trivially; what
    this refuses is a set whose rows are packed tighter than any engraver
    rules a staff - see STAFF_LINE_MIN_SPACING.
    """
    return all(b - a >= STAFF_LINE_MIN_SPACING for a, b in zip(rows, rows[1:]))


def _detect_staves(page):
    """Cluster long horizontal line primitives into staff systems.

    Returns (staves, anomalies) with the staves in READING ORDER - band by
    band down the page, and left to right within a band. anomalies records
    line-groups whose size was neither 5 nor 6, so callers can surface what
    was thrown away.

    A band is split into COLUMNS before its lines are counted (issue #152).
    Clustering by vertical gap alone answers "which lines are level with each
    other", which is not the same question as "which lines belong to one
    staff": the house layout these arrangements use prints the coda system to
    the RIGHT of the last full system, on the same band. Level, and two
    different staves.

    Merging the two cost music in two different ways, and both were measured:

      - Where the two systems are ruled at the SAME y, the rows collapsed
        into one full-width staff record. "Imprisoned Town (Suikoden II)" p2
        reported a standard staff at x 54.0-575.9 for a band that actually
        holds one system at 54.0-341.7 and another at 378.2-575.9. Nothing
        said anything was wrong; the staff simply described music that was
        not there, and its x span then swallowed marks belonging to the
        right-hand system (see _apply_nav_marks, which could not test its way
        out of a staff record spanning the whole page).

      - Where they are ruled 1.5-1.7pt apart - which is what an engraver
        does when the two systems have different content above them - the y
        values interleave inside the 15.0pt band and the group came back with
        TWICE the lines. That is the 12-line group issue #152 opens with:
        Imprisoned Town's last band, and both of "The Nautilus Knoweth" p3's
        (a 10-line pair of notation staves and a 12-line pair of tab staves),
        discarded whole. Imprisoned Town lost 4 printed bars, Nautilus 5.

    Splitting by x extent answers the second question directly, and the tell
    is unambiguous: two side-by-side systems do not overlap in x at all (the
    measured gaps are 36.5pt and 30.7pt) while the lines of one staff are
    drawn to the same x within STAFF_LINE_SIBLING_TOLERANCE. A column is
    therefore a maximal run of x-overlapping rows, and a page that prints one
    system per band yields exactly one column per band - which is why this
    moves no output on the 230 library files that have no such band.

    READING ORDER IS NOT TOP ORDER, and that is why this returns an order at
    all. Two side-by-side systems have nearly equal `top`, and which of them
    is the smaller number is decided by the 1.5pt engraving offset above -
    on "Troian Beauty" p3 the RIGHT-hand system is the higher one. Sorting
    staves by `top` would therefore have put the coda system's bars BEFORE
    the bars of the system printed to its left. Callers order by
    (band, x0) - see _Staff.band.
    """
    segs = _long_horizontal_segments(page)
    if not segs:
        return [], []

    ys = sorted({round(y, 1) for y, _x0, _x1 in segs})
    band_of = {ys[0]: 0}
    band = 0
    for prev, y in zip(ys, ys[1:]):
        if (y - prev) > 15.0:
            band += 1
        band_of[y] = band

    bands = collections.defaultdict(list)
    for y, x0, x1 in segs:
        bands[band_of[round(y, 1)]].append((y, x0, x1))

    staves = []
    anomalies = []
    for band in sorted(bands):
        for x0, x1, rows in _band_columns(bands[band]):
            c = sorted({round(y, 1) for y, _a, _b in rows})
            n = len(c)
            if n == 6:
                staves.append(_Staff("tab", c, x0, x1, band))
            elif n == 5:
                staves.append(_Staff("standard", c, x0, x1, band))
            else:
                anomalies.append({"line_count": n, "ys": c, "x0": x0, "x1": x1,
                                  "band": band})
    return staves, anomalies


def _band_columns(rows):
    """Split one horizontal band into the systems printed side by side in it.

    Returns [(x0, x1, rows), ...] left to right, each a maximal run of rows
    whose x extents overlap. See _detect_staves for why a band is not a staff.
    """
    columns = []
    for y, x0, x1 in sorted(rows, key=lambda r: (r[1], r[2])):
        if columns and x0 <= columns[-1][1]:
            columns[-1][1] = max(columns[-1][1], x1)
            columns[-1][2].append((y, x0, x1))
        else:
            columns.append([x0, x1, [(y, x0, x1)]])
    return [(x0, x1, rows) for x0, x1, rows in columns]


def _vertical_segments(page, min_len=15.0):
    """(x, y0, y1, width) for every vertical line primitive on the page.

    `width` is the drawn stroke's own width in points - a thick barline
    stroke and a thin one are otherwise indistinguishable, since a thick
    stroke is a STROKED line like any other, not a filled rectangle
    (BARLINE_THICK_MIN_PT), so the width has to travel with the segment
    rather than be re-derived later. For an "l" item it comes from the
    drawing dict's own `width` key (the whole path's stroke width, since
    pymupdf/fitz reports one width per drawing, not per line segment inside
    it); a filled "re" item has no stroke width, so its own geometric width
    (already how the `r.width < 1.0` filter below decides it is a thin
    vertical bar at all) stands in for one.
    """
    segs = []
    for d in glyph.page_drawings(page):
        stroke_width = d.get("width")
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.x - p2.x) < 0.08 and abs(p1.y - p2.y) >= min_len:
                    x = (p1.x + p2.x) / 2
                    segs.append((x, min(p1.y, p2.y), max(p1.y, p2.y),
                                 stroke_width if stroke_width is not None else 0.0))
            elif item[0] == "re":
                r = item[1]
                if r.width < 1.0 and r.height >= min_len:
                    x = (r.x0 + r.x1) / 2
                    segs.append((x, r.y0, r.y1, r.width))
    return segs


# A stroke this wide or wider is the thick half of a barline pair; below it,
# thin. Measured across five engraver families (issue #134 S2.2): every thin
# stroke sampled tops out at 0.906pt and every thick one starts at 2.268pt -
# no overlap anywhere - so a fixed point threshold sits in the middle with
# margin on both sides (1.66x above the widest thin stroke, 1.51x below the
# narrowest thick one) without needing a page-relative modal-width
# computation. A ratio against the page's own modal stroke width would also
# work and would survive a page scaled to a different size, but the library
# never needed that; this is simpler.
BARLINE_THICK_MIN_PT = 1.5

# A repeat's two dots sit this many staff spaces off a staff's own centre
# line - the second and fourth lines of a six-line (tab) staff, the second
# and third of a five-line (standard) one - each within +-0.05 of the
# library's measured extremes (issue #134 S2.3: p50 1.001/0.500, full range
# 0.990-1.010 / 0.456-0.545).
REPEAT_DOT_OFFSET_TAB = 1.0
REPEAT_DOT_OFFSET_STANDARD = 0.5
REPEAT_DOT_OFFSET_TOLERANCE = 0.05

# How far from a barline group's own extent to look for its dots, in staff
# spaces. The widest measured dot-to-stroke distance is 1.979 spaces (a
# forward repeat's outlier); 2.5 keeps it in reach with margin.
REPEAT_DOT_SEARCH_SPACES = 2.5


class _Barline:
    """One barline: where it is, what it is drawn from, and - where the
    drawing says so - what repeat it carries.

    `shape` is the merged group's strokes in left-to-right order as a string
    of 't' (thin) and 'H' (thick) - e.g. "t" for an ordinary barline, "tt" for
    a double bar, "tH"/"Ht" for a final or repeat barline, "tHt" for a
    back-to-back repeat. `repeat` is None, "forward", "backward" or "both".
    `repeat_unread` marks a dot pair that was found near this group but could
    not be resolved to a direction (see _read_repeat_dots) - counted, not
    guessed (issue #134 S5).

    `x` is the LEFTMOST stroke - the boundary a bar's edges are measured
    against (see _detect_barlines: which stroke survives makes no difference
    to any bar/beat/note/conformance figure). `edges` is (first, last) stroke
    x - both ends of the group, not just the boundary. A volta bracket's left
    hook abuts whichever physical stroke the engraver drew it against, which
    for a group closing WITH a repeat is its own thick stroke, not
    necessarily this group's leftmost - so a volta's "lands on a barline"
    test (issue #134 S2.4) has to reach both ends, not only `x`.
    """

    __slots__ = ("x", "shape", "repeat", "repeat_unread", "edges")

    def __init__(self, x, shape, repeat=None, repeat_unread=False, edges=None):
        self.x = x
        self.shape = shape
        self.repeat = repeat
        self.repeat_unread = repeat_unread
        self.edges = edges if edges is not None else (x, x)

    def __repr__(self):
        return f"<_Barline {self.shape!r}@{self.x:.1f} repeat={self.repeat!r}>"


def _read_repeat_dots(page, staff, group_x0, group_x1, stroke_count):
    """Which side of a barline group at [group_x0, group_x1] carries repeat
    dots: "forward" (dots to the right), "backward" (dots to the left),
    "both" (a back-to-back repeat), or (None, found_but_unresolved) where
    found_but_unresolved says whether a dot-shaped glyph was seen at all
    without resolving to a clean pair on one side.

    `stroke_count` is how many strokes the caller merged into this group -
    only a group of three or more (e.g. "tHt") has a thick stroke to spare
    for a second direction, so dots on both sides of a bare two-stroke group
    is the ambiguous case issue #134 S5 says to drop and disclose rather than
    guess.

    A repeat's two dots are drawn at the SAME x (measured on every engraved
    fixture checked), so grouping by x first turns "is this pair symmetric"
    into "does one candidate at this x sit near +offset and another near
    -offset" - equivalent to the sum/diff test in issue #134 S2.3
    (|off1+off2| < 0.35, |off2-off1| > 0.6) once both are already known to sit
    within REPEAT_DOT_OFFSET_TOLERANCE of +-offset, and clearer to read.
    """
    expected = (REPEAT_DOT_OFFSET_TAB if staff.kind == "tab"
                else REPEAT_DOT_OFFSET_STANDARD)
    reach = staff.spacing * REPEAT_DOT_SEARCH_SPACES
    candidates = glyph.dot_like_glyph_events(
        page, staff.top, staff.bottom, group_x0 - reach, group_x1 + reach)
    if not candidates:
        return None, False

    mid = (staff.top + staff.bottom) / 2
    by_x = collections.defaultdict(list)
    for e in candidates:
        offset = (e.yc - mid) / staff.spacing
        if abs(abs(offset) - expected) <= REPEAT_DOT_OFFSET_TOLERANCE:
            by_x[round(e.xc, 1)].append(offset)

    found_any = bool(candidates)
    left_pair = right_pair = False
    for x, offsets in by_x.items():
        has_above = any(o < 0 for o in offsets)
        has_below = any(o > 0 for o in offsets)
        if not (has_above and has_below):
            continue
        if x < group_x0:
            left_pair = True
        elif x > group_x1:
            right_pair = True

    if left_pair and right_pair:
        if stroke_count < 3:
            return None, found_any
        return "both", found_any
    if right_pair:
        return "forward", found_any
    if left_pair:
        return "backward", found_any
    return None, found_any


def _detect_barlines(segs, staff, page=None):
    """Barlines on this staff, as _Barline records in x order.

    `segs` is the page's full set of vertical line primitives (see
    _vertical_segments) - callers must compute it once per page and reuse it
    across staves. get_drawings() re-parses the page's whole content stream,
    so calling _vertical_segments(page) once per staff here made a 2-page,
    ~7-staves-per-page file re-parse the same page content ~14 times inside
    a single synchronous request.

    Strokes within BARLINE_STROKE_MERGE_SPACES of their immediate predecessor
    are the same barline (see its docstring for why that threshold and not a
    fixed point value): a repeat pair draws a thin stroke and a thick stroke a
    few points apart, and merging too tight leaves both as separate barlines
    with a phantom sliver "measure" between them. The comparison chains off
    the PREVIOUS stroke, not off the group's leftmost one, because a compound
    barline (e.g. a double bar abutting a repeat, `ttHt`) can carry four
    strokes whose individual gaps are each well under the threshold but whose
    total span, first stroke to last, is not - measured on the library, 17
    such groups. Chaining off each hop instead of the group's anchor still
    merges them correctly, since no genuine inter-measure gap is ever within
    this threshold of anything (the empty band is 0.526-2.273 staff spaces;
    see BARLINE_STROKE_MERGE_SPACES). Which stroke of a group survives as the
    boundary makes no difference to anything downstream - bars, beats, notes
    and every conformance count come out identical whichever end is kept - so
    the leftmost is kept because music never starts inside a barline group.

    `page` is optional: a caller that only wants boundary positions (the
    meter timeline, callers that pre-date repeat reading) can leave it out and
    every record comes back with `repeat` None and `repeat_unread` False -
    `shape` is read from stroke widths alone and does not depend on `page` at
    all, so it can still hold "H". Passing `page` is what enables the dot
    search - see _read_repeat_dots.
    """
    xs = []
    span = staff.bottom - staff.top
    for x, y0, y1, width in segs:
        if y0 <= staff.top + span * 0.3 and y1 >= staff.bottom - span * 0.3:
            if staff.x0 - 2 <= x <= staff.x1 + 2:
                xs.append((round(x, 1), width))
    by_x = {}
    for x, width in xs:
        if x not in by_x or width > by_x[x]:
            by_x[x] = width
    xs = sorted(by_x.items())

    merge_tol = staff.spacing * BARLINE_STROKE_MERGE_SPACES
    groups = []
    prev = None
    for x, width in xs:
        if prev is not None and x - prev < merge_tol:
            groups[-1].append((x, width))
        else:
            groups.append([(x, width)])
        prev = x

    barlines = []
    for group in groups:
        boundary_x = group[0][0]
        group_x0, group_x1 = group[0][0], group[-1][0]
        shape = "".join("H" if w >= BARLINE_THICK_MIN_PT else "t" for _x, w in group)
        repeat = None
        repeat_unread = False
        if page is not None:
            # Searched regardless of `shape` - NOT gated on "H" in shape.
            # `width` is missing (substituted 0.0, see _vertical_segments)
            # for 34 strokes across the library, which reads a genuinely
            # thick stroke as thin; a group that should carry an "H" but
            # doesn't still sits beside its own repeat dots, and gating the
            # search on the very shape the width bug corrupted silently
            # dropped the repeat with no disclosure at all (issue #134 S5).
            repeat, found_any = _read_repeat_dots(
                page, staff, group_x0, group_x1, len(group))
            if repeat is None and found_any:
                repeat_unread = True
        if repeat is None and not repeat_unread and shape.count("H") >= 2:
            # Two or more thick strokes with no direction resolved at all -
            # whether because no dot-shaped glyph was found nearby, or
            # because what was found didn't form a clean pair. Either way
            # this is the back-to-back-repeat SHAPE with no readable
            # direction, and it used to be dropped from `barline_recs`'
            # onward handling with no bar-style and no warning (see
            # _apply_repeat_marks, which now writes heavy-heavy for this
            # case explicitly rather than nothing at all).
            repeat_unread = True
        barlines.append(_Barline(boundary_x, shape, repeat, repeat_unread,
                                  edges=(group_x0, group_x1)))
    return barlines


def _bar_style_for_shape(shape):
    """<bar-style> for a barline group's stroke shape, or None where an
    ordinary single thin stroke needs none. A group with two or more thick
    strokes (a back-to-back repeat, "tHt") is not resolved here - see
    _apply_repeat_marks, which writes heavy-heavy for those directly, split
    across the two measures the boundary sits between."""
    if shape.count("H") >= 2:
        return None
    if "H" not in shape:
        return "light-light" if len(shape) > 1 else None
    return "heavy-light" if shape[0] == "H" else "light-heavy"


# How close, in the ANCHORING staff's own spaces (the staff `bounds` was
# built from - always the tab staff, see _anchor_mark's callers), an x has to
# land to one of this staff's own detected bar boundaries to count as
# anchored to it AT ALL (case 1 of _anchor_mark below), rather than falling
# through to the disclosed "no boundary here" case 4.
#
# `bounds` records only the LEFTMOST stroke of each barline group (see
# _detect_barlines: which stroke survives makes no difference to any
# bar/beat/note/conformance figure) - but a volta bracket's hook is drawn
# against whichever physical stroke the engraver actually used, which for a
# group closing WITH a repeat is that group's own THICK stroke, not
# necessarily the leftmost one (see _Barline.edges / _associate_voltas). A
# repeat pair's thin-to-thick gap is measured at 3.6-4.0pt in the library
# (BARLINE_STROKE_MERGE_SPACES) - up to 0.69 tab-staff-spaces measured
# directly on Zelda's Lullaby ending 2, whose left hook abuts a repeat's
# thick stroke 5.28pt from the group's registered (leftmost) x - so a
# tolerance anywhere near VOLTA_ANCHOR_SPACES (0.5) would reject a bracket
# that IS correctly anchored. 1.5 spaces comfortably clears every group width
# measured (including the 17 compound multi-stroke groups whose total span
# exceeds one merge hop) while staying well inside the empty band no genuine
# inter-measure gap ever falls under (>= 2.274 tab-staff-spaces) - so it
# cannot make two adjacent boundaries ambiguous with each other. A repeat
# mark's own x is always an exact match (0 distance) regardless of this
# value, so it never rejects one; case 4 was dead code before this test
# existed at all - every real x fell into case 1, 2 or 3 regardless of how
# far from a boundary it was.
ANCHOR_MARK_SNAP_SPACES = 1.5


def _anchor_mark(x, bounds, lo, hi, spacing):
    """Which LOCAL (0-based, staff-relative) bar's right and/or left barline
    an x position belongs to, per issue #134 S3.2's total rule over this
    staff's 513-mark sample:

    1. if x sits at one of this staff's own bar boundaries - within
       ANCHOR_MARK_SNAP_SPACES of the nearest one, whenever lo <= x <= hi -
       it is the right barline of the bar before it and the left barline of
       the bar after. A repeat mark's own x is always an exact member of
       `bounds` (built from the same detection pass), so this never rejects
       one; a volta bracket's left hook is not, which is exactly what the
       proximity test is for;
    2. otherwise, if x is left of the first fret column, it is the LEFT
       barline of this staff's first bar - the clef/meter region the
       fret-column filter carved out of `bounds` ate the boundary that would
       otherwise be there;
    3. otherwise, if x is right of the last fret column, it is the RIGHT
       barline of this staff's last bar;
    4. otherwise there is no boundary to anchor to at all: x sits inside
       [lo, hi] but farther than the snap tolerance from any boundary in
       `bounds` - a caller reaching this should disclose rather than guess.

    Returns (right_of_local_bar, left_of_local_bar); either may be None where
    that side does not apply (the very first/last boundary of the staff).
    """
    n_bars = len(bounds) - 1
    if lo <= x <= hi:
        i = min(range(len(bounds)), key=lambda k: abs(bounds[k] - x))
        if abs(bounds[i] - x) <= spacing * ANCHOR_MARK_SNAP_SPACES:
            right_of = i - 1 if i > 0 else None
            left_of = i if i < n_bars else None
            return right_of, left_of
    elif x < lo:
        return None, 0
    elif x > hi:
        return n_bars - 1, None
    return None, None


def _add_form_mark(form_marks, measure, location, bar_style=None, repeat=None,
                    ending_number=None, ending_type=None):
    """Record one piece of a barline's structure at (measure, location),
    merging into whatever is already there rather than replacing it - a
    measure that both closes an ending and ends a repeat carries both on one
    <barline location="right">, and this is the one place that assembles it
    (see musicxml.build's `barlines` parameter)."""
    rec = form_marks.setdefault(measure, {}).setdefault(location, {})
    if bar_style is not None:
        rec["bar_style"] = bar_style
    if repeat is not None:
        rec["repeat"] = repeat
    if ending_number is not None:
        rec["ending_number"] = ending_number
        rec["ending_type"] = ending_type


def _apply_repeat_marks(barline_recs, bounds, lo, hi, staff_first_bar, form_marks, spacing):
    """Turn this staff's repeat/bar-style records into form_marks entries
    (see _add_form_mark), keyed by DOCUMENT-level measure number.

    Returns (repeats_unread_bars, form_marks_unanchored_bars) - document-level
    bar numbers for the two failure modes issue #134 S5 names: a dot pair
    found but not resolved to a clean direction (or two-or-more thick strokes
    with no direction resolved at all - see _detect_barlines), and a mark
    with no boundary to anchor to at all.
    """
    repeats_unread_bars = []
    unanchored_bars = []
    for bl in barline_recs:
        bar_style = _bar_style_for_shape(bl.shape)
        if bl.repeat is None and bar_style is None and not bl.repeat_unread:
            continue
        right_of, left_of = _anchor_mark(bl.x, bounds, lo, hi, spacing)
        if right_of is None and left_of is None:
            unanchored_bars.append(staff_first_bar + max(len(bounds) - 2, 0))
            continue
        if bl.repeat_unread:
            local = right_of if right_of is not None else left_of
            repeats_unread_bars.append(staff_first_bar + local)
            # The bar-style for the strokes actually seen is still written -
            # only the repeat direction is dropped (issue #134 S5). Two or
            # more thick strokes with no direction resolved is the
            # back-to-back-repeat SHAPE with none of its meaning read, so it
            # is written as heavy-heavy rather than as nothing at all -
            # `_bar_style_for_shape` deliberately returns None for that shape
            # (it expects the "both" branch below to write heavy-heavy with
            # its direction attached), so this is the one place that has to
            # override it explicitly.
            unread_style = "heavy-heavy" if bl.shape.count("H") >= 2 else bar_style
            if unread_style is not None:
                if right_of is not None:
                    _add_form_mark(form_marks, staff_first_bar + right_of, "right",
                                    bar_style=unread_style)
                if left_of is not None:
                    _add_form_mark(form_marks, staff_first_bar + left_of, "left",
                                    bar_style=unread_style)
            continue
        if bl.repeat == "both":
            if right_of is not None:
                _add_form_mark(form_marks, staff_first_bar + right_of, "right",
                                bar_style="heavy-heavy", repeat="backward")
            if left_of is not None:
                _add_form_mark(form_marks, staff_first_bar + left_of, "left",
                                repeat="forward")
        elif bl.repeat == "forward":
            if left_of is not None:
                _add_form_mark(form_marks, staff_first_bar + left_of, "left",
                                bar_style="heavy-light", repeat="forward")
            elif right_of is not None:
                _add_form_mark(form_marks, staff_first_bar + right_of, "right",
                                bar_style="heavy-light", repeat="forward")
        elif bl.repeat == "backward":
            if right_of is not None:
                _add_form_mark(form_marks, staff_first_bar + right_of, "right",
                                bar_style="light-heavy", repeat="backward")
            elif left_of is not None:
                _add_form_mark(form_marks, staff_first_bar + left_of, "left",
                                bar_style="light-heavy", repeat="backward")
        elif bar_style is not None:
            if right_of is not None:
                _add_form_mark(form_marks, staff_first_bar + right_of, "right",
                                bar_style=bar_style)
            elif left_of is not None:
                _add_form_mark(form_marks, staff_first_bar + left_of, "left",
                                bar_style=bar_style)
    return repeats_unread_bars, unanchored_bars


# ---------------------------------------------------------------------------
# Volta brackets
# ---------------------------------------------------------------------------

# All in staff spaces, measured on 175 numbered brackets in the library
# (issue #134 S2.4-2.5). Height is deliberately wide and is NOT a
# discriminator on its own - the numbered brackets in the library bottom out
# at 4.00 spaces (3.28 on the MuseScore fixture) while non-volta brackets in
# the method books reach 3.26, so the hook and the number are what decide.
VOLTA_HEIGHT_MIN_SPACES = 2.5
VOLTA_HEIGHT_MAX_SPACES = 13.0
# A downward hook at the left end, required; 91 of 175 brackets have no
# closing hook at the right end, so that one is never required.
VOLTA_HOOK_MIN_SPACES = 1.5
VOLTA_HOOK_MAX_SPACES = 4.5
# The left end lands on a barline within this many spaces (p95 0.25, max
# 0.491) - the discriminator that rejects a ledger line or a tuplet bracket,
# which pass everything else.
VOLTA_ANCHOR_SPACES = 0.5
# The ending number's bbox top sits this many spaces below the bracket line
# (min -0.10, p50 0.51, max 0.52); its left sits this many spaces right of
# the bracket's left end (min 0.36, p50 1.83, max 3.33). Widening dx to 4 or
# 5 finds not one more; widening dy to 4 picks up a triplet numeral.
VOLTA_NUMBER_DY = (-0.2, 1.0)
VOLTA_NUMBER_DX = (-0.2, 3.5)
# Positive integers without a leading zero, comma-separated - MusicXML's own
# `ending-number` restriction (`[1-9][0-9]*(, ?[1-9][0-9]*)*`). "0" and a
# leading-zero numeral both match \d+ but are not valid ending numbers -
# unguarded, either would be read here and later rejected by the schema
# (or accepted by a laxer consumer as ending zero, which does not exist).
_VOLTA_NUMBER_RE = re.compile(r"^\s*([1-9]\d*(?:\s*,\s*[1-9]\d*)*)\s*\.?\s*$")


def _text_spans(page):
    """Every non-blank plain text span on the page, as (text, x0, y0, x1,
    y1). Used for the ending number, which is plain text, not a music glyph
    - see glyph_rhythm's own separation of the two."""
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text.strip():
                    x0, y0, x1, y1 = span["bbox"]
                    out.append((text, x0, y0, x1, y1))
    return out


def _text_lines(page):
    """Every non-blank plain text LINE on the page, as (text, x0, y0, x1,
    y1) with the spans of one line joined.

    _text_spans above is the right unit for an ending number, which is one
    short span on its own. It is the wrong unit for a navigation
    instruction: an engraver is free to break "D.C. al Fine" across two
    spans, and the MuseScore fixture does exactly that - the committed
    navigation.pdf splits it after "Fin", so a per-span read finds
    "D.C. al Fin" and the phrase's own "al Fine" half goes unread. Joining
    the line first is what makes the phrase the unit that is matched.
    """
    out = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if text.strip():
                x0, y0, x1, y1 = line["bbox"]
                out.append((text, x0, y0, x1, y1))
    return out


def _volta_horizontal_pieces(page, y0, y1):
    """Near-horizontal vector primitives in this y band, as raw (unwelded)
    (y, x0, x1) pieces.

    Deliberately NOT welded the way _long_horizontal_segments joins abutting
    staff-line pieces: two numbered endings drawn back to back share the
    barline between them, so their two bracket lines can abut with the same
    sub-point gap a single bracket's own broken-line pieces do (issue #134
    S2.4) - welding blind would fuse ending 1 and ending 2 into one bracket.
    _read_volta_brackets does its own welding, guarded by where the OTHER
    brackets' own hooks are, which a length- or gap-only rule cannot tell
    apart from this.
    """
    rows = collections.defaultdict(list)
    for d in glyph.page_drawings(page):
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) >= 0.08:
                    continue
                y, span = (p1.y + p2.y) / 2, (min(p1.x, p2.x), max(p1.x, p2.x))
            elif item[0] == "re":
                r = item[1]
                if r.height >= 1.0:
                    continue
                y, span = (r.y0 + r.y1) / 2, (r.x0, r.x1)
            else:
                continue
            if not (y0 <= y <= y1):
                continue
            rows[round(y, 1)].append(span)

    return [(y, x0, x1) for y, spans in rows.items() for x0, x1 in spans]


def _volta_hooks(page, y0, y1, spacing):
    """Short downward vertical strokes in this y band - a volta bracket's
    hook - as (x, top_y, bottom_y)."""
    hooks = []
    min_len = spacing * VOLTA_HOOK_MIN_SPACES
    max_len = spacing * VOLTA_HOOK_MAX_SPACES
    for d in glyph.page_drawings(page):
        for item in d.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.x - p2.x) >= 0.08:
                    continue
                length = abs(p1.y - p2.y)
                if not (min_len <= length <= max_len):
                    continue
                top_y, bot_y = min(p1.y, p2.y), max(p1.y, p2.y)
            elif item[0] == "re":
                r = item[1]
                if r.width >= 1.0:
                    continue
                length = r.y1 - r.y0
                if not (min_len <= length <= max_len):
                    continue
                top_y, bot_y = r.y0, r.y1
            else:
                continue
            if y0 - spacing <= top_y <= y1:
                x = (p1.x + p2.x) / 2 if item[0] == "l" else (r.x0 + r.x1) / 2
                hooks.append((x, top_y, bot_y))
    return hooks


def _read_volta_number(spans, left_x, line_y, spacing):
    """The ending number printed near this bracket's left end and line, or
    None. Window dy in VOLTA_NUMBER_DY, dx in VOLTA_NUMBER_DX staff spaces,
    nearest by |dy| then |dx|."""
    best = None
    best_key = None
    for text, x0, y0, _x1, _y1 in spans:
        m = _VOLTA_NUMBER_RE.match(text)
        if not m:
            continue
        dy = (y0 - line_y) / spacing
        dx = (x0 - left_x) / spacing
        if not (VOLTA_NUMBER_DY[0] <= dy <= VOLTA_NUMBER_DY[1]):
            continue
        if not (VOLTA_NUMBER_DX[0] <= dx <= VOLTA_NUMBER_DX[1]):
            continue
        key = (abs(dy), abs(dx))
        if best_key is None or key < best_key:
            best_key = key
            best = m.group(1).replace(" ", "")
    return best


def _read_volta_brackets(page, band_top, spacing, x0=None, x1=None):
    """Numbered volta brackets whose line sits above `band_top` (the
    system's topmost staff line - never the tab staff, see issue #134 S2.4),
    read fresh from the drawing primitives. Returns (brackets, hooks):

    `x0`/`x1` bound the search to ONE system's own horizontal extent. A band
    can hold two systems printed side by side (issue #152), and the y band
    above a staff runs the width of the page: without this, the "2." bracket
    drawn over the left-hand system was found AGAIN for the right-hand one
    and written a second time onto the coda system's bar. Measured on "Our
    Terms (Final Fantasy XVI)", which emitted ending 2 over both bar 27 and
    bar 28. A page with one system per band passes its full width here and
    nothing is excluded.
    brackets is (left_x, right_x, line_y, number) left-to-right; hooks is
    every downward stroke found in the search band, passed through so
    _associate_voltas can look for a closing hook at the boundary it
    resolves rather than at the drawn line's own end.

    `right_x` is the drawn right end of the piece(s) welded to the bracket's
    own left-hook piece at the SAME height - corroboration only (see
    _associate_voltas for why the extent itself never comes from here).

    A bracket is not always one continuous line even at one height: 12 of 175
    in the library arrive as two abutting pieces meeting at a barline - which
    is indistinguishable, by gap alone, from two adjacent NUMBERED endings
    meeting at their shared barline (see _volta_horizontal_pieces), so
    welding here stops at any point matching another bracket's own left
    hook.
    """
    y_hi = band_top
    y_lo = band_top - spacing * VOLTA_HEIGHT_MAX_SPACES
    y_reach_lo = band_top - spacing * VOLTA_HEIGHT_MIN_SPACES
    hooks = _volta_hooks(page, y_lo, y_hi, spacing)
    if x0 is not None:
        hooks = [h for h in hooks if x0 <= h[0] <= x1]
    if not hooks:
        return [], []
    pieces = _volta_horizontal_pieces(page, y_lo, y_hi)
    if x0 is not None:
        # Overlap, not containment: a bracket's drawn line may overhang its
        # system's last barline slightly, as ordinary engraving.
        pieces = [pc for pc in pieces if pc[1] <= x1 and pc[2] >= x0]
    if not pieces:
        return [], hooks
    spans = _text_spans(page)

    # Other brackets' own left hooks - a piece starting at one of these is
    # the NEXT bracket, not more of this one, even if it abuts.
    hook_xs = [hx for hx, _htop, _hbot in hooks]

    brackets = []
    used_pieces = set()
    for hx, htop, _hbot in hooks:
        if htop > y_reach_lo:
            continue
        best = None
        for pi, (py, px0, _px1) in enumerate(pieces):
            if pi in used_pieces:
                continue
            if abs(py - htop) > spacing * 0.3 or abs(px0 - hx) > spacing * 0.6:
                continue
            if best is None or abs(px0 - hx) < abs(pieces[best][1] - hx):
                best = pi
        if best is None:
            continue
        used_pieces.add(best)
        py, px0, px1 = pieces[best]
        right_x = px1

        # Weld further pieces at the SAME y, abutting within
        # STAFF_LINE_JOIN_GAP - the ordinary "one line drawn as several path
        # pieces" case, same as a staff line's.
        grew = True
        while grew:
            grew = False
            for pj, (py2, px02, px12) in enumerate(pieces):
                if pj in used_pieces or abs(py2 - py) > 0.05:
                    continue
                if px02 - right_x > STAFF_LINE_JOIN_GAP:
                    continue
                if any(abs(px02 - ox) <= spacing * 0.6 for ox in hook_xs if ox != hx):
                    continue
                used_pieces.add(pj)
                right_x = max(right_x, px12)
                grew = True

        number = _read_volta_number(spans, hx, py, spacing)
        brackets.append((hx, right_x, py, number))
    brackets.sort()
    return brackets, hooks


def _associate_voltas(brackets, hooks, barline_recs, bounds, lo, hi, staff_first_bar, spacing,
                       bar_spacing):
    """This system's volta brackets, split into (endings, unread_bars,
    unanchored_bars): endings is a list of (first_doc_bar, last_doc_bar,
    number, ending_type, truncated); unread_bars is document-level bar
    numbers for a bracket whose left hook lands on a barline but whose
    number could not be read (dropped rather than guessed - issue #134 S5);
    unanchored_bars is document-level bar numbers for a bracket _anchor_mark
    could not place against any bar boundary at all (its case 4).

    The first bar comes from the same anchoring rule a repeat mark uses (see
    _anchor_mark) applied to the bracket's own left hook - INCLUDING its
    case 2/3, a system-start/end bracket whose left end sits past the
    clef/key signature (or past the last note), with no real barline at its
    x at all because the fret-column filter carved that region's boundary
    out of `bounds`. That is 37% of the library's numbered brackets (issue
    #134 S3.2) and is not an edge case to guard against - it is the normal
    shape of a bracket that opens a system. The last bar comes, in order:
    (1) the bar whose right barline carries a backward repeat at or after
    the first bar - authoritative; (2) failing that, the bracket's own drawn
    right end snapped to a boundary within VOLTA_ANCHOR_SPACES; (3) failing
    both, the first bar alone, disclosed as truncated (issue #134 S3.2).

    `spacing` is the NOTATION staff's own spacing, used for the bracket's own
    geometry - see _read_volta_brackets. `bar_spacing` is the TAB staff's,
    the staff `bounds`/`lo`/`hi`/`barline_recs` were built from - passed
    through to _anchor_mark, whose proximity test is keyed to the staff its
    `bounds` came from, not the staff the bracket was drawn against (see
    ANCHOR_MARK_SNAP_SPACES).
    """
    def _dist_to_group(x, bl):
        e0, e1 = bl.edges
        if e0 <= x <= e1:
            return 0.0
        return min(abs(x - e0), abs(x - e1))

    endings = []
    unread_bars = []
    unanchored_bars = []
    n_bars = len(bounds) - 1
    # How many hook entries sit at each x, and how many BRACKETS claim that
    # exact x as their own left hook (issue #134 adversarial review, item
    # 9). Two numbered endings can sit back to back with nothing between
    # them (see fixture_adjacent_endings) - the closing hook of one and the
    # opening hook of the next then land at THE SAME x, drawn as two
    # coincident strokes (confirmed on Zelda's Lullaby: 2 separate hook
    # entries at the exact x where ending 1 closes and ending 2 opens). A
    # count comparison, not a flat exclusion, is what tells "the next
    # bracket's own opening hook, and nothing else" (count equal - exclude)
    # apart from "a genuine closing hook that happens to coincide with the
    # next bracket's opening one" (count exceeds the claims - the spare one
    # is fair game) - see has_right_hook below.
    hook_x_counts = collections.Counter(hx for hx, _htop, _hbot in hooks)
    own_hook_counts = collections.Counter(b[0] for b in brackets)
    for idx, (left_x, right_x, line_y, number) in enumerate(brackets):
        right_of, left_of = _anchor_mark(left_x, bounds, lo, hi, bar_spacing)
        if right_of is None and left_of is None:
            # _anchor_mark's own case 4 (issue #134 S3.2/S5) - genuinely no
            # bar boundary anywhere near this bracket's left end, as opposed
            # to the nearest_barline rejection below (a real boundary
            # nearby, just not one this bracket's hook actually lands on).
            # Only disclosed for a NUMBERED bracket: `brackets` also holds
            # every unnumbered height/hook-shaped candidate this staff's
            # system carried (ties, slurs, hairpins that happen to pass
            # those two tests), most of which were never volta candidates at
            # all and were always going to land nowhere near a barline -
            # disclosing every one of those as an unanchored FORM MARK would
            # be noise, not a finding. A number is what makes a bracket look
            # like an actual attempt at a volta in the first place.
            if number is not None:
                unanchored_bars.append(staff_first_bar + max(n_bars - 1, 0))
            continue
        if lo <= left_x <= hi:
            # The discriminator that rejects a ledger line or a tuplet
            # bracket, which pass the height/hook/number tests: the left end
            # has to land ON A BARLINE, not merely somewhere plausible.
            # Measured against the whole group's edges, not just its
            # boundary x: a hook drawn against a repeat's own thick stroke
            # can sit several points from the leftmost (kept) stroke - see
            # _Barline.edges. This only applies when _anchor_mark resolved
            # the bracket against one of THIS STAFF'S OWN detected
            # boundaries (case 1, lo <= left_x <= hi) - a bracket anchored
            # to the system start/end (case 2/3) has no real barline at its
            # x by construction, the clef/key signature region ate it, so
            # there is nothing here to measure against. Running this guard
            # before _anchor_mark used to reject every case-2/3 bracket
            # before the anchoring that exists precisely for them was ever
            # reached (issue #134 adversarial review, blocker 1).
            nearest_barline = min(
                (_dist_to_group(left_x, bl) for bl in barline_recs), default=float("inf"))
            if nearest_barline > spacing * VOLTA_ANCHOR_SPACES:
                continue
        first_local = left_of if left_of is not None else (
            right_of + 1 if right_of is not None else None)
        if first_local is None or first_local >= n_bars:
            continue
        if number is None:
            unread_bars.append(staff_first_bar + first_local)
            continue

        # This bracket's own extent search must not cross into the NEXT
        # bracket's territory - a backward repeat that closes THAT bracket's
        # own span is not this one's, even though "at or after first_local"
        # alone cannot tell the two apart (issue #134 adversarial review,
        # item 9 - the same neighbouring-bracket confusion has_right_hook
        # below guards against, for the extent search instead of the hook).
        next_first_local = None
        if idx + 1 < len(brackets):
            nxt_right_of, nxt_left_of = _anchor_mark(
                brackets[idx + 1][0], bounds, lo, hi, bar_spacing)
            if nxt_left_of is not None:
                next_first_local = nxt_left_of
            elif nxt_right_of is not None:
                next_first_local = nxt_right_of + 1

        last_local = None
        for bl in barline_recs:
            if bl.repeat not in ("backward", "both"):
                continue
            r_of, _l_of = _anchor_mark(bl.x, bounds, lo, hi, bar_spacing)
            if r_of is not None and r_of >= first_local:
                if next_first_local is not None and r_of >= next_first_local:
                    continue
                if last_local is None or r_of < last_local:
                    last_local = r_of
        truncated = False
        if last_local is None:
            nearest_i = min(range(len(bounds)), key=lambda k: abs(bounds[k] - right_x))
            if abs(bounds[nearest_i] - right_x) <= spacing * VOLTA_ANCHOR_SPACES:
                last_local = max(first_local, nearest_i - 1)
            else:
                last_local = first_local
                truncated = True
        last_local = min(last_local, n_bars - 1)

        # Whether the bracket closes with a hook, decided at the BRACKET'S
        # OWN drawn right end (right_x), not at the resolved last bar's
        # boundary. The two disagree on real engravings: the drawn right end
        # sits within VOLTA_ANCHOR_SPACES of a barline on 172 of 175 brackets
        # in the library, but not all - up to 10.495 spaces away on the
        # outliers - so a bracket whose line stops short of (or past) the
        # boundary the repeat/snap logic settled on still carries whatever
        # hook it was actually drawn with, at its OWN end, not the
        # boundary's. 91 of 175 brackets in the library have none, so this is
        # never required. The repeat search and `truncated` above decide the
        # ending's EXTENT (issue #134 S3.2) and are deliberately not
        # consulted here - the extent and the hook are different questions.
        # A hook at x counts as available here only if there are MORE hook
        # entries at that x than brackets claiming it as their own left hook
        # (issue #134 adversarial review, item 9) - see hook_x_counts /
        # own_hook_counts above. The ordinary case (a hook belonging only to
        # this bracket, or only to a neighbour's) has counts 1 and 1 or 1
        # and 0 - never available to a DIFFERENT bracket. The coincident
        # case (two hooks land at the same x - one closes this bracket, one
        # opens the next) has counts 2 and 1, so the spare is available.
        has_right_hook = any(
            abs(hx - right_x) <= spacing * VOLTA_ANCHOR_SPACES
            and abs(htop - line_y) <= spacing * 1.5
            and hook_x_counts[hx] > own_hook_counts[hx]
            for hx, htop, _hbot in hooks)

        endings.append((staff_first_bar + first_local, staff_first_bar + last_local,
                         number, "stop" if has_right_hook else "discontinue", truncated))
    return endings, unread_bars, unanchored_bars


# ---------------------------------------------------------------------------
# Navigation marks: D.C., D.S., To Coda, Fine, and the segno/coda signs
# ---------------------------------------------------------------------------
#
# THE CENSUS THIS IS BUILT ON (issue #134 phase 2), re-measured over the
# whole library, 297 PDFs, with the corrected Maestro glyph map and the
# anchored phrase patterns below:
#
#   at least one navigation phrase in the text layer     168 / 297
#   a "D.C." or "D.S." jump                              165 / 297 (176 marks)
#     of which D.S. al Coda 67, D.C. al Coda 67,
#     D.C. al Fine 14, bare D.S. 10, D.S. al Fine 4,
#     bare D.C. 2, and 12 numbered or "x2" variants
#   "To Coda"                                            143 / 297 (147 marks)
#   a coda SIGN in the music font                        143 / 297 (156 signs)
#   a standalone "Fine"                                   16 / 297 (20 marks)
#   a segno SIGN                                          84 / 297 (88 signs)
#   the word "Segno" anywhere in the text layer            0
#
# So this is not a long tail: a navigation instruction is on more than half
# the library's pages.
#
# THE SEGNO ROW WAS 0 UNTIL THE GLYPH WAS RENDERED. Every one of those signs
# is Finale's Maestro GID 4, which glyph_rhythm's calibrated map labelled
# "simile" - so a census that swept the mapped glyphs and then swept the
# UNMAPPED ones, twice over, could not see them either time. The consequence
# here was not cosmetic: 86 files print a "D.S.", and (as of issue #154)
# 84 of them do draw the sign it names. Only two do not - "Hollow (Final
# Fantasy VII Remake)" and "Rebel Army Theme (Final Fantasy II)" - written as
# the words the page prints, with no <sound> jump attached, and counted
# (nav_marks_unresolved). Inventing a segno at bar 1 for the other 84 would
# have been the wrong fix for a problem that was a mislabelled table row.
#
# A THIRD FILE WAS IN THAT LIST TOO, FOR A DIFFERENT REASON (issue #154):
# "Rito Village - Night (The Legend of Zelda Breath of the Wild)" embeds its
# Maestro subset as a resource literally named "CIDFont+F1" - every embedded
# font in that PDF was renamed generically by whatever tool produced it, none
# of them named "Maestro" - and glyph_rhythm.load_music_fonts used to reject
# a resource by that name before its fingerprint was ever consulted, so this
# file read NO music glyphs at all: no noteheads, no rhythm, not just no
# segno. Fixed by fingerprinting first (see load_music_fonts / _load_one_font
# and the module docstring's "THE NAME IS A FAST PATH, NOT A GATE"); this
# file's segno sign is counted in the 84/88 above, its coda sign in the
# 143/156, and its notes/bars/beats figures moved from the spacing-fallback
# numbers to glyph-decoded ones.

# How far above the staff it belongs to a navigation mark may sit, in that
# staff's own spaces. Measured with the attribution rule _assign_nav_marks
# uses (the nearest staff BELOW the mark, nothing in between) over every
# navigation mark in the library: coda signs 0.76-6.39, "To Coda" 0.88-8.21,
# D.C./D.S. 0.96-10.87, "Fine" 1.20-4.91. 12.0 clears the widest by 1.10x
# and sits just under the volta bracket's own ceiling
# (VOLTA_HEIGHT_MAX_SPACES); it is a sanity bound on how far a piece of text
# can be from the music it annotates, not a discriminator - the phrase
# itself is what identifies a navigation mark.
NAV_BAND_SPACES = 12.0
# How far a coda's LABEL ("Coda", "Coda 2") may be from the sign it labels,
# as a multiple of the sign's own drawn height. Usually zero: the engraver
# draws the sign inside the same text line as the word, so the two boxes
# overlap and the gap is 0 by inspection. The allowance is for the pages
# that draw the sign in the music font and the word in a text font a little
# apart - measured at up to 1.6 sign-heights in the library.
NAV_LABEL_GAP_HEIGHTS = 3.0
# How close a bar boundary has to be to an instruction's own text before
# that boundary is taken as the barline the instruction fires on, in TAB
# staff spaces (the staff the bar grid belongs to). Measured over the
# library, the distance from the nearest boundary to a D.C./D.S.'s own text
# extent is 0.00 spaces at the median and 0.00 at p95 - the engraver is
# aligning them to the barline, not placing them near it - so this is a
# guard against a mark that is aligned to nothing, not a tolerance being
# leaned on. 2.0 is under the narrowest real bar in the library (2.274
# spaces is the closest two boundaries ever come, see
# BARLINE_STROKE_MERGE_SPACES), so it can never make two boundaries
# ambiguous with each other.
NAV_BOUNDARY_SNAP_SPACES = 2.0

# WHY THE MARK'S OWNER IS THE NEAREST STAFF BELOW IT, AND NOT THE NEAREST
# STAFF FULL STOP. A navigation mark is engraved ABOVE the topmost staff of
# the system it applies to - the same placement rule a volta bracket follows
# (issue #134 S2.4) - but a guitar system is notation over tablature, so the
# gap above one system's notation staff is also the gap BELOW the previous
# system's tab staff, and "nearest staff" picks the wrong one about a third
# of the time - measured, not estimated: nearest-by-distance names a
# different staff than nearest-below for 170 of the 569 navigation marks this
# extractor reads off the library, 29.9%. (Two of the 569 are owned by no
# staff either way and so cannot disagree.) Measured in detail on Zelda's
# Lullaby, where the answer is known from the page's own printed bar numbers:
#
#   "D.C. al Coda"  6.9pt below system 3's tab staff, 29.9pt above system
#                   4's notation staff. It belongs to system 4 (bar 18):
#                   read as system 3's it would fall on bar 14 and bars
#                   15-18 would never be played at all.
#   "To Coda"       15.3pt below system 1's tab, 13.8pt above system 2's
#                   notation. It belongs to system 2 (bar 8).
#   the coda sign   7.4pt below system 4's tab, 16.1pt above system 5's
#                   notation. It belongs to system 5 (bar 19), which is
#                   what the page prints beside that system.
#
# Nearest-staff-by-distance gets the first and third of those wrong.
# Nearest-staff-BELOW gets all three right, and needs no tolerance to do it.


# A jump instruction, in the forms the library actually prints. `D.S.` and
# `D.C.` are always written with their dots here (86 and 83 files); the
# spelled-out "Da Capo" appears once and "Dal Segno" never, but both are
# accepted because rejecting a phrase for being spelled out would be a
# silent miss rather than a disclosed one.
#
# ANCHORED AT BOTH ENDS, for the reason _NAV_FINE_RE is: a line that merely
# CONTAINS a jump phrase is usually prose ABOUT the jump, not the jump. Six
# such lines in this library were being emitted as live directions, one of
# them ("only do the second / repeat after D.C.", Kaine Salvation) with a
# `<sound dacapo="yes"/>` on bar 1, undisclosed - a transcription of an
# instruction to the player as an instruction to the renderer. The prose
# found: "repeat after D.C.", "after D.S. repeat this", "on return D.S.",
# "D.S. 1: use second repeat", "D.S. 2: use first repeat to Coda" (and
# "To Coda after repeat", which _NAV_TO_CODA_RE below now refuses the same
# way), plus five method-book lines of the form "D.C. al Fine = Return to the
# beginning of the piece and play to the fine." - 11 lines over the library,
# 10 of them read as jumps and one as a "To Coda".
#
# The tail this still accepts is what the library's real marks print after
# the phrase and nothing else: the coda's own number ("D.S. al Coda 1"), a
# repeat count ("D.C. al Coda x2") and a closing full stop. Every one of the
# 176 real jump marks in the library matches; all 11 prose lines do not. A
# leading bar number is deliberately NOT allowed the way _NAV_CODA_LABEL_RE
# allows one - an engraver prints the system's bar number beside the sign at
# the head of a system, which is where a coda label sits and is not where a
# jump instruction sits, and no library line needs it.
_NAV_JUMP_RE = re.compile(
    r"^(?:(?P<dc>D\.\s?C\.|Da\s+Capo)|(?P<ds>D\.\s?S\.|Dal\s+Segno))"
    r"(?:\s*(?P<num>\d+))?"
    r"(?:\s*al\s*(?:(?P<coda>Coda)|(?P<fine>Fine))(?:\s*\d+)?)?"
    r"(?:\s*x\s*\d+)?"
    r"\.?$",
    re.IGNORECASE)
# "To Coda", optionally numbered, anchored for the same reason as the jump
# above. A list ("To Coda 1, 2" twice in the library, "To Coda 1 & 2" once)
# is deliberately NOT parsed into a number: it names two different codas from
# one mark, which MusicXML's own `tocoda` cannot express, so it is read as an
# unnumbered instruction and disclosed. The list has to be spelled out here
# rather than left to fall off the end, because anchoring would otherwise
# reject those three marks outright instead of reading them unnumbered.
_NAV_TO_CODA_RE = re.compile(
    r"^To\s*Coda\b"
    r"(?:\s*(?:(?P<num>\d+)|\d+(?:\s*[,&]\s*\d+)+))?"
    r"\.?$",
    re.IGNORECASE)
# A coda SECTION label - the word beside the sign, and the number after it
# where the score numbers its codas. Anchored at both ends so "To Coda" and
# "D.S. al Coda" cannot match it (they are tested for first in any case).
# The optional leading integer is the BAR NUMBER an engraver prints at the
# head of the system: Finale puts it in the same text line as the label, so
# a line reads "41 Coda" or "55 Coda 1" once the sign's private-use
# codepoint is stripped out, and anchoring without it lost the coda's own
# number on every such page.
_NAV_CODA_LABEL_RE = re.compile(
    r"^(?:\d+\s*)?Coda\b(?:\s*(?P<num>\d+))?$", re.IGNORECASE)
# "Fine" as the whole of a text span, so a method book's "Fine = Finish, end
# of the piece" (2 occurrences in the library) is not read as a mark.
_NAV_FINE_RE = re.compile(r"^Fine\.?$")
# A music font's own glyphs reach the text layer as private-use codepoints -
# Maestro draws its coda at U+F0DE, and 82 of the library's coda labels
# arrive as the single span "Coda". Stripping the private-use area
# before matching is what lets `^Coda$` anchor at all.
_PUA_RE = re.compile("[\ue000-\uf8ff]")


class _NavMark:
    """One navigation mark read off the page, before it is anchored to a bar.

    `kind` is "segno", "coda", "tocoda", "jump" or "fine". `number` is the
    integer a numbered coda carries ("To Coda 2" / "Coda 2"), or None.
    `back_to` is "start" for a D.C. and "segno" for a D.S.; `until` is
    "coda" or "fine" where the instruction names one.

    `opens_a_section` is what decides how the mark is anchored and where in
    its measure it is written: a segno or a coda opens the bar it sits in,
    and everything else fires at the end of one (see _apply_nav_marks and
    musicxml.build's `directions`).
    """

    __slots__ = ("kind", "text", "number", "back_to", "until",
                 "x0", "y0", "x1", "y1")

    def __init__(self, kind, text, x0, y0, x1, y1, number=None,
                 back_to=None, until=None):
        self.kind = kind
        self.text = text
        self.number = number
        self.back_to = back_to
        self.until = until
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    @property
    def opens_a_section(self):
        return self.kind in ("segno", "coda")

    def __repr__(self):  # pragma: no cover - debugging aid
        return (f"_NavMark({self.kind!r}, {self.text!r}, num={self.number}, "
                f"x={self.x0:.1f}-{self.x1:.1f}, y={self.y0:.1f})")


def _nav_text_marks(lines):
    """Navigation marks in this page's plain text, given _text_lines output.

    One mark per line at most, and the tests are ordered so a phrase that
    contains another cannot be read as the shorter one: "To Coda" holds the
    word "Coda", and "D.S. al Coda" holds both.

    All four patterns are anchored to the whole line. A line that merely
    contains a navigation phrase is prose about the music - a method book's
    gloss, or an instruction to the player like "on return D.S." - and
    writing it out as a `<direction>` with a live `<sound>` jump makes the
    file say something the engraver did not (see _NAV_JUMP_RE).
    """
    marks = []
    for text, x0, y0, x1, y1 in lines:
        clean = _PUA_RE.sub("", text).strip()
        if not clean:
            continue
        m = _NAV_JUMP_RE.match(clean)
        if m:
            marks.append(_NavMark(
                "jump", clean, x0, y0, x1, y1,
                number=int(m.group("num")) if m.group("num") else None,
                back_to="start" if m.group("dc") else "segno",
                until="coda" if m.group("coda") else ("fine" if m.group("fine") else None)))
            continue
        m = _NAV_TO_CODA_RE.match(clean)
        if m:
            marks.append(_NavMark(
                "tocoda", clean, x0, y0, x1, y1,
                number=int(m.group("num")) if m.group("num") else None))
            continue
        m = _NAV_CODA_LABEL_RE.match(clean)
        if m:
            marks.append(_NavMark(
                "coda", clean, x0, y0, x1, y1,
                number=int(m.group("num")) if m.group("num") else None))
            continue
        if _NAV_FINE_RE.match(clean):
            marks.append(_NavMark("fine", clean, x0, y0, x1, y1))
    return marks


def _read_navigation_marks(page):
    """Every navigation mark on this page: the two signs from the music
    font, and the instructions from the text layer.

    A coda SIGN and the word beside it ("Coda", "Coda 2") are one mark, not
    two - the sign is what carries the position (a Maestro page draws both
    inside a single text line whose left edge is the system's printed bar
    number, so the sign's own x is the only trustworthy one) and the word is
    what carries the number.
    """
    text_marks = _nav_text_marks(_text_lines(page))
    # A coda sign drawn INSIDE a "To Coda" instruction's own text line is
    # that instruction's reference glyph - the page is printing "To Coda ⊕",
    # naming the sign it sends the player to - and not a coda section head.
    # 6 files in this library engrave it that way (Ami, Bygone Days,
    # Cropdale, Ku Land of the Scarlet Sunset, The Crestlands, The Journey
    # Begins), and reading it as a section head put the coda on the "To
    # Coda"'s own bar, whereupon that instruction pointed the player at
    # itself and `coda="coda"` was written twice in the same score. The test
    # is geometric containment in the text line's box, not proximity: the
    # word and the glyph are one line, and a real coda head is a separate
    # line at the head of its own system.
    #
    # This is the exact opposite arrangement to the coda LABEL folded in
    # below, where a "Coda" word beside a sign is the sign's number - there
    # the word annotates the sign, here the sign annotates the words.
    tocoda_boxes = [(m.x0, m.y0, m.x1, m.y1) for m in text_marks
                    if m.kind == "tocoda"]
    signs = []
    for ev in glyph.navigation_glyph_events(page):
        if any(x0 <= ev.x0 and ev.x1 <= x1 and y0 <= ev.y0 and ev.y1 <= y1
               for x0, y0, x1, y1 in tocoda_boxes):
            continue
        signs.append(_NavMark(ev.category, "", ev.x0, ev.y0, ev.x1, ev.y1))

    # Fold a coda LABEL into the sign it labels. The label supplies the
    # NUMBER; the sign keeps the POSITION, which is the half that has to be
    # right: a Maestro page draws the sign inside the same text line as the
    # word AND as the system's printed bar number, so that line's own left
    # edge is the bar number's, several bars' width from where the coda
    # actually is.
    #
    # Matched by the gap between the two boxes rather than by the label
    # starting to the sign's right, for the same reason - a line that
    # CONTAINS the sign has a gap of zero and a negative "distance to the
    # right", and both shapes occur.
    kept = []
    for mark in text_marks:
        if mark.kind != "coda":
            kept.append(mark)
            continue
        owner = None
        best_gap = None
        for sign in signs:
            if sign.kind != "coda":
                continue
            height = max(sign.y1 - sign.y0, 1.0)
            if abs(sign.y0 - mark.y0) > height:
                continue
            gap = max(mark.x0 - sign.x1, sign.x0 - mark.x1, 0.0)
            if gap > height * NAV_LABEL_GAP_HEIGHTS:
                continue
            if best_gap is None or gap < best_gap:
                owner, best_gap = sign, gap
        if owner is None:
            kept.append(mark)
        elif owner.number is None:
            owner.number = mark.number
    return sorted(signs + kept, key=lambda m: (m.y0, m.x0))


def _nav_owner_in_band(nearest, candidates, mark):
    """Which of the staves sharing `nearest`'s band the mark is drawn over.

    Vertical order alone picks the owner wrongly as soon as a band holds two
    systems printed side by side (issue #152). The two are ruled 1.5-1.7pt
    apart and which one is the higher is an engraving detail, so "the first
    staff below the mark" is a coin toss between the system the mark is over
    and the one beside it. Measured over the library, taking the coin toss
    left 20 coda signs owned by the system to their LEFT - and the x refusal
    in _apply_nav_marks then correctly declined to anchor them, turning a
    mark that has a perfectly good bar into a disclosed unanchored one.

    So among the staves on that one band, the mark belongs to the one it
    horizontally overlaps most, and only where it overlaps none of them does
    the nearest-by-y answer stand - whereupon _apply_nav_marks refuses it as
    it always did. A page with one system per band has one candidate and
    this returns it unchanged.
    """
    mates = [s for s in candidates if s.band == nearest.band]
    best, best_overlap = nearest, 0.0
    for s in mates:
        overlap = min(s.x1, mark.x1) - max(s.x0, mark.x0)
        if overlap > best_overlap:
            best, best_overlap = s, overlap
    return best


def _assign_nav_marks(marks, staves, tab_for_top):
    """Bucket this page's navigation marks by the tab staff whose bars they
    belong to. Returns ({id(tab_staff): [marks]}, unowned).

    A mark belongs to the nearest staff BELOW it with nothing in between -
    see the block comment above for the measurements that rule out
    nearest-staff-by-distance. Where there is no staff below it at all (a
    mark printed under the last system on a page, which is where an
    engraver puts a closing "Fine" or "D.C.") it belongs to the nearest
    staff ABOVE instead; that case is unambiguous precisely because nothing
    follows it.

    A mark farther than NAV_BAND_SPACES from the staff it would attach to is
    not attached at all - it is page furniture, not an annotation on that
    system - and comes back in `unowned` for the caller to disclose.
    """
    by_top = sorted(staves, key=lambda s: s.top)
    by_bottom = sorted(staves, key=lambda s: s.bottom)
    buckets = collections.defaultdict(list)
    unowned = []
    for mark in marks:
        below = [s for s in by_top if s.top >= mark.y1 - 0.5]
        owner = _nav_owner_in_band(below[0], below, mark) if below else None
        gap = (owner.top - mark.y1) if owner is not None else None
        if owner is None:
            above = [s for s in reversed(by_bottom) if s.bottom <= mark.y0 + 0.5]
            owner = _nav_owner_in_band(above[0], above, mark) if above else None
            gap = (mark.y0 - owner.bottom) if owner is not None else None
        if owner is None or gap > owner.spacing * NAV_BAND_SPACES:
            unowned.append(mark)
            continue
        # The owner is a system's TOP staff; the bars belong to its tab
        # staff. A notation staff with no tab partner has no bar grid of its
        # own in this extractor, so a mark that lands on one is disclosed
        # rather than guessed onto a neighbour.
        tab = tab_for_top.get(id(owner))
        if tab is None:
            unowned.append(mark)
            continue
        buckets[id(tab)].append(mark)
    return buckets, unowned


def _apply_nav_marks(marks, bounds, staff_first_bar, spacing):
    """This staff's navigation marks, as [(document-level bar number, mark)].

    A navigation mark names a BAR, not a barline - which is where it differs
    from every phase-1 form mark. A repeat or a volta hook is drawn ON a
    boundary; a navigation mark is a piece of text (or a sign) drawn over
    the music, and which bar it names depends on which kind it is:

      - a sign that OPENS a section (segno, coda) is drawn at the head of
        its own bar, so it names the bar its LEFT edge falls in. Zelda's
        Lullaby's coda sign sits 34.8pt into bar 19 - past that system's
        clef and key signature - which no boundary rule would reach.

      - an instruction that FIRES AT THE END of a bar (D.C., D.S., To Coda,
        Fine) is drawn against that bar's closing barline - but WHICH SIDE
        of the text touches the barline is the engraver's choice, and the
        two engravers this project reads disagree. Finale right-aligns:
        measured over the library, the instruction's RIGHT edge sits a
        median 0.15 staff spaces from a bar boundary (0.39 for a "To
        Coda"). MuseScore left-aligns at the same barline, so its text
        starts there and runs on into the NEXT bar - three of the four
        instructions on the committed navigation.pdf are drawn that way.

        Anchoring by either edge alone therefore gets one engraver wrong by
        exactly one bar. So neither edge decides: the boundary NEAREST THE
        WHOLE TEXT does, and the mark names the bar that boundary closes.
        Both alignments put that boundary within a fraction of a space of
        the text, and both name the same bar once it is found. A mark with
        no boundary within NAV_BOUNDARY_SNAP_SPACES of it is not aligned to
        a barline at all and falls back to the bar its left edge is in.

    A mark drawn PAST either end of the bar grid is NOT anchored at all. It
    is returned in `refused` for the caller to disclose, and this is the one
    thing here that is not a matter of degree: `bounds` runs from the staff's
    own x0 to its own x1, so a mark lying entirely outside that span is not
    over this staff's music, and clamping it onto the nearest end bar puts a
    sign on a bar the page does not draw it over.

    That mattered on exactly one page shape, and it is a common one. On the
    Oeth layouts the coda system is engraved to the RIGHT of the last system
    on the same band; the right-hand system is lost to a staff-detection
    defect (a pre-existing one - it moves no bar count, see issue #152), and
    its coda sign was then clamped onto the LAST bar of the system to its
    left, which is the bar the D.C./D.S. jump closes. Measured over the
    library before this refusal existed: 41 marks (40 coda signs and one
    "D.S. 2") sat entirely past their staff's right end and were anchored
    anyway, the nearest of them 3.42 staff spaces out and the median 7.59 -
    so there is no borderline case here to trade a tolerance against. On "1
    AM (Animal Crossing New Leaf)" the page prints its coda at bar 18 and the
    clamp emitted it at 17, alongside that bar's "D.S. al Coda"; on "Kakariko
    Village" the page prints 37 and the clamp emitted 36.

    THIS CATCHES 40 OF THE 41. ONE RESIDUAL REMAINS, NAMED. "The Nautilus
    Knoweth (Final Fantasy XIV Endwalker)" has the same layout and escapes by
    a different route: its last band's two systems sit at the same y, so the
    staff-line clusterer MERGES them - the two 5-line notation staves come
    back as one 10-line group and the two 6-line tab staves as one 12-line
    group, both discarded as anomalies, so that band yields no staff at all.
    Its coda sign then falls to the "no staff below" case, attaches to the
    system ABOVE, and that staff's x span is the full page width - so the
    coda at x=423.5 is INSIDE the bounds and this refusal never fires. The
    page prints 57 bars (a three-bar system opening at 54 and a coda system
    opening at 57 to its right); the extractor reports 53. That is issue #152
    again by the MERGE route rather than the drop route, and no x test can
    reach it: the staff record it is measured against spans the whole page.

    Library-wide, bars carrying both a coda sign and a jump therefore go from
    43 to 2 - The Nautilus Knoweth's bar 52, above, and "Eyes on Me (Final
    Fantasy VIII)" bar 71, which is not a defect at all: that page really
    does print a one-bar system carrying both "(sign) Coda 1" and "D.S. 2".

    A mark that merely OVERHANGS an end (a right-aligned instruction whose
    text runs past the last barline, which is ordinary engraving) still
    anchors: it is the "entirely outside" case that is refused, not the
    "reaches past" one. The same "1 AM" system's "D.S. al Coda" straddles the
    staff's right end and keeps its bar.

    Within the grid there is no "no bar to anchor to" case: `bounds` always
    holds at least the staff's own two ends. The other case that exists is a
    mark on a staff that never got a bar grid (a tab staff with no
    fret-number tokens on it, which the measure loop skips whole); that one
    is disclosed by the caller, which is the only place that knows a staff
    was skipped.
    """
    n_bars = len(bounds) - 1
    snap = spacing * NAV_BOUNDARY_SNAP_SPACES
    anchored = []
    refused = []
    for mark in marks:
        if mark.x1 < bounds[0] or mark.x0 > bounds[-1]:
            refused.append(mark)
            continue
        local = None
        if not mark.opens_a_section:
            # Distance from each boundary to the text's own extent. The
            # staff's own left end is skipped: nothing lies to the left of it
            # for a mark aligned there to be closing.
            #
            # A boundary the text STRADDLES is ranked by how far it is from
            # the nearer text EDGE, not flattened to zero. Zero for every
            # straddled boundary alike made the 2.0-space eligibility window
            # unable to separate two of them - it would have taken whichever
            # came first in the list, at any distance - and an edge distance
            # is the right ordering on its own terms: both engravers align an
            # EDGE of the text to the barline it fires on (Finale the right,
            # MuseScore the left), so the boundary sitting at an edge is the
            # one that was aligned to and a boundary buried in the middle of
            # the words is not. Which boundaries are ELIGIBLE is unchanged -
            # still "within `snap` of the text" - so this reorders ties and
            # nothing else; measured over the library, it moves no score's
            # output, and the library holds no two boundaries close enough
            # together for one instruction to straddle both.
            best = None
            for i, b in enumerate(bounds[1:], start=1):
                outside = max(mark.x0 - b, b - mark.x1, 0.0)
                if outside > snap:
                    continue
                rank = outside if outside > 0 else min(b - mark.x0, mark.x1 - b)
                if best is None or rank < best[0]:
                    best = (rank, i - 1)
            if best is not None:
                local = best[1]
        if local is None:
            local = bisect.bisect_right(bounds, mark.x0) - 1
        local = min(max(local, 0), n_bars - 1)
        anchored.append((staff_first_bar + local, mark))
    return anchored, refused


def _resolve_nav_marks(anchored, refused=()):
    """Turn anchored navigation marks into MusicXML `<direction>` records.

    Returns (directions, unresolved_bars, coda_was_refused): directions is
    {measure -> {"before": [...], "after": [...]}} for musicxml.build's
    `directions` parameter, and unresolved_bars is the bars carrying an
    instruction whose jump target this transcription does not hold - either
    because the page draws none, or because the mark that would have been it
    could not be anchored to a bar (nav_marks_unanchored, disclosed
    separately). Counted by distinct BAR, so two instructions closing the
    same bar contribute one.

    WHY `refused` IS PASSED IN, AND WHAT IT MAY AND MAY NOT DO. One root
    cause reaches the reader through two counters. Measured over the library:
    of the 87 bars in nav_marks_unresolved, 79 - across 40 of the 47 files -
    are on scores whose coda sign was REFUSED for sitting outside its staff
    (see _apply_nav_marks), which is the same defect nav_marks_unanchored is
    already reporting. Only 8 bars, across 7 files, name a target the page
    genuinely does not draw.

    That is a fact about the DISCLOSURE, not about the counts, and it is
    fixed as one. `coda_was_refused` feeds the warning PROSE and nothing
    else: no bar moves between the two counters and no third counter is
    added, because nav_marks_unresolved means exactly one thing - "this
    instruction went out without its jump" - and splitting it by cause would
    make it mean two. What the reader gets instead is a sentence that says
    which cause it was, and points at the other counter.

    `coda_was_refused` is true when at least one coda mark on this score was
    read off the page and then refused for having no bar to name. That is
    precisely the condition under which "the coda this score draws sits on a
    system this transcription does not hold" is a true sentence.

    WHAT IS WRITTEN, AND WHAT IS NOT. Every mark that was read is written -
    as the words the page prints, or as the sign it draws - because that is
    what the page says and a reader is entitled to see it. What is
    conditional is the `<sound>` beside it, which is an ASSERTION about
    playback: `dalsegno` names a segno, `tocoda` names a coda, and naming
    one that is not in the file makes the transcription play a form nobody
    engraved. So a `<sound>` is attached only where its target was read off
    the same score, and where it is not, the mark is written without one and
    the bar is counted (nav_marks_unresolved). A D.C. is the exception that
    proves the rule: its target is the start of the score, which is always
    there, so `dacapo` never needs a mark to point at.
    """
    codas = {}
    segno = None
    fine = None
    for bar, mark in anchored:
        if mark.kind == "coda":
            codas.setdefault(mark.number, bar)
        elif mark.kind == "segno" and segno is None:
            segno = bar
        elif mark.kind == "fine" and fine is None:
            fine = bar

    def coda_id(number):
        return "coda" if number is None else f"coda{number}"

    directions = {}
    unresolved = []

    def add(bar, where, record):
        directions.setdefault(bar, {}).setdefault(where, []).append(record)

    for bar, mark in sorted(anchored, key=lambda pair: (pair[0], pair[1].kind)):
        if mark.kind == "segno":
            add(bar, "before", {"symbol": "segno", "sound": {"segno": "segno"}})
        elif mark.kind == "coda":
            cid = coda_id(mark.number)
            add(bar, "before", {"symbol": "coda", "sound": {"coda": cid}})
        elif mark.kind == "fine":
            add(bar, "after", {"words": mark.text, "sound": {"fine": "yes"}})
        elif mark.kind == "tocoda":
            sound = None
            if mark.number in codas:
                sound = {"tocoda": coda_id(mark.number)}
            elif mark.number is None and len(codas) == 1:
                # An unnumbered "To Coda" on a score with exactly one coda
                # sign names that one. On a score with several (the library
                # has 5 files that number theirs) it names none of them
                # unambiguously, so it is written as words and disclosed
                # rather than pointed at a guess.
                sound = {"tocoda": coda_id(next(iter(codas)))}
            if sound is None:
                unresolved.append(bar)
            add(bar, "after", {"words": mark.text, "sound": sound})
        elif mark.kind == "jump":
            sound = None
            if mark.back_to == "start":
                sound = {"dacapo": "yes"}
            elif segno is not None:
                sound = {"dalsegno": "segno"}
            # The second half of the instruction ("al Coda", "al Fine")
            # names where to STOP, and MusicXML carries that on the Coda or
            # Fine mark itself rather than on the jump - so a jump whose
            # target is known is still only half-read if no coda was read
            # (39 of the library's "al Coda" marks, most of them scores
            # whose coda sign is drawn on a system the staff detector loses
            # - see _apply_nav_marks) or no "Fine" was.
            if (sound is None
                    or (mark.until == "coda" and not codas)
                    or (mark.until == "fine" and fine is None)):
                unresolved.append(bar)
            add(bar, "after", {"words": mark.text, "sound": sound})
    coda_was_refused = any(mark.kind == "coda" for mark in refused)
    return directions, sorted(set(unresolved)), coda_was_refused


# ---------------------------------------------------------------------------
# Digit (fret number) extraction
# ---------------------------------------------------------------------------


class _DigitToken:
    __slots__ = ("text", "bbox", "font", "size", "harmonic")

    def __init__(self, text, bbox, font, size):
        self.text = text
        self.bbox = bbox  # (x0, y0, x1, y1)
        self.font = font
        self.size = size
        # Set by _mark_harmonic_digits: this fret number is drawn inside the
        # bracket pair that says it is a harmonic (issue #63).
        self.harmonic = False

    @property
    def x0(self):
        return self.bbox[0]

    @property
    def x1(self):
        return self.bbox[2]

    @property
    def yc(self):
        return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def width(self):
        return self.bbox[2] - self.bbox[0]


def _extract_digit_tokens(page):
    """All spans that are purely ASCII digits, 1-2 chars, any font. Font name
    is deliberately not used as a filter - position relative to a detected
    tab staff is what identifies a fret number, not the exporter's font
    choice for it."""
    tokens = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text.isdigit() and 1 <= len(text) <= 2:
                    tokens.append(_DigitToken(text, span["bbox"], span.get("font"), span.get("size")))
    return tokens


# The characters an engraver draws either side of a tablature fret number to
# say the note is a HARMONIC: single guillemets, U+2039 and U+203A (issue
# #63). Measured over the library's 297 scores, they bracket a fret number 983
# times across 121 files and appear nowhere else on a tab staff - the census
# found no other paired punctuation around a fret number at all, and not one
# unpaired closing mark.
#
# THE SPACING IS NOT WHAT IDENTIFIES THEM, the characters are; the tolerance
# below only has to be wide enough not to miss a real pair. Measured as a
# fraction of the tab staff's line spacing over all 896 unambiguous pairs: the
# gap from the opening mark to the digit runs -0.14 to 0.58 spacings (median
# 0.06) and from the digit to the closing mark -0.14 to 0.62 (median 0.12).
# Negative because the marks are set at 15.4pt against the digits' 9.4pt and
# their advance boxes overlap the digit's. 0.9 spacings clears the widest
# measured pair by half as much again, and no ordinary fret number has one of
# these characters anywhere near it to be confused by.
HARMONIC_BRACKETS = ("‹", "›")   # < and > as single guillemets
HARMONIC_BRACKET_GAP_SPACINGS = 0.9
# How far off the digit's own centre line a bracket may sit and still be its.
# An outer bound only - which digit a mark belongs to is decided by taking the
# NEAREST one rather than by this number, because a chord stacks its digits one
# spacing apart and the marks are drawn at nearly twice the digits' point size,
# so their boxes are taller than a string's worth of space and no fixed window
# separates a chord's members. Nearest-wins does.
HARMONIC_BRACKET_Y_SPACINGS = 1.0


def _harmonic_bracket_marks(page):
    """Every harmonic bracket character the page draws, as (char, bbox).

    Read from the raw character boxes rather than from spans: an engraver
    writes the pair as its own text run with the fret number in a different
    font between them, and PyMuPDF reports the space between the marks as part
    of the mark's own span (measured on "Hymn of the Fayth" p1, where '<', ' '
    and '>' are one 15.4pt TimesNewRomanPSMT run and the '12' between them is
    a 9.4pt Arial-BoldMT one). A span bbox therefore spans the whole bracket
    and says nothing about where either mark is.
    """
    marks = []
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    if ch["c"] in HARMONIC_BRACKETS:
                        marks.append((ch["c"], ch["bbox"]))
    return marks


def _mark_harmonic_digits(tokens_for_staff, staff, marks):
    """Flag every fret number on this staff that a bracket pair encloses.

    BOTH marks are required. One alone is not the convention and would let a
    stray character - or one belonging to the note before or after - claim a
    note the page says nothing about, which for a harmonic is a claim about
    how the note is played and not a decoration.

    EACH MARK GOES TO ITS NEAREST DIGIT, and to only one. A chord stacks its
    fret numbers a single line spacing apart while the marks are set at nearly
    twice the digits' point size, so a mark's box is taller than the gap
    between two strings and no fixed vertical window can separate a chord's
    members: given one, a bracketed digit's neighbour on the string below was
    marked a harmonic too. Nearest-wins needs no threshold to get that right.

    Returns how many were flagged.
    """
    if not marks or not tokens_for_staff:
        return 0
    gap = staff.spacing * HARMONIC_BRACKET_GAP_SPACINGS
    y_tol = staff.spacing * HARMONIC_BRACKET_Y_SPACINGS
    opening, closing = HARMONIC_BRACKETS
    before = set()
    after = set()
    for ch, bbox in marks:
        if ch == opening:
            side, reach = before, [t for t in tokens_for_staff
                                   if -gap <= t.bbox[0] - bbox[2] <= gap]
        elif ch == closing:
            side, reach = after, [t for t in tokens_for_staff
                                  if -gap <= bbox[0] - t.bbox[2] <= gap]
        else:
            continue
        mid = (bbox[1] + bbox[3]) / 2
        reach = [t for t in reach if abs(t.yc - mid) <= y_tol]
        if reach:
            side.add(id(min(reach, key=lambda t: abs(t.yc - mid))))
    found = 0
    for tok in tokens_for_staff:
        if id(tok) in before and id(tok) in after:
            tok.harmonic = True
            found += 1
    return found


def _assign_tokens_to_tab_staves(tokens, tab_staves):
    """Return ({staff_index: [tokens]}, unmatched_tokens)."""
    by_staff = collections.defaultdict(list)
    unmatched = []
    for tok in tokens:
        best = None
        best_d = None
        for i, st in enumerate(tab_staves):
            pad = st.spacing * 0.75
            if st.top - pad <= tok.yc <= st.bottom + pad and st.x0 - 5 <= tok.x0 <= st.x1 + 5:
                center = (st.top + st.bottom) / 2
                d = abs(tok.yc - center)
                if best is None or d < best_d:
                    best, best_d = i, d
        if best is None:
            unmatched.append(tok)
        else:
            by_staff[best].append(tok)
    return by_staff, unmatched


# No standard guitar has more frets than this; a merge result above it is a
# sign two unrelated notes were concatenated, not a real fret number.
_MAX_SANE_FRET = 24


def _merge_multidigit(tokens_for_staff, staff):
    """Merge adjacent 1-digit tokens on the same string line into 2-digit
    fret numbers (e.g. "1" then "2" immediately right -> "12").

    A merged pair inherits the harmonic mark from EITHER of its halves: a
    two-digit fret can reach here as two separate one-character tokens, and
    the bracket pair is drawn around the number, so only the first token has
    an opening mark beside it and only the second a closing one (see
    _mark_harmonic_digits, which requires both and therefore flags neither) -
    but a Finale export can equally emit the same number as one two-character
    span, which is flagged. Taking either half keeps the two spellings of one
    fret number reading the same way.

    Returns (merged_notes, rejected_count, suspicious_count):
    - rejected_count is how many candidate merges were declined because the
      result exceeded _MAX_SANE_FRET (kept as two separate notes instead).
    - suspicious_count is how many notes - merged or original two-character
      spans straight from the PDF text (e.g. Finale can emit a two-digit
      fret as a single span, and occasionally two adjacent single-digit
      frets end up close enough to read as one) - are still above
      _MAX_SANE_FRET and were emitted as-is because there's no safe way to
      split an already-single span back into two notes.
    Callers should surface both as warnings rather than silently trusting
    an impossible fret number.
    """
    per_string = collections.defaultdict(list)
    for tok in tokens_for_staff:
        s = staff.string_for_y(tok.yc)
        per_string[s].append(tok)

    merged_notes = []  # (x0, string, fret_text, yc, harmonic)
    rejected = 0
    suspicious = 0
    for s, toks in per_string.items():
        toks.sort(key=lambda t: t.x0)
        i = 0
        # Only 1-char tokens are merge candidates; averaging in already-merged
        # 2-char token widths inflated the window enough that two separate
        # adjacent single-digit notes (e.g. a "5" then, well clear of it, a
        # "7") could be pulled together into a nonsense fret like "57".
        single_widths = [t.width for t in toks if len(t.text) == 1]
        avg_w = sum(single_widths) / len(single_widths) if single_widths else 5.0
        while i < len(toks):
            t = toks[i]
            if (
                len(t.text) == 1
                and i + 1 < len(toks)
                and len(toks[i + 1].text) == 1
                and (toks[i + 1].x0 - t.x1) < avg_w * 0.35
                and abs(toks[i + 1].yc - t.yc) < 1.5
            ):
                nxt = toks[i + 1]
                fret_text = t.text + nxt.text
                if int(fret_text) > _MAX_SANE_FRET:
                    rejected += 1
                    merged_notes.append((t.x0, s, t.text, t.yc, t.harmonic))
                    i += 1
                else:
                    merged_notes.append((t.x0, s, fret_text, (t.yc + nxt.yc) / 2,
                                         t.harmonic or nxt.harmonic))
                    i += 2
            else:
                if len(t.text) == 2 and int(t.text) > _MAX_SANE_FRET:
                    suspicious += 1
                merged_notes.append((t.x0, s, t.text, t.yc, t.harmonic))
                i += 1
    merged_notes.sort(key=lambda n: n[0])
    return merged_notes, rejected, suspicious


# ---------------------------------------------------------------------------
# Column / chord grouping
# ---------------------------------------------------------------------------


def _group_into_columns(notes, x_tol=1.5, wide_chord_ratio=0.35):
    """notes: list of (x0, string, fret_text, yc, harmonic) sorted by x0.
    Returns [{"x": float, "notes": [MarkedNote(string, fret_text), ...]}].

    Two passes: tight x-proximity clustering catches chords engraved at
    exactly the same column; a second pass merges adjacent columns whose gap
    is small relative to the local column spacing, since engravers commonly
    offset a bass tab number a couple points right of a treble number in the
    same chord to keep both legible.

    A note is built as a musicxml.MarkedNote and then MOVED rather than
    rebuilt - both the merge and the dedupe below carry the object across, not
    its (string, fret) contents - because rebuilding the pair silently drops
    whatever the page said about the note. See MarkedNote's own docstring.
    """
    columns = []
    for x0, s, fret, yc, harmonic in notes:
        note = mxl.MarkedNote(
            s, fret, harmonic=mxl.HARMONIC_UNSPECIFIED if harmonic else None)
        if columns and (x0 - columns[-1]["x"]) < x_tol:
            columns[-1]["notes"].append(note)
            columns[-1]["x"] = min(columns[-1]["x"], x0)
        else:
            columns.append({"x": x0, "notes": [note]})

    if len(columns) > 2:
        gaps = [b["x"] - a["x"] for a, b in zip(columns, columns[1:])]
        median_gap = sorted(gaps)[len(gaps) // 2]
        merged = [columns[0]]
        for col, gap in zip(columns[1:], gaps):
            used_strings = {s for s, _ in merged[-1]["notes"]}
            new_strings = {s for s, _ in col["notes"]}
            if (
                median_gap > 0
                and gap < median_gap * wide_chord_ratio
                and not (used_strings & new_strings)
            ):
                merged[-1]["notes"].extend(col["notes"])
            else:
                merged.append(col)
        columns = merged

    for col in columns:
        seen = set()
        deduped = []
        for note in col["notes"]:
            s = note[0]
            if s in seen:
                continue
            seen.add(s)
            deduped.append(note)
        col["notes"] = sorted(deduped, key=lambda n: n[0])
    return columns


# ---------------------------------------------------------------------------
# Time signature (best-effort)
# ---------------------------------------------------------------------------


def _detect_time_signature(page, standard_staff):
    """Look for two stacked plain digits near the start of a standard staff.
    Frequently fails - see module docstring - callers should treat a None
    result as normal, not an error."""
    d = page.get_text("dict")
    candidates = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if text.isdigit() and len(text) == 1:
                    bbox = span["bbox"]
                    yc = (bbox[1] + bbox[3]) / 2
                    x0 = bbox[0]
                    if (
                        standard_staff.top - 2 <= yc <= standard_staff.bottom + 2
                        and standard_staff.x0 - 5 <= x0 <= standard_staff.x0 + 40
                    ):
                        candidates.append((x0, yc, text))
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda c: c[0])
    mid = (standard_staff.top + standard_staff.bottom) / 2
    for i in range(len(candidates)):
        for j in range(len(candidates)):
            if i == j:
                continue
            x0a, ya, ta = candidates[i]
            x0b, yb, tb = candidates[j]
            if abs(x0a - x0b) < 3.0 and ya < mid < yb:
                return int(ta), int(tb)
    return None


# ---------------------------------------------------------------------------
# Rhythm inference (heuristic, low confidence - see module docstring)
# ---------------------------------------------------------------------------


def _snap_duration(quarter_units):
    """Snap a duration in quarter-note units to the nearest alphaTex
    duration code, ignoring dotted values (not modeled)."""
    best = min(_PLAIN_DURATIONS, key=lambda p: abs(p[0] - quarter_units))
    return best[1]


def _measure_quarter_length(ts: tuple[int, int]) -> float:
    """Quarter-note budget for one measure of this time signature, e.g. 3/4
    and 6/8 both budget 3.0 quarters. The denominator matters: using the
    numerator alone would give 6/8 a budget of 6.0, doubling every
    spacing-inferred duration in a compound meter."""
    return ts[0] * 4.0 / ts[1]


def _infer_measure_rhythm(columns_in_measure, measure_quarter_len, bar_end_x):
    """Treat the x-gap from each column to the next - and from the last
    column to the measure's own barline (bar_end_x) - as proportional to
    that column's duration, normalized so gaps sum to measure_quarter_len,
    then snapped per-column. Not a real rhythm decoder - see module
    docstring.

    bar_end_x must be the actual barline (or staff end) position for this
    measure: using the mean of the preceding gaps instead systematically
    shortened the last note of any bar that ends on a long note.
    """
    if not columns_in_measure:
        return []
    xs = [c["x"] for c in columns_in_measure]
    if len(xs) == 1:
        gaps = [measure_quarter_len]
    else:
        gaps = [b - a for a, b in zip(xs, xs[1:])]
        gaps.append(bar_end_x - xs[-1])
    total = sum(gaps)
    if total <= 0:
        return [measure_quarter_len / len(xs)] * len(xs)
    return [g / total * measure_quarter_len for g in gaps]


# ---------------------------------------------------------------------------
# Rhythm decode from glyphs (high confidence - see glyph_rhythm.py)
# ---------------------------------------------------------------------------


# A tab staff and the notation staff it belongs to are part of one SYSTEM.
# Measured on the library: the vertical gap from a notation staff's bottom
# line to its own tab staff's top line is 3.3-7.3 tab-staff line spacings
# (n=254, median 5.4), while the gap to the next system down is far larger.
# 12 spacings therefore separates "same system" from "different system" with
# roughly a 65% margin either side of anything observed.
_SYSTEM_GAP_SPACINGS = 12.0
# A notation staff and its tab staff are engraved over the same horizontal
# extent - the measured overlap is 100% of the narrower staff on every one of
# those 254 pairs. Requiring most of it rules out pairing across the columns
# of a multi-instrument layout.
_PAIR_MIN_X_OVERLAP = 0.6
# Two notation staves both plausibly above one tab staff (a grand staff, or a
# neighbouring system that slipped inside the ceiling) means the pairing is a
# guess. Anything closer than this ratio to the best candidate makes it
# ambiguous, and an ambiguous pairing degrades to the spacing heuristic
# instead of reading a different line's rhythm at "high confidence".
_PAIR_AMBIGUITY_RATIO = 1.5


def _group_systems(staves):
    """Cluster staves into systems by vertical gap, so a tab staff is only
    ever paired inside its own system.

    Returns a list of lists, each in top-to-bottom order. Pairing on
    "nearest standard staff above, at any distance" instead reaches across
    systems and across instruments: where a tab-only system sits below a
    notation-only one, it read the notation system's rhythm and x-matched it
    onto this staff's fret columns - phantom rests and all - and reported
    high confidence for it.
    """
    ordered = sorted(staves, key=lambda s: s.top)
    systems = []
    for st in ordered:
        if systems:
            prev = systems[-1][-1]
            limit = max(prev.spacing, st.spacing) * _SYSTEM_GAP_SPACINGS
            if st.top - prev.bottom <= limit:
                systems[-1].append(st)
                continue
        systems.append([st])
    return systems


def _x_overlap_ratio(a, b):
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    narrower = min(a.x1 - a.x0, b.x1 - b.x0)
    if narrower <= 0:
        return 0.0
    return max(0.0, overlap) / narrower


def _pair_standard_staves(staves):
    """Resolve which standard-notation staff each tab staff should read its
    rhythm from. Returns ({id(tab_staff): std_staff}, {id(tab_staff): reason})
    where a tab staff missing from the mapping has a reason explaining why -
    callers degrade those to the spacing heuristic.

    Pairing is EXCLUSIVE: a notation staff is read by at most one tab staff.
    Letting two tab staves share one (the lead+bass case) meant each built
    its measures from a fresh view of the same events, so every rest was
    emitted twice and each staff's pitched clusters went hunting through the
    other staff's fret columns - tagging bass columns with treble durations.
    The second tab staff has no notation of its own to read, and saying so is
    the honest answer.
    """
    pairs = {}
    reasons = {}
    for system in _group_systems(staves):
        tabs = [s for s in system if s.kind == "tab"]
        stds = [s for s in system if s.kind == "standard"]
        if not tabs:
            continue
        if not stds:
            for t in tabs:
                reasons[id(t)] = "no notation staff in this tab staff's own system"
            continue
        # Score every (tab, std) candidate once, then hand out notation
        # staves closest-pair-first so the assignment is exclusive.
        candidates = []
        for t in tabs:
            for s in stds:
                if s.bottom > t.top + max(t.spacing, s.spacing) * 0.5:
                    continue  # notation is engraved above its tab staff
                gap = t.top - s.bottom
                if gap > max(t.spacing, s.spacing) * _SYSTEM_GAP_SPACINGS:
                    continue
                if _x_overlap_ratio(t, s) < _PAIR_MIN_X_OVERLAP:
                    continue
                candidates.append((gap, t, s))
        candidates.sort(key=lambda c: c[0])
        per_tab = collections.defaultdict(list)
        for gap, t, s in candidates:
            per_tab[id(t)].append((gap, s))
        taken = set()
        for gap, t, s in candidates:
            if id(t) in pairs or id(t) in reasons:
                continue
            mine = per_tab[id(t)]
            if len(mine) > 1 and mine[1][0] <= mine[0][0] * _PAIR_AMBIGUITY_RATIO:
                reasons[id(t)] = (
                    "two notation staves are equally plausible for this tab staff - "
                    "pairing would be a guess"
                )
                continue
            if id(s) in taken:
                reasons[id(t)] = (
                    "the notation staff for this system is already read by another tab staff"
                )
                continue
            taken.add(id(s))
            pairs[id(t)] = s
        for t in tabs:
            if id(t) not in pairs and id(t) not in reasons:
                reasons[id(t)] = "no notation staff within reach in this tab staff's own system"
    return pairs, reasons


def _cluster_pitched_glyph_events(events, cluster_x_tol=1.5):
    """Group glyph-decoded note events sharing (almost) the same x into one
    cluster - the members of a chord (chord noteheads land at identical x, a
    beat apart from neighbors by a full note-spacing), or of two overlapping
    voices notated at the same onset. A cluster becomes ONE beat, not one
    beat per member: matching every member independently against tab columns
    let a chord's later noteheads each go hunting for their own "nearest
    unused column" once the first member had already claimed the right one,
    silently stealing a neighboring beat's column in a dense passage."""
    events = sorted(events, key=lambda n: n.x)
    clusters = []
    for ev in events:
        if clusters and abs(ev.x - clusters[-1][0].x) <= cluster_x_tol:
            clusters[-1].append(ev)
        else:
            clusters.append([ev])
    return clusters


# How close a rest has to sit to a pitched onset before it is taken to BE
# that onset's second voice rather than a beat of its own, in staff-line
# spacings. The library engraves consecutive sixteenths about 1.25 spacings
# apart, so this has to stay well under that to avoid swallowing a real
# separate rest; engraving jitter between a rest glyph's centre and a
# notehead's centre at the same onset is a fraction of a spacing.
_REST_ONSET_MERGE_SPACINGS = 0.8


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------

# _ONSET_SHARE_SPACINGS lives in glyph_rhythm - it is also what
# glyph.decode_note_events uses to tell a coincident duplicate's genuine
# second voice from a runner-up stem borrowed from a different onset (issue
# #116), and glyph_rhythm cannot import this module back. Referenced here by
# the same name so nothing reading this file has to know it moved.
_ONSET_SHARE_SPACINGS = glyph._ONSET_SHARE_SPACINGS

# How far from the column it already claimed one onset may reach for ANOTHER
# column, in notation-staff line spacings. Engravers offset a bass tab number
# a couple of points right of a treble one in the same chord, so an onset's
# digits can arrive as two columns a fraction of a note-spacing apart - but
# the next ONSET's column is a full note-spacing away (about 2.5 spacings for
# eighths, 1.25 for sixteenths), so this has to stay well under that or an
# onset short of digits eats its neighbour's column.
_CHORD_SPLIT_SPACINGS = 0.6

# Guitar fingerstyle and classical writing is usually a melody over an
# accompaniment, but classical guitar arrangements genuinely go to three: a
# melody, an arpeggiated inner voice and a sustained bass, each with its own
# rhythm (Spanish-Romance-Guitar-Free.pdf, measured for issue #133 - with a
# ceiling of two the bass had nowhere to live and folded into the melody's
# chord). Measured across the whole library at the coincident-notehead
# binding this project currently has, a literal three-way onset collision -
# the strongest evidence a bar needs more than two voices - appears in
# exactly one score, and that one is a known missing-stem case, not a real
# third voice: the same mis-binding load-bearing for Spanish Romance (#116)
# hides the third voice's own collisions everywhere else, which is why this
# count cannot be pushed higher than "guitar rarely needs more than three"
# by measurement alone. Nothing in the library shows any need for a fourth,
# so the ceiling moves to three and no further; a fourth simultaneous stem
# is still far more likely to be a chord whose shared stem was not found
# than a real fourth voice, and extra groups past the ceiling join the
# lowest voice - the bar reports itself as overfull rather than this
# inventing a voice nothing engraved.
_MAX_VOICES = 3


class _StemGroup:
    """One beat: every notehead hanging off ONE engraved stem (a chord), or a
    notehead with no stem to hang off."""

    __slots__ = ("members", "x", "y", "stem_dir", "signal", "voice")

    def __init__(self, members):
        self.members = list(members)
        first = self.members[0]
        self.stem_dir = first.stem_dir
        # What this group says about which voice it belongs to. Two groups
        # sounding together are only two voices if they say DIFFERENT things.
        if first.stem_dir:
            self.signal = first.stem_dir
        elif first.notehead_kind == "notehead_whole":
            self.signal = "whole"  # never takes a stem in any notation
        else:
            self.signal = None  # a stem that should exist and was not found
        self.voice = 0
        self._recentre()

    def absorb(self, other):
        self.members.extend(other.members)
        self._recentre()

    def _recentre(self):
        self.x = sum(m.x for m in self.members) / len(self.members)
        self.y = sum(m.y for m in self.members) / len(self.members)


def _stem_groups(events, onset_tol):
    """Partition a measure's pitched glyph events into one group per beat, in
    x order.

    This is the basis of both voice separation and of telling a chord apart
    from two simultaneous voices - the one distinction the tab staff cannot
    make. A chord is several noteheads threaded on ONE stem and has to stay
    ONE beat; two noteheads at the same onset on DIFFERENT stems are two
    voices and have to become two beats. In the tab both are the same thing,
    a vertical stack of fret numbers at one x. Only the stems separate them,
    because an engraver draws exactly one per beat.

    Two things are therefore folded back together rather than being allowed
    to look like extra voices:

      - a notehead whose stem was NOT found joins whatever else sounds at its
        onset. Stems are what two-voice writing is notated with, so an onset
        carrying no stem evidence is not evidence of two voices - it is a
        chord. Some scores in the library engrave chords whose stem the
        vector pass cannot see at all, and splitting those by pitch invented
        a second voice out of one chord and left both halves short.
      - two groups sounding together whose stems point the SAME way. That is
        not three-voice writing; it is one chord whose stem came through as
        two separate strokes.

    Sharing a stem is necessary but NOT sufficient to be one beat: the
    noteheads also have to sound together. A notehead whose own stem the
    vector pass missed can end up threaded onto a neighbouring note's stem
    (see glyph_rhythm._stem_through_notehead, which deliberately drops the
    tight end-window), and keying on the stem alone then welded two
    consecutive onsets into a single chord positioned between them - losing
    an onset and its duration outright. So a stem's noteheads are additionally
    split into runs that actually share an onset.
    """
    by_stem = {}
    member_lists = []
    for ev in sorted(events, key=lambda e: e.x):
        if ev.stem_key is None:
            member_lists.append([ev])
            continue
        runs = by_stem.setdefault(ev.stem_key, [])
        target = None
        for run in runs:
            if abs(ev.x - run[0].x) <= onset_tol:
                target = run
                break
        if target is None:
            target = []
            runs.append(target)
            member_lists.append(target)
        target.append(ev)
    pending = [_StemGroup(m) for m in member_lists]

    # Stem-bearing groups settle first, so a notehead with no stem has
    # something real to attach itself to.
    groups = []
    for g in sorted(pending, key=lambda g: (g.signal is None, g.x)):
        host = None
        best = None
        for h in groups:
            if abs(h.x - g.x) > onset_tol:
                continue
            if g.signal is not None and h.signal != g.signal:
                continue
            d = abs(h.y - g.y)
            if best is None or d < best:
                host, best = h, d
        if host is None:
            groups.append(g)
        else:
            host.absorb(g)
    groups.sort(key=lambda g: g.x)
    return groups


def _stem_group_duration(group):
    """One (duration_code, dots) for a whole chord, composed from every
    notehead on the stem rather than voted on.

    Only the notehead at the stem's END can see the flag or beam that
    shortens the chord (see glyph_rhythm._best_stem, and the looser
    _stem_through_notehead that attaches the rest of them); the inner members
    read a plain quarter, and in a three-note chord they would outvote the
    one member that actually read the duration. A flag can be missed but
    never invented, so take the largest flag and dot count found anywhere on
    the stem, over the notehead value its members agree on.
    """
    counts = collections.Counter(m.base_units for m in group.members)
    base = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    flags = max(m.flags for m in group.members)
    dots = max(m.dotted for m in group.members)
    plain = base / (2 ** flags)
    codes = glyph.DURATION_CODE
    code = codes.get(plain) or codes[min(codes, key=lambda k: abs(k - plain))]
    return code, min(dots, 2)


def _onsets(groups, onset_tol):
    """Cluster x-sorted stem groups into the onsets they sound at."""
    onsets = []
    for g in groups:
        if onsets and (g.x - onsets[-1][0].x) <= onset_tol:
            onsets[-1].append(g)
        else:
            onsets.append([g])
    return onsets


def _stemless_voice(group, up_y, down_y):
    """Which voice a notehead with no stem belongs to.

    A whole note is the case that matters: it never takes a stem in any
    notation, so the only signal left is where it sits relative to the voices
    that do have one. Page y grows downward, so the upper voice has the
    smaller y.
    """
    if up_y is not None and down_y is not None:
        return 0 if abs(group.y - up_y) <= abs(group.y - down_y) else 1
    if up_y is not None:
        return 1 if group.y > up_y else 0
    if down_y is not None:
        return 0 if group.y < down_y else 1
    return 0


def _assign_group_voices(groups, onset_tol):
    """Split one bar's stem groups into voices - a list of voices in
    top-to-bottom order, each a list of groups in x order. A single-element
    result means the bar is monophonic and must stay that way.

    Stem direction is the signal engravers use: an upper voice's stems are
    forced up and a lower voice's forced down, whatever the pitches do. But
    stem direction ALSO flips with pitch in ordinary single-voice writing - a
    melody crossing the middle line is engraved stems-down above it and
    stems-up below it - so direction ON ITS OWN would shred a monophonic line
    into two voices at every crossing.

    What separates the two cases is simultaneity: a single voice never has
    two stems at one onset. So a bar counts as polyphonic only where some
    onset genuinely carries more than one group - which after _stem_groups
    has folded chords back together means two stems saying different things -
    and stem direction is used to sort the notes into voices only once that
    is established.

    A third voice (issue #133) is not a third stem direction - there are only
    two, up and down - so _stemless_voice's own return value only ever being
    0 or 1 looks like the hardcoded-pair bug a raised ceiling would need
    fixing, and was checked as one: it is not. A stem-bearing group's voice
    is fixed by which way its stem points, so it can never collide with a
    group going the other way UNLESS a third, direction-sharing voice is
    genuinely present - and that collision is exactly what the loop below
    catches and re-ranks by pitch, uncapped at two by _MAX_VOICES rather
    than the literal 1 this used to read. A stemless group's own initial
    guess only has to be WRONG (any value that collides) for that rescue to
    fire; it does not have to already be 0, 1 or 2. Verified on an engraved
    three-voice fixture (melody, arpeggio, sustained bass) with no change
    below this function: the ceiling was the only hard limit.
    """
    onsets = _onsets(groups, onset_tol)
    if not any(len(o) > 1 for o in onsets):
        return [groups]

    ups = [g for g in groups if g.stem_dir == "up"]
    downs = [g for g in groups if g.stem_dir == "down"]
    up_y = sum(g.y for g in ups) / len(ups) if ups else None
    down_y = sum(g.y for g in downs) / len(downs) if downs else None

    for g in groups:
        if g.stem_dir == "up":
            g.voice = 0
        elif g.stem_dir == "down":
            g.voice = 1
        else:
            g.voice = _stemless_voice(g, up_y, down_y)

    # Two groups sounding at one onset cannot be the same voice. Where that
    # happened - two stemless noteheads, or two stems the same way up - fall
    # back to pitch order within the onset, which is what "upper" and "lower"
    # voice mean in the first place.
    for onset in onsets:
        if len(onset) < 2 or len({g.voice for g in onset}) == len(onset):
            continue
        for rank, g in enumerate(sorted(onset, key=lambda g: g.y)):
            g.voice = min(rank, _MAX_VOICES - 1)

    voices = [[g for g in groups if g.voice == v] for v in range(_MAX_VOICES)]
    voices = [v for v in voices if v]
    return voices or [groups]


def _share_unison_digits(heads, digits, taken, per_group):
    """Give a notehead left with no fret number the digit its COINCIDENT TWIN
    was given, where the onset is a CHORD the tab has fully named and the twin
    belongs to another voice (issue #137).

    WHY A DIGIT CAN BE MISSING AT ALL. _match_onset_columns hands out the
    digits at an onset one per notehead, ranked by pitch against string, and
    that is right whenever every notehead is its own sounding note. A UNISON
    SHARED BETWEEN TWO VOICES IS NOT: it is one string, plucked once, notated
    in each voice - drawn as the same notehead glyph stamped twice at one
    position, one copy per voice's stem (see glyph.decode_note_events's
    coincident-pair pass, issue #116) - and the tab prints ONE number for it,
    because there is only one string to name. Two noteheads, one digit.

    A unison ALONE does not run short of digits, which is why this did not
    surface with #116: with nothing else at the onset the engraver is free to
    write the two voices on two different strings and does (MuseScore writes
    the `unison_voices` fixture's two voices as 2 on the fourth string and 7
    on the fifth), so there is a digit apiece. Put the unison inside a CHORD
    and that freedom is gone - the chord's own members are what the column
    holds, the unison is one of them, and the onset has three noteheads and
    two digits. Measured on The Cosmic Wheel (FF XI), 12 onsets across 4
    pages, every one of them that shape: an upper voice's two-note chord
    whose lower member is the lower voice's own eighth.

    Unfixed, the third notehead simply got nothing, and the VOICE it belongs
    to lost its note for that beat: 12 bars read an eighth short of their
    meter and were padded with silence nothing on the page prints. Giving it
    its twin's digit says what the page says - both voices sound that string
    at that moment.

    WHY THE CHORD IS REQUIRED, and not merely a coincident pair short of a
    digit. Only where the tab named EVERY distinct notehead position at this
    onset exactly once - `len(digits) == len(positions)`, with more than one
    position, i.e. a chord - is the column demonstrably complete, and a
    leftover copy therefore provably a second stem on a string the tab
    already names. Where the whole onset IS the coincident pair, one printed
    notehead over one printed digit, the page is self-consistent as a single
    note and the only thing suggesting otherwise is the two-stem signature,
    which #116 measured to be unreliable on its own: its adjudicated example
    (Carulli-Moderato-Op192) reads as single notes on the printed page while
    its content stream carries exactly that signature. Sharing the digit
    there would double a printed note into two sounding ones on no evidence.
    The two families are far apart in size as well as in kind - across the
    library's 293 extractable scores, 500 short onsets are a coincident pair
    alone over one digit split across two voices, against 65 that are a
    coincident copy inside a fully named chord. Of those 65: 16 have their
    twin in ANOTHER voice and take the shared digit (12 on The Cosmic Wheel,
    4 on Castti, the Apothecary), 48 have no coincident twin at the leftover
    head's own position at all (see the limitation below - a different
    defect, not one this refuses), and exactly 1 is a pair both of whose
    copies stayed in one voice, refused below (Kids Run Through the City
    Corner, FF VI).

    ONLY COINCIDENT, AND ONLY ACROSS VOICES. Position is the test for the
    first: two copies of one glyph at one position are one sounding note,
    whereas a chord's members sit at DIFFERENT positions and each needs its
    own digit, so a head that merely lacks a digit gets nothing here and is
    still reported as a notehead with no fret number. Requiring the twin to
    be in another GROUP matters for the same reason: where a coincident pair
    could not be split across two stems (glyph.decode_note_events's
    coincident_unsplit_pairs) both copies sit in one group, and copying the
    digit there would double one voice's note into two rather than give a
    second voice its own.

    KNOWN LIMITATION, measured and left alone. `heads` is in pitch order and
    the rank match consumes it from the top, so a leftover head is always one
    of the LOWEST, and it only has a twin among the matched ones when the
    duplicated position is the chord's own lowest. A unison on a chord's TOP
    or MIDDLE member therefore gets nothing here: both its copies sit inside
    the matched slice and take two different digits between them, and the
    member left starved is a different notehead with no twin at all. That is
    a distinct, pre-existing mis-ranking rather than a case this refuses -
    across the library it is 48 of the 65 in-chord onsets (47 with the unison
    on the top member, 1 in the middle; 34 of them on Spanish-Romance alone),
    and every one of them reads exactly as it did before this existed. See
    issue #141.

    `heads` is the onset's (notehead, group) pairs in pitch order and
    `digits` the fret numbers matched against them, so `heads[len(digits):]`
    is what the rank match left over. `taken` maps a rounded (x, y) to the
    (digit, id(group)) the head drawn there was given, and `per_group` is
    extended in place. Returns (noteheads still without a digit, digits
    shared) - the second is an INFERENCE this made rather than a reading, and
    is disclosed as `unison_digits_shared` for the same reason
    coincident_unsplit_pairs and dots_unassigned are."""
    unmatched = heads[len(digits):]
    if not unmatched:
        return 0, 0
    positions = {(round(m.x, 2), round(m.y, 2)) for m, _g in heads}
    if len(positions) < 2 or len(digits) != len(positions):
        return len(unmatched), 0
    starved = 0
    sharedn = 0
    for m, g in unmatched:
        shared = taken.get((round(m.x, 2), round(m.y, 2)))
        if shared is None or shared[1] == id(g):
            starved += 1
            continue
        per_group[id(g)].append(shared[0])
        sharedn += 1
    return starved, sharedn


def _mark_from_notehead(note, head):
    """Move what the NOTATION staff says about a note onto the tab digit that
    will be emitted for it.

    Two marks travel this way, and neither can be read from the tablature
    alone:

      - a DIAMOND notehead is how the notation staff writes a harmonic (issue
        #63). It says only that the note is one, not whether it is natural or
        artificial - the two are drawn with the same head - so the mark it
        sets is HARMONIC_UNSPECIFIED. Where the tab ALSO brackets the fret
        number the note is already marked and this changes nothing: the two
        conventions travel together on 120 of the library's 121 harmonic
        scores, which is why either alone is enough and neither is required.
      - both ends of a TIE (issue #81), matched from the engraved curve by
        glyph._mark_ties. Both, and not only the start, because the two ends
        are not interchangeable here: the second note of a tie is not struck,
        so the tablature normally prints NO fret number under it, and the
        digit this note is holding is therefore very often a neighbour's that
        the rank match reached for. Which note it should really be is settled
        by _resolve_ties, over the whole part, from the note the tie starts
        at - a tie can cross a barline and this function sees one onset.

    A rest carries neither: `head` is always a pitched notehead here.
    """
    harmonic = ... if head.notehead_kind != "notehead_diamond" else mxl.HARMONIC_UNSPECIFIED
    if harmonic is ... and not head.tied_next and not head.tied_prev:
        return note
    return mxl.mark_note(note, harmonic=harmonic,
                         tie_start=True if head.tied_next else ...,
                         tie_stop=True if head.tied_prev else ...)


def _match_onset_columns(onset_groups, cols_sorted, col_xcs, used, x_tol, split_tol):
    """Hand the tab digits sounding at one onset out to the groups there.

    A chord and two simultaneous voices are the same shape in the tab - a
    stack of fret numbers at one x - so the split cannot be read from the tab
    at all. It is read from the notation instead: the noteheads at this onset
    ordered by PITCH correspond one for one to the digits at this column
    ordered by STRING (string 1 is the highest-pitched). Rank-matching them
    puts each voice's note on the string the engraver actually wrote it on,
    and does so whether the onset is one chord or two voices.

    An onset can need MORE than one column, because engravers offset a bass
    tab number a couple of points right of a treble one in the same chord to
    keep both legible and _group_into_columns does not always merge the two
    back. But the search for those extra columns has to stay inside this
    onset's own neighbourhood (`split_tol`), not the full notehead-to-digit
    window (`x_tol`, which is wider than the gap between consecutive
    columns): searching that far let an onset whose own column held fewer
    digits than it had noteheads consume the NEXT onset's column instead,
    sounding those frets a beat early and dropping the notes that column
    belonged to.

    A UNISON SHARED BETWEEN TWO VOICES IS ONE PLUCKED STRING, so the tab
    prints ONE fret number for it however many voices sound it, and the
    one-notehead-one-digit rank match runs a digit short at that onset. See
    _share_unison_digits, which is what closes the gap.

    WHAT THE NOTEHEAD SAYS ABOUT THE NOTE comes across here too, and this is
    the only place it can: the tab digit is what gets emitted, the notehead is
    what carries the mark, and this rank match is where the two meet. A
    diamond notehead says the note is a harmonic (issue #63) and a matched tie
    curve says it is held into the next note of the same pitch (issue #81);
    both are moved onto the digit the head was given. See _mark_from_notehead.

    Returns ({id(group): [MarkedNote(string, fret), ...]},
    noteheads_with_no_digit, digits_shared) - the last being how many
    noteheads were given a digit the tab printed for their coincident twin
    rather than for them, which is an inference and is disclosed as such
    (`unison_digits_shared`).
    """
    heads = sorted(((m, g) for g in onset_groups for m in g.members),
                   key=lambda mg: mg[0].y)
    needed = len(heads)
    ox = sum(m.x for m, _ in heads) / needed

    digits = []
    anchor = None
    while len(digits) < needed:
        # The first column is found from the notehead x; any further one has
        # to sit beside THAT column, not merely somewhere in the window.
        centre, reach = (ox, x_tol) if anchor is None else (anchor, split_tol)
        lo = bisect.bisect_left(col_xcs, centre - reach)
        hi = bisect.bisect_right(col_xcs, centre + reach)
        best_i, best_d = None, None
        for i in range(lo, hi):
            if used[i]:
                continue
            d = abs(col_xcs[i] - centre)
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        if best_i is None:
            break
        used[best_i] = True
        if anchor is None:
            anchor = col_xcs[best_i]
        digits.extend(cols_sorted[best_i]["notes"])

    digits.sort(key=lambda n: n[0])  # by string: 1 is the highest-pitched
    per_group = {id(g): [] for g in onset_groups}
    # Where a head DID get a digit, remember it against the position it was
    # drawn at, so a coincident twin left over below can be given the same
    # one. First writer wins: with three copies at one position (never yet
    # seen on a page) the extra copies all take the first copy's digit,
    # which is the only digit the tab printed for that string.
    taken = {}
    for (m, g), digit in zip(heads, digits):
        digit = _mark_from_notehead(digit, m)
        per_group[id(g)].append(digit)
        taken.setdefault((round(m.x, 2), round(m.y, 2)), (digit, id(g)))
    starved, shared_here = _share_unison_digits(heads, digits, taken, per_group)
    leftover = digits[needed:]
    if leftover:
        # More fret numbers at this onset than noteheads were read from the
        # notation. The tab plainly shows those notes, so give them to the
        # lowest voice sounding here rather than dropping them.
        per_group[id(max(onset_groups, key=lambda g: g.y))].extend(leftover)
    for g in onset_groups:
        per_group[id(g)].sort(key=lambda n: n[0])
    return per_group, starved, shared_here


def _rest_beats_for(quarters, limit=12, inferred=False):
    """Decompose a span of silence into plain rest beats, longest first.

    `inferred=True` marks every beat produced as silence this extractor
    deduced from the meter rather than read from a rest glyph on the page (see
    musicxml.InferredRest). Every caller here that fills from the meter passes
    it: nothing on the page said these rests were there.
    """
    out = []
    remaining = quarters
    for q, code in _PLAIN_DURATIONS:
        while remaining >= q - 1e-6 and len(out) < limit:
            out.append((code, 0, mxl.inferred_rest() if inferred else []))
            remaining -= q
    return out


def _pad_voice_to_budget(tagged, budget, bar_first_x, onset_tol):
    """Fill a voice's silence so it accounts for the whole bar on its own.

    KEPT, deliberately, and marked. A voice that sounds for only part of a bar
    is engraved with rests for the remainder, and the silence has to be there
    for the bar to play: without it a voice that entered late starts on the
    downbeat instead, so every note in it sounds early and the voices drift
    against each other. Dropping the padding would not turn a wrong bar into an
    honestly short one - it would turn a bar whose notes are in the wrong
    PLACES into a bar whose notes are in the wrong places AND short. So the
    silence stays, and the lie it used to tell is removed instead: every beat
    inserted here is marked as inferred, which keeps it out of the Rule 8 sums
    (_bar_conformance, musicxml.voice_durations) and out of the emitted
    MusicXML's rests (it becomes `<forward>` - Rule 14). The bar therefore
    still reports SHORT by exactly what was missing, still reaches the
    confidence downgrade, and still lays out.

    Where the decoded rest glyphs already account for the silence this adds
    nothing.

    Nothing is returned. How much silence was inferred, and in which bars, is
    read back off the marked beats themselves (_bar_conformance) rather than
    reported through a side channel here - one source of truth, and the one the
    emitters read.
    """
    total = sum(_beat_quarters(code, dots) for _x, code, dots, _n in tagged)
    deficit = budget - total
    if deficit <= 1e-6:
        return
    fills = _rest_beats_for(deficit, inferred=True)
    if not fills:
        return
    # A voice whose first note sits well right of the bar's first onset
    # entered late, so its silence belongs at the front.
    late = not tagged or (tagged[0][0] - bar_first_x) > onset_tol
    x = (bar_first_x - 1.0) if late else (tagged[-1][0] + 1.0)
    inserted = [(x, code, dots, notes) for code, dots, notes in fills]
    if late:
        tagged[:0] = inserted
    else:
        tagged.extend(inserted)


def _build_measure_beats_glyph(m_cols, m_lo, m_hi, note_events, note_xs, x_tol,
                               notation_spacing, budget=None):
    """Build one measure's VOICES from glyph-decoded note/rest events on the
    tab staff's paired standard-notation staff, matching each event to the
    tab column at (approximately) the same x. A tab column with no matching
    glyph event within x_tol falls back to an eighth-note placeholder
    (counted in the returned unmatched-column total rather than silently
    trusted - see _extract's unmatched-column warning).

    Classical and fingerstyle guitar is polyphonic - a melody sounding OVER
    an independent bass line - so the notes of a bar do not form one
    sequence. They are grouped by the stem each hangs off (_stem_groups),
    split into voices by stem direction wherever two stems genuinely sound
    together (_assign_group_voices), and each voice is then filled out with
    rests so it accounts for the bar on its own (_pad_voice_to_budget).
    Assembling everything into one sequence instead made a bar hold the SUM
    of its voices - typically about double its meter - with every individual
    duration decoded correctly.

    Rests: in a monophonic bar they are clustered by onset and dropped where
    a pitched onset already sits, since a rest under a sounding note is the
    other voice's and not a beat of its own (emitting it made a 3/4 bar hold
    `:4 r` plus three notes). In a polyphonic bar that same rest is real
    information - it says WHICH voice is silent there - so it is assigned to
    a voice instead of dropped.

    `note_events` must be sorted by x and `note_xs` their x values, so each
    measure takes a bisect-bounded slice instead of rescanning the staff's
    whole event list (O(M*E) across a staff's measures).
    `notation_spacing` is the NOTATION staff's line spacing: every tolerance
    here compares the positions of two glyphs on that staff, not on the tab
    staff. `budget` is the measure's quarter-note allowance, needed to know
    how much of each voice is silence; without it no rests are inferred.

    Returns (voices, unmatched_columns, unmatched_glyph_notes,
    unison_digits_shared): voices is a list of one or more beat lists, each a
    list of (duration_code, dots, notes) triples in x order ready for
    _fmt_beat; unmatched_columns is how many tab columns had no glyph note
    within x_tol; unmatched_glyph_notes is how many decoded noteheads had no
    fret number to match (expected to be rare - every played tab note should
    have one); unison_digits_shared is how many were given the digit their
    coincident twin's position was printed with instead (see
    _share_unison_digits) - an inference, disclosed rather than silent.

    Silence that had to be deduced from the meter is not reported here: the
    beats carry it themselves, as an InferredRest notes slot, which is what the
    conformance count and both emitters read.
    """
    lo_i = bisect.bisect_left(note_xs, m_lo)
    hi_i = bisect.bisect_left(note_xs, m_hi)
    measure_events = note_events[lo_i:hi_i]
    onset_tol = notation_spacing * _ONSET_SHARE_SPACINGS
    merge_tol = notation_spacing * _REST_ONSET_MERGE_SPACINGS
    split_tol = notation_spacing * _CHORD_SPLIT_SPACINGS
    groups = _stem_groups([n for n in measure_events if not n.is_rest], onset_tol)
    rest_clusters = _cluster_pitched_glyph_events([n for n in measure_events if n.is_rest])

    voice_groups = _assign_group_voices(groups, onset_tol)
    polyphonic = len(voice_groups) > 1
    voice_of = {id(g): vi for vi, gs in enumerate(voice_groups) for g in gs}

    cols_sorted = sorted(m_cols, key=lambda c: c["x"])
    col_xcs = [c["xc"] for c in cols_sorted]
    used = [False] * len(cols_sorted)

    tagged = [[] for _ in voice_groups]  # per voice: (x, code, dots, notes)
    unmatched_glyph_notes = 0
    unison_digits_shared = 0
    onsets = _onsets(groups, onset_tol)
    for onset in onsets:
        per_group, missing, shared = _match_onset_columns(
            onset, cols_sorted, col_xcs, used, x_tol, split_tol)
        unmatched_glyph_notes += missing
        unison_digits_shared += shared
        for g in onset:
            notes = per_group[id(g)]
            if not notes:
                # A decoded notehead with no fret number anywhere near it -
                # already counted above. Nothing to emit a note for, so skip
                # rather than fabricate one.
                continue
            code, dots = _stem_group_duration(g)
            tagged[voice_of[id(g)]].append((g.x, code, dots, notes))

    _place_rest_clusters(rest_clusters, onsets, voice_groups, voice_of, tagged,
                         merge_tol, polyphonic)

    unmatched_columns = 0
    for i, col in enumerate(cols_sorted):
        if not used[i]:
            unmatched_columns += 1
            # A conservative placeholder, in whichever voice already plays
            # nearest to it on the fretboard.
            tagged[_placeholder_voice(col, tagged)].append(
                (col["x"], 8, 0, col["notes"]))

    for t in tagged:
        t.sort(key=lambda b: b[0])

    # A voice only exists if something in it actually plays. One whose every
    # group lost its fret number holds no notes at all, and padding it filled
    # a whole bar with inferred silence - a phantom voice that counted as
    # polyphony, overstated how much rest was deduced from the meter, and
    # wrote a meaningless `\voice :2 r :4 r` into the stored transcription.
    # A bar of nothing but decoded rests keeps them, though - that is a real
    # silent bar, not a phantom beside a voice that plays.
    noted = [t for t in tagged if any(notes for _x, _c, _d, notes in t)]
    live = noted if noted else [t for t in tagged if t]

    if len(live) > 1 and budget:
        first_x = min(t[0][0] for t in live)
        for t in live:
            _pad_voice_to_budget(t, budget, first_x, onset_tol)

    return ([[(code, dots, notes) for _x, code, dots, notes in t] for t in live],
            unmatched_columns, unmatched_glyph_notes, unison_digits_shared)


def _voice_mean_y(groups):
    return sum(g.y for g in groups) / len(groups) if groups else 0.0


def _mean_string(notes):
    return sum(s for s, _f in notes) / len(notes) if notes else 0.0


def _placeholder_voice(col, tagged):
    """Which voice an unmatched tab column's placeholder belongs to: whichever
    already plays nearest to it on the fretboard.

    A column with no notation event beside it has no stem to read, so the
    STRING it is written on is the only signal left - and a string number can
    only be compared against other string numbers, never against a notehead's
    position on the notation staff, which is a different quantity in
    different units.
    """
    target = _mean_string(col["notes"])
    best, best_d = 0, None
    for vi, beats in enumerate(tagged):
        strings = [s for _x, _c, _d, notes in beats for s, _f in notes]
        if not strings:
            continue
        d = abs(sum(strings) / len(strings) - target)
        if best_d is None or d < best_d:
            best, best_d = vi, d
    return best


def _place_rest_clusters(rest_clusters, onsets, voice_groups, voice_of, tagged,
                         merge_tol, polyphonic):
    """Turn decoded rest glyphs into beats in the right voice.

    In a MONOPHONIC bar a rest sharing an onset with a sounding note is the
    other voice's rest and is not a beat here at all - emitting it made a 3/4
    bar hold four beats' worth and shifted the whole bar's playback while
    still reporting high confidence. Simultaneous rests collapse to one beat
    for the same reason.

    In a POLYPHONIC bar that same rest is exactly the information needed: it
    says which voice is silent at that onset. So it is assigned to a voice
    that has nothing sounding there rather than dropped, and two rests at one
    onset become one beat in each voice.

    That only holds for a rest that IS at a known onset. A rest glyph engraved
    further than merge_tol from every decoded onset says nothing about which
    voice it belongs to or where in the bar it falls, and adding it anyway
    made a voice hold a beat more than its meter and pushed everything after
    it late - the same phantom-rest fault the monophonic branch exists to
    prevent. Those are dropped here too, and the silence they stood for is
    recovered by _pad_voice_to_budget from the meter instead.
    """
    onset_xs = [sum(g.x for g in o) / len(o) for o in onsets]
    voice_y = {vi: _voice_mean_y(gs) for vi, gs in enumerate(voice_groups)}

    for cluster in rest_clusters:
        rx = sum(ev.x for ev in cluster) / len(cluster)
        j = bisect.bisect_left(onset_xs, rx)
        hit = None
        for k in (j - 1, j):
            if 0 <= k < len(onset_xs) and abs(onset_xs[k] - rx) <= merge_tol:
                hit = onsets[k]
                break

        if not polyphonic:
            if hit is not None:
                continue  # the other voice's rest under a sounding note
            # one beat per rest onset; take the longest reading so a
            # collapsed pair doesn't silently shorten the bar
            rep = min(cluster, key=lambda ev: (ev.duration_code, -ev.dotted))
            tagged[0].append((rx, rep.duration_code, rep.dotted, []))
            continue

        if hit is None:
            continue  # no onset to pin it to - see the docstring
        # Voices sounding at this onset, plus any voice that already holds a
        # beat here at all: a rest cannot share an onset with the voice's own
        # note, and two rests must not stack in one voice either.
        free = set(voice_y) - {voice_of[id(g)] for g in hit}
        free = {v for v in free
                if not any(abs(bx - rx) <= merge_tol for bx, _c, _d, _n in tagged[v])}
        if not free:
            continue  # every voice is already sounding here
        for ev in sorted(cluster, key=lambda e: e.y):
            if not free:
                break
            v = min(free, key=lambda k: abs(voice_y[k] - ev.y))
            free.discard(v)
            tagged[v].append((ev.x, ev.duration_code, ev.dotted, []))


# ---------------------------------------------------------------------------
# Rhythm source resolution
# ---------------------------------------------------------------------------

# Where one tab staff's durations came from. Every document-level warning and
# the reported rhythm confidence are derived from the collected set of these
# in ONE place (see _rhythm_report), instead of a branch in the measure loop
# plus a warnings ladder plus a confidence ladder all kept mutually
# consistent by hand.
PROV_GLYPHS = "glyphs"
PROV_GLYPHS_DEGRADED = "glyphs-degraded"
PROV_SPACING = "spacing"

# The decoder reports how much of a staff's music-font text it could not
# classify. Acting on it matters because an unmapped flag or rest glyph does
# not fail loudly - it decodes as a systematically wrong duration while
# every other signal still looks healthy. Measured across the library: the
# unknown-glyph ratio is 0.0 for the 99th percentile of notation staves and
# peaks at 0.065, so these thresholds sit far above anything the calibrated
# vocabulary actually produces and only fire on genuinely foreign input.
_UNKNOWN_RATIO_FALLBACK = 0.35
_UNKNOWN_RATIO_WARN = 0.10


class _RhythmSource:
    """One tab staff's resolved rhythm source: where its durations come
    from, the decoded events if any, and what to tell the user about it."""

    __slots__ = ("provenance", "note_events", "note_xs", "detail", "stats")

    def __init__(self, provenance, note_events=None, detail=None, stats=None):
        self.provenance = provenance
        self.note_events = note_events or []
        self.note_xs = [n.x for n in self.note_events]
        self.detail = detail
        self.stats = stats or {}

    @property
    def uses_glyphs(self):
        return self.provenance in (PROV_GLYPHS, PROV_GLYPHS_DEGRADED)


# A group of this many lines or more was almost certainly a staff: five is
# the smallest notation staff and six the smallest tablature one, so anything
# at or above that which was thrown away probably took music with it.
_STAFF_SIZED_GROUP = 5


def _discard_report(discarded_groups):
    """Say what was thrown away, and cap the fret confidence if it mattered.

    Returns (warnings, fret_confidence_override | None, systems_unread,
    systems_unread_pages) - the last two being the COUNT of what was lost,
    which issue #152 added because prose is not a figure. A caller comparing
    two extractions, or a client reading this back out of storage, cannot
    grep a sentence; `systems_unread` is the same fact as a number, and the
    rule it exists to keep is that a system whose bars were not read must be
    counted.

    A group of staff lines whose count is neither 5 nor 6 cannot be read, and
    it used to be reported as "N staff-line group(s) with an unexpected line
    count were ignored" - which does not say that a tab system and six bars
    of music are missing from the transcription, and left the confidence at
    "high" while they were.

    That combination is the worst property this code could have, because the
    loss makes the score look BETTER: the bars that vanish are as likely as
    any to be the ones that did not add up, so discarding a system can move
    the defective-bar count down. A number that improves when music
    disappears is worse than no number. So the size of what went is named,
    and a staff-sized group caps the claim about the frets - which is exactly
    the claim it undermines, because a whole system's digits are missing.
    """
    if not discarded_groups:
        return [], None, 0, []
    warnings = []
    staff_sized = 0
    staff_sized_pages = []
    for page_no, anomalies in discarded_groups:
        counts = sorted(a.get("line_count", 0) for a in anomalies)
        on_this_page = sum(1 for n in counts if n >= _STAFF_SIZED_GROUP)
        staff_sized += on_this_page
        if on_this_page:
            staff_sized_pages.append(page_no)
        warnings.append(
            f"page {page_no}: {len(anomalies)} group(s) of staff lines could not be read as a "
            f"staff and were ignored (line counts: {counts}) - a staff has 5 lines and a "
            "tablature staff 6, so any other count is a group this pass cannot interpret"
        )
    if not staff_sized:
        return warnings, None, 0, []
    warnings.append(
        f"{staff_sized} of the ignored group(s) had at least {_STAFF_SIZED_GROUP} lines, which "
        "is the size of a staff - if any of those was one, that whole system's bars and notes "
        "are MISSING from this transcription rather than wrong in it, and every count and "
        f"bar-conformance figure here describes only the systems that were read "
        f"(systems_unread={staff_sized}, on page(s) {_bar_list(staff_sized_pages)})"
    )
    return warnings, (
        "medium - read directly from vector text spans, but "
        f"{staff_sized} staff-sized group(s) of lines on this score could not be read as a "
        "staff and were skipped, so notes may be missing entirely rather than misread"
    ), staff_sized, staff_sized_pages


def _resolve_rhythm_source(page, std_staff, pair_reason, decoded):
    """Decide how one tab staff's durations will be read, and say why.

    `decoded` memoises decode_note_events per notation staff. The decoder's
    own honesty stats gate the answer: a staff whose music-font text is
    mostly outside the calibrated vocabulary, or whose unrecognised glyphs
    sit exactly where flags attach, is degraded or dropped to the spacing
    heuristic rather than reported as high-confidence rhythm.
    """
    if std_staff is None:
        return _RhythmSource(PROV_SPACING, detail=pair_reason or "no paired notation staff")

    key = id(std_staff)
    if key not in decoded:
        decoded[key] = glyph.decode_note_events(
            page, std_staff.top, std_staff.bottom, std_staff.x0, std_staff.x1,
            std_staff.line_ys, std_staff.spacing,
        )
    note_events, stats = decoded[key]

    font_warnings = stats.get("font_warnings") or []
    if not note_events:
        detail = font_warnings[0] if font_warnings else (
            "no readable music-font note glyphs on the paired notation staff"
        )
        return _RhythmSource(PROV_SPACING, detail=detail, stats=stats)

    ratio = stats.get("unknown_ratio", 0.0)
    unknown = stats.get("unknown_glyphs", 0)
    at_flag = stats.get("unknown_at_flag_position", 0)
    sample = stats.get("unknown_gid_or_name_sample") or []
    unknown_heads = stats.get("unknown_noteheads", 0)
    head_sample = stats.get("unknown_notehead_sample") or []

    if unknown_heads and ratio <= _UNKNOWN_RATIO_FALLBACK:
        # An unrecognised NOTEHEAD is reported however few of them there are,
        # because a ratio cannot see this one: on a sparse system two
        # unrecognised harmonics were 22% of the glyphs and degraded
        # correctly, and on a dense one the same two were 3% and reported
        # nothing - no warning, no defective bar, confidence "high" - while
        # the voice above them lost two eighths to an invented quarter rest
        # and their tab digits were attached to the voice below. Density is
        # not evidence about a notehead.
        return _RhythmSource(
            PROV_GLYPHS_DEGRADED, note_events, stats=stats,
            detail=(
                f"{unknown_heads} notehead(s) on the paired notation staff are outside "
                f"this decoder's calibrated vocabulary ({head_sample[:6]}) - each one's "
                "duration was inferred from what the bar had left over rather than read, "
                "and its tab digits may have been attached to another voice"
            ),
        )

    if ratio > _UNKNOWN_RATIO_FALLBACK:
        return _RhythmSource(
            PROV_SPACING, note_events, stats=stats,
            detail=(
                f"{unknown} of {stats.get('band_glyphs', 0)} music glyphs on the paired "
                f"notation staff are outside this decoder's calibrated vocabulary "
                f"({ratio:.0%}) - durations from it cannot be trusted "
                f"(unrecognised glyphs: {sample[:6]})"
            ),
        )
    if at_flag or ratio > _UNKNOWN_RATIO_WARN:
        why = (
            f"{at_flag} unrecognised glyph(s) sit where a flag attaches to a stem"
            if at_flag else
            f"{unknown} of {stats.get('band_glyphs', 0)} music glyphs are unrecognised ({ratio:.0%})"
        )
        return _RhythmSource(
            PROV_GLYPHS_DEGRADED, note_events, stats=stats,
            detail=(
                f"{why} - some durations on this system may be wrong "
                f"(unrecognised glyphs: {sample[:6]})"
            ),
        )
    undecided = stats.get("undecided_rests", 0)
    if undecided:
        # Maestro and Opus draw the half and the whole rest with one glyph, so
        # a rest whose position says neither (no staff lines to measure it
        # against, an outline that could not be read, a rest engraved where
        # neither reading fits - see glyph.half_or_whole_rest) was read as the
        # commoner of the two rather than measured. That is a twofold
        # difference in one rest's duration, which is the whole bar's
        # arithmetic, so it is said out loud rather than left in the stats.
        return _RhythmSource(
            PROV_GLYPHS_DEGRADED, note_events, stats=stats,
            detail=(
                f"{undecided} rest(s) on the paired notation staff are drawn with the one "
                "glyph that serves as both the half and the whole rest, and their position "
                "did not say which - each was read as a half rest, so a bar holding one may "
                "be short by two quarter notes of silence"
            ),
        )
    no_stem = stats.get("no_stem_noteheads", 0)
    if no_stem:
        # A filled notehead with no stem cannot have its flags or beams
        # counted, because both attach to the stem that was not found, so it
        # goes out at its unflagged floor: a quarter where the page may say
        # an eighth, a sixteenth or shorter. The floor is the LONGEST of the
        # candidate readings, so this always reads long and always takes the
        # bar's arithmetic with it, and the note has lost its voice signal
        # besides (see glyph.decode_note_events, no_stem_noteheads).
        #
        # Reported however few there are, and NOT ratio-gated, for the same
        # reason an unrecognised notehead above is not: how dense the staff
        # around it happens to be is not evidence about this note. Measured
        # over the library that degrades 507 of the 2771 notation staves that
        # supplied glyph durations at all, which is the honest size of the
        # problem rather than a threshold chosen to keep the count down.
        # (This figure was 493 of 2657 as of e2ddf37, the commit that added
        # this no-stem counter and moved the library's degraded-staff total
        # from 3 to 507; it has moved again since as the library changed.)
        return _RhythmSource(
            PROV_GLYPHS_DEGRADED, note_events, stats=stats,
            detail=(
                f"{no_stem} notehead(s) on the paired notation staff have no stem this "
                "decoder could find, so no flag or beam could be counted for them - each "
                "was given the longest duration its notehead alone allows (a quarter) "
                "rather than a read one, and reads long wherever the score wrote it "
                "shorter"
            ),
        )
    return _RhythmSource(PROV_GLYPHS, note_events, stats=stats)


_TUPLET_WARNING = (
    "tuplets (triplets and similar) are not detected - a note written inside a tuplet "
    "will show its plain written duration rather than the shortened tuplet duration"
)
_TIE_WARNING = (
    "a tie is written only where the curve joining its two notes was matched on one "
    "staff (issue #81) - a tie drawn across a system break is engraved as two partial "
    "curves with its notes on different staves, is not matched, and its second note is "
    "transcribed as a separate re-struck note rather than as one held note"
)


_DOT_FACTORS = (1.0, 1.5, 1.75)

# WHAT THE RHYTHM LABEL MEANS (issue #114). Stated here, stated in
# docs/musicxml-tab-profile.md's "What the rhythm confidence label means", and
# computed from these two constants in _rhythm_report - one rule, written once.
#
# The label is the WEAKER of two independent judgements about the same score:
#
#   PROVENANCE - how the durations were obtained. "high" for a score read
#   entirely from its own notehead/stem/flag/beam/dot glyphs, "medium" where a
#   staff was read from the engraving with something on it left unread,
#   "mixed" where some staff's durations came from the gaps between noteheads
#   instead of the noteheads, "low" where every staff did.
#
#   ARITHMETIC - what fraction of the score's bars are unreliable: they do not
#   add up to their meter, or nothing in them was read at all. "high" only
#   when that fraction is ZERO, "medium" while it stays under
#   _RHYTHM_LOW_RATIO, "low overall" at or above it.
#
# THE HIGH BAND IS ZERO, not a small number, and that is the whole point of
# issue #114. The gate used to be a single boundary at a quarter, so a score
# read "high - decoded directly from the ... engraving" could have up to one
# bar in four failing to add up: measured on the library at the time this was
# written, the weakest score carrying a "high" label had 16 of its 65 bars
# (24.6%) arithmetically impossible, and 25 of the 30 "high" scores had at
# least one such bar. A musician reading the headline word does not expect to
# hit a wrong bar every fourth measure, and the whole argument for this
# pipeline is that it says what it could not read. So "high" now means exactly
# one checkable thing - EVERY bar in this score adds up to its meter, and
# every bar holds something that was read - and the middle band carries the
# rest, with the count of unreliable bars appended to the string either way.
_RHYTHM_LOW_RATIO = 0.25
# The ladder the two judgements are compared on. "mixed" and "medium" are the
# same rung said two different ways (one names spacing-derived rhythm, the
# other an incompletely-read staff); a tie keeps the PROVENANCE word, because
# it is the one carrying the extra fact - a score whose rhythm came partly out
# of note spacing must keep saying so in its headline (issue #117).
_CONFIDENCE_RANK = {"low overall": 0, "low": 1, "mixed": 2, "medium": 2, "high": 3}


def _relabel(confidence, word, reason=None):
    """Re-head a confidence string with `word` if `word` is the weaker of the
    two, and append `reason` to whatever it ends up saying.

    Composes onto the clause that is already there rather than replacing it.
    Replacing it re-asserted the exact claim a degraded or spacing-derived
    score had just finished disclosing as NOT fully true - the headline flatly
    contradicting the sentence right below it.

    A head this ladder does not rank (a caller-supplied "n/a") is left alone
    rather than demoted: failing open here would treat an unranked word as
    the STRONGEST one on the ladder and demote it into nonsense - "n/a -
    caller supplied" relabelled to "medium - caller supplied" reads as a
    judgement this function never made. The reason, if any, is still
    appended, because the caller's fact (e.g. an unreadable printed meter)
    is true regardless of what the headline word is allowed to say.
    """
    head, _sep, rest = confidence.partition(" - ")
    head_rank = _CONFIDENCE_RANK.get(head)
    if head_rank is not None and _CONFIDENCE_RANK[word] < head_rank:
        head = word
    out = f"{head} - {rest}" if rest else head
    return f"{out}; {reason}" if reason else out

# How many bar numbers a warning names before summarising the rest. At 12 the
# cap bound 131 of the 271 affected scores in the library, so the prose lost the
# fact for half of them; 60 leaves 25 scores capped and is still one readable
# line. The count beside the list is always complete, and the full list is
# reported as data (ExtractionResult.padded_bars / unread_bars) for anything
# that wants all of it.
_BARS_LISTED = 60


def _bar_list(numbers) -> str:
    """Bar numbers for a warning: the first _BARS_LISTED of them, then how many
    were left out - never a silently truncated list."""
    listed = ", ".join(str(n) for n in numbers[:_BARS_LISTED])
    left = len(numbers) - _BARS_LISTED
    return f"{listed} and {left} more" if left > 0 else listed


def _quarters_text(quarters) -> str:
    """A quarter-note count as an exact decimal.

    Every duration this extractor emits is a multiple of a 32nd, so a quarter
    count is exact and has to print that way. A %.4g format silently rounded
    43.875 to "43.88" and 104.25 to "104.2" - a wrong number, in a sentence
    whose entire purpose is to say how much of a score was invented.
    """
    return f"{quarters:.5f}".rstrip("0").rstrip(".")


def _beat_quarters(code, dots) -> float:
    if not code:
        return 0.0
    return (4.0 / code) * _DOT_FACTORS[min(dots, len(_DOT_FACTORS) - 1)]


def _voices_of(beats):
    """A measure's beats as a list of voices. A measure is normally a list of
    voices already; a plain flat list of beats is accepted as the one-voice
    case, which is what the spacing-heuristic path and callers with no
    polyphony to model hand over."""
    if not beats:
        return []
    return list(beats) if isinstance(beats[0], list) else [beats]


class _TieReport(NamedTuple):
    """What _resolve_ties did, as data.

    `written` is how many complete ties the emitted score holds. `unpaired`
    counts tie ENDS, not ties: a start with no stop after it and a stop no
    start reached are each one, because each is a separate mark the decoder
    made and could not spend, and there is no way to tell which two of them
    were meant to be one tie. `bars` is where they were, deduplicated."""
    written: int
    unpaired: int
    bars: list[int]


def _resolve_ties(measures) -> _TieReport:
    """Close every tie the decoder opened, or drop it, IN PLACE.

    glyph._mark_ties can only say "a curve joins this notehead to the next one
    at the same pitch". A written tie needs both ends - MusicXML spells it as
    a `start` on one note and a `stop` on another, and a renderer that finds a
    start with no stop either draws a mark going nowhere or, in the renderer
    this project embeds, keeps it pending for the rest of the part and lets
    some distant note of the same pitch close it. So the second end is found
    here, over the WHOLE part, because a tie's commonest use is exactly the
    one _build_measure_beats_glyph cannot see: holding a note across a barline.

    THE PARTNER IS THE VERY NEXT NOTE IN THE SAME VOICE that the decoder
    flagged as the OTHER END of a matched curve. Both ends come from
    glyph._mark_ties, so this is not a search for a plausible partner - it is
    the note the tie was measured to reach - and the adjacency test is what
    keeps a tie from spanning something the tie's own curve does not. A voice
    is followed across measures because the emitted `<voice>` numbers are what
    a consumer reads, and voice 1 of one measure and voice 1 of the next are
    one voice to it.

    THE HELD NOTE TAKES THE STRUCK NOTE'S STRING AND FRET, always, whatever
    the tab-matching pass gave it. That is not a repair of a defect elsewhere;
    it is what a tie IS. The second note of a tie is not plucked, so the
    engraving prints no fret number under it - measured on "Close in the
    Distance (FF XIV Endwalker)" bar 6, where the tab draws `0` under the
    struck sixteenth and nothing at all under the half note it is held into -
    and the rank match, which hands out whatever digits are near an onset,
    therefore gave that half note a digit belonging to a different string
    entirely (fret 0 on string 5, two octaves below the note actually
    written). MusicXML requires the two notes of a tie to carry the same
    pitch, alphaTab's importer matches them by pitch and then overwrites the
    destination's fret from the origin anyway, and the page means one sounding
    note - so the only defensible value is the one that was struck.

    SILENCE THE PRODUCER INVENTED IS STEPPED OVER; silence the page prints is
    not. A bar that came up short of its meter is padded with inferred rests
    (Rule 14, _pad_voice_to_budget) which are written as `<forward>` and were
    never on the page - a note tied across a barline into the next bar still
    has its partner immediately after it as far as the engraving is concerned,
    and the padding sits between them only because something else in that bar
    was read short. A PRINTED rest between two notes means they are not
    adjacent, and no tie is written.

    A HALF-TIE IS ERASED, at either end: an unpaired start, and a stop no
    start reached. Neither is written, so the beats the emitters read cannot
    describe half a tie, and `unpaired` counts what was erased with the bars
    it happened in - the commonest cause being a tie drawn across a SYSTEM
    break, where the engraving splits it into two partial curves whose notes
    sit on different staves and glyph._mark_ties matches neither half.

    A beat is REPLACED rather than edited: a beat's notes list can be the very
    list a tab column holds (the placeholder and spacing-heuristic paths hand
    `col["notes"]` straight through), so writing into one would reach back into
    the column grid this has no business touching.
    """
    written = 0
    unpaired = 0
    bars = []
    closed = set()

    class _Slot:
        """One sounding beat of one voice: where it lives, and its notes."""
        __slots__ = ("bar", "voice", "index", "notes")

        def __init__(self, bar, voice, index):
            self.bar = bar
            self.voice = voice
            self.index = index
            self.notes = voice[index][2]

        def replace(self, note_index, note):
            notes = list(self.notes)
            notes[note_index] = note
            code, dots, _old = self.voice[self.index]
            self.voice[self.index] = (code, dots, notes)
            self.notes = notes

        def remark(self, note_index, **marks):
            self.replace(note_index, mxl.mark_note(self.notes[note_index], **marks))

    per_voice = collections.defaultdict(list)
    for index, measure_in in enumerate(measures):
        # (beats, meter) is what _extract builds; a bare beats list is what a
        # caller with no per-measure meter hands over, and _voices_of below
        # accepts either a list of voices or a flat list of beats.
        beats_in = (measure_in[0] if isinstance(measure_in, tuple) and len(measure_in) == 2
                    else measure_in)
        for voice_number, voice in enumerate(_voices_of(beats_in), start=1):
            for beat_index, (_code, _dots, notes) in enumerate(voice):
                # Silence the producer deduced is not a barrier between two
                # notes the page draws a tie between; a printed rest is.
                if not notes and mxl.is_inferred_rest(notes):
                    continue
                per_voice[voice_number].append(_Slot(index + 1, voice, beat_index))

    for slots in per_voice.values():
        for position, slot in enumerate(slots):
            starts = [i for i, n in enumerate(slot.notes)
                      if mxl.note_tie_start(n)]
            following = slots[position + 1] if position + 1 < len(slots) else None
            for note_index in starts:
                note = slot.notes[note_index]
                partner = None
                if following is not None:
                    for i, candidate in enumerate(following.notes):
                        if (id(following), i) in closed:
                            continue
                        if mxl.note_tie_stop(candidate):
                            partner = i
                            break
                if partner is None:
                    unpaired += 1
                    bars.append(slot.bar)
                    slot.remark(note_index, tie_start=False)
                    continue
                string, fret = note
                # Rebuilt rather than remarked, because the value being
                # replaced is the whole point - but the note's OWN tie start
                # survives it: the middle link of a chain of ties is both a
                # destination and an origin, and dropping the start here
                # closed only the first link of every chain.
                following.replace(partner, mxl.MarkedNote(
                    string, fret, tie_stop=True,
                    tie_start=mxl.note_tie_start(following.notes[partner]),
                    harmonic=mxl.note_harmonic(note)))
                closed.add((id(following), partner))
                written += 1

    # Anything still claiming to be the end of a tie no start reached is half
    # a tie, and is erased for the same reason an unpaired start is.
    for slots in per_voice.values():
        for slot in slots:
            for i, note in enumerate(slot.notes):
                if mxl.note_tie_stop(note) and (id(slot), i) not in closed:
                    unpaired += 1
                    bars.append(slot.bar)
                    slot.remark(i, tie_stop=False)
    return _TieReport(written, unpaired, sorted(set(bars)))


def _voice_quarters(beats, count_inferred=True) -> list[float]:
    """Each voice's total length in quarter notes, in voice order.

    `count_inferred=False` leaves out rests this extractor deduced from the
    meter rather than read from the page (musicxml.is_inferred_rest), which
    gives what the bar was actually READ as. That is the quantity
    _bar_conformance measures: with the padding counted, a voice filled out
    from its meter is exactly as long as its meter by construction, so no
    padded bar could ever be reported short.
    """
    return [sum(_beat_quarters(code, dots) for code, dots, notes in v
                if count_inferred or not mxl.is_inferred_rest(notes))
            for v in _voices_of(beats)]


def _bar_quarters(beats) -> float:
    """The longest voice in this bar, in quarter notes. Voices sound
    CONCURRENTLY, so a bar is as long as its longest voice - not the sum of
    them, which is exactly the mistake that made a bar of two-voice writing
    read as double its meter.

    This is how long the bar PLAYS, so inferred silence counts: it occupies
    time. Not what to check conformance with - see _bar_conformance, which
    measures what was read instead.
    """
    return max(_voice_quarters(beats), default=0.0)


class _BarConformance(NamedTuple):
    """How many bars fail the MusicXML profile's Rule 8, and how.

    `defective` counts a bar once whichever way it is wrong - a polyphonic bar
    can have one voice over its meter and another under it, so overfull + short
    would count that bar twice and can exceed `counted`. Rule 8 treats both
    directions as defective, so `defective` is what the reported confidence is
    derived from.
    """

    overfull: int
    short: int
    defective: int
    counted: int
    # How many bars hold silence that was deduced from the meter rather than
    # read from the page, and which bars those are (1-based, in the same
    # numbering the emitted measures use).
    #
    # A padded bar with a meter is ALWAYS also a short one, not merely usually:
    # the padding only fires for a voice under its budget by more than the
    # tolerance, and the sums below measure exactly that pre-padding total, so
    # min(voices) is under budget by construction. `padded` can still exceed
    # `short` in principle, because a bar with no meter is padded but not
    # measured - which no score reaches today, since the meter falls back to
    # 4/4.
    padded: int = 0
    padded_bars: tuple[int, ...] = ()
    # And how much of it there is, in quarter notes, across the whole score.
    # Read off the marked beats rather than accumulated as the padding happens,
    # so the figure reported and the silence emitted cannot disagree.
    inferred_quarters: float = 0.0
    # Which bars are the defective ones. Needed because the confidence
    # downgrade is over defective bars UNION bars nothing was read from (see
    # _rhythm_report), and a bar can be both - adding the two counts would
    # double-count it and could put the ratio over 1.
    defective_bars: tuple[int, ...] = ()


def _bar_conformance(measures) -> _BarConformance:
    """Count bars whose voices do not add up, in each direction.

    This is exactly the MusicXML profile's Rule 8 - every sounding voice's
    durations sum to the measure's duration - measured on the beats model
    rather than on emitted XML, so the numbers are available whichever format
    is being written. A bar is counted once however many of its voices are
    wrong: it is the bar that plays wrong. Undetected tuplets, a flag the
    decoder missed, and two voices the stems did not separate all land in the
    overfull count, so this stays measured separately from how the durations
    were obtained - every individual reading can be right while the bar as a
    whole is not.

    OVERFULL uses the longest voice and SHORT the shortest, which between them
    is the same thing as "some voice differs from the meter". Nothing here
    corrects anything: a bar that does not add up is emitted as it was read,
    counted, and reported.

    MEASURED ON WHAT WAS READ, not on what is emitted. Rests the extractor
    deduced from the meter to fill a voice out (_pad_voice_to_budget) are left
    out of the sums, because counting them makes this incapable of ever
    reporting a padded bar short: the padding fills a voice to exactly its
    meter by construction, so `min(voices) < budget` could not fire and a bar
    that lost sixty notes reported as perfectly conformant. Leaving them out is
    the same measurement as running this before the padding, and it is also
    what a consumer computes from the emitted MusicXML, where inferred silence
    is a `<forward>` rather than a rest and so is not part of Rule 8's sum
    either.
    """
    overfull = 0
    short = 0
    counted = 0
    padded_bars = []
    defective_bars = []
    inferred_quarters = 0.0
    for index, (beats, ts) in enumerate(measures, start=1):
        # The padding accounting comes FIRST, before the meterless-bar skip: a
        # bar with no meter is not measured against one, but if it holds
        # inferred silence it still carries a `<forward>` into the file, and a
        # count of padded bars that the file disagrees with is exactly what this
        # whole mechanism exists to prevent.
        bar_inferred = sum(_beat_quarters(code, dots)
                           for v in _voices_of(beats) for code, dots, notes in v
                           if mxl.is_inferred_rest(notes))
        if bar_inferred:
            padded_bars.append(index)
            inferred_quarters += bar_inferred
        if not ts:
            continue
        counted += 1
        budget = _measure_quarter_length(ts)
        voices = _voice_quarters(beats, count_inferred=False)
        if not voices:
            continue
        over = max(voices) > budget + 1e-6
        under = min(voices) < budget - 1e-6
        overfull += over
        short += under
        if over or under:
            defective_bars.append(index)
    return _BarConformance(overfull, short, len(defective_bars), counted,
                           len(padded_bars), tuple(padded_bars),
                           inferred_quarters, tuple(defective_bars))


def _overfull_bars(measures) -> tuple[int, int]:
    """Bars holding MORE than their time signature allows, and how many bars
    were checked - see _bar_conformance."""
    bars = _bar_conformance(measures)
    return bars.overfull, bars.counted


def _rhythm_report(counts, details, conformance=None, unread_bars=(),
                   prov_bars=None, no_stem_notes=0, no_stem_staves=0,
                   dots_unassigned=0, dots_unassigned_no_candidate=0,
                   dots_unassigned_eliminated=0, dots_unassigned_staves=0,
                   coincident_unsplit_pairs=0, coincident_unsplit_staves=0,
                   unison_digits_shared=0):
    """Derive the document's rhythm warnings and confidence string from the
    collected per-staff provenances - the single place that decides both, so
    they cannot drift out of step with each other or with the measure loop.

    `counts` is a Counter over the PROV_* values; `details` maps each
    provenance to the distinct per-staff explanations collected for it.
    `conformance` is a _BarConformance, or None where no bars were measured.
    `unread_bars` is the numbers of bars nothing was read from at all - see the
    warning below for why they are reported here rather than as Rule 8 defects.

    `prov_bars` maps a PROV_* value to the numbers of the bars the staves that
    resolved to it produced. A count of staves says HOW MUCH of a score's
    rhythm was not read from its glyphs; only the bar numbers say WHICH music
    that was, which is what lets somebody check those bars against the PDF -
    the same reason the padded-bars warning names its bars rather than only
    counting them. Empty, or absent, means no bar numbers were collected and
    the prose says nothing it cannot support.

    `no_stem_notes` / `no_stem_staves` are how many filled noteheads across
    how many notation staves came out of the decode with no stem, and so with
    their duration floored at a quarter rather than read - see
    glyph.decode_note_events.

    `dots_unassigned` / `dots_unassigned_staves` are how many augmentation-dot
    glyphs across how many notation staves bound to no note - see
    glyph._assign_dots. Such a dot affects nothing in the emitted score (it
    is simply not counted), but is worth saying out loud: it means either an
    engraving convention this decoder does not model, or a note the rest of
    the decode also missed. `dots_unassigned` is their sum;
    `dots_unassigned_no_candidate` / `dots_unassigned_eliminated` split it by
    WHY, since the two are not the same claim about the page - see
    glyph._assign_dots's docstring.

    `coincident_unsplit_pairs` / `coincident_unsplit_staves` are how many
    coincident duplicate notehead pairs - the same glyph drawn twice at the
    identical position, which is how a unison shared by two voices is
    engraved (issue #116) - had only ONE candidate stem between them, so
    nothing could tell the two copies apart and both stayed bound to it. See
    glyph.decode_note_events.

    `unison_digits_shared` is how many noteheads were given the fret number
    the tablature printed for their coincident twin's position rather than
    one printed for them (issue #137) - the right reading of a unison shared
    between two voices, and still an inference about which string those
    notes are on. See _share_unison_digits.
    """
    conformance = conformance or _BarConformance(0, 0, 0, 0)
    prov_bars = prov_bars or {}
    overfull, short = conformance.overfull, conformance.short
    defective, bars = conformance.defective, conformance.counted
    padded, padded_bars = conformance.padded, conformance.padded_bars
    inferred_quarters = conformance.inferred_quarters
    glyphs = counts.get(PROV_GLYPHS, 0)
    degraded = counts.get(PROV_GLYPHS_DEGRADED, 0)
    spacing = counts.get(PROV_SPACING, 0)
    spacing_bars = sorted(prov_bars.get(PROV_SPACING) or ())
    degraded_bars = sorted(prov_bars.get(PROV_GLYPHS_DEGRADED) or ())
    warnings = []

    if not glyphs and not degraded:
        warnings.append(
            "note durations are inferred from horizontal spacing between columns, not decoded from "
            "the score - treat as low confidence (no dotted notes or ties modeled)"
        )
        confidence = "low - inferred from note spacing only, no dotted notes or ties modeled"
    else:
        if spacing:
            # WHICH bars those systems produced, not just how many systems
            # there were. "treat those sections as low confidence" was
            # unanswerable without them: a reader was told that some fraction
            # of the score's durations came out of the gaps between noteheads
            # rather than the noteheads themselves, and given no way to find
            # out which fraction. Spacing-derived rhythm is only as good as
            # the engraver's spacing being proportional, which a justified or
            # hand-adjusted system is not, so these are the bars to check
            # first.
            where = (f" The bars they produced are: {_bar_list(spacing_bars)}."
                     if spacing_bars else "")
            warnings.append(
                f"durations were read from the engraved notation for {glyphs + degraded} staff "
                f"system(s); {spacing} staff system(s) could not be read that way and use a "
                "rougher estimate from note spacing instead - treat those sections as low "
                f"confidence.{where}"
            )
        if degraded:
            # Named the same way, and for the same reason. A degraded staff was
            # read from the engraving, but something on it was not: an
            # uncalibrated glyph, a rest whose value its position did not
            # settle, or a notehead whose stem was never found. The
            # per-staff reason is appended below as "rhythm source: ..."; this
            # says how much of the score it applies to and where.
            where = (f" The bars they produced are: {_bar_list(degraded_bars)}."
                     if degraded_bars else "")
            warnings.append(
                f"{degraded} staff system(s) were read from the engraved notation but not "
                "everything on them could be read - a music-font glyph this decoder has not "
                "been calibrated for, a notehead with no stem this decoder could find, or a "
                "rest whose printed position did not say which value it was - so treat their "
                f"durations as medium confidence.{where}"
            )
        warnings.append(_TUPLET_WARNING)
        warnings.append(_TIE_WARNING)
        if spacing:
            confidence = (
                "mixed - decoded from the score's engraving where a standard-notation staff was "
                "paired with the tab staff; a low-confidence spacing estimate elsewhere"
            )
        elif degraded:
            confidence = (
                "medium - decoded from the score's engraving, but not all of it was read: some "
                "staves carry a music-font glyph outside the decoder's calibrated vocabulary, a "
                "notehead whose stem it could not find, or a rest whose printed position did not "
                "say which value it was"
            )
        else:
            confidence = (
                "high - decoded directly from the notehead/stem/flag/beam/dot glyphs in the score's "
                "own engraving"
            )

    # Noteheads whose duration is a FLOOR rather than a reading. Stated as its
    # own sentence, with its own count, because the provenance sentence above
    # counts staves and this is the only place the number of affected NOTES is
    # said. A count of degraded staves cannot be turned back into it: one
    # stemless notehead on a staff and forty of them read identically there.
    if no_stem_notes:
        # This count is honest for the GATE - the stem was not found, so both
        # the duration and the voice are a guess either way - but not for a
        # claim about what got emitted. A stemless head is folded into any
        # host stem within onset tolerance by the absorb pass in
        # `_stem_groups`, and most of them ARE attached that way: of the
        # heads this counts, most inherit a duration from that host rather
        # than going out at the plain-quarter floor. Only say what is true of
        # every one of them.
        warnings.append(
            f"{no_stem_notes} notehead(s) across {no_stem_staves} staff system(s) were read with "
            "no stem this decoder could find. A note's flags and beams hang off its stem, so for "
            "those notes both the duration and which of a bar's voices they belong to rest on a "
            "guess rather than a reading: where such a head could not be attached to a "
            "neighbouring stem, it was emitted at the plain quarter, the LONGEST duration its "
            "notehead on its own allows"
        )

    # A dot that bound to nothing, stated with its own count for the same
    # reason no_stem_notes is: a count of affected staves cannot say whether
    # one score has one stray dot or forty. The two are not the same claim
    # about the page, so the sentence says both rather than one thing true of
    # only most of them - see glyph._assign_dots.
    if dots_unassigned:
        if dots_unassigned_eliminated:
            reason = (
                f"{dots_unassigned_no_candidate} had no notehead or rest at the position an "
                f"engraver would have placed one next to; {dots_unassigned_eliminated} reached "
                "one, but it had already been given a dot at a different position and could "
                "not take a second"
            )
        else:
            reason = "none had a notehead or rest at the position an engraver would have placed one next to"
        warnings.append(
            f"{dots_unassigned} augmentation dot(s) across {dots_unassigned_staves} staff "
            f"system(s) could not be bound to a note - {reason}. Each was left unattached "
            "rather than bound to the nearest notehead anyway, so no note's duration was "
            "invented from it - but a note nearby may be missing a dot it should have"
        )

    # A coincident duplicate notehead pair (issue #116) that could not be
    # told apart: only one candidate stem was found for the pair, so both
    # copies stayed bound to it rather than one going to each voice. Stated
    # with its own count for the same reason dots_unassigned is - a count of
    # affected staves alone cannot say whether a score has one such pair or
    # a dozen.
    if coincident_unsplit_pairs:
        warnings.append(
            f"{coincident_unsplit_pairs} coincident duplicate notehead pair(s) across "
            f"{coincident_unsplit_staves} staff system(s) - the same notehead glyph drawn "
            "twice at the identical position, which is how a unison shared by two voices is "
            "engraved - had only one candidate stem near them, so nothing here could tell the "
            "two copies apart: both stayed bound to that one stem rather than one going to "
            "each voice, and the other voice's note at that position may be missing"
        )

    # A fret number READ FOR ANOTHER NOTEHEAD and given to this one (issue
    # #137). It is the right reading of a unison - one string, two voices -
    # but it is still a number the tab did not print for the notehead that
    # got it, so it is said out loud rather than folded into the note count
    # silently. Same reasoning as the two warnings above.
    if unison_digits_shared:
        warnings.append(
            f"{unison_digits_shared} note(s) were given the fret number printed for a "
            "coincident notehead at the same position rather than one printed for them - a "
            "unison shared between two voices is one plucked string, so the tablature names "
            "it once and the second voice's note has no number of its own. Only done inside "
            "a chord whose every other position the tablature did name, but the string and "
            "fret for those notes are inferred from the twin rather than read for them"
        )

    # Bars that don't add up outrank how the durations were obtained: a
    # confident reading of every notehead still produces a wrong bar when its
    # voices don't come apart, and playback follows the bar.
    if bars and overfull:
        warnings.append(
            f"{overfull} of {bars} bar(s) hold more than their time signature allows. Music "
            "written in two voices (a melody over a separate bass line) is separated into "
            "concurrent voices where the stems say so, but a bar whose voices the stems do not "
            "separate is still flattened into one, and an undetected tuplet or a missed flag "
            "lands here too - the notes and their individual durations can still be right while "
            "the bar as a whole is not, so playback timing will drift in those bars"
        )
    if bars and short:
        warnings.append(
            f"{short} of {bars} bar(s) hold less than their time signature allows - a note whose "
            "duration was read short, or one dropped for want of a fret number, leaves the bar "
            "with less music in it than the meter says it holds. Where such a bar has more than "
            "one voice the missing part is filled with silence deduced from the meter, at the "
            "front of the voice if that is where it entered late, so the voices still play in time "
            "with each other - that silence is not counted here and is not written as a rest. "
            "Either way the emitted MusicXML falls short by the same amount, so any MusicXML tool "
            "will report those bars too"
        )
    # Which bars were padded, not just how much silence was added across the
    # score. A total says a score is partly invented; the bar numbers say WHERE,
    # which is what lets somebody check those bars against the PDF.
    if padded:
        warnings.append(
            f"{padded} of {bars} bar(s) contain silence that was deduced from the time signature "
            "rather than read from a rest printed in the score, "
            f"{_quarters_text(inferred_quarters)} quarter note(s) of it in total. The bars are: "
            f"{_bar_list(padded_bars)}. A voice with a note missing from it is filled out the same "
            "way a genuinely resting voice is, so that the voices of the bar still play in time "
            "with each other; the inferred silence is NOT counted towards those bars adding up, and "
            "is written into the MusicXML as <forward> rather than as a rest so no consumer "
            "mistakes it for one the engraver printed"
        )
    # A bar nothing was read from is reported SEPARATELY from the Rule 8 counts,
    # and deliberately so. It holds a whole bar of rests that do add up to its
    # meter, so it conforms to Rule 8 in the emitted file, and folding it into
    # `defective` would make the figures reported here disagree with what any
    # consumer computes from the file - the one property the padding fix exists
    # to establish. But it is not a reading either: a 40-bar score read as
    # nothing at all was reporting 40 of 40 bars conformant at high confidence
    # with no warning mentioning emptiness. So it is counted, named, and folded
    # into the confidence downgrade below instead.
    if unread_bars:
        warnings.append(
            f"{len(unread_bars)} of {bars} bar(s) hold nothing that was read from the score - no "
            "fret number and no rest glyph fell inside them - and are emitted as a whole bar of "
            f"rests so the bar numbering still matches the source. The bars are: "
            f"{_bar_list(unread_bars)}. Those bars add up to their time signature and so pass every "
            "arithmetic check, but nothing in them was read: they are not evidence that the score "
            "was transcribed, and a bar that is genuinely silent in the source cannot be told from "
            "one whose contents were missed"
        )
    # The downgrade is driven by bars that fail Rule 8 in EITHER direction, plus
    # bars nothing was read from. A score whose bars are mostly SHORT because
    # notes were dropped is just as unplayable as one whose bars overflow, and
    # one whose bars are mostly EMPTY was not read at all; reporting "high -
    # decoded directly from the ... engraving" over either is exactly the kind
    # of confident-but-wrong claim the rest of this module exists to avoid.
    #
    # A union, not a sum: a bar can be defective and unread at once (a meter
    # whose rests cannot be spelled within the beat limit), and adding the two
    # counts would double-count it and could put the ratio over 1. `defective`
    # stays the term that carries it, with only the overlap subtracted off the
    # unread ones, so the downgrade is still driven by the same count it always
    # was and does not depend on the per-bar list being populated.
    unreliable = defective + len(set(unread_bars) - set(conformance.defective_bars))
    reason = f"{defective} of {bars} bar(s) do not add up to their time signature"
    if unread_bars:
        reason = (
            f"{unreliable} of {bars} bar(s) either do not add up to their time signature "
            f"({defective}) or hold nothing that was read from the score ({len(unread_bars)})"
        )
    if bars:
        # The ARITHMETIC judgement, on the three bands _RHYTHM_LOW_RATIO and
        # the zero-defect rule above define. It is composed onto the
        # PROVENANCE judgement `confidence` already carries by _relabel, which
        # takes the weaker of the two words and keeps the clause.
        #
        # The middle band exists because a binary gate threw away everything
        # the counts above establish, in BOTH directions: below it a score
        # said "high - decoded directly from the ... engraving" with a quarter
        # of its bars impossible, and the qualifying sentence bolted onto the
        # end could not undo the headline word. The reason is still appended
        # whichever band it lands in, so the number is never only in the label.
        if unreliable / bars >= _RHYTHM_LOW_RATIO:
            confidence = _relabel(confidence, "low overall", reason)
        elif unreliable:
            confidence = _relabel(confidence, "medium", reason)
        # A genuinely clean score is left exactly as its provenance wrote it,
        # and says nothing extra: the qualifier has to mean something, and one
        # that appeared on every score would not.

    # Surface the concrete per-staff reasons behind any downgrade, capped so
    # a long score can't turn the warning list into a wall of text.
    for prov in (PROV_SPACING, PROV_GLYPHS_DEGRADED):
        seen = details.get(prov) or []
        for detail in seen[:3]:
            warnings.append(f"rhythm source: {detail}")
        if len(seen) > 3:
            warnings.append(f"rhythm source: and {len(seen) - 3} further distinct reason(s)")
    return warnings, confidence


# ---------------------------------------------------------------------------
# alphaTex emission
# ---------------------------------------------------------------------------


def _fmt_note(note, extra=()):
    """One note as alphaTex `fret.string`, with any effects it carries.

    `extra` is effects the BEAT contributes (the augmentation dot), folded
    into the same brace group: alphaTex takes one `{...}` list per note and
    two consecutive groups are not a thing it parses.
    """
    string, fret = note
    effects = list(extra)
    if mxl.note_tie_stop(note):
        # `t` is alphaTex's tie-destination property, and it is the spelling
        # to use rather than the `-` note value the format also accepts: `-`
        # replaces the fret number, so a reader of the stored transcription -
        # this format exists to be hand-edited - loses which string and fret
        # the held note is on. `t` keeps both and says the note is held.
        # alphaTab's own exporter writes `t` for the same reason.
        #
        # Only the DESTINATION is spelled: alphaTex has no tie-origin token,
        # because the destination naming itself is the whole statement.
        effects.append("t")
    body = f"{fret}.{string}"
    return f"{body}{{{' '.join(effects)}}}" if effects else body


def _fmt_beat(duration_code, dots, notes):
    # An empty notes list marks a rest beat - alphaTex spells one as the
    # bare identifier "r". A dotted duration is NOT valid alphaTex as a
    # trailing dot on the duration code (":8." / ":8.." fail to parse) - it
    # is a beat effect appended to the note/chord body instead
    # (":8 3.4{d}", or with a chord ":2 (3.4 5.3){d}").
    #
    # An INFERRED rest comes out as a plain `r` here, unmarked. alphaTex has no
    # editorial mechanism to mark one with - it is a compact format for
    # describing music, not for annotating provenance - and inventing a
    # non-standard token would break the importer that has to read this back.
    # MusicXML is the canonical output and does mark it (Rule 14); this format
    # exists for the transcription editor to work in, and the padded bars are
    # named in the warnings for a reader of either.
    #
    # A HARMONIC IS NOT WRITTEN HERE, deliberately, and this is not the same
    # decision as the inferred rest above. alphaTex has a perfectly good
    # harmonic vocabulary - `{nh}`, `{ah n}`, `{ph n}` and the rest - but none
    # of those tokens is an annotation: each one names WHICH harmonic, and the
    # renderer then sounds the note at the pitch that implies (a `{nh}` on a
    # 12th-fret note plays an octave above the fretted pitch, and one on a
    # 7th-fret note a twelfth above it). What this extractor reads off the page
    # is that a note is a harmonic, not which kind - the diamond notehead and
    # the bracketed fret number say the same thing for a natural and an
    # artificial one - so writing any of those tokens would re-pitch a note on
    # a guess. MusicXML can say exactly what was read, because `<harmonic>`
    # takes an empty body, and it does (Rule 19).
    dot_effect = "{d}" if dots == 1 else "{dd}" if dots == 2 else ""
    if not notes:
        return f":{duration_code} r{dot_effect}"
    if len(notes) == 1:
        # One note: the beat's dot joins that note's own effect group, since
        # alphaTex parses a single `{...}` list after a note and not two.
        return f":{duration_code} {_fmt_note(notes[0], _DOT_EFFECTS.get(dots, ()))}"
    body = "(" + " ".join(_fmt_note(n) for n in notes) + ")"
    return f":{duration_code} {body}{dot_effect}"


# The augmentation dot as alphaTex effect tokens, for folding into a single
# note's own effect group - see _fmt_note.
_DOT_EFFECTS = {1: ("d",), 2: ("dd",)}


def _escape_tex_string(s: str) -> str:
    """Escape a value for embedding inside an alphaTex quoted string
    (backslash then double-quote, in that order so a literal backslash
    isn't doubled by the quote-escaping pass)."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _build_alphatex(title, tempo, tuning, ts, measures):
    """measures: list of (beats, measure_ts) where beats is a list of VOICES,
    each voice a list of (duration_code, dots, notes). A flat list of beats
    is accepted as the one-voice case. measure_ts may be None (meaning
    "whatever is already in effect"); where it differs from the meter
    currently in effect, a `\\ts` is emitted on that bar, so a score that
    changes meter part-way through is transcribed with the change in it
    rather than having every later bar measured against the opening meter.

    A plain list of beats per measure is also accepted, for callers that
    have no per-measure meter to carry.

    Concurrent voices are separated by `\\voice` INSIDE the bar, which needs
    `\\voicemode barwise` in the header - alphaTex's default (staffwise) reads
    `\\voice` as "start the whole staff again for the next voice" and would
    take everything after the first one as bar 1 onwards of voice 2. Verified
    against the installed renderer: barwise gives one bar per line with the
    intended voices in it, and `|` closes the bar and returns to voice 1.
    """
    lines = [f'\\title "{_escape_tex_string(title)}"']
    if tempo:
        lines.append(f"\\tempo {tempo}")
    if ts:
        lines.append(f"\\ts {ts[0]} {ts[1]}")
    if any(len(_voices_of(m[0] if isinstance(m, tuple) and len(m) == 2 else m)) > 1
           for m in measures):
        lines.append("\\voicemode barwise")
    if tuning:
        # alphaTex binds the FIRST \tuning entry to string 1, and
        # _Staff.string_for_y assigns string 1 to the top tab line (the
        # highest-pitched string). `tuning`/DEFAULT_TUNING/DROP_D_TUNING are
        # kept low-to-high (index 0 = lowest string) everywhere else in this
        # module and in the API response, so they must be reversed here -
        # emitting them as-is puts every note on its mirrored string.
        lines.append("\\tuning " + " ".join(reversed(tuning)))
    lines.append(".")
    body_lines = []
    in_effect = tuple(ts) if ts else None
    for measure in measures:
        if measure and isinstance(measure, tuple) and len(measure) == 2:
            beats_in, measure_ts = measure
        else:
            beats_in, measure_ts = measure, None
        prefix = ""
        if measure_ts and tuple(measure_ts) != in_effect:
            in_effect = tuple(measure_ts)
            prefix = f"\\ts {in_effect[0]} {in_effect[1]} "
        voices = " \\voice ".join(
            " ".join(_fmt_beat(dur, dots, notes) for dur, dots, notes in voice)
            for voice in _voices_of(beats_in)
        )
        body_lines.append(prefix + voices + " |")
    lines.append("\n".join(body_lines))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _page_is_raster(page) -> bool:
    return not page.get_fonts(full=True) and not page.get_drawings() and not page.get_text("text").strip()


def analyze(pdf_path) -> dict:
    """Cheap triage: is this PDF vector or raster, how many tab/notation
    staves does it have, is tab extraction worth attempting. Never raises -
    a malformed PDF comes back as extractable: false with a reason."""
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return {
            "extractable": False,
            "reason": f"could not open pdf: {exc}",
            "vector": False,
            "tab_staff_count": 0,
            "standard_staff_count": 0,
            "page_count": 0,
        }
    try:
        if doc.page_count == 0:
            return {
                "extractable": False,
                "reason": "pdf has no pages",
                "vector": False,
                "tab_staff_count": 0,
                "standard_staff_count": 0,
                "page_count": 0,
            }
        vector_pages = 0
        tab_total = 0
        std_total = 0
        for page in doc:
            if _page_is_raster(page):
                continue
            vector_pages += 1
            staves, _ = _detect_staves(page)
            tab_total += sum(1 for s in staves if s.kind == "tab")
            std_total += sum(1 for s in staves if s.kind == "standard")
        if vector_pages == 0:
            return {
                "extractable": False,
                "reason": "no fonts, no vector drawings, no text on any page - pdf is a raster scan",
                "vector": False,
                "tab_staff_count": 0,
                "standard_staff_count": 0,
                "page_count": doc.page_count,
            }
        extractable = tab_total > 0
        reason = None
        if not extractable:
            reason = (
                "no 6-line tab staff groups found - pages are vector but appear to be "
                "standard-notation only (fingering numbers are not fret numbers)"
            )
        return {
            "extractable": extractable,
            "reason": reason,
            "vector": True,
            "tab_staff_count": tab_total,
            "standard_staff_count": std_total,
            "page_count": doc.page_count,
        }
    except Exception as exc:
        return {
            "extractable": False,
            "reason": f"analysis failed: {exc}",
            "vector": False,
            "tab_staff_count": 0,
            "standard_staff_count": 0,
            "page_count": 0,
        }
    finally:
        doc.close()


def extract(pdf_path, time_signature: tuple[int, int] | None = None) -> ExtractionResult:
    """Full extraction: returns alphaTex plus bars/beats/notes, tuning,
    per-section confidence, and an explicit list of warnings. Never raises -
    a malformed PDF or one with no tab staves comes back as
    extractable: false with a reason, not an exception.

    time_signature lets a caller supply the numerator/denominator by hand,
    since auto-detection frequently fails (see module docstring).
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return ExtractionResult(extractable=False, reason=f"could not open pdf: {exc}")
    try:
        return _extract(doc, pdf_path, time_signature)
    except Exception as exc:
        return ExtractionResult(extractable=False, reason=f"extraction failed: {exc}")
    finally:
        doc.close()


def _ts_at(timeline, page_idx, y, x):
    """The time signature in effect at (page, vertical position, horizontal
    position), from the timeline. Engravers print a meter once, at the point
    it changes, so the value in effect is the last one printed at or before
    this position - not the first one found in the document.

    The horizontal position matters as much as the vertical one: a meter
    change engraved part-way along a system governs the bars after it and not
    the ones before it, and asking this about the system's position instead
    of the bar's applied it to the whole system (issue #104).

    PRECONDITION: `timeline` must already be in (page, y, x) order - the same
    order _build_time_signature_timeline emits it in. Sorted here defensively
    rather than trusted, since silently reading an unsorted timeline in
    document order does not raise, it just answers wrong: the loop below
    stops at the first entry it finds "later" than the query, so one
    out-of-order entry hides everything genuinely at-or-before the query that
    comes after it in the list.

    A second, sharper hazard sits in the lexicographic comparison itself even
    on a correctly-sorted timeline: `y` is expected to equal EXACTLY the top
    of the system the query's bar belongs to - the same value the matching
    timeline entries were recorded with - because comparing 3-tuples degrades
    to comparing `entry_y` against `y` ALONE whenever they differ, deciding
    the whole comparison without ever looking at `x`. A `y` that is even a
    hair GREATER than the true system top (the `anchor_y` fallback in
    `_extract` uses a tab staff's own top when it has no paired notation
    staff, which is not guaranteed to equal any recorded system's y) makes
    every entry in that system compare as "at or before" regardless of `x`,
    silently returning that system's LAST meter for a bar that asked about
    its first. `y` a hair SMALLER fails safe instead, refusing that whole
    system's entries and falling back to whatever caller-provided default
    the caller falls back to - wrong, but not silently wrong in the way that
    matters here.
    """
    best = None
    for entry_page, entry_y, entry_x, ts in sorted(timeline):
        if (entry_page, entry_y, entry_x) <= (page_idx, y, x):
            best = ts
        else:
            break
    return best


# The x recorded for a meter printed at a staff's own start: it governs every
# bar of that system, including the first, whose left boundary is measured off
# the TAB staff and can therefore land a hair left of the notation staff's.
_SYSTEM_START_X = float("-inf")


def _append_ts(timeline, entry):
    """Add one timeline entry unless it repeats the meter already in force -
    a meter reprinted, or simply still running, is not a change."""
    if timeline and timeline[-1][3] == entry[3]:
        return
    timeline.append(entry)


def _mid_system_meters(page, staff, vseg):
    """Meters engraved part-way along one notation staff, as (x, ts) in x
    order.

    A change is printed immediately after the barline it takes effect at, so
    only that space is read - see glyph.decode_meter_after_barline for why
    scanning a whole staff width instead invents meters out of tuplet
    numbers. The staff's own opening window is skipped because the meter
    there is read by decode_time_signature. `staff.x1` is passed through so
    that reader can tell a change at THIS barline from a courtesy signature
    for the system that follows, printed as the last thing on this one - see
    its own docstring for why the barline's distance from staff.x1 alone is
    not enough to tell the two apart once the reach is wide enough to see
    past a key change.

    The x recorded sits one staff space LEFT of the barline the meter was
    printed at. The bar boundaries this is later compared against are
    measured off the TAB staff's barlines rather than this staff's; the two
    are drawn to the same x but not to the same rounding, and a bar boundary
    landing a tenth of a point early would put the bar where the meter
    changes into the previous meter. One staff space is orders of magnitude
    below the width of a bar and orders above that jitter.
    """
    out = []
    unreadable = []
    opening = staff.x0 + staff.spacing * glyph.TS_LEAD_SPACINGS
    for bl in _detect_barlines(vseg, staff):
        bx = bl.x
        if bx <= opening or bx >= staff.x1 - staff.spacing:
            continue
        ts, why = glyph.decode_meter_after_barline(
            page, staff.top, staff.bottom, bx, staff.x1, staff.spacing)
        if ts is not None:
            out.append((bx - staff.spacing, ts))
        elif why and why.startswith(glyph.UNREADABLE_DIGIT_REASON):
            # A meter change refused because one of its own digits is a glyph
            # this decoder cannot name (issue #129). Reported rather than
            # folded into the ordinary "nothing is printed just after this
            # barline" silence: the two are different facts about the page,
            # and only this one means a meter WAS printed here and could not
            # be read.
            unreadable.append(why)
    return out, unreadable


def _build_time_signature_timeline(pages_with_tab):
    """Read every printed time signature on every page that has tab, in
    document order: the one at the start of each notation staff, and any
    engraved part-way along it at a barline.

    Returns (timeline, reasons, opening_read, unreadable) where timeline is a
    list of (page_index, staff_top, x, (num, den)) with consecutive duplicates
    collapsed, reasons is the decoder's own explanation for the staves where
    nothing was found - surfaced to the user instead of being dropped, so
    "assumed 4/4" can say what was actually looked at - opening_read says
    whether the meter printed at the score's FIRST notation staff was one of
    the ones read, and `unreadable` is the subset of those reasons that are a
    REFUSAL over a glyph the decoder could not name sitting among the meter's
    own digits (issue #129).

    `unreadable` is kept apart from `reasons` because the two are surfaced
    differently and have to be. `reasons` explains an assumed 4/4 and is only
    worth saying when nothing was read at all; a refused meter is worth saying
    however the score's meter was finally obtained, because it means a meter
    WAS printed at that staff and could not be read - and if some other staff
    supplied one, the reader would otherwise be shown a meter with no hint
    that a different one may be printed where this refusal happened.

    opening_read is what stops a meter read from a later system being
    backdated over the opening. A staff that decodes to nothing is skipped,
    so a score whose first system was not read used to take its opening meter
    from whichever staff answered first: a piece in 4/4 that changes to 3/4
    part-way through recorded only the 3/4, called it the opening meter at
    full confidence, and - having recorded exactly one meter - never fired the
    "changes time signature part-way through" warning either (issue #90).

    `first_staff` names the first notation staff among `pages_with_tab` -
    which is already filtered to pages that carry a TAB staff, not the
    score's pages in general. A score whose actual opening page is notation
    only (a foreword, a single-staff intro before the tabbed arrangement
    begins) is not in `pages_with_tab` at all, so "first" here means the
    first notation staff on the first page that ALSO has tablature, not the
    first notation staff in the document. Where the two differ, a meter
    printed only on that earlier, tab-less page is never read as the
    opening one - it is not read at all, since `pages_with_tab` never
    reaches it - and this reports "not detected (assumed 4/4)" rather than
    the meter that page actually shows.
    """
    timeline = []
    reasons = []
    unreadable = []
    opening_read = False
    first_staff = True
    for page_idx, page, _tab_staves, std_staves in pages_with_tab:
        # Computed once per page and reused across its staves - see
        # _detect_barlines. The page's drawings are memoised, so this is a
        # filter over an already-parsed content stream.
        vseg = _vertical_segments(page) if std_staves else []
        for s in std_staves:
            ts, reason = glyph.decode_time_signature(page, s.top, s.bottom, s.x0, s.spacing)
            if ts is None:
                reasons.append(reason)
                if reason and reason.startswith(glyph.UNREADABLE_DIGIT_REASON):
                    unreadable.append(reason)
            else:
                opening_read = opening_read or first_staff
                _append_ts(timeline, (page_idx, s.top, _SYSTEM_START_X, ts))
            first_staff = False
            mid, mid_unreadable = _mid_system_meters(page, s, vseg)
            unreadable.extend(mid_unreadable)
            for x, mid_ts in mid:
                _append_ts(timeline, (page_idx, s.top, x, mid_ts))
    return timeline, reasons, opening_read, unreadable


def _detect_key_signature(pages_with_tab) -> tuple[int, str, str | None]:
    """The score's key signature as a MusicXML `fifths` count, plus how it was
    obtained and, where it was not, the decoder's reason.

    Read as ONE document-level value from the first notation staff that yields
    one, rather than as a timeline the way the meter is. The key is used for
    nothing but choosing between enharmonic spellings of the same sounding
    pitch, so a key change part-way through a score costs a handful of
    oddly-spelled accidentals in the later sections and no wrong notes at all
    - not worth carrying a second timeline for. Where nothing can be read, 0
    is used, which is what MusicXML means by "no key signature".
    """
    reason = None
    for _page_idx, page, _tab_staves, std_staves in pages_with_tab:
        for s in std_staves:
            fifths, why = glyph.decode_key_signature(page, s.top, s.bottom, s.x0, s.spacing)
            if fifths is not None:
                return fifths, "glyph-decoded", None
            if reason is None:
                reason = why
    return 0, "not detected (assumed no key signature)", reason


def _extract(doc, pdf_path, time_signature: tuple[int, int] | None) -> ExtractionResult:
    if doc.page_count == 0:
        return ExtractionResult(extractable=False, reason="pdf has no pages")

    warnings: list[str] = []
    override = tuple(time_signature) if time_signature else None
    if override and not glyph.time_signature_is_valid(override):
        warnings.append(
            f"the supplied time signature {override[0]}/{override[1]} is not a usable meter - "
            "ignored in favour of detection"
        )
        override = None
    ts = override
    ts_source = "manual override" if ts else None
    tempo = None
    tuning = list(DEFAULT_TUNING)
    tuning_label = None
    tuning_unread: list[str] = []
    # tab_staff_count / standard_staff_count are the total number of tab /
    # standard staff systems found across the whole document (summed across
    # pages), matching analyze()'s definition - see ExtractionResult.
    tab_count = 0
    std_count = 0
    vector_pages = 0
    pages_with_tab = []  # (page_index, page, tab_staves, std_staves) in page order
    discarded_groups = []  # (page number, [anomaly, ...]) - see _discard_report

    # Pass 1: staff census plus tempo/tuning hints. Deliberately does NO
    # glyph work: time-signature decoding costs a font parse plus a
    # get_texttrace() walk per page, and a notation-only document (no tab
    # staves anywhere) used to pay all of it on every page before returning
    # extractable: false. The census tells us which pages are even
    # candidates first - see the time-signature pass below.
    for page_no in range(doc.page_count):
        page = doc[page_no]
        text = page.get_text("text")
        if not page.get_fonts(full=True) and not glyph.page_drawings(page) and not text.strip():
            warnings.append(f"page {page_no + 1} has no fonts, drawings, or text (raster scan) - skipped")
            continue
        vector_pages += 1

        staves, anomalies = _detect_staves(page)
        if anomalies:
            discarded_groups.append((page_no + 1, anomalies))
        # Reading order, NOT top order: a coda system printed to the right of
        # the last full system shares its band and can be ruled a shade
        # HIGHER, so ordering by `top` would emit its bars first (issue
        # #152). On a page with one system per band the two orders agree.
        tab_staves = sorted((s for s in staves if s.kind == "tab"),
                            key=lambda s: s.reading_order)
        std_staves = sorted((s for s in staves if s.kind == "standard"),
                            key=lambda s: s.reading_order)
        tab_count += len(tab_staves)
        std_count += len(std_staves)

        if tempo is None:
            q_match = re.search(r"=\s*(\d{2,3})", text)
            if q_match:
                tempo = int(q_match.group(1))

        if tuning_label is None and "Drop D" in text:
            tuning_label = "Drop D"
            tuning = list(DROP_D_TUNING)

        # Recognised and NOT applied - see unread_tuning_instructions. Collected
        # whether or not a tuning name was found, because the two are
        # independent: a score can name Drop D and then say to tune the lot down
        # a half step, and it is the combination that makes `tuning` wrong while
        # looking most like something that was read.
        for instruction in unread_tuning_instructions(text):
            if instruction not in tuning_unread:
                tuning_unread.append(instruction)

        if tab_staves:
            pages_with_tab.append((page_no, page, tab_staves, std_staves))

    # Reported here rather than beside the confidence dict so that a refusal
    # carries it too: a page whose staff-sized line groups were all discarded
    # is refused for "no tab staff found", which is true and yet says nothing
    # about the six lines that were thrown away to make it true.
    (discard_warnings, discard_note,
     systems_unread, systems_unread_pages) = _discard_report(discarded_groups)
    warnings.extend(discard_warnings)

    if vector_pages == 0:
        return ExtractionResult(
            extractable=False,
            reason="no fonts, no vector drawings, no text on any page - pdf is a raster scan",
        )

    if not pages_with_tab:
        return ExtractionResult(
            extractable=False,
            reason=(
                "no 6-line tab staff groups found - pages are vector but appear to be "
                "standard-notation only (fingering numbers are not fret numbers)"
            ),
            tab_staff_count=tab_count,
            standard_staff_count=std_count,
            warnings=warnings,
            # A refusal carries the count too: "no tab staff found" is true
            # and says nothing about the staff-sized groups that were thrown
            # away to make it true (issue #152).
            systems_unread=systems_unread,
            systems_unread_pages=systems_unread_pages,
        )

    # Time signature, now that we know there is tab worth extracting. Read at
    # every position one is printed - the start of each notation system and
    # each barline part-way along one - so a meter CHANGE is seen and each bar
    # can be measured against the meter in force where THAT bar is, instead of
    # taking whichever staff answered first and measuring every other bar in
    # the score against it (the library demonstrably contains pieces that
    # change meter mid-score - e.g. 4/4 -> 12/16 -> 4/4 - and 41 of them print
    # a change part-way along a system rather than at its start).
    ts_timeline = []
    ts_reasons = []
    ts_unreadable = []
    ts_opening_read = False
    if override is None:
        ts_timeline, ts_reasons, ts_opening_read, ts_unreadable = (
            _build_time_signature_timeline(pages_with_tab))
        if ts_timeline and ts_opening_read:
            # The opening meter is the one printed at the score's own first
            # notation staff, and ONLY that one. Indexing the first entry
            # whatever staff it came from is the "whichever staff answered
            # first" this timeline exists to avoid - see
            # _build_time_signature_timeline.
            ts = ts_timeline[0][3]
            ts_source = "glyph-decoded"
        else:
            for page_idx, page, _t, std_staves in pages_with_tab:
                if not std_staves:
                    continue
                detected = _detect_time_signature(page, std_staves[0])
                if detected and glyph.time_signature_is_valid(detected):
                    ts = detected
                    ts_source = "auto-detected"
                    break

    if ts is None:
        ts = (4, 4)
        ts_source = "not detected (assumed 4/4)"
        warnings.append(
            "time signature not detected - glyphs live in a subsetted music font at remapped "
            "codepoints; assumed 4/4 for bar/beat grouping, pass time_signature to override"
        )
        # The decoder's own account of what it looked at, rather than only a
        # generic "assumed 4/4".
        for reason in list(dict.fromkeys(ts_reasons))[:3]:
            warnings.append(f"time signature: {reason}")

    # A meter that was PRINTED and REFUSED, said out loud whatever source the
    # score's meter finally came from (issue #129). This is the disclosure the
    # refusal exists for: before it, an unrecognised glyph among the digits
    # was dropped and the rest assembled, so a Finale 10/8 whose '0' is
    # unmapped read as a confident (1, 8) with nothing anywhere saying a glyph
    # had been thrown away. Refusing without saying so would only move the
    # silence, and this must not be conditional on `ts is None` the way
    # `ts_reasons` above is - a second staff supplying a meter does not make
    # the refused one readable.
    meter_digits_unreadable = len(ts_unreadable)
    if meter_digits_unreadable:
        warnings.append(
            f"{meter_digits_unreadable} printed time signature(s) could not be read because a "
            "glyph this decoder has no category for sits among their digits - a music font "
            "subset whose glyph for one digit is not in the calibrated tables. Each was "
            "refused outright rather than assembled from the digits that WERE recognised, "
            "which would have produced a confident meter missing a digit; the bars they "
            "govern are barred by whatever meter is reported instead. The decoder's own "
            "account: " + "; ".join(list(dict.fromkeys(ts_unreadable))[:3])
        )

    if ts_timeline and not ts_opening_read:
        # Said out loud, because this is the shape that used to be silent AND
        # confident: the opening meter unread, a later one read, and that
        # later one reported as the meter of the whole score. Suppressed
        # when the later meter and the assumed opening happen to agree -
        # "read as 4/4, assumed 4/4" is not a discrepancy worth surfacing,
        # and `ts_source` is never interpolated into the sentence: it can
        # itself be the string "not detected (assumed 4/4)", which nested
        # inside prose about being "barred as 4/4 (not detected (assumed
        # 4/4))" reads as a token dump rather than an explanation.
        later = ts_timeline[0][3]
        if later != ts:
            warnings.append(
                f"the meter printed at the start of this score was not read, but a {later[0]}/"
                f"{later[1]} printed further into it was - the bars before that point are "
                f"barred as {ts[0]}/{ts[1]} rather than measured against a meter read from a "
                "later part of the score"
            )

    if tuning_unread:
        # Said in the warnings as well as carried as data, because it is a
        # caveat about the transcription and a reader who has only the API
        # should not have to infer it from a tuning array that looks complete.
        warnings.append(
            "the score prints a tuning instruction that was not applied ("
            + ", ".join(tuning_unread)
            + ") - the fret numbers are transcribed as written and the tuning is recorded as "
            "the strings are named, so the sounding pitches will be wrong by whatever that "
            "instruction asks for"
        )

    # The key signature, for enharmonic spelling only - see
    # _detect_key_signature and musicxml.spell_pitch.
    key_fifths, key_source, key_reason = _detect_key_signature(pages_with_tab)
    if key_reason:
        warnings.append(f"key signature: {key_reason} - notes are spelled as if there were none")

    # Every meter the transcription is actually barred in, in order, opening
    # meter first. Derived from `ts` rather than from the timeline alone
    # because the opening may be an assumption (see ts_opening_read): a score
    # assumed to open in 4/4 that then prints a 3/4 DOES change meter part-way
    # through, and the timeline holding one entry is exactly the reason that
    # went unsaid.
    meters_in_force = []
    for meter in [ts] + [entry[3] for entry in ts_timeline]:
        if not meters_in_force or meters_in_force[-1] != meter:
            meters_in_force.append(meter)

    if len(meters_in_force) > 1:
        changes = ", ".join(f"{n}/{d}" for n, d in meters_in_force[:6])
        warnings.append(
            f"this score changes time signature part-way through ({changes}) - the change is "
            "carried into the transcription, but bar grouping around a meter change is lower "
            "confidence than a single-meter score"
        )

    # Pass 2: real extraction, now that ts/tempo/tuning are settled.
    all_measures = []  # list of (beats, measure_ts)
    unmatched_total = 0
    rejected_merges_total = 0
    suspicious_frets_total = 0
    # Which rhythm source each tab staff resolved to. The document's rhythm
    # warnings and confidence are derived from these counts in ONE place
    # (see _rhythm_report) rather than from a branch here.
    prov_counts = collections.Counter()
    prov_details = collections.defaultdict(list)
    # WHICH bars each provenance produced, keyed by PROV_* value. A count of
    # staves says how much of a score's rhythm was not read from its glyphs;
    # only these say which music that was, and a bar number is the one
    # coordinate a reader can carry back to the PDF. See _rhythm_report.
    prov_bars = collections.defaultdict(list)
    # Filled noteheads that came out of the decode with no stem, and how many
    # notation staves carried at least one. Summed over the DECODES rather than
    # over the tab staves so a notation staff read by two tab staves is not
    # counted twice - the memo in `decoded` is per page, so the same set of
    # noteheads is only ever visited once here.
    no_stem_notes = 0
    no_stem_staves = 0
    no_stem_seen = set()
    # Augmentation-dot glyphs that bound to no note - a genuine anomaly (see
    # glyph.decode_note_events, dots_unassigned), summed the same way and
    # over the same de-duplicated decodes as no_stem_notes above.
    # `dots_unassigned_total` is the total; the two counts beside it split it
    # by WHY (see glyph._assign_dots) - `dots_unassigned_staves` is not
    # split, since it answers "how many staves had at least one anomaly of
    # either kind", true regardless of which kind.
    dots_unassigned_total = 0
    dots_unassigned_no_candidate_total = 0
    dots_unassigned_eliminated_total = 0
    dots_unassigned_staves = 0
    # Coincident duplicate notehead pairs (see glyph.decode_note_events,
    # coincident_unsplit_pairs) where only one candidate stem was found, so
    # both copies stayed bound to it rather than being told apart - summed
    # the same de-duplicated way as no_stem_notes/dots_unassigned_total
    # above.
    coincident_unsplit_total = 0
    coincident_unsplit_staves = 0
    unmatched_columns_glyph = 0
    unmatched_glyph_notes_total = 0
    # Noteheads given the fret number the tab printed for their coincident
    # twin's position rather than one of their own (see
    # _share_unison_digits, issue #137). Summed over MEASURES rather than
    # over decodes, unlike the counters above: a shared digit is a decision
    # taken while building one bar's beats, and the same notehead can only
    # ever be built into one bar.
    unison_digits_shared_total = 0
    # How many bars were transcribed as concurrent voices. How much of the
    # silence in them was deduced from the meter is counted off the emitted
    # beats instead (see _bar_conformance), so "the voices add up" can be told
    # apart from "the voices were padded until they added up".
    multivoice_bars = 0
    # Bars nothing was read from at all - no fret column and no rest glyph fell
    # inside them - emitted as a whole bar of rests so the bar numbering still
    # matches the source. Tracked here because it cannot be recovered from the
    # beats afterwards: the bar of rests it emits is indistinguishable from a
    # bar of rests that WAS read, and there is exactly one of those in the
    # library. See the _rhythm_report warning for why they are reported outside
    # the Rule 8 counts.
    unread_bars = []
    font_warnings_seen = []
    # Per-measure repeat/ending structure, keyed by document-level measure
    # number - see _add_form_mark and musicxml.build's `barlines` parameter.
    # Form marks carry no duration and never touch `all_measures` - Rule 15's
    # invariant that reading them cannot move a single Rule 8 figure.
    form_marks = {}
    repeats_unread_bars = []
    endings_unread_bars = []
    endings_truncated_bars = []
    form_marks_unanchored_bars = []
    ending_numbers_seen = set()
    # Navigation marks, collected across every page and resolved into
    # <direction> records only once the whole score's bars exist - a "To
    # Coda" on page 1 names a coda sign that is usually on page 2, so
    # nothing here can be decided a staff or a page at a time (see
    # _resolve_nav_marks).
    nav_anchored = []
    nav_unanchored = 0
    # The marks that were read and could not be given a bar. Kept, not just
    # counted, because WHICH KIND was refused is what tells the unresolved
    # disclosure whether a missing coda is one the page never drew or one
    # this transcription could not place - see _resolve_nav_marks.
    nav_refused = []
    for page_idx, page, tab_staves, std_staves in pages_with_tab:
        tokens = _extract_digit_tokens(page)
        by_staff, unmatched = _assign_tokens_to_tab_staves(tokens, tab_staves)
        unmatched_total += len(unmatched)
        # Which of this page's fret numbers the engraving brackets as
        # harmonics (issue #63). Read once per page and applied per staff,
        # because the tolerance is in the staff's own line spacing.
        harmonic_marks = _harmonic_bracket_marks(page)
        for si, staff in enumerate(tab_staves):
            _mark_harmonic_digits(by_staff.get(si, []), staff, harmonic_marks)
        # Computed once per page and reused for every staff on it - see
        # _detect_barlines docstring.
        vseg = _vertical_segments(page)
        # Pair every tab staff on the page to a notation staff inside its
        # own system, exclusively, before decoding anything - see
        # _pair_standard_staves.
        pairs, pair_reasons = _pair_standard_staves(tab_staves + std_staves)
        # Navigation marks are placed against a SYSTEM, so they are read and
        # bucketed once for the whole page, before any staff is decoded -
        # unlike a volta bracket, which is searched for in a band above one
        # staff at a time. Doing it per staff would let one mark be claimed
        # by two staves, or by none (issue #134 phase 2).
        # Which tab staff's bars a mark landing on each staff of the page
        # belongs to. A system's NOTATION staff maps to the tab staff under
        # it (the ordinary case - a mark above the system), and every tab
        # staff maps to itself, which is not the same entry: a mark drawn
        # BETWEEN a system's two staves has the tab staff as the nearest
        # thing below it, and without the second entry those marks - 57 of
        # the 569 this extractor reads off the library - had no bar grid to
        # land on and were disclosed as unanchored when they were nothing of
        # the kind.
        tab_for_top = {}
        for tab in tab_staves:
            std = pairs.get(id(tab))
            if std is not None:
                tab_for_top[id(std)] = tab
            tab_for_top[id(tab)] = tab
        nav_marks, nav_unowned = _assign_nav_marks(
            _read_navigation_marks(page), tab_staves + std_staves, tab_for_top)
        nav_unanchored += len(nav_unowned)
        decoded = {}
        for si, staff in enumerate(tab_staves):
            toks = by_staff.get(si, [])
            if not toks:
                continue
            notes, rejected, suspicious = _merge_multidigit(toks, staff)
            rejected_merges_total += rejected
            suspicious_frets_total += suspicious
            columns = _group_into_columns(notes)
            barline_recs = _detect_barlines(vseg, staff, page)
            col_xs = [c["x"] for c in columns]
            lo, hi = min(col_xs) - 5, max(col_xs) + 5
            bars = [bl.x for bl in barline_recs if lo <= bl.x <= hi]
            bounds = sorted(set([staff.x0] + bars + [staff.x1]))

            measure_idx = 0
            measures_for_staff = [[] for _ in range(len(bounds) - 1)]
            for col in columns:
                while measure_idx < len(bounds) - 2 and col["x"] >= bounds[measure_idx + 1]:
                    measure_idx += 1
                measures_for_staff[measure_idx].append(col)

            std_staff = pairs.get(id(staff))
            source = _resolve_rhythm_source(
                page, std_staff, pair_reasons.get(id(staff)), decoded)
            prov_counts[source.provenance] += 1
            if source.detail and source.detail not in prov_details[source.provenance]:
                prov_details[source.provenance].append(source.detail)
            for fw in source.stats.get("font_warnings") or []:
                if fw not in font_warnings_seen:
                    font_warnings_seen.append(fw)
            if source.uses_glyphs:
                # Counted per NOTATION staff, and only where its decode is
                # what the durations were actually taken from: a staff that
                # fell all the way back to spacing threw its decode away, so
                # its stemless noteheads never reached the transcription and
                # reporting them would overstate what is wrong with it.
                seen_key = (page_idx, id(std_staff))
                if seen_key not in no_stem_seen:
                    no_stem_seen.add(seen_key)
                    staff_no_stem = source.stats.get("no_stem_noteheads", 0)
                    if staff_no_stem:
                        no_stem_notes += staff_no_stem
                        no_stem_staves += 1
                    staff_dots_unassigned = source.stats.get("dots_unassigned", 0)
                    if staff_dots_unassigned:
                        dots_unassigned_total += staff_dots_unassigned
                        dots_unassigned_no_candidate_total += source.stats.get(
                            "dots_unassigned_no_candidate", 0)
                        dots_unassigned_eliminated_total += source.stats.get(
                            "dots_unassigned_eliminated", 0)
                        dots_unassigned_staves += 1
                    staff_coincident_unsplit = source.stats.get("coincident_unsplit_pairs", 0)
                    if staff_coincident_unsplit:
                        coincident_unsplit_total += staff_coincident_unsplit
                        coincident_unsplit_staves += 1

            # Where to look the meter up: the notation staff this tab staff
            # reads from, or its own position when it reads none. The meter
            # itself is resolved per BAR, in the measure loop below, because a
            # change engraved part-way along a system governs the bars after
            # it and not the ones before it.
            anchor_y = std_staff.top if std_staff is not None else staff.top

            # Two-and-a-half staff-line-spacings is comfortably wider than
            # normal engraving jitter between a notehead and its tab digit,
            # but tight enough to actually reject a mismatch in a dense
            # passage.
            x_tol = staff.spacing * 2.5
            if source.uses_glyphs:
                # col["x"] is a fret digit's LEFT edge; glyph note events'
                # x is the notehead bbox CENTER - comparing them directly
                # is a systematic offset of about half a digit's width,
                # enough to pick the wrong neighbor in a dense passage.
                # Approximate each column's center with this staff's own
                # measured average digit width.
                avg_digit_w = (sum(t.width for t in toks) / len(toks)) if toks else 5.0
                for col in columns:
                    col["xc"] = col["x"] + avg_digit_w / 2

            # Where this staff's bars start in the document's numbering. Every
            # iteration of the loop below appends exactly one measure, in both
            # branches, so the staff owns the whole run from here to the end.
            staff_first_bar = len(all_measures) + 1
            for i, m_cols in enumerate(measures_for_staff):
                m_lo, m_hi = bounds[i], bounds[i + 1]
                # This bar's own meter, at this bar's own position. The budget
                # it sets decides what _bar_conformance measures the bar
                # against and how far _pad_voice_to_budget fills a short
                # voice, so a meter borrowed from elsewhere in the system
                # lands on both of those figures.
                bar_ts = _ts_at(ts_timeline, page_idx, anchor_y, m_lo) or ts
                measure_quarter_len = _measure_quarter_length(bar_ts)
                if source.uses_glyphs:
                    voices, unmatched_cols, unmatched_notes, shared_digits = (
                        _build_measure_beats_glyph(
                            m_cols, m_lo, m_hi, source.note_events, source.note_xs,
                            x_tol, std_staff.spacing, measure_quarter_len,
                        )
                    )
                    unmatched_columns_glyph += unmatched_cols
                    unmatched_glyph_notes_total += unmatched_notes
                    unison_digits_shared_total += shared_digits
                    if len(voices) > 1:
                        multivoice_bars += 1
                    if not voices:
                        # Nothing decoded and no tab columns either - still
                        # emit an explicit rest bar rather than dropping the
                        # measure (see the non-glyph branch below for why).
                        # NOT marked inferred, even though the meter is where
                        # it came from. A bar of nothing but rests is already
                        # conspicuous - it is the one shape a reader cannot
                        # mistake for a reading - whereas an inferred rest that
                        # emits `<forward>` in a voice with no notes at all
                        # leaves the measure with nothing for a Rule 8 check to
                        # sum, so its defect would be visible to this extractor
                        # and to nobody else. Measured on the library, 337 bars
                        # across 172 scores take this branch. Keeping the
                        # marker to silence that COMPLETES a voice that was
                        # read is what keeps every reported figure equal to
                        # what a consumer computes from the file. The bar is
                        # counted and named instead - see unread_bars.
                        voices = [_rest_beats_for(measure_quarter_len)]
                        unread_bars.append(len(all_measures) + 1)
                    all_measures.append((voices, bar_ts))
                    continue
                if not m_cols:
                    # No digit columns landed in this bar - emit an explicit
                    # rest bar instead of dropping it. Skipping it entirely
                    # would omit its "|" separator and shift every later
                    # bar's number one position earlier than the PDF, which
                    # breaks side-by-side comparison against the original.
                    # Rests that ADD UP to the meter, not one snapped to the
                    # nearest plain value: a 3/4 bar's 3.0 quarters snap to a
                    # whole rest, which is a third longer than the bar. Left
                    # unmarked for the same reason as the glyph branch above,
                    # and counted as unread for the same reason too.
                    unread_bars.append(len(all_measures) + 1)
                    all_measures.append((_rest_beats_for(measure_quarter_len), bar_ts))
                    continue
                durations_q = _infer_measure_rhythm(m_cols, measure_quarter_len, m_hi)
                beats = [(_snap_duration(dq), 0, col["notes"]) for col, dq in zip(m_cols, durations_q)]
                all_measures.append((beats, bar_ts))
            prov_bars[source.provenance].extend(
                range(staff_first_bar, len(all_measures) + 1))

            # Repeat barlines and volta brackets - read after this staff's
            # bars exist, so they anchor to document-level measure numbers
            # (issue #134 phase 1). Form marks carry no duration and never
            # touch `all_measures` above - see the `form_marks` comment.
            staff_repeats_unread, staff_unanchored = _apply_repeat_marks(
                barline_recs, bounds, lo, hi, staff_first_bar, form_marks, staff.spacing)
            repeats_unread_bars.extend(staff_repeats_unread)
            form_marks_unanchored_bars.extend(staff_unanchored)

            # A volta bracket is drawn above the SYSTEM's topmost staff -
            # never above the tab staff itself, since every scored volta in
            # the library also carries notation above its tab (issue #134
            # S2.4) - so a tab staff with no notation partner has nowhere a
            # bracket for it could be, and is skipped.
            if std_staff is not None:
                # The bracket's own geometry (height, hook length, number
                # window) is drawn relative to the NOTATION staff it sits
                # above, not the tab staff below it - the two staves' spacing
                # in points differ (measured ~4.98-5.12 vs ~7.44-7.7 in this
                # fixture alone), and using the tab staff's here rejected a
                # bracket only 3.28 std-staff-spaces tall as too short,
                # because 2.5 TAB-staff-spaces is a taller absolute distance.
                brackets, volta_hooks = _read_volta_brackets(
                    page, std_staff.top, std_staff.spacing,
                    std_staff.x0, std_staff.x1)
                endings, unread, volta_unanchored = _associate_voltas(
                    brackets, volta_hooks, barline_recs, bounds, lo, hi, staff_first_bar,
                    std_staff.spacing, staff.spacing)
                endings_unread_bars.extend(unread)
                form_marks_unanchored_bars.extend(volta_unanchored)
                for first_bar, last_bar, number, ending_type, truncated in endings:
                    ending_numbers_seen.add(number)
                    _add_form_mark(form_marks, first_bar, "left", ending_number=number,
                                    ending_type="start")
                    _add_form_mark(form_marks, last_bar, "right", ending_number=number,
                                    ending_type=ending_type)
                    if truncated:
                        endings_truncated_bars.append(first_bar)

            # Navigation marks bucketed to this staff at the top of the page
            # (issue #134 phase 2). Anchored here, resolved into <direction>
            # records once the whole document's bars exist.
            staff_nav, staff_nav_refused = _apply_nav_marks(
                nav_marks.pop(id(staff), ()), bounds, staff_first_bar,
                staff.spacing)
            nav_anchored.extend(staff_nav)
            nav_unanchored += len(staff_nav_refused)
            nav_refused.extend(staff_nav_refused)

        # Marks bucketed to a tab staff the loop above skipped whole - a
        # staff with no fret-number token on it has no bar grid at all, so
        # there is no bar for a mark on it to name. Disclosed, not dropped
        # silently.
        nav_unanchored += sum(len(rest) for rest in nav_marks.values())

    # Rhythm warnings and confidence: derived once, from what the staves
    # actually resolved to.
    conformance = _bar_conformance(all_measures)
    spacing_bars = sorted(prov_bars.get(PROV_SPACING, ()))
    degraded_bars = sorted(prov_bars.get(PROV_GLYPHS_DEGRADED, ()))
    rhythm_warnings, rhythm_confidence = _rhythm_report(
        prov_counts, prov_details, conformance, tuple(unread_bars),
        prov_bars=prov_bars, no_stem_notes=no_stem_notes,
        no_stem_staves=no_stem_staves,
        dots_unassigned=dots_unassigned_total,
        dots_unassigned_no_candidate=dots_unassigned_no_candidate_total,
        dots_unassigned_eliminated=dots_unassigned_eliminated_total,
        dots_unassigned_staves=dots_unassigned_staves,
        coincident_unsplit_pairs=coincident_unsplit_total,
        coincident_unsplit_staves=coincident_unsplit_staves,
        unison_digits_shared=unison_digits_shared_total,
    )
    warnings.extend(rhythm_warnings)
    # Font-level problems that weren't already the reason a staff degraded
    # (those are reported by _rhythm_report as "rhythm source: ..."): a page
    # can carry an unreadable music font without it being what decided any
    # staff's provenance.
    already = "\n".join(warnings)
    for fw in font_warnings_seen[:3]:
        if fw not in already:
            warnings.append(f"music font: {fw}")

    if multivoice_bars:
        warnings.append(
            f"{multivoice_bars} bar(s) were transcribed as two concurrent voices, split by the "
            "direction of the stems the score engraves them with"
        )
    if unmatched_columns_glyph:
        warnings.append(
            f"{unmatched_columns_glyph} fret number(s) could not be matched to a note in the "
            "engraved notation and got an estimated duration instead - treat those specific notes "
            "as low confidence"
        )
    if unmatched_glyph_notes_total:
        warnings.append(
            f"{unmatched_glyph_notes_total} note(s) read from the engraved notation had no "
            "matching fret number and were dropped from the tab"
        )
    if unmatched_total:
        warnings.append(
            f"{unmatched_total} digit token(s) near a tab staff could not be assigned to a string"
        )
    if rejected_merges_total:
        warnings.append(
            f"{rejected_merges_total} adjacent-digit merge(s) were rejected because they would "
            f"have produced a fret number above {_MAX_SANE_FRET} - kept as separate notes instead"
        )
    if suspicious_frets_total:
        warnings.append(
            f"{suspicious_frets_total} fret number(s) above {_MAX_SANE_FRET} were read directly "
            "from the PDF's own text (not from a merge) - likely two adjacent notes rendered as "
            "one text span in the source - treat those frets as low confidence"
        )
    # A fret that high can put a note past the top of MusicXML's octave range,
    # which no <pitch> element can express. Those are left out of the emitted
    # score (their beat keeps its place as a rest, so the bar still adds up)
    # and said so here rather than quietly written as some other pitch.
    unwritable = mxl.unrepresentable_notes(all_measures, tuning, fifths=key_fifths)
    if unwritable:
        warnings.append(
            f"{unwritable} note(s) sit outside the range a MusicXML pitch can express - an "
            "impossible fret number puts them there - and are emitted as a rest of the same "
            "length rather than as a note at some other pitch"
        )

    # Repeat/volta form marks that were read only partly - dropped rather
    # than written as a guess (issue #134 S5). None of these affect
    # `all_measures` or any Rule 8 figure; a form mark carries no duration.
    if repeats_unread_bars:
        warnings.append(
            f"{len(repeats_unread_bars)} repeat barline(s) had dots next to a barline group but "
            "the dots could not be resolved to a clean forward/backward direction, so no "
            f"<repeat> was written for them. The bars are: "
            f"{', '.join(str(n) for n in repeats_unread_bars[:_BARS_LISTED])}."
        )
    if endings_unread_bars:
        warnings.append(
            f"{len(endings_unread_bars)} volta bracket(s) had a left hook landing on a barline "
            "but no readable ending number nearby, so no <ending> was written for them. The bars "
            f"are: {', '.join(str(n) for n in endings_unread_bars[:_BARS_LISTED])}."
        )
    if endings_truncated_bars:
        warnings.append(
            f"{len(endings_truncated_bars)} volta ending(s) could not have their last bar "
            "established (no backward repeat closing them, and the bracket's drawn right end "
            "snaps to no boundary), so each was written over its first bar only. The bars are: "
            f"{', '.join(str(n) for n in endings_truncated_bars[:_BARS_LISTED])}."
        )
    if form_marks_unanchored_bars:
        warnings.append(
            f"{len(form_marks_unanchored_bars)} repeat or volta mark(s) had no bar boundary to "
            f"anchor to and were dropped. Nearest bars: "
            f"{', '.join(str(n) for n in form_marks_unanchored_bars[:_BARS_LISTED])}."
        )
    # Navigation marks, resolved once the whole document's bars exist: a
    # "To Coda" on page 1 names a coda sign that is usually on page 2.
    nav_directions, nav_unresolved_bars, nav_coda_was_refused = _resolve_nav_marks(
        nav_anchored, nav_refused)
    if nav_unresolved_bars:
        # Prose only, and it moves no count: where the coda this score is
        # missing was read off the page and then refused for having no bar
        # (nav_marks_unanchored), saying "no coda read" would be false and
        # would hide that the two disclosures are one defect.
        coda_clause = (
            "a To Coda or al Coda whose coda this score draws on a system this "
            "transcription does not hold (see the unanchored count below)"
            if nav_coda_was_refused else
            "a To Coda or al Coda with no coda read"
        )
        warnings.append(
            f"{len(nav_unresolved_bars)} bar(s) carry a navigation instruction naming a jump "
            f"this transcription holds no target for - a D.S. with no segno read, {coda_clause}, "
            "or an al Fine with no Fine - so each is written as the words the page prints, with "
            "no playback jump attached. The bars are: "
            f"{', '.join(str(n) for n in nav_unresolved_bars[:_BARS_LISTED])}."
        )
    if nav_unanchored:
        warnings.append(
            f"{nav_unanchored} navigation mark(s) were read off the page but sit against no bar "
            "this transcription holds - drawn outside the horizontal span of the staff below "
            "them (usually a coda engraved as its own short system, which the staff detector "
            "does not report), too far from any staff, or on a staff no fret numbers were read "
            "from - and were left out rather than moved onto a bar the page does not draw them "
            "over"
        )
    endings_incomplete = 0
    if ending_numbers_seen:
        try:
            seen_ints = sorted({int(n.split(",")[0]) for n in ending_numbers_seen})
        except ValueError:
            seen_ints = []
        if seen_ints and seen_ints != list(range(1, seen_ints[-1] + 1)):
            endings_incomplete = 1
            warnings.append(
                "the volta ending numbers read from this score do not form a run starting at 1 "
                f"({', '.join(str(n) for n in seen_ints)}) - written as read rather than guessed"
            )

    if not all_measures:
        return ExtractionResult(
            extractable=False,
            reason=(
                "tab staff systems were found but no fret-number digits could be matched to a "
                "string - likely an outlined-text export where fret numbers are vector paths, "
                "not selectable text"
            ),
            tab_staff_count=tab_count,
            standard_staff_count=std_count,
            pages_processed=len(pages_with_tab),
            warnings=warnings,
            systems_unread=systems_unread,
            systems_unread_pages=systems_unread_pages,
            # Carried even on the refusal path: a meter that was printed and
            # could not be read is a fact about the PAGE, and does not stop
            # being one because no fret digits were matched (issue #129).
            meter_digits_unreadable=meter_digits_unreadable,
        )

    # Close the ties the decoder opened, before either emitter reads the beats
    # (issue #81). A tie's second note is very often in the NEXT bar, so this
    # cannot happen while the bars are being built - and both emitters have to
    # see the same answer, so it happens once, here, on the model they share.
    tie_report = _resolve_ties(all_measures)
    if tie_report.unpaired:
        where = (f" Bars: {_bar_list(tie_report.bars)}."
                 if tie_report.bars else "")
        warnings.append(
            f"{tie_report.unpaired} end(s) of a tie were found in the engraving whose other "
            "end was not, so those ties are not written and their second note is transcribed "
            "as separately re-struck rather than held. A tie drawn across a system break is "
            "the usual cause: the engraving splits it into two partial curves whose notes sit "
            f"on different staves, and neither half finds its partner.{where}"
        )

    title = Path(pdf_path).stem
    alphatex = _build_alphatex(title, tempo, tuning, ts, all_measures)
    musicxml = mxl.build(title, tempo, tuning, ts, all_measures, fifths=key_fifths,
                          barlines=form_marks, directions=nav_directions)
    # Both of these are what the emitted score actually HOLDS, not what was
    # read off the page, and both need the emitter's own rule for it rather
    # than a second copy of it here.
    #
    # `beats` therefore comes from mxl.written_beats: a beat of silence deduced
    # from the meter is written as `<forward>` and is not a beat of the score,
    # so counting the beats model instead reported more beats than the
    # canonical output contains - 6386 more across the library, which is
    # exactly its number of `<forward>` elements.
    #
    # `notes` subtracts `unwritable`: those have no expressible pitch and are
    # left out (their beat stays, as a rest, so the beat count is unaffected).
    # Reporting the pre-emission figure made `notes` larger than the canonical
    # output it is reported beside.
    beats_total = mxl.written_beats(all_measures)
    notes_total = sum(len(notes) for beats, _ in all_measures
                      for v in _voices_of(beats) for _, _, notes in v) - unwritable

    ts_confidence = {
        "manual override": "n/a - caller supplied",
        "glyph-decoded": "high - read directly from the time-signature digit glyphs printed on the score",
        "auto-detected": "medium - read from page text",
    }.get(ts_source, "low - not detected, assumed 4/4")
    if len(meters_in_force) > 1 and ts_source == "glyph-decoded":
        ts_confidence = (
            "medium - read directly from the time-signature digit glyphs, but the score changes "
            "meter part-way through"
        )
    if meter_digits_unreadable:
        # The reported meter may be right and still not be the whole story: a
        # meter printed somewhere on this score was refused because one of its
        # digits is a glyph with no category (issue #129), so this score is
        # known to print at least one meter that was NOT read. Capped through
        # the same _relabel ladder the rhythm label uses, so the clause the
        # source earned survives underneath the weaker word.
        #
        # Cannot fire on a caller-supplied meter, whose label is "n/a - caller
        # supplied" and is on no rung of that ladder: an override skips the
        # timeline entirely, so `ts_unreadable` is empty and this is not
        # reached.
        ts_confidence = _relabel(
            ts_confidence, "medium",
            f"{meter_digits_unreadable} printed meter(s) on this score were refused because a "
            "glyph with no category sits among their digits, so a meter this score prints was "
            "not read",
        )

    fret_confidence = discard_note or (
        "high - read directly from vector text spans positioned against detected tab staff lines"
    )

    # Kept apart from `rhythm`: a dropped volta says nothing about whether
    # durations were read, and folding it into `rhythm` would make that
    # figure mean two different things (issue #134 Rule 15).
    structure_issues = (len(repeats_unread_bars) + len(endings_unread_bars)
                         + len(endings_truncated_bars) + len(form_marks_unanchored_bars)
                         + endings_incomplete
                         + len(nav_unresolved_bars) + nav_unanchored)
    # A lost SYSTEM outranks any of the above, and is stated first (issue
    # #152). The marks that were read may be complete and still describe a
    # form this transcription cannot play, because the bars a jump names are
    # on the system that was not read - so "high" would be a claim about the
    # structure of a score this file does not contain. It is also the one
    # case here where a bar-level figure cannot express the loss: an absent
    # system has no bar to attach a caveat to.
    if systems_unread:
        structure_confidence = (
            f"low - {systems_unread} system(s) on this score could not be read at all (page(s) "
            f"{_bar_list(systems_unread_pages)}), so their bars are missing from this "
            "transcription entirely and any repeat or navigation mark naming them has nothing "
            "to point at"
        )
    elif not form_marks and not nav_directions and not structure_issues:
        structure_confidence = (
            "n/a - no repeat barlines, volta brackets or navigation marks were found on this "
            "score"
        )
    elif structure_issues:
        structure_confidence = (
            f"medium - {structure_issues} repeat/volta/navigation mark(s) could not be read in "
            "full and were left out rather than guessed"
        )
    else:
        structure_confidence = (
            "high - repeat barlines, volta brackets and navigation marks read directly from the "
            "score's own engraving"
        )

    confidence = {
        "frets": fret_confidence,
        "rhythm": rhythm_confidence,
        "time_signature": ts_confidence,
        "structure": structure_confidence,
        # The key decides between enharmonic spellings of the same sounding
        # pitch and nothing else, so even a wrong reading here cannot make a
        # note wrong - only oddly written.
        "key_signature": (
            "high - read from the accidentals engraved between the clef and the meter; affects "
            "enharmonic spelling only"
            if key_source == "glyph-decoded" else
            "not detected - notes are spelled as if there were no key signature, which affects "
            "how accidentals are written and not which pitches sound"
        ),
    }

    return ExtractionResult(
        extractable=True,
        musicxml=musicxml,
        alphatex=alphatex,
        title=title,
        tempo=tempo,
        tuning=tuning,
        tuning_label=tuning_label,
        tuning_unread=tuning_unread,
        time_signature=ts,
        time_signature_source=ts_source,
        key_fifths=key_fifths,
        key_signature_source=key_source,
        bars=len(all_measures),
        beats=beats_total,
        notes=notes_total,
        bars_overfull=conformance.overfull,
        bars_short=conformance.short,
        bars_defective=conformance.defective,
        bars_measured=conformance.counted,
        bars_padded=conformance.padded,
        padded_bars=list(conformance.padded_bars),
        inferred_rest_quarters=conformance.inferred_quarters,
        bars_unread=len(unread_bars),
        unread_bars=list(unread_bars),
        tab_staff_count=tab_count,
        standard_staff_count=std_count,
        pages_processed=len(pages_with_tab),
        confidence=confidence,
        warnings=warnings,
        rhythm_provenance=dict(prov_counts),
        spacing_bars=spacing_bars,
        degraded_bars=degraded_bars,
        staves_spacing_rhythm=prov_counts.get(PROV_SPACING, 0),
        staves_degraded_rhythm=prov_counts.get(PROV_GLYPHS_DEGRADED, 0),
        meter_digits_unreadable=meter_digits_unreadable,
        notes_no_stem=no_stem_notes,
        staves_no_stem=no_stem_staves,
        dots_unassigned=dots_unassigned_total,
        dots_unassigned_no_candidate=dots_unassigned_no_candidate_total,
        dots_unassigned_eliminated=dots_unassigned_eliminated_total,
        staves_dots_unassigned=dots_unassigned_staves,
        coincident_unsplit_pairs=coincident_unsplit_total,
        staves_coincident_unsplit=coincident_unsplit_staves,
        unison_digits_shared=unison_digits_shared_total,
        repeats_unread=len(repeats_unread_bars),
        repeats_unread_bars=list(repeats_unread_bars),
        endings_unread=len(endings_unread_bars),
        endings_unread_bars=list(endings_unread_bars),
        endings_truncated=len(endings_truncated_bars),
        endings_truncated_bars=list(endings_truncated_bars),
        form_marks_unanchored=len(form_marks_unanchored_bars),
        form_marks_unanchored_bars=list(form_marks_unanchored_bars),
        endings_incomplete=endings_incomplete,
        systems_unread=systems_unread,
        systems_unread_pages=systems_unread_pages,
        nav_marks_unanchored=nav_unanchored,
        nav_marks_unresolved=len(nav_unresolved_bars),
        nav_marks_unresolved_bars=nav_unresolved_bars,
        tie_ends_unpaired=tie_report.unpaired,
        tie_ends_unpaired_bars=list(tie_report.bars),
    )
