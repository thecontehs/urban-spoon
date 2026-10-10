#!/usr/bin/env python3
"""Capability preflight for the watch + content-evaluator skills (standard library only).

Reports what this machine can actually do before any video is fetched:
local tooling (yt-dlp version floor, ffmpeg, ffprobe, Deno), network reachability
per platform (distinguishing a proxy that refuses CONNECT from other failures),
whether a Gemini key is configured (boolean only, never the value), and which
transcription fallbacks exist.

Usage:
    python3 scripts/preflight.py            # human-readable matrix
    python3 scripts/preflight.py --json     # machine-readable JSON
    python3 scripts/preflight.py --no-network

Exit codes:
    0  local tooling is ready and at least one social platform is reachable
       (or --no-network was given and local tooling is ready)
    2  usage error (argparse)
    3  local tooling is ready but no social platform is reachable; only local
       files (and the Gemini route, if reported available) can be used
    4  required local tooling is missing, or yt-dlp is older than the security floor
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import socket
import ssl
import subprocess  # nosec B404 - fixed argv lists only, never a shell
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCH_SCRIPTS = REPO_ROOT / '.claude' / 'skills' / 'watch' / 'scripts'

# Keep in sync with .claude/skills/watch/scripts/download.py (a test enforces this).
MIN_YTDLP_VERSION = (2026, 6, 9)

# Platform -> hosts that a real fetch needs. CDN hosts vary per video; these are
# representative entry points, so "reachable" here is necessary, not sufficient.
PLATFORM_HOSTS = {
    'youtube': ['www.youtube.com', 'redirector.googlevideo.com', 'i.ytimg.com'],
    'tiktok': ['www.tiktok.com'],
    'instagram': ['www.instagram.com', 'scontent.cdninstagram.com'],
    'x': ['x.com', 'video.twimg.com'],
    'facebook': ['www.facebook.com'],
    'vimeo': ['vimeo.com', 'player.vimeo.com'],
    'reddit': ['www.reddit.com', 'v.redd.it'],
    'linkedin': ['www.linkedin.com'],
}
SERVICE_HOSTS = {
    'gemini': ['generativelanguage.googleapis.com'],
    'groq': ['api.groq.com'],
    'openai': ['api.openai.com'],
    'pypi': ['pypi.org'],
}


def _run(cmd: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace',  # nosec B603
                                timeout=timeout)
        return result.returncode, (result.stdout or '') + (result.stderr or '')
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, type(exc).__name__


def parse_ytdlp_version(text: str):
    match = re.match(r'\s*(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.\d+)?\s*$', text or '')
    return tuple(int(p) for p in match.groups()) if match else None


def fmt_version(version) -> str:
    return f'{version[0]}.{version[1]:02d}.{version[2]:02d}'


def check_ytdlp() -> dict:
    path = shutil.which('yt-dlp')
    out = {'present': bool(path), 'path': path, 'version': None, 'min_version': fmt_version(MIN_YTDLP_VERSION),
           'version_ok': False, 'js_runtimes': None, 'ejs': None}
    if not path:
        return out
    code, text = _run([path, '--ignore-config', '--version'])
    version = parse_ytdlp_version(text.strip()) if code == 0 else None
    out['version'] = text.strip()[:40] if code == 0 else None
    out['version_ok'] = bool(version and version >= MIN_YTDLP_VERSION)
    # The verbose header lists JS runtimes and optional libraries without any network access.
    _, verbose = _run([path, '--ignore-config', '-v'])
    runtimes = re.search(r'\[debug\] JS runtimes: (.*)', verbose)
    out['js_runtimes'] = runtimes.group(1).strip() if runtimes else None
    libs = re.search(r'\[debug\] Optional libraries: (.*)', verbose)
    out['ejs'] = bool(libs and 'yt_dlp_ejs' in libs.group(1)) if libs else None
    return out


def check_binary(name: str, flag: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {'present': False, 'path': None, 'version': None}
    code, text = _run([path, flag])
    first = text.splitlines()[0].strip() if text else None
    return {'present': code == 0, 'path': path, 'version': first}


def classify_error(exc: BaseException) -> str:
    """Map a urllib failure to a short, stable status string."""
    text = f'{exc} {getattr(exc, "reason", "")}'.lower()
    if 'tunnel connection failed' in text or 'connect tunnel failed' in text:
        return 'blocked_by_proxy' if '403' in text else 'proxy_error'
    if isinstance(exc, (socket.timeout, TimeoutError)) or 'timed out' in text:
        return 'timeout'
    if isinstance(exc, ssl.SSLError) or 'certificate' in text or 'ssl' in text:
        return 'tls_error'
    if 'name or service not known' in text or 'getaddrinfo' in text or 'nodename nor servname' in text:
        return 'dns_error'
    if 'refused' in text:
        return 'connection_refused'
    return 'error'


def probe_host(host: str, timeout: float) -> dict:
    """HEAD https://host/. Any HTTP answer from the origin (even 4xx) means the host is reachable."""
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host):
        return {'host': host, 'status': 'invalid_host'}
    request = urllib.request.Request(f'https://{host}/', method='HEAD',
                                     headers={'User-Agent': 'urban-spoon-preflight/1.0'})
    try:
        # Fixed https:// scheme on a validated hostname; env proxies and CA settings apply.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            return {'host': host, 'status': 'reachable', 'http_status': response.status}
    except urllib.error.HTTPError as exc:
        return {'host': host, 'status': 'reachable', 'http_status': exc.code}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {'host': host, 'status': classify_error(exc), 'detail': str(getattr(exc, 'reason', exc))[:160]}


