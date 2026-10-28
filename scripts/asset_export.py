#!/usr/bin/env python3
"""Stage 2 of project integration: keep the original, export production files, register.

  asset_export.py ORIGINAL --project . --id hero-landing --purpose "Landing hero illustration" \
      --ratio 16:9 --widths 640,1280,1920 --usage src/components/Hero.tsx
  asset_export.py ORIGINAL --project . --id feature-icon-sync --alpha --widths 256 --format png
  asset_export.py ORIGINAL --project . --id hero-landing --replace     # update same asset, same paths

Steps (each one checked):
  1. validate ORIGINAL (real image, decodes; --alpha requires real transparent pixels —
     a drawn checkerboard does NOT count)
  2. copy it byte-for-byte to the originals dir (default .gemini-assets/originals/, outside
     anything public), de-duplicated by SHA-256
  3. export production files to --dest (default: project convention from project_scan):
     optional centre/focus crop to --ratio (never stretch), widths capped at the source width
     (no upscaling), format auto=webp (keeps alpha) or as asked; .svg output is refused
     because a raster is not a vector
  4. refuse to replace any existing file unless it belongs to this same registry asset and
     --replace is given (stable paths, so existing references keep working)
  5. record everything in .gemini-assets/registry.json and print what to put in the code:
     public URL or import path, intrinsic width/height, srcset
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagekit as ik                  # noqa: E402
from asset_registry import Registry    # noqa: E402
from project_scan import is_public_dir, scan  # noqa: E402

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow is missing: pip install pillow")

FORMATS = {"webp": ("WEBP", ".webp"), "png": ("PNG", ".png"), "jpg": ("JPEG", ".jpg"), "avif": ("AVIF", ".avif")}


def crop_to_ratio(im, ratio, cx=0.5, cy=0.5):
    a, b = (float(x) for x in ratio.split(":"))
    target = a / b
    W, H = im.size
    if abs(W / H - target) / target < 0.005:
        return im
    if W / H > target:
        nw = round(H * target)
        x = round((W - nw) * cx)
        return im.crop((x, 0, x + nw, H))
    nh = round(W / target)
    y = round((H - nh) * cy)
    return im.crop((0, y, W, y + nh))


def public_url(project, dest_rel, filename):
    parts = dest_rel.replace(os.sep, "/").split("/")
    if parts and parts[0] in ("public", "static", "www", "htdocs"):
        return "/" + "/".join(parts[1:] + [filename]).lstrip("/")
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("original")
    ap.add_argument("--project", default=".")
    ap.add_argument("--id", required=True, help="stable asset id = file stem, e.g. hero-landing")
    ap.add_argument("--purpose", default="")
    ap.add_argument("--brief", default="", help="short design brief (no secrets)")
    ap.add_argument("--model", help="generation model if known")
    ap.add_argument("--dest", help="production dir relative to project (default: project convention)")
    ap.add_argument("--originals-dir", default=".gemini-assets/originals")
    ap.add_argument("--ratio", help="crop to this ratio first (never stretches)")
    ap.add_argument("--cx", type=float, default=0.5)
    ap.add_argument("--cy", type=float, default=0.5)
    ap.add_argument("--widths", default="", help="comma list, e.g. 640,1280,1920 (capped at source width)")
    ap.add_argument("--format", default="auto", choices=["auto", "webp", "png", "jpg", "avif", "svg"])
    ap.add_argument("--quality", type=int, default=82)
    ap.add_argument("--alpha", action="store_true", help="transparency required")
    ap.add_argument("--usage", action="append", default=[], help="component/file that will use it")
    ap.add_argument("--tags", default="")
    ap.add_argument("--replace", action="store_true", help="overwrite this asset's own production files")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    project = os.path.abspath(a.project)
    if a.format == "svg":
        sys.exit("Refusing to save a raster image as .svg. Need a real vector? Draw it as SVG code "
                 "(or use the icon library) — or keep the raster as webp/png and say so.")
    asset_id = ik.slugify(a.id, 60)
    rep = ik.validate(a.original, require_alpha=a.alpha)
    if not rep.ok:
        sys.exit("Original failed checks: " + "; ".join(rep.errors) +
                 (" — a checkerboard drawn into the pixels is not transparency; regenerate on a flat "
                  "background and run postprocess.py cutout." if a.alpha else ""))
    ctx = None
    dest = a.dest
    if not dest:
        ctx = scan(project)
        dest = ctx["suggested_dest"]
    if os.path.isabs(dest) or ".." in dest.split("/"):
        sys.exit("--dest must be a path inside the project")
    fmt = "webp" if a.format == "auto" else a.format
    if fmt == "jpg" and (a.alpha or rep.has_transparent_pixels):
        sys.exit("JPEG cannot keep transparency; use webp or png")
    if fmt == "avif" and "AVIF" not in Image.registered_extensions().values():
        sys.exit("This Pillow build cannot write AVIF; use webp")

    reg = Registry(project)
    existing = reg.get(asset_id)

    with Image.open(a.original) as src:
        im = ImageOps.exif_transpose(src)
        im.load()
    if a.ratio:
        im = crop_to_ratio(im, a.ratio, a.cx, a.cy)
    widths = sorted({int(w) for w in a.widths.split(",") if w.strip()}) or [im.width]
    capped = sorted({min(w, im.width) for w in widths})
    notes = []
    if capped != widths:
        notes.append(f"widths above the source width {im.width}px were capped (no upscaling)")

    pil_fmt, ext = FORMATS[fmt]
    dest_abs = os.path.join(project, dest)
    planned = []
    for w in capped:
        name = f"{asset_id}{ext}" if len(capped) == 1 else f"{asset_id}-{w}{ext}"
        path = os.path.join(dest_abs, name)
        rel = os.path.relpath(path, project).replace(os.sep, "/")
        if os.path.exists(path):
            owner = reg.owns(rel)
            if not (a.replace and owner and owner["id"] == asset_id):
                sys.exit(f"{rel} already exists and is {'another asset' if owner else 'not created by this skill'}; "
                         "choose another --id, or --replace to update this same asset")
        planned.append((w, path, rel))
    if a.dry_run:
        print(json.dumps({"dest": dest, "files": [p[2] for p in planned], "notes": notes}, indent=2))
        return 0

    # 2. original, byte-for-byte, outside public dirs
    orig_dir = os.path.join(project, a.originals_dir)
    if is_public_dir(a.originals_dir):
        notes.append("WARNING: originals dir is publicly served")
    os.makedirs(orig_dir, exist_ok=True)
    o_ext = ik.EXT_FOR.get(rep.kind, os.path.splitext(a.original)[1])
    o_path = os.path.join(orig_dir, f"{asset_id}{o_ext}")
    if os.path.exists(o_path) and ik.sha256_file(o_path) == rep.sha256:
        pass
    else:
        o_path = ik.unique_path(o_path)
        shutil.copyfile(a.original, o_path)
    if ik.sha256_file(o_path) != rep.sha256:
        sys.exit("Copy of the original does not match the source hash")

    # 3. production files
    os.makedirs(dest_abs, exist_ok=True)
    prod = []
    for w, path, rel in planned:
        out = im if w == im.width else im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        if pil_fmt == "JPEG":
            out = out.convert("RGB")
        elif out.mode not in ("RGB", "RGBA"):
            out = out.convert("RGBA" if rep.has_transparent_pixels else "RGB")
        kw = {"quality": a.quality} if pil_fmt in ("WEBP", "JPEG", "AVIF") else {"optimize": True}
        if pil_fmt == "WEBP":
            kw["method"] = 6
        tmp = path + ".part"
        out.save(tmp, pil_fmt, **kw)
        os.replace(tmp, path)
        v = ik.validate(path, require_alpha=a.alpha)
        if not v.ok:
            sys.exit(f"Exported file failed checks: {rel}: " + "; ".join(v.errors))
        prod.append({"path": rel, "width": v.width, "height": v.height, "format": fmt, "bytes": v.bytes,
                     "sha256": v.sha256, "public_url": public_url(project, dest, os.path.basename(path))})

    entry = {
        "id": asset_id, "purpose": a.purpose, "brief": a.brief[:400], "tags": a.tags,
        "model": a.model, "requires_alpha": a.alpha, "ratio": a.ratio,
        "original": os.path.relpath(o_path, project).replace(os.sep, "/"), "original_sha256": rep.sha256,
        "original_size": [rep.width, rep.height], "production": prod,
        "planned_usage": sorted(set((existing or {}).get("planned_usage", []) + a.usage)),
        "updated": ik.now(),
    }
    reg.upsert(entry)
    reg.save()

    biggest = prod[-1]
    srcset = ", ".join(f"{p['public_url'] or p['path']} {p['width']}w" for p in prod) if len(prod) > 1 else None
    out = {
        "status": "ok", "id": asset_id, "original": entry["original"], "production": prod,
        "use_in_code": {
            "public_url": biggest["public_url"],
            "import_path_from_project_root": None if biggest["public_url"] else biggest["path"],
            "width": biggest["width"], "height": biggest["height"], "srcset": srcset,
        },
        "registry": os.path.relpath(reg.path, project), "notes": notes,
        "next": "Wire it into the component (see references/project-integration.md), then run "
                "asset_registry.py verify and the project's build/typecheck.",
    }
    if ctx:
        out["framework"] = ctx["framework"]["name"]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
