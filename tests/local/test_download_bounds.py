"""H2 (unbounded downloads), M2 (SSRF) and process-lifetime tests, using local HTTP servers."""
from __future__ import annotations

import http.server
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import download
import runtime
from conftest import SCRIPTS, WATCH, need


class _Handler(http.server.BaseHTTPRequestHandler):
    clip: Path | None = None

    def _headers(self, length=None):
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.end_headers()

    def do_HEAD(self):
        self._headers(self.clip.stat().st_size if self.path.startswith('/clip') else None)

    def do_GET(self):
        try:
            if self.path.startswith('/clip'):
                data = self.clip.read_bytes()
                self._headers(len(data))
                self.wfile.write(data)
            elif self.path.startswith('/endless'):
                self._headers()
                chunk = b'\0' * 65536
                while True:
                    self.wfile.write(chunk)
            elif self.path.startswith('/slow'):
                self._headers()
                while True:
                    self.wfile.write(b'\0' * 1024)
                    self.wfile.flush()
                    time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def server(vertical_clip, monkeypatch):
    _Handler.clip = vertical_clip
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    for name in ('no_proxy', 'NO_PROXY'):  # talk to 127.0.0.1 directly even when a proxy is configured
        monkeypatch.setenv(name, '127.0.0.1,localhost')
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()
    httpd.server_close()


# --- M2: SSRF ---------------------------------------------------------------------------

@pytest.mark.parametrize('url', [
    'http://127.0.0.1/v.mp4', 'http://localhost:8080/v', 'http://[::1]/v', 'http://169.254.169.254/latest/meta-data',
    'http://10.0.0.5/v', 'http://172.16.3.4/v', 'http://192.168.1.1/v', 'http://100.64.0.1/v', 'http://0.0.0.0/v',
    'http://224.0.0.1/v', 'http://[fe80::1]/v', 'http://[fc00::1]/v', 'http://[::ffff:127.0.0.1]/v',
    'http://2130706433/v', 'http://240.0.0.1/v',
])
def test_non_public_destinations_are_refused(url):
    with pytest.raises(SystemExit, match='non-public'):
        download.check_destination(url)


@pytest.mark.parametrize('url', ['https://93.184.215.14/v', 'https://[2606:4700:4700::1111]/v'])
def test_public_literals_are_allowed(url):
    download.check_destination(url)


def test_opt_in_allows_private(monkeypatch):
    monkeypatch.setenv('WATCH_ALLOW_PRIVATE_NETWORK', '1')
    download.check_destination('http://127.0.0.1/v')


def test_ssrf_check_runs_before_ytdlp(monkeypatch, tmp_path):
    monkeypatch.setattr(download, '_run', lambda cmd: pytest.fail('yt-dlp must not run'))
    result = download.fetch_captions('http://127.0.0.1:9/v', tmp_path)
    assert 'non-public' in result['errors'][0]
    with pytest.raises(SystemExit, match='non-public'):
        download.download_url('http://127.0.0.1:9/v', tmp_path)


def test_watch_cli_refuses_loopback_without_flag(tmp_path, server):
    need('yt-dlp', 'ffmpeg', 'ffprobe')
    env = {**os.environ, 'WATCH_ENGINE': 'local'}
    env.pop('WATCH_ALLOW_PRIVATE_NETWORK', None)
    proc = subprocess.run([sys.executable, str(WATCH), f'{server}/clip.mp4', '--no-whisper', '--engine', 'local',
                           '--out-dir', str(tmp_path)], capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 1
    assert 'non-public address' in proc.stdout and '## Frames' in proc.stdout and '_No frames extracted._' in proc.stdout


def test_watch_cli_downloads_over_http_with_flag(tmp_path, server):
    """Real yt-dlp HTTP download of a finite local file (exercises the bounded media stage)."""
    need('yt-dlp', 'ffmpeg', 'ffprobe')
    proc = subprocess.run([sys.executable, str(WATCH), f'{server}/clip.mp4', '--no-whisper', '--engine', 'local',
                           '--allow-private-network', '--out-dir', str(tmp_path)],
                          capture_output=True, text=True, env={**os.environ, 'WATCH_ENGINE': 'local'}, timeout=180)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert '**Resolution:** 1080x1920' in proc.stdout and 'reason=' in proc.stdout


# --- H2: size, time, live and duration bounds ----------------------------------------------

def _ytdlp_children(port: int) -> list[str]:
    out = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if f'127.0.0.1:{port}' in l and 'yt-dlp' in l]


