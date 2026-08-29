"""Regenerate the engraved test fixtures in server/tests/fixtures/engraved.

WHY THESE EXIST. Every test that needed an engraved score used to read one
from the maintainer's own library, which cannot be committed, so the whole
extraction suite skipped in CI and a change that broke reading tablature out
of a PDF merged green. These fixtures are engraved here instead of sourced
from anywhere, so their licence is not in question, their content is known
exactly, and this script can rebuild them.

THE CHAIN, all of it in the repository:

    FIXTURES (below)  ->  <name>.musicxml  ->  MuseScore  ->  <name>.pdf

Run with no arguments to rebuild the MusicXML and, if MuseScore is
installed, re-engrave the PDFs:

    python server/tools/tab_extract/engrave_fixtures.py

    --check     rewrite nothing; report whether the committed MusicXML
                still matches what this script would write (a fixture whose
                source drifted from its generator is no longer regenerable)
    --musicxml  write the MusicXML only, no engraving
    --report    engrave, then print what the extractor makes of each PDF

MuseScore is found via $MUSESCORE, or the usual install locations. Any
SMuFL-font engraver would do - the decoder is keyed on SMuFL codepoints, not
on a brand - but the committed PDFs were produced by the version named in
the fixtures' README, and re-engraving with another one will move
coordinates and can change what the tests measure.
"""
import argparse
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "engraved"

DIVISIONS = 4  # per quarter note: enough for the 16ths most fixtures stop at

TYPE_QUARTERS = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
                 "16th": 0.25, "32nd": 0.125, "64th": 0.0625}

# The divisions the fixture being written right now is using. Four per quarter
# cannot express a 32nd - it rounds to zero, which is what `rests_and_flags`
# writes for each of its - so a fixture whose whole point is a value shorter
# than a 16th asks for more, and declares the number it asked for in its own
# <attributes>. Scoped rather than raised for everybody because every
# committed fixture's MusicXML is compared byte for byte against what this
# script writes (--check), and changing the divisions changes every duration
# in every one of them.
_divisions = DIVISIONS


@contextlib.contextmanager
def divisions(per_quarter):
    """Write the fixture inside this block with finer divisions."""
    global _divisions
    was, _divisions = _divisions, per_quarter
    try:
        yield
    finally:
        _divisions = was


STANDARD_TUNING = [("E", 3), ("A", 3), ("D", 4), ("G", 4), ("B", 4), ("E", 5)]
DROP_D_TUNING = [("D", 3), ("A", 3), ("D", 4), ("G", 4), ("B", 4), ("E", 5)]
# Both are written an octave above the strings they engrave. MuseScore reads
# a part whose clef carries an octave change as notated rather than sounding
# pitch, tuning included, so a staff-tuning of E3 A3 D4 G4 B4 E5 is what
# produces a guitar in standard E2 A2 D3 G3 B3 E4 - measured by reading the
# frets back out of the engraved page, not assumed. The pitches in the bars
# below are written pitches for the same reason: they sound an octave lower.


# ---------------------------------------------------------------------------
# MusicXML emission
# ---------------------------------------------------------------------------

def _dotted_quarters(typ, dots):
    """What `dots` augmentation dots make of `typ`.

    Each dot adds HALF of what the previous one added, so the multiplier is
    1.5, 1.75, 1.875 - not 1.5 ** dots, which is right for one dot and wrong
    for every number above it (it makes a double-dotted half 4.5 quarters
    instead of 3.5). No fixture asked for a second dot until #111."""
    return TYPE_QUARTERS[typ] * (2 - 0.5 ** dots)


def rest(typ, dots=0, staff=1, voice=1):
    quarters = _dotted_quarters(typ, dots)
    return (f"<note><rest/><duration>{int(round(quarters * _divisions))}</duration>"
            f"<voice>{voice}</voice><type>{typ}</type>{'<dot/>' * dots}"
            f"<staff>{staff}</staff></note>")


def note(pitch, typ, dots=0, staff=1, voice=1, chord=False, tie=None,
         tuplet=None, head=None, notations=()):
    step, octave = pitch[0], pitch[1]
    # An accidental has to be spelled out even when the key signature would
    # imply it: MusicXML pitch is absolute, so an F in D major with no
    # <alter> is an F natural, and the engraver dutifully prints the natural
    # sign that says so.
    alter = f"<alter>{pitch[2]}</alter>" if len(pitch) > 2 else ""
    quarters = _dotted_quarters(typ, dots)
    if tuplet:
        quarters = quarters * tuplet[1] / tuplet[0]
    out = ["<note>"]
    if chord:
        out.append("<chord/>")
    out.append(f"<pitch><step>{step}</step>{alter}<octave>{octave}</octave></pitch>")
    out.append(f"<duration>{int(round(quarters * _divisions))}</duration>")
    if tie:
        out.append(f'<tie type="{tie}"/>')
    out.append(f"<voice>{voice}</voice><type>{typ}</type>")
    out.append("<dot/>" * dots)
    if tuplet:
        out.append(f"<time-modification><actual-notes>{tuplet[0]}</actual-notes>"
                   f"<normal-notes>{tuplet[1]}</normal-notes></time-modification>")
    if head:
        out.append(f"<notehead>{head}</notehead>")
    out.append(f"<staff>{staff}</staff>")
    marks = list(notations)
    if tie:
        marks.append(f'<tied type="{tie}"/>')
    if marks:
        out.append("<notations>" + "".join(marks) + "</notations>")
    out.append("</note>")
    return "".join(out)


def backup(quarters):
    return f"<backup><duration>{int(round(quarters * _divisions))}</duration></backup>"


def mirror_to_tab(body):
    """The same notes again on the tablature staff.

    A tab staff in MusicXML is not a view of the notation staff, it is its
    own staff carrying its own notes, so a score that shows both writes each
    note twice. Rewriting the notation staff's own text is what keeps the
    two halves from drifting apart as these fixtures are edited. Only voices
    1, 2 and 3 are ever generated (see `note`/`rest` above), so only those
    three need remapping here."""
    return (body.replace("<staff>1</staff>", "<staff>2</staff>")
                .replace("<voice>1</voice>", "<voice>5</voice>")
                .replace("<voice>2</voice>", "<voice>6</voice>")
                .replace("<voice>3</voice>", "<voice>7</voice>"))


def staff_details(tuning, number=2):
    lines = "".join(
        f'<staff-tuning line="{i + 1}"><tuning-step>{step}</tuning-step>'
        f"<tuning-octave>{octave}</tuning-octave></staff-tuning>"
        for i, (step, octave) in enumerate(tuning))
    return (f'<staff-details number="{number}"><staff-lines>{len(tuning)}</staff-lines>'
            f"{lines}</staff-details>")


def time_signature(time, printed=True):
    """A `<time>` element, optionally engraved invisibly.

    `print-object="no"` is a real editorial choice - an edition that does not
    want a meter on the page still has to declare one for the file to mean
    anything - and it is the one shape that produces a score whose OPENING
    meter cannot be read off the page while a later change can."""
    show = "" if printed else ' print-object="no"'
    return (f"<time{show}><beats>{time[0]}</beats>"
            f"<beat-type>{time[1]}</beat-type></time>")


