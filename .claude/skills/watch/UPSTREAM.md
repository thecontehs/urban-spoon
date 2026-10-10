# Upstream provenance: watch skill

| Field | Value |
|---|---|
| Source | https://github.com/bradautomates/claude-video |
| Path vendored | `skills/watch/` (this directory) and `LICENSE` |
| Commit | `03ceb42` ("chore: release 0.3.2") |
| License | MIT, Copyright Bradley Bonanno (see `LICENSE` in this directory) |
| Upstream tests | `tests/*.py` copied to `tests/upstream/` in this repo |
| Not vendored | plugin manifests (`.claude-plugin/`, `.codex-plugin/`, `.agents/`), `hooks/` (plugin SessionStart hook), `docs/`, CI workflows, `dev-sync.sh` |

The first commit of this repo ("Vendor upstream watch skill and tests ...") is a byte-for-byte copy of those files, so `git diff <that commit> HEAD -- .claude/skills/watch tests/upstream` shows every local change.

## Local patches

All patched lines carry a `LOCAL PATCH` comment.

### 1. yt-dlp security version floor (`scripts/download.py`, `scripts/setup.py`)

- `MIN_YTDLP_VERSION = (2026, 7, 4)`. `check_ytdlp_version()` runs `yt-dlp --ignore-config --version` once per process before the first real yt-dlp call and raises `SystemExit` with an upgrade hint when the version is older, or fails closed when the version cannot be parsed.
- Why 2026.07.04 and not 2026.06.09: 2026.06.09 fixes CVE-2026-50574, CVE-2026-50023, CVE-2026-50019 and GHSA-69qj-pvh9-c5wg, but `pip-audit` (OSV) still reports CVE-2026-55404 (shortcut-file command injection via `--write-link` options) against 2026.06.09, fixed in 2026.07.04.
- `setup.py --json` reports `ytdlp_security.{min_version, version_ok, config_files}`.
- Tests: `tests/local/test_hardening.py` (`test_parse_version`, `test_old_ytdlp_is_refused_with_upgrade_hint`, `test_new_enough_ytdlp_is_accepted`, `test_unparseable_version_fails_closed`, `test_run_checks_version_before_invoking`, `test_installed_ytdlp_meets_floor`, `test_setup_json_reports_security_floor`, `test_preflight_floor_matches_download_floor`).

### 2. Ignore yt-dlp config and refuse dangerous options (`scripts/download.py`)

- Every yt-dlp argv now starts with `--ignore-config --no-plugin-dirs --no-exec` (`HARDENING_ARGS`).
- `assert_safe_args()` runs before every invocation. It refuses argv that lacks the hardening flags or contains `--exec`, `--exec-before-download`, `--downloader`, `--external-downloader`, `--downloader-args`, `--external-downloader-args`, `--netrc-cmd`, `--config-locations`, `--plugin-dirs`, `--use-postprocessor`, `--update`/`-U`/`--update-to`, `--batch-file`/`-a`, or any `--write-*-link` option. Option values (output templates, cookie paths, format strings) and anything after `--` are not treated as options.
- Tradeoff: upstream deliberately let the user's yt-dlp config apply ("existing yt-dlp configuration remains active, including proxy/CA/auth settings"). That is the vector by which a config file could re-enable aria2c, `--exec` or `--netrc-cmd`. We choose the safer option:
  - Proxy: yt-dlp still honors `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` environment variables. Verified here: a real download from `raw.githubusercontent.com` through this container's proxy succeeded with `--ignore-config`.
  - CA bundle: in this container yt-dlp's TLS through the intercepting proxy worked with `--ignore-config` and the environment's CA variables. A user who relied on `--compat-options no-certifi` or similar in a config file loses it; there is no per-run override (documented, not added).
  - Auth: `--cookies` / `--cookies-from-browser` remain available as explicit CLI flags. `.netrc` and `--netrc-cmd` from config are no longer used.
  - Plugins: `--no-plugin-dirs` disables yt-dlp plugin loading (verified: verbose header shows `Plugin directories: none`).
- Upstream's caption and media flows were checked: they never relied on config for correctness; the only dependency was upstream's own offline test, which used a user config to turn on `--enable-file-urls`. That test now injects the flag itself (see "Upstream test changes").
- Tests: `test_common_args_carry_hardening`, `test_forbidden_options_are_refused` (17 cases), `test_missing_ignore_config_is_refused`, `test_option_like_values_and_url_are_not_misread`, `test_every_stage_passes_hardening`, and `test_real_ytdlp_ignores_hostile_user_config`, which runs the real yt-dlp with a user config containing `--exec "touch MARKER"`: a control run without `--ignore-config` creates the marker, the watch code path does not.

### 3. Remote uv installer requires explicit opt-in (`scripts/setup.py`)

- On Linux and Windows, WhisperX setup bootstraps `uv` by downloading and executing `https://astral.sh/uv/install.sh` (or `install.ps1`), unpinned. This now raises `SystemExit` unless `WATCH_ALLOW_REMOTE_INSTALLER=1` is set, with a hint to install uv from a package manager instead. macOS (Homebrew) is unchanged. The rest of the WhisperX path is unchanged and is "do not enable without review" (see `docs/SECURITY_REVIEW.md`).
- Tests: `test_remote_uv_installer_requires_opt_in` (Linux, Windows), `test_remote_uv_installer_runs_with_opt_in`.

### 4. Proxy CONNECT refusals get an allowlist hint (`scripts/download.py`)

- `network_diagnostic()` now checks for `CONNECT tunnel failed`, `Tunnel connection failed` and `ProxyError` before the generic 403 branch. Previously a sandbox proxy refusal (`CONNECT tunnel failed, response 403`) matched the 403 branch and told the user to update yt-dlp, which is wrong.
- Test: `test_proxy_connect_403_is_not_reported_as_ytdlp_bug` (uses the error text yt-dlp 2026.08.19 actually printed for tiktok.com in this container).

### 5. `SKILL.md`

- Replaced the sentence saying yt-dlp configuration remains active with a "Local hardening" paragraph describing patches 1, 2 and 4.

## Upstream test changes (`tests/upstream/`)

| File | Change | Reason |
|---|---|---|
| `conftest.py`, `test_watch.py`, `test_packaging.py` | Paths point at `.claude/skills/watch` | Vendored location |
| `test_packaging.py::test_release_versions_match_canonical_skill` | Skipped | Plugin manifests not vendored |
| `test_hook.py` (whole module) | Skipped | Upstream plugin hook not vendored |
| `test_download.py::test_actual_ytdlp_exact_track_with_inherited_all` | Injects `--enable-file-urls` itself | Its user config can no longer enable it (patch 2); the hostile `-o`/`--sub-langs` config lines are still present and ignored |
| `test_local_whisperx.py` (3 tests of the uv bootstrap) | `monkeypatch.setenv('WATCH_ALLOW_REMOTE_INSTALLER', '1')` | Patch 3 |

## Updating from upstream

1. Diff upstream's new `skills/watch/` against the vendor commit, not against this directory.
2. Re-apply the patches above; keep `HARDENING_ARGS` on every yt-dlp argv.
3. Re-run `pip-audit` against the new yt-dlp pin and raise `MIN_YTDLP_VERSION` (in `download.py` and `scripts/preflight.py`; a test enforces they match) if any advisory applies.
4. Run the full test suite and update the counts in `docs/CAPABILITY_MATRIX.md`.
