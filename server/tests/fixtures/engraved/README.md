# Engraved test fixtures

Small scores, engraved here rather than sourced from anywhere, so the
extraction tests can run in CI.

Every test that needed a real engraved PDF used to read one from the
maintainer's personal library. That library is correctly gitignored, so 36
tests — the extractor's, the transcription API's and the glyph decoder's —
skipped everywhere except a developer's own machine, and a change that broke
reading tablature out of a PDF merged green.

## Provenance

The whole chain is in the repository:

    engrave_fixtures.py  ->  <name>.musicxml  ->  MuseScore  ->  <name>.pdf

- `server/tools/tab_extract/engrave_fixtures.py` writes the MusicXML and
  drives the engraver. `--check` verifies the committed MusicXML still
  matches the script, which a test asserts, so a fixture cannot quietly stop
  being regenerable.
- The MusicXML beside each PDF is what was asked for. Tests compare the
  extractor's answer against it, which measures accuracy against ground
  truth rather than against the extractor's own previous output.
- The PDFs here were engraved with **MuseScore 4.6.3**. Re-engraving with a
  different version moves coordinates and can change what the tests measure,
  so regenerate deliberately and re-read the assertions when you do.

Nothing purchased or downloaded can go in this directory. That is how the
gap this directory exists to close was created.

**`<name>.musicxml` is the engraving SOURCE, not a transcription.** It is
what MuseScore was asked to draw - for a tab-and-notation fixture, that
includes the tab staff's own notes, written as plain `<pitch>` with no
`<string>`/`<fret>` at all, because MuseScore frets them itself while
engraving. It is therefore not a conforming file under
docs/musicxml-tab-profile.md (Rule 9 requires `<string>`/`<fret>` on every
sounding note), and must never be fed to a MusicXML consumer - alphaTab
included - as if it were the extractor's output. Doing exactly that, once,
crashed alphaTab's `TabBarRenderer.collectSpaces` during paint (issue #165).

One fixture (`navigation`, so far) also carries `<name>.transcription.musicxml`: the actual
output of `tabextract.extract()` run on the committed PDF, regenerated and
checked byte for byte (modulo a pinned `<encoding-date>`) by
`engrave_fixtures.py --check`'s `write_transcriptions()`. This is the real
ground truth, always a single TAB staff with `<string>`/`<fret>` on every
note - see `TRANSCRIPTION_FIXTURES` in `engrave_fixtures.py` for which names
have one. It exists for consumers that cannot call the Python extractor at
test time, currently the web browser test suite
(`web/tests/browser/fixtures/navigation-score.js`), which needs real
transcription bytes to feed the real alphaTab importer through.

## What each one is for