def attributes(fifths=0, time=(4, 4), staves=2, tuning=STANDARD_TUNING,
               octave_clef=True, tab=True, time_printed=True):
    out = [f"<attributes><divisions>{_divisions}</divisions>",
           f"<key><fifths>{fifths}</fifths></key>",
           time_signature(time, printed=time_printed)]
    if staves > 1:
        out.append(f"<staves>{staves}</staves>")
    number = ' number="1"' if staves > 1 else ""
    if tab and staves == 1:
        out.append(staff_details(tuning, number=1))
        out.append("<clef><sign>TAB</sign><line>5</line></clef>")
    else:
        change = "<clef-octave-change>-1</clef-octave-change>" if octave_clef else ""
        out.append(f"<clef{number}><sign>G</sign><line>2</line>{change}</clef>")
        if tab:
            out.append(staff_details(tuning))
            out.append('<clef number="2"><sign>TAB</sign><line>5</line></clef>')
    out.append("</attributes>")
    return "".join(out)


def score(part_name, measures, program=25):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN"'
        ' "http://www.musicxml.org/dtds/partwise.dtd">\n'
        '<score-partwise version="4.0">\n'
        "  <part-list>\n"
        f'    <score-part id="P1"><part-name>{part_name}</part-name>\n'
        '      <score-instrument id="P1-I1">'
        f"<instrument-name>{part_name}</instrument-name></score-instrument>\n"
        '      <midi-instrument id="P1-I1"><midi-channel>1</midi-channel>'
        f"<midi-program>{program}</midi-program></midi-instrument>\n"
        "    </score-part>\n"
        "  </part-list>\n"
        '  <part id="P1">\n'
        + "".join(f'    <measure number="{i + 1}">{m}</measure>\n'
                  for i, m in enumerate(measures))
        + "  </part>\n</score-partwise>\n")


# ---------------------------------------------------------------------------
# The fixtures
# ---------------------------------------------------------------------------
#
# Every fixture is at least eight bars long, and that is load-bearing rather
# than arbitrary: MuseScore stretches a system to the page width only when
# more music follows it, so a two-bar score engraves a staff barely a
# quarter of the page wide, short enough that staff detection cannot tell it
# from a beam. Eight bars gives a justified first system to measure.

def _bar(seq, quarters):
    """One measure of the same notes on the notation staff and the tab."""
    body = "".join(seq)
    return body + backup(quarters) + mirror_to_tab(body)


def fixture_notation_and_tab():
    """The ordinary layout this project exists to read: notation over its
    tablature. Carries the shapes a happy-path decode has to get right - a
    dotted note, a chord on one stem, sixteenths under a beam, a whole note
    with no stem at all - in D major so a key signature is engraved, and
    every bar adds up so a defect anywhere is a real one."""
    quarter, eighth, half, whole, s16 = "quarter", "eighth", "half", "whole", "16th"
    f_sharp4 = ("F", 4, 1)
    b1 = _bar([note(("E", 4), quarter), note(f_sharp4, eighth),
               note(("G", 4), eighth), note(("A", 4), half)], 4.0)
    b2 = _bar([note(("B", 4), half, dots=1), note(("A", 4), quarter)], 4.0)
    b3 = _bar([note(("G", 4), s16), note(f_sharp4, s16), note(("E", 4), s16),
               note(("D", 4), s16), note(("E", 4), half), note(f_sharp4, quarter)], 4.0)
    b4 = _bar([note(("D", 4), whole), note(("A", 4), whole, chord=True),
               note(("D", 5), whole, chord=True)], 4.0)
    measures = [attributes(fifths=2) + b1, b2, b3, b4]
    # A second page, so the per-page totals are proved to accumulate rather
    # than the last page silently winning.
    measures += ['<print new-page="yes"/>' + b1, b2, b3, b4]
    return score("Guitar", measures)


def fixture_tab_only():
    """Tablature with no notation staff beside it. There is no rhythm in a
    stemless tab staff, so this is the case that must degrade to the spacing
    heuristic and say so - the honest fallback, which nothing in CI could
    reach before."""
    bar = "".join([note(("E", 4), "quarter", staff=1), note(("F", 4), "eighth", staff=1),
                   note(("G", 4), "eighth", staff=1), note(("A", 4), "half", staff=1)])
    # Twelve bars, not eight, because a tab staff alone fits six to a system:
    # eight would leave a two-bar final system that MuseScore does not
    # stretch (it fills a last system only past a fraction of the width), and
    # a staff that short cannot be told from a beam by length. Twelve is two
    # full systems, so every bar engraved is a bar these tests can count.
    measures = [attributes(staves=1) + bar] + [bar] * 11
    return score("Guitar", measures)


def fixture_two_voices():
    """A melody in quarters with its stems up over an accompaniment in
    eighths with its stems down - the library's core content, and the thing
    voice separation exists for. Each voice fills the bar on its own, so a
    bar that reads as 6 quarters instead of 3 means the voices were merged."""
    upper = "".join(note(("E", 5), "quarter", voice=1) for _ in range(4))
    lower = "".join(note(("E", 4), "eighth", voice=2) for _ in range(8))
    body = upper + backup(4.0) + lower
    bar = body + backup(4.0) + mirror_to_tab(body)
    return score("Guitar", [attributes() + bar] + [bar] * 7)


def fixture_unison_voices():
    """`two_voices` with the upper voice dropped to the lower voice's own
    pitch (issue #116) - a unison on every beat the two voices share, rather
    than the upper voice's own line. MuseScore draws that as the SAME
    notehead glyph twice at the identical position, once per voice's stem -
    32 coincident pairs (4 per bar, 8 bars) on this page, measured against
    the engraved PDF rather than assumed.

    A decoder that binds both copies to the SAME stem (main, unfixed) leaves
    the other voice's stem with no notehead at all: the upper voice loses
    its note on every beat, and the lower voice's own eighths are still
    there, so each bar reads as the lower voice's 8 eighths alone against a
    4/4 meter that wants the upper voice's 4 quarters riding concurrently
    over them - overfull by the upper voice's whole missing content. A
    decoder that instead collapses the coincident glyphs to one copy before
    reading rhythm is the WRONG fix and stays exactly as overfull, because
    collapsing does not put a notehead back on the abandoned stem - the two
    fixes are distinguished by whether they read every bar's beats and notes
    back correctly, not by whether they turn the bar count green by luck.

    Each voice fills the bar on its own (4 quarters, 8 eighths, both 4.0
    quarters long), same as `two_voices` - only the pitch changed, so any
    difference in the bar arithmetic between the two fixtures is entirely
    the coincident duplicate's doing."""
    upper = "".join(note(("E", 4), "quarter", voice=1) for _ in range(4))
    lower = "".join(note(("E", 4), "eighth", voice=2) for _ in range(8))
    body = upper + backup(4.0) + lower
    bar = body + backup(4.0) + mirror_to_tab(body)
    return score("Guitar", [attributes() + bar] + [bar] * 7)


