# Security review

Scope: everything in this repo as of the commit that adds this file: the vendored `watch` skill (upstream `bradautomates/claude-video` at `03ceb42`, plus local patches), the `content-evaluator` skill, `scripts/preflight.py`, the SessionStart hook, and pinned dependencies.

Method: manual reading of every vendored Python file before running it (the upstream clone was treated as untrusted and never executed in place), `bandit` static analysis, `pip-audit` against the pinned requirements, an OSV cross-check of older yt-dlp releases, and behavioral tests with the real yt-dlp binary. Review date: 2026-10-10.

Revision 2 (same day): an independent red-team review of revision 1 found the issues listed as R1 to R10 below. All were fixed with regression tests; each fix is a numbered local patch in `.claude/skills/watch/UPSTREAM.md` or a change to this repo's own files.

## Summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| S1 | Upstream does not enforce a minimum yt-dlp version; several 2026 yt-dlp CVEs allow command execution or file writes | High | **Fixed** (patch 1: refuse < 2026.07.04) |
| S2 | Upstream honors user/system yt-dlp config files, which can re-enable `--exec`, aria2c/curl external downloaders, `--netrc-cmd`, plugins | High | **Fixed** (patch 2: `--ignore-config --no-plugin-dirs --no-exec` + argv deny-list) |
| S3 | Optional WhisperX setup downloads and runs an unpinned remote installer script (`astral.sh/uv/install.sh`, `install.ps1`) | Medium | **Guarded** (patch 3: requires `WATCH_ALLOW_REMOTE_INSTALLER=1`); do not enable without review |
| S4 | Proxy CONNECT refusals were diagnosed as "update yt-dlp" | Low | **Fixed** (patch 4) |
| S5 | Gemini engine is auto-selected whenever a `GEMINI_API_KEY` resolves, including from a `.env` in the current directory; local or downloaded videos are then uploaded to Google | Medium | **Mitigated** in `content-evaluator` (always passes `--engine local`; Gemini only after asking). Not patched in `watch` |
| S6 | Video frames, speech, captions, titles, descriptions and Gemini answers can contain prompt-injection text | Medium | **Mitigated** by skill instructions; residual |
| S7 | Cookie options give yt-dlp a read/write cookie jar or browser profile access | Medium | Opt-in only; documented; residual |
| S8 | Downloading platform videos may breach terms of service or copyright | Policy | Documented; residual |
| S9 | Dependencies pinned by version but not by hash | Low | Wheels-only install; `pip-audit` clean; residual |
| S10 | yt-dlp and ffmpeg parse untrusted remote content | Medium | Pinned current yt-dlp; system ffmpeg is the distro package; residual |
| S11 | Downloaded media and frames persist in temp work dirs until deleted | Low | Skill tells Claude to delete its own work dirs (after a pattern check, R1); `.gitignore` excludes media; residual |
| R1 (H1) | Report forgery: raw titles, uploaders, transcripts and Gemini answers could inject fake `## Frames` entries (e.g. `/root/.ssh/id_rsa`) and a fake `Work dir: /home/user` cleanup line | High | **Fixed** (patch 6 + skill rules: read only `.jpg` frames inside the Work dir, delete only a Work dir matching `watch-XXXXXXXX`) |
| R2 (H2) | Unbounded downloads: endless or live media filled the disk; yt-dlp outlived a killed parent | High | **Fixed** (patch 7: socket timeout, size/live/duration filters, timeout + on-disk byte cap with process-group kill, child dies with parent on Linux) |
| R3 (M1) = S5b | Silent cloud audio upload: with `--engine local`, `whisper` backend `auto` uploads audio to Groq/OpenAI if a key resolves from env, `~/.config/watch/.env` or `./.env` | Medium | **Mitigated** in `content-evaluator` (always `--no-whisper`; asks before `--whisper groq|openai`). Not changed in `watch` itself |
| R4 (M2) | SSRF: loopback/private/link-local/reserved hosts accepted; `no_proxy` sends them around the proxy | Medium | **Fixed** (patch 8: refused unless `--allow-private-network`); redirect/rebinding residual |
| R5 (M3) | Plugin loading in probes: `-v` and setup probes lacked `--no-plugin-dirs`; a planted plugin executed | Medium | **Fixed** (patch 9: single `ytdlp_argv` helper; AST test) |
| R6 (M4) | Token budget: up to 100+ frames at 768 px per video | Medium | **Fixed** in evaluator (`--max-frames 40`, per-batch budget table) |
| R7 (M5) | README allowlist missed hosts (`api.vimeo.com`, `*.twimg.com`, `lookaside.fbsbx.com`, `*.vimeocdn.com`, ...) | Low | **Fixed** (README; extractor sources grepped) |
| R8 (L1, L2) | SessionStart hook could exit 1 on mkdir/venv failure; no hook timeout | Low | **Fixed** (ERR trap exits 0 with a note; `timeout: 300`) |
| R9 (L3, L4) | Rubric contradictions (evidence vs. unobservable, confidence, rounding, brand provisional); `metadata.py` recursive glob from any dir | Low | **Fixed** |
| R10 (L7) | Skill text told Claude to update yt-dlp on 403 | Low | **Fixed** (never self-update; updates via `requirements.txt`) |

