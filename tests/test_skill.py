"""Offline tests for the gemini-image skill.

Run:  python -m unittest discover -s tests -v      (from the skill folder)
Everything here is LOCAL: a fixture HTTP server on 127.0.0.1 and mocked Gemini
API responses. Nothing contacts Google. Real-service checks are listed in
tests/MANUAL_CHECKS.md and must be run by hand.
"""
import base64
import contextlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import imagekit as ik          # noqa: E402
import check_image             # noqa: E402
import deliver                 # noqa: E402
import fetch_image             # noqa: E402
import generate_image          # noqa: E402

from PIL import Image          # noqa: E402

FAKE_KEY = "AIzaSyFAKE_TEST_KEY_1234567890abcdef"


def png_bytes(w=64, h=64, color=(200, 30, 30), mode="RGB", transparent=False):
    im = Image.new(mode, (w, h), color + ((0 if transparent else 255,) if mode == "RGBA" else ()))
    if mode == "RGBA" and transparent:
        im.paste((0, 128, 255, 255), (w // 4, h // 4, 3 * w // 4, 3 * h // 4))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


IMG_A = png_bytes(320, 180, (10, 120, 200))
IMG_B = png_bytes(320, 180, (240, 200, 40))
HTML = b"<!DOCTYPE html><html><body>Sign in</body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    routes = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        r = self.routes.get(self.path.split("?")[0])
        if r is None:
            self.send_response(404)
            self.end_headers()
            return
        status, headers, body, delay = r
        if delay:
            time.sleep(delay)
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v.replace("PORT", str(self.server.server_port)))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


class Server:
    def __enter__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"


Handler.routes = {
    "/ok.png": (200, {"Content-Type": "image/png"}, IMG_A, 0),
    "/b.png": (200, {"Content-Type": "image/png"}, IMG_B, 0),
    "/redir": (302, {"Location": "/ok.png"}, b"", 0),
    "/fake.png": (200, {"Content-Type": "image/png"}, HTML, 0),
    "/json.png": (200, {"Content-Type": "image/png"}, b'{"error":{"code":403}}', 0),
    "/login": (302, {"Location": "https://accounts.google.com/ServiceLogin?continue=x"}, b"", 0),
    "/private": (401, {}, b"", 0),
    "/expired.png": (403, {}, b"", 0),
    "/gone.png": (410, {}, b"", 0),
    "/to-metadata": (302, {"Location": "http://169.254.169.254/latest/meta-data/"}, b"", 0),
    "/to-lan": (302, {"Location": "http://10.0.0.5/x.png"}, b"", 0),
    "/loop": (302, {"Location": "/loop"}, b"", 0),
    "/slow.png": (200, {"Content-Type": "image/png"}, IMG_A, 3),
    "/big.png": (200, {"Content-Type": "image/png"}, IMG_A + b"\0" * 5000, 0),
    "/empty.png": (200, {"Content-Type": "image/png"}, b"", 0),
    "/broken.png": (200, {"Content-Type": "image/png"}, IMG_A[:200], 0),
    "/dl?sig=SECRET123&token=abc": (200, {}, IMG_A, 0),
}


def run_cli(module, args):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = module.main(args) or 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if not isinstance(e.code, int) and e.code:
                err.write(str(e.code))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def fetch(self, srv, path, *extra):
        return run_cli(fetch_image, [srv.url(path), "--workdir", self.tmp, "--allow-http",
                                     "--allow-host", f"127.0.0.1:{srv.port}", *extra])


# ── validation ──────────────────────────────────────────────────────────────
class TestValidation(Base):
    def test_01_valid_image_passes(self):
        r = ik.validate(self.write("a.png", IMG_A), expect_ratio="16:9", min_width=320)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual((r.kind, r.width, r.height), ("png", 320, 180))
        self.assertEqual(len(r.sha256), 64)

    def test_02_html_and_json_saved_as_png_rejected(self):
        for data, word in ((HTML, "HTML"), (b'{"error": "quota"}', "JSON")):
            r = ik.validate(self.write("x.png", data))
            self.assertFalse(r.ok)
            self.assertIn(word, " ".join(r.errors))

    def test_03_empty_and_corrupt_detected(self):
        self.assertIn("empty", ik.validate(self.write("e.png", b"")).errors[0])
        r = ik.validate(self.write("c.png", IMG_A[:150]))
        self.assertFalse(r.ok)
        self.assertIn("cannot be decoded", " ".join(r.errors))

    def test_04_wrong_ratio_and_too_small(self):
        r = ik.validate(self.write("a.png", IMG_A), expect_ratio="4:5", min_width=1080, min_height=1350)
        msg = " ".join(r.errors)
        self.assertIn("aspect ratio", msg)
        self.assertIn("too small", msg)

    def test_05_transparency_requirement(self):
        opaque_rgba = self.write("o.png", png_bytes(64, 64, (1, 2, 3), "RGBA"))
        transp = self.write("t.png", png_bytes(64, 64, (1, 2, 3), "RGBA", transparent=True))
        rgb = self.write("r.png", png_bytes())
        self.assertIn("fully opaque", " ".join(ik.validate(opaque_rgba, require_alpha=True).errors))
        self.assertIn("no alpha", " ".join(ik.validate(rgb, require_alpha=True).errors))
        self.assertTrue(ik.validate(transp, require_alpha=True).ok)

    def test_extension_mismatch_warned(self):
        r = ik.validate(self.write("photo.jpg", IMG_A))
        self.assertTrue(r.ok)
        self.assertTrue(any("does not match" in w for w in r.warnings))

    def test_check_image_cli_exit_codes(self):
        good, bad = self.write("g.png", IMG_A), self.write("b.png", HTML)
        self.assertEqual(run_cli(check_image, [good])[0], 0)
        self.assertEqual(run_cli(check_image, [good, bad])[0], 1)
        code, out, _ = run_cli(check_image, [good, "--sha256", "0" * 64])
        self.assertEqual(code, 1)
        self.assertIn("changed", out)

    def test_visual_match(self):
        a, b = self.write("a.png", IMG_A), self.write("b.png", IMG_B)
        big = Image.open(a).resize((1280, 720))
        bigp = os.path.join(self.tmp, "big.png")
        big.save(bigp)
        self.assertLessEqual(ik.hamming(ik.ahash(a), ik.ahash(bigp)), 10)


# ── fetching ────────────────────────────────────────────────────────────────
class TestFetch(Base):
    def test_direct_url(self):
        with Server() as s:
            code, out, _ = self.fetch(s, "/ok.png")
        self.assertEqual(code, 0, out)
        res = json.loads(out)
        with open(res["file"], "rb") as f:
            self.assertEqual(f.read(), IMG_A)          # bytes untouched

    def test_redirect_to_image(self):
        with Server() as s:
            code, out, _ = self.fetch(s, "/redir", "--name", "r")
        self.assertEqual(code, 0, out)
        m = ik.Manifest(self.tmp)
        self.assertEqual(m.data["items"][0]["redirects"], 1)

    def test_png_url_returning_html_rejected(self):
        with Server() as s:
            code, out, _ = self.fetch(s, "/fake.png")
            code2, _, _ = self.fetch(s, "/json.png")
        self.assertEqual((code, code2), (3, 3))
        self.assertEqual(json.loads(out)["status"], "invalid")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "originals", "fake.png")))

    def test_login_required(self):
        with Server() as s:
            for path in ("/login", "/private"):
                code, out, _ = self.fetch(s, path)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out)["error"], "auth_required")

    def test_expired_link(self):
        with Server() as s:
            self.assertEqual(json.loads(self.fetch(s, "/expired.png")[1])["error"], "forbidden_or_expired")
            self.assertEqual(json.loads(self.fetch(s, "/gone.png")[1])["error"], "expired")

    def test_empty_and_broken_download(self):
        with Server() as s:
            self.assertEqual(self.fetch(s, "/empty.png")[0], 3)
            code, out, _ = self.fetch(s, "/broken.png")
        self.assertEqual(code, 3)
        self.assertIn("decoded", json.dumps(json.loads(out)))

    def test_redirect_to_internal_network_blocked(self):
        with Server() as s:
            for path in ("/to-metadata", "/to-lan"):
                code, out, _ = self.fetch(s, path)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out)["error"], "blocked_address")
        with self.assertRaises(ik.FetchError):
            ik.check_url("https://127.0.0.1/x.png")
        with self.assertRaises(ik.FetchError) as cm:
            ik.check_url("http://example.com/x.png")
        self.assertEqual(cm.exception.code, "bad_scheme")

    def test_limits_redirects_size_timeout(self):
        with Server() as s:
            self.assertEqual(json.loads(self.fetch(s, "/loop")[1])["error"], "too_many_redirects")
            self.assertEqual(json.loads(self.fetch(s, "/big.png", "--max-bytes", "1000")[1])["error"], "too_large")
            self.assertEqual(json.loads(self.fetch(s, "/slow.png", "--timeout", "1")[1])["error"], "timeout")

    def test_duplicate_and_name_collision(self):
        with Server() as s:
            self.assertEqual(self.fetch(s, "/ok.png", "--name", "pic")[0], 0)
            self.assertEqual(self.fetch(s, "/redir", "--name", "pic")[0], 4)       # same bytes → dedup
            self.assertEqual(self.fetch(s, "/b.png", "--name", "pic")[0], 0)       # new bytes, same name
        names = sorted(os.listdir(os.path.join(self.tmp, "originals")))
        self.assertEqual(names, ["pic-2.png", "pic.png"])

    def test_secrets_not_in_manifest_or_output(self):
        with Server() as s:
            code, out, err = self.fetch(s, "/dl?sig=SECRET123&token=abc")
        with open(os.path.join(self.tmp, "manifest.json")) as f:
            manifest = f.read()
        for text in (out, err, manifest):
            self.assertNotIn("SECRET123", text)
            self.assertNotIn("token=abc", text)

    def test_download_failure_never_generates(self):
        with mock.patch.object(generate_image, "call") as gen:
            with Server() as s:
                code, _, _ = self.fetch(s, "/expired.png")
        self.assertEqual(code, 2)
        gen.assert_not_called()
        self.assertNotIn("generate_image", open(os.path.join(SCRIPTS, "fetch_image.py")).read())
        items = ik.Manifest(self.tmp).data["items"]
        self.assertTrue(all(i.get("source_type") != "api" for i in items))

    def test_local_attachment(self):
        p = self.write("upload.png", IMG_B)
        code, out, _ = run_cli(fetch_image, [p, "--workdir", os.path.join(self.tmp, "w"), "--origin", "attachment"])
        self.assertEqual(code, 0, out)
        self.assertEqual(ik.Manifest(os.path.join(self.tmp, "w")).data["items"][0]["source_type"], "attachment")