def fixture_unison_in_chord():
    """`unison_voices` with the upper voice thickened into a CHORD, so the
    unison is one member of it rather than the whole of it (issue #137) - the
    shape the library's own residual case has, measured off The Cosmic Wheel
    (FF XI) page by page: an upper voice writing a two-note chord whose LOWER
    member sounds the same pitch, at the same moment, as the lower voice's
    own eighth. Three noteheads are drawn at that onset and only two
    positions are occupied, because two of the three are the identical glyph
    stamped twice - one copy per voice's stem, exactly as in `unison_voices`.

    WHAT MAKES THIS DIFFERENT FROM `unison_voices`, and why that fixture
    cannot cover it: a unison is ONE string being plucked, so the tablature
    prints ONE fret number for it however many voices sound it. With the
    unison alone (`unison_voices`) the engraver has a free choice of string
    for each voice and takes it - MuseScore writes that fixture's two voices
    on two different strings, 2 on the fourth and 7 on the fifth, so there is
    a digit apiece and nothing is ever short of one. Put the unison INSIDE a
    chord and the choice is gone: the chord's own two digits are what the
    column holds, and the lower voice's note is the lower of them, so the
    onset has THREE noteheads and TWO digits. The tab below is written that
    way deliberately - the lower voice's on-beat eighths are the chord's own
    lower digit and are not printed a second time, which is what the library
    page does and what an engraver would do.

    Unfixed, the digits are handed out one per notehead in pitch order, the
    third notehead gets nothing, and the voice it belongs to loses its note
    entirely: every bar reads with the lower voice 2.0 quarters short of its
    meter (4 of its 8 eighths gone), 8 of 8 bars defective. Nothing about the
    stems is wrong there - the split #116 introduced puts one copy on each
    voice's stem correctly - so a fixture that only checked the stem binding
    would pass while the bar it produced was still wrong."""
    upper = "".join(note(("A", 4), "quarter", voice=1)
                    + note(("E", 4), "quarter", voice=1, chord=True) for _ in range(4))
    lower = "".join(note(("E", 4), "eighth", voice=2) for _ in range(8))
    notation = upper + backup(4.0) + lower
    # The tab is NOT mirror_to_tab(notation): a unison is one plucked string
    # and prints one digit, so the lower voice's on-beat eighths are the
    # chord's own lower digit rather than four more numbers stacked on it.
    # Writing them out again would give the onset three digits for its three
    # noteheads and the shortage this fixture exists to hold could not arise.
    tab_upper = "".join(note(("A", 4), "quarter", voice=5, staff=2)
                        + note(("E", 4), "quarter", voice=5, staff=2, chord=True)
                        for _ in range(4))
    tab_lower = "".join(
        (rest("eighth", staff=2, voice=6) if i % 2 == 0
         else note(("E", 4), "eighth", voice=6, staff=2))
        for i in range(8))
    tab = tab_upper + backup(4.0) + tab_lower
    bar = notation + backup(4.0) + tab
    return score("Guitar", [attributes() + bar] + [bar] * 7)


def fixture_three_voices():
    """A melody in quarters (stems up) over an arpeggiated accompaniment in
    eighths (stems down), with a sustained bass held under both as a whole
    note every bar - issue #133's measured shape (melody, arpeggio and bass,
    each with its own rhythm) reduced to its smallest engraveable form.

    The bass is written as a WHOLE note deliberately, not because a real bass
    line only ever holds one note a bar, but because a whole note is the one
    duration that never takes a stem in any notation - _stem_groups' own
    chord-fold only merges two groups sharing a stem DIRECTION, and a
    stemless group's "whole" signal matches neither "up" nor "down", so this
    third voice's one note a bar reaches voice assignment as its own group
    rather than being silently absorbed into whichever of the other two it
    happens to share an onset with. All three voices attack together on beat
    one, which is exactly where a ceiling of two loses the bass: with only
    two voices available it is folded into whichever of the melody or the
    arpeggio it lands nearest, so its four quarters of silence for the rest
    of the bar are never accounted for and that voice overfills.

    Each voice fills the bar on its own (4 quarters, 8 eighths, 1 whole are
    all four quarter-notes long), so a bar that does not read as three voices
    each summing to 4.0 means the third voice vanished into one of the
    other two."""
    melody = "".join(note(("E", 5), "quarter", voice=1) for _ in range(4))
    arpeggio = "".join(note(("E", 4), "eighth", voice=2) for _ in range(8))
    bass = note(("E", 3), "whole", voice=3)
    body = melody + backup(4.0) + arpeggio + backup(4.0) + bass
    bar = body + backup(4.0) + mirror_to_tab(body)
    return score("Guitar", [attributes() + bar] + [bar] * 7)


def fixture_tuplet_and_tie():
    """A triplet and a tie across a barline. Both are known gaps - a tuplet
    is not detected at all and a tie is low confidence - so what this pins
    is that the score SAYS so, and that the triplet's bar is reported as
    holding more than its meter rather than quietly reading as correct."""
    trip = [note(("E", 4), "eighth", tuplet=(3, 2),
                 notations=('<tuplet type="start"/>',)),
            note(("F", 4), "eighth", tuplet=(3, 2)),
            note(("G", 4), "eighth", tuplet=(3, 2),
                 notations=('<tuplet type="stop"/>',))]
    b1 = _bar(trip + [note(("A", 4), "quarter"), note(("B", 4), "half")], 4.0)
    b2 = _bar([note(("E", 4), "half"), note(("G", 4), "half", tie="start")], 4.0)
    b3 = _bar([note(("G", 4), "half", tie="stop"), note(("A", 4), "half")], 4.0)
    b4 = _bar([note(("B", 4), "quarter")] * 4, 4.0)
    return score("Guitar", [attributes() + b1, b2, b3, b4, b1, b2, b3, b4])


def fixture_drop_d():
    """A non-standard tuning, named in the score the way a real edition names
    it, with a metronome mark. Both are read out of the page's text rather
    than its glyphs, and neither had any CI coverage."""
    words = ('<direction placement="above"><direction-type>'
             "<words>Drop D</words></direction-type></direction>")
    metronome = ('<direction placement="above"><direction-type><metronome>'
                 "<beat-unit>quarter</beat-unit><per-minute>88</per-minute>"
                 "</metronome></direction-type><sound tempo=\"88\"/></direction>")
    bar = _bar([note(("D", 4), "quarter"), note(("E", 4), "quarter"),
                note(("F", 4), "quarter"), note(("G", 4), "quarter")], 4.0)
    first = attributes(tuning=DROP_D_TUNING) + metronome + words + bar
    return score("Guitar", [first] + [bar] * 7)


def fixture_defective_bars():
    """Bars that genuinely do not add up, in both directions, including one
    bar that is wrong in BOTH directions at once - a voice over its meter
    beside a voice under it. The library has no example of that at all, so
    the only thing that has ever exercised it is a unit test built out of
    tuples; this is the shape arriving off a real page."""
    over = _bar([note(("E", 4), "quarter")] * 5, 5.0)
    short = _bar([note(("E", 4), "quarter")] * 3, 3.0)
    upper = "".join(note(("E", 5), "quarter", voice=1) for _ in range(5))
    lower = "".join(note(("E", 4), "quarter", voice=2) for _ in range(3))
    both_body = upper + backup(5.0) + lower
    both = both_body + backup(5.0) + mirror_to_tab(both_body)
    exact = _bar([note(("E", 4), "quarter")] * 4, 4.0)
    measures = [over, short, both, exact]
    return score("Guitar", [attributes() + measures[0]] + measures[1:] + measures)


def barline(location, style=None, repeat=None, ending=None):
    """One `<barline>` element, in the schema's own child order - bar-style,
    then ending, then repeat (see musicxml._append_barline / issue #134 Rule
    15). `ending` is a pre-built `<ending .../>` fragment from `ending()`
    below, not a bare tuple, so a caller can also pass one built by hand."""
    parts = [f'<barline location="{location}">']
    if style:
        parts.append(f"<bar-style>{style}</bar-style>")
    if ending:
        parts.append(ending)
    if repeat:
        parts.append(f'<repeat direction="{repeat}"/>')
    parts.append("</barline>")
    return "".join(parts)


def ending(number, type):
    return f'<ending number="{number}" type="{type}"/>'


def repeat_forward():
    """A forward repeat's own `<barline location="left">` - heavy-light,
    opening the repeated span."""
    return barline("left", style="heavy-light", repeat="forward")


def repeat_backward():
    """A backward repeat's own `<barline location="right">` - light-heavy,
    closing the repeated span."""
    return barline("right", style="light-heavy", repeat="backward")


