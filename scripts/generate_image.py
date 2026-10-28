#!/usr/bin/env python3
"""Generate or edit images with the Google Gemini API (standard library only).

  generate_image.py "a red fox in snow, photo" --aspect 16:9 --workdir out --name fox
  generate_image.py "same scene at sunset" --input out/originals/fox.png --workdir out --name fox-sunset
  generate_image.py "<prompt>" -n 3 --workdir out --name hero          # 3 variants (3 billed calls)
  generate_image.py "<prompt>" --quality pro --size 2K --workdir out    # pro model, explicit choice
  generate_image.py "<prompt>" --dry-run                                 # show request, send nothing

Cost & safety rules built in:
  * models only fall back WITHIN the chosen tier (fast→fast, pro→pro); it never
    switches to a different (possibly pricier) tier on its own
  * a request that timed out is NOT re-sent automatically: Google may have
    processed (and billed) it. Use --retry-timeouts to opt in.
  * 429/5xx are retried with backoff, at most --retries times (429 means not processed)
  * at most GEMINI_IMAGE_MAX_PER_RUN images per run (default 4)
  * the API key is read from GEMINI_API_KEY, sent only as a header, never printed
Returned bytes are written untouched (no re-encode), named by their real format,
never over an existing file, verified, and recorded in <workdir>/manifest.json.
"""
import argparse
import base64
import json
import mimetypes
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imagekit as ik  # noqa: E402

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_CAPS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references", "model-capabilities.json")
with open(_CAPS_PATH, encoding="utf-8") as _f:
    CAPS = json.load(_f)
MODELS = dict(CAPS["tiers"])          # fallbacks stay inside one tier
MODELS["fast"] = MODELS["standard"]   # backwards-compatible alias
ASPECTS = set(CAPS["aspect_ratios_common"]) | set(CAPS["aspect_ratios_extended"])
SIZES = {"512", "1K", "2K", "4K"}


def supported(model, aspect, size):
    """Return (aspect, size) actually sendable to this model; drop what it doesn't support."""
    info = CAPS["models"].get(model)
    notes = []
    if info is None:          # unknown/explicit model: send as asked, the API will validate
        return aspect, size, notes
    if aspect and aspect in CAPS["aspect_ratios_extended"] and not info.get("extended_aspects"):
        notes.append(f"{model} does not list aspect {aspect}; not sent")
        aspect = None
    if size and size not in info.get("sizes", []):
        notes.append(f"{model} does not support imageSize {size} (supports {info.get('sizes') or 'none'}); not sent")
        size = None
    return aspect, size, notes
RETRY_CODES = {429, 500, 502, 503, 504}
MAX_PER_RUN = ik._env_int("GEMINI_IMAGE_MAX_PER_RUN", 4)


class Timeout(Exception):
    pass


def build_body(prompt, input_paths, aspect, size, model):
    parts = [{"text": prompt}]
    for path in input_paths or []:
        with open(path, "rb") as f:
            raw = f.read()
        kind, mime = ik.sniff(raw[:2048])
        if not mime.startswith("image/"):
            sys.exit(f"Input {path} is not an image ({kind})")
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode()}})
    gen = {"responseModalities": ["TEXT", "IMAGE"]}
    image_cfg = {}
    if aspect:
        image_cfg["aspectRatio"] = aspect
    if size:
        image_cfg["imageSize"] = size
    if image_cfg:
        gen["imageConfig"] = image_cfg
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}


