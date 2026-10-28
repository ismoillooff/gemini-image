# Quality system

Goal: an image fit for its job — strong composition, believable materials, few
artefacts — not just a bigger file. Technical checks and aesthetic judgement are
kept separate; never write unmeasured claims ("98/100", "20x better").

## 1. Quality = criteria of the task

`brief.py --criteria <task>` prints them. Summary:

| Task | What decides quality | Detail level |
|---|---|---|
| product | silhouette & proportions, material surfaces, exact label/logo, grounded shadow | high on product, low on background |
| photo | anatomy, perspective, light ↔ shadow ↔ reflection consistency, textures | as a real lens would render |
| illustration | one style, readable silhouettes, composition | moderate, no noisy micro-detail |
| icon | recognisable at 16–32 px, one metaphor, consistent stroke, light+dark bg | minimal — no texture, no tiny parts |
| hero / banner | one focal subject, calm text area, survives responsive crops | low where text sits |
| background | doesn't compete with content | low |

More detail is not better: never add texture to icons or minimal designs.

## 2. Brief → prompt (fixed order)

`brief.py --task T --json brief.json --mode M` (fields in `templates/brief.example.json`)
composes: **purpose → subject (+ key features) → composition (foreground / midground /
background) → materials → lighting → colour & style → must-keep elements →
constraints → output**. Missing essentials → one short question; otherwise
reasonable defaults per task.

Write visible attributes, not praise:

| Instead of | Write |
|---|---|
| "high quality metal" | "brushed aluminium with fine horizontal grain, soft specular streak along the edge" |
| "perfect lighting" | "large soft key light from the upper left, warm 3200K; cool fill from the right; soft shadow to the lower right" |
| "ultra detailed" | the 2–4 details that matter: "stitching on the strap, embossed logo, matte rubber feet" |
| "realistic" | "photograph, eye-level, 35mm-equivalent framing, natural window light" |
| "4K / 8k" | nothing — size is `imageSize` (API) and is verified on the file |

Camera terms (lens, aperture) only when they change the picture (depth of field,
perspective); the model does not simulate optics exactly — don't promise it.
Words in the subject can turn into lettering: "layers (cloud, data, AI)" produced
an "AI" label on a layer. Describe things by look ("a cloud-shaped layer"), not by
name, when no text is wanted.

`brief.py --lint "<prompt>"` flags contradictions (photo vs flat, night vs noon,
transparent vs scene, minimal vs highly detailed, no-text vs quoted text, wide vs
vertical, soft vs hard light), empty quality words, size claims, unsupported
parameters (seed, negative prompt, steps, --ar) and over-long/repetitive prompts.

## 3. What the model supports

`references/model-capabilities.json` (from the official docs). Only documented
parameters are sent: aspectRatio, imageSize (per model), reference images.
Not documented → never sent: mask/inpainting, transparency, seed, negative
prompt, steps, quality. "4K" in a prompt ≠ output size; the generator checks the
real file size and warns on mismatch. Gemini web app: size/ratio come only from
the prompt; the on-page image is a ~1024 px preview.

## 4. Reference consistency

List each reference's role (colour / composition / object / style) and the
must-keep features (logo geometry, product shape, colours, proportions). Compare
results side by side (`qa.py compare`). Logos: place the real logo file
(`postprocess.py logo`) instead of trusting a generated copy when exactness
matters. Series: one style sentence reused verbatim. Never promise identical
reproduction; name what drifted and fix it with a targeted edit. Re-generating a
small concept does not reproduce its composition — use the same Gemini chat
(multi-turn editing) or attach the concept as a reference.

## 5. Staged workflow (use only what the task needs)

1. Direction & composition → 2. limited concepts (2–3, only for real choices) →
3. pick by criteria, or let the user pick aesthetic options → 4. targeted edits →
5. export at a supported final size → 6. technical + visual QA → 7. deliver / wire
into the project. Simple tasks: brief → one generation → QA → deliver.

Observed in live tests (2026-10-28): the Gemini web app returned the requested
4:5 and 1:1 ratios (825×1024, 1024×1024 previews); a "keep everything else" edit in
the same chat changed only the targeted region (verified with `qa.py compare`
difference maps) in 2 of 2 cases; a flat-icon prompt still added a third colour
(outline). Treat these as observations, not guarantees.

## 6. Modes and budgets (`templates/quality-modes.json`)

| Mode | Tier | Size | Variants | Edit attempts | Max generations |
|---|---|---|---|---|---|
| draft | lite | 1K | 1 | 0 | 2 |
| standard | standard | 1K (hero 2K) | 1 | 2 | 4 |
| final | standard | 2K | 2 | 3 | 6 |

`final` is not unlimited and never auto-selects the pro tier. `qa.py budget W --mode M`
shows what's left; `generate_image.py --mode M` refuses to exceed it. At the limit:
show the best result and the remaining issues.

## 7. Visual QA — look, then record

- Overall view first, then `qa.py detail IMG --grid 3x3` (100%+ crops) and
  `--box` on key regions (faces, hands, logo, label, text).
- Checklist: `qa.py checklist --task T` (common + task items: counts & placement,
  anatomy/geometry, perspective, light/shadow/reflection agreement, materials,
  melted/duplicated detail, halos & oversharpening, edges, text/logo, crop & margins,
  readability at final size).
- Icons: `qa.py icon IMG --brand "#hex"` — judge the *actual size* rows, not the big preview.
- Hero/banner: `qa.py crops IMG --ratios 16:9,4:5,9:16`.
- Record: `qa.py record W ID --task T --pass … --fail … --visual` — `--visual` only
  after actually viewing. No viewing tool → status stays "not visually reviewed";
  pixel size or hashes never count as an aesthetic check.

## 8. Targeted fixes

For each fix: the observed defect · the region/property to change · what must
stay · one instruction (same Gemini chat, or the edit with the image attached) ·
`qa.py compare previous new` · keep the previous version if the fix broke
something else. Stop at the mode's edit-attempt limit.

Fix prompt shape:
> "Edit the last image. Defect: <what is wrong, where>. Change only: <region/property>.
> Keep exactly: <list>. Do not change anything else."

## 9. Text & exact data

Headlines, prices, phone numbers, CTAs → `postprocess.py text` (font glyph check
incl. oʻ gʻ, fit inside margins, contrast). Leave space in the composition for
it. OCR, if available, is a helper — read the text yourself. Charts, diagrams and
anything with exact geometry or numbers: build them in code/SVG, not with the
image model.

## 10. Upscale & post-processing

Use a native size when available (API imageSize). Upscale only if needed:
`postprocess.py upscale` (max 4x, labelled "upscaled", noise check) — it adds no
real detail. Sharpen/denoise/colour lightly, on a copy; `qa.py compare` against
the source and discard if worse. Originals are never overwritten.