def fixture_volta():
    """A repeat with "1." / "2." ending brackets under the staff.

    This is the shape that made joining collinear pieces dangerous: the
    bracket is drawn as two strokes meeting exactly at a barline, each one
    short enough that it only becomes a candidate staff line once they are
    welded, and it lands close enough below the staff to fall inside the
    cluster gap - turning a 6-line group into a 7-line group that was then
    discarded whole, taking its system's music with it."""
    bar = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                note(("G", 4), "quarter"), note(("A", 4), "quarter")], 4.0)
    first = ('<barline location="left"><ending number="1" type="start"/></barline>' + bar
             + '<barline location="right"><bar-style>light-heavy</bar-style>'
               '<ending number="1" type="stop"/><repeat direction="backward"/></barline>')
    second = ('<barline location="left"><ending number="2" type="start"/></barline>' + bar
              + '<barline location="right"><ending number="2" type="stop"/></barline>')
    return score("Guitar", [attributes() + bar, bar, bar, first, second, bar, bar, bar])


def fixture_repeat_structure():
    """Everything volta.pdf does not already cover (issue #134 Rule 15):
    volta.pdf has a backward repeat and two one-bar endings, both closed with
    a hook. This one has a FORWARD repeat opening the span (absent from
    volta, and 238 of the library's repeats are forward), three endings
    rather than two, one of them two bars long (so no `<ending>` is written
    on its own interior measure), an OPEN-hook ending (`discontinue`), a
    `light-light` double barline mid-score, and a closing `light-heavy` final
    barline."""
    bar = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                note(("G", 4), "quarter"), note(("A", 4), "quarter")], 4.0)
    m1 = repeat_forward() + bar
    m3 = (barline("left", ending=ending(1, "start")) + bar
          + barline("right", style="light-heavy", repeat="backward",
                    ending=ending(1, "stop")))
    m4 = barline("left", ending=ending(2, "start")) + bar
    m5 = bar + barline("right", ending=ending(2, "discontinue"))
    m6 = bar + barline("right", style="light-light")
    m7 = (barline("left", ending=ending(3, "start")) + bar
          + barline("right", ending=ending(3, "stop")))
    m8 = bar + barline("right", style="light-heavy")
    return score("Guitar", [attributes() + m1, bar, m3, m4, m5, m6, m7, m8])


def fixture_adjacent_endings():
    """The one shape neither volta.pdf nor repeat_structure.pdf reaches
    (issue #134 adversarial review, item 9): an ending that discontinues
    (open hook - no downward jog drawn) with the VERY NEXT ending's own
    bracket starting at that same barline, no bar in between, on the SAME
    engraved system.

    `_associate_voltas`' `has_right_hook` decides "stop" vs "discontinue" by
    looking for ANY hook near the bracket's own drawn right end that is not
    its own left hook (see the comment there) - which is exactly the
    discriminator the next bracket's OWN opening hook could defeat if it
    sits close enough. Every fixture and every library score sampled so far
    happens to draw a bar's width of clearance (or a hook) between one
    ending's close and the next one's open, so the assertion that reads
    "discontinue" here has never actually been forced to tell the two
    apart - see fixture_repeat_structure, whose ending 2 discontinues into a
    PLAIN bar (measure 6) before ending 3 opens, and volta.pdf, whose
    adjacent ending closes with a hook (`stop`), not without one
    (`discontinue`).

    Ending 1 discontinues directly into ending 2's own opening barline -
    deliberately the FIRST pair in the piece rather than the second: this
    engraver's line breaks land measures 1-5 on one system and measure 6
    onward on the next (confirmed against repeat_structure.pdf, which wraps
    at exactly the same point with the same bar widths) regardless of which
    endings sit where, so putting the adjacency any later would put the two
    brackets on DIFFERENT systems - not abutting at all, and a different
    (also real, already covered by blocker 1's own fixtures) case."""
    bar = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                note(("G", 4), "quarter"), note(("A", 4), "quarter")], 4.0)
    m1 = repeat_forward() + bar
    m3 = (barline("left", ending=ending(1, "start")) + bar
          + barline("right", ending=ending(1, "discontinue")))
    # No bar, no double barline, nothing between ending 1's discontinue
    # above and ending 2's own opening hook right here - the abutting case.
    m4 = (barline("left", ending=ending(2, "start")) + bar
          + barline("right", style="light-heavy", repeat="backward",
                    ending=ending(2, "stop")))
    m6 = (barline("left", ending=ending(3, "start")) + bar
          + barline("right", ending=ending(3, "stop")))
    m8 = bar + barline("right", style="light-heavy")
    return score("Guitar", [attributes() + m1, bar, m3, m4, bar, m6, bar, m8])


def direction(words=None, symbol=None, sound=None):
    """One navigation `<direction>` (issue #134 Rule 16). `symbol` is "segno"
    or "coda"; `words` is the printed instruction; `sound` is a dict of
    playback attributes ({"dacapo": "yes"}, {"tocoda": "coda"}, ...)."""
    inner = f"<{symbol}/>" if symbol else f"<words>{words}</words>"
    attrs = "".join(f' {k}="{v}"' for k, v in sorted((sound or {}).items()))
    tail = f"<sound{attrs}/>" if attrs else ""
    return (f'<direction placement="above"><direction-type>{inner}'
            f"</direction-type>{tail}</direction>")


def fixture_navigation():
    """Navigation marks: a segno, a "To Coda", a "D.S. al Coda", the coda
    sign itself, a "Fine" and a "D.C. al Fine" (issue #134 phase 2).

    Deliberately carries BOTH signs. This said the library drew "155 coda
    signs across 142 files and not one segno"; it draws 156 coda signs
    across 143 files and 88 segnos across 84 files, and the zero was a
    mislabelled row in the Maestro glyph table
    (see Rule 16, which retracts the claim). What the fixture still supplies
    that the library cannot is the OTHER route to a segno: MuseScore draws it
    as a published SMuFL codepoint, where every library segno is a Finale
    glyph ID, and the two are decoded by completely separate code paths.

    It carries every placement the reader has to tell apart:

    - a SIGN at the head of a bar (segno on 1, coda on 6) against an
      INSTRUCTION at the end of one (To Coda on 2, D.S. on 4, Fine on 7,
      D.C. on 8) - the difference between "before" and "after" in
      musicxml.build's `directions`, and between containment and
      boundary-snap in tabextract._apply_nav_marks;
    - an instruction written at the very end of its bar (2, 4, 7), which
      MuseScore engraves LEFT-aligned at the barline so its text runs on
      into the next bar - the alignment the library's own Finale
      engravings never use, and the one that made anchoring by the text's
      right edge alone wrong by exactly one bar;
    - an instruction written a beat BEFORE the end of its bar (8), which
      lands the text inside the bar it names rather than across its
      boundary. That one is on the last bar on purpose - a real "D.C. al
      Fine" is the last thing on the page - and is written a beat early
      because MuseScore truncates a string that would cross the page edge:
      engraved after the final note it comes out as "D.C. al Fin", which
      is an engraver's limit rather than anything this decoder does.

    No repeat barline and no volta bracket: the two features have to be
    readable independently, and repeat_structure.pdf already covers those
    with no navigation mark on it at all.
    """
    seq = [note(("E", 4), "quarter"), note(("F", 4), "quarter"),
           note(("G", 4), "quarter"), note(("A", 4), "quarter")]
    bar = _bar(seq, 4.0)
    m1 = direction(symbol="segno", sound={"segno": "segno"}) + bar
    m2 = bar + direction(words="To Coda", sound={"tocoda": "coda"})
    m4 = bar + direction(words="D.S. al Coda", sound={"dalsegno": "segno"})
    m6 = direction(symbol="coda", sound={"coda": "coda"}) + bar
    m7 = bar + direction(words="Fine", sound={"fine": "yes"})
    # The instruction goes between the third and fourth notes of the
    # notation staff's voice, so it is engraved a beat inside the bar - see
    # the docstring. The tablature staff mirrors the bar's notes only; a
    # direction belongs to the measure, not to a staff, and writing it twice
    # would engrave it twice.
    m8 = ("".join(seq[:3]) + direction(words="D.C. al Fine", sound={"dacapo": "yes"})
          + seq[3] + backup(4.0) + mirror_to_tab("".join(seq))
          + barline("right", style="light-heavy"))
    return score("Guitar", [attributes() + m1, m2, bar, m4, bar, m6, m7, m8])