# ── delivery ────────────────────────────────────────────────────────────────
class TestDeliver(Base):
    def ingest(self, data, name):
        return fetch_image.ingest_bytes(data, self.tmp, name, "local", name)[1]

    def test_hash_of_delivered_original_matches(self):
        self.ingest(IMG_A, "a")
        self.ingest(IMG_B, "b")
        code, out, _ = run_cli(deliver, ["--workdir", self.tmp, "--zip"])
        self.assertEqual(code, 0, out)
        res = json.loads(out)
        for it in res["items"]:
            orig = next(f["path"] for f in it["files"] if f["role"].startswith("original"))
            self.assertEqual(ik.sha256_file(orig), it["sha256"])
        self.assertTrue(res["zip"]["verified"])
        with zipfile.ZipFile(res["zip"]["path"]) as z:
            self.assertEqual(z.read("a.png"), IMG_A)

    def test_tampered_original_not_called_original(self):
        item = self.ingest(IMG_A, "a")
        with open(os.path.join(self.tmp, item["file"]), "ab") as f:
            f.write(b"x")
        code, out, _ = run_cli(deliver, ["--workdir", self.tmp])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["items"][0]["status"], "integrity_failed")

    def test_preview_differs_from_original_is_labelled(self):
        big = io.BytesIO()
        Image.new("RGB", (5000, 2000), (9, 9, 9)).save(big, "PNG")
        self.ingest(big.getvalue(), "huge")
        res = json.loads(run_cli(deliver, ["--workdir", self.tmp])[1])["items"][0]
        roles = [f["role"] for f in res["files"]]
        self.assertTrue(roles[0].startswith("preview"))
        self.assertEqual(roles[1], "original")
        self.assertFalse(res["preview_is_original"])
        self.assertIn("reduced preview", res["say"])

    def test_small_original_shown_directly(self):
        self.ingest(IMG_A, "a")
        res = json.loads(run_cli(deliver, ["--workdir", self.tmp])[1])["items"][0]
        self.assertTrue(res["preview_is_original"])
        self.assertEqual(res["files"][0]["display"], "render")

    def test_environment_without_inline_images(self):
        self.ingest(IMG_A, "a")
        res = json.loads(run_cli(deliver, ["--workdir", self.tmp, "--env", "claude-code"])[1])["items"][0]
        self.assertEqual(res["files"][0]["display"], "path")
        m = ik.Manifest(self.tmp)
        self.assertEqual(m.data["items"][0]["stages"]["preview_ready"], "skipped")
        self.assertEqual(m.data["items"][0]["stages"]["ui_verified"], "unknown")

    def test_resume_skips_delivered(self):
        self.ingest(IMG_A, "a")
        run_cli(deliver, ["--workdir", self.tmp])
        self.ingest(IMG_B, "b")
        res = json.loads(run_cli(deliver, ["--workdir", self.tmp])[1])
        self.assertEqual([i["name"] for i in res["items"]], ["b"])

    def test_screenshot_never_called_original(self):
        fetch_image.ingest_bytes(IMG_A, self.tmp, "shot", "screenshot", "shot")
        res = json.loads(run_cli(deliver, ["--workdir", self.tmp])[1])["items"][0]
        self.assertIn("SCREENSHOT", res["say"])
        self.assertNotIn("original", " ".join(f["role"] for f in res["files"]))

    def test_mark_stage(self):
        item = self.ingest(IMG_A, "a")
        run_cli(deliver, ["--workdir", self.tmp, "--mark", item["id"], "attached"])
        self.assertEqual(ik.Manifest(self.tmp).find_id(item["id"])["stages"]["attached"], "done")


