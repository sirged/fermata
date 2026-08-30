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
  - [Inferred silence](#inferred-silence-rule-14)
  - [Repeat structure](#repeat-structure-rule-15)
  - [Navigation marks](#navigation-marks-rule-16)
    - [A system that was not read](#a-system-that-was-not-read)
  - [Note identifiers](#note-identifiers-rule-17)
  - [Ties](#ties-rule-18)
  - [Harmonics](#harmonics-rule-19)
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
  rule — see [Checking a file](#checking-a-file). Where a producer genuinely
  has to hold a position it did not read — a voice that enters late still has
  to enter late — [Rule 14](#inferred-silence-rule-14) says how, and that
  mechanism is deliberately not a rest and so does not enter this sum.
- "Every voice that appears in it" is the whole of the measure's voices, and a
  voice can appear without carrying a note: a `<forward>` names a `<voice>`
  too. A checker that enumerates voices only from `<note>` elements will miss
  such a voice entirely, and score a measure whose every voice was inferred as
  conforming. Enumerate voices from `<note>` **and** `<forward>`, then sum only
  notes and rests.
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

**What a producer does when it cannot fret a note.** `musicxml.build()` never
writes a sounding note with `<pitch>` and no `<technical>` half - a note it
has a (string, fret) pair for gets both, written together; a note it does not
is written as a `<rest>` of the same duration instead (Rule 11 covers the one
case that reaches this: a fret number MusicXML's `<octave>` cannot express).
There is no third shape. This is a structural property of the emitter's own
branching, not a claim that happens to hold on every fixture measured against
it - see `test_library_wide_every_sounding_note_carries_string_and_fret` and
its engraved-fixtures counterpart in the test suite, which check it against
every PDF this project can commit or point `FERMATA_TEST_LIBRARY` at, and
found nothing (issue #165).

**What a consumer does when a file does not hold to this.** Rule 9 binds a
*producer*; nothing stops a file this project did not write - a direct
`.musicxml`/`.mxl` upload, or one hand-edited afterward - from declaring a TAB
staff and leaving some of its notes unfretted anyway, which third-party
notation software does for a tab staff LINKED to a notation staff and left
for the reading application to fret. A staff like that cannot be honestly
drawn as tablature, and Fermata's renderer does not try: it turns tablature
off for exactly that staff before the first render (`showTablature = false`,
the same lever this project already relies on to keep a percussion staff's
tuning from being offered as tab - see `disqualifyUnstrungTabStaves` in
`web/src/lib/score-render.js`) rather than asking alphaTab to draw a staff
whose `TabBarRenderer.collectSpaces` indexes `tuning.length - note.string`
with no bounds check.

That check is a range, not "was a string ever read": alphaTab's importer maps
a `<string>` value S (1..N, MusicXML's own convention) to `note.string =
tuning.length - S + 1` with no validation, so an out-of-range S - `0` or
`N + 1`, say - round-trips to an out-of-range `note.string` that still passes
`Note.isStringed` (`string >= 0`). The only condition that keeps every note
inside `collectSpaces`'s array is `1 <= note.string <= tuning.length`; a check
against `isStringed` alone still crashes on exactly this shape. Standard
notation on the same staff, or another staff in the same track, is
unaffected - only tablature drawing for the disqualified staff is turned off,
and disclosed rather than left silent (`host.dataset.scoreTabWithheld` and a
distinct viewer notice, `tabWithheldMessage` - "no notation or tablature" is
false for a score that had a TAB staff and lost it to one bad note).

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

1. From **four** accidentals up, the nearest-position rule can pick a chromatic
   spelling an engraver would not have. E major spells F natural as E sharp; A
   flat major spells B natural as C flat, which also moves the printed octave,
   since C flat 5 and B natural 4 are the same pitch. B major spells C natural
   as B sharp. Three accidentals or fewer never produce one. The pitch is
   correct in every case; only the spelling is unusual, so a reader that
   derives pitch from `<pitch>` is unaffected and one that renders accidentals
   literally will show something a human would have written differently.
2. A producer that cannot read the key signature at all should write
   `<fifths>0</fifths>`, which is what MusicXML means by no key signature, and
   spell accordingly.

Both cost only how accidentals are written. The sounding pitch, the fret and
the string are unaffected by the key, so a wrong or missing key signature never
makes a note wrong — only oddly spelled. That asymmetry is why this profile
picks a documented default instead of recording per-note uncertainty.

### Inferred silence (Rule 14)

**Rule 14.** Silence a producer deduced rather than read is written as
`<forward>`, never as a `<rest>`. Each such element carries a `<duration>`, a
`<footnote>` saying so, and the `<voice>` it belongs to:

```xml
<forward>
  <duration>960</duration>
  <footnote>silence deduced from the time signature, not read from a rest printed in the source</footnote>
  <voice>1</voice>
</forward>
```

The schema's sequence for `forward` is `duration`, `footnote?`, `level?`,
`voice?`, `staff?` — the footnote comes **before** the voice.

**Why a producer needs this at all.** Reading polyphonic tablature off an
engraved page loses notes: an unmapped notehead, an unreadable fret number, a
rest glyph too far from any onset to place. A voice that has lost a note is
shorter than its meter, and a voice that is shorter than its meter cannot
simply be written short, because *where* its remaining notes fall depends on
the silence around them. A voice that enters on beat three and is written with
nothing before it enters on beat one instead, and every note in it sounds two
beats early against the other voices. The silence has to be there for the bar
to play.

**Why it must not be a rest.** A rest is a claim about the source: somebody
engraved it. Writing invented silence as a rest makes the measure add up, so
[Rule 8](#voices-rules-6-8) passes, the transcription reports itself conformant,
and the notes that went missing leave no trace anywhere. Measured on one real
library, a score that had lost ninety notes and gained seventy-seven quarter
notes of invented rest reported every bar as adding up, at high confidence.
Every dropped note downstream of that was invisible for the same reason.

`<forward>` resolves both halves. It advances the writing position exactly as a
rest of the same duration would, so notes still sound where they should and the
measure still lays out — renderers treat it as the silence it is. But it is not
a note and not a rest, so it does not enter Rule 8's sum, and the measure fails
Rule 8 by exactly the amount that was never read. **The producer's own count of
defective measures and an independent tool's count therefore agree**, which is
the entire reason for choosing a standard format.

**What this rule covers, and what it does not.** It covers silence inserted to
complete a voice the producer *did* read notes from. A measure a producer read
nothing at all from is a different statement — it holds no voice to complete —
and is written as an ordinary measure of rests.

Writing *that* as `<forward>` would be worse, not better: a voice consisting of
nothing but `<forward>` contributes no notes and no rests, so a consumer
enumerating voices from `<note>` elements never sees the voice at all, and the
measure reads as vacuously conforming. An honest measure of rests is the better
encoding, and Fermata emits one — the measure keeps its number, so side-by-side
comparison against the source stays aligned.

The consequence has to be stated, because a consumer cannot recover it: **a
measure of rests may be either genuinely engraved silence or a measure whose
contents were missed, and nothing in the file distinguishes them.** In the
library this profile was developed against, 24 measures are a bar of rests and
exactly one of them was printed that way. (An earlier count of 338 included 314
measures that were never on the page at all - a repeat barline's two strokes,
drawn a few points apart, that `_detect_barlines` read as two separate
barlines with a phantom sliver "measure" between them; see
[Rule 15](#repeat-structure-rule-15) for the fix.) Fermata therefore reports
these measures outside the Rule 8 figures — counted, named by number, and
folded into its own confidence — because the file cannot carry the
distinction. A consumer that needs it has to get it from the producer.

**For a reader.** Four things follow, and they all matter:

- Do not treat `<forward>` as a rest when checking Rule 8. It is what makes a
  short measure detectable.
- A file with no `<forward>` in it makes no claim either way. This profile
  requires a producer that infers silence to mark it; it cannot make a producer
  that pads silently declare itself.
- A `<forward>` that is the last thing in its voice may be dropped rather than
  rendered: nothing sounds after it, so no note moves either way. alphaTab ends
  the voice there; MuseScore rewrites it as an invisible rest. A `<forward>`
  anywhere else must advance the position, or every note after it sounds early.
- **The marking does not survive a save by another program.** Measured over the
  same library with MuseScore 4, re-saving the 293 files it emits: of the 6,155
  `<forward>` elements written, 2,957 survive as one and every one of those
  loses its `<footnote>` and its `<voice>`, while a trailing one is rewritten as
  an invisible rest and a leading one as a `<backup>` with no `<forward>` at
  all. Not one `<forward>` in the re-saved files still carries the marking. Net
  effect on a Rule 8 check of the re-saved files — a measure counted once
  whichever way it is wrong, which is the `bars_defective` figure below — is
  that defective measures fall from 5,863 to 5,430 and identifiable inferred
  silence from 5,529.2 quarter notes to none that can be identified at all, so
  **433 measures this profile reports defective read as conforming to anything
  downstream of that save**. The agreement between a producer's figures and an
  independent check holds for the file *as the producer wrote it*. Verify
  against that file, not against a round trip through an editor — see
  [Checking a file](#checking-a-file). (The two absolute counts here were
  measured before the printed meter could be read behind a key signature and
  before each measure was budgeted against the meter in force at its own
  position. On the same library the producer's own defective count moved to
  5,477, from 5,863, and then to 5,332 once an augmentation dot was
  assigned to the notehead at its expected offset rather than whichever one
  was nearest (#89) — a chord of three or more close notes could otherwise
  give one notehead two dots and another none, corrupting that measure's
  arithmetic in both directions at once. Re-measured at **5,300** after
  #152 recovered systems that were not being read at all, #111/#112 bound
  the dots a seconds interval displaces, and #113 counted the third stroke of
  a beam group so that a 32nd stopped being emitted at a 16th's length; the
  figure is quoted here only to date the round trip, and each of those
  changes states its own before and after. The round trip has not been
  re-measured since any of them, so the before-and-after pair above is
  left as it was taken rather than half updated. What the paragraph is
  about — that the marking does not survive the save — is unaffected either
  way.)

**In the other direction.** Fermata also emits the same music as alphaTex for
its transcription editor. That format has no editorial mechanism for this and
carries the inferred silence as an ordinary rest; MusicXML is the canonical
output, and the numbers under [Checking a file](#checking-a-file) name the
affected measures for a reader of either.

### Repeat structure (Rule 15)

**Rule 15.** Repeat barlines and multiple endings are written as `<barline>`,
and only where the engraving says so.

A repeat that begins is `<barline location="left">` on the first measure of
the repeated span, carrying `<bar-style>heavy-light</bar-style>` and
`<repeat direction="forward"/>`. A repeat that ends is
`<barline location="right">` on the last measure of the span, carrying
`<bar-style>light-heavy</bar-style>` and `<repeat direction="backward"/>`.
`times` is never written: an engraved `:‖` says to play the span twice and
says nothing more, and 2 is what a consumer assumes in its absence.

An ending is `<ending>` on the first and last measures of its range —
`type="start"` on the left barline of the first, and on the right barline of
the last either `type="stop"` where the bracket is drawn with a closing hook
or `type="discontinue"` where it is left open. Intermediate measures carry no
`<ending>`; a consumer that tracks the open ending sees them, and one that
does not would not be helped by repeating it. The `number` attribute is the
number printed inside the bracket, verbatim as an integer list.

The schema's sequence inside `barline` is `bar-style?, footnote?, level?,
wavy-line?, segno?, coda?, fermata*, ending?, repeat?` — **`<ending>` comes
before `<repeat>`**, and a measure that both closes an ending and ends a
repeat carries both in that order on one right barline.

**A form mark carries no duration, and Rule 8 is unaffected by it.** This is
load-bearing in both directions. A producer must not let reading a repeat
change a measure's contents, and a consumer must not let a `<barline>` enter
the per-voice sums. Measured on the library this profile was developed
against: adding repeat structure to 188 of 297 scores moved
`bars_overfull`, `bars_short`, `bars_defective`, `bars_padded` and
`inferred_rest_quarters` by exactly zero.

**What is not written, and why the file cannot tell you.** A repeat mark this
producer read only partly — dots with no thick stroke, a bracket with no
readable number, an ending whose extent could not be established — is
**omitted entirely** and reported in the producer's own warnings. There is no
way to write "there is a repeat here and I could not read it" in MusicXML:
a `<repeat>` is an assertion, and a half-read one written anyway would make
the transcription play a form nobody engraved. So a conforming file's silence
about repeats means only that none were written, exactly as
[Rule 14](#inferred-silence-rule-14) says of a missing `<forward>`. A reader
who needs to know whether any were *missed* has to get it from the producer —
see `repeats_unread`, `endings_unread`, `endings_truncated`,
`form_marks_unanchored` and `endings_incomplete` on `ExtractionResult`, and
the `structure` confidence key beside `frets` / `rhythm` / `time_signature` /
`key_signature`. `structure` is kept apart from `rhythm` deliberately: a
dropped volta says nothing about whether the durations were read, and folding
it into `rhythm` would make that figure mean two different things.

**`bar-style` alone.** A final barline (`light-heavy`) and a double barline
(`light-light`) carry no `<repeat>` and no `<ending>`; they are engraving, and
writing them costs nothing and makes a round trip look like the page.

Text navigation — `D.C.`, `D.S.`, `To Coda`, `Fine` and the two signs — is
not a `<barline>` and has its own rule; see
[Rule 16](#navigation-marks-rule-16).

### Navigation marks (Rule 16)

**Rule 16.** A navigation mark is written as a `<direction>` on the measure
it names, and the `<sound>` beside it is written only where the score draws
what it points at.

A **sign** — the segno and the coda — is written as the element MusicXML has
for it, before the measure's notes, because it marks that measure's downbeat:

```xml
<direction placement="above">
  <direction-type><coda/></direction-type>
  <sound coda="coda"/>
</direction>
```

An **instruction** — `D.C.`, `D.S.`, `To Coda`, `Fine` — is written as
`<words>`, verbatim as the page prints it, *after* the measure's notes,
because it fires at the end of that measure:

```xml
<direction placement="above">
  <direction-type><words>D.C. al Coda</words></direction-type>
  <sound dacapo="yes"/>
</direction>
```

The `<sound>` attribute is `dacapo`, `dalsegno`, `tocoda` or `fine`, and
`segno`/`coda` on the signs themselves. Where a score carries more than one
coda they are `coda1`, `coda2`, … after the number printed beside each sign,
and a `To Coda 2` names `coda2`.

`<sound>` is written **inside** `<direction>`, which is where the
specification's own examples put it and where every notation program writes
it. It is deliberately not *also* written as a direct child of `<measure>`:
two `<sound>` elements naming one jump are two instructions to a reader that
honours both.

**A navigation mark carries no duration, and Rule 8 is unaffected by it** —
the same invariant [Rule 15](#repeat-structure-rule-15) states for a
`<barline>`, and it was measured the same way. Adding navigation marks to
166 of 297 scores moved `bars`, `bars_measured`, `bars_overfull`,
`bars_short`, `bars_defective`, `bars_padded`, `inferred_rest_quarters`,
`notes` and `beats` by exactly zero, score by score.

**What is written without its jump, and why.** Unlike a half-read repeat,
a navigation instruction that was read is always written: the words are what
the page prints, and a reader is entitled to see them. What is conditional is
the `<sound>`, which is an assertion about playback — `dalsegno` names a
segno, `tocoda` names a coda — and naming a target that is not in the file
would make the transcription play a form nobody engraved. So an instruction
whose target the score does not draw is written as words alone, and the
measure is reported in `nav_marks_unresolved` / `nav_marks_unresolved_bars`.
A mark with no measure to name at all — too far from any staff, over a staff
no music was read from, or lying entirely outside the horizontal span of the
staff whose bars it would otherwise be clamped onto — is reported in
`nav_marks_unanchored`, which has no bar list precisely because it has no
bar. Both feed the `structure` confidence key.

This is not a rare branch. In the library this profile was developed against,
**86 of 297 scores print "D.S."** Of those 86, two draw no segno for the
D.S. to name and get their words and no `dalsegno`; the other 84 do draw one.

**`nav_marks_unanchored` is now 0 over that library, and it used to be 43.**
Those 43 were almost entirely one defect, and not a defect in reading marks:
these arrangements print the coda as a short system to the *right* of the
last full system on the same horizontal band, and staff detection lost that
whole system, so its coda sign was read off the page and had no bar to name
because this transcription held none of that system's bars. Reading the
system is what fixed it — see **A system that was not read** below — and it
took `nav_marks_unresolved` from 87 bars to 7 at the same time. The 7 that
remain are scores that genuinely name a target their page does not draw.

### A system that was not read

Every figure above describes music that reached the transcription and says
how well it was read. `systems_unread` says how much never reached it: a
staff-sized group of staff lines was found on a page, could not be read as a
staff, and so contributed no bars at all. `systems_unread_pages` says which
pages — **pages, not bars, and for the same reason `nav_marks_unanchored`
has no bar list: a system that was never read has no bar numbers, because
bar numbers are assigned by a grid its bars never entered.**

It has to be counted, and counted separately, because of an asymmetry that
makes silence here worse than error. The bars that vanish with a system are
as likely as any others to be the ones that did not add up — so losing a
system can move `bars_defective` *down*. A conformance figure that improves
when music disappears is worse than no figure, and `systems_unread` is the
number that stops it being read that way: **`bars`, `notes` and every Rule 8
count describe only the systems that were read, and this is what says so.**

A score with a lost system also reports `structure` confidence as `low`,
outranking every other structure term. The repeat and navigation marks that
were read may be perfectly complete and still describe a form built out of
bars the file does not contain.

In the library this profile was developed against, `systems_unread` is **2**,
on 2 files. Before the side-by-side systems above were read it was **41**,
across 22 files.

**A correction, stated plainly, because an earlier version of this rule said
the opposite.** This document previously claimed that *no score in the
library draws a segno at all*, and that the claim had been "measured twice —
once over every music glyph that resolved to a category and once over every
glyph that resolved to none". The claim was false: the library draws **88
segno signs across 84 files**. Every one of them is Finale's Maestro glyph ID
4, which this project's calibrated glyph table labelled `"simile"`. That is
precisely the error a two-sided census cannot see — a *wrongly categorised*
glyph is in neither bucket, because it is not unmapped (so the "what are we
missing" sweep skips it) and it is not a segno (so the "what did we find"
sweep never counts it). Only rendering the outline and looking at it settles
that class of question, which is the standard the glyph table claims for
itself; GID 4 renders as an unmistakable segno, and every one of the 84 files
carrying it also prints a "D.S.". (One of those 84, "Rito Village - Night",
embeds its Maestro subset under a PDF resource name this decoder did not yet
recognise at all — issue #154 — so its glyphs, segno included, were invisible
regardless of the GID-mislabelling this correction is otherwise about.) What
survives from the old claim is
narrower and still true: **the word "Segno" appears in no file's text layer**,
so the sign is the only evidence there ever was.

**What the renderer does with it.** This project's own player, alphaTab
1.8.4, reads a jump only from a `<sound>` that is a direct child of
`<measure>`, so it ignores every jump written above and plays the score
straight through. That is a renderer limitation, not a file defect: the file
is what MusicXML says it should be, and a consumer that reads the
specification's own shape gets the form. Measured directly on the engraved
`navigation` fixture, both ways round — see
`test_navigation_pdf_is_correct_musicxml_that_alphatab_still_plays_straight`,
which also shows that hoisting the same `<sound>` elements to measure level
makes alphaTab take the `D.S.` and ignore the `To Coda` and the `Fine`, so
that shape would not be more correct either. MuseScore 4.6.3 imports these
directions as staff text on the right measures and drops the `<sound>`
attributes, so a round trip through it keeps the marks and loses the jumps.

### Note identifiers (Rule 17)

**Rule 17.** Every `<note>` — rest or sounding, single note or chord member —
carries an `id` attribute, unique within the document and stable across
re-emission of the same content:

```
n{measure}-{voice}-{onset}-{chord}
```

`measure` is the same 1-based number the enclosing `<measure number=>` itself
carries. `voice` is the same 1-based number the note's own `<voice>` carries.
`onset` is the position (from 0) of this note's *beat* within its voice, and
counts every beat the producer processed for that voice — including one that
resolved to no writable duration and one written as `<forward>` rather than a
`<note>` — so it never shifts because something elsewhere in the measure did
or did not get written. `chord` is the position (from 0) of this note among
its own beat's written notes: 0 for a rest or an unaccompanied note,
incrementing for each further member of a chord (Rule 7).

```xml
<note id="n3-2-4-0">
```

is the note written from voice 2's fifth beat (index 4) in measure 3 — the
first (and, since `chord` is 0, only) note at that onset.

The schema's `optional-unique-id` attribute group types `id` as `xs:ID`,
which the specification requires to be unique across the **whole** document,
not merely among notes — and `xs:ID` is in turn constrained to `xs:NCName`,
which cannot begin with a digit. `n` is prefixed for exactly that reason: a
bare `3-2-4-0` is not a legal NCName, and a schema-validating parser would
reject the whole file. Nothing else in a file this profile writes begins with
`n` followed by a digit — `<score-part>`, `<score-instrument>` and
`<midi-instrument>` write `P1` / `P1-I1` — so a note id can never collide with
one of those either.

**Uniqueness precondition: one part, one staff.** The formula's uniqueness
rests on measure, voice, onset and chord member each being unique within the
scope the axis above it names — and that chain only reaches the whole
document because this profile writes exactly one `<part>` and one staff (see
[Scope](#scope)). #10's proposed second staff (tablature paired with its own
notation staff) and #93's proposed multiple tracks each add an axis the
formula does not currently name: a note at measure 3, voice 1, onset 0 on a
second staff or a second track would compute the identical id string to the
one at that position on the first. Either extension has to add a part or
staff component to the formula before this rule's uniqueness claim would
still hold across the whole document — it is not automatic.

**The chord ordinal is emission-derived; the onset is not, and that
asymmetry is real.** `onset` counts every beat in the *model's* voice list,
written or not, so a beat that never gets written (a zero-duration beat, an
inferred rest) does not shift a later beat's onset. `chord` is assigned
during *emission*, from position in the list of chord members that survive
[Rule 11](#fret-string-and-pitch-rules-9-13)'s pitch-representability
filter — so a chord member Rule 11 drops shifts every later member's
`chord` index down by one: a four-note chord whose second member is
unrepresentable writes `chord` 0, 1, 2 on its three survivors, not 0, 2, 3,
and a later fix that makes that pitch representable again would renumber
the other two. Onset avoids this because the model is fixed before emission
decides anything; chord does not, because representability is decided per
note inside one beat, and unlike an unwritten beat, a dropped chord member
leaves no placeholder to number around.

**Why every note, rests included, rather than only the ones that sound.** The
alternative considered was to skip rests, on the reasoning that a rest is
never a target of anything downstream: the alternative renderer measured
against this profile carries an id through to its own output for every
*sounding* note and drops the rest of the attribute set from a rest entirely.
That is a real property of one consumer, not a reason to special-case the
rule here. An id exists so a positional map — MusicXML document order against
a renderer's own bar → voice → beat → note order — can be checked rather than
assumed, and a rest occupies a position in that order exactly as a sounding
note does; a reader walking the file to audit its own alignment has to skip
rests deliberately if they carry no id, which is a second rule disguised as
an exception to the first. Writing one rule — every `<note>` element gets an
`id` — costs nothing (the `chord`-0 case an unaccompanied note already needs
is exactly what a rest's id looks like) and leaves nothing for a consumer to
special-case.

**What this does not cover: inferred silence has no id, and cannot.**
Writing ids on rests closes the gap between a sounding note's position and a
printed rest's position — it does not close the gap between either of those
and [Rule 14](#inferred-silence-rule-14)'s `<forward>`. The schema's
`forward` type carries no `optional-unique-id` attribute group at all, so
inferred silence is not merely written without an id by this profile's
choice — it has no `id` attribute to write. Measured on the library this
profile was developed against: 5450 `<forward>` elements, none nameable.
A consumer building a positional map over every onset a voice holds,
silence included, therefore has partial coverage from ids alone — sounding
notes and printed rests are addressable, inferred silence is not — and has
to fall back to counting `<forward>` elements by position for that part of
the map, the same way [Rule 8](#voices-rules-6-8) checking already does.
Say this plainly because it is easy to read Rule 17 as closing the gap
entirely once rests are included: it closes exactly one of the two gaps.

**Determinism, and what it does and does not promise.** The id is computed
from nothing but the beat's own position among the voices and measures
`build()` was handed — never a random value, a clock, or a counter carried
between notes — so extracting the same score twice, or re-emitting the same
beats model, produces byte-identical ids down to the ordering of chord
members. It is NOT stable across an edit that inserts or removes a beat
earlier in the same voice: every onset after the edit shifts, and every id
built from that onset shifts with it. That is the same shape Rule 15's
measure numbers already have — an id names a *position*, not the note
itself — and it is what "auditable against the file as emitted" means: a
consumer checks a positional map against *this* emission's ids, not against
ids the previous emission wrote for what looks like the same note.

**Ids are emitter-owned: a document mutated outside `build()` has to
re-derive them, not mint one.** The formula is defined over an entire
voice's beat sequence, not over one note in isolation, so keeping it valid
after an edit means recomputing every id in the edited voice from that
voice's new beat sequence — never assigning a fresh, one-off id to an
inserted note while leaving its neighbours' onset numbers as they were,
because the neighbours after the insertion point are no longer at the
onsets their old ids name. This is not hypothetical for this project:
`PUT /scores/{score_id}/transcription` stores whatever content a client
sends as the edited transcription, verbatim, with no id validation of any
kind. A saved edit that inserts a note without renumbering what follows it
in the same voice writes either a duplicate id — two notes now claiming the
same onset — or, in the case that mangles the `n` prefix along the way, an
id that is not a legal NCName; either fails validation against the MusicXML
4.0 XSD outright (`id` is `xs:ID`, checked exactly this way under [Checking
a file](#checking-a-file)). Renumbering the affected voice is the editor's
responsibility, not this emitter's — this is where that contract is stated.

**The invariant this rule does not disturb.** An id is one attribute on an
element that was already being written; it adds nothing else to the
document and moves no Rule 8 figure. Measured on the library this profile was
developed against: adding note ids to all 293 extractable scores left
`bars`, `bars_measured`, `bars_overfull`, `bars_short`, `bars_defective`,
`bars_padded`, `inferred_rest_quarters`, `notes` and `beats` exactly where
they were before the change, and every byte of every emitted file was
unchanged apart from the `id="…"` attribute added to each `<note>`.

### Ties (Rule 18)

**Rule 18.** A tie is written at **both** of its ends and in **both** of the
specification's two spellings. The note the tie starts at carries
`<tie type="start"/>` as a direct child of `<note>` and
`<tied type="start"/>` inside its `<notations>`; the note it is held into
carries `<tie type="stop"/>` and `<tied type="stop"/>` in the same two
places. A note in the middle of a chain of ties carries the stop before the
start, in both places, which is the order the two events are in time.

**Why both spellings.** They are not duplicates: `<tie>` is the *sound* of a
tie — the schema's own documentation calls it "just the sounding tie", and it
is what says the second note is not struck — while `<tied>` is the *printed
mark*, a member of `<notations>` alongside slurs and articulations. The
specification's own guidance is that "Ties that join two notes of the same
pitch together should be represented with a tied element on the first note
with type="start" and a tied element on the second note with type="stop"",
and separately that the notated `<tied>` and the sounded `<tie>` are distinct
elements. Consumers genuinely differ on which they read, and the difference is
audible rather than cosmetic: **alphaTab, the renderer this project embeds,
reads only `<tied>`** — its MusicXML importer's `<note>` child switch has no
`tie` case at all, so a file carrying only the sounding element re-strikes
every note it should hold. Writing one without the other produces a file that
is correct for half of its readers.

**The two notes carry the same pitch, string and fret.** This is a
requirement, not a coincidence. An unnumbered `<tied>` is matched by pitch:
alphaTab's importer resolves a `stop` by searching its pending starts for one
whose `<pitch>` computes to the same MIDI value, and silently drops the tie if
it finds none. Two notes joined by a tie are one sounding note on one string,
so `<string>`, `<fret>` and `<pitch>` are all the struck note's — see the
producer note below.

**`number` is not written.** The schema allows a `number` attribute to
disambiguate overlapping ties. This profile leaves it off: unnumbered
matching is the well-trodden path in every reader tested, and a producer that
writes one tie per pitch at a time never needs the disambiguation.

**What a producer does about the held note's fret number.** A tie's second
note is not plucked, so a tab staff normally prints **no fret number under
it** — and it must still be written with the struck note's string and fret,
because it is that same string still sounding. Measured over this project's
297-score library: for an ordinary struck note, the nearest tablature digit
column sits a median 0.64 staff spacings from the notehead, and for the first
note of a tie the median is the same 0.64; for the second note of a tie it is
1.26, with a 95th percentile of 4.41. The nearest digit to a held note is
usually the *next* note's. So the extractor gives the second note of every
written tie the first note's string and fret outright, rather than whatever
digit the notehead-to-digit match found nearest it.

**What a producer does when it can only find one end.** It writes neither.
A `start` with no `stop` is not a half-written tie; in alphaTab it is a start
that stays pending for the rest of the part and can be closed by an unrelated
later note of the same pitch. Both dangling ends are therefore removed from
the emitted score and counted instead, as `tie_ends_unpaired` with the bars
they were in. The count is of tie **ends**, not ties, because there is no way
to tell which two dangling ends were meant to be one tie.

**The invariant this rule does not disturb.** A tie changes the *structure*
of what is written — which notes are struck — and not how long anything is.
Measured on the library this profile was developed against, adding ties left
`bars`, `beats`, `notes`, `bars_overfull`, `bars_short` and `bars_defective`
each exactly where they were: 10762, 83365, 99461, 1541, 4190, 5300 across
the 293 extractable scores, unchanged to the unit. 998 ties were written
across 140 scores and 515 dangling ends disclosed across 122.

### Harmonics (Rule 19)

**Rule 19.** A note the engraving marks as a harmonic carries `<harmonic>`
inside its `<technical>`, beside the `<string>` and `<fret>` Rule 9 already
requires:

```xml
<notations>
  <technical>
    <string>1</string>
    <fret>12</fret>
    <harmonic/>
  </technical>
</notations>
```

**`<harmonic>` is written with an empty body unless the kind was read.** The
schema makes both of its children optional (`<natural/>`/`<artificial/>` and
`<base-pitch/>`/`<touching-pitch/>`/`<sounding-pitch/>` are each a
`minOccurs="0"` choice), so `<harmonic/>` alone is valid and says exactly what
was read: this note is a harmonic. The two engraving conventions this
extractor reads — a diamond notehead on the notation staff, and a fret number
in single guillemets (`‹12›`, U+2039 and U+203A) on the tablature staff —
mark a natural and an artificial harmonic identically, so naming one would be
a guess dressed as a reading. A producer that *can* tell them apart should
write `<natural/>` or `<artificial/>`; a consumer must not assume either from
a bare `<harmonic/>`.

**`<pitch>`, `<string>` and `<fret>` are the fretted position, unchanged.**
Rule 10's pitch is the open string plus the fret, and a harmonic does not
change what the tablature says to do — it says where to touch. Writing the
*sounding* pitch instead would need the kind (a natural harmonic at the 12th
fret sounds an octave above the fretted note, at the 7th a twelfth above it,
and an artificial one is a different calculation again), which is precisely
what a bare `<harmonic/>` says was not read.

**What the renderer does with it, plainly: nothing.** alphaTab's MusicXML
importer consumes the `<harmonic>` element and discards it — the `technical`
child switch has `case "harmonic": break;`, and never descends into it — so
`harmonicType` stays `None`, no harmonic marking is drawn, and playback
sounds the fretted pitch. That is a limitation of that importer and not of
this encoding; the element is still written, because the canonical output of
this project is the MusicXML file and a mark a reader cannot use is worth
more than a mark that was never made.

**Not carried into alphaTex, deliberately.** alphaTex has a harmonic
vocabulary (`{nh}`, `{ah n}`, `{ph n}`, …) and alphaTab honours all of it,
but none of those tokens is an annotation: each names *which* harmonic, and
the renderer then sounds the note at the pitch that implies. Writing one on
the strength of a convention that does not distinguish the kinds would
re-pitch a note on a guess. This is the same reasoning as Rule 14's inferred
rest, which alphaTex also does not carry.

**Conventions that are not read.** A `harm.` or `art. harm.` text marker, and
the small circle some editions draw above the note, are **not** recognised.
They mark a passage rather than a note, and this profile does not guess a
note's extent from a direction. Measured on this project's library, they
appear on 19 of 297 scores and the two conventions that *are* read appear on
121; on all but one of those 121, both appear together.

**The invariant this rule does not disturb.** `<harmonic>` carries no
duration and moves no Rule 8 figure. Measured on the library, marking 1030
notes across 123 scores left `bars`, `beats`, `notes` and all three bar
conformance counts exactly where they were.

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
      <note id="n1-1-0-0">
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
      <note id="n1-1-1-0">
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
      <note id="n1-1-2-0">
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
      <note id="n1-1-3-0">
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
1 holds four notes of 480 each. Note ids: `n1-1-0-0` through `n1-1-3-0` — one
measure, one voice, four onsets in order, no chords (Rule 17).

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
  <note id="n1-1-0-0">
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
  <note id="n1-1-1-0">
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
  <note id="n1-1-2-0">
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
  <note id="n1-2-0-0">
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

- Note ids run per-voice: voice 1's three notes are `n1-1-0-0` through
  `n1-1-2-0`, and voice 2's one note starts its own onset count over at
  `n1-2-0-0` — the `<backup>` between them rewinds the writing position, and
  Rule 17's onset index rewinds with it.
- The measure duration is `480 × 4 × 3 ÷ 4 = 1440`. Voice 1 holds three notes
  of 480; voice 2 holds one of 1440. Both sum to 1440, so Rule 8 holds.
- The `<backup>` duration is 1440 — the whole of what voice 1 wrote — which
  returns the position to the start of the measure for voice 2 to begin.
- Voice 2's note is a dotted half: `<type>half</type>` plus one `<dot/>`, with
  `<duration>` 1440 rather than 960. The `<type>` and `<dot>` describe how it
  is written; `<duration>` is what sounds, and the two must agree.
- Fret 2 on string 1 is F sharp 4 — spelled sharp, not G flat, by Rule 12: in C
  major, F sharp is nearer the key's centre on the line of fifths.

## Example 3: repeat barline and two endings

Three bars of 4/4 in standard tuning, illustrating [Rule 15](#repeat-structure-rule-15):
a forward repeat opens bar 1, ending 1 spans bar 2 and is closed by a backward
repeat with a closing hook (`type="stop"`), and ending 2 spans bar 3 and is
left open (`type="discontinue"`) with no repeat mark of its own. The complete
file is published as
[`docs/examples/repeat-structure.musicxml`](examples/repeat-structure.musicxml)
and is validated by the test suite; the header, `<part-list>` and
`<staff-details>` are identical to Example 1, so only the three `<barline>`
elements are shown here, each beside the measure it belongs to.

```xml
<measure number="1">
  ...
  <barline location="left">
    <bar-style>heavy-light</bar-style>
    <repeat direction="forward" />
  </barline>
  ...
</measure>
<measure number="2">
  <barline location="left">
    <ending number="1" type="start" />
  </barline>
  ...
  <barline location="right">
    <bar-style>light-heavy</bar-style>
    <ending number="1" type="stop" />
    <repeat direction="backward" />
  </barline>
</measure>
<measure number="3">
  <barline location="left">
    <ending number="2" type="start" />
  </barline>
  ...
  <barline location="right">
    <bar-style>light-heavy</bar-style>
    <ending number="2" type="discontinue" />
  </barline>
</measure>
```

The things to note:

- `<ending>` comes before `<repeat>` inside `<barline>` — the schema's own
  sequence order — and measure 2's right barline carries both, closing ending
  1 and the repeat at once.
- Ending 2 carries no `<repeat>` at all: nothing closes it, because nothing on
  the page closes it either — a bracket left open at its right end is written
  exactly as drawn, not guessed shut.
- Neither barline moves a single note or rest. A form mark carries no
  duration (Rule 8 is unaffected by it) — see [Repeat structure
  (Rule 15)](#repeat-structure-rule-15) above.

## Example 4: navigation marks

The same three bars, illustrating [Rule 16](#navigation-marks-rule-16): a
segno opening bar 1 with a `To Coda` at its end, a `D.S. al Coda` ending bar
2, and the coda sign opening bar 3 with a `Fine` at its end. The `Fine` is
written with no `<sound>` on purpose, to show the shape an instruction takes
when the score names something it does not draw. The complete file is
published as [`docs/examples/navigation.musicxml`](examples/navigation.musicxml)
and is validated by the test suite; only the `<direction>` elements are shown
here, each in the position within its measure that says when it applies.

```xml
<measure number="1">
  ...
  <direction placement="above">
    <direction-type><segno /></direction-type>
    <sound segno="segno" />
  </direction>
  ... the measure's notes ...
  <direction placement="above">
    <direction-type><words>To Coda</words></direction-type>
    <sound tocoda="coda" />
  </direction>
</measure>
<measure number="2">
  ... the measure's notes ...
  <direction placement="above">
    <direction-type><words>D.S. al Coda</words></direction-type>
    <sound dalsegno="segno" />
  </direction>
</measure>
<measure number="3">
  <direction placement="above">
    <direction-type><coda /></direction-type>
    <sound coda="coda" />
  </direction>
  ... the measure's notes ...
  <direction placement="above">
    <direction-type><words>Fine</words></direction-type>
  </direction>
</measure>
```

The things to note:

- The segno and the coda are written **before** their measure's notes and the
  instructions **after** them. That position is the only thing in the file
  that says whether a mark opens the measure or fires at the end of it.
- A sign is written as `<segno/>` or `<coda/>`, never as the word an engraver
  prints beside it — the word is a label for the sign, and the sign is the
  mark.
- The `Fine` carries no `<sound fine="yes"/>`. Rule 16's conditional half:
  the words are what the page prints and are always written; the `<sound>` is
  an assertion about playback and is written only where its target was read
  off the same score.
- No `<direction>` moves a single note or rest, for the same reason no
  `<barline>` does.

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
measure's `barDuration.quarterLength`. Count notes and rests only: a
`<forward>` is [inferred silence](#inferred-silence-rule-14) and holding it
against the meter would report the measure as adding up when the producer knows
it does not.

**Round-trip through a renderer.** Loading the file in an application that
reads tablature is what catches a mirrored string numbering, since the notes
come back on the wrong strings while everything else looks correct. Fermata
uses [alphaTab](https://alphatab.net/) for this, via
`server/tools/tab_extract/verify_musicxml.mjs`, which reports each file's bar,
voice and note counts along with the first note's MIDI value, string and fret.
Its `--onsets` flag adds every beat's playback position, which is how [Rule
14](#inferred-silence-rule-14)'s central assumption is checked: a note after a
`<forward>` has to sound where a note after a rest of the same duration would.
A loader that ignored the element would produce a file that still loads, still
validates, and plays every late-entering voice on the downbeat.

**Load the file, do not save it.** This check means opening the file, not
opening and re-saving it. A save by another program is that program's encoding
of the music, not this one's, and at least one major editor rewrites Rule 14's
`<forward>` elements into something that no longer carries the marking — with
the figures moving accordingly. The bound is in [Rule
14](#inferred-silence-rule-14). Whatever a producer states about its own file
is a statement about the bytes it wrote.

**What Fermata reports about its own transcriptions.** A transcription's
warnings and confidence live on the transcription record and in the API
response, not in the MusicXML — the emitted file is an ordinary score, and the
one thing in it that speaks about provenance is [Rule
14](#inferred-silence-rule-14)'s `<forward>`. The warnings say how many measures
fail Rule 8, in each direction, and how many notes were unwritable under Rule
11. The same Rule 8 counts are also returned as numbers — `bars_overfull`,
`bars_short`, `bars_defective` and `bars_measured` — so a consumer can compare
them against what its own MusicXML tooling makes of the file.

`bars_defective` counts a measure once whichever way it is wrong. A measure
with two voices can have one over its meter and the other under it, so
`bars_overfull + bars_short` double-counts such a measure and can exceed
`bars_measured`; `bars_defective` is the figure to compare against another
tool's count, and the one the reported confidence is derived from.

`bars_padded` is how many of those measures hold inferred silence,
`padded_bars` is which ones by number, and `inferred_rest_quarters` is how much
silence there is in quarter notes. These are the counterpart of the `<forward>`
elements in the file: a consumer that counts measures containing one gets
`bars_padded`, one that lists them gets `padded_bars`, and one that sums their
durations gets `inferred_rest_quarters`.

Every padded measure is also a short one, without exception: the padding only
fires for a voice that is under its meter, and the Rule 8 sum measures exactly
that pre-padding total. `bars_padded` can still be smaller than `bars_short` —
a measure with a single voice is never padded, and is emitted short — and could
in principle be larger only for a measure carrying no `<time>` at all, which is
not a thing a conforming file has.

`bars_unread` and `unread_bars` are the measures nothing was read from, reported
separately for the reason given in [Rule 14](#inferred-silence-rule-14): the
measure of rests they hold does add up, so counting them as Rule 8 defects would
make these figures disagree with the file, and *not* counting them anywhere let
a score read as nothing at all report every measure conforming. They are folded
into the reported confidence instead.

**What the rhythm confidence label means.** The label is the weaker of two
independent judgements about the same transcription, and the string states
both — the word first, then the clause the provenance earned, then the count.

*How the durations were obtained.* `high` means every staff system's durations
were decoded from the notehead, stem, flag, beam and rest glyphs the score
itself is engraved with — none of it inferred from spacing. `medium` means at
least one staff was read that way with something on it left unread — a glyph
outside the decoder's calibrated vocabulary, a notehead whose stem it could
not find, or a rest whose printed position did not say which value it was.
An augmentation dot that could not be bound to the notehead or rest it
belongs to (`dots_unassigned`) is disclosed on its own and does not gate this
label: a staff otherwise fully decoded still reads `high` with dots left
unbound, because a missing dot changes one note's length rather than the
staff's provenance. `mixed` means at least one staff's
durations were inferred from the horizontal gaps between noteheads instead;
`low` means every staff was. A transcription with **any** spacing-derived staff
can never present as fully read, however many other staves were decoded and
however cleanly its measures add up, because spacing is only evidence about
rhythm while the engraver's spacing is proportional — which a justified or
hand-adjusted system is not.

*Whether the measures add up.* `high` requires that **no** measure is
unreliable: every measure sums to its meter under Rule 8, and every measure
holds something that was read. `medium` covers any smaller fraction than a
quarter; at or above a quarter the label is `low overall`.

Rule 8 has no anacrusis model: a pickup measure — deliberately short of its
meter, with the missing beats made up by the piece's final measure — is
scored exactly like a mistake, so a demotion earned on bar 1 alone may just
be a pickup rather than a misread bar.

So an unqualified `high` is a claim a reader can check against the page, and it
is the strongest one this profile makes: not "few measures were in question"
but *none were*. Any known defect is stated whichever band it falls in — the
count never lives only in the label — but below the top band the headline word
moves too, because a word that contradicts the sentence under it is not a
disclosure.

The fraction is over `bars_defective` and `bars_unread` together, and each
measure counts once. That is why `bars_defective` counts a measure once
whichever way it is wrong: a measure over its meter in one voice and under it
in another would otherwise count twice, and a measure that is both defective
and unread would count twice again — either of which could put the fraction
above 1 and make the bands meaningless.

**How each measure's durations were obtained** is reported alongside them.
`rhythm_provenance` counts staff systems by source: `glyphs` means flags,
beams, dots and rest shapes were decoded; `spacing` means durations were
inferred from the horizontal gaps between noteheads, which is only as good as
the engraver's spacing being proportional and is wrong wherever a system was
justified to the margin or spaced by hand; `glyphs-degraded` means the staff
was read from its engraving with something on it left unread. `spacing_bars`
and `degraded_bars` are the measure numbers those systems produced. The counts
say how much of a transcription is in question, and only the lists say which of
it — which is the form of the fact a reader comparing against the PDF can use.

`staves_spacing_rhythm` and `staves_degraded_rhythm` are those first two counts
again, as fields of their own beside the measure lists they belong to.
`rhythm_provenance` is produced by the extractor and is not stored, so a
consumer reading a transcription back — which is every reading of it after the
first — has these two and not that. They are what the two measure lists are
counts of, and they are the pair that decides the provenance half of the rhythm
label above.

**A meter that was printed and refused** is reported by
`meter_digits_unreadable`: printed time signatures the decoder declined because
a glyph with no category sat among the digits it did read. This is not
recoverable from `time_signature_source`, which describes how the meter that
*is* reported was obtained and cannot say that a different, unread one is
printed on the page. The refusal exists because assembling a numeral out of the
digits that happened to be recognised produces a confident wrong meter: a 10/8
whose `0` the decoder cannot name reads as 1/8, and every measure in the score
is then barred against it.

`notes_no_stem` is how many filled noteheads were read with no stem attached,
across `staves_no_stem` notation staves. A note's flags and beams hang off its
stem, so for those notes there was nothing to count and each was emitted at the
longest duration its notehead alone allows — a quarter. That is a floor rather
than a reading, and because it is the longest of the candidate values such a
note always plays long and always pushes its measure towards Rule 8's overfull
side; it has also lost the stem direction that assigns it to a voice. A staff
carrying any of them is reported as `glyphs-degraded` rather than `glyphs`.

Every one of these figures is also stated in the warning prose, in the same
numbers. That is deliberate duplication rather than redundancy: a consumer
written before a field existed does not read it, and the warnings are a list of
strings that gets displayed as it is, so a count added without its sentence is
a measurement nobody is ever told.

**How the meter and key were obtained** is reported the same way, and for the
same reason: `time_signature` with `time_signature_source`, and `key_fifths`
with `key_signature_source`. A source of `glyph-decoded` or `auto-detected`
means the value was read off the page, `manual override` means the caller
supplied it, and anything beginning `not detected` means the value is an
assumption — 4/4, or no key signature. These are stored on the record rather
than only echoed on the response that extracted them, because a value that was
assumed has to still say so on the tenth reading of the same score, and none of
it is recoverable from the warning prose.

**The tuning is reported differently, because it is not known the same way.**
`tuning` is the six strings in use and `tuning_label` is a name — and the name
is found by matching text on the page, which is recognition of a label rather
than a reading of the tuning. `tuning_unread` is what makes the difference
sayable: printed tuning instructions found on the page and **not** applied to
`tuning`. Today that is a direction to tune down a half step and a capo, neither
of which is parsed; a non-empty list means `tuning` is known to be incomplete
and no consumer may describe it as having been read. Measured across the
library, 41 of the 100 scores carrying a `tuning_label` also carry one of these
— 9 the half-step direction, so the array is a semitone out, and 32 a capo, so
every sounding pitch is out. `tuning_unread` is detection only: it does not
change `tuning`, the emitted `<staff-tuning>`, or any pitch. Parsing these
properly is a separate piece of work.

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
- Vibrato, tremolo, palm muting, `<open-string>`, `<thumb-position>`,
  `<golpe>`, `<fingernails>`.
- Left-hand fingering and right-hand plucking indications (`<fingering>`,
  `<pluck>`).

**Rhythm and structure this profile does not model.**

- Tuplets (`<time-modification>`, `<tuplet>`). This is why Rule 2's divisions
  value is chosen to divide by 3 — so adding them later does not require
  changing it.
- Slurs (`<slur>`). Ties are covered — see [Rule 18](#ties-rule-18) — but a
  slur is a different mark: it joins notes of *different* pitch, and the
  equal pitch a tie joins is the only thing this extractor uses to tell one
  curve from the other.
- Grace notes, which the schema handles as a separate `<note>` branch with no
  `<duration>` at all. This is a live source of wrong arithmetic rather than
  an absence: a grace note is decoded as an ordinary note and given its
  written duration, so it adds time its bar does not have. Measured: "The
  Cosmic Wheel (Final Fantasy XI)" bar 13 is a slashed grace note slurred
  into a whole note, and comes out `:16 3.1 :1 5.1` — 4.25 quarters in a 4/4
  bar, one of that score's eight overfull bars.
- A `<segno/>` or `<coda/>` written inside a `<barline>` rather than as a
  `<direction>`. The schema allows both; this profile writes the direction,
  which is where a sign that is not on a barline has to go anyway — see
  [Rule 16](#navigation-marks-rule-16).
- `<sound>` as a direct child of `<measure>`, and every `<sound>` attribute
  other than `tempo` and the four jumps of Rule 16.
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
