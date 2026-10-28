# gemini-image — Claude skill

Google Gemini orqali rasm yaratadi, tahrirlaydi yoki Gemini havolasidan rasmni oladi.
Natijada **tekshirilgan original faylni** topshiradi: rasm chatda ko'rinadi va
original yuklab olish uchun beriladi. Ekran rasmi hech qachon original deb
ko'rsatilmaydi.

## Nima qiladi
- Yangi rasm yoki tahrir: API (Claude Code) yoki Gemini sayti (Claude in Chrome) orqali
- Gemini chat yoki share havolasidagi kerakli rasmni topadi va **full-size** yuklab oladi
- Originalni baytma-bayt saqlaydi va SHA-256 bilan tekshiradi: haqiqiy format, dekodlanish, o'lcham, nisbat, shaffoflik. HTML yoki JSON xato sahifalarini rad etadi
- Hosila fayllarni alohida yaratadi: platforma o'lchamlari (cho'zmasdan), aniq matn (oʻ, gʻ va kirill glif tekshiruvi, kontrast, chetdan masofa), logo, shaffof fon, format, variantlar varag'i
- `manifest.json` original, preview va tahrirlarni bog'lab turadi va yetkazish bosqichlarini halol qayd etadi
- Xavfsizlik: faqat HTTPS, redirect va hajm limitlari, ichki tarmoqqa (SSRF) yo'l yo'q; sirlar loglarga tushmaydi; fayllar ustidan yozilmaydi; yashirin pulli generatsiya bo'lmaydi

## Loyiha rejimi (yangi)
Agent kod ustida ishlayotganda rasm kerak bo'lsa, skill quyidagilarni bajaradi:
1. Loyihani o'qiydi: AGENTS.md, framework, rasm papkalari, brend ranglari, dark mode, ikonalar kutubxonasi, build va typecheck buyruqlari, placeholder'lar.
2. Mos asset bor-yo'qligini reyestrdan tekshiradi. Oddiy ikonka kerak bo'lsa, loyihadagi kutubxonadan foydalanadi.
3. Originalni public bo'lmagan `.gemini-assets/originals/` papkasida saqlaydi. Production nusxalarni loyiha konvensiyasidagi papkaga chiqaradi: WebP, srcset kengliklari, upscale'siz, cho'zmasdan.
4. Assetni komponentga ulaydi: alt, o'lcham, `fetchpriority`, lazy-load, dark mode hisobga olinadi.
5. Tekshiradi: reyestr verify, build va typecheck, so'ng real brauzerda desktop va mobil, yorug' va qorong'i tema.

```bash
S=~/.claude/skills/gemini-image/scripts
python $S/project_scan.py .                       # kontekst
python $S/asset_export.py gen.png --project . --id hero-landing --ratio 16:9 --widths 640,1280,1920 --usage src/components/Hero.tsx
python $S/asset_registry.py verify . --strict      # ulangani va butunligi
python $S/render_check.py http://localhost:5173 --widths 1440,390
```

## O'rnatish

### Claude Code (tavsiya etiladi)
```bash
git clone https://github.com/ismoillooff/gemini-image ~/.claude/skills/gemini-image
# yoki faqat bitta loyiha uchun:  <loyiha>/.claude/skills/gemini-image
pip install -r ~/.claude/skills/gemini-image/requirements.txt
# ixtiyoriy, vizual tekshiruv uchun:  pip install playwright && python -m playwright install chromium
cp ~/.claude/skills/gemini-image/.env.example ~/.gemini-image.env   # kerakli limitlarni tahrirlang
echo 'export GEMINI_API_KEY="..."' >> ~/.zshrc                       # kalit: https://aistudio.google.com/apikey
```
Tekshirish: `cd ~/.claude/skills/gemini-image && python -m unittest discover -s tests` → `OK`.
Claude Code'da skill avtomatik ishga tushadi ("rasm yarat …") yoki `/gemini-image` deb chaqiriladi.