# ── generation (mocked API) ─────────────────────────────────────────────────
def api_response(img=IMG_A, text="ok"):
    body = {"responseId": "resp-1", "candidates": [{"content": {"parts": [
        {"text": text}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(img).decode()}}]}}]}
    return io.BytesIO(json.dumps(body).encode())


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class TestGenerate(Base):
    def gen(self, *args):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": FAKE_KEY}), mock.patch("time.sleep"):
            return run_cli(generate_image, ["a fox", "--workdir", self.tmp, *args])

    def test_saves_verified_original_with_manifest(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(api_response().getvalue())):
            code, out, _ = self.gen("--aspect", "16:9", "--name", "fox")
        self.assertEqual(code, 0, out)
        item = ik.Manifest(self.tmp).data["items"][0]
        self.assertEqual((item["source_type"], item["status"], item["response_id"]), ("api", "verified", "resp-1"))
        with open(os.path.join(self.tmp, item["file"]), "rb") as f:
            self.assertEqual(f.read(), IMG_A)

    def test_timeout_is_not_resent(self):
        calls = []

        def boom(*a, **k):
            calls.append(1)
            raise urllib.error.URLError("timed out")
        with mock.patch("urllib.request.urlopen", side_effect=boom):
            code, out, _ = self.gen()
        self.assertEqual(code, 5)
        self.assertEqual(len(calls), 1)
        self.assertIn("NOT re-sent", out)

    def test_rate_limit_retries_are_bounded(self):
        calls = []

        def limited(*a, **k):
            calls.append(1)
            raise urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(b"quota"))
        with mock.patch("urllib.request.urlopen", side_effect=limited):
            code, _, err = self.gen("--retries", "2")
        self.assertNotEqual(code, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn("429", err)

    def test_no_hidden_switch_to_another_tier(self):
        urls = []

        def notfound(req, timeout=0):
            urls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"no model"))
        with mock.patch("urllib.request.urlopen", side_effect=notfound):
            code, _, _ = self.gen("--quality", "pro")
        self.assertNotEqual(code, 0)
        self.assertTrue(all("pro-image" in u for u in urls), urls)

    def test_variant_cap(self):
        code, _, err = self.gen("-n", "50")
        self.assertNotEqual(code, 0)
        self.assertIn("GEMINI_IMAGE_MAX_PER_RUN", err)

    def test_api_key_never_logged(self):
        def echo(*a, **k):
            raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(f"invalid key {FAKE_KEY}".encode()))
        with mock.patch("urllib.request.urlopen", side_effect=echo):
            code, out, err = self.gen()
        self.assertNotIn(FAKE_KEY, out + err)
        self.assertIn("REDACTED", err)
        manifest = os.path.join(self.tmp, "manifest.json")
        if os.path.exists(manifest):
            with open(manifest) as f:
                self.assertNotIn(FAKE_KEY, f.read())

    def test_no_overwrite_of_existing_output(self):
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: _Resp(api_response().getvalue())):
            self.gen("--name", "same")
        with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: _Resp(api_response(IMG_B).getvalue())):
            self.gen("--name", "same")
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "originals"))), ["same-2.png", "same.png"])


