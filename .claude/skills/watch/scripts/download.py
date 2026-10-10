#!/usr/bin/env python3
"""Bounded captions and isolated per-run media downloads through yt-dlp."""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from runtime import configure_stdio, diagnostic, run_text

VIDEO_EXTS = {'.mp4', '.mkv', '.webm', '.mov', '.m4v', '.avi', '.flv', '.wmv'}

# LOCAL PATCH (urban-spoon, see ../UPSTREAM.md): minimum yt-dlp release. 2026.07.04 fixes
# CVE-2026-55404 (--write-link shortcut injection); 2026.06.09 fixed CVE-2026-50574 (aria2c
# argument injection), CVE-2026-50023, CVE-2026-50019 and GHSA-69qj-pvh9-c5wg (--exec
# template injection); older fixes (CVE-2026-26331, CVE-2025-54072) are included too.
MIN_YTDLP_VERSION = (2026, 7, 4)
# LOCAL PATCH: options that run external programs, load extra config/plugins, or hand the
# transfer to an external downloader. watch never needs them; refuse if any appear in argv.
FORBIDDEN_YTDLP_OPTIONS = (
    '--exec', '--exec-before-download', '--downloader', '--external-downloader',
    '--downloader-args', '--external-downloader-args', '--netrc-cmd', '--config-locations',
    '--plugin-dirs', '--use-postprocessor', '--update', '-U', '--update-to', '--batch-file', '-a',
    '--write-link', '--write-url-link', '--write-webloc-link', '--write-desktop-link',
)
# LOCAL PATCH: always prepended. --ignore-config stops user/system/portable yt-dlp config files
# from re-enabling the vectors above; proxies still come from HTTP(S)_PROXY env vars.
HARDENING_ARGS = ['--ignore-config', '--no-plugin-dirs', '--no-exec']
_YTDLP_VERSION_OK: str | None = None


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return not source.startswith('-') and parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f'File not found: {p}')
    return {'video_path': str(p), 'subtitle_path': None, 'info': {'title': p.name, 'url': str(p)}, 'downloaded': False}


def auth_args(cookies_file=None, cookies_from_browser=None) -> list[str]:
    if cookies_file and cookies_from_browser:
        raise SystemExit('Choose either --cookies or --cookies-from-browser, not both.')
    if cookies_from_browser:
        return ['--no-cookies', '--cookies-from-browser', cookies_from_browser]
    if cookies_file:
        return ['--no-cookies-from-browser', '--cookies', str(Path(cookies_file).expanduser().resolve())]
    return []


def parse_ytdlp_version(text: str) -> tuple[int, int, int] | None:
    """LOCAL PATCH: parse 'YYYY.MM.DD' (optionally with a nightly suffix) from yt-dlp --version."""
    match = re.match(r'\s*(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.\d+)?\s*$', text or '')
    return tuple(int(part) for part in match.groups()) if match else None


def check_ytdlp_version(executable: str = 'yt-dlp') -> str:
    """LOCAL PATCH: refuse to run a yt-dlp older than MIN_YTDLP_VERSION (fails closed)."""
    global _YTDLP_VERSION_OK
    if _YTDLP_VERSION_OK is not None:
        return _YTDLP_VERSION_OK
    minimum = '.'.join(f'{part:02d}' if i else str(part) for i, part in enumerate(MIN_YTDLP_VERSION))
    hint = (f'Upgrade to yt-dlp >= {minimum} (this repo pins one in requirements.txt: '
            'pip install -r requirements.txt, or pipx upgrade yt-dlp / brew upgrade yt-dlp).')
    result = run_text([executable, '--ignore-config', '--version'], timeout=30)
    raw = (result.stdout or '').strip()
    version = parse_ytdlp_version(raw) if result.returncode == 0 else None
    if version is None:
        raise SystemExit(f'Cannot determine the yt-dlp version (got {raw[:40]!r}); refusing to run. {hint}')
    if version < MIN_YTDLP_VERSION:
        raise SystemExit(f'yt-dlp {raw} is older than {minimum} and has known security vulnerabilities '
                         f'(e.g. CVE-2026-55404, CVE-2026-50574, CVE-2026-50023, CVE-2026-50019); refusing to run. {hint}')
    _YTDLP_VERSION_OK = raw
    return raw


def assert_safe_args(cmd: list[str]) -> None:
    """LOCAL PATCH: refuse argv that could run commands or hand off to an external downloader."""
    options = cmd[:cmd.index('--')] if '--' in cmd else cmd
    for i, arg in enumerate(options):
        # Values of -o/--sub-langs/etc. are never option names; only inspect option-like tokens.
        if i and options[i - 1] in ('-o', '--output', '--cookies', '--cookies-from-browser', '--load-info-json',
                                    '--print-to-file', '-f', '--sub-langs', '--sub-format', '--convert-subs',
                                    '--merge-output-format', '-N'):
            continue
        name = arg.split('=', 1)[0]
        if name in FORBIDDEN_YTDLP_OPTIONS:
            raise SystemExit(f'Refusing to run yt-dlp with {name}: watch never needs it (security hardening).')
    for required in HARDENING_ARGS:
        if required not in options:
            raise SystemExit(f'Refusing to run yt-dlp without {required} (security hardening).')


