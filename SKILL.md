---
name: gemini-image
description: Create, edit or retrieve raster images with Google Gemini and hand over the verified ORIGINAL file — shown in the chat plus a download — or, inside a code project, save the asset in the project's convention, wire it into the component and verify build and rendering. Use whenever the user or a coding task needs a picture/photo/illustration/hero/banner/icon/asset (rasm, surat, banner, ikonka, изображение, картинка), replaces a placeholder image, shares a Gemini chat or share link, or asks for "the original"/"full size"; also for exact text, logos and platform sizes on images. Claude cannot generate raster images itself, so this skill is the way to deliver them.
compatibility: Claude Code (API route, needs GEMINI_API_KEY and Python 3.9+ with Pillow; optional Playwright for render checks), claude.ai and Claude Desktop with Claude in Chrome (browser route). See references/environments.md.
---

# Gemini Image

Pipeline: **brief → generate / edit / fetch → original file → verify → derived
versions → deliver (view in chat + original download) → honest report.**

**Working inside a code project** (an asset for a page/component, a placeholder to
replace, "put it on the landing page")? Use **project mode**: follow
`references/project-integration.md` — scan the project (`project_scan.py`), reuse
before generating (`asset_registry.py find`), keep the original outside public
dirs and export production files (`asset_export.py`), wire it into the code,
then verify (`asset_registry.py verify`, build/typecheck, `render_check.py` or the
browser at desktop + mobile). Done means *used in the code and checked*.

`SKILL_DIR` below = this skill's folder (`${CLAUDE_SKILL_DIR}` in Claude Code; the
base directory shown when the skill loads elsewhere). Work in a per-task folder,
e.g. `gemini-out/<task>/`, which gets `originals/`, `edits/`, `previews/` and a
`manifest.json` linking them.

## Non-negotiable rules

1. **The original is sacred.** Bytes from the API, Gemini's "Download full-sized
   image" or a direct URL are stored as received — no re-encode, resize or
   recompress — and verified with SHA-256. Every edit, preview, crop or upscale is
   a *separate* file. An upscale is never "higher resolution original".
2. **Screenshots are for checking composition only.** If a screenshot is all that
   can be delivered, ingest it with `--origin screenshot` and say plainly it is a
   screenshot, not the original.
3. **Verify before claiming.** Run `check_image.py` / the scripts' built-in
   checks. Say "transparent", "4K", "16:9" only when the check confirmed it.
   Say "shown in chat" only after actually sending the file; you can't see the
   chat UI, so visibility stays "unknown" unless the user confirms.
4. **No hidden costs.** One generation per request unless variants are wanted;
   never switch to a pricier model tier on your own; a timed-out or failed
   download never triggers a new generation — report and ask.
5. **No secrets anywhere.** Never ask for passwords, cookies or tokens; the user
   signs in themselves. API keys live only in the environment. Signed image URLs
   are not printed, logged or stored (scripts redact them).
6. **Never overwrite** the user's files; scripts pick `name-2.png` etc.

## Quality (read `references/quality.md` for anything beyond a quick image)

Quality = the task's criteria, not more detail or a bigger file
(`brief.py --criteria <product|photo|illustration|icon|hero|banner|background>`).
Build prompts with `brief.py` (fixed order: purpose → subject → composition →
materials → lighting → colour/style → must-keep → constraints → output; visible
attributes instead of praise words) and fix what `--lint` reports. Pick a mode:
**draft / standard / final** (`templates/quality-modes.json`: tier, size, variants,
edit attempts, generation budget — final ≠ unlimited, ≠ pro). Send only
parameters in `references/model-capabilities.json`. After each result: look at it,
then `qa.py detail` crops (icons: `qa.py icon`; heroes: `qa.py crops`), run the
task checklist, record with `qa.py record … --visual` only if you really looked.
Fix defects one at a time with a targeted edit, compare (`qa.py compare`), keep
the better version, stop at the budget and report what remains. Never claim
scores or "N× better".

## 1. Brief (fast)

