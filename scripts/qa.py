#!/usr/bin/env python3
"""Visual quality control helpers. They prepare things to LOOK at and measure
technical properties; they do not judge beauty and give no "quality score".

  qa.py detail  IMG --grid 3x3 --out W/qa/detail.png      # 100% crops of every region, labelled
  qa.py detail  IMG --box 0.3,0.1,0.7,0.4 --out …          # one region (fractions) at 100%+
  qa.py icon    IMG --sizes 16,24,32,48,64 --out …         # real display sizes on light/dark/brand bg
  qa.py crops   IMG --ratios 16:9,4:5,9:16 --out …         # how responsive crops cut the subject
  qa.py compare A B --out W/qa/a-vs-b.png                  # side by side + difference map + metric deltas
  qa.py metrics IMG                                        # sharpness, noise, clipping, size (technical)
  qa.py checklist --task photo                             # items to verify by eye
  qa.py record  W img-001 --task photo --pass "…" --fail "hands: 6 fingers" --visual
  qa.py budget  W --mode standard                          # generations used vs the mode's limit

`record` writes findings into manifest.json. Use --visual ONLY after you actually
looked at the image/crops; without it the item is recorded as not visually reviewed.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagekit as ik  # noqa: E402

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat
except ImportError:
    sys.exit("Pillow is missing: pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAPLACE = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def load(path):
    rep = ik.validate(path)
    if not rep.ok:
        sys.exit(f"{path}: " + "; ".join(rep.errors))
    return ImageOps.exif_transpose(Image.open(path))


def font(size):
    from postprocess import find_font, load_font  # reuse font discovery
    return load_font(find_font(), size)


def save(img, out):
    out = ik.unique_path(out)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    img.save(out)
    print("Saved:", os.path.abspath(out), f"{img.width}x{img.height}")
    return out


# ── metrics ─────────────────────────────────────────────────────────────────
def metrics(im):
    g = im.convert("L")
    if max(g.size) > 2048:  # keep it fast; metrics are relative anyway
        g = ImageOps.contain(g, (2048, 2048))
    lap = g.filter(LAPLACE)
    sharp = ImageStat.Stat(lap).var[0]
    med = g.filter(ImageFilter.MedianFilter(3))
    noise = ImageStat.Stat(ImageChops.difference(g, med)).mean[0]
    hist = g.histogram()
    total = sum(hist)
    clip_hi = sum(hist[250:]) / total
    clip_lo = sum(hist[:5]) / total
    rgb = im.convert("RGB")
    st = ImageStat.Stat(rgb)
    return {"width": im.width, "height": im.height,
            "sharpness_laplacian_var": round(sharp, 1),
            "noise_median_residual": round(noise, 2),
            "clipped_highlights_pct": round(100 * clip_hi, 2),
            "clipped_shadows_pct": round(100 * clip_lo, 2),
            "mean_rgb": [round(x) for x in st.mean],
            "note": "technical measurements for comparing versions of the SAME image; not an aesthetic score"}


# ── commands ────────────────────────────────────────────────────────────────
def cmd_detail(a):
    im = load(a.image).convert("RGB")
    W, H = im.size
    tiles = []
    if a.box:
        x0, y0, x1, y1 = (float(v) for v in a.box.split(","))
        tiles.append(("box", (int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))))
    else:
        cols, rows = (int(v) for v in a.grid.lower().split("x"))
        for r in range(rows):
            for c in range(cols):
                tiles.append((f"r{r + 1}c{c + 1}", (c * W // cols, r * H // rows, (c + 1) * W // cols, (r + 1) * H // rows)))
    # each tile shown at >= 100% (never downscaled below native), upscaled with NEAREST for inspection
    tile_w = a.tile
    crops = []
    for name, box in tiles:
        crop = im.crop(box)
        scale = max(1, tile_w // max(1, crop.width))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.NEAREST) if scale > 1 else crop
        if crop.width > tile_w:  # tile bigger than slot: keep native pixels, cut to slot
            crop = crop.crop((0, 0, tile_w, min(crop.height, int(tile_w * crop.height / crop.width) or crop.height)))
        crops.append((name, crop, scale))
    cols = 1 if a.box else int(a.grid.lower().split("x")[0])
    rows = math.ceil(len(crops) / cols)
    cw = max(c.width for _, c, _ in crops)
    ch = max(c.height for _, c, _ in crops)
    gap, lab = 8, 22
    sheet = Image.new("RGB", (cols * cw + (cols + 1) * gap, rows * (ch + lab) + (rows + 1) * gap), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    f = font(14)
    for i, (name, crop, scale) in enumerate(crops):
        r, c = divmod(i, cols)
        x, y = gap + c * (cw + gap), gap + r * (ch + lab + gap)
        d.text((x, y + 2), f"{name}  {'%dx zoom' % scale if scale > 1 else '100%'}", font=f, fill=(230, 230, 230))
        sheet.paste(crop, (x, y + lab))
    save(sheet, a.out)
    print("Look at every tile: melted/duplicated details, edges, text, hands/faces, halos.")


def cmd_icon(a):
    im = load(a.image).convert("RGBA")
    sizes = [int(s) for s in a.sizes.split(",")]
    bgs = [("light", (255, 255, 255)), ("dark", (24, 24, 27))]
    if a.brand:
        from postprocess import parse_color
        bgs.append(("brand", parse_color(a.brand)[:3]))
    mag = a.magnify
    pad = 12
    col_w = max(sizes) * mag + pad
    lab = 20
    sheet = Image.new("RGB", (len(sizes) * (col_w + pad) + pad + 70, len(bgs) * 2 * (max(sizes) * mag + lab + pad) + pad), (128, 128, 128))
    d = ImageDraw.Draw(sheet)
    f = font(13)
    y = pad
    for bname, bg in bgs:
        for view in ("actual", f"{mag}x"):
            d.text((pad, y + max(sizes) * mag // 2), f"{bname}\n{view}", font=f, fill=(255, 255, 255))
            x = pad + 70
            for s in sizes:
                small = im.resize((s, s), Image.LANCZOS)
                tile = Image.new("RGB", (s, s), bg)
                tile.paste(small, (0, 0), small)
                if view != "actual":
                    tile = tile.resize((s * mag, s * mag), Image.NEAREST)
                box = Image.new("RGB", (max(sizes) * mag, max(sizes) * mag), bg)
                box.paste(tile, ((box.width - tile.width) // 2, (box.height - tile.height) // 2))
                sheet.paste(box, (x, y + lab))
                d.text((x, y + 2), f"{s}px", font=f, fill=(255, 255, 255))
                x += col_w + pad
            y += max(sizes) * mag + lab + pad
    save(sheet, a.out)
    print("Judge the 'actual' rows: is the symbol still recognisable at 16-24 px on both backgrounds?")


def cmd_crops(a):
    im = load(a.image).convert("RGB")
    th = a.height
    pieces = []
    for r in a.ratios.split(","):
        rw, rh = (float(v) for v in r.split(":"))
        c = ImageOps.fit(im, (int(th * rw / rh), th), Image.LANCZOS, centering=(a.cx, a.cy))
        pieces.append((r, c))
    gap = 12
    sheet = Image.new("RGB", (sum(p.width for _, p in pieces) + gap * (len(pieces) + 1), th + 30 + 2 * gap), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    f = font(16)
    x = gap
    for r, p in pieces:
        d.text((x, gap), r, font=f, fill=(20, 20, 20))
        sheet.paste(p, (x, gap + 26))
        x += p.width + gap
    save(sheet, a.out)
    print("Check that the subject and the text area survive every crop.")


def cmd_compare(a):
    A, B = load(a.a).convert("RGB"), load(a.b).convert("RGB")
    ma, mb = metrics(A), metrics(B)
    h = min(A.height, B.height, a.height)
    A2 = ImageOps.contain(A, (10 ** 6, h))
    B2 = ImageOps.contain(B, (10 ** 6, h))
    diff = None
    if A.size == B.size:
        dimg = ImageChops.difference(A, B).convert("L")
        dimg = ImageOps.autocontrast(dimg)
        diff = ImageOps.contain(ImageOps.colorize(dimg, (0, 0, 0), (255, 60, 60)), (10 ** 6, h))
    panels = [("A", A2), ("B", B2)] + ([("difference", diff)] if diff else [])
    gap = 10
    sheet = Image.new("RGB", (sum(p.width for _, p in panels) + gap * (len(panels) + 1), h + 30 + 2 * gap), (30, 30, 30))
    d = ImageDraw.Draw(sheet)
    f = font(16)
    x = gap
    for n, p in panels:
        d.text((x, gap), n, font=f, fill=(240, 240, 240))
        sheet.paste(p, (x, gap + 26))
        x += p.width + gap
    out = save(sheet, a.out)
    hints = []
    if mb["sharpness_laplacian_var"] > 1.6 * ma["sharpness_laplacian_var"] and mb["noise_median_residual"] > 1.3 * ma["noise_median_residual"]:
        hints.append("B: sharper AND noisier than A — possible oversharpening/halos; inspect edges")
    if mb["sharpness_laplacian_var"] < 0.6 * ma["sharpness_laplacian_var"]:
        hints.append("B: much softer than A — detail may be lost")
    if mb["clipped_highlights_pct"] > ma["clipped_highlights_pct"] + 2:
        hints.append("B: more blown highlights")
    if mb["clipped_shadows_pct"] > ma["clipped_shadows_pct"] + 2:
        hints.append("B: more crushed shadows")
    print(json.dumps({"sheet": out, "A": ma, "B": mb, "hints": hints or ["no technical regression detected — still compare visually"]},
                     indent=2))


def cmd_metrics(a):
    print(json.dumps(metrics(load(a.image)), indent=2))


def cmd_checklist(a):
    c = json.load(open(os.path.join(ROOT, "templates", "qa-checklists.json"), encoding="utf-8"))
    print(json.dumps({"task": a.task, "items": c["common"] + c.get(a.task, [])}, indent=2))


def cmd_record(a):
    m = ik.Manifest(a.workdir)
    item = m.find_id(a.id)
    if not item:
        sys.exit(f"no item {a.id}")
    entry = {"time": ik.now(), "task": a.task, "visually_reviewed": bool(a.visual),
             "passed": a.passed or [], "failed": a.failed or [], "notes": a.note or []}
    if not a.visual:
        entry["notes"].append("NOT visually reviewed — technical checks only")
    item.setdefault("qa", []).append(entry)
    if a.visual and not a.failed:
        item["qa_status"] = "visually_reviewed_ok"
    elif a.failed:
        item["qa_status"] = "issues_found"
    else:
        item["qa_status"] = "not_visually_reviewed"
    m.save()
    print(json.dumps({"id": a.id, "qa_status": item["qa_status"], "entry": entry}, indent=2, ensure_ascii=False))


def cmd_budget(a):
    modes = json.load(open(os.path.join(ROOT, "templates", "quality-modes.json"), encoding="utf-8"))
    mode = modes[a.mode]
    m = ik.Manifest(a.workdir)
    gens = [i for i in m.data["items"] if i.get("source_type") in ("api", "gemini-download", "screenshot")
            or i.get("generated")]
    used = len(gens) + m.data.get("extra_generations", 0)
    left = mode["max_generations"] - used
    print(json.dumps({"mode": a.mode, "max_generations": mode["max_generations"], "used": used, "left": max(0, left),
                      "edit_attempts_allowed": mode["edit_attempts"],
                      "advice": "stop and show the best result with remaining issues" if left <= 0 else "ok"}, indent=2))
    return 0 if left > 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("detail"); p.add_argument("image"); p.add_argument("--grid", default="3x3")
    p.add_argument("--box"); p.add_argument("--tile", type=int, default=420); p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_detail)
    p = sub.add_parser("icon"); p.add_argument("image"); p.add_argument("--sizes", default="16,24,32,48,64")
    p.add_argument("--magnify", type=int, default=3); p.add_argument("--brand"); p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_icon)
    p = sub.add_parser("crops"); p.add_argument("image"); p.add_argument("--ratios", default="16:9,4:5,9:16")
    p.add_argument("--height", type=int, default=360); p.add_argument("--cx", type=float, default=0.5)
    p.add_argument("--cy", type=float, default=0.5); p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_crops)
    p = sub.add_parser("compare"); p.add_argument("a"); p.add_argument("b"); p.add_argument("--height", type=int, default=480)
    p.add_argument("--out", required=True); p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("metrics"); p.add_argument("image"); p.set_defaults(fn=cmd_metrics)
    p = sub.add_parser("checklist"); p.add_argument("--task", required=True); p.set_defaults(fn=cmd_checklist)
    p = sub.add_parser("record"); p.add_argument("workdir"); p.add_argument("id"); p.add_argument("--task", required=True)
    p.add_argument("--pass", dest="passed", action="append"); p.add_argument("--fail", dest="failed", action="append")
    p.add_argument("--note", action="append"); p.add_argument("--visual", action="store_true")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("budget"); p.add_argument("workdir"); p.add_argument("--mode", default="standard",
                                                                              choices=["draft", "standard", "final"])
    p.set_defaults(fn=cmd_budget)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