def _common(directory: Path, auth: list[str]) -> list[str]:
    # Escape literal percent signs in the directory, while retaining template fields.
    prefix = str(directory.resolve()).replace('%', '%%')
    return ['yt-dlp', *HARDENING_ARGS, '--no-playlist', '--no-simulate', *auth,
            '-o', f'{prefix}/video.%(ext)s',
            '-o', f'subtitle:{prefix}/video.%(ext)s',
            '-o', f'infojson:{prefix}/video.%(ext)s']


def network_diagnostic(stderr: str) -> str:
    detail = diagnostic(stderr)
    lower = detail.lower()
    if any(s in lower for s in ('connect tunnel failed', 'tunnel connection failed', 'proxyerror')):
        # LOCAL PATCH: a proxy refusing CONNECT (e.g. a cloud sandbox allowlist) is not a yt-dlp bug.
        hint = ('A proxy refused the connection (often a network allowlist). Allow the platform and CDN '
                'domains in your environment settings (see README.md), or use an accessible local file.')
    elif any(s in lower for s in ('javascript runtime', 'js runtime', 'ejs', 'challenge solver')):
        hint = 'Update yt-dlp with its owning package manager and check Deno/EJS using setup.py --json.'
    elif any(s in lower for s in ('sign in', 'login required', 'authentication required', 'confirm you’re not a bot', "confirm you're not a bot")):
        hint = 'This source requires authentication; supply an explicit cookie option if you have access.'
    elif '429' in lower or 'too many requests' in lower:
        hint = 'The service is rate limiting requests; wait before trying again.'
    elif re.search(r'\b403\b', lower) or 'forbidden' in lower:
        # Only the latest yt-dlp is supported; sites routinely break older releases with 403s.
        hint = 'No generic fix for a 403; update yt-dlp to its latest release and retry once.'
    elif any(s in lower for s in ('egress denied', 'not in the allowlist', 'blocked by network policy')):
        hint = 'This environment blocks the destination; check its network settings or use an accessible local file.'
    elif any(s in lower for s in ('certificate_verify_failed', 'certificate verify failed', 'unable to get local issuer certificate')):
        hint = 'Check your trusted CA configuration; keep TLS verification enabled.'
    else:
        hint = 'Check the original error and update the owning yt-dlp package if needed.'
    return f'{detail}\n{hint}'


def _run(cmd: list[str]):
    # LOCAL PATCH: version floor and argv hardening run before every real yt-dlp invocation.
    assert_safe_args(cmd)
    check_ytdlp_version(cmd[0])
    result = run_text(cmd)
    if result.returncode != 0:
        raise SystemExit(f'yt-dlp failed (exit {result.returncode}): {network_diagnostic(result.stderr)}')
    return result


def _new_run(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='run-', dir=out_dir)).resolve()


