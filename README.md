# urban-spoon: social video evaluator for Claude Code

Two Claude Code skills that let Claude "watch" a social video from just its URL and then judge whether it is worth posting, remixing, or learning from for your content page.

- **`watch`** (`.claude/skills/watch/`): vendored from [bradautomates/claude-video](https://github.com/bradautomates/claude-video) (MIT) with security hardening. It downloads the video with yt-dlp, extracts timestamped frames with ffmpeg, and pulls captions as a transcript. Claude then reads the frames as images and the transcript as text. Optionally, Google's Gemini API watches public YouTube URLs directly.
- **`content-evaluator`** (`.claude/skills/content-evaluator/`): the "brain". It runs a capability preflight, watches each URL, reads every frame and the transcript, scores the video on a 10-criterion weighted rubric with timestamped evidence, and returns a scorecard with a verdict: **Post / Remix / Reference only / Skip**, plus "what to steal" and "how we'd do it better". It compares several videos in one table.

Claude cannot ingest video directly. "Watching" here means frames plus transcript (local engine) or a Gemini answer (Gemini engine). The scorecard always says which evidence was missing.

## What is verified and what is not

Read [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md) before relying on any platform. In short:

- **Verified here (this repo's cloud container):** the whole local pipeline on synthetic vertical videos; a real yt-dlp URL download over the network from an allowed host (`raw.githubusercontent.com`); the hardening against hostile yt-dlp config; the preflight's proxy detection.
- **Pending network allowlist:** every social platform (YouTube, TikTok, Instagram, X, Facebook, Vimeo, Reddit, LinkedIn). The container used to build this repo blocks those hosts, so no live platform URL has been tested. Nothing in this repo claims otherwise.
- **Needs a key:** the Gemini engine (not tested end to end; no key was available).

## Quick start

### Use in Claude Code

Open this repo in Claude Code and ask, for example:

- "Evaluate this video for our page: https://www.tiktok.com/@creator/video/123"
- "Is this Reel worth reposting? https://www.instagram.com/reel/abc/"
- "Compare these three Shorts and tell me which format to copy: <url1> <url2> <url3>"

The `content-evaluator` skill triggers on phrases like these. First fill in [`.claude/skills/content-evaluator/references/brand-context.md`](.claude/skills/content-evaluator/references/brand-context.md) (audience, pillars, voice, goals) so Brand fit is scored against your page rather than generic best practice.

### Local machine

Requirements: Python 3.10+, ffmpeg and ffprobe on PATH.

```bash
python3 -m venv ~/.venvs/urban-spoon          # keep venvs outside the repo
~/.venvs/urban-spoon/bin/pip install --only-binary=:all: -r requirements.txt
export PATH="$HOME/.venvs/urban-spoon/bin:$PATH"   # yt-dlp and deno from the pinned venv
python3 scripts/preflight.py                      # what can this machine do?
```

`requirements.txt` pins yt-dlp 2026.8.19 (with the default and curl-cffi extras), every transitive dependency, and Deno (the JavaScript runtime yt-dlp needs for YouTube) from its official PyPI wheel.

### Claude Code on the web (cloud sessions)

`.claude/settings.json` registers `.claude/hooks/session-start.sh` as a SessionStart hook. When `CLAUDE_CODE_REMOTE=true` it creates a venv at `~/.cache/urban-spoon/venv` (outside the repo), installs `requirements-dev.txt` with exact pins and wheels only, records a hash so later sessions skip pip, and adds the venv to `PATH` through `CLAUDE_ENV_FILE`. It never blocks the session: on failure it logs to `~/.cache/urban-spoon/session-start.log` and exits 0, and `preflight.py` reports what is missing. Locally the hook does nothing.

First run took about 100 s in the build container; later runs take milliseconds.

## Network access (cloud environments)

By default a cloud environment's network policy may block video platforms. The preflight reports this as `blocked_by_proxy`. To allow them, edit your environment's network access settings as described in the Claude Code docs: <https://code.claude.com/docs/en/cloud-environments#network-access>.

Domains to allow, by platform (CDN hosts change; if a download still fails, the yt-dlp error names the blocked host):

| Platform | Domains |
|---|---|
| YouTube | `youtube.com`, `*.youtube.com`, `youtu.be`, `*.googlevideo.com`, `*.ytimg.com`, `*.ggpht.com` |
| TikTok | `tiktok.com`, `*.tiktok.com`, `*.tiktokcdn.com`, `*.tiktokcdn-us.com`, `*.tiktokv.com`, `*.byteoversea.com`, `*.ibytedtos.com` |
| Instagram | `instagram.com`, `*.instagram.com`, `*.cdninstagram.com`, `*.fbcdn.net` |
| Facebook | `facebook.com`, `*.facebook.com`, `fb.watch`, `*.fbcdn.net` |
| X / Twitter | `x.com`, `twitter.com`, `api.x.com`, `api.twitter.com`, `video.twimg.com`, `pbs.twimg.com`, `abs.twimg.com` |
| Vimeo | `vimeo.com`, `player.vimeo.com`, `*.vimeocdn.com` |
| Reddit | `reddit.com`, `www.reddit.com`, `v.redd.it`, `i.redd.it` |
| LinkedIn | `linkedin.com`, `www.linkedin.com`, `*.licdn.com` |
| Gemini engine (optional) | `generativelanguage.googleapis.com` |
| Cloud transcription (optional) | `api.groq.com`, `api.openai.com` |
| Dependency install | `pypi.org`, `files.pythonhosted.org` |

After changing the allowlist, run `python3 scripts/preflight.py` again. Then please run the pending live tests in `docs/CAPABILITY_MATRIX.md` and record the results there.

## Preflight

```bash
python3 scripts/preflight.py            # human-readable
python3 scripts/preflight.py --json     # for the skill
python3 scripts/preflight.py --no-network
```

It reports yt-dlp presence and version against the security floor (2026.07.04), JS runtime and EJS availability, ffmpeg/ffprobe, Deno, per-platform host reachability (`reachable`, `blocked_by_proxy`, `timeout`, `dns_error`, `tls_error`, ...), whether `GEMINI_API_KEY`/`GROQ_API_KEY`/`OPENAI_API_KEY` are set (booleans only, never values), and which transcription fallbacks exist.

| Exit code | Meaning |
|---|---|
| 0 | Local tooling ready and at least one social platform reachable (or `--no-network` and tooling ready) |
| 2 | Usage error |
| 3 | Tooling ready but no social platform reachable: local files only (and the Gemini YouTube route if reported) |
| 4 | Tooling missing, or yt-dlp older than the security floor |

## Running the watch skill directly

```bash
python3 .claude/skills/watch/scripts/watch.py "<url-or-local-file>" --engine local --no-whisper
```

See `.claude/skills/watch/SKILL.md` for all options and `.claude/skills/watch/UPSTREAM.md` for the local patches.

## Tests

```bash
python3 -m pytest                        # offline: upstream suite + this repo's tests
LIVE_TESTS=1 python3 -m pytest -m live   # opt-in tests that use the real network
```

Use the venv's Python (it has pytest from `requirements-dev.txt`). Current counts are in `docs/CAPABILITY_MATRIX.md`.

## Security

See [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md). Highlights: yt-dlp is pinned and the skill refuses anything older than 2026.07.04; yt-dlp config files, plugins and `--exec`/external downloaders are disabled; video content is treated as untrusted data, never instructions; the WhisperX installer path needs an explicit opt-in. Downloading videos may be restricted by platform terms of service and copyright; evaluate content you have the right to access, and do not repost others' work without permission.

## Layout

```
.claude/
  settings.json                  SessionStart hook registration
  hooks/session-start.sh         cloud-only pinned dependency install
  skills/watch/                  vendored + hardened upstream skill (UPSTREAM.md, LICENSE)
  skills/content-evaluator/      SKILL.md, references/, scripts/metadata.py
scripts/preflight.py             capability matrix (stdlib only)
tests/upstream/                  upstream test suite (paths adjusted)
tests/local/                     hardening, preflight, evaluator and offline E2E tests
docs/SECURITY_REVIEW.md
docs/CAPABILITY_MATRIX.md
requirements.txt, requirements-dev.txt
```

## License

The vendored `watch` skill is MIT licensed, Copyright Bradley Bonanno (`.claude/skills/watch/LICENSE`). Local modifications are listed in `.claude/skills/watch/UPSTREAM.md`.