# ── post-processing ─────────────────────────────────────────────────────────
class TestPostprocess(Base):
    def setUp(self):
        super().setUp()
        self.item = fetch_image.ingest_bytes(png_bytes(1200, 800, (240, 240, 240)), self.tmp, "base", "local", "x")[1]
        self.orig = os.path.join(self.tmp, self.item["file"])
        self.sha = ik.sha256_file(self.orig)

    def pp(self, *args):
        import postprocess
        return run_cli(postprocess, list(args))

    def test_derived_files_recorded_and_original_untouched(self):
        out = os.path.join(self.tmp, "edits", "ig.png")
        self.assertEqual(self.pp("resize", self.orig, "--preset", "ig-post", "--out", out)[0], 0)
        self.assertEqual(ik.sha256_file(self.orig), self.sha)
        d = ik.Manifest(self.tmp).data["items"][0]["derived"]
        self.assertEqual(d[0]["kind"], "upscaled")      # 800px tall → 1350 is an upscale

    def test_refuses_overwrite_of_input_and_originals(self):
        code, _, err = self.pp("convert", self.orig, "--out", self.orig)
        self.assertNotEqual(code, 0)
        code, _, err = self.pp("convert", self.orig, "--out", os.path.join(self.tmp, "originals", "x.webp"))
        self.assertNotEqual(code, 0)
        self.assertEqual(ik.sha256_file(self.orig), self.sha)

    def test_no_stretch_extend_mode(self):
        out = os.path.join(self.tmp, "edits", "banner.png")
        self.assertEqual(self.pp("resize", self.orig, "--size", "1920x600", "--mode", "extend", "--out", out)[0], 0)
        with Image.open(out) as im:
            self.assertEqual(im.size, (1920, 600))

    def test_uzbek_text_glyphs_and_contrast(self):
        out = os.path.join(self.tmp, "edits", "t.png")
        code, _, err = self.pp("text", self.orig, "--text", "Oʻzbekiston gʻalaba", "--sub", "Ўзбекистон",
                               "--color", "#F0F0F0", "--out", out)
        self.assertEqual(code, 0, err)
        self.assertIn("low contrast", err)                 # light text on light image
        code, _, err = self.pp("text", self.orig, "--text", "漢字", "--out", out)
        self.assertNotEqual(code, 0)
        self.assertIn("lacks glyphs", err)

    def test_cutout_produces_real_transparency(self):
        im = Image.new("RGB", (300, 300), (0, 255, 0))
        from PIL import ImageDraw
        ImageDraw.Draw(im).ellipse((100, 100, 199, 199), fill=(200, 40, 40))   # round object
        src = os.path.join(self.tmp, "edits", "green.png")
        os.makedirs(os.path.dirname(src))
        im.save(src)
        out = os.path.join(self.tmp, "edits", "asset.png")
        code, _, err = self.pp("cutout", src, "--trim", "--out", out)
        self.assertEqual(code, 0, err)
        r = ik.validate(out, require_alpha=True)
        self.assertTrue(r.ok, r.errors)
        self.assertTrue(r.has_transparent_pixels)          # corners around the circle
        self.assertLessEqual(abs(r.width - 100), 2)        # trimmed to the object

    def test_long_text_shrinks_inside_margins(self):
        out = os.path.join(self.tmp, "edits", "long.png")
        code, _, err = self.pp("text", self.orig, "--text", "Juda uzun sarlavha " * 6, "--size", "300", "--out", out)
        self.assertEqual(code, 0, err)
        self.assertIn("shrunk", err)