def fixture_harmonics_dense():
    """Diamond noteheads - harmonics - on a densely written two-voice system.

    SMUFL_CODE_MAP deliberately does not carry the diamond noteheads, so
    these are unrecognised, and that is the point: on a sparse system two of
    them are a fifth of the glyphs and degrade confidence through the
    unknown ratio, while on a system this dense they are three percent and a
    ratio cannot see them at all. What actually happens to the music is
    worse than the missing warning - the voice above loses beats to an
    invented rest and the harmonics' own tab digits are attached to the
    voice below - so an unrecognised notehead has to be reported however few
    of them there are."""
    def system(harmonics):
        upper = [note(("E", 5), "eighth", voice=1) for _ in range(8)]
        for index in harmonics:
            upper[index] = note(("E", 5), "eighth", voice=1, head="diamond")
        lower = [note(("E", 4), "quarter", voice=2) for _ in range(4)]
        body = "".join(upper) + backup(4.0) + "".join(lower)
        return body + backup(4.0) + mirror_to_tab(body)

    plain = system(())
    # TWO harmonics in the whole score, not two per bar: the point is a
    # density at which the unknown-glyph ratio stays under its threshold and
    # says nothing, so there have to be few of them among many.
    return score("Guitar", [attributes() + system((3, 6))] + [plain] * 7)


def fixture_tab_only_short_last_system():
    """Eight bars of tablature, which MuseScore lays out as six bars and
    then two - and a two-bar system is not stretched to the page width.

    Engraved as a tripwire for a real limitation of detecting a staff by the
    length of its lines: this system's lines fell under the length floor and
    the system was not detected at all, so two bars of music were lost with
    nothing said. `tab_only` is twelve bars precisely to avoid it.

    The tripwire has since fired. Issue #152 is the same defect at library
    scale - the floor also hid the right-hand coda system on 54 systems
    across the maintainer's library - and all eight bars are now read (see
    tabextract.SHORT_STAFF_LEN_RATIO). The fixture stays exactly as it is,
    because a short final system is still the shape that would be lost if
    that floor were ever raised again."""
    bar = "".join([note(("E", 4), "quarter", staff=1), note(("F", 4), "eighth", staff=1),
                   note(("G", 4), "eighth", staff=1), note(("A", 4), "half", staff=1)])
    return score("Guitar", [attributes(staves=1) + bar] + [bar] * 7)


def fixture_notation_only():
    """Standard notation with no tablature, on a plain treble clef. Not
    extractable, and the reason matters: fingering numbers on a notation
    staff are not fret numbers, and reporting them as a transcription would
    be worse than refusing."""
    bar = "".join([note(("E", 4), "quarter"), note(("F", 4), "eighth"),
                   note(("G", 4), "eighth"), note(("A", 4), "half")])
    measures = [attributes(staves=1, tab=False, octave_clef=False) + bar] + [bar] * 7
    return score("Flute", measures, program=74)


def fixture_rests_and_flags():
    """Every rest value and every flag hook the SMuFL map calibrates.

    A SMuFL font spells a rest's value in the glyph itself, unlike Maestro
    and Opus which draw the half and whole rest with one glyph and leave the
    reader to tell them apart by which line it hangs from - so these rests
    are the branch that trusts the engraving instead of guessing at a
    position. The flagged notes are separated by rests on purpose: beamed,
    the duration would come from counting beam strokes and no flag glyph
    would be drawn at all, and the 32nd flag in particular is the difference
    between a 32nd and a quarter."""
    b1 = _bar([rest("quarter"), note(("E", 4), "quarter"), note(("G", 4), "half")], 4.0)
    b2 = _bar([rest("eighth"), note(("E", 4), "eighth"),
               rest("16th"), note(("F", 4), "16th"),
               rest("16th"), note(("G", 4), "16th"),
               note(("A", 4), "half")], 4.0)
    b3 = _bar([rest("whole")], 4.0)
    b4 = _bar([rest("half"), note(("B", 4), "half")], 4.0)
    b5 = _bar(sum(([note(("E", 4), "32nd"), rest("32nd")] for _ in range(4)), [])
              + [note(("G", 4), "quarter"), note(("A", 4), "half")], 4.0)
    b6 = _bar([note(("E", 4), "quarter")] * 4, 4.0)
    return score("Guitar", [attributes() + b1, b2, b3, b4, b5, b6, b1, b2])


def fixture_thirty_second_beams():
    """Values shorter than a 16th, written both ways an engraver writes them:
    under a beam, and on a flag.

    A beam GROUP is a stack of strokes - three of them for a 32nd - that
    starts at the stem's tip and grows toward the notehead. `rests_and_flags`
    already covers the flag side of that vocabulary, but it separates every
    32nd from the next with a rest, so no beam is ever drawn over one and
    nothing in this repository engraved a three-stroke beam at all. That is
    the shape issue #113 is about: the decoder read only the two strokes
    nearest the tip and emitted every note under one at twice its written
    length, which is silent, internally consistent, and the most damaging
    kind of duration error there is.

    Both halves are here on purpose, because it is the PAIR that says where
    the defect lives. Bars 1-3 and 5 are beamed and were wrong; bar 4 writes
    two 32nds that are adjacent on the page but land in different beam
    groups, so the engraver flags them instead, and those were already right.
    A fixture with only the beamed half would leave "adjacent 32nds are
    misread" as the diagnosis, and that diagnosis is wrong.

    Bar 1 is the figure the library actually prints - a dotted eighth and two
    32nds filling one beat - which is what nearly every one of the bars this
    fixed on real pages turned out to be.

    A four-stroke beam is deliberately NOT here. The rule follows a stack to
    any depth and counts one, but nothing downstream can carry the answer:
    the emitter's whole duration vocabulary stops at a 32nd (musicxml
    TYPE_NAMES), so a 64th comes out as a 32nd however well it was read. A
    fixture bar asserting that would be documenting a different limit. The
    four-deep case is covered on constructed geometry in test_glyph_rhythm.py
    instead, where the beam count can be read directly.

    Written with 16 divisions per quarter, the first fixture to need more
    than 4: at 4 a 32nd's duration rounds to zero, which is what
    `rests_and_flags` writes for each of its (see `divisions`).

    ONE bar to a system, which no other fixture asks for and this one needs.
    Left to fill the page MuseScore puts four bars on a system, and with 32nds
    that close together the decoder reads a beamed pair as a single chord and
    drops the note after it - a coincident-onset defect landing on top of the
    one under test. Given a system each, the bars space out like the
    library's own pages do and every bar reads back exactly as written. This
    was chosen by engraving the alternatives and reading them back, not by
    eye."""
    with divisions(16):
        system = '<print new-system="yes"/>'
        b1 = _bar([note(("E", 4), "eighth", dots=1), note(("F", 4), "32nd"),
                   note(("G", 4), "32nd"), note(("A", 4), "quarter"),
                   note(("B", 4), "half")], 4.0)
        b2 = _bar([note(("E", 4), "32nd"), note(("F", 4), "32nd"),
                   rest("16th"), rest("eighth"), note(("A", 4), "quarter"),
                   note(("B", 4), "half")], 4.0)
        b3 = _bar([note(("E", 4), "32nd"), note(("F", 4), "32nd"),
                   note(("G", 4), "32nd"), note(("A", 4), "32nd"),
                   rest("eighth"), rest("quarter"), note(("B", 4), "half")], 4.0)
        # One 32nd closing the first beat and one opening the second. They
        # touch, but a beam never crosses a beat here, so each is drawn with
        # a flag of its own.
        b4 = _bar([rest("eighth"), rest("16th"), rest("32nd"),
                   note(("E", 4), "32nd"), note(("F", 4), "32nd"),
                   rest("32nd"), rest("16th"), rest("eighth"),
                   note(("B", 4), "half")], 4.0)
        b5 = _bar([note(("E", 4), "quarter")] * 4, 4.0)
        bars = [b1, b2, b3, b4, b5, b1, b2, b3]
        return score("Guitar", [attributes() + bars[0]]
                     + [system + b for b in bars[1:]])