def test_endless_stream_is_stopped_by_byte_cap(monkeypatch, tmp_path, server):
    need('yt-dlp')
    monkeypatch.setenv('WATCH_ALLOW_PRIVATE_NETWORK', '1')
    monkeypatch.setenv('WATCH_MAX_DOWNLOAD_MB', '5')
    started = time.monotonic()
    with pytest.raises(SystemExit, match='wrote more than 5 MB'):
        download.download_url(f'{server}/endless.mp4', tmp_path)
    assert time.monotonic() - started < 60
    leftovers = [p for p in tmp_path.rglob('*') if p.is_file() and p.stat().st_size > 1_000_000]
    assert not leftovers, leftovers  # partial download removed
    time.sleep(0.5)
    assert not _ytdlp_children(int(server.rsplit(':', 1)[1]))


def test_trickle_stream_is_stopped_by_timeout(monkeypatch, tmp_path, server):
    need('yt-dlp')
    monkeypatch.setenv('WATCH_ALLOW_PRIVATE_NETWORK', '1')
    monkeypatch.setenv('WATCH_DOWNLOAD_TIMEOUT', '4')
    started = time.monotonic()
    with pytest.raises(SystemExit, match='timed out after 4s'):
        download.download_url(f'{server}/slow.mp4', tmp_path)
    assert time.monotonic() - started < 30
    time.sleep(0.5)
    assert not _ytdlp_children(int(server.rsplit(':', 1)[1]))


@pytest.mark.parametrize('info,needle', [
    ({'is_live': True}, 'live'), ({'live_status': 'is_upcoming'}, 'live'),
    ({'duration': 1801}, 'exceeds WATCH_MAX_DURATION=1800'),
])
def test_live_and_long_sources_are_refused(info, needle):
    assert needle in download._refusal(info)


def test_duration_cap_is_configurable(monkeypatch):
    monkeypatch.setenv('WATCH_MAX_DURATION', '60')
    assert download._refusal({'duration': 61}) and not download._refusal({'duration': 59})
    assert download._refusal({'duration': None}) is None


def test_refusal_stops_media_stage(monkeypatch, tmp_path):
    monkeypatch.setattr(download, '_run', lambda cmd: pytest.fail('yt-dlp must not run'))
    with pytest.raises(SystemExit, match='live'):
        download.download_url('https://93.184.215.14/v', tmp_path, context={'refused': 'Refusing live stream'})


@pytest.mark.parametrize('name,value', [('WATCH_MAX_DOWNLOAD_MB', '0'), ('WATCH_DOWNLOAD_TIMEOUT', '-1'),
                                        ('WATCH_MAX_DURATION', 'abc'), ('WATCH_MAX_DOWNLOAD_MB', 'inf')])
def test_bad_limits_fail_closed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match=name):
        download.limits()


def test_child_dies_with_killed_parent(tmp_path):
    """If watch itself is SIGKILLed, its yt-dlp child must not keep downloading (Linux)."""
    if not sys.platform.startswith('linux'):
        pytest.skip('PR_SET_PDEATHSIG is Linux-only')
    pidfile = tmp_path / 'child.pid'
    parent = subprocess.Popen([sys.executable, '-c', (
        f'import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import runtime; '
        f"runtime.run_bounded(['sh', '-c', 'echo $$ > {pidfile}; exec sleep 60'], timeout=120)")])
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.05)
    child = int(pidfile.read_text())
    os.kill(parent.pid, signal.SIGKILL)
    parent.wait()
    for _ in range(60):
        if not _alive(child):
            return
        time.sleep(0.05)
    os.kill(child, signal.SIGKILL)
    pytest.fail('child outlived its killed parent')


def test_run_bounded_kills_process_group(tmp_path):
    marker = tmp_path / 'grandchild.pid'
    with pytest.raises(SystemExit, match='timed out'):
        runtime.run_bounded(['sh', '-c', f'sleep 60 & echo $! > {marker}; wait'], timeout=1)
    grandchild = int(marker.read_text())
    time.sleep(0.2)
    assert not _alive(grandchild)


def _alive(pid: int) -> bool:
    """True if pid exists and is not a zombie (an unreaped zombie is already dead)."""
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        return state != 'Z'
    except (FileNotFoundError, ProcessLookupError):
        return False
