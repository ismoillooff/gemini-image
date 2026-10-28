"""Shared helpers for the gemini-image skill (standard library + optional Pillow).

Responsibilities:
  * sniff real file type from bytes (never trust extensions or URLs)
  * validate images (decode, size, ratio, alpha, error-page detection)
  * SHA-256 integrity, de-duplication, collision-free file names
  * a compact JSON manifest that links originals, previews and edits
  * SSRF-safe, size/time/redirect-limited HTTP(S) fetching
  * redaction of secrets and signed URL tokens before anything is logged
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# ── limits (overridable via env, see .env.example) ──────────────────────────
def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MAX_BYTES = _env_int("GEMINI_IMAGE_MAX_BYTES", 50 * 1024 * 1024)
FETCH_TIMEOUT = _env_int("GEMINI_IMAGE_FETCH_TIMEOUT", 30)
MAX_REDIRECTS = _env_int("GEMINI_IMAGE_MAX_REDIRECTS", 5)
MANIFEST_NAME = "manifest.json"

# ── redaction ───────────────────────────────────────────────────────────────
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                 # Google API keys
    re.compile(r"(?i)(x-goog-api-key|authorization|cookie|token|key|sig|signature)=([^&\s]+)"),
    re.compile(r"ya29\.[0-9A-Za-z_\-]+"),                    # OAuth access tokens
]


def redact(text: str) -> str:
    """Remove API keys, tokens and signed-URL parameters from a string."""
    if not text:
        return text
    out = _SECRET_PATTERNS[0].sub("AIza***REDACTED***", str(text))
    out = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}=***", out)
    out = _SECRET_PATTERNS[2].sub("ya29.***", out)
    key = os.environ.get("GEMINI_API_KEY")
    if key and len(key) > 6:
        out = out.replace(key, "***REDACTED***")
    return out


def redact_url(url: str) -> str:
    """Keep scheme+host only. Paths on image CDNs are capability tokens too."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return "<invalid-url>"
    if p.scheme in ("http", "https"):
        return f"{p.scheme}://{p.hostname or ''}/…"
    if p.scheme in ("", "file"):
        return os.path.basename(p.path) or url
    return f"{p.scheme}:…"


def url_fingerprint(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# ── type sniffing ───────────────────────────────────────────────────────────
IMAGE_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpeg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"BM", "bmp", "image/bmp"),
]
EXT_FOR = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "bmp": ".bmp", "webp": ".webp",
           "avif": ".avif", "heic": ".heic", "tiff": ".tif"}


def sniff(head: bytes) -> tuple[str, str]:
    """Return (kind, mime) from the first bytes. kind is an image format or
    'html' | 'json' | 'xml' | 'text' | 'empty' | 'unknown'."""
    if not head:
        return "empty", "application/x-empty"
    for sig, kind, mime in IMAGE_SIGNATURES:
        if head.startswith(sig):
            return kind, mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "avif", "image/avif"
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "heic", "image/heic"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff", "image/tiff"
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if text.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or b"<html" in text[:512]:
        return "html", "text/html"
    if text.startswith((b"{", b"[")):
        return "json", "application/json"
    if text.startswith(b"<?xml") or text.startswith(b"<svg"):
        return ("svg", "image/svg+xml") if b"<svg" in text[:1024] else ("xml", "application/xml")
    try:
        head[:512].decode("utf-8")
        return "text", "text/plain"
    except UnicodeDecodeError:
        return "unknown", "application/octet-stream"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ahash(path_or_image) -> str:
    """64-bit average hash of the visual content (same algorithm as browser
    snippet 3). Survives resizing/re-encoding, so it can match a downloaded
    full-size file to the preview seen on a page. Not a security hash."""
    from PIL import Image
    im = Image.open(path_or_image) if isinstance(path_or_image, str) else path_or_image
    im = im.convert("RGB").resize((8, 8), Image.BOX)
    px = im.tobytes()
    g = [0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2] for i in range(0, 192, 3)]
    avg = sum(g) / 64
    return f"{int(''.join('1' if v > avg else '0' for v in g), 2):016x}"


def hamming(h1: str, h2: str) -> int:
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


# ── validation ──────────────────────────────────────────────────────────────
@dataclass
class Report:
    path: str
    ok: bool = False
    kind: str = "unknown"
    mime: str = ""
    bytes: int = 0
    width: int | None = None
    height: int | None = None
    mode: str | None = None
    has_alpha_channel: bool | None = None
    has_transparent_pixels: bool | None = None
    sha256: str | None = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], "")} | {"ok": self.ok}