def fixture_multidigit_meter():
    """A meter whose numerator needs TWO digit glyphs stacked at the same x
    column - 12/8 - which is exactly the shape a single missing digit in a
    font's calibration table breaks (issue #84): lose the '1' and the
    numerator's remaining glyph, a lone '2', is still a perfectly plausible
    single-digit numerator, so the failure is a confident WRONG meter, not a
    detected gap. Nothing before this fixture engraved a real two-digit
    numerator through the full PDF pipeline - the multi-digit clustering
    itself was only ever exercised with hand-built glyph coordinates (see
    test_glyph_rhythm.py), never against actual font glyphs extracted from a
    real page. Twelve eighths per bar (6.0 quarters) makes a wrong reading
    show up in the bar arithmetic too, not only in the reported meter."""
    bar = _bar([note(("E", 4), "eighth"), note(("F", 4), "eighth"),
                note(("G", 4), "eighth"), note(("A", 4), "eighth"),
                note(("B", 4), "eighth"), note(("A", 4), "eighth"),
                note(("G", 4), "eighth"), note(("F", 4), "eighth"),
                note(("E", 4), "eighth"), note(("D", 4), "eighth"),
                note(("E", 4), "eighth"), note(("D", 4), "eighth")], 6.0)
    return score("Guitar", [attributes(time=(12, 8)) + bar] + [bar] * 7)


def fixture_four_sharps_in_three_four():
    """Four sharps between the clef and the meter, and a meter that is not
    4/4.

    The key signature is what makes this fixture: the clef, four accidentals
    and the meter in a row put the meter's digits about ten and a half staff
    spaces into the staff, past the eight-and-a-bit that a clef and a meter
    alone occupy. A window measured from the staff's left edge never reaches
    them, so the printed meter is not found and the whole score is barred as
    4/4 - which is why this one is written in 3/4, so that failing to read it
    misplaces every barline instead of landing on the same answer by luck.
    Measured on one real library, this shape lost the printed meter on 49 of
    292 first pages."""
    bar = _bar([note(("E", 4), "quarter"), note(("F", 4, 1), "eighth"),
                note(("G", 4, 1), "eighth"), note(("A", 4), "quarter")], 3.0)
    last = _bar([note(("E", 4), "half", dots=1)], 3.0)
    return score("Guitar", [attributes(fifths=4, time=(3, 4)) + bar]
                 + [bar] * 6 + [last])


def fixture_hidden_opening_meter():
    """A score whose opening meter is engraved invisibly and which then
    prints a change to 3/4 at a later system.

    The opening meter cannot be read here for a reason no window can fix: it
    is not on the page. That makes it the case that shows what happens when
    ONE staff of a score decodes and the first one does not - the later 3/4
    used to be adopted as the score's opening meter, at "read directly from
    the digit glyphs" confidence, and every earlier bar was measured and
    emitted in 3/4 against music written in 4/4. It also used to suppress the
    warning that says the meter changes, because only one meter had been
    recorded and one meter is not a change.

    The 4/4 bars hold four quarters and the 3/4 bars three, so barring them
    in the wrong meter is visible in the conformance figures and not only in
    what is reported about the meter."""
    four = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                 note(("G", 4), "quarter"), note(("A", 4), "quarter")], 4.0)
    three = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                  note(("G", 4), "quarter")], 3.0)
    change = ('<print new-system="yes"/><attributes>'
              + time_signature((3, 4)) + "</attributes>")
    measures = ([attributes(time_printed=False) + four] + [four] * 3
                + [change + three] + [three] * 3)
    return score("Guitar", measures)


def fixture_hidden_opening_meter_matches_the_default():
    """The other direction from fixture_hidden_opening_meter: the opening
    meter is invisible, decoding it fails, and the only meter read anywhere
    in the score is a later, explicit 4/4 - printed again at the second
    system for no musical reason, purely so there is something for the
    decoder to read - which happens to be exactly what "assumed 4/4" already
    guesses. The "meter printed at the start of this score was not read"
    warning must not fire here: there is no discrepancy between the assumed
    opening and the meter that was read, so saying so would be noise rather
    than a caveat worth a reader's attention (PR #122's review, finding
    F10). Every bar is in 4/4 throughout, so the "changes time signature"
    warning must stay quiet too."""
    four = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                 note(("G", 4), "quarter"), note(("A", 4), "quarter")], 4.0)
    change = ('<print new-system="yes"/><attributes>'
              + time_signature((4, 4)) + "</attributes>")
    measures = ([attributes(time_printed=False) + four] + [four] * 3
                + [change + four] + [four] * 3)
    return score("Guitar", measures)


def fixture_mid_system_meter_change():
    """A meter change engraved part-way ALONG a system, not at its start.

    An engraver prints a change where it takes effect, which is wherever the
    barline is - the middle of a system as often as its start. The bars ahead
    of it in that same system are still in the previous meter, so a meter
    resolved once per system is the wrong meter for some of them: their
    budget is wrong, they are measured against a length nobody wrote, and a
    voice that falls short of it gets padded with silence towards a meter it
    is not in.

    Every bar here adds up exactly to its own printed meter, and the 2/4 bars
    hold half what the 4/4 bars do, so a bar budgeted against the system's
    meter instead of its own cannot help but show up as defective. The system
    break before the second 4/4 is explicit so that the first system holds
    both of the first two meters - which is the whole point of the
    fixture."""
    four = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                 note(("G", 4), "quarter"), note(("A", 4), "quarter")], 4.0)
    two = _bar([note(("B", 4), "quarter"), note(("A", 4), "quarter")], 2.0)
    to_two = "<attributes>" + time_signature((2, 4)) + "</attributes>"
    back = ('<print new-system="yes"/><attributes>'
            + time_signature((4, 4)) + "</attributes>")
    measures = [attributes() + four, four, to_two + two, two,
                back + four, four, four, four]
    return score("Guitar", measures)


def fixture_mid_system_key_and_meter_change():
    """A meter change engraved at the SAME barline as a key change, part-way
    along a system - the mid-system counterpart of
    fixture_four_sharps_in_three_four.

    Four sharps printed right after the barline push the numerator's own
    left edge out exactly as they do at a staff's own start: a mid-system
    reader sized only for "nothing between the barline and the meter" drops
    this the same way the opening reader used to drop a key-signature-fronted
    meter (issue #90). The change is to 3/4 so a bar barred in the wrong
    meter is visible in the conformance figures rather than landing on the
    right answer by luck, and every bar adds up exactly to its own printed
    meter."""
    four = _bar([note(("E", 4), "quarter"), note(("F", 4), "quarter"),
                 note(("G", 4), "quarter"), note(("A", 4), "quarter")], 4.0)
    three = _bar([note(("F", 4, 1), "quarter"), note(("G", 4, 1), "quarter"),
                  note(("A", 4), "quarter")], 3.0)
    to_three_sharps = ("<attributes><key><fifths>4</fifths></key>"
                        + time_signature((3, 4)) + "</attributes>")
    measures = [attributes() + four, four, to_three_sharps + three,
                three, three, three, three, three]
    return score("Guitar", measures)


