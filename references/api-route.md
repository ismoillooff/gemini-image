# API route — `scripts/generate_image.py`

For Claude Code or any shell that can reach `generativelanguage.googleapis.com`.

## Setup

```bash
pip install -r "${CLAUDE_SKILL_DIR}/requirements.txt"     # Pillow
export GEMINI_API_KEY=...        # from https://aistudio.google.com/apikey, in your shell profile
```
Never paste the key into a chat, a commit or `.env.example`. Usage of image
models may be billed by Google — check pricing and quotas in AI Studio.

## Calls

```bash
S="${CLAUDE_SKILL_DIR}/scripts"
python "$S/generate_image.py" "<prompt>" --aspect 4:5 --workdir gemini-out/post --name cafe-post
python "$S/generate_image.py" "<prompt>" --aspect 16:9 -n 3 --workdir gemini-out/hero --name hero   # 3 billed calls
python "$S/generate_image.py" "<edit>" --input gemini-out/post/originals/cafe-post.png --workdir gemini-out/post --name cafe-post-v2
python "$S/generate_image.py" "<prompt>" --size 2K --mode final --workdir …      # standard tier at 2K, budgeted
python "$S/generate_image.py" "<prompt>" --quality pro --size 2K --workdir …   # pro tier, only when the user agrees
python "$S/generate_image.py" "<prompt>" --dry-run                              # inspect request only
```

Request shape (Gemini API `generateContent`): `contents[].parts` with the text and
optional `inline_data` images; `generationConfig.responseModalities: ["TEXT","IMAGE"]`;
`generationConfig.imageConfig.aspectRatio` and
`imageConfig.imageSize` (`512`/`1K`/`2K`/`4K`, uppercase K, per model). Tiers
(`--quality`): **lite** `gemini-3.1-flash-lite-image` (1K only) → legacy
`gemini-2.5-flash-image`; **standard** (default) `gemini-nano-banana-2.1` →
`gemini-3.1-flash-image`; **pro** `gemini-3-pro-image` (only when asked). Unsupported
size/aspect for a model is not sent (a note is printed); the delivered file's real
size is checked against the request. Details: `references/model-capabilities.json`.
`--mode draft|standard|final` enforces the generation budget per workdir.

## Output

JSON on stdout: saved files with id, size, check result. Originals in
`<workdir>/originals/`, recorded in `manifest.json` with prompt, model, aspect,
`response_id`, sha256 and stage states.

## Errors

| Exit / message | Meaning | Do |
|---|---|---|
| 5 `timeout` | no answer in time; may still be billed | don't resend; ask the user |
| `429` after retries | quota/rate limit | wait; tell the user |
| `401/403` | key invalid / no access to model | user fixes key or billing |
| 6 `no_image` | blocked or text-only answer | read `gemini_note`; rephrase honestly or tell the user |
| `-n exceeds GEMINI_IMAGE_MAX_PER_RUN` | cost guard | raise the limit only if the user wants |
