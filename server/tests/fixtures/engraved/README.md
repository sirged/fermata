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

## What each one is for

| fixture | shape it covers |
| --- | --- |
| `notation_and_tab` | notation over tablature, two pages, D major, a dotted note, beamed sixteenths, a whole-note chord; every bar adds up |
| `rests_and_flags` | every rest value and every flag hook the SMuFL map calibrates, including 32nds |
| `tab_only` | tablature with no notation staff — the honest fall back to spacing-inferred rhythm |
| `tab_only_short_last_system` | eight bars of which six are read: an unstretched final system falls under the length floor and is lost silently |
| `two_voices` | a melody stems-up over an accompaniment stems-down in one bar |
| `unison_voices` | `two_voices` with the upper voice dropped to the lower voice's own pitch — a unison every beat, drawn as the same notehead glyph twice at the identical position, once per voice's stem (issue #116) |
| `three_voices` | a melody, an arpeggiated accompaniment and a sustained whole-note bass, all three attacking together on beat one (issue #133) |
| `tuplet_and_tie` | a triplet (not detected, so its bar is reported overfull) and a tie across a barline |
| `drop_d` | a non-standard tuning named in the score's text, and a metronome mark |
| `defective_bars` | bars over their meter, bars under it, and a bar wrong in both directions at once |
| `volta` | a repeat with "1." / "2." ending brackets close under the staff |
| `harmonics_dense` | two uncalibrated harmonic noteheads on a system dense enough that the unknown-glyph ratio cannot see them |
| `notation_only` | standard notation with no tablature — refused, with the reason |
| `four_sharps_in_three_four` | four sharps between the clef and the meter, pushing the meter's digits past the window a clef and a meter alone need — in 3/4, so failing to read it misplaces every barline |
| `hidden_opening_meter` | an invisible opening `<time>` and a printed 3/4 later: the only meter that can be read is not the opening one |
| `hidden_opening_meter_matches_the_default` | the same shape, but the only meter read anywhere is a 4/4 that happens to match the assumed default — the "opening not read" warning must stay quiet |
| `mid_system_meter_change` | a change to 2/4 engraved part-way along the first system, and back to 4/4 at the second — the bars ahead of a change are not in it |
| `mid_system_key_and_meter_change` | four sharps printed behind a barline, pushing a mid-system meter change's digits past the flat reach a mid-system reader alone needs — the mid-system counterpart of `four_sharps_in_three_four` |
| `multidigit_meter` | a 12/8 meter — a numerator that needs two digit glyphs stacked at one x column, which is exactly the shape a missing digit in a font's calibration table (issue #84) turns into a confident wrong meter instead of a detected gap |

Two fixtures are **synthesised** rather than engraved, because no engraver
produces them on purpose. The script builds both, and their `/Creator` says
so.

| fixture | shape it covers |
| --- | --- |
| `raster_scan` | `notation_and_tab` flattened to an image — refused as a scan |
| `fake_music_font` | a page whose "music font" is an unembedded text font drawing the letters A–H, with a ToUnicode CMap claiming they are SMuFL music symbols as its only credential — refused |

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