def fixture_stacked_dotted_chord():
    """A three-note chord on one stem, every member an identical dotted half:
    E4, then a third up to G4 (both on lines, a third apart), then a second
    further to A4. G4 sits close enough to A4 that MuseScore shifts G4's own
    notehead left of the shared stem column to keep the two from touching -
    the offset #112 describes - and that shift carries G4's own dot glyph
    out of this decoder's x-reach entirely: no notehead is close enough to
    it, on `main` or fixed.

    That is not, on its own, this issue's defect - it is a genuine, orphaned
    dot. The defect is what "main" does with A4's OWN dot once G4's is
    unreachable: nearest-distance has nothing else nearby to rank A4's dot
    against, so A4 takes it, and separately takes the *next* dot along too
    (the one at the offset A4's own tier ranking prefers) with no check that
    A4 already has one. A4 ends up with two dots from two different relative
    positions - not a real double dot, which is two ink marks at the SAME
    position - and the chord reads as double-dotted (3.5 quarters) instead
    of the 3 every member is actually written as, since a chord shares one
    duration for all its members. Refusing to let an owner already given a
    dot at one tier take a second, different tier leaves A4 with its own one
    dot and reports G4's orphaned dot rather than inventing a home for it -
    the anomaly path, exercised here by the same fixture rather than only a
    synthetic one."""
    bar = _bar([note(("E", 4), "half", dots=1),
                note(("G", 4), "half", dots=1, chord=True),
                note(("A", 4), "half", dots=1, chord=True),
                note(("E", 4), "quarter")], 4.0)
    return score("Guitar", [attributes() + bar] + [bar] * 7)


def fixture_double_dotted_note():
    """A genuine DOUBLE dot: two ink marks side by side after one notehead
    (issue #111). A double-dotted half is 3.5 quarters, so an eighth fills the
    4/4 bar behind it.

    The second dot is the point. It sits one dot-advance beyond the first,
    which is always further from the notehead than a reach window anchored on
    the notehead can be - the window would have to be wider than the gap
    between two adjacent noteheads to cover it. Reading this note as
    single-dotted loses half a quarter and starts everything after it early.

    Every other bar is a SINGLE-dotted half instead, as the control: a fix
    that merely widened the reach window would give that note a second dot
    too, and only a fix that reads the two marks as one note's own leaves it
    alone."""
    two = _bar([note(("G", 4), "half", dots=2),
                note(("E", 4), "eighth")], 4.0)
    one = _bar([note(("G", 4), "half", dots=1),
                note(("E", 4), "quarter")], 4.0)
    return score("Guitar", [attributes() + two, one] + [two, one] * 3)


def fixture_seconds_interval_dots():
    """Two dotted notes a SECOND apart, both ways round (issue #112).

    Two noteheads a second apart cannot share a column, so one of the two is
    moved a full notehead width off it - the upper one, right of the stem, in
    the stem-up chord of the first bar; the lower one, left of the stem, in
    the stem-down chord of the second. Their two dots do not move with it:
    both stay in the chord's single dot column. So in each bar one of the two
    heads is a whole notehead width further from its own dot than from the
    other member's, and that member silently lost its dot - making a chord
    whose members must share one duration read as two different ones.

    The two bars sit at opposite ends of the staff so the engraver picks a
    different stem direction for each, which is what decides WHICH of the two
    heads is moved off the column. G3 and A3 are the low pair because they
    fall on two different strings - a second whose members share one string
    cannot be played as a chord, and its tablature would be nonsense."""
    low = _bar([note(("G", 3), "half", dots=1),
                note(("A", 3), "half", dots=1, chord=True),
                note(("E", 4), "quarter")], 4.0)
    high = _bar([note(("F", 5), "half", dots=1),
                 note(("G", 5), "half", dots=1, chord=True),
                 note(("E", 4), "quarter")], 4.0)
    return score("Guitar", [attributes() + low, high] + [low, high] * 3)


def fixture_double_dotted_in_chord():
    """A double-dotted note stacked under a second voice, whose noteheads sit
    at exactly the height its two dots are drawn at (issue #131, #111).

    The lower voice carries a double-dotted half (3.5 quarters, plus an
    eighth) on a staff line, so its dots go where an engraver's default puts
    them - the space above the note. The upper voice's quarter notes are a
    SECOND above it, which puts their noteheads in that very space, level with
    the dots to within a hundredth of a staff space.

    So the two dots are as close vertically to a note that has none as to the
    note that owns them, and the only thing separating the two readings is
    that a note's dots are drawn to ITS OWN right, in reach of it and out of
    reach of the note above. Both dots must count for the half note - 3.5
    quarters, not 3 - and neither may attach to the voice above."""
    bar = _bar([note(("G", 4), "quarter", voice=1),
                note(("G", 4), "quarter", voice=1),
                note(("G", 4), "quarter", voice=1),
                note(("G", 4), "quarter", voice=1),
                backup(4.0),
                note(("F", 4), "half", dots=2, voice=2),
                note(("F", 4), "eighth", voice=2)], 4.0)
    return score("Guitar", [attributes() + bar] + [bar] * 7)


FIXTURES = {
    "notation_and_tab": fixture_notation_and_tab,
    "stacked_dotted_chord": fixture_stacked_dotted_chord,
    "double_dotted_note": fixture_double_dotted_note,
    "seconds_interval_dots": fixture_seconds_interval_dots,
    "double_dotted_in_chord": fixture_double_dotted_in_chord,
    "four_sharps_in_three_four": fixture_four_sharps_in_three_four,
    "hidden_opening_meter": fixture_hidden_opening_meter,
    "hidden_opening_meter_matches_the_default": fixture_hidden_opening_meter_matches_the_default,
    "mid_system_meter_change": fixture_mid_system_meter_change,
    "mid_system_key_and_meter_change": fixture_mid_system_key_and_meter_change,
    "multidigit_meter": fixture_multidigit_meter,
    "rests_and_flags": fixture_rests_and_flags,
    "thirty_second_beams": fixture_thirty_second_beams,
    "tab_only": fixture_tab_only,
    "tab_only_short_last_system": fixture_tab_only_short_last_system,
    "two_voices": fixture_two_voices,
    "unison_voices": fixture_unison_voices,
    "unison_in_chord": fixture_unison_in_chord,
    "three_voices": fixture_three_voices,
    "tuplet_and_tie": fixture_tuplet_and_tie,
    "drop_d": fixture_drop_d,
    "defective_bars": fixture_defective_bars,
    "volta": fixture_volta,
    "repeat_structure": fixture_repeat_structure,
    "adjacent_endings": fixture_adjacent_endings,
    "navigation": fixture_navigation,
    "harmonics_dense": fixture_harmonics_dense,
    "notation_only": fixture_notation_only,
}

# Rasterised from an engraved fixture rather than engraved itself: no
# engraver emits a scan. It is what a photographed or scanned edition looks
# like, and the one thing extraction must refuse outright.
RASTER_FROM = "notation_and_tab"
RASTER_NAME = "raster_scan"
RASTER_DPI = 96

