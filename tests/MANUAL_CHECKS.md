# Checks against real services (not automated)

`python -m unittest discover -s tests` covers everything that can run offline
(local fixture server + mocked API). These need a real account, network or UI:

| # | Check | Status (2026-10-28) |
|---|---|---|
| R1 | Snippets 1, 2, 4 on a real Gemini chat page: lists the generated image only, finds "Download full-sized image", state not busy | **passed** (1 image, 1024×572 preview) |
| R2 | Snippet 3 fingerprint vs Python `ahash` of a capture of the same image | **passed** (distance 5/64) |
| R3 | Snippet 3 blob-byte variant | **failed → replaced**: blob revoked (`Failed to fetch`); canvas fingerprint used instead |
| R4 | Clicking "Download full-sized image" and reading the file from Downloads (Desktop link) | **not run** — needs the user's OK and a linked Desktop app |
| R5 | Real Gemini **share** page with several images | **not run** — needs a share link |
| R6 | `generate_image.py` against the real API | **not run** — no API key; this sandbox cannot reach Google (proxy 403) |
| R7 | `fetch_image.py` against a real public HTTPS image URL | **not run** — sandbox allowlist blocks most hosts; run in Claude Code |
| R8 | Inline display of a delivered PNG in claude.ai | earlier SendUserFile renders were seen by the user; Claude cannot observe the UI itself |
| R9 | Project mode end-to-end on a static landing project: scan → export → wire hero → `verify --strict` → Chromium render 1440/390 light/dark | **passed**; render found a pre-existing dark-mode contrast bug in the demo page's heading (not caused by the asset) |
| R10 | Same flow on a real Vite/Next project with `npm run build` | **not run** — npm registry unreachable from this sandbox; framework detection and wiring covered by fixture tests only |
| R11 | Product photo (earbuds) via brief.py → Gemini web → QA found mismatched earbud designs → one targeted edit → `qa.py compare` | **passed**: difference map shows change only in the right earbud; 4:5 delivered as 825×1024 preview |
| R12 | Brand hero illustration (NyroSoft palette) with headline space → exact Uzbek headline added as a layer → responsive crop sheet | **passed with issues**: stray "AI" label (prompt wording; lint rule added), 9:16 crop cuts right-side devices |
| R13 | Custom icon → cutout to real alpha → `qa.py icon` at 16–64 px on light/dark/brand | **passed with issues**: clear at 24 px+, sync arrows merge at 16 px; extra outline colour |
| R14 | Reference fix on NyroSoft HQ (sign perspective/mounting) in the same Gemini chat | **improved**: sign on a solid band, halo-lit, logo/spelling kept, change confined to sign; top of "Soft" still touches a louvre; budget (standard, 4) reached |
| R15 | Gemini input focus: click by ref/coordinates lost the typed text twice; JS focus (snippet 8) + check (snippet 9) worked every time | **fixed in skill** |

All R11–R14 images are page previews (screenshots, ~1024 px), recorded with `--origin screenshot`; full-size originals were not downloaded (no permission given in chat).

How to run R4–R7:
- R4: Desktop app, chat linked to the computer, ask "Download image 1 from this Gemini chat and show the original".
- R5: send a `gemini.google.com/share/…` link with 2+ images.
- R6: `GEMINI_API_KEY=… python scripts/generate_image.py "a red apple on white" --workdir /tmp/t` then `python scripts/check_image.py /tmp/t/originals/*`.
- R7: `python scripts/fetch_image.py https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png --workdir /tmp/t --alpha`.
