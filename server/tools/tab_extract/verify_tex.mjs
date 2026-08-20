// Regression check for tabextract's alphaTex output: parse a .tex file (or
// a whole directory of them) with the SAME alphaTab importer the web player
// actually uses, so a change that produces syntactically-plausible-looking
// but unparseable alphaTex (e.g. a dotted-duration bug where ":8." reaches
// the importer instead of the valid ":8 ...{d}" beat-effect form) gets
// caught immediately instead of only surfacing later when a real page fails
// to load. This can't be done from the Python test suite - it needs the
// real JS importer, not a re-implementation of alphaTex's grammar.
//
// Usage:
//   node verify_tex.mjs <path-to-alphaTab.mjs> <tex-file-or-glob-dir> [...more]
//
// Each argument after the alphaTab path is either a .tex file or a
// directory (all *.tex files directly inside it are checked). Prints one
// JSON line per file: {file, ok, bars, beats, notes, dottedBeats, voices,
// firstNoteMidi} or {file, ok: false, error}. Exits 1 if any file failed to
// parse. dottedBeats, voices and firstNoteMidi round out the things a
// syntax-only check can't catch: that a `{d}`/`{dd}` beat effect actually
// produced a dotted beat rather than just parsing without error, that a
// `\voice` separator really did land the beats after it in a SECOND
// concurrent voice (voices counts more than one per bar only if it did), and
// that tuning was emitted in the string order alphaTex expects (a mirrored
// \tuning line parses fine but gives every note the wrong pitch).
//
// Example (from this directory, against the web project's own alphaTab):
//   node verify_tex.mjs ../../../web/node_modules/@coderline/alphatab/dist/alphaTab.mjs out
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

async function main() {
    const [alphaTabPath, ...targets] = process.argv.slice(2);
    if (!alphaTabPath || targets.length === 0) {
        console.error("usage: node verify_tex.mjs <alphaTab.mjs> <tex-file-or-dir> [...]");
        process.exit(2);
    }

    const files = [];
    for (const target of targets) {
        const st = fs.statSync(target);
        if (st.isDirectory()) {
            for (const name of fs.readdirSync(target)) {
                if (name.endsWith(".tex")) files.push(path.join(target, name));
            }
        } else {
            files.push(target);
        }
    }

    const mod = await import(pathToFileURL(path.resolve(alphaTabPath)).href);
    const { AlphaTexImporter } = mod.importer;
    const { Settings } = mod;

    let anyFailed = false;
    for (const file of files) {
        const tex = fs.readFileSync(file, "utf-8");
        const result = { file };
        try {
            const importer = new AlphaTexImporter();
            importer.logErrors = false;
            importer.initFromString(tex, new Settings());
            const score = importer.readScore();
            let bars = 0, beats = 0, notes = 0, dottedBeats = 0, voices = 0;
            let firstNoteMidi = null;
            for (const track of score.tracks) {
                for (const staff of track.staves) {
                    bars = Math.max(bars, staff.bars.length);
                    for (const bar of staff.bars) {
                        for (const voice of bar.voices) {
                            // alphaTab gives EVERY bar as many voices as the
                            // busiest bar on the staff has, padding the ones
                            // a bar does not use with a single auto-inserted
                            // rest. Those are the importer's own filler, not
                            // beats the transcription emitted, so counting
                            // them would put this over the emitted total by
                            // one per unused voice. isEmpty marks exactly
                            // that filler: a voice the transcription really
                            // did write, even one holding only rests, comes
                            // back isEmpty === false.
                            if (voice.isEmpty) continue;
                            voices += 1;
                            for (const beat of voice.beats) {
                                beats += 1;
                                if (beat.dots > 0) dottedBeats += 1;
                                for (const note of beat.notes) {
                                    notes += 1;
                                    if (firstNoteMidi === null) firstNoteMidi = note.realValue;
                                }
                            }
                        }
                    }
                }
            }
            Object.assign(result, { ok: true, bars, beats, notes, dottedBeats, voices, firstNoteMidi });
        } catch (e) {
            anyFailed = true;
            Object.assign(result, { ok: false, error: String(e && e.stack ? e.stack : e) });
        }
        console.log(JSON.stringify(result));
    }
    if (anyFailed) process.exitCode = 1;
}

main();
