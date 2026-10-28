# Environments — what actually works where

Checked 2026-10-28. Re-check when something fails; don't promise features listed
as "no".

| Capability | Claude Code (terminal) | claude.ai web | Claude Desktop app |
|---|---|---|---|
| Gemini API (`generate_image.py`) | **yes** if `GEMINI_API_KEY` set and Google reachable | **no** — sandbox blocks Google hosts (HTTP 403 from proxy) | runs in the same cloud sandbox as web → **no**, unless a local shell tool is linked |
| Fetch arbitrary image URL (`fetch_image.py URL`) | yes | **no** for most hosts (allowlist: package registries) | as web |
| Drive Gemini website (Claude in Chrome) | possible if the Chrome integration is connected | **yes** with the extension | **yes** with the extension |
| Click Gemini's "Download full-sized image" | via Chrome integration | yes → file lands in the user's **Downloads** (user's computer) | same |
| Read that downloaded file | yes (local disk) | **no** — Downloads is on the user's computer, not the sandbox | **yes** when the chat is linked to the computer (`mcp__remote-devices__*` file tools) |
| Show an image in chat | **no** (terminal) — give the path | yes: SendUserFile `display: "render"` | yes |
| Original as a downloadable file | path on disk | yes: SendUserFile | yes |
| Inline image from an external URL | n/a | not relied on: Gemini image URLs are signed, short-lived and account-bound; blob: URLs only exist in that tab | same |
| Python + Pillow | install with pip | preinstalled | same sandbox |
| npm install (real framework builds) | yes | **no** — npm registry unreachable from the sandbox (checked 2026-10-28) | same sandbox |
| Headless render check (`render_check.py`) | if Playwright + Chromium installed | **yes** — Playwright Chromium present; wkhtmltoimage too | same sandbox |

## Consequences per environment

**Claude Code** — best route. API bytes are the original, saved straight into the
project. No inline images: report paths. For the browser route, Claude Code needs
its Chrome integration; then the download lands in Downloads, which is local, so
`fetch_image.py ~/Downloads/<file> --origin gemini-download` works directly.

**claude.ai web** — generate in Gemini through Chrome; click download (with the
user's OK). The original is then in the user's Downloads but NOT reachable from
the sandbox. Honest options, in this order:
1. Ask the user to drag the downloaded file into the chat → `fetch_image.py <attachment> --origin attachment` → verify → deliver (shown in chat + attached). Use browser snippet 3's `ahash` with `check_image.py --match` to confirm it's the right image.
2. Or switch to the Desktop app with the computer linked (fully automatic).
3. Until then, a screenshot may be shown for review, ingested with `--origin screenshot` and labelled "screenshot — not the original".

**Claude Desktop with the computer linked** — after the download, list the
Downloads folder via the bridge, pick the newest image file created after the
click (confirm with `check_image.py --match <ahash>`), stage it into the
workspace, then `fetch_image.py <staged file> --origin gemini-download` →
`deliver.py`. Fully automatic and lossless.

## Signals to detect the environment

- `GEMINI_API_KEY` present and `curl -sS -o /dev/null -w '%{http_code}' https://generativelanguage.googleapis.com/` not `000` → API usable.
- `mcp__claude-in-chrome__*` tools (or `enable__mcp__claude-in-chrome`) present → browser route.
- `mcp__remote-devices__*` file tools present → user's computer reachable.
- `SendUserFile` present → can show/attach files in chat. No such tool and a terminal → Claude Code: paths only.
