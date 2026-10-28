#!/usr/bin/env python3
"""Ingest an existing image as an ORIGINAL: from a direct HTTPS URL or a local file
(user attachment, file staged from Downloads). Never generates anything — a failed
download can never start a new (paid) generation.

  fetch_image.py https://example.com/pic.png --workdir out/
  fetch_image.py ~/Downloads/Gemini_Generated_Image_x.png --workdir out/ --origin gemini-download
  fetch_image.py URL --workdir out/ --ratio 16:9 --name cafe-hero

What it does:
  * HTTPS only; at most N redirects, every hop re-checked; private, loopback,
    link-local and metadata addresses refused (also after redirects)
  * size and time limits; HTML/JSON/login pages are rejected, not saved as images
  * bytes are written exactly as received (no re-encoding), name gets the REAL
    extension, existing files are never overwritten
  * SHA-256 de-duplication: the same image twice is stored once
  * manifest.json records source type, redacted source, size, format, hash, stages
Signed URL tokens and query strings are never written to the manifest or logs.

Exit codes: 0 ok, 2 fetch failed, 3 not a valid image, 4 duplicate (already stored).
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagekit as ik  # noqa: E402

SOURCE_TYPES = ["url", "share-page", "gemini-download", "attachment", "api", "local", "screenshot"]


def ingest_bytes(data, workdir, name, source_type, source_label, url_fp=None, ratio=None,
                 require_alpha=False, min_size=(0, 0), extra=None):
    m = ik.Manifest(workdir)
    kind, mime = ik.sniff(data[:2048])
    sha = __import__("hashlib").sha256(data).hexdigest()
    dup = m.find_sha(sha)
    if dup:
        return m, dup, "duplicate"
    if kind not in ik.EXT_FOR and kind != "svg":
        item = m.add(name=name, source_type=source_type, source=source_label, url_fingerprint=url_fp,
                     received_mime=mime, created=ik.now(), status="rejected")
        m.stage(item, "source_opened")
        m.stage(item, "original_obtained", "failed", f"content is {kind}, not an image")
        m.save()
        return m, item, "invalid"
    ext = ik.EXT_FOR.get(kind, ".svg")
    path = ik.atomic_write(os.path.join(workdir, "originals", ik.slugify(name) + ext), data)
    rep = ik.validate(path, ratio, min_width=min_size[0], min_height=min_size[1], require_alpha=require_alpha)
    item = m.add(name=name, source_type=source_type, source=source_label, url_fingerprint=url_fp,
                 file=m.rel(path), sha256=sha, format=kind, mime=mime, bytes=len(data),
                 width=rep.width, height=rep.height, has_transparency=rep.has_transparent_pixels,
                 created=ik.now(), role="screenshot" if source_type == "screenshot" else "original",
                 **(extra or {}))
    m.stage(item, "source_opened")
    m.stage(item, "original_obtained")
    if rep.ok:
        m.stage(item, "verified")
        item["status"] = "verified"
    else:
        m.stage(item, "verified", "failed", "; ".join(rep.errors))
        item["status"] = "invalid"
        item["errors"] = rep.errors
    m.save()
    return m, item, ("ok" if rep.ok else "invalid")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="https URL or local file path")
    ap.add_argument("--workdir", default="gemini-out")
    ap.add_argument("--name", help="base file name (default from source)")
    ap.add_argument("--origin", choices=SOURCE_TYPES, help="how the file was obtained (recorded in manifest)")
    ap.add_argument("--ratio")
    ap.add_argument("--alpha", action="store_true")
    ap.add_argument("--min", help="WxH")
    ap.add_argument("--max-bytes", type=int, default=ik.MAX_BYTES)
    ap.add_argument("--timeout", type=int, default=ik.FETCH_TIMEOUT)
    ap.add_argument("--max-redirects", type=int, default=ik.MAX_REDIRECTS)
    ap.add_argument("--allow-http", action="store_true", help="also accept plain http (not recommended)")
    ap.add_argument("--allow-host", action="append", default=[], help=argparse.SUPPRESS)  # tests only
    a = ap.parse_args(argv)
    min_size = tuple(int(x) for x in a.min.lower().split("x")) if a.min else (0, 0)

    is_url = a.source.lower().startswith(("http://", "https://"))
    if is_url:
        label, fp = ik.redact_url(a.source), ik.url_fingerprint(a.source)
        try:
            data, final, ctype, hops = ik.fetch(a.source, max_bytes=a.max_bytes, timeout=a.timeout,
                                                max_redirects=a.max_redirects, allow_http=a.allow_http,
                                                allow_hosts=set(a.allow_host))
        except ik.FetchError as e:
            m = ik.Manifest(a.workdir)
            item = m.add(name=a.name or "download", source_type=a.origin or "url", source=label,
                         url_fingerprint=fp, created=ik.now(), status="fetch_failed", error_code=e.code)
            m.stage(item, "source_opened", "failed", str(e))
            m.save()
            print(json.dumps({"status": "fetch_failed", "error": e.code, "message": ik.redact(str(e)),
                              "id": item["id"], "next": _advice(e.code)}, indent=2))
            return 2
        name = a.name or os.path.splitext(os.path.basename(final.split("?")[0]))[0] or "image"
        m, item, status = ingest_bytes(data, a.workdir, name, a.origin or "url", label, fp, a.ratio,
                                       a.alpha, min_size, {"redirects": hops, "server_content_type": ctype.split(";")[0]})
    else:
        if not os.path.isfile(a.source):
            print(json.dumps({"status": "fetch_failed", "error": "missing_file",
                              "message": f"no such file: {a.source}"}))
            return 2
        with open(a.source, "rb") as f:
            data = f.read(a.max_bytes + 1)
        if len(data) > a.max_bytes:
            print(json.dumps({"status": "fetch_failed", "error": "too_large"}))
            return 2
        name = a.name or os.path.splitext(os.path.basename(a.source))[0]
        m, item, status = ingest_bytes(data, a.workdir, name, a.origin or "local",
                                       os.path.basename(a.source), None, a.ratio, a.alpha, min_size)
    out = {"status": status, "id": item["id"], "role": item.get("role"), "file": os.path.join(m.workdir, item["file"]) if item.get("file") else None,
           "format": item.get("format"), "size": [item.get("width"), item.get("height")],
           "bytes": item.get("bytes"), "sha256": item.get("sha256"), "errors": item.get("errors")}
    print(json.dumps({k: v for k, v in out.items() if v not in (None, [None, None])}, indent=2))
    return {"ok": 0, "duplicate": 4}.get(status, 3)


def _advice(code):
    return {
        "auth_required": "Source needs sign-in. Use the browser route in the user's own signed-in Chrome; never ask for passwords or cookies.",
        "forbidden_or_expired": "Link refused or expired. Re-open the source page to get a fresh link; don't guess URL parameters.",
        "expired": "Link expired. Re-open the source page to get a fresh link.",
        "blocked_address": "URL points to an internal/private address; refused for safety.",
        "timeout": "Server too slow. Retry once later; do NOT start a new generation.",
        "rate_limited": "Rate limited. Wait and retry once; do NOT start a new generation.",
        "too_large": "File exceeds the size limit (GEMINI_IMAGE_MAX_BYTES).",
    }.get(code, "Download failed. Report it to the user; do NOT start a new generation automatically.")


if __name__ == "__main__":
    sys.exit(main())