## Known yt-dlp vulnerabilities

| Advisory | Issue | Fixed in | Relevance here |
|---|---|---|---|
| CVE-2026-55404 / GHSA-6v4j-43gg-vj32 / PYSEC-2026-3622 | Command injection through `.url`/`.desktop` shortcut files written by `--write-link`, `--write-url-link`, `--write-desktop-link` | 2026.07.04 | Floor set to 2026.07.04; those options are also refused |
| CVE-2026-50574 / GHSA-vx4q-3cr2-7cg2 | aria2c argument injection on fragmented formats (RCE) | 2026.06.09 | Below floor refused; external downloaders refused; config ignored |
| CVE-2026-50023 / GHSA-c6mh-fpjc-4pr3 | Arbitrary OS shortcut file write | 2026.06.09 | Below floor refused |
| CVE-2026-50019 / GHSA-f7j3-774f-rfhj | Cookie leak to other hosts with the curl external downloader | 2026.06.09 | Below floor refused; external downloaders refused |
| GHSA-69qj-pvh9-c5wg | `--exec` command injection from metadata templates | 2026.06.09 | Below floor refused; `--exec` refused and `--no-exec` always passed |
| CVE-2026-26331 / GHSA-g3gw-q23r-pgqm / PYSEC-2026-3432 | `--netrc-cmd` command injection | 2026.02.21 | Below floor refused; `--netrc-cmd` refused; config ignored |
| CVE-2025-54072 | `--exec` injection on Windows | Before 2026 (per briefing; not re-verified) | Below floor refused; `--exec` refused |

The briefing for this work named 2026.06.09 as the floor. Running `pip-audit` against `yt-dlp==2026.6.9` returned CVE-2026-55404 (fixed in 2026.07.04), so the floor was raised to 2026.07.04. `pip-audit` against `yt-dlp==2026.3.17` and `yt-dlp==2025.6.30` listed every advisory above except CVE-2025-54072, which cross-checks those IDs and fix versions. CVE-2025-54072 comes from the briefing; OSV did not report it for 2025.6.30 and it was not independently re-verified here (any version it affects is far below the floor).

## Details

### S1, S2: yt-dlp hardening

Upstream builds yt-dlp argv as Python lists (no shell), validates URLs (`http`/`https` only, no leading `-`), and places the URL after `--`, which blocks argument injection through the URL itself. It did not control the yt-dlp version or configuration. A user or system `yt-dlp.conf` with `--downloader aria2c`, `--exec ...` or `--netrc-cmd ...` would have applied to every watch call, which is exactly the trigger condition of the CVEs above.

Patches (details and tests in `.claude/skills/watch/UPSTREAM.md`):