def probe_groups(groups: dict, timeout: float, probe=probe_host) -> dict:
    hosts = sorted({h for hs in groups.values() for h in hs})
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(hosts) or 1)) as pool:
        results = dict(zip(hosts, pool.map(lambda h: probe(h, timeout), hosts)))
    out = {}
    for name, hs in groups.items():
        statuses = [results[h]['status'] for h in hs]
        if all(s == 'reachable' for s in statuses):
            summary = 'reachable'
        elif any(s == 'reachable' for s in statuses):
            summary = 'partial'
        elif all(s == 'blocked_by_proxy' for s in statuses):
            summary = 'blocked_by_proxy'
        else:
            summary = 'unreachable'
        out[name] = {'status': summary, 'hosts': [results[h] for h in hs]}
    return out


def _env_file_has(path: Path, name: str) -> bool:
    try:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key.strip() == name and value.strip().strip('"\''):
                return True
    except (OSError, UnicodeError):
        pass
    return False


def key_present(name: str) -> bool:
    """Boolean only. Same lookup order as the watch skill: env, ~/.config/watch/.env, ./.env."""
    if os.environ.get(name, '').strip():
        return True
    return any(_env_file_has(p, name) for p in (Path.home() / '.config' / 'watch' / '.env', Path.cwd() / '.env'))


def whisperx_ready() -> bool:
    exe = os.environ.get('WATCH_WHISPERX_BIN', '').strip()
    if not exe:
        cfg = Path.home() / '.config' / 'watch' / '.env'
        try:
            for line in cfg.read_text(encoding='utf-8-sig').splitlines():
                key, sep, value = line.strip().partition('=')
                if sep and key.strip() == 'WATCH_WHISPERX_BIN':
                    exe = value.strip().strip('"\'')
        except (OSError, UnicodeError):
            pass
    path = Path(exe) if exe else None
    return bool(path and path.is_absolute() and path.is_file() and (path.parent.parent / '.deps-ok').is_file())


