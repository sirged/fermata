"""Shared fixtures for the transcription tests.

There are two sources of engraved PDFs here and they do different jobs.

ENGRAVED FIXTURES (fixtures/engraved) are committed, so they run everywhere
including CI. They are engraved from MusicXML in the repository by
server/tools/tab_extract/engrave_fixtures.py, which means their licence is
not in question, their content is known exactly, and what the extractor
reports can be compared against what was asked for. A missing engraved
fixture is a FAILURE, never a skip - the whole point is that these always
run.

REAL LIBRARY FIXTURES come from the maintainer's own sheet music (real
Finale/Sibelius output, exercising paths nothing generated here reaches: the
Maestro glyph-ID fingerprint, Opus's PUA names, two-voice fingerstyle
writing at scale). They cannot be committed, so point FERMATA_TEST_LIBRARY
at a library root to run them; they skip otherwise. That skip is now
counted and announced at the end of the run - see
pytest_terminal_summary - because a green run that quietly skipped a third
of the extraction suite is exactly how this gap went unnoticed.
"""

import os
from pathlib import Path

import pytest

ENGRAVED_DIR = Path(__file__).resolve().parent / "fixtures" / "engraved"

_FIXTURE_RELATIVE_PATHS = {
    "score_a": "Patreon/John Oeth/Final Fantasy/FF X/To Zanarkand (Final Fantasy X).pdf",
    "score_b": "Classical/Tarrega/Tarrega-Study-in-C-Guitar-Free.pdf",
    "score_c": "Favorites/ClairDeLuneGuitar.pdf",
    # Two-voice writing where a melody note shares a beat with a stem-down
    # chord: the figure notehead-to-stem attachment gets wrong, and an Opus
    # engraving, so it also exercises the side bearing that only that font has.
    "score_d": "Classical/PrimoGuitar Misc/Dalza-Recercar-Guitar-2019.pdf",
    # Issue #174 anacrusis cases. score_e opens on a two-eighth-note pickup and
    # is otherwise clean, so recognising the pickup takes it to zero defective
    # bars. score_f opens on a one-beat pickup but ALSO carries an
    # unrelated short bar (19), so recognising the pickup lifts bar 1 while its
    # demotion remains. score_g is the adversarial non-pickup: its first bar
    # is short in ONE voice while another fills it (a dropped note), and its
    # final bar IS a complete measure - so the "wholly short" guard has to keep
    # it defective even though the final-bar half of the pairing holds.
    "score_e": "Patreon/John Oeth/Super Mario/Sad Song (Super Mario RPG).pdf",
    "score_f": "Patreon/John Oeth/Chrono Trigger/Singing Mountain (Chrono Trigger).pdf",
    "score_g": "Patreon/John Oeth/Chrono Cross/Far Promise (Radical Dreamers).pdf",
    # Filled noteheads whose stems the vector pass never sees, on every one of
    # its notation staves. Nothing engraved in this repository reproduces that
    # - MuseScore draws every stem as a clean vector line, so all twelve
    # committed fixtures report zero - and it is the state that floors a
    # notehead's duration at a quarter, so the counter for it needs a real
    # score to be exercised on at all.
    "score_h": (
        "Patreon/John Oeth/Final Fantasy/FF X/Hymn of the Fayth (Final Fantasy X).pdf"),
    # Meter changes engraved part-way ALONG a system, over and over, on a
    # system compressed enough that a stem lands within reading distance of
    # the next meter's digits. Nothing engraved here reproduces that
    # crowding - see test_a_meter_further_along_a_bar_is_not_a_meter_at_this_barline.
    "score_i": "Patreon/John Oeth/Anime/Your Name/Theme of Mitsuha (Your Name.).pdf",
    # A meter change printed part-way along a system, behind a key change at
    # the SAME barline: three flats push the numerator's left edge to 6.18
    # staff spaces past the barline, past the flat reach a mid-system reader
    # sized only for "nothing between the barline and the meter" allows.
    # Nothing engraved here carries a key change at a mid-system barline at
    # all - see test_a_key_change_at_a_mid_system_barline_does_not_hide_the_meter.
    "score_j": "Patreon/John Oeth/Wild Arms/Into the Wilderness (Wild Arms).pdf",
    # A courtesy time signature - the key and meter for the NEXT system,
    # printed as the last thing on THIS one - behind four sharps, about 7
    # staff spaces past the system's own last barline. Widening the
    # mid-system window enough to read a key change at a barline (see
    # `score_j` above) also brings this within reach, and reading it there
    # would start the change a system early - see
    # test_a_courtesy_meter_at_the_end_of_a_system_is_not_applied_early.
    "score_k": "Patreon/John Oeth/NieR/Kaine Salvation (NieR).pdf",
    # A five-note chord whose dots the engraver pushed down a step, over a
    # notehead that already carries a dot of its own further up the same
    # column (issues #111/#112). Nothing engraved in this repository produces
    # a chord that deep, and the exemption that keeps such a head from
    # refuting the pushed-down reading has no other real score to be
    # exercised on - see glyph_rhythm._pushed_down_pairs.
    "score_l": "Patreon/John Oeth/New World/Storm_s Past (New World).pdf",
    # Issue #160: a chord whose members share ONE dot column, not the pushed-down
    # arrangement above. Five noteheads, five printed dots in a single column;
    # the undisplaced members sit a full notehead width too far from the column
    # for their own reach, so before #160 only the members within reach read - 2
    # of the 5. Two scores carry the shape; both are pinned. Referenced by
    # relative path, named here by the mechanism they exercise.
    "chord_shared_dot_column_a": ("Patreon/John Oeth/The Legend of Zelda/"
        "TLOZ Link_s Awakening/Inside a House (The Legend of Zelda Link_s Awakening).pdf"),
    "chord_shared_dot_column_b": ("Patreon/John Oeth/Final Fantasy/FF IX/"
        "Vamo alla Flamenco (Final Fantasy IX).pdf"),
    # Issue #160's other class: three members whose dots the engraver pushed into
    # an evenly spaced cascade - a displaced pair pushed down a step, then a
    # further member whose own slot the pair took pushed a step again - four dots
    # over members that are not evenly spaced. The pair model orphaned the last
    # dot (read 3 of 4). Page 3 (0-based page index 2) isolates it.
    "pushed_down_cascade": "Patreon/John Oeth/Suikoden/Reminiscence (Suikoden II).pdf",
    # Two of the four scores the #116 research had a guitarist check against
    # the printed page. score_m's flagged spot is a genuine unison
    # shared by two voices - two notes drawn adjacent on the same row, the
    # lower stem-left and the higher swapped stem-right - and must survive as
    # two notes in two voices. score_n's flagged spots looked like single
    # notes on the page but are ALSO two-voice unisons underneath (a melody
    # note and a bass note sharing one position); the guitarist's read was of
    # the ink, not the content stream, so the correct fix emits one note per
    # voice there rather than doubling one note into two.
    "score_m": "Patreon/John Oeth/To the Moon/Born a Stranger (To the Moon).pdf",
    "score_n": "Classical/PrimoGuitar Misc/Carulli-Moderato-Op192-Free.pdf",
    # Carries coincident duplicate pairs with only ONE candidate stem between
    # them (issue #116) - the residue nothing can split - so the disclosure
    # counter (coincident_unsplit_pairs) has a real score to be exercised on.
    "score_o": "Patreon/John Oeth/Final Fantasy/FF XI/Ronfaure (Final Fantasy XI).pdf",
    # Ties, in both of the states that matter for issue #81, and harmonics
    # beside them. Nothing engraved in this repository has a HALF-matched
    # tie - `tuplet_and_tie`'s split one is matched at neither end, which is
    # a different thing - so `tie_ends_unpaired` has no committed fixture
    # where it is non-zero, and a counter only ever asserted at zero cannot
    # tell a working round trip from a dropped field that reads back as
    # None... which compares equal to nothing and unequal to 0. This score
    # writes 6 ties, leaves 4 tie ends unpaired, and marks 19 harmonics.
    "score_p": "Patreon/John Oeth/Final Fantasy/FF XVI/Courage (Final Fantasy XVI).pdf",
    # Issue #210's top-member half of the coincident unison: bar 18 writes an
    # upper voice's open string in unison with the lower voice's own two-note
    # chord top, and the chord's distinct lower member (fret 2, sixth string)
    # was starved because the rank match spent both digits on the top pair.
    # Referenced by relative path; named here by the mechanism it exercises.
    "top_member_unison": ("Patreon/John Oeth/Final Fantasy/FF XVI/"
                          "My Star (Final Fantasy XVI).pdf"),
    # #116's one named residual, and issue #137's whole subject: 12 onsets
    # across 4 pages where the coincident duplicate is one member of a
    # three-notehead CHORD, so the tab's two digits are consumed by the
    # chord's own two positions and the third copy - the lower voice's own
    # note - was left with none. The only score in the library where the
    # shape occurs more than four times.
    "score_q": "Patreon/John Oeth/Final Fantasy/FF XI/The Cosmic Wheel (Final Fantasy XI).pdf",
    # #116's abutting-stem-segment case, and issue #137's largest population
    # of the arrangement its sharing CANNOT reach: 34 onsets where the
    # coincident copy is a chord's TOP member rather than its lowest, so the
    # leftover head has no twin at its own position. Pinned to prove #137
    # leaves that family exactly as it found it (see issue #141).
    "score_r": "Classical/PrimoGuitar Misc/Spanish-Romance-Guitar-Free.pdf",
    # The phase-1 repeat-structure acceptance case (issue #134): a forward
    # repeat, two endings (one closed with a hook, one left open), and the
    # phantom-measure defect that used to shift its numbering from bar 9
    # onward - the score the project's one human tester checked by hand.
    "score_s": (
        "Patreon/John Oeth/The Legend of Zelda/"
        "Zelda_s Lullaby (The Legend of Zelda Series).pdf"),
    # The adversarial review's own acid test for issue #134's blocker 1
    # (system-start volta anchoring): ending 1 opens and closes within a
    # single bar, and ending 2 opens on the very next bar, which used to be
    # rejected by the nearest_barline guard running before _anchor_mark ever
    # got a chance to place it.
    "score_t": (
        "Patreon/John Oeth/Final Fantasy/FF V/Lenna_s Theme (Final Fantasy V).pdf"),
    # Issue #87, which is the same bug as #152 on a score related to score_t:
    # page 1's last two bands each print two systems side by side, ruled 0.6pt
    # apart in y so their lines interleave. Before #152's column split those
    # bands came back as a 10-line and a 12-line group and were discarded whole,
    # the "anomaly lines=10/12" the issue reports. This fixture pins that
    # score_u's side-by-side systems are read, so #87 cannot silently
    # regress - see test_a_score_with_ruled_close_side_by_side_systems_reads_them.
    "score_u": (
        "Patreon/John Oeth/Final Fantasy/FF V/Sorrows of Parting (Final Fantasy V).pdf"),
    # A "2." bracket with no matching "1." anywhere - genuinely, not from a
    # dropped candidate: the only mark near where a "1." would be sits 1.93
    # std-staff-spaces from the nearest barline, well outside every genuine
    # bracket measured in the library, and is correctly rejected by the same
    # discriminator that rejects a ledger line or a tuplet bracket. This is
    # the one figure that is `endings_incomplete=1` and NOTHING else -
    # repeats_unread, endings_unread, endings_truncated and
    # form_marks_unanchored are all 0 - so it is the case that proves
    # `structure` confidence actually reads `endings_incomplete` (issue #134
    # adversarial review, blocker 2).
    "score_v": (
        "Patreon/John Oeth/Final Fantasy/FF VII/Victory Fanfare (Final Fantays VII).pdf"),
    # Two numbered segnos and two numbered D.S. jumps (issue #167): the page
    # draws a segno printing "1" at bar 16 and one printing "2" at bar 32, and
    # "D.S. 1"/"D.S. 2" then name one each. Before #167 both segno signs and
    # both jumps emitted the single shared id "segno", so a numbered D.S.
    # landed at whichever segno a reader found first.
    "score_w": (
        "Patreon/John Oeth/Final Fantasy/FF IX/Melodies of Life (Final Fantasy IX).pdf"),
    # The adversarial case for #167: this page prints its segno "2" on the
    # HIGHER system (bar 2) and its segno "1" LOWER (bar 10), so numbering by
    # appearance order gets both backwards. The id has to come from the
    # printed digit, which is what this fixture proves.
    "score_x": (
        "Patreon/John Oeth/Octopath Traveler/Agnea, the Dancer (Octopath Traveler II).pdf"),
    # Numbered codas drawn as a music-font digit BEFORE the sign ("1 (sign)
    # Coda" @19, "2 (sign) Coda" @27) rather than as the word "Coda 1" (issue
    # #167). Also the "To Coda 1 & 2" @10 that names both, which a single-id
    # tocoda cannot express and so is disclosed.
    "score_y": (
        "Patreon/John Oeth/Anime/Naruto/Hinata vs Neji (Naruto).pdf"),
    # Two thick strokes ("tHHt") with no repeat dots found anywhere nearby -
    # neither resolved to a direction nor unread for want of a thick stroke,
    # just two thick strokes and nothing beside them (issue #134 adversarial
    # review, item 6). `_bar_style_for_shape` deliberately returns None for
    # 2+ thick strokes (it expects the "both"-repeat branch to write
    # heavy-heavy with its own direction attached), so before this fix the
    # whole barline group - not just its repeat, its bar-style too - was
    # dropped silently. The only real fixture in the library with this shape
    # (2 instances, both on this one barline group's two measure sides).
    "score_z": "Classical/Tarrega/Tarrega-Estudio-Em-Werner.pdf",
    # The coda-system layout, in the two shapes issue #152 covers. In both,
    # the coda is engraved as a short system to the RIGHT of the last full
    # system, on the same horizontal band.
    #
    # SHAPE 1 - the right-hand system's staff lines are SHORT (134.5pt on a
    # 612pt page, under the old 0.25 length floor), so they never reached
    # staff detection at all and the system was invisible: no staff, no
    # anomaly, no bars, nothing said. 40 files library-wide. The page prints
    # 18 bars and the extractor reported 17.
    "score_aa": "Patreon/John Oeth/Animal Crossing/1 AM (Animal Crossing New Leaf).pdf",
    # The same shape with a D.C. rather than a D.S.: the page prints 37 bars
    # and the extractor reported 36.
    "score_ab": (
        "Patreon/John Oeth/The Legend of Zelda/"
        "Kakariko Village (The Legend of Zelda Series).pdf"),
    # SHAPE 2 - both systems on the band are long enough to be seen, but
    # they are ruled 1.5-1.7pt apart, so their rows interleave inside the
    # 15.0pt cluster gap and the pair came back as ONE group with twice the
    # lines, which was discarded whole. score_ad's last band is a
    # 12-line tab group (its two notation staves, ruled at the SAME y,
    # having silently merged into one full-width staff instead); the page
    # prints 35 bars and the extractor reported 31.
    "score_ad": "Patreon/John Oeth/Suikoden/Imprisoned Town (Suikoden II).pdf",
    # A system that is STILL lost after issue #152, and lost for a different
    # reason - so `systems_unread` has a score with a genuinely nonzero count
    # to be exercised on. Page 1's third band comes back as a 7-line group:
    # an ordinary 6-line tab staff ruled at 7.7pt, plus ONE extra rule 14.3pt
    # below the last line, which falls inside the 15.0pt cluster gap. Not two
    # systems side by side - the stray rule spans the same full width the
    # staff does - so no split by x extent can separate them, and the group
    # is discarded whole with its bars.
    "score_ac": (
        "Patreon/John Oeth/Final Fantasy/FF XIV/Dynamis (Final Fantasy XIV Endwalker).pdf"),
    # The same shape showing BOTH halves of it at once: a 10-line group (two
    # notation staves) and a 12-line group (two tab staves) on one band,
    # both discarded. Named in issue #153 as the one coda sign no test
    # inside the navigation reader could reach, because the staff its mark
    # was measured against spanned the whole page width. The page prints 58
    # bars - a three-bar system opening at 54 and a two-bar coda system
    # opening at 57 beside it - and the extractor reported 53.
    "score_ae": (
        "Patreon/John Oeth/Final Fantasy/FF XIV/"
        "The Nautilus Knoweth (Final Fantasy XIV Endwalker).pdf"),
    # A "To Coda" on a score that draws no coda sign and prints no coda
    # label anywhere - so `nav_marks_unresolved` is genuinely 1, on a score
    # whose every other structure figure is 0 (issue #134 phase 2).
    "score_af": "Patreon/John Oeth/Final Fantasy/FF VI/Phantom Train (Final Fantasy VI).pdf",
    # "To Coda (sign)": the coda glyph printed INSIDE the instruction's own
    # text line, which was read as a coda section head on the To Coda's own
    # bar (issue #134 phase 2 adversarial review, blocker 4).
    "score_ag": (
        "Patreon/John Oeth/Octopath Traveler/Bygone Days (Octopath Traveler II).pdf"),
    # Issue #154: every embedded font in this PDF is renamed generically
    # ("CIDFont+F1".."CIDFont+F9"), including its Maestro subset - none of
    # them named "Maestro" at all. load_music_fonts used to reject the
    # Maestro resource by that name before its fingerprint was ever
    # consulted, so this fully engraved, 3-page score read zero glyph events:
    # no noteheads, no rhythm, and its segno/coda signs invisible with it.
    "score_ah": (
        "Patreon/John Oeth/The Legend of Zelda/TLOZ Breath of the Wild/"
        "Rito Village - Night (The Legend of Zelda Breath of the Wild).pdf"),
    # Issue #180: bar 16 (a traditional-song setting, last system on PDF page
    # 84) prints a whole-measure rest in the melody staff - the melody is silent
    # for the bar - over a six-eighth arpeggio in the tablature that fills the
    # 3/4 bar on its own. The tab staff draws no stems for the silent melody, so
    # the stem-based voice split never makes it, and the whole rest used to be
    # stacked onto the arpeggio's voice, reading the bar as 7.0 quarters. Now
    # read as two voices of 3.0. Nothing committed here reproduces a
    # whole-measure rest sharing a bar with a voice that already fills it.
    "score_ai": (
        "Method Books/Classical-Guitar-Method-Vol1-2020.pdf"),
}