- Version floor checked before the first yt-dlp call in each process. Unknown version strings fail closed.
- `--ignore-config --no-plugin-dirs --no-exec` on every invocation, verified with the real binary: a hostile user config with `--exec "touch MARKER"` runs in a control invocation and does not run through watch.
- Deny-list of options that run programs, hand off to external downloaders, load more config or plugins, self-update, or write shortcut files.
- `file://` URLs stay disabled (yt-dlp's default; watch never passes `--enable-file-urls`). Verified by test.

Tradeoff accepted: proxy settings must come from environment variables, and CA settings from the environment or yt-dlp defaults, not from yt-dlp config files. Verified in this container: a real download from `raw.githubusercontent.com` through the intercepting proxy works with `--ignore-config`.

### S3: WhisperX installer path (do not enable without review)

`setup.py --backend whisperx` (or `--install-whisperx`) on Linux and Windows bootstraps `uv` from `https://astral.sh/uv/install.sh` / `install.ps1` and executes it, then creates a Python 3.12 venv in `~/.cache/watch/whisperx-venv`, installs `torch==2.8.0`, `torchaudio==2.8.0` (from the PyTorch CPU index) and `whisperx==3.8.6` without hashes, and downloads models from Hugging Face. None of this was run or tested here (Hugging Face is blocked in this container). The remote-script step now needs `WATCH_ALLOW_REMOTE_INSTALLER=1`; the safer alternative is to install `uv` from a package manager first. Treat the whole WhisperX path as unreviewed: it pulls about 1.5 GB of third-party code and models. The `content-evaluator` skill never selects it.

### S5: Gemini engine data flow

With the Gemini engine, YouTube URLs are sent to Google and other videos are downloaded locally and uploaded to the Gemini Files API, then deleted (or expire after 48 hours). `engine=auto` picks Gemini whenever a key resolves from the environment, `~/.config/watch/.env`, or `./.env`. A `.env` in an untrusted working directory could therefore route a user's local video to a third party's Google project. The `content-evaluator` skill always passes `--engine local` and asks before using Gemini. The key is sent only as an `x-goog-api-key` header and redacted from errors (upstream code, read and confirmed). Gemini answers are treated as untrusted evidence.

### S5b (R3): Silent cloud transcription upload

`watch` picks a transcription fallback automatically (`WATCH_WHISPER_BACKEND=auto`) when a video has no captions: it uses a `GROQ_API_KEY` or `OPENAI_API_KEY` found in the environment, `~/.config/watch/.env` or `./.env`, and uploads extracted audio to that provider, even with `--engine local`. As with S5, a `.env` in an untrusted working directory could send audio to a third party. The `content-evaluator` skill always passes `--no-whisper` and asks the user before enabling `--whisper groq|openai` for a specific video. Direct use of `watch` without `--no-whisper` keeps upstream behavior (residual).

### R1: Report forgery

Creator-controlled strings were printed verbatim into a Markdown report that Claude then acts on (open frames, delete the work dir). Now every such string is either a single sanitized line labeled `(untrusted)` or sits inside an `<untrusted-...>` fenced block that its content cannot close. Independently, both skills tell Claude to read only `.jpg` paths inside the run's Work dir `frames/` and to delete only a Work dir whose name matches the `mkdtemp` pattern inside the expected parent. Residual: the model still reads the untrusted text; see S6.

### R2: Download bounds

yt-dlp's `--max-filesize` is not enforced for responses without `Content-Length` (reproduced: 16.6 GB in 60 s from a local endless server). Enforcement therefore happens outside yt-dlp: `run_bounded` polls the run directory every 0.2 s and kills the process group past the byte cap (default 500 MB; overshoot is bounded by about 0.2 s of transfer), and enforces a wall-clock timeout (default 900 s for media, 180 s for metadata). `--match-filter` and a metadata check refuse live/upcoming streams and videos over 1800 s. Residual: `PR_SET_PDEATHSIG` is Linux-only; on macOS/Windows a SIGKILLed watch leaves yt-dlp running until its own socket timeout or completion. Windows uses `process.kill()` (no process-group kill; untested). `--download-sections` is not used because it would shift frame timestamps.

### R4: SSRF

Before yt-dlp runs, the URL's host is resolved and every address must be global. Residual risks: an allowed public URL can redirect to a private address (yt-dlp follows redirects); DNS can change between our check and yt-dlp's resolution (rebinding); names that only a proxy can resolve are not checked locally. Cloud environments with an egress proxy reduce but do not remove these.

### R5: Plugins in probes

All yt-dlp invocations, including diagnostics, are built by `ytdlp_argv`, which always adds `--ignore-config --no-plugin-dirs --no-exec`. Verified with a real planted plugin. Plugins installed as `yt_dlp_plugins` packages inside the yt-dlp venv itself are outside this control (treat the venv as trusted; it is built from pinned requirements).

### S6: Prompt injection from video content

Anything inside a video (burned-in text, speech, captions, title, description, tags) is attacker-controllable. Both skills instruct Claude to treat it as data, never instructions, to report manipulation attempts under Risk, and to never run commands suggested by content. `metadata.py` labels description and tags as untrusted and truncates them. Residual risk: instruction-following models can still be influenced by persuasive content; reviewers should sanity-check scores that look out of line with the evidence.

### S7: Cookies

`--cookies FILE` gives yt-dlp a read/write jar; `--cookies-from-browser` reads a browser profile. Both are opt-in per run (or via `WATCH_COOKIES_*` settings). Cookies are sent to the platform and its CDNs; with external downloaders refused, the CVE-2026-50019 leak path does not apply. Use a throwaway account's cookies if any. The evaluator skill never adds cookie options on its own.

### S8: Terms of service and copyright

Most platforms' terms restrict downloading. The skills download only to analyze, keep files in temporary work dirs, and tell Claude to delete them afterwards. A **Post** verdict for third-party content means "share with permission and credit", never re-upload. Music and footage rights are scored under Risk. This is not legal advice.

### S9: Supply chain

`requirements.txt` pins yt-dlp and all transitive dependencies by exact version; the hook and README install with `--only-binary=:all:` (no sdist build scripts run). Hash pinning was not added because wheels differ per platform and no lock tool was available; this is a residual risk. Deno comes from the official `deno` PyPI wheel. The SessionStart hook prepends the venv's `bin/` to `PATH`, so its `python3`, `yt-dlp`, `deno` and `pytest` shadow system ones for the session.

### S10: Parsers of untrusted input

yt-dlp extractors, ffmpeg/ffprobe demuxers and decoders, and Deno (running YouTube challenge scripts via yt-dlp-ejs) all process remote content. Keep yt-dlp at or above the floor, keep the distro ffmpeg updated (6.1.1 in this container), and prefer running in a sandboxed environment such as a cloud session.

## Static analysis: bandit 1.9.4

Command: `bandit -r .claude scripts` and `bandit -r tests`.

| Scope | LOC | High | Medium | Low | Notes |
|---|---|---|---|---|---|
| Skills + scripts (`.claude`, `scripts`) | 2983 | 0 | 2 | 9 | Medium: B310 `urlopen` in `gemini.py:68` and `whisper.py:205`, both on hardcoded `https://` API endpoints (not user-controlled). Low: B404/B603 subprocess with argv lists, no `shell=True` (one more B603 than revision 1: `runtime.run_bounded`). 3 findings suppressed with `# nosec` in `scripts/preflight.py` (B404, B603 on fixed argv; B310 on a fixed `https://` scheme with a regex-validated hostname) |
| Tests (`tests`) | 2638 | 0 | 1 | 608 | Medium: B108 `/tmp` literal in upstream `test_gemini.py:58` (string fixture). Low: B101 asserts, subprocess in tests, one B105 (a fake key string used to prove keys never leak). A B613 (bidi control character) in a test string was fixed by using escape sequences |

No `eval`, `exec`, `pickle`, `yaml.load`, `shell=True`, or `verify=False` anywhere in the repo's Python (grep and bandit).

## Dependency audit: pip-audit 2.10.1

Re-run for revision 2 with the same results.

| Input | Result |
|---|---|
| `requirements.txt` (yt-dlp 2026.8.19 and 14 pinned dependencies) | No known vulnerabilities found |
| `requirements-dev.txt` (adds pytest 9.1.1 and its 4 dependencies) | No known vulnerabilities found |
| Cross-check: `yt-dlp==2026.6.9` | 1 advisory (CVE-2026-55404, fix 2026.7.4) |
| Cross-check: `yt-dlp==2026.3.17` | 5 distinct advisories (listed in the table above) |
| Cross-check: `yt-dlp==2025.6.30` | 6 distinct advisories (adds CVE-2026-26331) |

## Not reviewed or not tested

- Redirect-based SSRF and DNS rebinding (R4 residual).
- Download bounds and process cleanup on macOS and Windows (R2 residual).

- Live downloads from any social platform (blocked by this container's network policy).
- The Gemini engine end to end (no API key available; upstream's mocked tests pass).
- Cloud transcription (Groq/OpenAI hosts blocked) and WhisperX (Hugging Face blocked; not installed).
- Windows and macOS behavior of the patches (tests ran on Linux only).
- A line-by-line audit of `frames.py` (758 lines) and `whisper.py` beyond reading, grep for dangerous calls, and bandit.
