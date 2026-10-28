"""Offline tests for project-integration mode (fixture projects, no npm needed).

Run: python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import asset_export      # noqa: E402
import asset_registry    # noqa: E402
import imagekit as ik    # noqa: E402
import project_scan      # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


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


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def img(path, size=(1600, 900), color=(30, 90, 200), alpha=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if alpha:
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((size[0] // 4, size[1] // 4, 3 * size[0] // 4, 3 * size[1] // 4), fill=color + (255,))
    else:
        im = Image.new("RGB", size, color)
    im.save(path)
    return path


HERO_TSX = """import './Hero.css'
export default function Hero() {
  return (
    <section className="hero">
      <h1>Welcome</h1>
      <img src="https://placehold.co/1600x900" className="hero-img aspect-[16/9] w-full object-cover" loading="lazy" />
    </section>
  )
}
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def vite_react(self):
        write(self.root, "package.json", json.dumps({"scripts": {"dev": "vite", "build": "tsc -b && vite build", "lint": "eslint ."},
                                                     "dependencies": {"react": "^19", "lucide-react": "^0.5"},
                                                     "devDependencies": {"vite": "^7", "typescript": "^5"}}))
        write(self.root, "tsconfig.json", "{}")
        write(self.root, "AGENTS.md", "# Rules\nImages go in src/assets/images and are imported.\n")
        write(self.root, "src/components/Hero.tsx", HERO_TSX)
        write(self.root, "src/index.css", ":root{--brand:#0B1F3A;--accent:#FFB020}\n@media (prefers-color-scheme: dark){:root{--brand:#fff}}")
        img(os.path.join(self.root, "src/assets/images/logo.png"), (128, 128))
        img(os.path.join(self.root, "src/assets/images/team.jpg"), (400, 300))
        os.makedirs(os.path.join(self.root, "node_modules/x"))
        img(os.path.join(self.root, "node_modules/x/ignored.png"), (10, 10))


class TestScan(Fixture):
    def test_vite_react_context(self):
        self.vite_react()
        s = project_scan.scan(self.root)
        self.assertEqual(s["framework"]["name"], "React (Vite/CRA)")
        self.assertEqual(s["framework"]["bundler"], "vite")
        self.assertIn("AGENTS.md", s["read_first"])
        self.assertEqual(s["suggested_dest"], "src/assets/images")      # existing images win
        self.assertFalse(s["dest_is_public"])
        self.assertIn("lucide-react", s["icon_libraries"])
        self.assertEqual(s["design_tokens"].get("--brand"), "#0B1F3A")
        self.assertTrue(s["dark_mode_signals"])
        self.assertEqual(s["check_commands"]["build"], "npm run build")
        self.assertEqual(s["check_commands"]["typecheck"], "npx tsc --noEmit")
        self.assertTrue(any("placehold.co" in p["text"] for p in s["placeholders"]))
        self.assertNotIn("node_modules/x", s["image_dirs"])

    def test_frameworks_and_no_public_images_assumption(self):
        cases = {"next": ("Next.js", "public/images"), "@sveltejs/kit": ("SvelteKit", "static/images"),
                 "astro": ("Astro", "src/assets/images")}
        for dep, (name, dest) in cases.items():
            root = tempfile.mkdtemp()
            try:
                write(root, "package.json", json.dumps({"dependencies": {dep: "1"}}))
                s = project_scan.scan(root)
                self.assertEqual((s["framework"]["name"], s["suggested_dest"]), (name, dest))
            finally:
                shutil.rmtree(root)
        root = tempfile.mkdtemp()
        try:
            write(root, "index.html", "<img src='img/a.png'>")
            img(os.path.join(root, "img/a.png"), (50, 50))
            s = project_scan.scan(root)
            self.assertEqual((s["framework"]["name"], s["suggested_dest"]), ("Static HTML", "img"))
        finally:
            shutil.rmtree(root)

    def test_component_slots(self):
        self.vite_react()
        c = project_scan.inspect_component(self.root, "src/components/Hero.tsx")
        slot = c["image_slots"][0]
        self.assertEqual(slot["ratio_hint"], "16:9")
        self.assertTrue(slot["is_placeholder"])
        self.assertEqual(slot["loading"], "lazy")
        self.assertIn("missing alt", " ".join(slot["issues"]))
        self.assertTrue(c["likely_above_the_fold"])
        self.assertIn("do NOT lazy-load", c["advice"])