# Skips for want of a library are COUNTED HERE as they happen, rather than
# recognised afterwards by matching text in the skip reason. Matching text
# would quietly stop counting the day someone worded a new skip differently,
# and an undercount here reads as "everything ran" - the exact failure this
# summary exists to prevent.
_library_skips = []


def skip_without_library(reason: str):
    _library_skips.append(reason)
    pytest.skip(reason)


# Skips for want of `node` or the web project's installed alphaTab build get
# the same treatment, for the same reason (issue #134 adversarial review,
# item 7): a run with `web/node_modules` missing quietly skipped nine tests -
# including score_s's and playback-order headline cases - and
# nothing said so unless a reader compared this run's summary against CI's by
# hand. See test_tabextract._parse_with_alphatab /
# _load_musicxml_with_alphatab, the two places that actually skip.
_node_modules_skips = []


def skip_without_node_modules(reason: str):
    _node_modules_skips.append(reason)
    pytest.skip(reason)


def engraved_pdf(name: str) -> Path:
    """One committed engraved fixture. Absent means the repository is broken,
    not that the test cannot run, so this fails rather than skipping."""
    path = ENGRAVED_DIR / f"{name}.pdf"
    if not path.is_file():
        raise AssertionError(
            f"engraved fixture {name}.pdf is missing from {ENGRAVED_DIR} - it is "
            "committed on purpose; regenerate with "
            "server/tools/tab_extract/engrave_fixtures.py"
        )
    return path