def call(model, body, key, timeout, retries, retry_timeouts):
    req = urllib.request.Request(
        ENDPOINT.format(model=model), data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    delay = 4
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = ik.redact(e.read().decode(errors="replace")[:600])
            if e.code == 404:
                raise LookupError(detail)
            if e.code in RETRY_CODES and attempt < retries:
                wait = int(e.headers.get("Retry-After") or delay) if str(e.headers.get("Retry-After", "")).isdigit() else delay
                print(f"  HTTP {e.code}; retry {attempt + 1}/{retries} in {wait}s", file=sys.stderr)
                time.sleep(wait)
                delay *= 2
                continue
            hint = {400: "bad request (check aspect/size/prompt)", 401: "API key invalid",
                    403: "key lacks access or billing for this model",
                    429: "rate limit / quota exhausted — wait or check quota at aistudio.google.com"}.get(e.code, "")
            sys.exit(f"Gemini API error {e.code} {hint}\n{detail}")
        except (socket.timeout, TimeoutError) as e:
            if retry_timeouts and attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise Timeout(str(e))
        except urllib.error.URLError as e:
            if "timed out" in str(e.reason):
                if retry_timeouts and attempt < retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise Timeout(str(e.reason))
            sys.exit(f"Could not reach Gemini API: {ik.redact(str(e.reason))}\n"
                     "(Sandboxes without access to Google: use the browser route.)")
    sys.exit("Gave up after retries.")


def extract(data):
    images, texts = [], []
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            blob = part.get("inline_data") or part.get("inlineData")
            if blob:
                images.append(base64.b64decode(blob["data"]))
            elif part.get("text") and not part.get("thought"):
                texts.append(part["text"].strip())
    return images, texts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt")
    ap.add_argument("--workdir", default="gemini-out")
    ap.add_argument("--name", help="base file name (default from prompt)")
    ap.add_argument("--input", action="append", help="reference/input image (repeatable)")
    ap.add_argument("--aspect", help="aspect ratio: " + ", ".join(sorted(ASPECTS)))
    ap.add_argument("--size", help="512 / 1K / 2K / 4K (sent only if the model supports it)")
    ap.add_argument("--quality", choices=["lite", "standard", "fast", "pro"], default="standard",
                    help="model tier; pro is used only when asked")
    ap.add_argument("--mode", choices=["draft", "standard", "final"],
                    help="apply this mode's generation budget (templates/quality-modes.json)")
    ap.add_argument("--model", help="exact model id (no fallback)")
    ap.add_argument("-n", "--count", type=int, default=1, help=f"variants (separate billed calls, max {MAX_PER_RUN})")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--retry-timeouts", action="store_true", help="re-send after a timeout (may double-bill)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.aspect and a.aspect not in ASPECTS:
        sys.exit(f"Unsupported aspect {a.aspect}. Use one of: {', '.join(sorted(ASPECTS))}")
    size = a.size.upper() if a.size else None
    if size and size not in SIZES:
        sys.exit("--size must be 512, 1K, 2K or 4K (uppercase K)")
    if a.count > MAX_PER_RUN:
        sys.exit(f"-n {a.count} exceeds GEMINI_IMAGE_MAX_PER_RUN={MAX_PER_RUN}; raise it explicitly if intended")
    for p in a.input or []:
        if not os.path.isfile(p):
            sys.exit(f"Input image not found: {p}")
    if a.model:
        models = [a.model]
    elif os.environ.get("GEMINI_IMAGE_MODEL"):
        models = [os.environ["GEMINI_IMAGE_MODEL"]]
    else:
        models = MODELS[a.quality]
    if a.mode:
        modes = json.load(open(os.path.join(os.path.dirname(_CAPS_PATH), "..", "templates", "quality-modes.json"),
                               encoding="utf-8"))
        limit = modes[a.mode]["max_generations"]
        m0 = ik.Manifest(a.workdir) if os.path.isdir(a.workdir) else None
        used = len([i for i in (m0.data["items"] if m0 else []) if i.get("source_type") == "api"])
        if used + a.count > limit:
            sys.exit(f"Budget: mode '{a.mode}' allows {limit} generations in {a.workdir}; {used} used, {a.count} requested. "
                     "Show the best result so far, or ask the user to raise the limit.")

    if a.dry_run:
        asp, sz, notes = supported(models[0], a.aspect, size)
        print(json.dumps({"models": models, "count": a.count, "notes": notes,
                          "body": build_body(a.prompt, None, asp, sz, models[0])}, indent=2))
        return 0
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set. Create a key at https://aistudio.google.com/apikey and export it "
                 "in your shell profile or .env (never paste it into a chat).")

    m = ik.Manifest(a.workdir)
    name = a.name or ik.slugify(a.prompt, 30)
    saved, used_model, notes = [], None, []
    for v in range(a.count):
        data = None
        for model in (models if used_model is None else [used_model]):
            asp, sz, cap_notes = supported(model, a.aspect, size)
            for n in cap_notes:
                print("  note:", n, file=sys.stderr)
            body = build_body(a.prompt, a.input, asp, sz, model)
            try:
                data = call(model, body, key, a.timeout, a.retries, a.retry_timeouts)
                used_model = model
                break
            except LookupError:
                print(f"  model {model} not available, trying next in the same tier…", file=sys.stderr)
            except Timeout:
                print(json.dumps({"status": "timeout", "variant": v + 1, "saved": saved,
                                  "message": "No response in time. It may still have been processed and "
                                             "billed, so it was NOT re-sent. Ask the user before retrying."}, indent=2))
                m.save()
                return 5
        if data is None:
            sys.exit(f"No model available in this tier: {models}. Pass --model with a current id.")
        images, texts = extract(data)
        notes += texts
        if not images:
            reason = data.get("promptFeedback") or [c.get("finishReason") for c in data.get("candidates", [])]
            print(f"  variant {v + 1}: no image returned ({reason})", file=sys.stderr)
            continue
        for raw in images:
            kind, mime = ik.sniff(raw[:2048])
            ext = ik.EXT_FOR.get(kind, ".bin")
            suffix = "" if a.count == 1 and len(images) == 1 else f"-v{len(saved) + 1}"
            path = ik.atomic_write(os.path.join(m.workdir, "originals", f"{ik.slugify(name)}{suffix}{ext}"), raw)
            rep = ik.validate(path, a.aspect, ratio_tol=0.03)
            if size and rep.width:   # was the requested size actually delivered?
                want = CAPS["expected_long_side_px"].get(size)
                got = max(rep.width, rep.height)
                if want and got < 0.85 * want:
                    rep.warnings.append(f"requested {size} but file is {rep.width}x{rep.height} (long side {got}px)")
            dup = m.find_sha(rep.sha256)
            if dup:
                os.unlink(path)
                saved.append({"id": dup["id"], "file": os.path.join(m.workdir, dup["file"]), "duplicate": True})
                continue
            item = m.add(name=os.path.splitext(os.path.basename(path))[0], source_type="api", source=used_model,
                         file=m.rel(path), sha256=rep.sha256, format=rep.kind, mime=mime, bytes=rep.bytes,
                         width=rep.width, height=rep.height, has_transparency=rep.has_transparent_pixels,
                         prompt=a.prompt, aspect=a.aspect, size=size, inputs=[os.path.basename(p) for p in a.input or []],
                         response_id=data.get("responseId"), created=ik.now(), role="original")
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
            if rep.warnings:
                item["warnings"] = rep.warnings
                m.save()
            saved.append({"id": item["id"], "file": path, "size": [rep.width, rep.height], "ok": rep.ok,
                          "errors": rep.errors or None, "warnings": rep.warnings or None})
    m.save()
    out = {"status": "ok" if saved else "no_image", "model": used_model, "images": saved}
    if notes:
        out["gemini_note"] = ik.redact(" ".join(dict.fromkeys(notes)))[:600]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if saved else 6


if __name__ == "__main__":
    sys.exit(main())
