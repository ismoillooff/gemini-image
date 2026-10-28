"""Tests for the quality layer: brief/prompt lint, modes & budgets, capability checks,
QA sheets, metrics, upscale labelling. Offline only."""
import base64
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import brief            # noqa: E402
import fetch_image      # noqa: E402
import generate_image   # noqa: E402
import imagekit as ik   # noqa: E402
import postprocess      # noqa: E402
import qa               # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402


def run(module, args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = module.main(args) or 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if not isinstance(e.code, int) and e.code:
                err.write(str(e.code))
    return code, out.getvalue(), err.getvalue()


def scene(w=800, h=450):
    im = Image.new("RGB", (w, h), (90, 120, 160))
    d = ImageDraw.Draw(im)
    for i in range(0, w, 40):
        d.line((i, 0, i, h), fill=(200, 210, 220), width=2)
    d.ellipse((w // 3, h // 4, w // 3 + 200, h // 4 + 200), fill=(240, 180, 60))
    return im


class TestBrief(unittest.TestCase):
    def test_lint_finds_conflicts_vague_unsupported_and_size_claims(self):
        f = brief.lint("ultra detailed masterpiece, flat vector photorealistic, night scene at noon, seed: 7, --no people, 4K")
        types = {x["type"] for x in f}
        self.assertTrue({"vague", "conflict", "unsupported", "size-claim"} <= types)
        conflicts = " ".join(x["text"] for x in f if x["type"] == "conflict")
        self.assertIn("photographic vs flat", conflicts)
        self.assertIn("night vs daytime", conflicts)

    def test_clean_prompt_passes(self):
        p = brief.compose("product", {"subject": "white ceramic mug", "materials": ["glossy glaze"], "aspect": "4:5"})
        f = brief.lint(p, "product")
        self.assertFalse([x for x in f if x["type"] in ("conflict", "unsupported")], f)

    def test_prompt_follows_the_required_order(self):
        p = brief.compose("hero", {"purpose": "landing hero", "subject": "robot hand", "materials": ["brushed aluminium"],
                                   "palette": "navy and amber", "keep": ["logo"], "avoid": ["clutter"], "aspect": "16:9"})
        order = ["Purpose:", "Subject:", "Composition:", "Materials:", "Lighting:", "Style:", "Keep exactly:", "Avoid:", "Output:"]
        idx = [p.index(k) for k in order]
        self.assertEqual(idx, sorted(idx))

    def test_icon_rejects_texture(self):
        f = brief.lint("app icon of a cloud with grunge texture", "icon")
        self.assertTrue(any(x["type"] == "detail-policy" for x in f))

    def test_acronym_labels_flagged_as_text_risk(self):
        f = brief.lint("stacked layers (cloud, data, AI). No text.")
        self.assertTrue(any(x["type"] == "text-risk" for x in f))

    def test_snippets_8_and_9_exist_and_parse(self):
        import subprocess, shutil
        if not shutil.which("node"):
            self.skipTest("node missing")
        src = open(os.path.join(os.path.dirname(HERE), "scripts", "browser_snippets.js")).read()
        for n in ("8", "9"):
            body = src.split(f"// ── {n}.")[1].split("// ── ")[0]
            body = "\n".join(l for l in body.splitlines()[1:] if not l.strip().startswith("//")).strip().rstrip(";")
            subprocess.run(["node", "-e", f"new Function('return (' + {json.dumps(body)} + ')')"], check=True)

    def test_quoted_text_conflicts_with_no_text(self):
        f = brief.lint('poster with the words "SALE" and no text')
        self.assertTrue(any("no text" in x["text"] for x in f if x["type"] == "conflict"))

    def test_plan_respects_tier_capabilities(self):
        draft = brief.plan("draft", "photo", {"display": "hero"})
        self.assertEqual(draft["tier"], "lite")
        self.assertIn("not supported", draft.get("note", ""))     # lite = 1K only
        final = brief.plan("final", "hero", {"display": "hero"})
        self.assertEqual(final["size"], "2K")
        self.assertNotIn("gemini-3-pro-image", final["models"])  # final ≠ most expensive model

    def test_cli_exit_codes(self):
        self.assertEqual(run(brief, ["--lint", "a calm studio photo of a mug"])[0], 0)
        self.assertEqual(run(brief, ["--lint", "minimal and highly detailed"])[0], 1)
        code, out, _ = run(brief, ["--task", "icon", "--subject", "cloud sync symbol", "--mode", "standard"])
        self.assertEqual(code, 0)
        res = json.loads(out)
        self.assertGreaterEqual(res["plan"]["variants"], 2)
        self.assertIn("recognisable at 16, 24 and 32 px (qa.py icon)", res["qa_checklist"])


class TestCapabilities(unittest.TestCase):
    def test_unsupported_params_are_not_sent(self):
        a, s, notes = generate_image.supported("gemini-3.1-flash-lite-image", "21:9", "2K")
        self.assertEqual((a, s), ("21:9", None))
        self.assertTrue(notes)
        a, s, notes = generate_image.supported("gemini-2.5-flash-image", "8:1", "1K")
        self.assertEqual((a, s), (None, None))
        body = generate_image.build_body("x", None, None, None, "m")
        self.assertNotIn("imageConfig", body["generationConfig"])
        for k in ("seed", "negativePrompt", "steps", "quality"):
            self.assertNotIn(k, json.dumps(body))

    def test_size_mismatch_is_reported(self):
        tmp = tempfile.mkdtemp()
        try:
            buf = io.BytesIO()
            scene(1024, 576).save(buf, "PNG")
            body = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                    "data": base64.b64encode(buf.getvalue()).decode()}}]}}]}

            class R(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass
            sent = []

            def fake(req, timeout=0):
                sent.append(json.loads(req.data))
                return R(json.dumps(body).encode())
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaFAKEFAKEFAKEFAKEFAKE12345"}), \
                    mock.patch("urllib.request.urlopen", side_effect=fake):
                code, out, _ = run(generate_image, ["x", "--size", "2K", "--aspect", "16:9", "--workdir", tmp])
            self.assertEqual(code, 0)
            self.assertEqual(sent[0]["generationConfig"]["imageConfig"]["imageSize"], "2K")
            self.assertIn("requested 2K but file is 1024x576", json.dumps(json.loads(out)))
        finally:
            shutil.rmtree(tmp)

    def test_mode_budget_blocks_extra_generations(self):
        tmp = tempfile.mkdtemp()
        try:
            m = ik.Manifest(tmp)
            for i in range(2):
                m.add(source_type="api", role="original")
            m.save()
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaFAKEFAKEFAKEFAKEFAKE12345"}):
                code, _, err = run(generate_image, ["x", "--mode", "draft", "--workdir", tmp])
            self.assertNotEqual(code, 0)
            self.assertIn("Budget", err)
        finally:
            shutil.rmtree(tmp)


class TestQA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.img = os.path.join(self.tmp, "s.png")
        scene().save(self.img)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_detail_crops_are_never_downscaled(self):
        out = os.path.join(self.tmp, "d.png")
        self.assertEqual(run(qa, ["detail", self.img, "--grid", "2x2", "--tile", "400", "--out", out])[0], 0)
        with Image.open(out) as im:
            self.assertGreaterEqual(im.width, 800)          # two 400px tiles at >= 100%

    def test_icon_sheet_and_crops_sheet(self):
        icon = os.path.join(self.tmp, "i.png")
        im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((64, 64, 448, 448), fill=(30, 110, 220, 255))
        im.save(icon)
        self.assertEqual(run(qa, ["icon", icon, "--brand", "#0B1F3A", "--out", os.path.join(self.tmp, "ic.png")])[0], 0)
        self.assertEqual(run(qa, ["crops", self.img, "--out", os.path.join(self.tmp, "cr.png")])[0], 0)

    def test_compare_flags_oversharpening(self):
        sharp = scene().filter(ImageFilter.UnsharpMask(radius=3, percent=400, threshold=0))
        noisy = sharp.point(lambda v: v)
        import random
        px = noisy.load()
        random.seed(1)
        for _ in range(40000):
            x, y = random.randrange(800), random.randrange(450)
            r, g, b = px[x, y]
            n = random.randint(-40, 40)
            px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
        b = os.path.join(self.tmp, "b.png")
        noisy.save(b)
        code, out, _ = run(qa, ["compare", self.img, b, "--out", os.path.join(self.tmp, "c.png")])
        self.assertIn("oversharpening", out)
        soft = os.path.join(self.tmp, "soft.png")
        scene().filter(ImageFilter.GaussianBlur(4)).save(soft)
        code, out, _ = run(qa, ["compare", self.img, soft, "--out", os.path.join(self.tmp, "c2.png")])
        self.assertIn("much softer", out)

    def test_record_requires_explicit_visual_flag(self):
        w = os.path.join(self.tmp, "w")
        item = fetch_image.ingest_bytes(open(self.img, "rb").read(), w, "s", "local", "s")[1]
        _, out, _ = run(qa, ["record", w, item["id"], "--task", "photo", "--pass", "perspective"])
        self.assertEqual(json.loads(out)["qa_status"], "not_visually_reviewed")
        _, out, _ = run(qa, ["record", w, item["id"], "--task", "photo", "--visual", "--fail", "hand has 6 fingers"])
        self.assertEqual(json.loads(out)["qa_status"], "issues_found")
        _, out, _ = run(qa, ["record", w, item["id"], "--task", "photo", "--visual", "--pass", "all"])
        self.assertEqual(json.loads(out)["qa_status"], "visually_reviewed_ok")

    def test_budget(self):
        w = os.path.join(self.tmp, "w")
        m = ik.Manifest(w)
        for _ in range(2):
            m.add(source_type="screenshot")
        m.save()
        self.assertEqual(run(qa, ["budget", w, "--mode", "draft"])[0], 1)
        self.assertEqual(run(qa, ["budget", w, "--mode", "final"])[0], 0)

    def test_upscale_is_labelled_and_bounded(self):
        w = os.path.join(self.tmp, "w")
        item = fetch_image.ingest_bytes(open(self.img, "rb").read(), w, "s", "local", "s")[1]
        src = os.path.join(w, item["file"])
        out = os.path.join(w, "edits", "s@2x.png")
        code, _, err = run(postprocess, ["upscale", src, "--scale", "2", "--out", out])
        self.assertEqual(code, 0, err)
        self.assertIn("not a higher-resolution original", err)
        self.assertEqual(ik.Manifest(w).data["items"][0]["derived"][0]["kind"], "upscaled")
        self.assertNotEqual(run(postprocess, ["upscale", src, "--scale", "8", "--out", out])[0], 0)


if __name__ == "__main__":
    unittest.main()
