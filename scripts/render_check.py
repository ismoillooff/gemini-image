#!/usr/bin/env python3
"""Fallback visual check when no interactive browser tool is available.

  render_check.py http://localhost:5173/ --widths 1440,390 --out shots/
  render_check.py dist/index.html --widths 1440,390 --out shots/

Renders the page at each width with the first renderer found (Playwright Chromium,
then wkhtmltoimage) and saves PNGs to look at. wkhtmltoimage uses an old WebKit:
modern CSS (grid gaps, container queries, some flex features) may render
differently from real browsers, so treat its output as a rough check and say so.
Prefer Claude in Chrome / the built-in browser + browser_snippets.js #7 when present.
Exit 3 if no renderer exists — then report "not visually verified".
"""
import argparse
import re
import json
import os
import shutil
import subprocess
import sys


def page_check_js():
    """Snippet 7 from browser_snippets.js: per-image load/scale/distortion/lazy/alt/CLS report."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_snippets.js"), encoding="utf-8").read()
    body = src.split("// ── 7.")[1].split("// ── ")[0].split("\n", 1)[1]   # only snippet 7
    body = "\n".join(l for l in body.splitlines() if not l.strip().startswith("//")).strip().rstrip(";")
    return re.sub(r"^await\s+", "", body)   # page.evaluate awaits the returned promise itself


def with_playwright(url, widths, out, schemes):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    js = page_check_js()
    shots = []
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser binary not installed
            print(f"playwright present but Chromium unavailable: {e}", file=sys.stderr)
            return None
        for scheme in schemes:
            for w in widths:
                pg = b.new_page(viewport={"width": w, "height": 900 if w > 600 else 844},
                                color_scheme=scheme, device_scale_factor=2 if w <= 600 else 1)
                pg.goto(url, wait_until="networkidle")
                pg.wait_for_timeout(300)
                path = os.path.join(out, f"page-{w}-{scheme}.png")
                pg.screenshot(path=path, full_page=True)
                report = pg.evaluate(js)
                issues = [{"src": i["src"], "issues": i["issues"]} for i in report["images"] if i["issues"]]
                shots.append({"width": w, "scheme": scheme, "file": path, "broken": report["broken"],
                              "layout_shift": report["layoutShift"], "image_issues": issues})
                pg.close()
        b.close()
    return {"renderer": "playwright-chromium", "shots": shots}


def with_wkhtml(url, widths, out):
    exe = shutil.which("wkhtmltoimage")
    if not exe:
        return None
    shots = []
    for w in widths:
        path = os.path.join(out, f"page-{w}.png")
        cmd = [exe, "--quiet", "--width", str(w), "--enable-local-file-access", "--format", "png", url, path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        shots.append({"width": w, "file": path, "ok": r.returncode == 0 and os.path.exists(path),
                      "stderr": r.stderr.strip()[-300:] or None})
    return {"renderer": "wkhtmltoimage (old WebKit: approximate)", "shots": shots}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="URL or local HTML file")
    ap.add_argument("--widths", default="1440,390")
    ap.add_argument("--out", default="render-check")
    ap.add_argument("--schemes", default="light,dark", help="color schemes to emulate (playwright only)")
    a = ap.parse_args(argv)
    url = a.target if "://" in a.target else "file://" + os.path.abspath(a.target)
    widths = [int(w) for w in a.widths.split(",")]
    os.makedirs(a.out, exist_ok=True)
    res = with_playwright(url, widths, a.out, a.schemes.split(",")) or with_wkhtml(url, widths, a.out)
    if not res:
        print(json.dumps({"status": "no_renderer", "message": "No browser available: page NOT visually verified."}))
        return 3
    problems = sum(len(s_.get("image_issues", [])) + s_.get("broken", 0) for s_ in res["shots"])
    res["summary"] = "no image problems found" if not problems else f"{problems} problem(s) — see image_issues"
    print(json.dumps(res, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