def _ratio_value(r: str) -> float:
    a, b = (float(x) for x in r.split(":"))
    return a / b


def validate(path: str, expect_ratio: str | None = None, ratio_tol: float = 0.02,
             min_width: int = 0, min_height: int = 0, require_alpha: bool = False,
             max_bytes: int = MAX_BYTES) -> Report:
    r = Report(path=path)
    if not os.path.exists(path):
        r.errors.append("file does not exist")
        return r
    if not os.path.isfile(path):
        r.errors.append("not a regular file")
        return r
    r.bytes = os.path.getsize(path)
    if r.bytes == 0:
        r.kind, r.mime = "empty", "application/x-empty"
        r.errors.append("file is empty (0 bytes)")
        return r
    if r.bytes > max_bytes:
        r.errors.append(f"file larger than limit ({r.bytes} > {max_bytes} bytes)")
    with open(path, "rb") as f:
        head = f.read(2048)
    r.kind, r.mime = sniff(head)
    r.sha256 = sha256_file(path)
    if r.kind in ("html", "json", "xml", "text", "empty", "unknown"):
        hint = {"html": "an HTML page (login/error page?)", "json": "a JSON error response",
                "xml": "an XML document", "text": "plain text"}.get(r.kind, "not a recognised image")
        r.errors.append(f"content is {hint}, not an image")
        return r
    ext = os.path.splitext(path)[1].lower()
    if ext and EXT_FOR.get(r.kind) and ext not in (EXT_FOR[r.kind], ".jpeg" if r.kind == "jpeg" else ""):
        r.warnings.append(f"extension {ext} does not match real format {r.kind}")
    if r.kind == "svg":
        r.warnings.append("SVG is vector markup; pixel checks skipped")
        r.ok = not r.errors
        return r
    try:
        from PIL import Image
    except ImportError:
        r.warnings.append("Pillow not installed: decode/size checks skipped (pip install pillow)")
        r.ok = not r.errors
        return r
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            im.load()
            r.width, r.height, r.mode = im.width, im.height, im.mode
            r.has_alpha_channel = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            if r.has_alpha_channel:
                alpha = im.convert("RGBA").getchannel("A")
                r.has_transparent_pixels = alpha.getextrema()[0] < 255
            else:
                r.has_transparent_pixels = False
    except Exception as e:  # truncated/corrupt data lands here
        r.errors.append(f"image cannot be decoded: {type(e).__name__}: {e}")
        return r
    if expect_ratio:
        want = _ratio_value(expect_ratio)
        got = r.width / r.height
        if abs(got - want) / want > ratio_tol:
            r.errors.append(f"aspect ratio {got:.3f} ({r.width}x{r.height}) does not match {expect_ratio} ({want:.3f})")
    if r.width < min_width or r.height < min_height:
        r.errors.append(f"too small: {r.width}x{r.height} < required {min_width}x{min_height}")
    if require_alpha:
        if not r.has_alpha_channel:
            r.errors.append("transparency required but image has no alpha channel")
        elif not r.has_transparent_pixels:
            r.errors.append("transparency required but every pixel is fully opaque")
    r.ok = not r.errors
    return r


# ── naming & storage ────────────────────────────────────────────────────────
def slugify(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^\w\-]+", "-", text.lower(), flags=re.UNICODE).strip("-_")
    return (s[:maxlen].rstrip("-_") or "image")


def unique_path(path: str, overwrite: bool = False) -> str:
    """Return path, or path-2, path-3… so an existing file is never overwritten."""
    if overwrite or not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{root}-{i}{ext}"):
        i += 1
    return f"{root}-{i}{ext}"


def atomic_write(path: str, data: bytes, overwrite: bool = False) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    final = unique_path(path, overwrite)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(final)), prefix=".part-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if overwrite:
        os.replace(tmp, final)
    else:
        try:
            os.link(tmp, final)          # fails if final appeared meanwhile
            os.unlink(tmp)
        except FileExistsError:
            os.unlink(tmp)
            return atomic_write(path, data, overwrite)
        except OSError:                  # filesystems without hard links
            os.replace(tmp, final)
    return final


# ── manifest ────────────────────────────────────────────────────────────────
STAGES = ["source_opened", "original_obtained", "verified", "preview_ready",
          "attached", "download_ready", "ui_verified"]


