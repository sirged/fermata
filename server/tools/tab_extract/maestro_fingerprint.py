"""Re-derive glyph_rhythm.MAESTRO_GLYF_DIGESTS from a library of PDFs.

The Maestro GID map is only meaningful because a given glyph ID's outline is
byte-identical across every file produced by the calibrated Finale export
pipeline. This tool both CHECKS that property and prints the digest table
glyph_rhythm gates on, so the fingerprint can be regenerated after a Finale
upgrade or extended to admit a second export pipeline.

Usage:
    python maestro_fingerprint.py <library-root> [more-roots...]

It walks every *.pdf under each root, extracts every embedded font resource
named "Maestro", slices its `glyf` table per glyph ID using the `loca`
offsets, and hashes each mapped GID's raw bytes.

Read the output in this order:

  1. "GIDs with MORE THAN ONE distinct outline" MUST be empty. A GID listed
     there is no longer a stable key for this family: the subsets in the
     scanned corpus disagree about what that glyph is. The fix is to REMOVE
     that GID from MAESTRO_GID_MAP (and re-derive what it should be by
     rendering outlines, as the map's own docstring describes) - NOT to pick
     whichever digest appeared most often, which would bless one pipeline
     and silently mis-decode the other.

  2. "mapped-and-present GIDs per resource" sets the floor for
     MAESTRO_FINGERPRINT_MIN_GLYPHS: it must stay comfortably below the
     minimum, since a subset only fills the glyphs its page actually uses.

  3. The printed table can then replace MAESTRO_GLYF_DIGESTS verbatim.
"""
import argparse
import collections
import hashlib
import io
import sys
from pathlib import Path

import fitz
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fermata.glyph_rhythm import MAESTRO_GID_MAP  # noqa: E402


def scan(roots):
    digests = collections.defaultdict(collections.Counter)
    examples = collections.defaultdict(dict)
    present_counts = []
    files = 0
    resources = 0
    for root in roots:
        for path in sorted(Path(root).rglob("*.pdf")):
            try:
                doc = fitz.open(path)
            except Exception:
                continue
            try:
                seen = set()
                hit = False
                for pno in range(doc.page_count):
                    try:
                        fonts = doc[pno].get_fonts(full=True)
                    except Exception:
                        continue
                    for f in fonts:
                        xref, _ext, _ftype, basefont = f[0], f[1], f[2], f[3]
                        if basefont.split("+")[-1] != "Maestro" or xref in seen:
                            continue
                        seen.add(xref)
                        try:
                            content = doc.extract_font(xref)
                            if isinstance(content, tuple):
                                content = content[-1]
                            tt = TTFont(io.BytesIO(content), fontNumber=0)
                            glyf_raw = tt.getTableData("glyf")
                            loca = tt["loca"]
                        except Exception:
                            continue
                        hit = True
                        resources += 1
                        present = 0
                        for gid in sorted(MAESTRO_GID_MAP):
                            if gid + 1 >= len(loca):
                                continue
                            seg = glyf_raw[loca[gid]:loca[gid + 1]]
                            if not seg:
                                continue
                            present += 1
                            dg = hashlib.sha256(seg).hexdigest()[:32]
                            digests[gid][dg] += 1
                            examples[gid].setdefault(dg, f"{path.name} xref{xref}")
                        present_counts.append(present)
                if hit:
                    files += 1
            finally:
                doc.close()
    return digests, examples, present_counts, files, resources


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("roots", nargs="+", help="directories to walk for *.pdf")
    args = ap.parse_args()

    digests, examples, present_counts, files, resources = scan(args.roots)
    print(f"pdf files with an embedded Maestro: {files}   distinct font resources: {resources}")
    if present_counts:
        pc = sorted(present_counts)
        print(f"mapped-and-present GIDs per resource: min={pc[0]} "
              f"p50={pc[len(pc) // 2]} max={pc[-1]}")
        print("  -> keep MAESTRO_FINGERPRINT_MIN_GLYPHS comfortably below min")

    conflicts = {g: d for g, d in digests.items() if len(d) > 1}
    print(f"\nGIDs observed: {len(digests)}   "
          f"GIDs with MORE THAN ONE distinct outline: {len(conflicts)}")
    for g, d in sorted(conflicts.items()):
        print(f"  !! gid {g} ({MAESTRO_GID_MAP[g]}) is NOT stable - drop it from MAESTRO_GID_MAP:")
        for dg, n in d.most_common():
            print(f"       {dg} n={n}  e.g. {examples[g][dg]}")

    print("\nMAESTRO_GLYF_DIGESTS = {")
    for g in sorted(digests):
        dg, n = digests[g].most_common(1)[0]
        flag = "  # UNSTABLE - see above" if len(digests[g]) > 1 else ""
        print(f'    {g}: "{dg}",  # {MAESTRO_GID_MAP[g]}, {n} resource(s){flag}')
    print("}")
    return 1 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
