#!/usr/bin/env python3
"""Verify that files are real, decodable images and meet requirements.

  check_image.py out/hero.png --ratio 16:9 --min 1600x900
  check_image.py logo.png --alpha                 # transparency required
  check_image.py a.png b.png --json               # machine-readable
  check_image.py copy.png --sha256 <expected>     # integrity against the original

Exit code 0 = every file passed, 1 = at least one failed.
Checks: exists, non-empty, real format from signature (rejects HTML/JSON/XML error
pages saved as .png), decodes fully, pixel size, aspect ratio, alpha channel and
actual transparent pixels, size limit, SHA-256.
Big pixel dimensions do not prove visual quality; this checks integrity, not beauty.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imagekit import ahash, hamming, validate  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--ratio", help="expected aspect ratio, e.g. 16:9")
    ap.add_argument("--tolerance", type=float, default=0.02, help="relative ratio tolerance (default 2%%)")
    ap.add_argument("--min", help="minimum size WxH, e.g. 1080x1350")
    ap.add_argument("--alpha", action="store_true", help="require real transparency")
    ap.add_argument("--sha256", help="expected SHA-256 (single file)")
    ap.add_argument("--match", metavar="AHASH", help="compare visual fingerprint (from browser snippet 3); "
                    "lists files by distance, <= 10 means same image")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    mw, mh = (int(x) for x in a.min.lower().split("x")) if a.min else (0, 0)

    results, ok_all = [], True
    for f in a.files:
        r = validate(f, a.ratio, a.tolerance, mw, mh, a.alpha)
        if a.sha256 and r.sha256 and r.sha256 != a.sha256.lower():
            r.errors.append("SHA-256 differs from expected: file was changed")
            r.ok = False
        d = r.to_dict()
        if a.match and r.ok and r.width:
            d["ahash"] = ahash(f)
            d["match_distance"] = hamming(d["ahash"], a.match)
            d["matches"] = d["match_distance"] <= 10
        ok_all &= r.ok
        results.append(d)
        if not a.json:
            if r.ok:
                alpha = " alpha" if r.has_transparent_pixels else ""
                print(f"OK   {f}: {r.kind.upper()} {r.width}x{r.height}{alpha}, "
                      f"{r.bytes / 1024:.0f} KB, sha256 {r.sha256[:12]}…")
            else:
                print(f"FAIL {f}: " + "; ".join(r.errors))
            if "match_distance" in d:
                print(f"     visual match distance {d['match_distance']}/64 -> "
                      + ("SAME image" if d["matches"] else "different image"))
            for w in r.warnings:
                print(f"     warning: {w}")
    if a.json:
        print(json.dumps(results, indent=2))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
