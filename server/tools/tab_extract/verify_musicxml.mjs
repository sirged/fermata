// Regression check for the MusicXML emitter: load a .musicxml file (or a
// whole directory of them) with the SAME alphaTab loader the web player uses,
// so a change that produces schema-valid but musically wrong output - a
// mirrored string numbering, a <backup> that does not return to the start of
// the measure, a voice alphaTab does not see as a voice - gets caught here
// rather than surfacing later as a page that renders wrong. This cannot be
// done from the Python test suite: it needs the real importer, not a
// re-implementation of it.
//
// Usage:
//   node verify_musicxml.mjs <path-to-alphaTab.mjs> [--onsets] <file-or-dir> [...]
//
// Prints one JSON line per file: {file, ok, bars, voices, beats, notes,
// dottedBeats, firstNoteMidi, firstNoteString, firstNoteFret, tuning} or
// {file, ok: false, error}. Exits 1 if any file failed to load.
//
// With --onsets each line also carries `onsets`: one entry per beat, as
// [barIndex, voiceIndex, playbackStart, playbackDuration, isRest]. That is
// what checks the profile's Rule 14 - inferred silence is written as
// <forward>, which is only safe if a consumer advances its position across
// one, so a note following a <forward> has to sound where a note following a
// rest of the same duration would. A file whose leading <forward> is ignored
// still loads, still validates, and plays every late-entering voice on the
// downbeat, so nothing but a position tells you.
//
// With --repeats each line also carries `repeats`: per master bar (1-based
// bar number), {isRepeatStart, isRepeatEnd, repeatCount, alternateEndings}
// read straight off alphaTab's own MasterBar - alternateEndings is the
// BITMASK alphaTab itself uses (bit n-1 set means this bar belongs to ending
// n), not a count - and `tickLookup`: the PLAYBACK bar order (1-based,
// repeats and all) read from MidiFileGenerator's own tickLookup.masterBars,
// which is the only thing here that proves a file plays right rather than
// merely parses right (issue #134 S4.2 / docs Rule 15).
//
// With --ties each line also carries `ties`: one entry per sounding note, as
// [barIndex, voiceIndex, string, fret, isTieOrigin, isTieDestination,
// harmonicType], and `noteOns`: the MIDI note-on events MidiFileGenerator
// produces, as [tick, key]. That is what checks the profile's Rule 18 - a tie
// is only a tie if the second note is NOT struck, and alphaTab reads the
// `<tied>` half of the encoding and ignores the `<tie>` half entirely, so a
// file carrying only the latter loads, validates and re-strikes the note.
// Nothing but the note-on stream tells you which happened. harmonicType is
// reported for the opposite reason: alphaTab's importer consumes `<harmonic>`
// and does nothing with it (`case "harmonic": break;`), so this is the field
// that keeps Rule 19's statement about the renderer honest rather than
// hopeful.
//
// firstNoteString/Fret and tuning are the ones that matter beyond "it
// parsed": MusicXML numbers staff LINES from the bottom and STRINGS from the
// top, so a file with the two confused still validates against the schema and
// still loads here - but comes back with its notes on mirrored strings, which
// is visible in these fields and nowhere else.
//
// Example (from this directory):
//   node verify_musicxml.mjs ../../../web/node_modules/@coderline/alphatab/dist/alphaTab.mjs out
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

