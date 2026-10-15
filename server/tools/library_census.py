"""Measure what transcription actually yields over a library of PDFs.

Usage:
    python library_census.py <library-dir> [--csv PATH]

For every *.pdf under the directory (folders named .fermata* or holding the
word "trash" are skipped, case-insensitively) this runs
fermata.tabextract.analyze and, when the PDF has a readable tab staff,
fermata.tabextract.extract. It prints a summary table (PDFs with a readable
tab staff, rhythm confidence, scores with every bar adding up, bars that do
not add up, how the tuning was found), a per-score histogram of the share of
measured bars that are defective, and the elapsed time. With --csv it also
writes one row per PDF.

The counting rules are the decoder's own, not re-implemented here: a bar is
defective when the decoder says it does not add up to its meter, and a bar
whose voice had to be padded with silence counts as short. This tool only
adds up the ExtractionResult fields the decoder already reports. The README's
yield table is re-derived from this output, not kept by hand.
"""
import argparse
import csv
import logging
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)  # fontTools logs through logging, not warnings

import pymupdf  # noqa: E402

# PyMuPDF prints its own notices (the fitz deprecation, odd timestamps) to
# stderr through its message hook, not through the warnings module.
pymupdf.set_messages(path=os.devnull)
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

from fermata import tabextract  # noqa: E402

warnings.filterwarnings("ignore")

FIELDS = [
    "file", "status", "bars", "bars_measured", "bars_defective",
    "bars_overfull", "bars_short", "bars_padded", "bars_unread", "notes",
    "rhythm_confidence", "time_signature_source", "key_signature_source",
    "tuning_source", "warnings",
]

BUCKETS = [("0%", lambda s: s == 0), ("<10%", lambda s: s < 0.10),
           ("<25%", lambda s: s < 0.25), ("<50%", lambda s: s < 0.50),
           (">=50%", lambda s: True)]


def find_pdfs(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        parts = [q.lower() for q in p.relative_to(root).parts[:-1]]
        if any(q.startswith(".fermata") or "trash" in q for q in parts):
            continue
        yield p


def census_one(path: Path, root: Path) -> dict:
    row = {"file": path.relative_to(root).as_posix()}
    info = tabextract.analyze(str(path))
    if not info.get("extractable"):
        row["status"] = "raster" if not info.get("vector") else "notation-only"
        return row
    res = tabextract.extract(str(path))
    if not res.extractable:
        row["status"] = "not-extractable"
        return row
    conf = (res.confidence or {}).get("rhythm") or ""
    row.update(
        status="extracted", bars=res.bars, bars_measured=res.bars_measured,
        bars_defective=res.bars_defective, bars_overfull=res.bars_overfull,
        bars_short=res.bars_short, bars_padded=res.bars_padded,
        bars_unread=res.bars_unread, notes=res.notes,
        rhythm_confidence=(conf.split() or [""])[0].strip(" -:,;").lower(),
        time_signature_source=res.time_signature_source,
        key_signature_source=res.key_signature_source,
        tuning_source=res.tuning_source, warnings=len(res.warnings or []))
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Count how much of a PDF library transcribes, and how "
                    "well the decoded bars add up.")
    ap.add_argument("library", type=Path, help="library directory to scan")
    ap.add_argument("--csv", type=Path, metavar="PATH",
                    help="write one row per PDF to this CSV file")
    args = ap.parse_args(argv)
    if not args.library.is_dir():
        ap.error(f"not a directory: {args.library}")

    start = time.monotonic()
    rows, errors = [], 0
    for path in find_pdfs(args.library):
        try:
            rows.append(census_one(path, args.library))
        except Exception as exc:  # one bad file must not end the run
            errors += 1
            rows.append({"file": path.relative_to(args.library).as_posix(),
                         "status": f"error: {type(exc).__name__}"})
    elapsed = time.monotonic() - start

    ex = [r for r in rows if r["status"] == "extracted"]
    n_notation = sum(r["status"] == "notation-only" for r in rows)
    n_raster = sum(r["status"] == "raster" for r in rows)
    n_other = len(rows) - len(ex) - n_notation - n_raster - errors
    conf = {k: sum(r["rhythm_confidence"] == k for r in ex)
            for k in ("high", "medium", "low")}
    clean = sum(r["bars_defective"] == 0 for r in ex)
    measured = sum(r["bars_measured"] for r in ex)
    defective = sum(r["bars_defective"] for r in ex)
    over = sum(r["bars_overfull"] for r in ex)
    short = sum(r["bars_short"] for r in ex)
    padded = sum(r["bars_padded"] for r in ex)
    unread = sum(r["bars_unread"] for r in ex)
    tuned = sum(r["tuning_source"] != tabextract.TUNING_ASSUMED_STANDARD
                for r in ex)

    extra = [f"{n_notation} notation-only", f"{n_raster} raster scan"]
    if n_other:
        extra.append(f"{n_other} not extractable")
    if errors:
        extra.append(f"{errors} errors")
    print("| Measure | Result |")
    print("| --- | --- |")
    print(f"| PDFs with a readable tab staff | {len(ex)} of {len(rows)} "
          f"({', '.join(extra)}) |")
    print(f"| Rhythm confidence | high {conf['high']}, medium {conf['medium']}, "
          f"low {conf['low']} |")
    print(f"| Scores with every bar adding up | {clean} of {len(ex)} |")
    print(f"| Bars that do not add up | {defective:,} of {measured:,} "
          f"({over:,} overfull, {short:,} short of which {padded:,} are "
          f"padded voices, {unread:,} unread) |")
    print(f"| Tuning read from the score | {tuned} of {len(ex)}; the other "
          f"{len(ex) - tuned} assume standard |")
    print()
    print("Defective share of measured bars, per score:")
    counts = dict.fromkeys((b for b, _ in BUCKETS), 0)
    for r in ex:
        share = r["bars_defective"] / r["bars_measured"] if r["bars_measured"] else 0.0
        for name, test in BUCKETS:
            if test(share):
                counts[name] += 1
                break
    for name, c in counts.items():
        print(f"  {name:>6}  {c}")
    print()
    print(f"Elapsed: {elapsed:.1f}s")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, restval="")
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