class TestExport(Fixture):
    def setUp(self):
        super().setUp()
        self.vite_react()
        self.src = img(os.path.join(self.root, "..", os.path.basename(self.root) + "-gen.png"), (1600, 1000))

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.src):
            os.remove(self.src)

    def export(self, *extra):
        return run(asset_export, [self.src, "--project", self.root, "--id", "hero-landing",
                                  "--purpose", "Landing hero", "--ratio", "16:9", *extra])

    def test_original_kept_outside_public_and_production_separate(self):
        code, out, err = self.export("--widths", "640,1280,1920", "--usage", "src/components/Hero.tsx")
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertTrue(r["original"].startswith(".gemini-assets/originals/"))
        self.assertEqual(ik.sha256_file(os.path.join(self.root, r["original"])), ik.sha256_file(self.src))
        widths = [p["width"] for p in r["production"]]
        self.assertEqual(widths, [640, 1280, 1600])            # 1920 capped: no upscaling
        self.assertTrue(all(p["path"].startswith("src/assets/images/hero-landing-") for p in r["production"]))
        self.assertTrue(all(abs(p["width"] / p["height"] - 16 / 9) < 0.01 for p in r["production"]))
        self.assertIn("capped", " ".join(r["notes"]))
        self.assertIsNone(r["use_in_code"]["public_url"])       # src/assets → import, not URL

    def test_refuses_fake_svg_and_jpg_with_alpha(self):
        self.assertNotEqual(self.export("--format", "svg")[0], 0)
        a = img(os.path.join(self.root, "a.png"), (300, 300), alpha=True)
        code, _, err = run(asset_export, [a, "--project", self.root, "--id", "icon", "--format", "jpg", "--alpha"])
        self.assertNotEqual(code, 0)
        self.assertIn("transparency", err)

    def test_alpha_required_rejects_opaque_and_checkerboard(self):
        cb = Image.new("RGB", (200, 200), (255, 255, 255))
        d = ImageDraw.Draw(cb)
        for y in range(0, 200, 20):
            for x in range(0, 200, 20):
                if (x + y) // 20 % 2:
                    d.rectangle((x, y, x + 19, y + 19), fill=(204, 204, 204))
        p = os.path.join(self.root, "checker.png")
        cb.save(p)
        code, _, err = run(asset_export, [p, "--project", self.root, "--id", "icon", "--alpha"])
        self.assertNotEqual(code, 0)
        self.assertIn("checkerboard", err)
        a = img(os.path.join(self.root, "ok.png"), (300, 300), alpha=True)
        code, out, err = run(asset_export, [a, "--project", self.root, "--id", "icon", "--alpha", "--dest", "public/icons"])
        self.assertEqual(code, 0, err)
        r = json.loads(out)
        self.assertEqual(r["use_in_code"]["public_url"], "/icons/icon.webp")
        self.assertTrue(ik.validate(os.path.join(self.root, r["production"][0]["path"]), require_alpha=True).ok)

    def test_no_overwrite_of_user_files_but_stable_replace(self):
        img(os.path.join(self.root, "src/assets/images/hero-landing.webp"), (10, 10))   # user's own file
        code, _, err = self.export()
        self.assertNotEqual(code, 0)
        self.assertIn("not created by this skill", err)
        os.remove(os.path.join(self.root, "src/assets/images/hero-landing.webp"))
        self.assertEqual(self.export()[0], 0)
        self.assertNotEqual(self.export()[0], 0)                  # second time needs --replace
        code, out, err = self.export("--replace")
        self.assertEqual(code, 0, err)
        reg = asset_registry.Registry(self.root)
        self.assertEqual(len(reg.data["assets"]), 1)
        self.assertEqual(len(reg.get("hero-landing")["history"]), 1)
        self.assertEqual(reg.get("hero-landing")["production"][0]["path"], "src/assets/images/hero-landing.webp")


