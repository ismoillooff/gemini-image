#!/usr/bin/env python3
"""Lightweight per-project asset registry: <project>/.gemini-assets/registry.json

  asset_registry.py find  PROJECT "hero illustration landing"   # reuse before generating
  asset_registry.py list  PROJECT
  asset_registry.py usage PROJECT [--id hero-landing]             # where each asset is referenced
  asset_registry.py verify PROJECT [--strict]                     # integrity + wiring checks

verify checks, per asset: original and production files exist, are real images,
hashes match the registry, alpha present when required; at least one source file
references a production file; and project-wide: no code references Gemini or
googleusercontent URLs, no .svg files that are actually raster data or a raster
wrapped in <svg><image>. Exit 1 on any error (warnings don't fail unless --strict).
No secrets are ever stored: only paths, sizes, hashes, model name and a short brief.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagekit as ik  # noqa: E402
from project_scan import CODE_EXT, REMOTE_GEMINI_RE, read, walk  # noqa: E402

REG_REL = os.path.join(".gemini-assets", "registry.json")


class Registry:
    def __init__(self, project, path=None):
        self.project = os.path.abspath(project)
        self.path = os.path.join(self.project, path or REG_REL)
        self.data = {"version": 1, "assets": []}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def get(self, asset_id):
        return next((a for a in self.data["assets"] if a["id"] == asset_id), None)

    def upsert(self, entry):
        old = self.get(entry["id"])
        if old:
            entry["history"] = (old.get("history") or []) + [{k: old.get(k) for k in ("updated", "original", "sha256")}]
            self.data["assets"][self.data["assets"].index(old)] = {**old, **entry}
        else:
            self.data["assets"].append(entry)
        return self.get(entry["id"])

    def owns(self, rel_path):
        rel_path = rel_path.replace(os.sep, "/")
        for a in self.data["assets"]:
            if any(p["path"] == rel_path for p in a.get("production", [])):
                return a
        return None


def score(asset, words):
    hay = " ".join(str(asset.get(k, "")) for k in ("id", "purpose", "brief", "tags", "used_in")).lower()
    return sum(1 for w in words if w in hay)


def find_usages(project, asset):
    names = set()
    for p in asset.get("production", []):
        names.add(os.path.basename(p["path"]))
        if p.get("public_url"):
            names.add(p["public_url"])
    hits = []
    for f in walk(project, CODE_EXT - {".json"}, 6000):
        if f.startswith(os.path.join(project, ".gemini-assets")):
            continue
        t = read(f, 400_000)
        if any(n in t for n in names):
            hits.append(os.path.relpath(f, project).replace(os.sep, "/"))
    return sorted(hits)


def fake_svgs(project):
    bad = []
    for f in walk(project, {".svg"}, 4000):
        with open(f, "rb") as fh:
            head = fh.read(4096)
        kind, _ = ik.sniff(head)
        rel = os.path.relpath(f, project)
        if kind not in ("svg", "xml"):
            bad.append({"file": rel, "problem": f"named .svg but contains {kind} data"})
            continue
        body = read(f, 2_000_000)
        has_vector = re.search(r"<(path|circle|rect|polygon|polyline|ellipse|line|text|g)\b", body)
        if re.search(r"<image\b[^>]*(href|xlink:href)=[\"']data:image/(png|jpe?g|webp)", body) and not has_vector:
            bad.append({"file": rel, "problem": "SVG only wraps an embedded raster image (not real vector)"})
    return bad


def verify(reg, strict=False):
    errors, warnings, report = [], [], []
    for a in reg.data["assets"]:
        r = {"id": a["id"], "errors": [], "warnings": []}
        o = a.get("original")
        if o:
            op = os.path.join(reg.project, o)
            if not os.path.exists(op):
                r["errors"].append(f"original missing: {o}")
            elif a.get("original_sha256") and ik.sha256_file(op) != a["original_sha256"]:
                r["errors"].append("original changed since registration (hash mismatch)")
        for p in a.get("production", []):
            pp = os.path.join(reg.project, p["path"])
            v = ik.validate(pp, require_alpha=bool(a.get("requires_alpha")))
            if not v.ok:
                r["errors"].append(f"{p['path']}: " + "; ".join(v.errors))
            elif p.get("sha256") and v.sha256 != p["sha256"]:
                r["warnings"].append(f"{p['path']}: changed since export (hash differs)")
        used = find_usages(reg.project, a)
        a["used_in"] = used
        if not used:
            r["errors" if strict else "warnings"].append("not referenced anywhere in the code (asset only sits in a folder)")
        report.append({**r, "used_in": used})
        errors += [f"{a['id']}: {e}" for e in r["errors"]]
        warnings += [f"{a['id']}: {w}" for w in r["warnings"]]
    for f in walk(reg.project, CODE_EXT - {".json", ".md"}, 6000):
        for i, line in enumerate(read(f, 300_000).splitlines(), 1):
            if REMOTE_GEMINI_RE.search(line):
                errors.append(f"{os.path.relpath(f, reg.project)}:{i}: code references a Gemini/googleusercontent "
                              "URL (temporary, login-bound) — use a local asset")
    for b in fake_svgs(reg.project):
        errors.append(f"{b['file']}: {b['problem']}")
    return errors, warnings, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["find", "list", "usage", "verify"])
    ap.add_argument("project")
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--id")
    ap.add_argument("--registry", help="registry path relative to project")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    reg = Registry(a.project, a.registry)

    if a.cmd == "list":
        print(json.dumps([{k: x.get(k) for k in ("id", "purpose", "production", "used_in", "updated")}
                          for x in reg.data["assets"]], indent=2, ensure_ascii=False))
    elif a.cmd == "find":
        words = [w for w in re.findall(r"\w+", a.query.lower()) if len(w) > 2]
        ranked = sorted(((score(x, words), x) for x in reg.data["assets"]), key=lambda t: -t[0])
        hits = [{"id": x["id"], "score": s, "purpose": x.get("purpose"),
                 "production": [p["path"] for p in x.get("production", [])], "used_in": x.get("used_in")}
                for s, x in ranked if s > 0]
        print(json.dumps({"matches": hits[:5],
                          "advice": "Reuse a match if it fits; otherwise generate." if hits else "No existing asset matches."},
                         indent=2, ensure_ascii=False))
    elif a.cmd == "usage":
        out = {}
        for x in reg.data["assets"]:
            if a.id and x["id"] != a.id:
                continue
            x["used_in"] = find_usages(reg.project, x)
            out[x["id"]] = x["used_in"]
        reg.save()
        print(json.dumps(out, indent=2))
    else:
        errors, warnings, report = verify(reg, a.strict)
        if os.path.exists(reg.path):
            reg.save()
        print(json.dumps({"ok": not errors, "errors": errors, "warnings": warnings, "assets": report},
                         indent=2, ensure_ascii=False))
        return 1 if errors else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
