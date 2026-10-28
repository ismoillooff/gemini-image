# Project mode — create, store, wire, verify

Goal: the asset ends up **used** in the right component and **checked**, not just
sitting in a folder. Within a request like "make a hero image and put it on the
landing page", do the local saving, exporting and code edits without asking at
each step. Still: no commits/pushes/deploys unless asked, no unrelated refactors,
keep the user's uncommitted changes, respect cost limits, no secrets in code.

`S=${CLAUDE_SKILL_DIR}/scripts` (or the skill's base directory).

## 1. Context (read before generating)

```bash
python $S/project_scan.py .                                  # framework, dirs, tokens, icons, checks, placeholders
python $S/project_scan.py . --component src/components/Hero.tsx   # image slots, ratio hints, a11y/loading issues
python $S/asset_registry.py find . "hero landing illustration"     # reuse before generating
```
Read every file in `read_first` (AGENTS.md, CLAUDE.md…) and follow it over these
defaults. Take colours from `design_tokens`, dark-mode handling from
`dark_mode_signals`, size/ratio from the component's slot.

## 2. Generate or not

| Need | Do |
|---|---|
| A matching asset already exists (registry `find`, or existing images) | reuse it |
| Plain UI icon (arrow, close, user, settings…) and an icon library is installed | use the library — consistency beats novelty |
| Real vector required (icon set, logo, scalable line art) | write SVG code or use the library; never save Gemini's raster as `.svg` (`asset_export.py` refuses) |
| Custom illustration, product shot, hero, banner, unique icon | generate with Gemini (user asked for Gemini → always Gemini) |

## 3. Brief from the project

Purpose & placement (component, above/below fold) · brand palette from tokens and
`brand.json` · light/dark background it sits on · subject & composition · empty
space where UI text overlays · ratio from the slot (`ratio_hint`) and needed
resolution (largest rendered width × 2 for retina) · transparency? · references
and what must not change. Same style sentence for every asset of one project.
One image by default; variants only for real concept choices.

## 4. Original vs production (two separate steps)

1. Get the original (API / browser download / attachment — SKILL.md §4).
2. `asset_export.py ORIGINAL --project . --id <stable-id> --purpose "…" --brief "…" --ratio <slot ratio> --widths <w1,w2,w3> --usage <component> [--alpha] [--dest <dir>] [--format webp|png|avif]`
   - original copied byte-for-byte to `.gemini-assets/originals/` (not publicly served; whether to commit it or add it to `.gitignore` is the user's call — mention it, don't decide)
   - production files in the project's convention dir (`suggested_dest`), stable names `<id>.webp` or `<id>-<w>.webp`
   - no upscaling (widths capped), crop to ratio instead of stretching; alpha verified when `--alpha`
   - existing user files are never replaced; `--replace` updates only this asset's own files at the same paths (references keep working)
   - registry entry with purpose, brief, model, paths, sizes, hashes

Widths: hero 640,1280,1920 · content image 480,960 · card 400,800 · icon/logo one size (2× display).
Transparent icons: `postprocess.py cutout` first, then export with `--alpha` (webp or png).

## 5. Wire it into the code (minimal diff)

Replace the placeholder; don't touch unrelated code. Always: explicit
width/height (or aspect-ratio) to avoid layout shift; `object-fit: cover` when
the box ratio differs; alt text that says what the image shows (Uzbek if the page
is Uzbek); `alt=""` + `aria-hidden` for purely decorative images; icon-only
buttons get `aria-label`. Hero/LCP image: **no** `loading="lazy"`, add
`fetchpriority="high"`/`priority`; below the fold: `loading="lazy"`.
Never reference Gemini/googleusercontent URLs.

**Next.js** (public dir)
```tsx
import Image from "next/image";
<Image src="/images/hero-landing-1920.webp" alt="…" width={1920} height={1080}
       priority sizes="100vw" className="h-auto w-full object-cover" />
```
or static import from `src/` (`import hero from "@/assets/images/hero-landing.webp"`) for automatic sizing.

**React / Vue / Svelte + Vite** (src/assets → bundled, hashed)
```tsx
import hero640 from "../assets/images/hero-landing-640.webp";
import hero1280 from "../assets/images/hero-landing-1280.webp";
<img src={hero1280} srcSet={`${hero640} 640w, ${hero1280} 1280w`} sizes="(max-width: 768px) 100vw, 1200px"
     width={1280} height={720} alt="…" fetchPriority="high" className="w-full h-auto object-cover" />
```
Vue: `:src`, `:srcset`; Svelte: `src={hero1280}`. TypeScript needs `vite/client` types (usually present).

**Astro**
```astro
---
import { Image } from "astro:assets";
import hero from "../assets/images/hero-landing.webp";
---
<Image src={hero} alt="…" widths={[640, 1280, 1920]} sizes="100vw" loading="eager" fetchpriority="high" />
```

**Nuxt** `<NuxtImg src="/images/hero-landing.webp" …>` if `@nuxt/image` is installed, else `<img>`.
**SvelteKit** `import hero from '$lib/assets/hero-landing.webp'` or `/images/…` from `static/`.
**Angular** `<img ngSrc="images/hero-landing.webp" width="1920" height="1080" priority>` (NgOptimizedImage) or plain `<img>`.
**Laravel** `<img src="{{ asset('images/hero-landing.webp') }}" …>` · **Django** `{% static 'images/…' %}` · **Rails** `image_tag`.
**Static HTML** `<img src="img/x-1280.webp" srcset="…" sizes="…" width height alt fetchpriority="high">`.
**CSS background** (decorative only): `background-image: image-set(url(x-1280.webp) 1x, url(x-1920.webp) 2x)` + `background-size: cover`; give the element a min-height/aspect-ratio.

**Dark mode**: if the asset has a light background and the site has dark mode,
generate a dark variant (same id + `-dark`) and switch with
`<picture><source srcset="…-dark.webp" media="(prefers-color-scheme: dark)"><img …></picture>`
or the project's theme mechanism (`.dark` class → two images with `dark:hidden` / `hidden dark:block`).
Transparent assets: check contrast on both themes.

## 6. Verify (report only what actually ran)

1. `python $S/asset_registry.py verify . --strict` → files valid, hashes, referenced in code, no Gemini URLs, no fake SVGs.
2. `python $S/project_scan.py . --component <file>` → slot has no issues left.
3. Project checks from `check_commands` (build, typecheck, lint). Build output contains the asset (Vite: `dist/assets/<id>-*.webp`; Next: `.next` / `public` copy). A failure you caused → fix it; pre-existing failures → report, don't fix unrelated code.
4. Visual: start the dev server (`check_commands.dev`) or open the built page:
   - Claude in Chrome / built-in browser: load the page, run `browser_snippets.js` #7 at desktop, `resize_window` to 390×844 and run again, screenshot both; or
   - `python $S/render_check.py <url-or-html> --widths 1440,390` (Playwright Chromium, light + dark; wkhtmltoimage fallback is approximate).
   - Look at the screenshots: cropping of the subject, stretching, text contrast over the image, layout shift.
   - No browser available → say "not visually verified". Never claim it.

## 7. Report

What was created (id, purpose, size/format) · original path · production paths ·
where it's wired (file:line) · checks run with results · anything blocked or
left for the user (commit originals?, pre-existing issues found). Show the
production image in chat (SendUserFile render) and attach the original when the
environment allows.

If Gemini is unreachable: don't leave fake files or broken imports. Finish the
independent parts (slot analysis, code ready for the asset path, alt text) only
if they don't break the build, otherwise leave the placeholder, and state the
blocked step.
