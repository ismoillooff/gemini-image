#!/usr/bin/env python3
"""Prepare verified originals for hand-off: integrity check, preview, optional ZIP,
and an honest delivery report saying exactly what was done.

  deliver.py --workdir out/                       # every verified item not yet delivered
  deliver.py --workdir out/ --ids img-001 img-003  # selected items
  deliver.py --workdir out/ --zip                  # also bundle originals into one ZIP
  deliver.py --workdir out/ --env claude-code      # terminal: paths only, no inline claims

Rules it enforces:
  * the original is re-hashed and must match the manifest; otherwise it is NOT
    called original
  * when the original is a web-safe image (PNG/JPEG/WebP/GIF, <= --inline-max-mb and
    <= --inline-max-px) it is shown as-is: preview == original
  * otherwise a separate preview (JPEG, longest side <= --preview-px) is written to
    previews/ and labelled as a preview; the original is attached separately
  * nothing is ever written over an original

Output (JSON on stdout): for each item the files to send and how, and a 'say' line
for the user. The assistant then sends files (e.g. SendUserFile) and records
results with:   deliver.py --workdir out/ --mark img-001 attached
UI visibility can't be observed by the assistant, so 'ui_verified' stays 'unknown'
unless the user confirms.
"""
import argparse
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagekit as ik  # noqa: E402

WEB_SAFE = {"png", "jpeg", "webp", "gif"}


def make_preview(src, dst, max_px):
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((max_px, max_px), Image.LANCZOS)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        dst = ik.unique_path(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        im.save(dst, "JPEG", quality=88, optimize=True)
        return dst, im.width, im.height


def deliver_item(m, item, a):
    res = {"id": item["id"], "name": item.get("name")}
    orig = os.path.join(m.workdir, item["file"])
    if not os.path.exists(orig):
        res.update(status="missing_original", say=f"{item['id']}: original file is missing; not delivered.")
        m.stage(item, "download_ready", "failed", "original missing")
        return res
    sha = ik.sha256_file(orig)
    if sha != item.get("sha256"):
        res.update(status="integrity_failed",
                   say=f"{item['id']}: original changed on disk (hash mismatch); not presented as original.")
        m.stage(item, "verified", "failed", "hash mismatch at delivery")
        return res
    rep = ik.validate(orig)
    if not rep.ok:
        res.update(status="invalid", say=f"{item['id']}: " + "; ".join(rep.errors))
        return res
    size_mb = rep.bytes / 1048576
    dims = f"{rep.width}x{rep.height}" if rep.width else "vector"
    label = f"{rep.kind.upper()}, {dims}, {size_mb:.1f} MB"
    inline_ok = (a.env != "claude-code" and rep.kind in WEB_SAFE and size_mb <= a.inline_max_mb
                 and max(rep.width or 0, rep.height or 0) <= a.inline_max_px)
    files = []
    if inline_ok:
        files.append({"path": orig, "role": "original+preview", "display": "render"})
        res["preview_is_original"] = True
        m.stage(item, "preview_ready", "done", "original shown directly")
    elif a.env == "claude-code":
        files.append({"path": orig, "role": "original", "display": "path"})
        res["preview_is_original"] = None
        m.stage(item, "preview_ready", "skipped", "terminal: no inline images")
    else:
        stem = os.path.splitext(os.path.basename(orig))[0]
        pv, pw, ph = make_preview(orig, os.path.join(m.workdir, "previews", f"{stem}-preview.jpg"), a.preview_px)
        m.add_derived(item, pv, "preview", width=pw, height=ph)
        files.append({"path": pv, "role": f"preview {pw}x{ph} (reduced)", "display": "render"})
        files.append({"path": orig, "role": "original", "display": "attach"})
        res["preview_is_original"] = False
        m.stage(item, "preview_ready")
    # integrity again after preview creation: preview must not touch the original
    if ik.sha256_file(orig) != sha:
        res.update(status="integrity_failed", say="original changed during preview creation")
        return res
    m.stage(item, "download_ready")
    item["stages"]["ui_verified"] = "unknown"
    if item.get("role") == "screenshot":   # never presented as the original file
        for f in files:
            f["role"] = f["role"].replace("original+preview", "screenshot").replace("original", "screenshot")
        res.update(status="ready", label=label, sha256=sha, files=files, preview_is_original=None,
                   say=f"{item.get('name') or item['id']}: SCREENSHOT {label} — a capture of the page, "
                       "not the original file")
        return res
    res.update(status="ready", label=label, sha256=sha, files=files,
               say=(f"{item.get('name') or item['id']}: original {label}"
                    + ("" if res["preview_is_original"] is not False else " — inline image is a reduced preview; the attached file is the original")))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--all", action="store_true", help="include items already delivered")
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--env", choices=["web", "desktop", "claude-code"], default="web")
    ap.add_argument("--preview-px", type=int, default=1600)
    ap.add_argument("--inline-max-mb", type=float, default=float(os.environ.get("GEMINI_IMAGE_INLINE_MAX_MB", 10)))
    ap.add_argument("--inline-max-px", type=int, default=4096)
    ap.add_argument("--mark", nargs=2, metavar=("ID", "STAGE"),
                    help="record a stage reached outside this script, e.g. img-001 attached")
    ap.add_argument("--status", default="done", help="status for --mark (done|failed|unknown)")
    a = ap.parse_args(argv)

    m = ik.Manifest(a.workdir)
    if a.mark:
        item = m.find_id(a.mark[0])
        if not item:
            sys.exit(f"no item {a.mark[0]}")
        m.stage(item, a.mark[1], a.status)
        m.save()
        print(json.dumps({"id": item["id"], "stages": item["stages"]}, indent=2))
        return 0

    items = [i for i in m.data["items"] if i.get("role") in ("original", "screenshot") and i.get("status") == "verified"]
    if a.ids:
        items = [i for i in items if i["id"] in a.ids]
    elif not a.all:  # resume: skip items already fully handed over
        items = [i for i in items if i["stages"].get("download_ready") != "done"]
    results = [deliver_item(m, i, a) for i in items]
    out = {"items": results}
    ready = [r for r in results if r.get("status") == "ready"]
    if a.zip and len(ready) > 1:
        zpath = ik.unique_path(os.path.join(m.workdir, "originals.zip"))
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:   # stored: bytes unchanged
            for r in ready:
                orig = next(f["path"] for f in r["files"] if f["role"].startswith("original"))
                z.write(orig, os.path.basename(orig))
        with zipfile.ZipFile(zpath) as z:
            bad = [n for n in z.namelist()
                   if __import__("hashlib").sha256(z.read(n)).hexdigest()
                   not in {r["sha256"] for r in ready}]
        out["zip"] = {"path": zpath, "verified": not bad, "display": "attach"}
    if not results:
        out["note"] = "nothing to deliver (no verified originals pending; use --all to resend)"
    m.save()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if all(r.get("status") == "ready" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
