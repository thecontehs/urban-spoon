"""Tests for local hardening patches to the vendored watch skill (see UPSTREAM.md)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import download
import setup as watch_setup
from conftest import WATCH, need

URL = 'https://example.com/video'


def fake_ytdlp(tmp_path: Path, version: str) -> str:
    exe = tmp_path / 'bin' / 'yt-dlp'
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f'#!/bin/sh\necho "{version}"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


# --- Patch a: yt-dlp version floor -------------------------------------------------------

@pytest.mark.parametrize('text,expected', [
    ('2026.08.19', (2026, 8, 19)), ('2026.06.09\n', (2026, 6, 9)),
    ('2026.08.19.233512', (2026, 8, 19)), ('2025.12.1', (2025, 12, 1)),
    ('garbage', None), ('', None), ('2026.08', None),
])
def test_parse_version(text, expected):
    assert download.parse_ytdlp_version(text) == expected


# 2026.06.09 is refused: pip-audit/OSV lists CVE-2026-55404 (fixed in 2026.07.04) against it.
@pytest.mark.parametrize('version', ['2026.07.03', '2026.06.09', '2026.06.08', '2025.12.31', '2026.02.21'])
def test_old_ytdlp_is_refused_with_upgrade_hint(tmp_path, version):
    with pytest.raises(SystemExit) as err:
        download.check_ytdlp_version(fake_ytdlp(tmp_path, version))
    message = str(err.value)
    assert 'older than 2026.07.04' in message and 'refusing' in message
    assert 'requirements.txt' in message and 'Upgrade' in message


@pytest.mark.parametrize('version', ['2026.07.04', '2026.08.19', '2027.01.01.123'])
def test_new_enough_ytdlp_is_accepted(tmp_path, version):
    assert download.check_ytdlp_version(fake_ytdlp(tmp_path, version)) == version


def test_unparseable_version_fails_closed(tmp_path):
    with pytest.raises(SystemExit, match='Cannot determine'):
        download.check_ytdlp_version(fake_ytdlp(tmp_path, 'not-a-version'))


def test_run_checks_version_before_invoking(monkeypatch, tmp_path):
    exe = fake_ytdlp(tmp_path, '2026.01.01')
    cmd = [exe, *download.HARDENING_ARGS, '--', URL]
    with pytest.raises(SystemExit, match='older than'):
        download._run(cmd)


def test_installed_ytdlp_meets_floor():
    need('yt-dlp')
    assert download.parse_ytdlp_version(download.check_ytdlp_version()) >= download.MIN_YTDLP_VERSION


def test_preflight_floor_matches_download_floor():
    import preflight
    assert preflight.MIN_YTDLP_VERSION == download.MIN_YTDLP_VERSION


# --- Patch b: --ignore-config and forbidden options ---------------------------------------

def test_common_args_carry_hardening():
    cmd = download._common(Path('work-dir'), [])
    assert cmd[0] == 'yt-dlp' and cmd[1:4] == ['--ignore-config', '--no-plugin-dirs', '--no-exec']
    download.assert_safe_args([*cmd, '--', URL])


@pytest.mark.parametrize('bad', [
    ['--exec', 'touch x'], ['--exec=touch x'], ['--exec-before-download', 'x'],
    ['--downloader', 'aria2c'], ['--external-downloader', 'aria2c'], ['--downloader=aria2c'],
    ['--downloader-args', 'aria2c:--foo'], ['--netrc-cmd', 'x'], ['--config-locations', 'cfgdir'],
    ['--plugin-dirs', 'plugdir'], ['--use-postprocessor', 'X'], ['-U'], ['--update-to', 'nightly'],
    ['--write-link'], ['--write-desktop-link'], ['--write-url-link'], ['--write-webloc-link'],
])
def test_forbidden_options_are_refused(bad):
    cmd = [*download._common(Path('work-dir'), []), *bad, '--', URL]
    with pytest.raises(SystemExit, match='Refusing'):
        download.assert_safe_args(cmd)


def test_missing_ignore_config_is_refused():
    with pytest.raises(SystemExit, match='--ignore-config'):
        download.assert_safe_args(['yt-dlp', '--no-plugin-dirs', '--no-exec', '--', URL])


def test_option_like_values_and_url_are_not_misread():
    cmd = [*download._common(Path('work-dir'), ['--no-cookies-from-browser', '--cookies', '--exec']),
           '-f', '--downloader', '--', '--exec']
    download.assert_safe_args(cmd)


def test_every_stage_passes_hardening(monkeypatch, tmp_path):
    """Metadata, caption and media stages all go through the hardened argv."""
    seen = []
    def fake_run_text(cmd, **kw):
        seen.append(cmd)
        if cmd[-1] == '--version':
            return subprocess.CompletedProcess(cmd, 0, '2026.08.19\n', '')
        template = cmd[cmd.index('-o') + 1]
        directory = Path(template.rsplit('/video.', 1)[0].replace('%%', '%'))
        if '--load-info-json' not in cmd and '--skip-download' in cmd:
            (directory / 'video.info.json').write_text(json.dumps({'id': 't', 'language': 'en', 'subtitles': {'en': [{'url': 'u', 'ext': 'vtt'}]}}))
        elif '--sub-langs' in cmd:
            (directory / 'video.en.vtt').write_text('WEBVTT\n\n00:00.000 --> 00:01.000\nhi\n')
        else:
            media = directory / 'video.mp4'
            media.write_bytes(b'x')
            Path(cmd[cmd.index('--print-to-file') + 2].replace('%%', '%')).write_text(json.dumps(str(media)) + '\n')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    monkeypatch.setattr(download, 'run_text', fake_run_text)
    monkeypatch.setattr(download, 'run_bounded', lambda cmd, **kw: fake_run_text(cmd))
    result = download.fetch_captions(URL, tmp_path)
    download.download_url(URL, tmp_path, context=result)
    stages = [c for c in seen if '--version' not in c]
    assert len(stages) == 3
    for cmd in stages:
        assert cmd[1:4] == ['--ignore-config', '--no-plugin-dirs', '--no-exec']
        assert '--socket-timeout' in cmd
    media = stages[-1]
    assert media[media.index('--max-filesize') + 1] == '500M'
    assert media[media.index('--match-filter') + 1] == '!is_live & duration <=? 1800'
    assert sum('--version' in c for c in seen) == 1  # version is checked once per process


def _offline_info(tmp_path, clip):
    raw = {'id': 'offline', 'title': 'Offline', 'extractor': 'generic', 'extractor_key': 'Generic',
           'webpage_url': URL, 'url': clip.as_uri(), 'ext': 'mp4', 'duration': 8}
    info = tmp_path / 'input.info.json'
    info.write_text(json.dumps(raw))
    return info


def test_real_ytdlp_ignores_hostile_user_config(monkeypatch, tmp_path, isolated_home, vertical_clip):
    """A user config with --exec must not run when watch invokes the real yt-dlp."""
    need('yt-dlp')
    marker = tmp_path / 'PWNED'
    cfg = isolated_home / '.config' / 'yt-dlp' / 'config'
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'--exec "touch {marker}"\n--enable-file-urls\n')
    info = _offline_info(tmp_path, vertical_clip)
    # Control: the same yt-dlp, without --ignore-config, does honor the config and runs --exec.
    control = subprocess.run(['yt-dlp', '--load-info-json', str(info), '-o', str(tmp_path / 'control.%(ext)s')],
                             capture_output=True, text=True)
    assert control.returncode == 0, control.stderr
    assert marker.exists(), 'control failed: yt-dlp did not read the user config, test proves nothing'
    marker.unlink()
    # watch path: hardening args present, so the config (and its --exec) is ignored.
    real_run = download._run
    monkeypatch.setattr(download, '_run', lambda cmd: real_run([cmd[0], '--enable-file-urls', *cmd[1:]]))
    result = download.download_url(URL, tmp_path / 'out', context={'info_path': str(info), 'info': {'title': 'Offline'}})
    assert Path(result['video_path']).is_file()
    assert not marker.exists(), 'user config --exec ran despite --ignore-config'


def test_real_ytdlp_rejects_file_urls_without_flag(monkeypatch, tmp_path, vertical_clip):
    """Without --enable-file-urls (which watch never passes) yt-dlp refuses file:// media."""
    need('yt-dlp')
    info = _offline_info(tmp_path, vertical_clip)
    with pytest.raises(SystemExit, match='file://'):
        download.download_url(URL, tmp_path / 'out', context={'info_path': str(info), 'info': {}})


