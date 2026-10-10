"""Unit tests for scripts/preflight.py (no real network unless LIVE_TESTS=1)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error

import pytest

import preflight
from conftest import PREFLIGHT


@pytest.mark.parametrize('exc,status', [
    (urllib.error.URLError(OSError('Tunnel connection failed: 403 Forbidden')), 'blocked_by_proxy'),
    (urllib.error.URLError(OSError('Tunnel connection failed: 502 Bad Gateway')), 'proxy_error'),
    (urllib.error.URLError(TimeoutError('timed out')), 'timeout'),
    (urllib.error.URLError(OSError('[Errno -2] Name or service not known')), 'dns_error'),
    (urllib.error.URLError(OSError('[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed')), 'tls_error'),
    (urllib.error.URLError(ConnectionRefusedError('Connection refused')), 'connection_refused'),
])
def test_classify_error(exc, status):
    assert preflight.classify_error(exc) == status


def fake_probe(statuses):
    def probe(host, timeout):
        return {'host': host, 'status': statuses.get(host, 'blocked_by_proxy')}
    return probe


def test_group_summary():
    groups = {'a': ['h1', 'h2'], 'b': ['h3'], 'c': ['h4', 'h5'], 'd': ['h6']}
    out = preflight.probe_groups(groups, 1, fake_probe({'h1': 'reachable', 'h2': 'reachable', 'h4': 'reachable',
                                                         'h6': 'timeout'}))
    assert {k: v['status'] for k, v in out.items()} == {'a': 'reachable', 'b': 'blocked_by_proxy',
                                                        'c': 'partial', 'd': 'unreachable'}


def _tools(monkeypatch, *, ytdlp_ok=True, ffmpeg=True, deno=True):
    monkeypatch.setattr(preflight, 'check_ytdlp', lambda: {'present': True, 'path': '/x', 'version': '2026.08.19',
                                                            'min_version': '2026.07.04', 'version_ok': ytdlp_ok,
                                                            'js_runtimes': 'deno', 'ejs': True})
    def binary(name, flag):
        present = {'ffmpeg': ffmpeg, 'ffprobe': ffmpeg, 'deno': deno}[name]
        return {'present': present, 'path': '/x' if present else None, 'version': 'v' if present else None}
    monkeypatch.setattr(preflight, 'check_binary', binary)


def test_exit_codes(monkeypatch):
    _tools(monkeypatch)
    assert preflight.build_report(probe=fake_probe({}))['exit_code'] == 3
    report = preflight.build_report(probe=fake_probe({'www.tiktok.com': 'reachable'}))
    assert report['exit_code'] == 0 and report['capabilities']['url_watch_local_engine']['tiktok'] is True
    assert preflight.build_report(network=False)['exit_code'] == 0
    _tools(monkeypatch, ytdlp_ok=False)
    assert preflight.build_report(network=False)['exit_code'] == 4
    _tools(monkeypatch, ffmpeg=False)
    assert preflight.build_report(network=False)['exit_code'] == 4


def test_youtube_needs_deno(monkeypatch):
    _tools(monkeypatch, deno=False)
    hosts = {h: 'reachable' for h in preflight.PLATFORM_HOSTS['youtube']}
    report = preflight.build_report(probe=fake_probe(hosts))
    assert report['platforms']['youtube']['status'] == 'reachable'
    assert report['capabilities']['url_watch_local_engine']['youtube'] is False


def test_gemini_route_needs_key_and_host(monkeypatch):
    _tools(monkeypatch)
    probe = fake_probe({'generativelanguage.googleapis.com': 'reachable'})
    assert preflight.build_report(probe=probe)['capabilities']['gemini_youtube_route'] is False
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key-value')
    report = preflight.build_report(probe=probe)
    assert report['capabilities']['gemini_youtube_route'] is True
    assert 'Gemini YouTube route available' in report['verdict']


def test_keys_are_reported_as_booleans_only(isolated_home):
    secret = 'AIzaSy-THIS-MUST-NOT-LEAK-123'
    env_file = isolated_home / '.config' / 'watch' / '.env'
    env_file.parent.mkdir(parents=True)
    env_file.write_text(f'GROQ_API_KEY="{secret}"\n')
    env = {**os.environ, 'GEMINI_API_KEY': secret}
    for args in (['--no-network'], ['--no-network', '--json']):
        proc = subprocess.run([sys.executable, str(PREFLIGHT), *args], capture_output=True, text=True, env=env)
        assert secret not in proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data['keys_present'] == {'GEMINI_API_KEY': True, 'GROQ_API_KEY': True, 'OPENAI_API_KEY': False}


def test_cli_json_shape_and_exit_code_agree():
    proc = subprocess.run([sys.executable, str(PREFLIGHT), '--json', '--no-network'], capture_output=True, text=True)
    data = json.loads(proc.stdout)
    assert proc.returncode == data['exit_code']
    assert set(data['tools']) == {'python', 'yt-dlp', 'ffmpeg', 'ffprobe', 'deno'}
    assert data['network_probed'] is False


def test_invalid_host_is_never_requested():
    assert preflight.probe_host('evil.com/@x', 1)['status'] == 'invalid_host'


def test_bad_timeout_is_usage_error():
    proc = subprocess.run([sys.executable, str(PREFLIGHT), '--timeout', '0'], capture_output=True, text=True)
    assert proc.returncode == 2


@pytest.mark.live
def test_live_probe_classifies_this_environment():
    if os.environ.get('LIVE_TESTS') != '1':
        pytest.skip('set LIVE_TESTS=1 to run network tests')
    # pypi.org must be reachable for the SessionStart hook to install anything at all.
    assert preflight.probe_host('pypi.org', 5)['status'] == 'reachable'
