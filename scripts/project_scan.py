#!/usr/bin/env python3
"""Read a project before creating a visual asset for it.

  project_scan.py [PROJECT_DIR]                         # project context as JSON
  project_scan.py PROJECT_DIR --component src/components/Hero.tsx   # image slots in one file

Reports (best effort, read-only, never modifies anything):
  * instruction files to read first (AGENTS.md, CLAUDE.md, CONTRIBUTING, design docs)
  * framework and how it loads images (public URL vs import vs image component)
  * where images already live (directories with most images) and the suggested
    destination for production assets, plus a non-public place for originals
  * icon libraries (use them for plain UI icons instead of generating)
  * design tokens: colours from Tailwind config / CSS custom properties, dark mode
  * check commands (build, typecheck, lint, test) the project already has
  * placeholders (placehold.co, picsum, TODO images…) that are candidates to replace
  * existing asset registry
--component lists each <img>/<Image>/background in the file with line, src, alt,
width/height, aspect hints and loading attributes, plus a recommended ratio.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", ".svelte-kit", ".astro",
             "out", "coverage", ".cache", "vendor", "__pycache__", ".venv", "venv", ".turbo", ".output",
             ".gemini-assets", "target", ".idea", ".vscode"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".svg", ".ico"}
CODE_EXT = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".html", ".htm", ".css", ".scss",
            ".sass", ".less", ".php", ".blade.php", ".erb", ".py", ".md", ".mdx", ".json"}
INSTRUCTION_FILES = ["AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "CONTRIBUTING.md", ".cursorrules",
                     ".github/copilot-instructions.md", "DESIGN.md", "docs/design.md", "docs/DESIGN.md",
                     "STYLEGUIDE.md", "README.md"]
ICON_LIBS = ["lucide-react", "lucide-vue-next", "lucide-svelte", "@heroicons/react", "@heroicons/vue",
             "react-icons", "@tabler/icons-react", "@phosphor-icons/react", "@fortawesome/fontawesome-svg-core",
             "@fortawesome/react-fontawesome", "@mui/icons-material", "@radix-ui/react-icons",
             "@iconify/react", "unplugin-icons", "bootstrap-icons", "@ant-design/icons", "ionicons",
             "@primer/octicons-react", "feather-icons", "remixicon"]
PLACEHOLDER_RE = re.compile(r"(placehold\.co|placeholder\.com|via\.placeholder|picsum\.photos|dummyimage\.com|"
                            r"source\.unsplash\.com|placekitten|loremflickr|fakeimg\.pl|"
                            r"src=[\"'][^\"']*placeholder[^\"']*[\"']|TODO[:\s].{0,40}(image|img|illustration|rasm))",
                            re.I)
REMOTE_GEMINI_RE = re.compile(r"(lh3\.googleusercontent\.com/(gg|rd-gg)/|gemini\.google\.com/(app|share)/|blob:https://gemini)",
                              re.I)

FRAMEWORKS = [  # (dependency, name, production dir candidates, how images are referenced)
    ("next", "Next.js", ["public/images", "public"], "public URL '/images/x.webp' with next/image <Image>, or static import from src for automatic width/height"),
    ("nuxt", "Nuxt", ["public/images", "public", "assets/images"], "public URL, or <NuxtImg> if @nuxt/image is installed"),
    ("@sveltejs/kit", "SvelteKit", ["static/images", "static", "src/lib/assets"], "import from $lib/assets (hashed) or /images/x from static/"),
    ("astro", "Astro", ["src/assets/images", "src/assets", "public/images"], "import from src/assets and render with <Image> from astro:assets"),
    ("@angular/core", "Angular", ["public/images", "src/assets/images", "src/assets"], "path relative to the assets/public folder in angular.json, NgOptimizedImage if used"),
    ("gatsby", "Gatsby", ["src/images", "static/images"], "gatsby-plugin-image StaticImage or static/"),
    ("@remix-run/react", "Remix", ["public/images", "public", "app/assets"], "public URL or import"),
    ("vue", "Vue (Vite)", ["src/assets/images", "src/assets", "public/images"], "import from src/assets (hashed) or /images/x from public/"),
    ("svelte", "Svelte (Vite)", ["src/assets", "public/images"], "import from src/assets or /images/x from public/"),
    ("react", "React (Vite/CRA)", ["src/assets/images", "src/assets", "public/images"], "import x from './assets/x.webp' (bundled, hashed) or '/images/x.webp' from public/"),
]


def read(path, limit=400_000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def walk(root, exts=None, max_files=20000):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") or d in (".claude", ".github")]
        for f in filenames:
            if exts is None or os.path.splitext(f)[1].lower() in exts or (".blade.php" in exts and f.endswith(".blade.php")):
                n += 1
                if n > max_files:
                    return
                yield os.path.join(dirpath, f)


def detect_framework(root):
    pkg = {}
    p = os.path.join(root, "package.json")
    if os.path.exists(p):
        try:
            pkg = json.loads(read(p))
        except json.JSONDecodeError:
            pkg = {}
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for dep, name, dirs, how in FRAMEWORKS:
        if dep in deps:
            bundler = "vite" if "vite" in deps else ("webpack" if "webpack" in deps or "react-scripts" in deps else None)
            return {"name": name, "dependency": dep, "version": deps[dep], "bundler": bundler,
                    "dest_candidates": dirs, "image_reference": how}, pkg, deps
    if os.path.exists(os.path.join(root, "artisan")):
        return {"name": "Laravel", "dest_candidates": ["public/images", "resources/images"],
                "image_reference": "asset('images/x.webp') in Blade, or Vite import from resources"}, pkg, deps
    if os.path.exists(os.path.join(root, "manage.py")):
        return {"name": "Django", "dest_candidates": ["static/images"],
                "image_reference": "{% static 'images/x.webp' %} (check STATICFILES_DIRS / app static dirs)"}, pkg, deps
    if os.path.exists(os.path.join(root, "Gemfile")) and os.path.isdir(os.path.join(root, "app")):
        return {"name": "Rails", "dest_candidates": ["app/assets/images"],
                "image_reference": "image_tag 'x.webp'"}, pkg, deps
    if os.path.exists(os.path.join(root, "index.html")):
        return {"name": "Static HTML", "dest_candidates": ["images", "assets/images", "assets", "img"],
                "image_reference": "relative path from the HTML file"}, pkg, deps
    return {"name": "unknown", "dest_candidates": ["assets/images"], "image_reference": "unknown"}, pkg, deps


def image_dirs(root):
    c = Counter()
    exts = Counter()
    for f in walk(root, IMG_EXT):
        rel = os.path.relpath(os.path.dirname(f), root)
        c[rel] += 1
        exts[os.path.splitext(f)[1].lower()] += 1
    return c, exts


def tokens(root):
    colors, dark = {}, []
    for name in ("tailwind.config.js", "tailwind.config.ts", "tailwind.config.cjs", "tailwind.config.mjs"):
        t = read(os.path.join(root, name))
        if t:
            for k, v in re.findall(r"['\"]?([\w-]+)['\"]?\s*:\s*['\"](#[0-9a-fA-F]{3,8})['\"]", t)[:40]:
                colors.setdefault(f"tailwind.{k}", v)
            if "darkMode" in t:
                dark.append(f"{name}: darkMode " + (re.search(r"darkMode\s*:\s*([^,\n]+)", t).group(1).strip()
                                                     if re.search(r"darkMode\s*:\s*([^,\n]+)", t) else ""))
    for f in walk(root, {".css", ".scss", ".sass", ".less"}, 400):
        t = read(f, 200_000)
        for k, v in re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8}|oklch\([^)]+\)|hsl\([^)]+\)|rgb\([^)]+\))", t)[:60]:
            colors.setdefault(f"--{k}", v)
        if "prefers-color-scheme: dark" in t or re.search(r"\.dark\b|\[data-theme=['\"]?dark", t):
            dark.append(os.path.relpath(f, root))
    return dict(list(colors.items())[:60]), sorted(set(dark))[:10]


def placeholders_and_remote(root):
    ph, remote = [], []
    for f in walk(root, CODE_EXT - {".json", ".md"}, 4000):
        for i, line in enumerate(read(f, 300_000).splitlines(), 1):
            if PLACEHOLDER_RE.search(line):
                ph.append({"file": os.path.relpath(f, root), "line": i, "text": line.strip()[:140]})
            if REMOTE_GEMINI_RE.search(line):
                remote.append({"file": os.path.relpath(f, root), "line": i})
    return ph[:40], remote[:20]


def check_commands(root, pkg):
    scripts = pkg.get("scripts", {})
    cmds = {}
    runner = "pnpm" if os.path.exists(os.path.join(root, "pnpm-lock.yaml")) else \
        "yarn" if os.path.exists(os.path.join(root, "yarn.lock")) else \
        "bun" if os.path.exists(os.path.join(root, "bun.lockb")) or os.path.exists(os.path.join(root, "bun.lock")) else "npm"
    for key in ("build", "typecheck", "type-check", "check", "lint", "test"):
        if key in scripts:
            cmds[key] = f"{runner} run {key}"
    if "typecheck" not in cmds and "type-check" not in cmds and os.path.exists(os.path.join(root, "tsconfig.json")):
        cmds["typecheck"] = "npx tsc --noEmit"
    if "dev" in scripts:
        cmds["dev"] = f"{runner} run dev"
    if os.path.exists(os.path.join(root, "manage.py")):
        cmds["check"] = "python manage.py check"
    return cmds, runner


def suggest_dest(root, fw, dirs):
    for cand in fw["dest_candidates"]:
        if os.path.isdir(os.path.join(root, cand)) and dirs.get(cand):
            return cand
    if dirs:
        public_like = [d for d, _ in dirs.most_common() if re.match(r"(public|static|src/assets|assets|images|img|app/assets|resources)", d)]
        if public_like:
            return public_like[0]
    for cand in fw["dest_candidates"]:
        if os.path.isdir(os.path.join(root, cand)):
            return cand
    return fw["dest_candidates"][0]


def is_public_dir(dest):
    return bool(re.match(r"(public|static|www|htdocs)(/|$)", dest))


def scan(root):
    root = os.path.abspath(root)
    fw, pkg, deps = detect_framework(root)
    dirs, exts = image_dirs(root)
    colors, dark = tokens(root)
    ph, remote = placeholders_and_remote(root)
    cmds, runner = check_commands(root, pkg)
    dest = suggest_dest(root, fw, dirs)
    registry = next((r for r in (".gemini-assets/registry.json", "assets.registry.json", "docs/assets-registry.json")
                     if os.path.exists(os.path.join(root, r))), None)
    usage = Counter()
    for f in walk(root, {".tsx", ".jsx", ".vue", ".svelte", ".astro", ".ts", ".js"}, 3000):
        t = read(f, 200_000)
        if "next/image" in t:
            usage["next/image"] += 1
        if "astro:assets" in t:
            usage["astro:assets"] += 1
        if "<NuxtImg" in t or "<NuxtPicture" in t:
            usage["@nuxt/image"] += 1
        if "NgOptimizedImage" in t or "ngSrc" in t:
            usage["NgOptimizedImage"] += 1
        if re.search(r"import\s+\w+\s+from\s+['\"][^'\"]+\.(png|jpe?g|webp|avif|svg)['\"]", t):
            usage["static import"] += 1
        if re.search(r"src=[\"']/[^\"']+\.(png|jpe?g|webp|avif|svg)", t):
            usage["public URL"] += 1
    return {
        "root": root,
        "read_first": [f for f in INSTRUCTION_FILES if os.path.exists(os.path.join(root, f))],
        "framework": fw,
        "package_manager": runner if pkg else None,
        "image_usage_patterns": dict(usage),
        "image_dirs": dict(dirs.most_common(8)),
        "image_formats": dict(exts),
        "suggested_dest": dest,
        "dest_is_public": is_public_dir(dest),
        "suggested_originals_dir": ".gemini-assets/originals",
        "icon_libraries": [l for l in ICON_LIBS if l in deps],
        "design_tokens": colors,
        "dark_mode_signals": dark,
        "check_commands": cmds,
        "placeholders": ph,
        "remote_gemini_urls_in_code": remote,
        "asset_registry": registry,
        "notes": [
            "Read every file in read_first before deciding anything.",
            "Plain UI icons: use an icon library listed above when one fits." if any(l in deps for l in ICON_LIBS) else
            "No icon library detected.",
            "Production code must reference local assets, never Gemini/googleusercontent URLs.",
        ],
    }


# ── component inspection ────────────────────────────────────────────────────
TAG_RE = re.compile(r"<(img|Image|NuxtImg|NuxtPicture|Picture|source|StaticImage|enhanced:img)\b([^>]*?)/?>", re.S)
ATTR_RE = re.compile(r"([:@\w-]+)\s*=\s*(\{[^{}]*\}|\"[^\"]*\"|'[^']*')")
ASPECT_CLASS_RE = re.compile(r"aspect-(video|square|\[(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\])")
BG_RE = re.compile(r"background(?:-image)?\s*:\s*url\(([^)]+)\)|bg-\[url\(([^)]+)\)\]")


def _reduce(w, h):
    from math import gcd
    try:
        a, b = float(w), float(h)
    except ValueError:
        return f"{w}:{h}"
    if a.is_integer() and b.is_integer() and a and b:
        g = gcd(int(a), int(b))
        return f"{int(a) // g}:{int(b) // g}"
    return f"{a:g}:{b:g}"


def ratio_from(attrs, context):
    r, why = _ratio_from(attrs, context)
    return (_reduce(*r.split(":")), why) if r else (r, why)


def _ratio_from(attrs, context):
    w, h = attrs.get("width"), attrs.get("height")
    try:
        if w and h:
            wv, hv = float(re.sub(r"[^\d.]", "", w)), float(re.sub(r"[^\d.]", "", h))
            if wv and hv:
                return f"{wv:g}:{hv:g}", "width/height attributes"
    except ValueError:
        pass
    m = re.search(r"(placehold|placeholder|dummyimage|picsum|fakeimg)[^\"'\s]*?(\d{2,5})[x/](\d{2,5})",
                  attrs.get("src", ""), re.I)
    if m:
        return f"{m.group(2)}:{m.group(3)}", "placeholder URL size"
    m = ASPECT_CLASS_RE.search(context)
    if m:
        if m.group(1) == "video":
            return "16:9", "aspect-video class"
        if m.group(1) == "square":
            return "1:1", "aspect-square class"
        return f"{m.group(2)}:{m.group(3)}", "aspect-[w/h] class"
    m = re.search(r"aspect-ratio\s*:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", context)
    if m:
        return f"{m.group(1)}:{m.group(2)}", "CSS aspect-ratio"
    return None, None


def inspect_component(root, path):
    full = path if os.path.isabs(path) else os.path.join(root, path)
    text = read(full)
    if not text:
        sys.exit(f"Cannot read {path}")
    lines = text.splitlines()
    slots = []
    for m in TAG_RE.finditer(text):
        line_no = text.count("\n", 0, m.start()) + 1
        attrs = {k: v.strip("{}\"' ") for k, v in ATTR_RE.findall(m.group(2))}
        ctx = "\n".join(lines[max(0, line_no - 6): line_no + 3])
        ratio, why = ratio_from(attrs, ctx + " " + attrs.get("className", "") + " " + attrs.get("class", ""))
        src = attrs.get("src") or attrs.get("srcSet") or attrs.get("srcset") or ""
        slots.append({
            "line": line_no, "tag": m.group(1), "src": src[:120],
            "alt": attrs.get("alt"), "width": attrs.get("width"), "height": attrs.get("height"),
            "class": (attrs.get("className") or attrs.get("class") or "")[:160],
            "loading": attrs.get("loading"), "priority": "priority" in m.group(2) or attrs.get("fetchpriority"),
            "sizes": attrs.get("sizes"), "ratio_hint": ratio, "ratio_source": why,
            "is_placeholder": bool(PLACEHOLDER_RE.search(m.group(0))),
            "is_remote_gemini": bool(REMOTE_GEMINI_RE.search(m.group(0))),
            "issues": [x for x in (
                None if attrs.get("alt") is not None else "missing alt (empty alt=\"\" if decorative)",
                None if (attrs.get("width") and attrs.get("height")) or ratio or m.group(1) in ("Image",) else
                "no width/height or aspect ratio → layout shift risk",
            ) if x],
        })
    for i, line in enumerate(lines, 1):
        for bm in BG_RE.finditer(line):
            slots.append({"line": i, "tag": "css-background", "src": (bm.group(1) or bm.group(2) or "")[:120],
                          "ratio_hint": ratio_from({}, line)[0], "is_placeholder": bool(PLACEHOLDER_RE.search(line))})
    comp_hint = "hero" if re.search(r"hero|banner|jumbotron", path + text[:2000], re.I) else None
    return {"file": os.path.relpath(full, root), "image_slots": slots,
            "likely_above_the_fold": comp_hint == "hero",
            "advice": ("Above-the-fold image: do NOT lazy-load; use priority/fetchpriority=high and explicit size."
                       if comp_hint == "hero" else "Below the fold: loading=\"lazy\" is appropriate.")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--component", help="file to inspect for image slots")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.project):
        sys.exit(f"Not a directory: {a.project}")
    out = inspect_component(os.path.abspath(a.project), a.component) if a.component else scan(a.project)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