class TestRegistryVerify(Fixture):
    def setUp(self):
        super().setUp()
        self.vite_react()
        src = img(os.path.join(self.root, "gen.png"), (1600, 900))
        code, out, err = run(asset_export, [src, "--project", self.root, "--id", "hero-landing",
                                            "--purpose", "Landing hero illustration", "--brief", "navy 3D hero"])
        assert code == 0, err

    def verify(self, *extra):
        code, out, _ = run(asset_registry, ["verify", self.root, *extra])
        return code, json.loads(out)

    def test_unused_asset_warns_then_wired_passes(self):
        code, r = self.verify()
        self.assertEqual(code, 0)
        self.assertIn("not referenced", " ".join(r["warnings"]))
        self.assertEqual(self.verify("--strict")[0], 1)
        write(self.root, "src/components/Hero.tsx",
              "import hero from '../assets/images/hero-landing.webp'\nexport default () => <img src={hero} alt='Team at work' width={1600} height={900} />\n")
        code, r = self.verify("--strict")
        self.assertEqual(code, 0, r)
        self.assertEqual(r["assets"][0]["used_in"], ["src/components/Hero.tsx"])

    def test_find_existing_before_generating(self):
        _, out, _ = run(asset_registry, ["find", self.root, "hero illustration for landing"])
        self.assertEqual(json.loads(out)["matches"][0]["id"], "hero-landing")
        _, out, _ = run(asset_registry, ["find", self.root, "pricing table icon"])
        self.assertEqual(json.loads(out)["matches"], [])

    def test_detects_gemini_url_fake_svg_and_missing_file(self):
        write(self.root, "src/Bad.tsx", "<img src='https://lh3.googleusercontent.com/gg/AAAA' alt='' />")
        shutil.copy(os.path.join(self.root, "gen.png"), os.path.join(self.root, "src/assets/images/icon.svg"))
        write(self.root, "src/assets/images/wrapped.svg",
              "<svg xmlns='http://www.w3.org/2000/svg'><image href='data:image/png;base64,iVBORw0KGgo='/></svg>")
        write(self.root, "src/assets/images/real.svg", "<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0h1'/></svg>")
        os.remove(os.path.join(self.root, "src/assets/images/hero-landing.webp"))
        code, r = self.verify()
        msg = " ".join(r["errors"])
        self.assertEqual(code, 1)
        self.assertIn("googleusercontent", msg)
        self.assertIn("icon.svg: named .svg but contains png", msg)
        self.assertIn("wrapped.svg", msg)
        self.assertNotIn("real.svg", msg)
        self.assertIn("hero-landing.webp", msg)

    def test_registry_has_no_secrets_and_tampered_original_detected(self):
        reg = asset_registry.Registry(self.root)
        text = open(reg.path).read()
        self.assertNotIn("AIza", text)
        self.assertNotIn("googleusercontent", text)
        with open(os.path.join(self.root, reg.get("hero-landing")["original"]), "ab") as f:
            f.write(b"x")
        code, r = self.verify()
        self.assertIn("hash mismatch", " ".join(r["errors"]))


def _has_chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@unittest.skipUnless(_has_chromium(), "Playwright Chromium not available")
class TestRenderCheck(unittest.TestCase):
    """Real browser render (Chromium) of a good and a bad page."""

    def test_good_page_passes_bad_page_flags_every_problem(self):
        import render_check
        root = tempfile.mkdtemp()
        try:
            src = img(os.path.join(root, "src.png"), (1600, 900))
            for w in (640, 1280, 1600):
                Image.open(src).resize((w, w * 9 // 16)).save(os.path.join(root, f"h-{w}.webp"))
            write(root, "good.html", "<!doctype html><meta name=viewport content='width=device-width'>"
                  "<img src='h-1280.webp' srcset='h-640.webp 640w, h-1280.webp 1280w, h-1600.webp 1600w' "
                  "sizes='100vw' width=1600 height=900 alt='Hero' style='width:100%;height:auto'>")
            write(root, "bad.html", "<!doctype html><img src='h-640.webp' loading=lazy style='width:600px;height:600px'>"
                  "<img src='nope.webp' alt='' width=5 height=5>")
            code, out, _ = run(render_check, [os.path.join(root, "good.html"), "--out", os.path.join(root, "s")])
            self.assertEqual(code, 0, out)
            code, out, _ = run(render_check, [os.path.join(root, "bad.html"), "--widths", "1200",
                                              "--schemes", "light", "--out", os.path.join(root, "s")])
            self.assertEqual(code, 1)
            issues = " ".join(" ".join(i["issues"]) for s_ in json.loads(out)["shots"] for i in s_["image_issues"])
            for word in ("distorted", "lazy above the fold", "no alt", "CLS", "NOT LOADED"):
                self.assertIn(word, issues)
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
