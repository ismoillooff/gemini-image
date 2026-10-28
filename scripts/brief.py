#!/usr/bin/env python3
"""Turn a brief into a well-ordered Gemini prompt, lint it, and plan the quality mode.

  brief.py --task product --json brief.json            # full brief from a file
  brief.py --task hero --purpose "SaaS landing hero" --subject "…" --aspect 16:9 --mode standard
  brief.py --lint "ultra detailed 8k masterpiece, flat vector, photorealistic, night at noon"
  brief.py --criteria icon                              # quality criteria + checklist for a task type

Prompt order (always): purpose → subject → composition → materials → lighting →
colour & style → must-keep elements → constraints → output requirements.

Lint finds: contradictions (style, time of day, background, detail level, ratio,
text), empty quality words ("high quality", "8k", "masterpiece", "ultra detailed")
that should be replaced by visible attributes, size claims ("4K" in the prompt is
NOT the output size), unsupported parameters (seed, negative prompt, steps), and
detail that hurts the task (texture on icons/minimal design).
Output: JSON with prompt, lint findings, mode plan (tier/size/variants/attempts/budget)
and the QA checklist for the task. Nothing is sent anywhere.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

TASKS = {
    "product": {
        "criteria": ["silhouette and proportions match the real product", "materials read correctly (metal, glass, fabric…)",
                     "label/logo exactly as the reference", "clean edges against background", "believable contact shadow"],
        "detail_policy": "high on the product, low on the background",
        "defaults": {"composition": "product centred, three-quarter view at eye level, fills ~60% of frame, generous margins",
                     "lighting": "large soft key light from front-left, subtle rim light from behind, soft contact shadow",
                     "style": "photorealistic commercial studio photograph"},
    },
    "photo": {
        "criteria": ["anatomy (hands, faces, limbs) correct", "consistent perspective and horizon", "light direction matches shadows and reflections",
                     "believable textures, no plastic skin", "no melted/duplicated objects", "signage/text correct"],
        "detail_policy": "natural: sharp subject, background as a real lens would render it",
        "defaults": {"composition": "eye-level, rule of thirds, clear foreground/midground/background",
                     "lighting": "single natural light source with consistent direction", "style": "documentary-style photograph"},
    },
    "illustration": {
        "criteria": ["one consistent style throughout", "clear readable silhouettes", "composition leads the eye to the subject",
                     "limited palette", "no stray photographic elements"],
        "detail_policy": "moderate and consistent; avoid noisy micro-detail",
        "defaults": {"composition": "clear focal point, balanced negative space", "lighting": "simple, stylised light with soft shadows",
                     "style": "clean modern illustration"},
    },
    "icon": {
        "criteria": ["recognisable at 16-32 px", "single clear metaphor", "clean contour, consistent stroke weight",
                     "works on light and dark backgrounds", "no tiny detail, no texture, no text"],
        "detail_policy": "minimal: no texture, no gradients unless brand uses them, no small details",
        "defaults": {"composition": "single centred symbol, 15% padding, square", "lighting": "none (flat)",
                     "style": "flat vector-style icon with consistent stroke weight"},
    },
    "hero": {
        "criteria": ["one strong focal subject", "calm empty area for headline/CTA", "survives responsive crops (desktop and mobile)",
                     "brand palette", "background does not fight the text"],
        "detail_policy": "detail on the focal subject; quiet area where text goes",
        "defaults": {"composition": "subject on the right third, calm negative space on the left for headline",
                     "lighting": "soft directional light from the upper left", "style": "brand-consistent modern illustration or photo"},
    },
    "banner": {
        "criteria": ["readable at a glance", "safe area for text and CTA", "wide composition survives cropping", "strong contrast for text area"],
        "detail_policy": "low in text area",
        "defaults": {"composition": "wide, focal element at one side, flat space for headline", "lighting": "even, bright",
                     "style": "bold commercial graphic"},
    },
    "background": {
        "criteria": ["does not compete with foreground content", "low contrast in content areas", "tileable or crop-safe if needed", "no focal objects"],
        "detail_policy": "low; soft, even texture at most",
        "defaults": {"composition": "evenly distributed, no focal point", "lighting": "soft, even", "style": "subtle abstract background"},
    },
}

VAGUE = {
    r"\b(high|best|top|ultra|super)[ -]?quality\b": "say which visible qualities matter (e.g. 'crisp label text, clean edges')",
    r"\b(ultra|hyper|highly|extremely|super)[ -]?detailed\b": "name the details that matter (e.g. 'visible brushed-metal grain on the bezel')",
    r"\bmasterpiece\b|\baward[- ]winning\b|\bstunning\b|\bbeautiful\b|\bamazing\b": "describe the look instead (composition, light, materials)",
    r"\bperfect (lighting|light)\b": "give light source direction, softness and colour temperature",
    r"\b(8k|16k|uhd|hd)\b": "resolution words don't set output size; use imageSize where supported and verify the file",
    r"\bhigh[- ]?res(olution)?\b": "use imageSize where supported; verify the downloaded file",
    r"\btrending on \w+\b|\bartstation\b|\bunreal engine\b|\boctane render\b": "generator buzzwords; describe the intended look",
    r"\brealistic\b(?!.*photograph)": "say 'photograph' and give camera position, light and material cues",
}
CONFLICTS = [
    (r"\bphoto(realistic|graph)?\b|\bdslr\b", r"\bflat (vector|illustration|design)\b|\bcartoon\b|\b2d\b", "photographic vs flat/cartoon style"),
    (r"\bnight\b|\bmidnight\b", r"\bnoon\b|\bmidday\b|\bbright daylight\b", "night vs daytime"),
    (r"\b(golden hour|sunset|sunrise)\b", r"\bnoon\b|\bmidday\b", "golden hour vs midday"),
    (r"\btransparent background\b", r"\b(sky|landscape|room|street|scene|interior)\b", "transparent background vs a full scene"),
    (r"\bminimal(ist)?\b", r"\b(highly|ultra|extremely|intricate(ly)?) detail", "minimal vs highly detailed"),
    (r"\bno text\b", r"[\"“][^\"”]{2,}[\"”]", "'no text' but quoted text is requested"),
    (r"\b(16:9|21:9|landscape|wide|horizontal)\b", r"\b(9:16|portrait orientation|vertical|story)\b", "wide vs vertical framing"),
    (r"\bsoft (light|shadows?)\b", r"\bhard (light|shadows?)\b", "soft vs hard light"),
    (r"\bwhite background\b", r"\bblack background\b|\bdark background\b", "white vs dark background"),
]
UNSUPPORTED = [r"\bseed\s*[:=]?\s*\d+", r"--no\b|\bnegative prompt\b", r"\bsteps\s*[:=]\s*\d+", r"\bcfg\b|\bguidance scale\b",
               r"--ar\b|--v\s*\d|--style\b"]
TEXTURE_WORDS = r"\b(texture|grain|noise|grunge|scratches|weathered|intricate)\b"


def lint(prompt, task=None):
    p = prompt.lower()
    findings = []
    for pat, advice in VAGUE.items():
        for m in re.finditer(pat, p):
            findings.append({"type": "vague", "text": m.group(0), "advice": advice})
    for a, b, label in CONFLICTS:
        if re.search(a, p) and re.search(b, p):
            findings.append({"type": "conflict", "text": label, "advice": "pick one; contradictory instructions average out"})
    for pat in UNSUPPORTED:
        if re.search(pat, p):
            findings.append({"type": "unsupported", "text": re.search(pat, p).group(0),
                             "advice": "not a documented Gemini parameter; remove it (state exclusions in words instead)"})
    if re.search(r"\b4k\b", p):
        findings.append({"type": "size-claim", "text": "4K", "advice": "'4K' in the prompt is not the output size; request imageSize via API (supported models) and check the file"})
    if re.search(r"\bno text\b", p) and re.search(r"\(([^)]*\b[A-Z]{2,}\b[^)]*)\)", prompt):
        findings.append({"type": "text-risk", "text": re.search(r"\(([^)]*\b[A-Z]{2,}\b[^)]*)\)", prompt).group(0),
                         "advice": "named labels/acronyms in the subject often get drawn as text; describe them by look"})
    if task in ("icon", "background") and re.search(TEXTURE_WORDS, p):
        findings.append({"type": "detail-policy", "text": re.search(TEXTURE_WORDS, p).group(0),
                         "advice": f"{task}: {TASKS[task]['detail_policy']}"})
    words = len(prompt.split())
    if words > 220:
        findings.append({"type": "length", "text": f"{words} words", "advice": "too long: cut repeated adjectives; keep visible facts"})
    common = {"image", "without", "light", "lighting", "background", "colour", "color", "subject", "style"}
    dup = [w for w in set(re.findall(r"\b\w{5,}\b", p)) if len(re.findall(rf"\b{w}\b", p)) >= 4 and w not in common]
    if dup:
        findings.append({"type": "repetition", "text": ", ".join(sorted(dup)[:5]), "advice": "repeated words add no control"})
    return findings


def compose(task, b):
    d = TASKS[task]["defaults"]
    parts = []
    if b.get("purpose"):
        parts.append(f"Purpose: {b['purpose']}.")
    subj = b.get("subject", "").strip()
    if b.get("key_features"):
        subj += ("; key features: " + ", ".join(b["key_features"])) if subj else ", ".join(b["key_features"])
    parts.append(f"Subject: {subj}.")
    comp = b.get("composition") or d["composition"]
    layers = [f"foreground: {b['foreground']}" if b.get("foreground") else "",
              f"midground: {b['midground']}" if b.get("midground") else "",
              f"background: {b['background']}" if b.get("background") else ""]
    layers = "; ".join(x for x in layers if x)
    parts.append(f"Composition: {comp}{'; ' + layers if layers else ''}.")
    if b.get("materials"):
        parts.append("Materials: " + "; ".join(b["materials"]) + ".")
    parts.append(f"Lighting: {b.get('lighting') or d['lighting']}.")
    style = b.get("style") or d["style"]
    palette = b.get("palette")
    parts.append(f"Style: {style}" + (f"; colour palette {palette}" if palette else "") +
                 (f"; contrast {b['contrast']}" if b.get("contrast") else "") + ".")
    if b.get("keep"):
        parts.append("Keep exactly: " + "; ".join(b["keep"]) + ".")
    text = b.get("text")
    if text:
        parts.append(f'Text: only the words "{text}", spelled exactly, {b.get("text_style", "clean sans-serif")}.')
    elif b.get("text_space"):
        parts.append(f"No text; leave clean empty space {b['text_space']} for a headline added later.")
    else:
        parts.append("No text, letters or logos except those listed.")
    avoid = list(b.get("avoid", [])) + ["watermarks"]
    parts.append("Avoid: " + ", ".join(dict.fromkeys(avoid)) + ".")
    out = [b.get("aspect") and f"aspect ratio {b['aspect']}", b.get("transparent") and
           "isolated on a perfectly flat pure #00FF00 background (it will be cut out later)"]
    out = [x for x in out if x]
    if out:
        parts.append("Output: " + ", ".join(out) + ".")
    return " ".join(parts)


def plan(mode_name, task, b):
    modes = json.load(open(os.path.join(ROOT, "templates", "quality-modes.json"), encoding="utf-8"))
    mode = dict(modes[mode_name])
    if b.get("display") in modes["size_by_display"]:
        want = modes["size_by_display"][b["display"]]
        order = ["1K", "2K", "4K"]
        mode["size"] = max(mode["size"], want, key=order.index)
    caps = json.load(open(os.path.join(ROOT, "references", "model-capabilities.json"), encoding="utf-8"))
    tier_models = caps["tiers"][mode["tier"]]
    supported = [m for m in tier_models if mode["size"] in caps["models"][m]["sizes"]]
    mode["models"] = supported or tier_models
    if not supported:
        mode["note"] = f"{mode['size']} not supported by the {mode['tier']} tier; the file will be ~1K — ask before using a higher tier"
    if task == "icon":
        mode["variants"] = max(mode["variants"], 2) if mode_name != "draft" else 1
    return mode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=sorted(TASKS))
    ap.add_argument("--json", help="brief JSON file (see templates/brief.example.json)")
    ap.add_argument("--mode", choices=["draft", "standard", "final"], default="standard")
    for f in ("purpose", "subject", "composition", "lighting", "style", "palette", "aspect", "text", "text_space",
              "foreground", "midground", "background", "display"):
        ap.add_argument(f"--{f.replace('_', '-')}")
    ap.add_argument("--material", action="append", dest="materials")
    ap.add_argument("--keep", action="append")
    ap.add_argument("--avoid", action="append")
    ap.add_argument("--transparent", action="store_true")
    ap.add_argument("--lint", metavar="PROMPT", help="only lint an existing prompt")
    ap.add_argument("--criteria", choices=sorted(TASKS), help="print criteria and checklist for a task type")
    a = ap.parse_args(argv)

    if a.criteria:
        checks = json.load(open(os.path.join(ROOT, "templates", "qa-checklists.json"), encoding="utf-8"))
        print(json.dumps({"task": a.criteria, "criteria": TASKS[a.criteria]["criteria"],
                          "detail_policy": TASKS[a.criteria]["detail_policy"], "checklist": checks[a.criteria]}, indent=2))
        return 0
    if a.lint:
        f = lint(a.lint, a.task)
        print(json.dumps({"findings": f, "ok": not any(x["type"] in ("conflict", "unsupported") for x in f)}, indent=2))
        return 0 if not any(x["type"] in ("conflict", "unsupported") for x in f) else 1
    if not a.task:
        ap.error("--task is required (or use --lint / --criteria)")
    b = {}
    if a.json:
        with open(a.json, encoding="utf-8") as fh:
            b = json.load(fh)
    for k, v in vars(a).items():
        if k in ("task", "json", "mode", "lint", "criteria") or v in (None, False, []):
            continue
        b[k] = v
    if not b.get("subject"):
        ap.error("a subject is required (--subject or in the JSON)")
    prompt = compose(a.task, b)
    findings = lint(prompt, a.task)
    blocking = [x for x in findings if x["type"] in ("conflict", "unsupported")]
    checks = json.load(open(os.path.join(ROOT, "templates", "qa-checklists.json"), encoding="utf-8"))
    print(json.dumps({"task": a.task, "prompt": prompt, "words": len(prompt.split()), "lint": findings,
                      "ok": not blocking, "mode": a.mode, "plan": plan(a.mode, a.task, b),
                      "criteria": TASKS[a.task]["criteria"], "qa_checklist": checks[a.task]},
                     indent=2, ensure_ascii=False))
    return 0 if not blocking else 1


if __name__ == "__main__":
    sys.exit(main())
