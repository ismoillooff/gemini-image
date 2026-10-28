#!/usr/bin/env python3
"""Derived versions of an image: platform sizes, exact text, logo, formats, contact sheets.

Every command writes a NEW file (never over the input, never over an existing file
unless --overwrite) and, when the input is tracked in a manifest.json, records the
new file as a derivative of that original (kind: resized / upscaled / text / logo /
converted / sheet). An upscaled file is labelled 'upscaled' — never as an original.

  resize   to a preset or WxH without stretching
      postprocess.py resize in.png --preset ig-post --mode crop --out out/ig.png
      postprocess.py resize in.png --size 1920x600 --mode extend --out out/banner.png
        modes: crop (fill, centre/--cx --cy focus) | pad (solid --bg) | extend (blurred fill of itself)
  text     exact text as a separate layer (Uzbek oʻ gʻ, Cyrillic checked against the font)
      postprocess.py text in.png --text "Con-Dor CRM" --sub "Biznesingiz nazoratda" \
          --pos bottom-left --box "#0B1F3ACC" --out out/post.png
        checks: missing glyphs (error), text fits inside safe margins (auto-shrinks),
        contrast against what is behind it (warning below 4.5:1, 3:1 for large text)
  logo     place a supplied logo (the logo itself is never regenerated)
      postprocess.py logo in.png --logo logo.png --pos top-right --scale 0.15 --out out/x.png
  cutout   make a flat background transparent (for assets generated on a solid colour)
      postprocess.py cutout in.png --key auto --tolerance 40 --out out/asset.png
        Gemini rarely returns real transparency: prompt for "on a plain pure #00FF00
        background", then cut it out here and verify with check_image.py --alpha.
  upscale  enlarge only when no native size is available; labelled 'upscaled', compared to the source
      postprocess.py upscale in.png --scale 2 --sharpen 0.4 --out out/x@2x.png
        Upscaling adds no real detail. Prefer generating at the needed size (API imageSize).
  convert  change format / quality      postprocess.py convert in.png --out out/x.webp --quality 90
  sheet    numbered contact sheet        postprocess.py sheet a.png b.png c.png --out out/variants.jpg
  info     print size, format, mode      postprocess.py info in.png

Recommended order for several outputs: resize the clean original to each size first,
then add text and logo to each size.
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagekit as ik  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
except ImportError:
    sys.exit("Pillow is missing: pip install pillow  (add --break-system-packages in sandboxes)")

PRESETS = {
    "ig-post": (1080, 1350), "ig-square": (1080, 1080), "ig-story": (1080, 1920),
    "fb-post": (1200, 630), "fb-cover": (1640, 624), "tg-post": (1280, 720),
    "yt-thumb": (1280, 720), "web-hero": (1920, 1080), "web-banner": (1920, 600),
    "og-image": (1200, 630), "a4-300dpi": (2480, 3508), "favicon": (512, 512),
}
POSITIONS = {"top-left", "top", "top-right", "left", "center", "right",
             "bottom-left", "bottom", "bottom-right"}
FONT_CANDIDATES = [
    "NotoSans-Bold.ttf", "NotoSans-Regular.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf",
    "LiberationSans-Bold.ttf", "Arial Bold.ttf", "Arial.ttf", "arialbd.ttf", "arial.ttf",
    "Roboto-Bold.ttf", "Inter-Bold.ttf",
]
FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
             os.path.expanduser("~/.local/share/fonts"), os.path.expanduser("~/Library/Fonts"),
             "/Library/Fonts", "/System/Library/Fonts", "C:/Windows/Fonts"]


# ── helpers ─────────────────────────────────────────────────────────────────
def parse_color(value):
    if value is None:
        return None
    v = value.lstrip("#")
    if len(v) in (6, 8):
        rgb = tuple(int(v[i:i + 2], 16) for i in range(0, len(v), 2))
        return rgb if len(v) == 8 else rgb + (255,)
    raise argparse.ArgumentTypeError(f"color must be #RRGGBB or #RRGGBBAA, got {value}")


def find_font(path=None, bold=True):
    if path:
        if not os.path.isfile(path):
            sys.exit(f"Font not found: {path}")
        return path
    names = FONT_CANDIDATES if bold else [n for n in FONT_CANDIDATES if "Bold" not in n and "bd" not in n]
    for name in names:
        for d in FONT_DIRS:
            hits = glob.glob(os.path.join(d, "**", name), recursive=True)
            if hits:
                return hits[0]
    return None


def load_font(path, size):
    if path:
        return ImageFont.truetype(path, size)
    print("Warning: no TTF font found; Pillow default font has limited glyphs "
          "(install fonts-dejavu / Noto, or pass --font).", file=sys.stderr)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def missing_glyphs(font_path, text):
    """Characters the font cannot draw (rendered as the .notdef 'tofu' box)."""
    if not font_path:
        return []
    try:
        from fontTools.ttLib import TTFont  # exact check when available
        cmap = TTFont(font_path, lazy=True).getBestCmap()
        return sorted({c for c in text if not c.isspace() and ord(c) not in cmap})
    except Exception:
        pass
    f = ImageFont.truetype(font_path, 48)
    tofu = f.getmask("\U000F0000").tobytes()  # private-use char: absent in normal fonts
    return sorted({c for c in text if not c.isspace() and f.getmask(c).tobytes() == tofu})


def luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(c1, c2):
    l1, l2 = sorted((luminance(c1), luminance(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def anchor_xy(pos, canvas, box, margin):
    W, H = canvas
    w, h = box
    x = {"left": margin, "center": (W - w) // 2, "right": W - w - margin}
    y = {"top": margin, "center": (H - h) // 2, "bottom": H - h - margin}
    if pos == "center":
        v = hz = "center"
    elif pos in ("left", "right"):
        v, hz = "center", pos
    elif pos in ("top", "bottom"):
        v, hz = pos, "center"
    else:
        v, hz = pos.split("-")
    return x[hz], y[v]


def wrap(draw, text, font, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def open_input(path):
    rep = ik.validate(path)
    if not rep.ok:
        sys.exit(f"Input is not a valid image: {'; '.join(rep.errors)}")
    return ImageOps.exif_transpose(Image.open(path))


def find_manifest(path):
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(4):
        if os.path.exists(os.path.join(d, ik.MANIFEST_NAME)):
            return d
        d = os.path.dirname(d)
    return None


def save(img, out, a, src=None, kind="derived", **extra):
    out_abs = os.path.abspath(out)
    if src and os.path.abspath(src) == out_abs:
        sys.exit("Refusing to write over the input; choose a different --out")
    if f"{os.sep}originals{os.sep}" in out_abs:
        sys.exit("Refusing to write into originals/: derived files go elsewhere")
    final = ik.unique_path(out, getattr(a, "overwrite", False))
    os.makedirs(os.path.dirname(os.path.abspath(final)) or ".", exist_ok=True)
    ext = os.path.splitext(final)[1].lower()
    if ext in (".jpg", ".jpeg") and img.mode != "RGB":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        img = bg
    kwargs = {"quality": getattr(a, "quality", 92)} if ext in (".jpg", ".jpeg", ".webp") else {}
    if ext == ".png":
        kwargs["optimize"] = True
    img.save(final, **kwargs)
    if final != out:
        print(f"Note: {out} existed; wrote {final} instead (use --overwrite to replace)", file=sys.stderr)
    tracked = ""
    if src:
        wd = find_manifest(src)
        if wd:
            m = ik.Manifest(wd)
            item = m.find_path(src)
            if item:
                m.add_derived(item, final, kind, width=img.width, height=img.height,
                              from_file=m.rel(src), **extra)
                m.save()
                tracked = f" (recorded as {kind} of {item['id']})"
    print("Saved:", os.path.abspath(final), f"{img.width}x{img.height}{tracked}")
    return final


# ── commands ────────────────────────────────────────────────────────────────
def cmd_resize(a):
    img = open_input(a.input)
    W, H = PRESETS[a.preset] if a.preset else tuple(int(x) for x in a.size.lower().split("x"))
    if a.mode == "crop":
        scale = max(W / img.width, H / img.height)
        out = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(a.cx, a.cy))
    else:
        fitted = ImageOps.contain(img, (W, H), Image.LANCZOS)
        scale = fitted.width / img.width
        if a.mode == "pad":
            out = Image.new("RGBA", (W, H), parse_color(a.bg) if a.bg else (255, 255, 255, 255))
        else:  # extend: blurred, darkened cover-fill of the same image behind it
            out = ImageOps.fit(img.convert("RGBA"), (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(max(W, H) // 30))
            out = Image.blend(out, Image.new("RGBA", (W, H), (0, 0, 0, 255)), 0.25)
        fitted = fitted.convert("RGBA")
        out.paste(fitted, ((W - fitted.width) // 2, (H - fitted.height) // 2), fitted)
    kind = "resized"
    if scale > 1.05:
        kind = "upscaled"
        print(f"Warning: upscaled {scale:.2f}x from {img.width}x{img.height}. Labelled 'upscaled' — it is not "
              "a higher-resolution original. Generate at a larger size if sharpness matters.", file=sys.stderr)
    save(out, a.out, a, a.input, kind, mode=a.mode, scale=round(scale, 3))


def cmd_text(a):
    img = open_input(a.input).convert("RGBA")
    W, H = img.size
    font_path = find_font(a.font)
    body_font_path = find_font(a.font, bold=False) or font_path
    miss = missing_glyphs(font_path, a.text) + missing_glyphs(body_font_path, a.sub or "")
    if miss:
        sys.exit(f"Font {os.path.basename(font_path or 'default')} lacks glyphs: {' '.join(sorted(set(miss)))} "
                 "— pass --font with a font that covers them (e.g. Noto Sans, DejaVu Sans).")
    margin = int(min(W, H) * a.margin)
    t_size = a.size or max(24, int(H * 0.075))
    for _ in range(40):  # shrink until the block fits inside the safe area
        s_size = a.sub_size or max(14, int(t_size * 0.5))
        tf, sf = load_font(font_path, t_size), load_font(body_font_path, s_size)
        probe = ImageDraw.Draw(img)
        max_w = int(W * a.max_width)
        lines = [(l, tf) for l in wrap(probe, a.text, tf, max_w)]
        if a.sub:
            lines += [(l, sf) for l in wrap(probe, a.sub, sf, max_w)]
        gap = int(t_size * 0.25)
        boxes = [probe.textbbox((0, 0), l, font=f) for l, f in lines]
        bw = max(b[2] - b[0] for b in boxes)
        bh = sum(b[3] - b[1] for b in boxes) + gap * (len(lines) - 1)
        pad = int(t_size * 0.45) if a.box else 0
        if bw + 2 * pad <= W - 2 * margin and bh + 2 * pad <= H - 2 * margin:
            break
        t_size = int(t_size * 0.9)
    else:
        sys.exit("Text cannot fit inside the image; shorten it")
    if a.size and t_size != a.size:
        print(f"Note: title shrunk from {a.size}px to {t_size}px to stay inside the margins", file=sys.stderr)
    x, y = anchor_xy(a.pos, (W, H), (bw + 2 * pad, bh + 2 * pad), margin)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    color = parse_color(a.color)
    if a.box:
        d.rounded_rectangle((x, y, x + bw + 2 * pad, y + bh + 2 * pad), radius=pad, fill=parse_color(a.box))
    cy = y + pad
    centered = a.pos in ("top", "center", "bottom")
    for (line, f), b in zip(lines, boxes):
        lw = b[2] - b[0]
        cx = x + pad + ((bw - lw) // 2 if centered else 0)
        if a.shadow:
            off = max(2, t_size // 25)
            d.text((cx - b[0] + off, cy - b[1] + off), line, font=f, fill=(0, 0, 0, 140))
        d.text((cx - b[0], cy - b[1]), line, font=f, fill=color)
        cy += (b[3] - b[1]) + gap
    result = Image.alpha_composite(img, layer)
    # contrast of text colour vs. what is actually behind it (box or image)
    behind = Image.alpha_composite(img, Image.new("RGBA", img.size, (0, 0, 0, 0)))
    if a.box:
        bl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(bl).rectangle((x, y, x + bw + 2 * pad, y + bh + 2 * pad), fill=parse_color(a.box))
        behind = Image.alpha_composite(img, bl)
    region = behind.crop((x, y, x + bw + 2 * pad, y + bh + 2 * pad)).convert("RGB")
    bg_mean = tuple(int(v) for v in ImageStat.Stat(region).mean)
    ratio = contrast(color, bg_mean)
    need = 3.0 if t_size >= 24 else 4.5
    if ratio < need:
        print(f"Warning: low contrast {ratio:.1f}:1 (want ≥ {need}:1). Add --box or --shadow, or change --color.",
              file=sys.stderr)
    save(result, a.out, a, a.input, "text", text=a.text, sub=a.sub, contrast=round(ratio, 2),
         font=os.path.basename(font_path) if font_path else "default")


def cmd_logo(a):
    img = open_input(a.input).convert("RGBA")
    lrep = ik.validate(a.logo)
    if not lrep.ok:
        sys.exit(f"Logo is not a valid image: {'; '.join(lrep.errors)}")
    logo = Image.open(a.logo).convert("RGBA")
    if not lrep.has_transparent_pixels:
        print("Note: logo has no transparent pixels; it will appear as a rectangle.", file=sys.stderr)
    target_w = int(img.width * a.scale)
    if target_w > logo.width:
        print(f"Warning: logo upscaled from {logo.width}px to {target_w}px; supply a larger logo for sharpness.",
              file=sys.stderr)
    logo = logo.resize((target_w, max(1, int(logo.height * target_w / logo.width))), Image.LANCZOS)
    if a.opacity < 1:
        logo.putalpha(logo.getchannel("A").point(lambda p: int(p * a.opacity)))
    margin = int(min(img.size) * a.margin)
    img.alpha_composite(logo, anchor_xy(a.pos, img.size, logo.size, margin))
    save(img, a.out, a, a.input, "logo", logo=os.path.basename(a.logo))


def cmd_cutout(a):
    img = open_input(a.input).convert("RGBA")
    W, H = img.size
    if a.key == "auto":  # median of the border pixels
        px = img.load()
        border = [px[x, y][:3] for x in range(W) for y in (0, H - 1)] + \
                 [px[x, y][:3] for y in range(H) for x in (0, W - 1)]
        key = tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))
    else:
        key = parse_color(a.key)[:3]
    tol, feather = a.tolerance, max(1, a.feather)
    rgb = img.convert("RGB")
    dist = Image.new("L", img.size)
    r, g, b = (ch.load() for ch in rgb.split())
    dp = dist.load()
    for y in range(H):
        for x in range(W):
            d = ((r[x, y] - key[0]) ** 2 + (g[x, y] - key[1]) ** 2 + (b[x, y] - key[2]) ** 2) ** 0.5
            dp[x, y] = 0 if d <= tol else (255 if d >= tol + feather else int(255 * (d - tol) / feather))
    a_ch = dist if not a.keep_alpha else Image.composite(img.getchannel("A"), dist, dist)
    out = img.copy()
    out.putalpha(a_ch)
    if a.trim:
        bbox = a_ch.getbbox()
        if bbox:
            out = out.crop(bbox)
    removed = 1 - sum(1 for v in a_ch.getdata() if v > 0) / (W * H)
    print(f"Key colour #{key[0]:02X}{key[1]:02X}{key[2]:02X}, {removed:.0%} of pixels made transparent", file=sys.stderr)
    if removed < 0.02:
        print("Warning: almost nothing removed — background is not a flat colour; regenerate on a plain background.",
              file=sys.stderr)
    if not os.path.splitext(a.out)[1].lower() in (".png", ".webp"):
        sys.exit("Transparent output needs .png or .webp")
    save(out, a.out, a, a.input, "cutout", key=f"#{key[0]:02X}{key[1]:02X}{key[2]:02X}", tolerance=tol)


def cmd_upscale(a):
    img = open_input(a.input)
    if a.scale <= 1:
        sys.exit("--scale must be > 1")
    if a.scale > 4:
        sys.exit("--scale above 4 only invents blur; regenerate at a larger size instead")
    big = img.resize((round(img.width * a.scale), round(img.height * a.scale)), Image.LANCZOS)
    if a.sharpen > 0:
        big = big.filter(ImageFilter.UnsharpMask(radius=1.2 * a.scale, percent=int(120 * a.sharpen), threshold=3))
    print(f"Warning: upscaled {a.scale}x — labelled 'upscaled', not a higher-resolution original.", file=sys.stderr)
    out = save(big, a.out, a, a.input, "upscaled", scale=a.scale, sharpen=a.sharpen)
    try:
        import qa
        ma = qa.metrics(img.resize(big.size, Image.LANCZOS))
        mb = qa.metrics(Image.open(out))
        if mb["noise_median_residual"] > 1.4 * ma["noise_median_residual"]:
            print("Warning: sharpening added visible noise/halos; try --sharpen 0", file=sys.stderr)
    except Exception:
        pass


def cmd_convert(a):
    img = open_input(a.input)
    save(img, a.out, a, a.input, "converted", quality=a.quality)


def cmd_sheet(a):
    imgs = [open_input(p).convert("RGB") for p in a.inputs]
    th, gap = a.height, 16
    thumbs = [ImageOps.contain(i, (max(1, int(th * i.width / i.height)), th), Image.LANCZOS) for i in imgs]
    cols = a.cols or (2 if len(thumbs) == 4 else min(len(thumbs), 3))
    rows = [thumbs[i:i + cols] for i in range(0, len(thumbs), cols)]
    width = max(sum(t.width for t in r) + gap * (len(r) + 1) for r in rows)   # flow layout: no empty cells
    sheet = Image.new("RGB", (width, len(rows) * th + (len(rows) + 1) * gap), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    font = load_font(find_font(), max(18, th // 12))
    n = 0
    for ri, row in enumerate(rows):
        x = gap + (width - (sum(t.width for t in row) + gap * (len(row) + 1))) // 2
        y = gap + ri * (th + gap)
        for t in row:
            n += 1
            sheet.paste(t, (x, y))
            label = str(n)
            b = d.textbbox((0, 0), label, font=font)
            s_ = max(b[2] - b[0], b[3] - b[1]) + 16
            d.ellipse((x + 10, y + 10, x + 10 + s_, y + 10 + s_), fill=(20, 20, 20))
            d.text((x + 10 + (s_ - (b[2] - b[0])) / 2 - b[0], y + 10 + (s_ - (b[3] - b[1])) / 2 - b[1]),
                   label, font=font, fill=(255, 255, 255))
            x += t.width + gap
    a.quality = 90
    save(sheet, a.out, a, None, "sheet")


def cmd_info(a):
    r = ik.validate(a.input)
    print(f"{a.input}: " + (f"{r.kind.upper()} {r.width}x{r.height} {r.mode}, {r.bytes / 1024:.0f} KB, "
                            f"ratio {r.width / r.height:.3f}, transparent={r.has_transparent_pixels}, "
                            f"sha256 {r.sha256[:12]}…" if r.ok else "INVALID: " + "; ".join(r.errors)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--out", required=True)
        p.add_argument("--overwrite", action="store_true", help="allow replacing an existing --out file")

    r = sub.add_parser("resize")
    r.add_argument("input")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--preset", choices=sorted(PRESETS))
    g.add_argument("--size", help="WxH, e.g. 1920x600")
    r.add_argument("--mode", choices=["crop", "pad", "extend"], default="crop")
    r.add_argument("--cx", type=float, default=0.5, help="crop focus x 0..1")
    r.add_argument("--cy", type=float, default=0.5, help="crop focus y 0..1")
    r.add_argument("--bg", help="pad colour #RRGGBB[AA]")
    r.add_argument("--quality", type=int, default=92)
    common(r)
    r.set_defaults(fn=cmd_resize)

    t = sub.add_parser("text")
    t.add_argument("input")
    t.add_argument("--text", required=True)
    t.add_argument("--sub", help="smaller second line")
    t.add_argument("--pos", choices=sorted(POSITIONS), default="bottom-left")
    t.add_argument("--color", default="#FFFFFF")
    t.add_argument("--box", help="background box colour #RRGGBBAA, e.g. #00000099")
    t.add_argument("--shadow", action="store_true")
    t.add_argument("--font", help="path to .ttf/.otf")
    t.add_argument("--size", type=int, help="title px (default 7.5%% of height)")
    t.add_argument("--sub-size", type=int)
    t.add_argument("--max-width", type=float, default=0.8, help="fraction of width before wrapping")
    t.add_argument("--margin", type=float, default=0.05, help="safe margin as fraction of the short side")
    t.add_argument("--quality", type=int, default=92)
    common(t)
    t.set_defaults(fn=cmd_text)

    lg = sub.add_parser("logo")
    lg.add_argument("input")
    lg.add_argument("--logo", required=True)
    lg.add_argument("--pos", choices=sorted(POSITIONS), default="bottom-right")
    lg.add_argument("--scale", type=float, default=0.15, help="logo width as fraction of image width")
    lg.add_argument("--opacity", type=float, default=1.0)
    lg.add_argument("--margin", type=float, default=0.04)
    lg.add_argument("--quality", type=int, default=92)
    common(lg)
    lg.set_defaults(fn=cmd_logo)

    cu = sub.add_parser("cutout")
    cu.add_argument("input")
    cu.add_argument("--key", default="auto", help="background colour #RRGGBB or 'auto' (from the border)")
    cu.add_argument("--tolerance", type=int, default=40, help="colour distance treated as background")
    cu.add_argument("--feather", type=int, default=30, help="soft-edge width in colour distance")
    cu.add_argument("--trim", action="store_true", help="crop to the remaining object")
    cu.add_argument("--keep-alpha", action="store_true")
    common(cu)
    cu.set_defaults(fn=cmd_cutout)

    up = sub.add_parser("upscale")
    up.add_argument("input")
    up.add_argument("--scale", type=float, default=2.0)
    up.add_argument("--sharpen", type=float, default=0.3, help="0 = none, 1 = strong")
    up.add_argument("--quality", type=int, default=92)
    common(up)
    up.set_defaults(fn=cmd_upscale)

    c = sub.add_parser("convert")
    c.add_argument("input")
    c.add_argument("--quality", type=int, default=92)
    common(c)
    c.set_defaults(fn=cmd_convert)

    s = sub.add_parser("sheet")
    s.add_argument("inputs", nargs="+")
    s.add_argument("--height", type=int, default=512)
    s.add_argument("--cols", type=int)
    common(s)
    s.set_defaults(fn=cmd_sheet)

    i = sub.add_parser("info")
    i.add_argument("input")
    i.set_defaults(fn=cmd_info)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
