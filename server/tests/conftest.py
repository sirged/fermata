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
    "zanarkand": "Patreon/John Oeth/Final Fantasy/FF X/To Zanarkand (Final Fantasy X).pdf",
    "tarrega": "Classical/Tarrega/Tarrega-Study-in-C-Guitar-Free.pdf",
    "claire_de_lune": "Favorites/ClairDeLuneGuitar.pdf",
    # Two-voice writing where a melody note shares a beat with a stem-down
    # chord: the figure notehead-to-stem attachment gets wrong, and an Opus
    # engraving, so it also exercises the side bearing that only that font has.
    "dalza": "Classical/PrimoGuitar Misc/Dalza-Recercar-Guitar-2019.pdf",
    # Filled noteheads whose stems the vector pass never sees, on every one of
    # its notation staves. Nothing engraved in this repository reproduces that
    # - MuseScore draws every stem as a clean vector line, so all twelve
    # committed fixtures report zero - and it is the state that floors a
    # notehead's duration at a quarter, so the counter for it needs a real
    # score to be exercised on at all.
    "hymn_of_the_fayth": (
        "Patreon/John Oeth/Final Fantasy/FF X/Hymn of the Fayth (Final Fantasy X).pdf"),
    # Meter changes engraved part-way ALONG a system, over and over, on a
    # system compressed enough that a stem lands within reading distance of
    # the next meter's digits. Nothing engraved here reproduces that
    # crowding - see test_a_meter_further_along_a_bar_is_not_a_meter_at_this_barline.
    "mitsuha": "Patreon/John Oeth/Anime/Your Name/Theme of Mitsuha (Your Name.).pdf",
    # A meter change printed part-way along a system, behind a key change at
    # the SAME barline: three flats push the numerator's left edge to 6.18
    # staff spaces past the barline, past the flat reach a mid-system reader
    # sized only for "nothing between the barline and the meter" allows.
    # Nothing engraved here carries a key change at a mid-system barline at
    # all - see test_a_key_change_at_a_mid_system_barline_does_not_hide_the_meter.
    "wild_arms": "Patreon/John Oeth/Wild Arms/Into the Wilderness (Wild Arms).pdf",
    # A courtesy time signature - the key and meter for the NEXT system,
    # printed as the last thing on THIS one - behind four sharps, about 7
    # staff spaces past the system's own last barline. Widening the
    # mid-system window enough to read a key change at a barline (see
    # `wild_arms` above) also brings this within reach, and reading it there
    # would start the change a system early - see
    # test_a_courtesy_meter_at_the_end_of_a_system_is_not_applied_early.
    "kaine_salvation": "Patreon/John Oeth/NieR/Kaine Salvation (NieR).pdf",
    # A five-note chord whose dots the engraver pushed down a step, over a
    # notehead that already carries a dot of its own further up the same
    # column (issues #111/#112). Nothing engraved in this repository produces
    # a chord that deep, and the exemption that keeps such a head from
    # refuting the pushed-down reading has no other real score to be
    # exercised on - see glyph_rhythm._pushed_down_pairs.
    "storms_past": "Patreon/John Oeth/New World/Storm_s Past (New World).pdf",
    # Two of the four scores the #116 research had a guitarist check against
    # the printed page. Born a Stranger's flagged spot is a genuine unison
    # shared by two voices - two notes drawn adjacent on the same row, the
    # lower stem-left and the higher swapped stem-right - and must survive as
    # two notes in two voices. Carulli's flagged spots looked like single
    # notes on the page but are ALSO two-voice unisons underneath (a melody
    # note and a bass note sharing one position); the guitarist's read was of
    # the ink, not the content stream, so the correct fix emits one note per
    # voice there rather than doubling one note into two.
    "born_a_stranger": "Patreon/John Oeth/To the Moon/Born a Stranger (To the Moon).pdf",
    "carulli_moderato": "Classical/PrimoGuitar Misc/Carulli-Moderato-Op192-Free.pdf",
    # Carries coincident duplicate pairs with only ONE candidate stem between
    # them (issue #116) - the residue nothing can split - so the disclosure
    # counter (coincident_unsplit_pairs) has a real score to be exercised on.
    "ronfaure": "Patreon/John Oeth/Final Fantasy/FF XI/Ronfaure (Final Fantasy XI).pdf",
    # Ties, in both of the states that matter for issue #81, and harmonics
    # beside them. Nothing engraved in this repository has a HALF-matched
    # tie - `tuplet_and_tie`'s split one is matched at neither end, which is
    # a different thing - so `tie_ends_unpaired` has no committed fixture
    # where it is non-zero, and a counter only ever asserted at zero cannot
    # tell a working round trip from a dropped field that reads back as
    # None... which compares equal to nothing and unequal to 0. This score
    # writes 6 ties, leaves 4 tie ends unpaired, and marks 19 harmonics.
    "courage": "Patreon/John Oeth/Final Fantasy/FF XVI/Courage (Final Fantasy XVI).pdf",
    # #116's one named residual, and issue #137's whole subject: 12 onsets
    # across 4 pages where the coincident duplicate is one member of a
    # three-notehead CHORD, so the tab's two digits are consumed by the
    # chord's own two positions and the third copy - the lower voice's own
    # note - was left with none. The only score in the library where the
    # shape occurs more than four times.
    "cosmic_wheel": "Patreon/John Oeth/Final Fantasy/FF XI/The Cosmic Wheel (Final Fantasy XI).pdf",
    # #116's abutting-stem-segment case, and issue #137's largest population
    # of the arrangement its sharing CANNOT reach: 34 onsets where the
    # coincident copy is a chord's TOP member rather than its lowest, so the
    # leftover head has no twin at its own position. Pinned to prove #137
    # leaves that family exactly as it found it (see issue #141).
    "spanish_romance": "Classical/PrimoGuitar Misc/Spanish-Romance-Guitar-Free.pdf",
    # The phase-1 repeat-structure acceptance case (issue #134): a forward
    # repeat, two endings (one closed with a hook, one left open), and the
    # phantom-measure defect that used to shift its numbering from bar 9
    # onward - the score the project's one human tester checked by hand.
    "zelda_lullaby": (
        "Patreon/John Oeth/The Legend of Zelda/"
        "Zelda_s Lullaby (The Legend of Zelda Series).pdf"),
    # The adversarial review's own acid test for issue #134's blocker 1
    # (system-start volta anchoring): ending 1 opens and closes within a
    # single bar, and ending 2 opens on the very next bar, which used to be
    # rejected by the nearest_barline guard running before _anchor_mark ever
    # got a chance to place it.
    "lenna_theme": (
        "Patreon/John Oeth/Final Fantasy/FF V/Lenna_s Theme (Final Fantasy V).pdf"),
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
    "victory_fanfare": (
        "Patreon/John Oeth/Final Fantasy/FF VII/Victory Fanfare (Final Fantays VII).pdf"),
    # Two thick strokes ("tHHt") with no repeat dots found anywhere nearby -
    # neither resolved to a direction nor unread for want of a thick stroke,
    # just two thick strokes and nothing beside them (issue #134 adversarial
    # review, item 6). `_bar_style_for_shape` deliberately returns None for
    # 2+ thick strokes (it expects the "both"-repeat branch to write
    # heavy-heavy with its own direction attached), so before this fix the
    # whole barline group - not just its repeat, its bar-style too - was
    # dropped silently. The only real fixture in the library with this shape
    # (2 instances, both on this one barline group's two measure sides).
    "tarrega_estudio_em": "Classical/Tarrega/Tarrega-Estudio-Em-Werner.pdf",
    # The coda-system layout, in the two shapes issue #152 covers. In both,
    # the coda is engraved as a short system to the RIGHT of the last full
    # system, on the same horizontal band.
    #
    # SHAPE 1 - the right-hand system's staff lines are SHORT (134.5pt on a
    # 612pt page, under the old 0.25 length floor), so they never reached
    # staff detection at all and the system was invisible: no staff, no
    # anomaly, no bars, nothing said. 40 files library-wide. The page prints
    # 18 bars and the extractor reported 17.
    "one_am": "Patreon/John Oeth/Animal Crossing/1 AM (Animal Crossing New Leaf).pdf",
    # The same shape with a D.C. rather than a D.S.: the page prints 37 bars
    # and the extractor reported 36.
    "kakariko_village": (
        "Patreon/John Oeth/The Legend of Zelda/"
        "Kakariko Village (The Legend of Zelda Series).pdf"),
    # SHAPE 2 - both systems on the band are long enough to be seen, but
    # they are ruled 1.5-1.7pt apart, so their rows interleave inside the
    # 15.0pt cluster gap and the pair came back as ONE group with twice the
    # lines, which was discarded whole. Imprisoned Town's last band is a
    # 12-line tab group (its two notation staves, ruled at the SAME y,
    # having silently merged into one full-width staff instead); the page
    # prints 35 bars and the extractor reported 31.
    "imprisoned_town": "Patreon/John Oeth/Suikoden/Imprisoned Town (Suikoden II).pdf",
    # A system that is STILL lost after issue #152, and lost for a different
    # reason - so `systems_unread` has a score with a genuinely nonzero count
    # to be exercised on. Page 1's third band comes back as a 7-line group:
    # an ordinary 6-line tab staff ruled at 7.7pt, plus ONE extra rule 14.3pt
    # below the last line, which falls inside the 15.0pt cluster gap. Not two
    # systems side by side - the stray rule spans the same full width the
    # staff does - so no split by x extent can separate them, and the group
    # is discarded whole with its bars.
    "dynamis": (
        "Patreon/John Oeth/Final Fantasy/FF XIV/Dynamis (Final Fantasy XIV Endwalker).pdf"),
    # The same shape showing BOTH halves of it at once: a 10-line group (two
    # notation staves) and a 12-line group (two tab staves) on one band,
    # both discarded. Named in issue #153 as the one coda sign no test
    # inside the navigation reader could reach, because the staff its mark
    # was measured against spanned the whole page width. The page prints 58
    # bars - a three-bar system opening at 54 and a two-bar coda system
    # opening at 57 beside it - and the extractor reported 53.
    "nautilus_knoweth": (
        "Patreon/John Oeth/Final Fantasy/FF XIV/"
        "The Nautilus Knoweth (Final Fantasy XIV Endwalker).pdf"),
    # A "To Coda" on a score that draws no coda sign and prints no coda
    # label anywhere - so `nav_marks_unresolved` is genuinely 1, on a score
    # whose every other structure figure is 0 (issue #134 phase 2).
    "phantom_train": "Patreon/John Oeth/Final Fantasy/FF VI/Phantom Train (Final Fantasy VI).pdf",
    # "To Coda (sign)": the coda glyph printed INSIDE the instruction's own
    # text line, which was read as a coda section head on the To Coda's own
    # bar (issue #134 phase 2 adversarial review, blocker 4).
    "bygone_days": (
        "Patreon/John Oeth/Octopath Traveler/Bygone Days (Octopath Traveler II).pdf"),
    # Issue #154: every embedded font in this PDF is renamed generically
    # ("CIDFont+F1".."CIDFont+F9"), including its Maestro subset - none of
    # them named "Maestro" at all. load_music_fonts used to reject the
    # Maestro resource by that name before its fingerprint was ever
    # consulted, so this fully engraved, 3-page score read zero glyph events:
    # no noteheads, no rhythm, and its segno/coda signs invisible with it.
    "rito_village": (
        "Patreon/John Oeth/The Legend of Zelda/TLOZ Breath of the Wild/"
        "Rito Village - Night (The Legend of Zelda Breath of the Wild).pdf"),
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
# including the Zelda's Lullaby and playback-order headline cases - and
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


