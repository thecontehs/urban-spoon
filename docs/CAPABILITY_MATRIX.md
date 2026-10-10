# Capability matrix

What has actually been exercised, where, and how. Nothing here was mocked to look like a live result.

Status legend:

- **Verified-here**: ran for real in the build environment (a Claude Code cloud container, Linux, Python 3.13, ffmpeg 6.1.1, yt-dlp 2026.8.19, Deno 2.9.7) against real binaries and, where stated, the real network.
- **Verified-offline**: ran for real on synthetic local media or recorded fixtures; no platform involved.
- **Pending-network**: blocked by the build environment's network policy (proxy answers `CONNECT ... 403`). Not tested. Needs the domains in the README allowlisted, then the "How to verify" command run.
- **Needs-key**: needs an API key that was not available. Not tested end to end.
- **Not tested**: possible in principle but not exercised.

Build environment reachability, measured with `python3 scripts/preflight.py` on 2026-10-10: every social platform host `blocked_by_proxy` (youtube.com, googlevideo.com, ytimg.com, tiktok.com, instagram.com, cdninstagram.com, x.com, video.twimg.com, facebook.com, vimeo.com, reddit.com, v.redd.it, linkedin.com); `api.groq.com` and `api.openai.com` `blocked_by_proxy`; `generativelanguage.googleapis.com`, `pypi.org`, `raw.githubusercontent.com` reachable. Preflight exit code 3.

## Pipeline capabilities

| Capability | Status | Evidence |
|---|---|---|
| Frame extraction with timestamps from a 1080x1920 vertical clip | Verified-offline | `tests/local/test_e2e_synthetic.py` (synthetic 8 s testsrc2 + drawtext overlays + 440 Hz tone) |
| Claude reading extracted frames, including on-screen text | Verified-here | Frames from the synthetic clip were opened with the Read tool during the build; "HOOK: 3 tips" (t=00:00) and "FOLLOW for part 2" (t=00:07) were legible at 512 px |
| Hook-window frame pinning (`--timestamps 0:00,0:01,0:02`) | Verified-offline | `test_cue_timestamps_pin_hook_and_cta`, `test_documented_watch_command_runs_on_local_clip`. Without pins, near-duplicate removal left only 1 frame in the first 3 s of the synthetic clip |
| Honest "missing transcript" reporting | Verified-offline | E2E asserts `fallback disabled; no captions available` |
| Sidecar `.srt`/`.vtt` next to a local file | Not supported (upstream) | `test_local_sidecar_subtitles_are_not_supported` asserts it is ignored and reported as missing |
| yt-dlp URL download over the real network (generic direct-media URL) | Verified-here | `LIVE_TESTS=1 pytest -m live`: `raw.githubusercontent.com/chromium/chromium/main/media/test/data/bear-1280x720.mp4` downloaded through the proxy with `--ignore-config`, frames extracted (1280x720) |
| Caption selection and VTT parsing | Verified-offline | Upstream `test_download.py`, `test_transcribe.py`; real yt-dlp offline caption test with `--load-info-json` |
| yt-dlp hardening (version floor, ignored config, deny-list) | Verified-here | `tests/local/test_hardening.py`, including a real-binary test where a hostile user config's `--exec` runs in a control call and not via watch |
| `file://` and leading-`-` sources rejected | Verified-here | `test_non_http_or_option_like_sources_are_not_urls`, `test_rejected_sources_never_reach_ytdlp`, `test_watch_cli_rejects_file_and_dash_sources`, `test_real_ytdlp_rejects_file_urls_without_flag` |
| Proxy-block detection (preflight and watch diagnostics) | Verified-here | Preflight run above; yt-dlp error text for tiktok.com captured live and used in `test_proxy_connect_403_is_not_reported_as_ytdlp_bug` |
| Engagement metadata extraction (views, likes, comments) | Verified-offline | `tests/local/test_content_evaluator.py` on synthetic `info.json`. Which fields each platform actually provides: Pending-network |
| Scoring rubric and scorecard | Verified-offline (structure only) | Weights sum to 100, verdicts and anchors present. The quality of real evaluations has not been tested on real videos |
| yt-dlp HTTP download of the synthetic clip from a local server (`--allow-private-network`) | Verified-here | `test_watch_cli_downloads_over_http_with_flag` (real yt-dlp, 127.0.0.1) |
| Download bounds: byte cap on an endless stream, timeout on a trickle stream, process-group kill, child dies with killed parent | Verified-here | `tests/local/test_download_bounds.py` with real yt-dlp against local servers (Linux). macOS/Windows: Not tested |
| Live / over-long source refusal | Verified-offline | Unit tests on metadata dicts; no real live stream was available |
| Non-public destinations refused (SSRF) | Verified-here | 15 address forms refused, CLI refusal against a real local server; redirect/rebinding cases: Not tested (residual) |
| Report sanitization (forged title, transcript, Gemini answer) | Verified-offline | `tests/local/test_report_safety.py` |
| No plugin loading in any yt-dlp call, including probes | Verified-here | Real planted plugin in `~/.config/yt-dlp/plugins` (control run loads it), logging fake yt-dlp, AST scan |
| SessionStart hook | Verified-here | Ran with `CLAUDE_CODE_REMOTE=true`: first run about 100 s, second run skipped pip (0.01 s), local mode exits 0 immediately; failure modes (unwritable home, missing python3) exit 0 with a note (`tests/local/test_session_hook.py`) |
| Gemini engine (YouTube URL or uploaded file) | Needs-key | Host reachable here; no key. Upstream mocked tests pass |
| Groq / OpenAI transcription fallback | Needs-key + Pending-network | Hosts blocked here; upstream mocked tests pass |
| WhisperX local transcription | Not tested | Install needs Hugging Face (blocked) and the opt-in in `UPSTREAM.md` patch 3 |
| Windows / macOS | Not tested | Linux only |

