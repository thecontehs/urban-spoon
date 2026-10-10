"""M3: every yt-dlp invocation (downloads and probes) goes through the hardened argv helper."""
from __future__ import annotations

import ast
import stat
from pathlib import Path

import pytest

import download
import preflight
import setup as watch_setup
from conftest import REPO, need

RUNTIME_FILES = sorted([*(REPO / '.claude' / 'skills').rglob('*.py'), *(REPO / 'scripts').glob('*.py')])
HELPERS = {'ytdlp_argv'}


def _mentions_ytdlp(node) -> bool:
    return any((isinstance(n, ast.Constant) and n.value == 'yt-dlp') or
               (isinstance(n, ast.Name) and 'ytdlp' in n.id.lower()) for n in ast.walk(node))


def scan(source: str) -> list[int]:
    """Line numbers of yt-dlp argv lists built outside ytdlp_argv, or stray --ignore-config literals."""
    tree = ast.parse(source)
    inside_helper = set()
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef) and func.name in HELPERS:
            inside_helper.update(id(n) for n in ast.walk(func))
    constant_values = set()
    for assign in ast.walk(tree):
        if isinstance(assign, ast.Assign) and any(isinstance(t, ast.Name) and 'HARDENING' in t.id for t in assign.targets):
            constant_values.update(id(n) for n in ast.walk(assign))
    offenders = []
    for node in ast.walk(tree):
        if id(node) in inside_helper or id(node) in constant_values:
            continue
        if isinstance(node, ast.List) and node.elts:
            head = node.elts[0]
            argv_like = isinstance(head, ast.Name) or (isinstance(head, ast.Constant) and head.value == 'yt-dlp')
            if argv_like and _mentions_ytdlp(node):
                offenders.append(node.lineno)
        if isinstance(node, ast.Constant) and node.value in ('--ignore-config', '--no-plugin-dirs'):
            offenders.append(node.lineno)
    return offenders


def test_no_ytdlp_argv_built_outside_helper():
    offenders = {str(p.relative_to(REPO)): scan(p.read_text(encoding='utf-8')) for p in RUNTIME_FILES}
    assert not {k: v for k, v in offenders.items() if v}, offenders


@pytest.mark.parametrize('old', [
    "def f(path):\n    return run([path, '--ignore-config', '-v'])\n",           # old preflight -v probe
    "def f(path, name):\n    return _probe([path, '--version' if name == 'yt-dlp' else '-version'])\n",  # old setup
    "def f(ytdlp):\n    return _probe([ytdlp, '--list-impersonate-targets'])\n",
    "def f():\n    return ['yt-dlp', '--no-playlist']\n",
])
def test_scanner_catches_the_old_patterns(old):
    assert scan(old)


def test_helpers_agree():
    assert preflight.YTDLP_HARDENING_ARGS == download.HARDENING_ARGS
    assert download.ytdlp_argv('x', '--version') == ['x', '--ignore-config', '--no-plugin-dirs', '--no-exec', '--version']
    assert preflight.ytdlp_argv('x', '-v') == ['x', '--ignore-config', '--no-plugin-dirs', '--no-exec', '-v']


def test_every_runtime_call_is_hardened(monkeypatch, tmp_path):
    """A logging fake yt-dlp on PATH sees every argv from preflight, setup --json and the version check."""
    need('ffmpeg', 'ffprobe')
    log = tmp_path / 'argv.log'
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    fake = bindir / 'yt-dlp'
    fake.write_text(f'#!/bin/sh\necho "$*" >> {log}\necho 2026.08.19\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    import os
    monkeypatch.setenv('PATH', f'{bindir}{os.pathsep}{os.environ["PATH"]}')
    preflight.check_ytdlp()
    watch_setup._status(detailed=True)
    download.check_ytdlp_version(str(fake))
    calls = log.read_text().splitlines()
    assert len(calls) >= 5, calls
    for call in calls:
        assert call.startswith('--ignore-config --no-plugin-dirs --no-exec'), call


def test_real_plugin_is_not_loaded_by_any_probe(isolated_home):
    """A planted yt-dlp plugin executes under a plain `-v` probe, but not under ours."""
    need('yt-dlp', 'ffmpeg', 'ffprobe')
    import subprocess
    marker = isolated_home / 'PLUGIN_RAN'
    plugin = isolated_home / '.config' / 'yt-dlp' / 'plugins' / 'evil' / 'yt_dlp_plugins' / 'extractor' / 'evil.py'
    plugin.parent.mkdir(parents=True)
    plugin.write_text(f'open({str(marker)!r}, "w").write("x")\n')
    subprocess.run(['yt-dlp', '--ignore-config', '-v'], capture_output=True)
    assert marker.exists(), 'control failed: plugin did not load, test proves nothing'
    marker.unlink()
    preflight.check_ytdlp()
    watch_setup._status(detailed=True)
    download.check_ytdlp_version()
    assert not marker.exists()