| fixture | shape it covers |
| --- | --- |
| `notation_and_tab` | notation over tablature, two pages, D major, a dotted note, beamed sixteenths, a whole-note chord; every bar adds up |
| `rests_and_flags` | every rest value and every flag hook the SMuFL map calibrates, including 32nds |
| `thirty_second_beams` | three-stroke beams — 32nds written under a beam, beside 32nds written on a flag, so which of the two was misread is decidable (#113) |
| `tab_only` | tablature with no notation staff — the honest fall back to spacing-inferred rhythm |
| `tab_only_short_last_system` | eight bars of which six are read: an unstretched final system falls under the length floor and is lost silently |
| `two_voices` | a melody stems-up over an accompaniment stems-down in one bar |
| `unison_voices` | `two_voices` with the upper voice dropped to the lower voice's own pitch — a unison every beat, drawn as the same notehead glyph twice at the identical position, once per voice's stem (issue #116) |
| `unison_in_chord` | `unison_voices` with the upper voice thickened into a chord, so the unison is one MEMBER of it — three noteheads at the onset, two positions, and only **two** tab digits, because a unison is one plucked string and prints one number however many voices sound it (issue #137) |
| `three_voices` | a melody, an arpeggiated accompaniment and a sustained whole-note bass, all three attacking together on beat one (issue #133) |
| `tuplet_and_tie` | a triplet (not detected, so its bar is reported overfull) and a tie across a barline |
| `drop_d` | a non-standard tuning named in the score's text, and a metronome mark |
| `defective_bars` | bars over their meter, bars under it, and a bar wrong in both directions at once |
| `volta` | a repeat with "1." / "2." ending brackets close under the staff |
| `repeat_structure` | a forward repeat opening the span, three numbered endings (one two bars long, one closed with an open hook), a mid-score double barline, and a closing final barline |
| `adjacent_endings` | an ending that discontinues (no closing hook) with the very next ending's own opening hook at the same barline, no bar in between — the one shape that forces `stop` vs `discontinue` to actually tell a bracket's own closing hook apart from its neighbour's opening one, rather than agreeing with it by luck (issue #134 adversarial review, item 9) |
| `navigation` | a segno, a "To Coda", a "D.S. al Coda", the coda sign, a "Fine" and a "D.C. al Fine" — the marks of Rule 16, including **a segno drawn as a SMuFL codepoint** — the other of the two routes a segno reaches this decoder by, the maintainer's library drawing all 88 of its own as Finale glyph IDs — and both text alignments: three instructions engraved left-aligned at the barline they close (so their text runs on into the next bar) and one engraved a beat inside its own bar |
| `harmonics_dense` | two uncalibrated harmonic noteheads on a system dense enough that the unknown-glyph ratio cannot see them |
| `notation_only` | standard notation with no tablature — refused, with the reason |
| `four_sharps_in_three_four` | four sharps between the clef and the meter, pushing the meter's digits past the window a clef and a meter alone need — in 3/4, so failing to read it misplaces every barline |
| `hidden_opening_meter` | an invisible opening `<time>` and a printed 3/4 later: the only meter that can be read is not the opening one |
| `hidden_opening_meter_matches_the_default` | the same shape, but the only meter read anywhere is a 4/4 that happens to match the assumed default — the "opening not read" warning must stay quiet |
| `mid_system_meter_change` | a change to 2/4 engraved part-way along the first system, and back to 4/4 at the second — the bars ahead of a change are not in it |
| `mid_system_key_and_meter_change` | four sharps printed behind a barline, pushing a mid-system meter change's digits past the flat reach a mid-system reader alone needs — the mid-system counterpart of `four_sharps_in_three_four` |
| `multidigit_meter` | a 12/8 meter — a numerator that needs two digit glyphs stacked at one x column, which is exactly the shape a missing digit in a font's calibration table (issue #84) turns into a confident wrong meter instead of a detected gap |

Three fixtures are **synthesised** rather than engraved, because no engraver
produces them on purpose. The script builds all three, and their `/Creator`
says so.

| fixture | shape it covers |
| --- | --- |
| `raster_scan` | `notation_and_tab` flattened to an image — refused as a scan |
| `fake_music_font` | a page whose "music font" is an unembedded text font drawing the letters A–H, with a ToUnicode CMap claiming they are SMuFL music symbols as its only credential — refused |
| `unmapped_meter_digit` | `multidigit_meter` with one entry of its music font's ToUnicode CMap rewritten, so the `2` of its 12/8 draws as a SMuFL codepoint outside the decoder's calibrated tables — what a Finale subset with an unmapped glyph ID, or a Sibelius one with an unmapped PUA name, looks like from this side (issue #129). The engraving, the outlines and every coordinate are the original's; only that one mapping differs. Before the refusal it read as a confident 1/8 "read directly from the time-signature digit glyphs" |

## What they cannot cover

Stated here as well as in `test_engraved_fixtures.py`, because a fixture that
looks like coverage and is not is worse than none:

- **Maestro and Opus.** Finale's and Sibelius's music fonts cannot be
  committed either, so the glyph-ID fingerprint and the PUA name map still
  need `FERMATA_TEST_LIBRARY`. What runs here is the third calibration, the
  SMuFL codepoint map, which is what a free engraver draws with.
- **A CFF-flavour embedding of Maestro or Opus**, one of the reasons those
  two fall back to spacing. It does not arise for a SMuFL font — nothing on
  that path reads outlines at all — so there is nothing here to exercise it
  with.
- **Reading a raster page.** The rasterised fixture proves extraction
  declines a scan, not that anything reads one.
- **Reading a diamond or harmonic notehead.** Those codepoints are
  deliberately absent from the SMuFL map, because which one means which was
  not established. `harmonics_dense` covers the *reporting* of that gap, not
  its closure — a score with harmonics still loses their durations.
- **A note shorter than a 32nd.** `_beam_count_near` follows a beam stack to
  any depth and counts a four-stroke group correctly - there is a unit test
  on constructed geometry for it - but nothing downstream can carry the
  answer, because the emitter's whole duration vocabulary stops at a 32nd
  (`musicxml.TYPE_NAMES`). IF a genuine 64th were engraved, it would be read
  as four levels and emitted as a 32nd - but the library holds no genuine
  64th to confirm that against: the only stems this decoder reads at four
  levels are Troian Beauty p2's grace notes, which are a pre-existing,
  unrelated over-count (a grace beam read past its own group), not a real
  64th. `thirty_second_beams` deliberately does not contain a 64th either: a
  bar asserting one would be pinning a different limit than the one measured
  here.
- **A repeat bracket welded into a phantom staff line.** The engraver used
  here leaves a visible gap in an ending bracket, whereas Finale's abut
  exactly; that geometry is covered by a synthetic page built inside
  `test_engraved_fixtures.py` instead, and the real examples are in the
  maintainer's library.
- **Crowded engraving.** A system compressed enough that a stem lands within
  reading distance of the next bar's printed meter — which is what makes a
  mid-system meter change attributable to the wrong barline. This engraver
  spaces the fixtures too generously for it; the shape lives in the
  maintainer's library and is covered by a test that skips without one.
- **A courtesy time signature at the end of a system.** The key and meter for
  the system that follows, engraved as the last thing on this one, behind
  enough accidentals to fall inside a mid-system reach - the shape that
  proves widening that reach is not safe on its own. This engraver does not
  print courtesy signatures the way the maintainer's library scores do; the
  shape lives there and is covered by a test that skips without a library.
- **Scale.** The library's reference score is 50 bars of real two-voice
  fingerstyle writing. The fixture with two voices is eight contrived bars,
  and a regression that only appears in density will still only appear
  there.
