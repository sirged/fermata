// Regression check for extract_tab.py's alphaTex output: parse every
// generated .tex file with the SAME alphaTab importer the web player
// actually uses, so a change that produces syntactically-plausible-looking
// but unparseable alphaTex (e.g. the dotted-duration bug this check was
// added for - see extract_tab.py's _fmt_beat) gets caught immediately
// instead of only surfacing later when a real page fails to load.
//
// Usage:
//   node verify_tex.mjs <path-to-alphaTab.mjs> <tex-file-or-glob-dir> [...more]
//
// Each argument after the alphaTab path is either a .tex file or a
// directory (all *.tex files directly inside it are checked). Prints one
// JSON line per file: {file, ok, bars, beats, notes} or {file, ok: false,
// error}. Exits 1 if any file failed to parse.
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
            let bars = 0, beats = 0, notes = 0;
            for (const track of score.tracks) {
                for (const staff of track.staves) {
                    bars = Math.max(bars, staff.bars.length);
                    for (const bar of staff.bars) {
                        for (const voice of bar.voices) {
                            beats += voice.beats.length;
                            for (const beat of voice.beats) notes += beat.notes.length;
                        }
                    }
                }
            }
            Object.assign(result, { ok: true, bars, beats, notes });
        } catch (e) {
            anyFailed = true;
            Object.assign(result, { ok: false, error: String(e && e.stack ? e.stack : e) });
        }
        console.log(JSON.stringify(result));
    }
    if (anyFailed) process.exitCode = 1;
}

main();