async function main() {
    const [alphaTabPath, ...rest] = process.argv.slice(2);
    const wantOnsets = rest.includes("--onsets");
    const wantRepeats = rest.includes("--repeats");
    const wantTies = rest.includes("--ties");
    const targets = rest.filter(
        (a) => a !== "--onsets" && a !== "--repeats" && a !== "--ties");
    if (!alphaTabPath || targets.length === 0) {
        console.error("usage: node verify_musicxml.mjs <alphaTab.mjs> [--onsets] <file-or-dir> [...]");
        process.exit(2);
    }

    const files = [];
    for (const target of targets) {
        const st = fs.statSync(target);
        if (st.isDirectory()) {
            for (const name of fs.readdirSync(target)) {
                if (name.endsWith(".musicxml") || name.endsWith(".xml")) {
                    files.push(path.join(target, name));
                }
            }
        } else {
            files.push(target);
        }
    }

    const mod = await import(pathToFileURL(path.resolve(alphaTabPath)).href);
    const { ScoreLoader } = mod.importer;
    const { Settings } = mod;

    let anyFailed = false;
    for (const file of files) {
        const result = { file: path.basename(file) };
        try {
            const bytes = new Uint8Array(fs.readFileSync(file));
            const score = ScoreLoader.loadScoreFromBytes(bytes, new Settings());
            let bars = 0, voices = 0, beats = 0, notes = 0, dottedBeats = 0;
            let firstNoteMidi = null, firstNoteString = null, firstNoteFret = null;
            let tuning = null;
            const onsets = [];
            const ties = [];
            for (const track of score.tracks) {
                for (const staff of track.staves) {
                    bars = Math.max(bars, staff.bars.length);
                    if (tuning === null && staff.tuning && staff.tuning.length) {
                        tuning = Array.from(staff.tuning);
                    }
                    for (const bar of staff.bars) {
                        for (const voice of bar.voices) {
                            // alphaTab pads every bar out to the staff's
                            // busiest voice count with an auto-inserted rest;
                            // isEmpty marks exactly that filler, so skipping
                            // it counts only voices the file really wrote.
                            if (voice.isEmpty) continue;
                            voices += 1;
                            for (const beat of voice.beats) {
                                beats += 1;
                                if (beat.dots > 0) dottedBeats += 1;
                                if (wantOnsets) {
                                    onsets.push([
                                        bar.index, voice.index,
                                        beat.playbackStart, beat.playbackDuration,
                                        beat.isRest,
                                    ]);
                                }
                                for (const note of beat.notes) {
                                    notes += 1;
                                    if (wantTies) {
                                        ties.push([
                                            bar.index, voice.index,
                                            note.string, note.fret,
                                            note.isTieOrigin, note.isTieDestination,
                                            note.harmonicType,
                                        ]);
                                    }
                                    if (firstNoteMidi === null) {
                                        firstNoteMidi = note.realValue;
                                        firstNoteString = note.string;
                                        firstNoteFret = note.fret;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Object.assign(result, {
                ok: true, bars, voices, beats, notes, dottedBeats,
                firstNoteMidi, firstNoteString, firstNoteFret, tuning,
            });
            if (wantOnsets) result.onsets = onsets;
            if (wantTies) {
                result.ties = ties;
                const midiFile = new mod.midi.MidiFile();
                const handler = new mod.midi.AlphaSynthMidiFileHandler(midiFile);
                const generator = new mod.midi.MidiFileGenerator(
                    score, new Settings(), handler);
                generator.generate();
                const noteOns = [];
                for (const event of midiFile.events) {
                    if (event.constructor.name === "NoteOnEvent") {
                        noteOns.push([event.tick, event.noteKey]);
                    }
                }
                result.noteOns = noteOns;
            }
            if (wantRepeats) {
                const repeats = score.masterBars.map((mb) => ({
                    isRepeatStart: mb.isRepeatStart,
                    isRepeatEnd: mb.isRepeatEnd,
                    repeatCount: mb.repeatCount,
                    alternateEndings: mb.alternateEndings,
                }));
                const midiFile = new mod.midi.MidiFile();
                const handler = new mod.midi.AlphaSynthMidiFileHandler(midiFile);
                const generator = new mod.midi.MidiFileGenerator(score, new Settings(), handler);
                generator.generate();
                const tickLookup = generator.tickLookup.masterBars.map(
                    (item) => item.masterBar.index + 1);
                Object.assign(result, { repeats, tickLookup });
            }
        } catch (e) {
            anyFailed = true;
            Object.assign(result, { ok: false, error: String(e && e.stack ? e.stack : e) });
        }
        console.log(JSON.stringify(result));
    }
    if (anyFailed) process.exitCode = 1;
}

main();
