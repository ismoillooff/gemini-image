// Snippets for the Claude in Chrome `javascript_tool` on gemini.google.com
// (chat pages /app/… and public share pages /share/… or g.co/gemini/share/…).
// Read this file, then pass ONE snippet's body as `text`. Each returns small JSON.
// Never return or log full image URLs: googleusercontent paths are access tokens.

// ── 1. LIST CANDIDATE IMAGES (generated images only, oldest → newest) ───────
// Excludes avatars, icons and thumbnails. `n` is 1-based, as shown to the user.
(() => {
  // Size + URL pattern only: Gemini's <body> classes match generic 'avatar'/'nav' selectors.
  const isAvatar = i => /\/(a|ogw)\/|=s\d{2,3}-c|=w\d{2,3}-h\d{2,3}/.test(i.currentSrc || i.src);
  const list = [...document.images]
    .filter(i => i.complete && i.naturalWidth >= 256 && i.naturalHeight >= 256 && !isAvatar(i))
    .map(i => {
      const r = i.getBoundingClientRect();
      return { w: i.naturalWidth, h: i.naturalHeight, alt: (i.alt || '').slice(0, 60),
               visible: r.width > 0 && r.height > 0,
               rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
               src: (i.currentSrc || i.src).startsWith('blob:') ? 'blob' : 'url' };
    });
  return { page: location.pathname.split('/').slice(0, 2).join('/'), count: list.length,
           images: list.map((x, k) => ({ n: k + 1, ...x })) };
})();

// ── 2. FIND THE "DOWNLOAD FULL-SIZED IMAGE" BUTTON FOR IMAGE n ──────────────
// Set N (1-based; -1 = newest). Hovers are sometimes needed before the button
// appears: run, and if found=false, `hover` the image's rect centre and rerun.
(() => {
  const N = -1;
  const imgs = [...document.images].filter(i => i.complete && i.naturalWidth >= 256 && !/\/(a|ogw)\//.test(i.src));
  const img = N < 0 ? imgs.at(N) : imgs[N - 1];
  if (!img) return { found: false, reason: 'no such image' };
  img.scrollIntoView({ block: 'center' });
  let box = img.parentElement;
  for (let k = 0; k < 8 && box; k++, box = box.parentElement) {
    const btn = [...box.querySelectorAll('button[aria-label], a[download]')]
      .find(b => /download/i.test(b.getAttribute('aria-label') || '') || b.hasAttribute('download'));
    if (btn) {
      const r = btn.getBoundingClientRect();
      return { found: true, label: btn.getAttribute('aria-label'),
               center: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)],
               visible: r.width > 0 };
    }
  }
  const r = img.getBoundingClientRect();
  return { found: false, hover_here: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)] };
})();