# --- Patch d: proxy CONNECT refusals get an allowlist hint --------------------------------

def test_proxy_connect_403_is_not_reported_as_ytdlp_bug():
    # Verbatim shape of the error yt-dlp 2026.08.19 printed for tiktok.com in a sandbox.
    stderr = ('ERROR: [TikTok] 6718335390845095173: Unable to download webpage: Failed to perform, curl: (7) '
              'CONNECT tunnel failed, response 403. (caused by ProxyError(...))')
    hint = download.network_diagnostic(stderr)
    assert 'allowlist' in hint and 'update yt-dlp' not in hint
    assert 'latest release' in download.network_diagnostic('HTTP Error 403: Forbidden')


# --- URL validation (upstream behavior, asserted here) ------------------------------------

@pytest.mark.parametrize('source', ['-https://example.com/v', '--exec=id', 'file:///etc/hostname',
                                    'ftp://example.com/v.mp4', 'javascript:alert(1)', 'https://'])
def test_non_http_or_option_like_sources_are_not_urls(source):
    assert not download.is_url(source)


@pytest.mark.parametrize('source', ['-https://example.com/v', 'file:///etc/hostname'])
def test_rejected_sources_never_reach_ytdlp(monkeypatch, tmp_path, source):
    monkeypatch.setattr(download, '_run', lambda cmd: pytest.fail('yt-dlp must not run'))
    with pytest.raises(SystemExit, match='File not found'):
        download.download(source, tmp_path)


