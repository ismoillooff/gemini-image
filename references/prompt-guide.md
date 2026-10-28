# Prompt guide for Gemini images

Read this when writing the prompt (SKILL.md step 3; ready-made templates in templates/prompts.md). Gemini rewards concrete, visual,
English descriptions written as one flowing paragraph, not tag soup.

## The formula

```
[Image type & style] of [subject + key details], [action/pose], in/at [setting],
[composition & camera], [lighting], [colour palette], [mood], [aspect ratio],
[text instruction], [what to avoid].
```

Example — user says "kofe do'koni uchun Instagram post":
> Photorealistic lifestyle photo of a ceramic cup of cappuccino with leaf latte art
> on a light oak table, a hand reaching for it, in a bright minimalist café in
> Tashkent; overhead 45° angle, shallow depth of field, soft morning window light,
> warm cream and terracotta palette, calm inviting mood, 4:5 vertical, no text,
> clean empty space in the top third for a headline, no logos, no watermarks.

## Vocabulary that works

| Want | Say |
|---|---|
| Real photo look | photorealistic, shot on 50mm lens, f/1.8, natural light, film grain |
| Product / e-commerce | studio product shot, seamless white background, softbox lighting, soft shadow, centered |
| Clean UI/SaaS hero | 3D isometric illustration, soft gradients, glassmorphism, rounded shapes, pastel |
| Flat graphic | flat vector illustration, bold shapes, limited 4-colour palette, no gradients |
| Premium / luxury | dark moody background, rim light, gold accents, high contrast |
| Friendly / kids | cute 3D cartoon style, rounded, bright saturated colours, soft lighting |
| Local feel (Uzbekistan) | Uzbek ikat patterns, blue Samarkand tilework, suzani embroidery, Tashkent street, chinar trees |
| Icons | single minimalist app icon, centered, simple geometric glyph, solid background |

Negative phrasing goes at the end as plain words: "no text, no logos, no
watermark, no extra fingers, no distorted faces".

## Text inside the image

- Gemini is decent at **short English** words (1–3 words) and poor at Uzbek
  (oʻ, gʻ, sh), Cyrillic, prices and long sentences.
- Exact wording → generate with "no text, clean empty space at <top/left/bottom>
  for a headline", then add the text with `postprocess.py text`.
- If you do let Gemini write text, put it in quotes and say the font style:
  `the word "SALE" in bold white sans-serif letters`.

## Editing an existing image

Describe only the change and what must stay identical:
> Keep the person, pose and framing exactly the same. Replace the background with
> a sunset beach. Match the lighting on the person to the warm sunset.

For multi-image composition (product into scene, logo onto shirt) attach both
images and name them by role: "Put the product from the first image on the table
in the second image, same scale and perspective, natural shadow."

## Consistency across a series

Re-use the same style sentence word-for-word (store it in the brand file), keep
palette and lighting identical, and for the browser route stay in the same Gemini
chat so it keeps context ("same style as before, now a 9:16 version").

## Iterating

Change one thing per retry and say it plainly: "Same image but the cup is
white and the background is less busy." Two misses on the same issue → tell the
user what Gemini struggles with and offer an alternative (post-processing,
different composition).
