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
| `tab_only` | tablature with no notation staff — the honest fall back to spacing-inferred rhythm |
| `two_voices` | a melody stems-up over an accompaniment stems-down in one bar |
| `tuplet_and_tie` | a triplet (not detected, so its bar is reported overfull) and a tie across a barline |
| `drop_d` | a non-standard tuning named in the score's text, and a metronome mark |
| `defective_bars` | bars over their meter, bars under it, and a bar wrong in both directions at once |
| `notation_only` | standard notation with no tablature — refused, with the reason |
| `raster_scan` | `notation_and_tab` flattened to an image — refused as a scan |

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
- **Diamond and harmonic noteheads**, deliberately absent from the SMuFL map
  because which codepoint means which was not established.
- **Scale.** The library's reference score is 50 bars of real two-voice
  fingerstyle writing. The fixture with two voices is eight contrived bars,
  and a regression that only appears in density will still only appear
  there.
