# A MusicXML profile for guitar tablature

This document describes how Fermata writes guitar tablature as MusicXML, in
enough detail that another program can produce files Fermata reads and read
files Fermata writes.

It is a **profile**, not a format. Every construct used here is defined by
[MusicXML 4.0](https://www.w3.org/2021/06/musicxml40/); nothing is invented,
nothing is namespaced into a private extension, and a file that follows this
profile is an ordinary MusicXML file that opens in any application supporting
tablature. The profile exists because the specification is deliberately
permissive: the same tab bar can be encoded several equally valid ways, and two
programs that each read MusicXML correctly can still fail to agree. Pinning
down one encoding is what makes interoperation predictable.

The rules below are numbered so they can be cited. They are stated as
requirements on a **conforming file**. Where a rule goes beyond what MusicXML
itself requires, that is said explicitly — the specification is silent on
several things this profile has to decide.

## Contents

- [Scope](#scope)
- [Conformance rules](#conformance-rules)
  - [Document form](#document-form-rules-1-3)
  - [Tab staff description](#tab-staff-description-rules-4-5)
  - [Voices](#voices-rules-6-8)
  - [Fret, string and pitch](#fret-string-and-pitch-rules-9-13)
- [Example 1: one monophonic bar](#example-1-one-monophonic-bar)
- [Example 2: two voices in one bar](#example-2-two-voices-in-one-bar)
- [Checking a file](#checking-a-file)
- [Out of scope](#out-of-scope)

## Scope

The profile covers what is needed to represent a fretted-string part read from
an engraved tab score: the staff and its tuning, barlines and meter, note
durations including augmentation dots, rests, chords, concurrent voices, and
the fret, string and sounding pitch of every note.

It covers those things because they are the ones whose encoding is either
ambiguous or easy to get subtly wrong — string numbering above all, which runs
opposite to staff-line numbering and produces a file that validates cleanly
while placing every note on the wrong string.

It does not cover playing techniques whose encoding is settled but whose
rendering varies widely between applications, nor anything a tab-reading
extractor cannot determine. Those are listed under [Out of
scope](#out-of-scope) rather than left as an implied promise.

## Conformance rules

### Document form (Rules 1–3)

**Rule 1.** A conforming file is a `score-partwise` document with
`version="4.0"`, encoded UTF-8, and carries **no DOCTYPE**.

Omitting the DOCTYPE is a deliberate departure from the form the MusicXML 4.0
tutorial shows. Three reasons, all practical:

- The MusicXML DTDs are deprecated as of version 4.0 in favour of the XSD.
- The public identifier's system URL,
  `http://www.musicxml.org/dtds/partwise.dtd`, no longer resolves. A parser
  configured to fetch external DTDs fails outright on a file that names it.
- An external DTD reference is an XXE and denial-of-service surface, so many
  XML parsers refuse such a document by default rather than fetching it.

A `DOCTYPE` therefore costs interoperability instead of buying it. A conforming
**reader**, on the other hand, must tolerate one: a great many real MusicXML
files in circulation carry it, and they are otherwise fine. Ignore the DTD
rather than resolving it.

Declaring the schema location is optional and inert for non-validating readers:

```xml
<score-partwise version="4.0"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:noNamespaceSchemaLocation="http://www.musicxml.org/xsd/musicxml.xsd">
```

**Rule 2.** `<divisions>` is declared once, in the first measure's
`<attributes>`, as a positive integer, and every `<duration>` and `<backup>`
duration in the file is an exact integer multiple of the note values in use.
No measure re-declares it.

`<divisions>` is the number of divisions per quarter note. Its value has to be
chosen so that no duration in the file needs a fraction, because that is what
lets a consumer check [Rule 8](#voices-rules-6-8) with integer arithmetic
instead of comparing floating-point sums. The tightest case in this profile's
duration vocabulary is a double-dotted 32nd note — an eighth of a quarter
times 7/4, so 7/32 of a quarter — which makes 32 the smallest workable value.

Fermata writes **480**. It is a common choice in files produced by notation
software, so the output looks ordinary; it also divides by 3 and 5, which
leaves room for tuplets without a later change of divisions. The specification
notes that a value above 16383 costs Standard MIDI 1.0 compatibility, so stay
below that.

A conforming file may use any value satisfying the rule. A conforming reader
must read the declared value rather than assuming one.

**Rule 3.** The file contains exactly one `<part>`, declared by exactly one
`<score-part>` in `<part-list>`, with matching `id` attributes. Measures are
numbered from 1, consecutively, without gaps.

`<part-name>` is required by the schema. `<score-instrument>` and
`<midi-instrument>` are optional; Fermata writes both, with MIDI program 25
(General MIDI *Acoustic Guitar (nylon)*), so that a file plays with a
plausible timbre rather than a default piano. A reader must not require them.

### Tab staff description (Rules 4–5)

**Rule 4.** The first measure's `<attributes>` declares, in this order:
`<divisions>`, `<key>`, `<time>`, `<clef>` with `<sign>TAB</sign>`, and
`<staff-details>` containing `<staff-lines>` and one `<staff-tuning>` per
string. A later measure carries an `<attributes>` element only where the meter
changes, and then only a `<time>`.

The order is not a style preference. `<attributes>`, `<note>`,
`<staff-details>` and `<staff-tuning>` are all `xs:sequence` in the schema, so
each has exactly one legal child order and any other fails validation. The
sequences that matter here:

| Element | Child order |
|---|---|
| `<attributes>` | `divisions`, `key`, `time`, `staves`, `part-symbol`, `instruments`, `clef`, `staff-details`, … |
| `<staff-details>` | `staff-type`, `staff-lines`, `line-detail`\*, `staff-tuning`\*, `capo`, `staff-size` |
| `<staff-tuning>` | `tuning-step`, `tuning-alter`?, `tuning-octave` |
| `<note>` (normal) | `chord`?, (`pitch`\|`unpitched`\|`rest`), `duration`, `tie`\*, `instrument`\*, `footnote`?, `level`?, `voice`?, `type`?, `dot`\*, `accidental`?, `time-modification`?, `stem`?, `notehead`?, `notehead-text`?, `staff`?, `beam`\*, `notations`\*, … |

Two consequences that catch people out: `<voice>` comes **before** `<type>`,
and `<clef>` comes **before** `<staff-details>`. Both orders read as unnatural
and both are mandatory.

`<notations>` and `<technical>`, by contrast, are `xs:choice` with unbounded
repetition, so their children may appear in any order. `<string>` before
`<fret>` and `<fret>` before `<string>` are both valid.

`<clef><line>` is optional for a TAB sign — the specification says line numbers
"are only needed with the G, F, and C signs in order to position a pitch
correctly on the staff". The specification's own tablature example nonetheless
writes `<line>5</line>`, and this profile follows it rather than inventing a
different value or leaving it out.

**Rule 5.** In `<staff-tuning>`, the `line` attribute counts staff lines **from
the bottom**: `line="1"` is the lowest-pitched string. `<capo>`, where present,
appears after every `<staff-tuning>`, and the tunings are written as the open,
non-capo values.

This is the rule most likely to be got backwards, and the failure is silent —
a file with the numbering mirrored validates against the schema and loads in
every application, with every note on the wrong string.

The reason it is easy to invert is that the two numbering schemes genuinely run
in opposite directions, and both are normative. From the schema's own
documentation:

> The staff-line type indicates the line on a given staff. Staff lines are
> numbered from bottom to top, with 1 being the bottom line on a staff.

> The string-number type indicates a string number. Strings are numbered from
> high to low, with 1 being the highest pitched full-length string.

`<staff-tuning line=>` is a staff line, and `<string>` is a string number.
Therefore:

```
line = staff-lines + 1 - string
```

For a six-line guitar staff: `line="1"` is the low E and string 6; `line="6"`
is the high E and string 1. Standard tuning is written bottom-up as E2, A2, D3,
G3, B3, E4 — see [Example 1](#example-1-one-monophonic-bar).

Beware prose summaries of the tablature tutorial, some of which state the
opposite. The schema's `staff-line` documentation and the tutorial's own code
example agree with the rule above.

`<capo>` "indicates at which fret a capo should be placed on a fretted
instrument. This changes the open tuning of the strings specified by
staff-tuning by the specified number of half-steps." So a capo does not alter
the written tunings, and fret numbers stay relative to it:

```
sounding pitch = tuning + capo + fret
```

Fermata never writes `<capo>`, because it cannot detect a capo from an
engraved score. The emitter supports it for other producers, and Fermata reads
it.

### Voices (Rules 6–8)

**Rule 6.** Voices are written one complete voice at a time. Each voice after
the first is preceded by a `<backup>` whose `<duration>` equals the total
duration the preceding voice wrote, returning the writing position to the start
of the measure. Voices are numbered with the strings `1`, `2`, … from 1,
consecutively within a measure, in top-to-bottom order.

MusicXML is silent on almost all of this, so most of Rule 6 is this profile's
choice rather than the specification's requirement. What the specification does
say:

> The backup and forward elements are required to coordinate multiple voices in
> one part, including music on multiple staves. […] Duration values should
> always be positive, and should not cross measure boundaries or mid-measure
> changes in the divisions value.

> The duration element moves the musical position when used in backup elements,
> forward elements, and note elements that do not contain a chord child
> element.

What it does **not** say: whether `<voice>` is scoped to the part or the staff,
whether numbering starts at 1, or whether the values are numeric at all —
`<voice>` is typed `xs:string`, so any string validates. This profile requires
consecutive numeric strings from 1, part-scoped, because a reader has no other
way to tell how many voices a measure has.

**Rule 7.** The notes of a chord share one onset: the first carries no
`<chord>`, every subsequent note carries `<chord/>` as its first child, and all
of them carry the same `<duration>`, `<type>` and `<voice>`.

Only the first note of a chord advances the position. From the specification:

> The duration of a chord note does not move the musical position within a
> measure. That is done by the duration of the first preceding note without a
> chord element. Thus the duration of a chord note cannot be longer than the
> preceding note.

A four-note chord in 4/4 therefore contributes one beat, not four.

**Rule 8.** In every measure, for every voice that appears in it, the sum of
the durations of that voice's notes and rests — counting chord members once —
equals the measure's duration, which is
`divisions × 4 × beats ÷ beat-type`.

This is the profile's central rule and the main reason for choosing MusicXML in
the first place. **MusicXML does not require it.** There is no statement
anywhere in the specification that a measure's contents must add up to its
meter, and plenty of real files in which they do not. It is stated here as a
requirement because it is the one property that makes a defective tab
transcription *detectable by a tool nobody involved wrote*.

A transcription derived from an engraved score can get individual durations
right and still produce a bar that does not add up — an undetected tuplet, a
flag the reader missed, or two voices that were flattened into one. In a
bespoke text format, catching that needs bespoke arithmetic. Under Rule 8 it is
a structural defect that any MusicXML implementation reports.

The checking arithmetic is exactly:

```
measure duration = divisions * 4 * beats / beat-type
```

with `beats` and `beat-type` from the `<time>` in effect. The denominator
matters: 3/4 and 6/8 are both three quarter notes' worth, and using the
numerator alone gives a compound meter twice its true length.

Two notes on what conformance means in practice:

- A producer must not pad or trim a measure to satisfy Rule 8. Silently
  inserting a rest to make the sum work hides the defect the rule exists to
  expose. Fermata emits measures exactly as read and reports how many fail the
  rule — see [Checking a file](#checking-a-file).
- A reader should treat a Rule 8 violation as a diagnostic, not a parse error.
  The file is still valid MusicXML and still largely playable; the affected
  measures simply drift.

### Fret, string and pitch (Rules 9–13)

**Rule 9.** Every note that sounds carries
`<notations><technical><string>` and `<fret>`. Frets count from 0 for an open
string; strings count from 1 for the highest-pitched string. A `<rest>` carries
neither.

Both conventions are the specification's, verbatim:

> Fret numbers start with 0 for an open string and 1 for the first fret.

> String numbers start with 1 for the highest pitched full-length string.

`<fret>` extends `xs:nonNegativeInteger`, so 0 is legal and a negative fret is
not; `<string>` extends `string-number`, a restriction of `xs:positiveInteger`,
so string numbers start at 1. A conforming file's string numbers are all within
the instrument's string count, so that each resolves against a declared
`<staff-tuning>`.

**Rule 10.** Every note that sounds also carries `<pitch>`, and its pitch
agrees with its string, fret, the declared tuning and any capo.

The schema requires exactly one of `<pitch>`, `<unpitched>` or `<rest>` on
every note; `<fret>` and `<string>` are under `<notations>` and do not
substitute for it. The specification does not say which to use for tablature —
the "always use `<unpitched>`" instruction applies to percussion clef, not TAB
— but every note in its own tablature tutorial uses `<pitch>`, and a fretted
note has determinate pitch. This profile requires `<pitch>`.

Requiring the two to *agree* is the substantive half of the rule. Nothing in
MusicXML forces a consumer to derive pitch from string, fret and tuning, and
nothing forces a producer to keep them consistent, so a file can state one
pitch in `<pitch>` and imply another in `<technical>`. A conforming file does
not: a reader may use whichever it prefers and get the same answer.

**Rule 11.** A note's pitch is one `<pitch>` can express. MusicXML's `octave`
type is an integer from 0 to 9, which bounds pitch to roughly MIDI 12–131.

This sounds like a formality and is not. Fret numbers read from an engraved PDF
are occasionally not fret numbers: two adjacent single-digit frets that the
source rendered as one text span arrive as, say, 78, and 78 semitones above a
string is past the top of the range. Writing it produces an `<octave>` of 10,
which fails validation and makes the **entire document** unreadable to a
validating consumer — a far worse outcome than one missing note.

A producer that cannot represent a note must not write it as some other pitch.
Fermata omits the note, keeps its beat in place as a rest of the same duration
so that Rule 8 still holds for the measure, and reports the count.

**Rule 12.** Enharmonic spelling — the choice between F sharp and G flat for
the same sounding pitch — is determined by the key signature in
`<attributes><key><fifths>`, resolved on the line of fifths as described below.

String, fret and tuning give an exact MIDI number; they say nothing about how
to spell it, and MusicXML wants `<step>`, `<alter>` and `<octave>`. The key
signature is what decides.

On the line of fifths, a key *is* a position. The seven diatonic notes of a key
with `fifths` accidentals occupy positions `fifths-1` through `fifths+5`, so
the key's centre is at `fifths+2`. C major (`fifths` 0) centres on 2, and its
positions −1 to 5 are exactly F C G D A E B. Position *n* has:

```
step  = "FCGDAEB"[(n + 1) mod 7]
alter = floor((n + 1) / 7)
pitch class = (7n) mod 12
```

Each pitch class's spellings lie 12 positions apart (twelve fifths is seven
octaves), so at most two fall within the single-accidental range −8 (F flat) to
12 (B sharp), and the **nearer one to the key's centre** is chosen. In C major,
position 7 (C sharp) is five steps from the centre against D flat's seven, so a
chromatic C sharp is spelled sharp; E flat, at five against D sharp's seven, is
spelled flat.

The octave follows from the spelling, not from the MIDI number alone — MIDI 60
spelled B sharp is octave 3, not 4:

```
octave = floor((midi - alter - semitone(step)) / 12) - 1
```

Every note *in* the key is spelled unambiguously by this rule, in every key: a
key's own seven notes sit within three positions of its centre and their
enharmonic partners twelve further out, so distance always decides them.

**Rule 13.** Where two spellings are equally distant from the key's centre, the
one needing no accidental wins; failing that, the flat.

A tie occurs for exactly one pitch class per key — the tritone from the centre,
six positions either way — and both halves of the tie-break earn their place:

- In C major the tied pair is A flat against G sharp. Both need an accidental,
  so the flat wins, which is the conventional reading.
- In A flat major it is E against F flat. Preferring the plain letter is what
  keeps an ordinary E natural from being written F flat.

**Where this profile guesses, and what it costs.** Two honest limitations:

1. In a key with five or more accidentals, the nearest-position rule can pick a
   chromatic spelling an engraver would not have — B sharp rather than C
   natural in B major. It is the right pitch, spelled unusually.
2. A producer that cannot read the key signature at all should write
   `<fifths>0</fifths>`, which is what MusicXML means by no key signature, and
   spell accordingly.

Both cost only how accidentals are written. The sounding pitch, the fret and
the string are unaffected by the key, so a wrong or missing key signature never
makes a note wrong — only oddly spelled. That asymmetry is why this profile
picks a documented default instead of recording per-note uncertainty.

## Example 1: one monophonic bar

One bar of 4/4 at 80 bpm in standard tuning: open first string (E4), second
string second fret (C sharp 4), third string second fret (A3), first string
third fret (G4). This file is published as
[`docs/examples/monophonic.musicxml`](examples/monophonic.musicxml) and is
validated by the test suite.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://www.musicxml.org/xsd/musicxml.xsd">
  <work>
    <work-title>Monophonic example</work-title>
  </work>
  <identification>
    <encoding>
      <software>Fermata</software>
      <encoding-date>2026-08-19</encoding-date>
    </encoding>
  </identification>
  <part-list>
    <score-part id="P1">
      <part-name>Guitar</part-name>
      <score-instrument id="P1-I1">
        <instrument-name>Guitar</instrument-name>
      </score-instrument>
      <midi-instrument id="P1-I1">
        <midi-channel>1</midi-channel>
        <midi-program>25</midi-program>
      </midi-instrument>
    </score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>480</divisions>
        <key>
          <fifths>0</fifths>
        </key>
        <time>
          <beats>4</beats>
          <beat-type>4</beat-type>
        </time>
        <clef>
          <sign>TAB</sign>
          <line>5</line>
        </clef>
        <staff-details>
          <staff-lines>6</staff-lines>
          <staff-tuning line="1">
            <tuning-step>E</tuning-step>
            <tuning-octave>2</tuning-octave>
          </staff-tuning>
          <staff-tuning line="2">
            <tuning-step>A</tuning-step>
            <tuning-octave>2</tuning-octave>
          </staff-tuning>
          <staff-tuning line="3">
            <tuning-step>D</tuning-step>
            <tuning-octave>3</tuning-octave>
          </staff-tuning>
          <staff-tuning line="4">
            <tuning-step>G</tuning-step>
            <tuning-octave>3</tuning-octave>
          </staff-tuning>
          <staff-tuning line="5">
            <tuning-step>B</tuning-step>
            <tuning-octave>3</tuning-octave>
          </staff-tuning>
          <staff-tuning line="6">
            <tuning-step>E</tuning-step>
            <tuning-octave>4</tuning-octave>
          </staff-tuning>
        </staff-details>
      </attributes>
      <direction placement="above">
        <direction-type>
          <metronome>
            <beat-unit>quarter</beat-unit>
            <per-minute>80</per-minute>
          </metronome>
        </direction-type>
        <sound tempo="80" />
      </direction>
      <note>
        <pitch>
          <step>E</step>
          <octave>4</octave>
        </pitch>
        <duration>480</duration>
        <voice>1</voice>
        <type>quarter</type>
        <notations>
          <technical>
            <string>1</string>
            <fret>0</fret>
          </technical>
        </notations>
      </note>
      <note>
        <pitch>
          <step>C</step>
          <alter>1</alter>
          <octave>4</octave>
        </pitch>
        <duration>480</duration>
        <voice>1</voice>
        <type>quarter</type>
        <notations>
          <technical>
            <string>2</string>
            <fret>2</fret>
          </technical>
        </notations>
      </note>
      <note>
        <pitch>
          <step>A</step>
          <octave>3</octave>
        </pitch>
        <duration>480</duration>
        <voice>1</voice>
        <type>quarter</type>
        <notations>
          <technical>
            <string>3</string>
            <fret>2</fret>
          </technical>
        </notations>
      </note>
      <note>
        <pitch>
          <step>G</step>
          <octave>4</octave>
        </pitch>
        <duration>480</duration>
        <voice>1</voice>
        <type>quarter</type>
        <notations>
          <technical>
            <string>1</string>
            <fret>3</fret>
          </technical>
        </notations>
      </note>
    </measure>
  </part>
</score-partwise>
```

Rule 8 checks out: the measure duration is `480 × 4 × 4 ÷ 4 = 1920`, and voice
1 holds four notes of 480 each.

The tempo direction is optional; a file with no tempo simply omits it. The
`<metronome>` is what gets engraved and `<sound tempo=>` is what a player
reads, so both are written.

## Example 2: two voices in one bar

One bar of 3/4 in standard tuning. The upper voice plays three quarter notes —
E4, D4, F sharp 4 — over a lower voice holding one dotted half note, open fifth
string (A2). The complete file is published as
[`docs/examples/two-voice.musicxml`](examples/two-voice.musicxml); the header,
`<part-list>` and `<staff-details>` are identical to Example 1 apart from the
meter, so only the measure is shown here.

```xml
<measure number="1">
  <attributes>
    <divisions>480</divisions>
    <key>
      <fifths>0</fifths>
    </key>
    <time>
      <beats>3</beats>
      <beat-type>4</beat-type>
    </time>
    <clef>
      <sign>TAB</sign>
      <line>5</line>
    </clef>
    <staff-details>
      <staff-lines>6</staff-lines>
      <!-- six <staff-tuning> elements, exactly as in Example 1 -->
    </staff-details>
  </attributes>
  <note>
    <pitch>
      <step>E</step>
      <octave>4</octave>
    </pitch>
    <duration>480</duration>
    <voice>1</voice>
    <type>quarter</type>
    <notations>
      <technical>
        <string>1</string>
        <fret>0</fret>
      </technical>
    </notations>
  </note>
  <note>
    <pitch>
      <step>D</step>
      <octave>4</octave>
    </pitch>
    <duration>480</duration>
    <voice>1</voice>
    <type>quarter</type>
    <notations>
      <technical>
        <string>2</string>
        <fret>3</fret>
      </technical>
    </notations>
  </note>
  <note>
    <pitch>
      <step>F</step>
      <alter>1</alter>
      <octave>4</octave>
    </pitch>
    <duration>480</duration>
    <voice>1</voice>
    <type>quarter</type>
    <notations>
      <technical>
        <string>1</string>
        <fret>2</fret>
      </technical>
    </notations>
  </note>
  <backup>
    <duration>1440</duration>
  </backup>
  <note>
    <pitch>
      <step>A</step>
      <octave>2</octave>
    </pitch>
    <duration>1440</duration>
    <voice>2</voice>
    <type>half</type>
    <dot />
    <notations>
      <technical>
        <string>5</string>
        <fret>0</fret>
      </technical>
    </notations>
  </note>
</measure>
```

The things to note:

- The measure duration is `480 × 4 × 3 ÷ 4 = 1440`. Voice 1 holds three notes
  of 480; voice 2 holds one of 1440. Both sum to 1440, so Rule 8 holds.
- The `<backup>` duration is 1440 — the whole of what voice 1 wrote — which
  returns the position to the start of the measure for voice 2 to begin.
- Voice 2's note is a dotted half: `<type>half</type>` plus one `<dot/>`, with
  `<duration>` 1440 rather than 960. The `<type>` and `<dot>` describe how it
  is written; `<duration>` is what sounds, and the two must agree.
- Fret 2 on string 1 is F sharp 4 — spelled sharp, not G flat, by Rule 12: in C
  major, F sharp is nearer the key's centre on the line of fifths.

## Checking a file

Fermata's own output is checked three ways, and any implementation of this
profile can be checked the same way.

**Schema validation.** Validate against the MusicXML 4.0 XSD, available from
the [w3c/musicxml](https://github.com/w3c/musicxml) repository. The schema's
`xs:import` elements name remote URLs for `xml.xsd` and `xlink.xsd`; download
those alongside it and repoint the `schemaLocation` attributes locally, or
validation will fail with an unrelated complaint about the `xml:lang`
attribute. Fermata's test suite validates its examples when
`FERMATA_MUSICXML_XSD` points at such a copy.

Schema validation catches every child-order mistake in [Rule
4](#tab-staff-description-rules-4-5) and every out-of-range value in [Rule
11](#fret-string-and-pitch-rules-9-13). It cannot catch a mirrored string
numbering or a Rule 8 violation, because both produce perfectly valid XML.

**Measure arithmetic.** Rule 8 is checkable by any MusicXML implementation.
[music21](https://www.music21.org/) reports it directly: parse the file, and for
each measure compare each voice's summed `duration.quarterLength` against the
measure's `barDuration.quarterLength`.

**Round-trip through a renderer.** Loading the file in an application that
reads tablature is what catches a mirrored string numbering, since the notes
come back on the wrong strings while everything else looks correct. Fermata
uses [alphaTab](https://alphatab.net/) for this, via
`server/tools/tab_extract/verify_musicxml.mjs`, which reports each file's bar,
voice and note counts along with the first note's MIDI value, string and fret.

**What Fermata reports about its own transcriptions.** A transcription's
warnings and confidence live on the transcription record and in the API
response, not in the MusicXML — the emitted file is an ordinary score with
nothing unusual in it. Those warnings include how many measures fail Rule 8, in
each direction, and how many notes were unwritable under Rule 11.

## Out of scope

An honest scope statement is more useful than an aspirational one. The
following are **not** covered by this profile. Some are MusicXML features this
profile simply does not use; others are things Fermata cannot determine from an
engraved score. A conforming file does not contain them, and a conforming
reader need not expect them.

**Techniques whose encoding is settled but whose rendering is not.** MusicXML
defines all of these under `<technical>`; applications differ enough in how
faithfully they render and play them that pinning down an encoding here would
promise more interoperability than exists.

- Bends and bend contours (`<bend>`, `<bend-alter>`, `<pre-bend>`,
  `<release>`). Bend *shape* in particular renders very differently between
  applications.
- Tapping (`<tap>`), hammer-ons and pull-offs (`<hammer-on>`, `<pull-off>`),
  slides (`<slide>`) and glissandi.
- Harmonics (`<harmonic>`), whether natural or artificial.
- Vibrato, tremolo, palm muting, `<open-string>`, `<thumb-position>`,
  `<golpe>`, `<fingernails>`.
- Left-hand fingering and right-hand plucking indications (`<fingering>`,
  `<pluck>`).

**Rhythm and structure this profile does not model.**

- Tuplets (`<time-modification>`, `<tuplet>`). This is why Rule 2's divisions
  value is chosen to divide by 3 — so adding them later does not require
  changing it.
- Ties (`<tie>`, `<tied>`) and slurs.
- Grace notes, which the schema handles as a separate `<note>` branch with no
  `<duration>` at all.
- Repeats, codas, segnos, multiple endings, and any other structural
  navigation.
- Beaming (`<beam>`). Readers group notes into beams themselves.
- Note values shorter than a 32nd, and more than two augmentation dots.

**Score-level things.**

- More than one part, more than one staff per part, and a tab staff paired with
  its own standard-notation staff in the same part. MusicXML supports the last
  of these with `<staff-type>alternate</staff-type>`; this profile writes the
  tab staff alone.
- Printed accidentals. `<accidental>` is the accidental an engraver *drew*,
  which is a display decision — courtesy accidentals, cautionary parentheses —
  distinct from `<alter>`, which is the pitch. This profile writes `<alter>`
  and omits `<accidental>`, leaving the reader to decide what to print from the
  pitch and the key signature.
- Layout: `default-x`, `default-y`, `<print>`, `<defaults>`, page and system
  breaks. Nothing here positions anything.
- Lyrics, chord symbols (`<harmony>`), dynamics, articulations, fermatas,
  arpeggios and ornaments.
- Key changes part-way through a score. The meter is tracked as a timeline and
  a change is written where it happens; the key is a single document-level
  value, because it affects only enharmonic spelling and a later change costs
  some oddly-spelled accidentals rather than any wrong pitch.
- Capo detection. `<capo>` is supported on both sides but Fermata never writes
  it, having no way to read a capo off an engraved score.