@pytest.fixture
def zanarkand_pdf() -> Path:
    p = _fixture_path("zanarkand")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing 'To Zanarkand' fixture)")
    return p


@pytest.fixture
def tarrega_pdf() -> Path:
    p = _fixture_path("tarrega")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing Tarrega fixture)")
    return p


@pytest.fixture
def claire_de_lune_pdf() -> Path:
    p = _fixture_path("claire_de_lune")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing Clair de Lune fixture)")
    return p


@pytest.fixture
def dalza_pdf() -> Path:
    p = _fixture_path("dalza")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing Dalza fixture)")
    return p


@pytest.fixture
def hymn_of_the_fayth_pdf() -> Path:
    p = _fixture_path("hymn_of_the_fayth")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Hymn of the Fayth' fixture)")
    return p


@pytest.fixture
def courage_pdf() -> Path:
    p = _fixture_path("courage")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Courage' fixture)")
    return p


@pytest.fixture
def mitsuha_pdf() -> Path:
    p = _fixture_path("mitsuha")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Theme of Mitsuha' fixture)")
    return p


@pytest.fixture
def wild_arms_pdf() -> Path:
    p = _fixture_path("wild_arms")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Into the Wilderness' fixture)")
    return p


@pytest.fixture
def kaine_salvation_pdf() -> Path:
    p = _fixture_path("kaine_salvation")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Kaine Salvation' fixture)")
    return p