# ── browser snippet selection logic (node) ──────────────────────────────────
@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestBrowserSnippets(unittest.TestCase):
    def test_share_page_with_many_images_picks_generated_only(self):
        src = open(os.path.join(SCRIPTS, "browser_snippets.js")).read()
        snippet = src.split("// ── 1.")[1].split("// ── 2.")[0]
        body = "\n".join(l for l in snippet.splitlines()[1:] if not l.strip().startswith("//")).strip()
        js = f"""
const mk=(src,w,h)=>({{src,currentSrc:src,naturalWidth:w,naturalHeight:h,complete:true,alt:'',
  getBoundingClientRect:()=>({{left:0,top:0,right:w,bottom:h,width:w,height:h}})}});
globalThis.location={{pathname:'/share/abc123'}};
globalThis.document={{images:[
  mk('https://lh3.googleusercontent.com/a/ACg8oc=s64-c',64,64),
  mk('https://lh3.googleusercontent.com/ogw/AF2=s512-c-mo',512,512),
  mk('https://lh3.googleusercontent.com/gg/AAA',1024,1024),
  mk('https://www.gstatic.com/logo.png',120,40),
  mk('blob:https://gemini.google.com/x',1536,1024)]}};
console.log(JSON.stringify({body.rstrip(';')}));"""
        res = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(res["count"], 2)
        self.assertEqual([(i["n"], i["w"]) for i in res["images"]], [(1, 1024), (2, 1536)])


if __name__ == "__main__":
    unittest.main()