# Synthesised rather than engraved, because no engraver produces it on
# purpose: a page whose "music font" is an unembedded text font drawing the
# letters A-H, with a ToUnicode CMap claiming they are SMuFL music symbols
# as its only qualification. Decoding from a codepoint means trusting a
# number the producer wrote, so there has to be a page that abuses exactly
# that, and it has to keep being refused. Confirmed to decode as
# rhythm-from-glyphs before the corroboration requirements went in.
FAKE_FONT_NAME = "fake_music_font"
FAKE_FONT_CODES = (0xE0A4, 0xE0A3, 0xE052, 0xE084, 0xE0A4, 0xE0A4, 0xE0A4, 0xE1E7)
FAKE_FONT_LETTERS = "ABCDEFGH"

# Which tool made each committed PDF, asserted by a test. A fixture
# re-engraved by a DIFFERENT version lands different coordinates, and the
# assertions here measure coordinates - so the version has to be part of what
# is checked, not only written down in the README. The MusicXML has its own,
# stronger guard: it is regenerated and compared byte for byte.
ENGRAVER_CREATOR = "MuseScore Studio Version: 4.6.3"
SYNTHESISED_CREATOR = "fermata engrave_fixtures.py"
SYNTHESISED = (RASTER_NAME, FAKE_FONT_NAME)


# ---------------------------------------------------------------------------
# Driving the engraver
# ---------------------------------------------------------------------------

MUSESCORE_CANDIDATES = (
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    "/usr/bin/mscore",
    "/usr/bin/musescore",
    "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
)


def find_musescore():
    named = os.environ.get("MUSESCORE")
    if named:
        return named if Path(named).is_file() else None
    for name in ("mscore", "musescore", "MuseScore4"):
        found = shutil.which(name)
        if found:
            return found
    for path in MUSESCORE_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def write_musicxml(check_only=False):
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    drifted = []
    for name, build in sorted(FIXTURES.items()):
        path = FIXTURE_DIR / f"{name}.musicxml"
        wanted = build()
        if check_only:
            have = path.read_text(encoding="utf-8") if path.is_file() else None
            if have != wanted:
                drifted.append(name)
            continue
        path.write_text(wanted, encoding="utf-8")
        print(f"  wrote {path.name} ({len(wanted)} bytes)")
    return drifted


def engrave(musescore):
    for name in sorted(FIXTURES):
        src = FIXTURE_DIR / f"{name}.musicxml"
        pdf = FIXTURE_DIR / f"{name}.pdf"
        subprocess.run([musescore, "-o", str(pdf), str(src)], check=True,
                       capture_output=True, timeout=300)
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise SystemExit(f"{musescore} produced no PDF for {name}")
        print(f"  engraved {pdf.name} ({pdf.stat().st_size} bytes)")


def synthesise_fake_music_font():
    """Draw a plausible-looking staff whose only music credential is a lie."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    xs = (90, 140, 215, 265, 340, 390, 465, 515)
    for i in range(5):
        page.draw_line((60, 120 + i * 5.125), (560, 120 + i * 5.125), width=0.5)
    for i in range(6):
        page.draw_line((60, 180 + i * 7.4), (560, 180 + i * 7.4), width=0.5)
    for x in (60, 185, 310, 435, 560):
        page.draw_line((x, 120), (x, 120 + 4 * 5.125), width=0.6)
        page.draw_line((x, 180), (x, 180 + 5 * 7.4), width=0.6)
    for i, x in enumerate(xs):
        page.insert_text((x, 180 + (i % 6) * 7.4 + 2.5), str(i % 5), fontsize=7, fontname="helv")
    for letter, x in zip(FAKE_FONT_LETTERS, xs):
        page.insert_text((x, 130), letter, fontsize=18, fontname="tiro")
    raw = FIXTURE_DIR / f"{FAKE_FONT_NAME}.raw.pdf"
    doc.save(raw)
    doc.close()

    doc = fitz.open(raw)
    entries = "\n".join(f"<{ord(l):04X}> <{c:04X}>"
                        for l, c in zip(FAKE_FONT_LETTERS, FAKE_FONT_CODES))
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CMapName /Fake-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <00FF>\nendcodespacerange\n"
        f"{len(FAKE_FONT_CODES)} beginbfchar\n{entries}\nendbfchar\nendcmap\nend\nend\n"
    ).encode()
    cm_xref = doc.get_new_xref()
    doc.update_object(cm_xref, f"<</Length {len(cmap)}>>")
    doc.update_stream(cm_xref, cmap)

    patched = 0
    for xref in range(1, doc.xref_length()):
        obj = doc.xref_object(xref, compressed=True)
        if "/Type/Font" in obj.replace(" ", "") and "Times" in obj:
            doc.update_object(xref, obj.rstrip()[:-2] + f"/ToUnicode {cm_xref} 0 R>>")
            patched += 1
    if patched != 1:
        raise SystemExit(f"expected one text font to patch, found {patched}")
    path = FIXTURE_DIR / f"{FAKE_FONT_NAME}.pdf"
    doc.set_metadata({"creator": SYNTHESISED_CREATOR,
                      "title": "a text font claiming its letters are music symbols"})
    doc.save(path)
    doc.close()
    raw.unlink(missing_ok=True)
    print(f"  synthesised {path.name} ({path.stat().st_size} bytes)")


def rasterise():
    """Flatten one engraved fixture to a page-sized image in a PDF wrapper."""
    import fitz

    src = fitz.open(FIXTURE_DIR / f"{RASTER_FROM}.pdf")
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=RASTER_DPI)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
    path = FIXTURE_DIR / f"{RASTER_NAME}.pdf"
    out.set_metadata({"creator": SYNTHESISED_CREATOR,
                      "title": f"{RASTER_FROM} rasterised at {RASTER_DPI} dpi"})
    out.save(path, deflate=True)
    print(f"  rasterised {path.name} ({path.stat().st_size} bytes)")


def report():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from fermata import tabextract

    for name in sorted(list(FIXTURES) + [RASTER_NAME]):
        pdf = FIXTURE_DIR / f"{name}.pdf"
        if not pdf.is_file():
            print(f"{name}: MISSING")
            continue
        r = tabextract.extract(pdf)
        print(f"\n{name}: extractable={r.extractable} reason={r.reason}")
        if not r.extractable:
            continue
        print(f"  pages={r.pages_processed} staves tab/std={r.tab_staff_count}/"
              f"{r.standard_staff_count} bars={r.bars} notes={r.notes} beats={r.beats}")
        print(f"  ts={r.time_signature} ({r.time_signature_source}) "
              f"key={r.key_fifths} ({r.key_signature_source}) tempo={r.tempo} "
              f"tuning={r.tuning_label}")
        print(f"  rhythm={r.rhythm_provenance} bars over/short/defective/measured="
              f"{r.bars_overfull}/{r.bars_short}/{r.bars_defective}/{r.bars_measured}")
        for w in r.warnings:
            print(f"    ! {w}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--musicxml", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    if args.check:
        drifted = write_musicxml(check_only=True)
        if drifted:
            raise SystemExit(
                "these fixtures' committed MusicXML no longer matches this script: "
                + ", ".join(drifted))
        print("every committed fixture MusicXML matches this script")
        return

    print("MusicXML:")
    write_musicxml()
    if args.musicxml:
        return

    musescore = find_musescore()
    if musescore is None:
        raise SystemExit(
            "no MuseScore found - set $MUSESCORE to its executable. The committed "
            "PDFs are unchanged; only the MusicXML was rewritten.")
    print(f"engraving with {musescore}:")
    engrave(musescore)
    rasterise()
    synthesise_fake_music_font()
    if args.report:
        report()


if __name__ == "__main__":
    main()