@pytest.fixture
def storms_past_pdf() -> Path:
    p = _fixture_path("storms_past")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Storm's Past' fixture)")
    return p


@pytest.fixture
def born_a_stranger_pdf() -> Path:
    p = _fixture_path("born_a_stranger")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Born a Stranger' fixture)")
    return p


@pytest.fixture
def carulli_moderato_pdf() -> Path:
    p = _fixture_path("carulli_moderato")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing Carulli-Moderato fixture)")
    return p


@pytest.fixture
def ronfaure_pdf() -> Path:
    p = _fixture_path("ronfaure")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing Ronfaure fixture)")
    return p


@pytest.fixture
def cosmic_wheel_pdf() -> Path:
    p = _fixture_path("cosmic_wheel")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'The Cosmic Wheel' fixture)")
    return p


@pytest.fixture
def spanish_romance_pdf() -> Path:
    p = _fixture_path("spanish_romance")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Spanish Romance' fixture)")
    return p


@pytest.fixture
def zelda_lullaby_pdf() -> Path:
    p = _fixture_path("zelda_lullaby")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing Zelda's Lullaby fixture)")
    return p


@pytest.fixture
def lenna_theme_pdf() -> Path:
    p = _fixture_path("lenna_theme")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing Lenna's Theme fixture)")
    return p