// ── 3. VISUAL FINGERPRINT OF IMAGE n (average hash) ─────────────────────────
// 64-bit aHash of what the page shows — same algorithm as imagekit.ahash().
// Use it to confirm which file in Downloads is this image:
//   check_image.py candidates*.png --match <ahash>   (distance <= 10 = same image)
// (Fetching the blob bytes fails once Gemini revokes the blob URL; the canvas
// route works because the image is same-origin.)
(() => {
  const N = -1;
  const imgs = [...document.images].filter(i => i.complete && i.naturalWidth >= 256 && !/\/(a|ogw)\//.test(i.src));
  const img = N < 0 ? imgs.at(N) : imgs[N - 1];
  if (!img) return { error: 'no such image' };
  const c = document.createElement('canvas'); c.width = 8; c.height = 8;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(img, 0, 0, 8, 8);
  let d;
  try { d = ctx.getImageData(0, 0, 8, 8).data; } catch (e) { return { error: 'canvas tainted: ' + e.name }; }
  const g = []; for (let k = 0; k < 64; k++) g.push(0.299 * d[4*k] + 0.587 * d[4*k+1] + 0.114 * d[4*k+2]);
  const avg = g.reduce((a, b) => a + b, 0) / 64;
  let bits = ''; for (const v of g) bits += v > avg ? '1' : '0';
  return { preview_w: img.naturalWidth, preview_h: img.naturalHeight,
           ahash: BigInt('0b' + bits).toString(16).padStart(16, '0') };
})();

// ── 4. GENERATION / PAGE STATE ──────────────────────────────────────────────
// busy = a VISIBLE stop button or spinner (hidden sidebar spinners are ignored).
({ busy: [...document.querySelectorAll('[aria-label*="Stop" i], mat-progress-spinner, [role="progressbar"]')]
     .some(e => e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== 'hidden'
       && !/conversations|gems/i.test(e.getAttribute('aria-label') || '')),
   images: [...document.images].filter(i => i.complete && i.naturalWidth >= 256 && !/\/(a|ogw)\//.test(i.src)).length,
   signin_needed: /accounts\.google\.com|ServiceLogin/.test(location.href)
     || !!document.querySelector('a[href*="ServiceLogin"], a[href*="accounts.google.com/signin"]'),
   limit_message: /reached your limit|quota|try again later|limitga yetdingiz|лимит/i
     .test((document.body.innerText || '').slice(-5000)),
   not_found: /not found|doesn't exist|no longer available|mavjud emas/i.test(document.title + ' ' + (document.body.innerText || '').slice(0, 800)) });

// ── 5. PREVIEW OVERLAY (composition check only — NEVER the delivered original) ──
(() => {
  const N = -1;
  const imgs = [...document.images].filter(i => i.complete && i.naturalWidth >= 256 && !/\/(a|ogw)\//.test(i.src));
  const src = N < 0 ? imgs.at(N) : imgs[N - 1];
  const ov = document.createElement('div');
  ov.id = '__claude_overlay';
  ov.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#111;display:flex;align-items:center;justify-content:center;cursor:none';
  const im = document.createElement('img');
  im.src = src.currentSrc || src.src;
  const s = Math.min(1 / devicePixelRatio, (innerWidth - 8) / src.naturalWidth, (innerHeight - 8) / src.naturalHeight);
  im.style.cssText = `width:${src.naturalWidth * s}px;height:${src.naturalHeight * s}px`;
  ov.appendChild(im);
  document.body.appendChild(ov);
  const r = im.getBoundingClientRect();
  return { zoom_region: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)] };
})();

// ── 6. REMOVE OVERLAY ───────────────────────────────────────────────────────
(document.getElementById('__claude_overlay')?.remove(), 'removed');

// ── 7. VERIFY IMAGES ON A PROJECT PAGE (dev server / preview) ───────────────
// Run on the app page at desktop size, then again after resize_window to ~390x844.
// Reports every <img>: loaded?, real file pixels vs rendered device pixels (upscaling),
// distortion (rendered ratio ≠ file ratio without object-fit), lazy-loading above the
// fold, missing alt, missing width/height (layout-shift risk), cumulative layout shift.
// Note: with srcset, img.naturalWidth is density-corrected, so the chosen file is
// re-loaded to read its true size. Keep the leading `await`.
await (async () => {
  const vh = innerHeight, dpr = devicePixelRatio;
  const fileSize = src => new Promise(res => { const t = new Image(); t.onload = () => res([t.naturalWidth, t.naturalHeight]);
    t.onerror = () => res([0, 0]); t.src = src; });
  const rows = [];
  for (const i of document.images) {
    const r = i.getBoundingClientRect(), cs = getComputedStyle(i);
    const [fw, fh] = i.currentSrc || i.src ? await fileSize(i.currentSrc || i.src) : [0, 0];
    const issues = [];
    if (!i.complete || i.naturalWidth === 0 || fw === 0) issues.push('NOT LOADED');
    if (fw && r.width * dpr > fw * 1.15) issues.push(`upscaled: file ${fw}px shown at ${Math.round(r.width * dpr)} device px`);
    const nr = fw / (fh || 1), rr = r.width / (r.height || 1);
    if (fw && r.height > 0 && Math.abs(nr - rr) / nr > 0.03 && !['cover', 'contain', 'scale-down'].includes(cs.objectFit)) issues.push('distorted (no object-fit)');
    if (r.top < vh && i.loading === 'lazy') issues.push('lazy above the fold');
    if (!i.hasAttribute('alt')) issues.push('no alt attribute');
    if (!i.getAttribute('width') && !i.getAttribute('height') && !cs.aspectRatio.includes('/')) issues.push('no size/aspect → CLS risk');
    rows.push({ src: (i.currentSrc || i.src).split('/').slice(-2).join('/').slice(0, 80), alt: i.alt?.slice(0, 50),
                file: [fw, fh], rendered: [Math.round(r.width), Math.round(r.height)], objectFit: cs.objectFit,
                inViewport: r.top < vh && r.bottom > 0, issues });
  }
  const cls = performance.getEntriesByType('layout-shift').reduce((s, e) => s + (e.hadRecentInput ? 0 : e.value), 0);
  return { viewport: [innerWidth, innerHeight], dpr, images: rows, layoutShift: +cls.toFixed(4),
           broken: rows.filter(r => r.issues.includes('NOT LOADED')).length };
})();

// ── 8. FOCUS THE GEMINI INPUT (before typing) ───────────────────────────────
// Clicking by ref/coordinates sometimes leaves focus elsewhere and the typed text
// is lost (seen live 2026-10-28). Focus the editor directly, then `type`, then
// run snippet 9 BEFORE pressing Enter.
(() => {
  const el = document.querySelector('rich-textarea [contenteditable="true"], .ql-editor[contenteditable="true"], [contenteditable="true"][role="textbox"]');
  el?.focus();
  return { focused: !!el && document.activeElement === el, queries_before: document.querySelectorAll('user-query').length };
})();

// ── 9. VERIFY TEXT IS IN THE INPUT / MESSAGE WAS SENT ───────────────────────
// Before Enter: `inputText` must start with your prompt. After Enter + a few
// seconds: `queries` must be queries_before + 1, otherwise the message was not sent.
({ inputText: (document.querySelector('rich-textarea, [contenteditable="true"]')?.innerText || '').slice(0, 60),
   queries: document.querySelectorAll('user-query').length });