def _read_metadata(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or raw.get('_type') in ('playlist', 'multi_video') or 'entries' in raw:
            raise ValueError('expected one video, not a playlist')
        return raw
    except (OSError, ValueError) as exc:
        raise SystemExit(f'yt-dlp metadata unavailable or invalid: {type(exc).__name__}') from None


def _summary(raw: dict, url: str) -> dict:
    return {'title': raw.get('title'), 'uploader': raw.get('uploader') or raw.get('channel'),
            'duration': raw.get('duration'), 'url': raw.get('webpage_url') or url}


def select_caption(info: dict, language: str = 'auto') -> dict | None:
    """Select at most one track and keep provenance separate from yt-dlp's clean JSON."""
    manual = {k: v for k, v in (info.get('subtitles') or {}).items() if v and k != 'live_chat'}
    automatic = {k: v for k, v in (info.get('automatic_captions') or {}).items() if v and k != 'live_chat'}
    youtube = str(info.get('extractor_key') or info.get('extractor') or '').lower().startswith('youtube')
    originals = sorted(k for k in automatic if youtube and k.endswith('-orig'))
    original = originals[0][:-5] if len(originals) == 1 else None
    target = language if language != 'auto' else original or info.get('language')
    def matches(key):
        base = key.removesuffix('-orig')
        if language != 'auto':
            return key == target or base == target or ('-' not in language and base.split('-')[0] == target)
        return target and (base == target or base.split('-')[0] == str(target).split('-')[0])
    def english_first(key):  # Unknown source language: prefer English over alphabetical order.
        return key.removesuffix('-orig').split('-')[0] != 'en', key
    for kind, tracks in (('manual', manual), ('automatic', automatic)):
        keys = sorted(tracks, key=english_first)
        if target:
            keys = sorted((k for k in keys if matches(k)), key=lambda k: (k != target, k != f'{target}-orig', k))
        if keys:
            key = keys[0]
            # Auto mode must prefer original ASR to translated/dubbed metadata.
            if kind == 'automatic' and language == 'auto' and originals:
                key = originals[0]
            base = key.removesuffix('-orig')
            provenance = 'original' if original and base.split('-')[0] == original.split('-')[0] else 'unknown'
            if language != 'auto' and original and base.split('-')[0] != original.split('-')[0]:
                provenance = 'requested translation'
            return {'key': key, 'language': base, 'kind': kind, 'provenance': provenance}
    # Unknown language evidence: select one available track, visibly labeled unknown.
    if language == 'auto':
        for kind, tracks in (('manual', manual), ('automatic', automatic)):
            if tracks:
                key = min(tracks, key=english_first)
                return {'key': key, 'language': key.removesuffix('-orig'), 'kind': kind, 'provenance': 'unknown'}
    return None


def fetch_captions(url: str, out_dir: Path, *, sub_lang='auto', cookies_file=None, cookies_from_browser=None) -> dict:
    auth = auth_args(cookies_file, cookies_from_browser)
    run = _new_run(out_dir)
    info_path = run / 'video.info.json'
    result = {'video_path': None, 'subtitle_path': None, 'info': {'url': url}, 'downloaded': False,
              'run_dir': str(run), 'info_path': None, 'caption_track': None, 'errors': []}
    try:
        _run([*_common(run, auth), '--skip-download', '--write-info-json', '--no-write-subs', '--no-write-auto-subs', '--', url])
        info = _read_metadata(info_path)
        result.update(info=_summary(info, url), info_path=str(info_path))
        track = select_caption(info, sub_lang)
        if not track:
            return result
        result['caption_track'] = track
        # --sub-langs accumulates across configs: explicitly clear inherited all.
        _run([*_common(run, auth), '--load-info-json', str(info_path), '--skip-download',
              '--no-write-info-json',
              '--write-subs' if track['kind'] == 'manual' else '--no-write-subs',
              '--write-auto-subs' if track['kind'] == 'automatic' else '--no-write-auto-subs',
              '--sub-langs', '-all,^' + re.escape(track['key']) + '$',
              '--sub-format', 'vtt/best', '--convert-subs', 'vtt'])
        subtitle = (run / f"video.{track['key']}.vtt").resolve()
        if not subtitle.is_relative_to(run) or not subtitle.is_file() or not subtitle.stat().st_size:
            raise SystemExit('yt-dlp did not produce the selected caption track.')
        result['subtitle_path'] = str(subtitle)
    except SystemExit as exc:
        result['errors'].append(str(exc))
        print(f'[watch] captions unavailable: {exc}', file=sys.stderr)
    return result


def download_url(url: str, out_dir: Path, audio_only: bool = False, *, context=None,
                 cookies_file=None, cookies_from_browser=None) -> dict:
    auth = auth_args(cookies_file, cookies_from_browser)
    context = context or {}
    run = Path(context['run_dir']) if context.get('run_dir') else _new_run(out_dir)
    record = run / 'final-path.jsonl'
    record.unlink(missing_ok=True)
    fmt = 'ba/bestaudio' if audio_only else 'bv*[height<=720]+ba/b[height<=720]/bv+ba/b'
    cmd = [*_common(run, auth), '-N', '8', '-f', fmt, '--merge-output-format', 'mp4',
           '--no-ignore-errors', '--no-write-subs', '--no-write-auto-subs', '--write-info-json',
           '--print-to-file', 'after_move:%(filepath)j', str(record).replace('%', '%%')]
    if context.get('info_path'):
        cmd += ['--load-info-json', context['info_path']]
    else:
        cmd += ['--', url]
    _run(cmd)
    try:
        lines = record.read_text(encoding='utf-8').splitlines()
        if len(lines) != 1:
            raise ValueError()
        value = json.loads(lines[0])
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError()
        video = Path(value).resolve()
        if (not video.is_relative_to(run.resolve()) or not video.is_file() or video.stat().st_size == 0
                or video.suffix in ('.part', '.ytdl', '.json', '.vtt') or re.search(r'\.f\d+\.', video.name)):
            raise ValueError()
    except (OSError, ValueError, TypeError):
        raise SystemExit('yt-dlp did not report one completed, nonempty media file inside this run directory.') from None
    info = context.get('info') or _summary(_read_metadata(run / 'video.info.json'), url)
    return {**context, 'video_path': str(video), 'subtitle_path': context.get('subtitle_path'),
            'info': info, 'downloaded': True, 'run_dir': str(run)}


def download(source: str, out_dir: Path, audio_only: bool = False, **kwargs) -> dict:
    return download_url(source, out_dir, audio_only, **kwargs) if is_url(source) else resolve_local(source)


if __name__ == '__main__':
    configure_stdio()
    if len(sys.argv) != 3:
        raise SystemExit('usage: download.py <url-or-path> <out-dir>')
    print(json.dumps(download(sys.argv[1], Path(sys.argv[2])), indent=2))