class Manifest:
    """Compact JSON record in <workdir>/manifest.json. Never stores secrets."""

    def __init__(self, workdir: str):
        self.workdir = os.path.abspath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.path = os.path.join(self.workdir, MANIFEST_NAME)
        self.data = {"version": 1, "items": []}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def rel(self, p):
        return os.path.relpath(os.path.abspath(p), self.workdir)

    def find_sha(self, sha):
        return next((i for i in self.data["items"] if i.get("sha256") == sha), None)

    def find_id(self, item_id):
        return next((i for i in self.data["items"] if i["id"] == item_id), None)

    def find_path(self, path):
        rp = self.rel(path)
        for i in self.data["items"]:
            if i.get("file") == rp:
                return i
            for d in i.get("derived", []):
                if d.get("file") == rp:
                    return i
        return None

    def add(self, **fields):
        item_id = fields.pop("id", None) or f"img-{len(self.data['items']) + 1:03d}"
        item = {"id": item_id, "stages": {s: "pending" for s in STAGES}, "derived": []}
        item.update(fields)
        self.data["items"].append(item)
        return item

    @staticmethod
    def stage(item, name, status="done", note=None):
        assert name in STAGES, name
        item["stages"][name] = status
        if note:
            item.setdefault("notes", []).append(redact(note))

    def add_derived(self, item, path, kind, **extra):
        rp = self.rel(path)
        item["derived"] = [d for d in item["derived"] if d["file"] != rp]
        d = {"file": rp, "kind": kind, "sha256": sha256_file(path), "created": now()}
        d.update(extra)
        item["derived"].append(d)
        return d


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ── SSRF-safe fetching ──────────────────────────────────────────────────────
class FetchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code           # machine-readable category


def _ip_blocked(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved
            or a.is_unspecified or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")))


def check_url(url: str, allow_http=False, allow_hosts=()):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in (("https", "http") if allow_http else ("https",)):
        raise FetchError("bad_scheme", f"scheme '{p.scheme}' not allowed (https only)")
    if not p.hostname:
        raise FetchError("bad_url", "URL has no host")
    if p.username or p.password:
        raise FetchError("bad_url", "credentials inside URLs are not accepted")
    hostport = f"{p.hostname}:{p.port}" if p.port else p.hostname
    if p.hostname in allow_hosts or hostport in allow_hosts:
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise FetchError("dns", f"cannot resolve host {p.hostname}: {e}")
    for info in infos:
        if _ip_blocked(info[4][0]):
            raise FetchError("blocked_address",
                             f"{p.hostname} resolves to a private/internal address; refused")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch(url: str, *, max_bytes=MAX_BYTES, timeout=FETCH_TIMEOUT, max_redirects=MAX_REDIRECTS,
          allow_http=False, allow_hosts=(), headers=None):
    """GET url following at most max_redirects, re-checking every hop.
    Returns (bytes, final_url, content_type, hops). Raises FetchError."""
    opener = urllib.request.build_opener(_NoRedirect)
    hops = 0
    current = url
    while True:
        check_url(current, allow_http, allow_hosts)
        req = urllib.request.Request(current, headers={"User-Agent": "gemini-image-skill/2",
                                                       "Accept": "image/*,*/*;q=0.5", **(headers or {})})
        try:
            resp = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    raise FetchError("bad_redirect", "redirect without Location")
                hops += 1
                if hops > max_redirects:
                    raise FetchError("too_many_redirects", f"more than {max_redirects} redirects")
                nxt = urllib.parse.urljoin(current, loc)
                if re.search(r"accounts\.google\.com|/ServiceLogin|/signin|/login", nxt, re.I):
                    raise FetchError("auth_required", "source redirects to a sign-in page")
                current = nxt
                continue
            code = {401: "auth_required", 403: "forbidden_or_expired", 404: "not_found",
                    410: "expired", 429: "rate_limited"}.get(e.code, "http_error")
            raise FetchError(code, f"HTTP {e.code} from {redact_url(current)}")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.timeout) or "timed out" in str(reason):
                raise FetchError("timeout", f"timed out after {timeout}s")
            raise FetchError("network", f"network error: {redact(str(reason))}")
        except (socket.timeout, TimeoutError):
            raise FetchError("timeout", f"timed out after {timeout}s")
        with resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("too_large", f"Content-Length {length} exceeds limit {max_bytes}")
            chunks, total = [], 0
            while True:
                try:
                    chunk = resp.read(65536)
                except (socket.timeout, TimeoutError):
                    raise FetchError("timeout", f"timed out after {timeout}s while reading")
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("too_large", f"body exceeds limit {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks), current, resp.headers.get("Content-Type", ""), hops