### claude.ai / Claude Desktop
`gemini-image.skill` faylini *Settings → Capabilities → Skills* bo'limiga yuklang
(yoki chatdagi **Save skill** tugmasini bosing). Chrome'da **Claude in Chrome**
kengaytmasi bo'lishi va Google akkauntga kirilgan bo'lishi kerak. Desktop ilovada
chat kompyuterga ulansa, original avtomatik keladi. Web'da esa yuklab olingan
originalni chatga o'zingiz tashlaysiz. Batafsil: `references/environments.md`.

## Misollar
1. **Yangi rasm:** "Kofe do'konim uchun Instagram post yarat, ustida 'Ertalabki qahva — 8:00 dan' yozuvi bo'lsin."
   → prompt yoziladi → original yaratiladi → tekshiriladi → `ig-post` o'lchamiga keltiriladi → matn qo'shiladi → chatda ko'rsatiladi va original hamda post fayli beriladi.
2. **Reference asosida tahrir:** "Shu mahsulot rasmidagi fonni oq studiyaga almashtir, mahsulotning o'zi o'zgarmasin."
   → rasm `--origin attachment` bilan qabul qilinadi → nimalar saqlanishi va nimalar o'zgarishi prompt'da aniq yoziladi → oldin va keyin yonma-yon ko'rsatiladi; original o'zgarmaydi.
3. **Gemini havolasidan original:** "Bu Gemini havolasidagi rasmni chatda ko'rsat va originalini ber."
   → sahifa ochiladi → rasm tanlanadi → "Download full-sized image" bosiladi → fayl fingerprint bilan tekshiriladi → chatda ko'rsatiladi va original biriktiriladi.
   Shu bilan birga: "Bu suhbatdagi barcha rasmlarni alohida ko'rsat" (ZIP bilan) va "Rasmni bannerga mosla, originalini ham saqla" (`resize --mode extend`).
4. **Loyiha rejimi:** "Landing page uchun brend ranglariga mos hero illustratsiya yarat, asset papkasiga saqla, hero komponentiga ulagin va mobil hamda desktop ko'rinishini tekshir."
   → scan → find → Gemini → `asset_export` → komponentga ulash → `verify --strict` → build/typecheck → `render_check` (1440 va 390, light va dark) → hisobot.

## Tuzilma
```
SKILL.md                      ish jarayoni va qat'iy qoidalar
references/environments.md    Claude Code, web va Desktop'da nima ishlashi
references/browser-route.md   Gemini sayti: yaratish, tanlash, yuklab olish
references/api-route.md       Gemini API, xatolar, xarajat
references/prompt-guide.md    prompt yozish tamoyillari
templates/brief.md            brief shabloni
templates/prompts.md          6 ta prompt shabloni
templates/brand.example.json  brend konfiguratsiyasi
scripts/imagekit.py           umumiy kutubxona (tekshiruv, hash, manifest, xavfsiz yuklash)
scripts/generate_image.py     API orqali yaratish va tahrirlash
scripts/fetch_image.py        URL yoki fayldan originalni qabul qilish
scripts/check_image.py        fayllarni tekshirish (--ratio --min --alpha --sha256 --match)
scripts/postprocess.py        resize, text, logo, cutout, convert, sheet
scripts/deliver.py            preview, ZIP, yetkazish hisoboti, bosqichlar
scripts/browser_snippets.js   Gemini sahifasi va loyiha sahifasini tekshirish uchun JS
scripts/project_scan.py       loyiha konteksti va komponentdagi rasm slotlari
scripts/asset_export.py       original va production nusxalarni ajratish, reyestrga yozish
scripts/asset_registry.py     find / list / usage / verify
scripts/render_check.py       desktop va mobil, light va dark render hamda rasm muammolari
references/project-integration.md  framework bo'yicha ulash retseptlari, a11y, loading, dark mode
tests/                        54 ta test (53 ta oflayn va 1 ta real Chromium) + qo'lda bajariladigan tekshiruvlar
```

## Cheklovlar
`tests/MANUAL_CHECKS.md` va `references/environments.md` ga qarang. Asosiylari:
claude.ai web sandbox'i Google'ga ulana olmaydi; Gemini UI o'zgarishi mumkin;
Gemini piksel maskasi bilan tahrir qilmaydi; kontrast va glif tekshiruvlari
yordamchi vosita, imlo xatolarini aniqlash uchun OCR yo'q.