Extract purpose & audience, platform/format/ratio and final display size, subject
& key features, composition (fore/mid/background), materials, light (direction,
softness, colour temperature), palette & realism level, exact text, references
(and each one's role), what must not change, what must not appear. Infer the obvious (Instagram post → 4:5).
If something essential is missing, ask once, in one message, with your
assumptions stated. Template: `templates/brief.md`. Brand named → load its
config (`templates/brand.example.json` shows the format; look for
`brands/<name>.json` here or `.claude/brand.json` in the project).

## 2. Choose the route

| Input / situation | Route |
|---|---|
| New image or edit, `GEMINI_API_KEY` set and Google reachable (Claude Code) | **API** → `references/api-route.md` |
| New image or edit, Claude in Chrome connected (web / Desktop) | **Browser** → `references/browser-route.md` |
| Gemini chat or share link | Browser: open it, pick the right image, use its download button |
| Direct image URL | `fetch_image.py URL` (Claude Code; the web sandbox can't reach most hosts — then use the browser) |
| User attached an image | `fetch_image.py <file> --origin attachment` |

Environment capabilities differ — check `references/environments.md` before
promising inline display or automatic downloads. Nothing available → say what
to set up, in one sentence. Don't substitute an SVG unless asked.

## 3. Prompt

Use `templates/prompts.md` (product ad, social post, website hero, banner,
transparent asset, reference edit) and `references/prompt-guide.md`.
**Exact text** (Uzbek oʻ/gʻ, Cyrillic, prices, phone numbers, CTAs) is added
locally in step 5 — prompt for clean empty space instead. **Supplied logos are
placed, never regenerated.** **Variants:** only when choosing a concept; then 2–3
with clearly different compositions, and continue from the one picked. For
edits, list what stays and what changes; don't promise the model will preserve
things it can't guarantee.

## 4. Get and verify the original

- API: `generate_image.py` saves, verifies and records originals itself.
- Browser / Downloads / attachment / URL: `fetch_image.py <path-or-url> --workdir W --origin <type>`
  (rejects HTML/JSON/login pages, empty or corrupt files, internal-network URLs;
  de-duplicates by hash). Exit codes: 0 ok, 2 fetch failed, 3 not an image, 4 duplicate.
- Check against the brief: `check_image.py FILE --ratio 4:5 --min 1080x1350 [--alpha]`.
- Look at the image yourself: subject, hands/faces, artefacts, unwanted text.
  Clear miss → at most two targeted retries (each one is a new generation).

## 5. Derived versions (`scripts/postprocess.py`)

Order: **resize → text → logo**, each size from the clean original.

| Need | Command |
|---|---|
| Platform size (no stretching) | `resize O --preset ig-post --mode crop\|pad\|extend --out W/edits/x.png` |
| Exact text, glyph + fit + contrast checked | `text IN --text "…" --sub "…" --pos bottom-left --box "#000000AA" --out …` |
| Supplied logo | `logo IN --logo logo.png --pos top-right --scale 0.15 --out …` |
| Transparent asset | `cutout IN --key auto --trim --out W/edits/asset.png` then `check_image.py --alpha` |
| Format | `convert IN --out …webp` |
| Variant picker | `sheet A B C --out W/edits/variants.jpg` |

Presets: ig-post, ig-story, ig-square, tg-post, fb-post, fb-cover, og-image,
yt-thumb, web-hero, web-banner, a4-300dpi, favicon. Outputs are recorded in the
manifest as derivatives of their original. If Pillow can't be installed, say that
text/logo/resize steps are unavailable rather than letting Gemini draw the text.

## 6. Deliver

`deliver.py --workdir W [--zip] [--env web|desktop|claude-code]` re-hashes each
original and returns what to send:

- `display: render` → send with SendUserFile `display: "render"` (shown in chat;
  for normal-size originals this IS the original, so preview = original).
- `display: attach` → the original as a downloadable file, when the inline copy
  had to be a reduced preview (it is labelled as such).
- `display: path` → Claude Code: give the path (terminal shows no images).

After sending, record it: `deliver.py --workdir W --mark img-001 attached`.
Several images: send the variants sheet or previews in order, each with its
original; add the ZIP for bulk download. Tell the user the format and real pixel
size from the report's `say` line. If a stage failed (download blocked, hash
mismatch), say which stage and what still works. Re-running `deliver.py`
resumes: already-delivered items are skipped.

## 7. Close

One or two lines: what was made, which file is the original, what is a
preview/edit. Offer one natural next step. Close browser tabs you opened once the
user stops iterating.