@pytest.fixture
def engraved():
    return engraved_pdf


@pytest.fixture
def extractable_pdf() -> Path:
    """A committed engraved score with notation over tablature - for tests
    that need SOME extractable PDF and are not about whose engraver made it."""
    return engraved_pdf("notation_and_tab")


@pytest.fixture
def non_extractable_pdf() -> Path:
    """A committed engraved score with no tablature staff at all."""
    return engraved_pdf("notation_only")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say out loud how much of the suite did not run for want of a library,
    or for want of `web/node_modules`.

    Without this the only way to notice was to compare a CI log against a
    local run by hand, which is why 36 skipped extraction tests sat
    unnoticed - and, separately, nine more that skip on missing
    `web/node_modules` (issue #134 adversarial review, item 7) went
    unannounced the same way. A count that has to be read off the screen is
    not a guarantee, but silence was not one either."""
    if not _library_skips:
        if _library_root() is not None:
            terminalreporter.write_sep(
                "=", "real-library tests all ran (FERMATA_TEST_LIBRARY is set)", green=True)
    else:
        terminalreporter.write_sep(
            "=",
            f"{len(_library_skips)} test(s) skipped for want of a sheet music library - this "
            "run did NOT exercise extraction against real engraved scores; set "
            "FERMATA_TEST_LIBRARY to a library root to run them",
            yellow=True,
            bold=True,
        )
    if _node_modules_skips:
        terminalreporter.write_sep(
            "=",
            f"{len(_node_modules_skips)} test(s) skipped for want of web/node_modules - this run "
            "did NOT verify against the real alphaTab importer/player (parsing, MusicXML "
            "loading, or playback order); run `npm ci` in web/ to run them",
            yellow=True,
            bold=True,
        )


def _library_root() -> Path | None:
    root = os.environ.get("FERMATA_TEST_LIBRARY")
    if not root:
        return None
    p = Path(root)
    return p if p.is_dir() else None


def _fixture_path(name: str) -> Path | None:
    root = _library_root()
    if root is None:
        return None
    p = root / _FIXTURE_RELATIVE_PATHS[name]
    return p if p.is_file() else None


def _check_fixture_path_keys() -> None:
    """A typo in a `_fixture_path(...)` argument below is invisible whenever
    FERMATA_TEST_LIBRARY is unset: `_fixture_path` returns None before ever
    indexing `_FIXTURE_RELATIVE_PATHS`, so the run stays green and just skips
    - the same silent-gap failure mode this file's skip counter exists to
    catch, one level up. Checked here, at collection time, against every
    quoted key argument this file's own source text actually calls
    `_fixture_path` with, so a typo'd key fails loudly on every run, library
    configured or not."""
    import re

    src = Path(__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'_fixture_path\("([^"]+)"\)', src))
    valid = set(_FIXTURE_RELATIVE_PATHS)
    unknown = used - valid
    if unknown:
        raise AssertionError(
            f"_fixture_path(...) called with key(s) not in _FIXTURE_RELATIVE_PATHS: "
            f"{sorted(unknown)}"
        )


_check_fixture_path_keys()


@pytest.fixture
def score_a_pdf() -> Path:
    p = _fixture_path("score_a")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_a' fixture)")
    return p


@pytest.fixture
def score_b_pdf() -> Path:
    p = _fixture_path("score_b")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_b' fixture)")
    return p


@pytest.fixture
def score_c_pdf() -> Path:
    p = _fixture_path("score_c")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_c' fixture)")
    return p


@pytest.fixture
def score_d_pdf() -> Path:
    p = _fixture_path("score_d")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_d' fixture)")
    return p


@pytest.fixture
def score_e_pdf() -> Path:
    p = _fixture_path("score_e")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_e' fixture)")
    return p


@pytest.fixture
def score_f_pdf() -> Path:
    p = _fixture_path("score_f")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_f' fixture)")
    return p


@pytest.fixture
def score_g_pdf() -> Path:
    p = _fixture_path("score_g")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_g' fixture)")
    return p


@pytest.fixture
def score_h_pdf() -> Path:
    p = _fixture_path("score_h")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_h' fixture)")
    return p


@pytest.fixture
def score_p_pdf() -> Path:
    p = _fixture_path("score_p")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_p' fixture)")
    return p


@pytest.fixture
def score_i_pdf() -> Path:
    p = _fixture_path("score_i")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_i' fixture)")
    return p


@pytest.fixture
def score_j_pdf() -> Path:
    p = _fixture_path("score_j")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_j' fixture)")
    return p


@pytest.fixture
def score_k_pdf() -> Path:
    p = _fixture_path("score_k")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_k' fixture)")
    return p


@pytest.fixture
def score_l_pdf() -> Path:
    p = _fixture_path("score_l")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_l' fixture)")
    return p


@pytest.fixture
def chord_shared_dot_column_a_pdf() -> Path:
    p = _fixture_path("chord_shared_dot_column_a")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'chord_shared_dot_column_a' fixture)")
    return p


@pytest.fixture
def chord_shared_dot_column_b_pdf() -> Path:
    p = _fixture_path("chord_shared_dot_column_b")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'chord_shared_dot_column_b' fixture)")
    return p


@pytest.fixture
def pushed_down_cascade_pdf() -> Path:
    p = _fixture_path("pushed_down_cascade")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'pushed_down_cascade' fixture)")
    return p


@pytest.fixture
def score_m_pdf() -> Path:
    p = _fixture_path("score_m")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_m' fixture)")
    return p


@pytest.fixture
def score_n_pdf() -> Path:
    p = _fixture_path("score_n")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_n' fixture)")
    return p


@pytest.fixture
def score_o_pdf() -> Path:
    p = _fixture_path("score_o")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_o' fixture)")
    return p


@pytest.fixture
def score_q_pdf() -> Path:
    p = _fixture_path("score_q")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_q' fixture)")
    return p


@pytest.fixture
def score_r_pdf() -> Path:
    p = _fixture_path("score_r")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_r' fixture)")
    return p


@pytest.fixture
def top_member_unison_pdf() -> Path:
    p = _fixture_path("top_member_unison")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'top_member_unison' fixture)")
    return p


@pytest.fixture
def score_s_pdf() -> Path:
    p = _fixture_path("score_s")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_s' fixture)")
    return p


@pytest.fixture
def score_t_pdf() -> Path:
    p = _fixture_path("score_t")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_t' fixture)")
    return p


@pytest.fixture
def score_u_pdf() -> Path:
    p = _fixture_path("score_u")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_u' fixture)")
    return p


@pytest.fixture
def score_v_pdf() -> Path:
    p = _fixture_path("score_v")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_v' fixture)")
    return p


@pytest.fixture
def score_w_pdf() -> Path:
    p = _fixture_path("score_w")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_w' fixture)")
    return p


@pytest.fixture
def score_x_pdf() -> Path:
    p = _fixture_path("score_x")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_x' fixture)")
    return p


@pytest.fixture
def score_y_pdf() -> Path:
    p = _fixture_path("score_y")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_y' fixture)")
    return p


@pytest.fixture
def score_z_pdf() -> Path:
    p = _fixture_path("score_z")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_z' fixture)")
    return p


@pytest.fixture
def score_aa_pdf() -> Path:
    p = _fixture_path("score_aa")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_aa' fixture)")
    return p


@pytest.fixture
def score_ab_pdf() -> Path:
    p = _fixture_path("score_ab")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_ab' fixture)")
    return p


@pytest.fixture
def score_ac_pdf() -> Path:
    p = _fixture_path("score_ac")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing the 'score_ac' fixture)")
    return p


@pytest.fixture
def score_ad_pdf() -> Path:
    p = _fixture_path("score_ad")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_ad' fixture)")
    return p


@pytest.fixture
def score_ae_pdf() -> Path:
    p = _fixture_path("score_ae")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_ae' fixture)")
    return p


@pytest.fixture
def score_af_pdf() -> Path:
    p = _fixture_path("score_af")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_af' fixture)")
    return p


@pytest.fixture
def score_ag_pdf() -> Path:
    p = _fixture_path("score_ag")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_ag' fixture)")
    return p


@pytest.fixture
def score_ah_pdf() -> Path:
    p = _fixture_path("score_ah")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_ah' fixture)")
    return p


@pytest.fixture
def score_ai_pdf() -> Path:
    p = _fixture_path("score_ai")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing the 'score_ai' fixture)")
    return p


@pytest.fixture
def library_root() -> Path:
    """The whole configured library root, for tests that scan every PDF in
    it rather than reading one named fixture - see issue #134's library-wide
    conformance and repeat-structure checks."""
    root = _library_root()
    if root is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set")
    return root


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Point the db module at a throwaway sqlite file for one test, and reset
    the per-thread cached connection so the swap actually takes effect.

    The config module's three directories are redirected here as well, and the
    library one is CREATED. Fermata refuses to start without a library folder
    on purpose (see config.ensure_dirs and #95), so a test that goes through
    main.py's lifespan needs one that exists - and pointing it at tmp_path
    rather than making the repository's own ./library is what keeps a test run
    from writing into the checkout, which the config directory previously did.
    """
    from fermata import config, db

    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(config, "LIBRARY_DIR", library)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "config" / "cache")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fermata_test.db")
    db._local.conn = None
    db.init_db()
    yield
    db._local.conn = None


@pytest.fixture
def insert_score():
    def _insert(conn, rel_path: str, title: str = "Test Score") -> int:
        cur = conn.execute(
            """INSERT INTO scores(title, path, file_type, hash, size, mtime)
               VALUES (?, ?, 'pdf', 'deadbeef', 1, 0.0)""",
            (title, rel_path),
        )
        conn.commit()
        return cur.lastrowid

    return _insert
