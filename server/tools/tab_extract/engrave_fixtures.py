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
import os
import shutil
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "engraved"

DIVISIONS = 4  # per quarter note: 16ths are the shortest value used here

TYPE_QUARTERS = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5,
                 "16th": 0.25, "32nd": 0.125}

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

def rest(typ, dots=0, staff=1, voice=1):
    quarters = TYPE_QUARTERS[typ] * (1.5 ** dots if dots else 1)
    return (f"<note><rest/><duration>{int(round(quarters * DIVISIONS))}</duration>"
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
    quarters = TYPE_QUARTERS[typ] * (1.5 ** dots if dots else 1)
    if tuplet:
        quarters = quarters * tuplet[1] / tuplet[0]
    out = ["<note>"]
    if chord:
        out.append("<chord/>")
    out.append(f"<pitch><step>{step}</step>{alter}<octave>{octave}</octave></pitch>")
    out.append(f"<duration>{int(round(quarters * DIVISIONS))}</duration>")
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
    return f"<backup><duration>{int(round(quarters * DIVISIONS))}</duration></backup>"


def mirror_to_tab(body):
    """The same notes again on the tablature staff.

    A tab staff in MusicXML is not a view of the notation staff, it is its
    own staff carrying its own notes, so a score that shows both writes each
    note twice. Rewriting the notation staff's own text is what keeps the
    two halves from drifting apart as these fixtures are edited."""
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
    out = [f"<attributes><divisions>{DIVISIONS}</divisions>",
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
    then two - and a two-bar system is not stretched to the page width, so
    its staff lines fall under the length floor and the system is not
    detected at all. Two bars of music are lost with nothing said.

    This is a real limitation of detecting a staff by the length of its
    lines, and it is engraved here so that fixing it, or making it worse,
    changes something. `tab_only` is twelve bars precisely to avoid it."""
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


FIXTURES = {
    "notation_and_tab": fixture_notation_and_tab,
    "stacked_dotted_chord": fixture_stacked_dotted_chord,
    "four_sharps_in_three_four": fixture_four_sharps_in_three_four,
    "hidden_opening_meter": fixture_hidden_opening_meter,
    "hidden_opening_meter_matches_the_default": fixture_hidden_opening_meter_matches_the_default,
    "mid_system_meter_change": fixture_mid_system_meter_change,
    "mid_system_key_and_meter_change": fixture_mid_system_key_and_meter_change,
    "rests_and_flags": fixture_rests_and_flags,
    "tab_only": fixture_tab_only,
    "tab_only_short_last_system": fixture_tab_only_short_last_system,
    "two_voices": fixture_two_voices,
    "tuplet_and_tie": fixture_tuplet_and_tie,
    "drop_d": fixture_drop_d,
    "defective_bars": fixture_defective_bars,
    "volta": fixture_volta,
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
