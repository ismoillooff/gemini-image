# Changelog

## 3.2.0 — 2026-10-28
Image quality, detail accuracy and design consistency.
- `brief.py`: task types (product, photo, illustration, icon, hero, banner, background) with criteria and detail policy; prompt composed in a fixed order; lint for contradictions, empty quality words, size claims, unsupported parameters, over-long/repetitive prompts; mode plan.
- `templates/quality-modes.json`: draft / standard / final with tier, size, variants, edit attempts and generation budget (final ≠ unlimited, ≠ pro).
- `references/model-capabilities.json` from the official docs (Nano Banana 2.1 / Pro / 2 Lite / 2 / legacy; aspect ratios; imageSize per model; reference limits; no mask/seed/negative prompt). `generate_image.py` sends only supported parameters, checks the real output size, enforces mode budgets; tiers lite/standard/pro with current model IDs.
- `qa.py`: 100% detail crops, icon real-size sheets on light/dark/brand, responsive crop sheets, before/after compare with difference map and technical regression hints, technical metrics, task checklists, QA records with an explicit "visually reviewed" flag, budget status.
- `postprocess.py upscale`: max 4x, labelled, noise check.
- `references/quality.md`, `templates/qa-checklists.json`, `templates/brief.example.json`.
- Live-test lessons: browser snippets 8–9 (focus the input directly, verify the message was really sent); `text-risk` lint for labels/acronyms in the subject; observations recorded in `references/quality.md`.
- Tests: 72 (18 new).

## 3.1.0 — 2026-10-28
Project integration mode: the asset is created, stored by project convention, wired into the code and verified.
- `project_scan.py`: instruction files (AGENTS.md…), framework + image conventions (Next, Nuxt, SvelteKit, Astro, Angular, Gatsby, Remix, Vue/React/Svelte+Vite, Laravel, Django, Rails, static), existing image dirs (no `public/images` assumption), icon libraries, design tokens, dark-mode signals, check commands, placeholders, Gemini URLs in code; `--component` image slots with ratio hints (attributes, Tailwind aspect classes, CSS, placeholder URL), alt/size/loading issues, above-the-fold advice.
- `asset_export.py`: original byte-for-byte to `.gemini-assets/originals/` (non-public), production files (webp/png/avif) with ratio crop, width cap (no upscaling), stable names, refuses fake SVG / JPEG+alpha / checkerboard "transparency", never replaces user files, `--replace` keeps paths stable; registry entry.
- `asset_registry.py`: `find` (reuse first), `list`, `usage`, `verify` (integrity, hashes, referenced in code, no Gemini URLs, no raster-in-SVG).
- `render_check.py` + browser snippet 7: real-browser desktop/mobile × light/dark screenshots with per-image audit (not loaded, true upscaling incl. srcset, distortion, lazy above the fold, alt, CLS). Fixed a srcset false positive found in the live test.
- `references/project-integration.md`: decision table, brief from project, framework wiring recipes, a11y, loading, dark mode, verification and report.
- Tests: 54 (12 new project-mode tests incl. one real Chromium render).

## 3.0.0 — 2026-10-28
Focus: original quality, verification and honest delivery.
- **Original-first delivery**: originals stored byte-for-byte, SHA-256 verified at ingest and again at delivery; previews, crops, upscales, text and logo versions are separate, labelled derivatives in `manifest.json`.
- **Screenshots never pass as originals**: `--origin screenshot` labels them everywhere.
- New `imagekit.py`: real-format sniffing (rejects HTML/JSON/XML saved as images), decode check, size/ratio/alpha checks, collision-free atomic writes, dedup by hash, redaction of keys and signed URLs.
- New `fetch_image.py`: HTTPS-only, redirect/size/time limits, SSRF guard on every hop, sign-in/expired detection; never triggers generation.
- New `check_image.py` (incl. `--match` visual fingerprint) and `deliver.py` (inline-or-preview decision, ZIP, resume, stage tracking with `ui_verified: unknown`).
- `generate_image.py`: no fallback across price tiers, no automatic resend after timeout, bounded retries, per-run cap, manifest records, no overwrites.
- `postprocess.py`: `extend` mode (no stretching), `cutout` for transparent assets, glyph coverage + fit + contrast checks for text, refuses to write over inputs/originals.
- Browser snippets fixed after a live test on gemini.google.com (avatar filter, async result, busy detection); blob fingerprint replaced with canvas aHash.
- Docs: environment capability matrix, browser and API playbooks, brief and 6 prompt templates, brand JSON, 42 offline tests + manual check list.

## 2.0.0 — 2026-10-28
Prompt guide, browser playbook, postprocess (text/logo/resize/convert/sheet), variants, model fallback.

## 1.0.0 — 2026-10-28
First version: browser route via Claude in Chrome and a minimal API script.