@pytest.mark.parametrize('source', ['file:///etc/hostname', '-https://example.com/v'])
def test_watch_cli_rejects_file_and_dash_sources(tmp_path, source):
    env = {**os.environ, 'WATCH_ENGINE': 'local', 'PATH': str(tmp_path)}  # no yt-dlp reachable at all
    proc = subprocess.run([sys.executable, str(WATCH), '--no-whisper', '--out-dir', str(tmp_path / 'o'), '--', source],
                          capture_output=True, text=True, env=env)
    assert proc.returncode != 0
    assert 'yt-dlp' not in proc.stdout
    assert 'Media unavailable' in proc.stdout and 'File not found' in proc.stdout


# --- Patch c: remote uv installer requires explicit opt-in --------------------------------

@pytest.mark.parametrize('system', ['Linux', 'Windows'])
def test_remote_uv_installer_requires_opt_in(monkeypatch, system):
    monkeypatch.setattr(watch_setup.platform, 'system', lambda: system)
    monkeypatch.setattr(watch_setup, '_which', lambda name: None)
    monkeypatch.setattr(watch_setup, '_install_step', lambda *a: pytest.fail('installer must not run'))
    with pytest.raises(SystemExit, match='WATCH_ALLOW_REMOTE_INSTALLER'):
        watch_setup._ensure_uv()


def test_remote_uv_installer_runs_with_opt_in(monkeypatch):
    monkeypatch.setenv('WATCH_ALLOW_REMOTE_INSTALLER', '1')
    monkeypatch.setattr(watch_setup.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(watch_setup, '_which', lambda name: None)
    calls = []
    def step(cmd, label):
        calls.append(label)
        raise SystemExit('stop')
    monkeypatch.setattr(watch_setup, '_install_step', step)
    with pytest.raises(SystemExit, match='stop'):
        watch_setup._ensure_uv()
    assert calls == ['Download uv installer']


def test_setup_json_reports_security_floor():
    need('yt-dlp', 'ffmpeg', 'ffprobe')
    status = watch_setup._status(detailed=True)
    assert status['ytdlp_security']['min_version'] == '2026.07.04'
    assert status['ytdlp_security']['version_ok'] is True
