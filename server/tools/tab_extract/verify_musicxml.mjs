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
//   node verify_musicxml.mjs <path-to-alphaTab.mjs> <file-or-dir> [...more]
//
// Prints one JSON line per file: {file, ok, bars, voices, beats, notes,
// dottedBeats, firstNoteMidi, firstNoteString, firstNoteFret, tuning} or
// {file, ok: false, error}. Exits 1 if any file failed to load.
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
    const [alphaTabPath, ...targets] = process.argv.slice(2);
    if (!alphaTabPath || targets.length === 0) {
        console.error("usage: node verify_musicxml.mjs <alphaTab.mjs> <file-or-dir> [...]");
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
                                for (const note of beat.notes) {
                                    notes += 1;
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
        } catch (e) {
            anyFailed = true;
            Object.assign(result, { ok: false, error: String(e && e.stack ? e.stack : e) });
        }
        console.log(JSON.stringify(result));
    }
    if (anyFailed) process.exitCode = 1;
}

main();