@pytest.fixture
def victory_fanfare_pdf() -> Path:
    p = _fixture_path("victory_fanfare")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Victory Fanfare' fixture)")
    return p


@pytest.fixture
def tarrega_estudio_em_pdf() -> Path:
    p = _fixture_path("tarrega_estudio_em")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing Tarrega-Estudio-Em fixture)")
    return p


@pytest.fixture
def one_am_pdf() -> Path:
    p = _fixture_path("one_am")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing '1 AM' fixture)")
    return p


@pytest.fixture
def kakariko_village_pdf() -> Path:
    p = _fixture_path("kakariko_village")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Kakariko Village' fixture)")
    return p


@pytest.fixture
def dynamis_pdf() -> Path:
    p = _fixture_path("dynamis")
    if p is None:
        skip_without_library("FERMATA_TEST_LIBRARY not set (or missing 'Dynamis' fixture)")
    return p


@pytest.fixture
def imprisoned_town_pdf() -> Path:
    p = _fixture_path("imprisoned_town")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Imprisoned Town' fixture)")
    return p


@pytest.fixture
def nautilus_knoweth_pdf() -> Path:
    p = _fixture_path("nautilus_knoweth")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'The Nautilus Knoweth' fixture)")
    return p


@pytest.fixture
def phantom_train_pdf() -> Path:
    p = _fixture_path("phantom_train")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Phantom Train' fixture)")
    return p


@pytest.fixture
def bygone_days_pdf() -> Path:
    p = _fixture_path("bygone_days")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Bygone Days' fixture)")
    return p


@pytest.fixture
def rito_village_pdf() -> Path:
    p = _fixture_path("rito_village")
    if p is None:
        skip_without_library(
            "FERMATA_TEST_LIBRARY not set (or missing 'Rito Village - Night' fixture)")
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