def build_report(network: bool = True, timeout: float = 5.0, probe=probe_host) -> dict:
    tools = {
        'python': {'present': True, 'version': sys.version.split()[0], 'ok': sys.version_info >= (3, 10)},
        'yt-dlp': check_ytdlp(),
        'ffmpeg': check_binary('ffmpeg', '-version'),
        'ffprobe': check_binary('ffprobe', '-version'),
        'deno': check_binary('deno', '--version'),
    }
    keys = {name: key_present(name) for name in ('GEMINI_API_KEY', 'GROQ_API_KEY', 'OPENAI_API_KEY')}
    platforms = probe_groups(PLATFORM_HOSTS, timeout, probe) if network else {}
    services = probe_groups(SERVICE_HOSTS, timeout, probe) if network else {}
    local_ok = (tools['yt-dlp']['present'] and tools['yt-dlp']['version_ok']
                and tools['ffmpeg']['present'] and tools['ffprobe']['present'] and tools['python']['ok'])
    media_ok = tools['ffmpeg']['present'] and tools['ffprobe']['present']
    capabilities = {
        'local_file_watch': media_ok,
        'url_watch_local_engine': {},
        'gemini_youtube_route': None,
        'transcription_fallback': {
            'captions': 'when the platform provides them (needs URL access)',
            'groq': keys['GROQ_API_KEY'] and (not network or services['groq']['status'] == 'reachable'),
            'openai': keys['OPENAI_API_KEY'] and (not network or services['openai']['status'] == 'reachable'),
            'whisperx_installed': whisperx_ready(),
        },
    }
    for name in PLATFORM_HOSTS:
        reachable = platforms.get(name, {}).get('status') == 'reachable' if network else None
        ready = bool(local_ok and reachable)
        if name == 'youtube':
            ready = ready and tools['deno']['present']
        capabilities['url_watch_local_engine'][name] = ready if network else ('unknown (network not probed)' if local_ok else False)
    if network:
        capabilities['gemini_youtube_route'] = keys['GEMINI_API_KEY'] and services['gemini']['status'] == 'reachable'
    else:
        capabilities['gemini_youtube_route'] = 'unknown (network not probed)' if keys['GEMINI_API_KEY'] else False

    if not local_ok:
        exit_code, verdict = 4, 'local tooling missing or yt-dlp below the security floor'
    elif not network:
        exit_code, verdict = 0, 'local tooling ready (network not probed)'
    elif any(p['status'] == 'reachable' for p in platforms.values()):
        exit_code, verdict = 0, 'ready'
    else:
        exit_code, verdict = 3, 'no social platform reachable; local files only' + (
            ' (Gemini YouTube route available)' if capabilities['gemini_youtube_route'] else '')
    return {'verdict': verdict, 'exit_code': exit_code, 'tools': tools, 'keys_present': keys,
            'network_probed': network, 'platforms': platforms, 'services': services,
            'capabilities': capabilities,
            'proxy_env_set': any(os.environ.get(v) for v in ('HTTPS_PROXY', 'https_proxy')),
            'cloud_session': os.environ.get('CLAUDE_CODE_REMOTE') == 'true'}


def render_human(report: dict) -> str:
    def mark(value):
        return 'yes' if value is True else 'no' if value is False else str(value)
    lines = ['Preflight capability matrix', '', 'Tools:']
    y = report['tools']['yt-dlp']
    lines.append(f"  yt-dlp    {mark(y['present'])}  version={y['version']}  floor={y['min_version']}  "
                 f"version_ok={mark(y['version_ok'])}  js_runtimes={y['js_runtimes']}  ejs={mark(y['ejs'])}")
    for name in ('ffmpeg', 'ffprobe', 'deno'):
        t = report['tools'][name]
        lines.append(f"  {name:<9} {mark(t['present'])}  {t['version'] or ''}")
    lines.append(f"  python    {report['tools']['python']['version']}")
    lines += ['', 'Keys (presence only):'] + [f'  {k}: {mark(v)}' for k, v in report['keys_present'].items()]
    if report['network_probed']:
        lines += ['', 'Network:']
        for group in ('platforms', 'services'):
            for name, info in report[group].items():
                hosts = ', '.join(f"{h['host']}={h['status']}" for h in info['hosts'])
                lines.append(f"  {name:<10} {info['status']:<17} {hosts}")
    else:
        lines += ['', 'Network: not probed (--no-network)']
    cap = report['capabilities']
    lines += ['', 'Capabilities:', f"  local file watch:       {mark(cap['local_file_watch'])}",
              f"  Gemini YouTube route:   {mark(cap['gemini_youtube_route'])}"]
    lines += [f'  URL watch ({k}): {mark(v)}' for k, v in cap['url_watch_local_engine'].items()]
    lines += [f'  transcription {k}: {mark(v)}' for k, v in cap['transcription_fallback'].items()]
    lines += ['', f"Verdict: {report['verdict']} (exit {report['exit_code']})"]
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Capability preflight for the watch/content-evaluator skills.')
    parser.add_argument('--json', action='store_true', help='print JSON only')
    parser.add_argument('--no-network', action='store_true', help='skip host reachability probes')
    parser.add_argument('--timeout', type=float, default=5.0, help='per-host timeout in seconds (default 5)')
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 60:
        parser.error('--timeout must be between 0 and 60 seconds')
    report = build_report(network=not args.no_network, timeout=args.timeout)
    print(json.dumps(report, indent=2) if args.json else render_human(report))
    return report['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
