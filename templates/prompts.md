# Prompt templates

Fill the `<…>` slots; delete what doesn't apply. Always end with the text rule
and exclusions. Brand → paste its `style_sentence` verbatim.

## 1. Product advertisement
```
Professional studio product photograph of <product, material, colour, key detail>,
<placed on / floating above> <surface or backdrop>, <hero angle: 3/4 front, eye level>,
<lighting: softbox key light from left, subtle rim light>, <background colour/gradient>,
crisp focus on the label and texture, soft natural shadow, premium commercial look,
<aspect> composition with clean empty space on the <side> for a headline,
no text, no logos other than the product's own, no watermark.
```
Edit with the real product: attach its photo and add "Use the exact product from
the reference image; keep its shape, colours and label unchanged."

## 2. Social media post
```
<Style: candid lifestyle photo | bright flat illustration> of <scene with people/object>
in <setting, city detail>, <emotion/mood>, <palette>, <light>, scroll-stopping but
uncluttered, subject in the <upper/lower> two thirds, clean area at the <top/bottom>
for text, <4:5 | 1:1 | 9:16> format, no text, no watermark.
```

## 3. Website hero image
```
Wide <16:9 | 21:9> hero image for a <industry> website: <concept metaphor or scene>,
<style: soft 3D isometric | modern editorial photo>, main subject on the <right>
third, calm negative space on the <left> for headline and buttons, <brand palette>,
<light>, high detail, modern, trustworthy, no text, no UI elements, no watermark.
```

## 4. Banner
```
<Event/offer> banner background: <key visual>, <style>, strong focal element on
the <left/right>, wide horizontal composition that survives cropping to 1920x600,
<palette>, <energy level>, no text, plenty of flat space for a headline and CTA.
```
Then `resize --size 1920x600 --mode crop|extend`, `text`, `logo`.

## 5. Transparent asset (icon, sticker, product cut-out)
```
Single <object> as a clean isolated asset, <style: 3D clay | flat vector | photo>,
centred, fully visible with margin, sharp edges, no shadow on the ground,
on a perfectly plain solid pure #00FF00 green background (use pure white #FFFFFF
if the object is green), no gradients, no text.
```
Then `postprocess.py cutout IN --key auto --trim --out asset.png` and
`check_image.py asset.png --alpha`. Report transparency only if the check passes.

## 6. Edit from a reference
```
Edit the attached image. KEEP unchanged: <list: person's face and pose, product,
framing, perspective>. CHANGE only: <one or two precise changes>. Match lighting,
colour and grain of the original so the edit is seamless. No other changes,
no added text.
```
Masks/regional edits: Gemini (API and web) edits from instructions, not pixel
masks, so describe the region in words ("only the wall behind the person").
Say to the user that preservation is likely but not guaranteed, and compare
before/after side by side (`sheet`).