## Platform matrix

| Platform | URL watch, local engine | Captions/transcript | Engagement metadata | Gemini engine | How to verify once allowlisted |
|---|---|---|---|---|---|
| YouTube / Shorts | Pending-network | Pending-network | Pending-network | Needs-key | `python3 .claude/skills/watch/scripts/watch.py "<youtube url>" --engine local --no-whisper` then `metadata.py <work dir>` |
| TikTok | Pending-network | Pending-network | Pending-network | Needs-key + Pending-network (non-YouTube videos are downloaded locally, then uploaded to Google) | same command with a TikTok URL |
| Instagram Reels | Pending-network (often needs login cookies) | Pending-network | Pending-network | Needs-key + Pending-network | same |
| X / Twitter | Pending-network | Pending-network | Pending-network | Needs-key + Pending-network | same |
| Facebook | Pending-network | Pending-network | Pending-network | Needs-key + Pending-network | same |
| Vimeo | Pending-network | Pending-network | Pending-network | Needs-key + Pending-network | same |
| Reddit | Pending-network | Pending-network | Pending-network | Needs-key + Pending-network | same |
| LinkedIn | Pending-network | Pending-network | Pending-network | Needs-key + Pending-network | same |
| Local file | Verified-offline | No (no sidecar support; transcription fallbacks untested) | No (none for local files) | Needs-key | `tests/local/test_e2e_synthetic.py` |

When a platform is verified, replace its cells with Verified-here plus the date, URL type (not necessarily the URL), and which modalities came back (frames, captions manual/auto, metadata fields).

## Test results (2026-10-10, build environment, revision 2)

All commands run from the repo root with the pinned venv on `PATH` (`requirements-dev.txt`).

| Command | Result |
|---|---|
| `python -m pytest tests/upstream -q` | 244 passed, 5 skipped (plugin hook and manifest tests: files not vendored) |
| `python -m pytest tests/local -q` | 148 passed, 2 skipped (live tests, opt-in) |
| `python -m pytest -q` | 392 passed, 7 skipped |
| `LIVE_TESTS=1 python -m pytest -m live -q` | 2 passed (generic direct-media URL download from raw.githubusercontent.com; pypi.org reachability) |
| Upstream suite before any local patch (path changes only) | 244 passed, 5 skipped |

`tests/local` breakdown: `test_hardening.py` 57, `test_download_bounds.py` 34, `test_preflight.py` 15 (1 live), `test_content_evaluator.py` 15, `test_report_safety.py` 11, `test_ytdlp_invocations.py` 8, `test_e2e_synthetic.py` 5 (1 live), `test_session_hook.py` 5.

Revision 1 (before the red-team fixes) recorded 329 passed, 7 skipped.
