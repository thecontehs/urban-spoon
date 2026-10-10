"""Fixtures for this repo's own tests (separate from the vendored upstream suite)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / '.claude' / 'skills' / 'watch' / 'scripts'
WATCH = SCRIPTS / 'watch.py'
PREFLIGHT = REPO / 'scripts' / 'preflight.py'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / 'scripts'))


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    """No test reads the developer's yt-dlp config, watch config, keys, or project .env."""
    home = tmp_path / 'home'
    cwd = tmp_path / 'cwd'
    home.mkdir()
    cwd.mkdir()
    for key in list(os.environ):
        if key.startswith('WATCH_') or key in {'GROQ_API_KEY', 'OPENAI_API_KEY', 'GEMINI_API_KEY', 'XDG_CONFIG_HOME'}:
            monkeypatch.delenv(key)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.chdir(cwd)
    import config
    monkeypatch.setattr(config, 'CONFIG_DIR', home / '.config' / 'watch')
    monkeypatch.setattr(config, 'CONFIG_FILE', config.CONFIG_DIR / '.env')
    import download
    monkeypatch.setattr(download, '_YTDLP_VERSION_OK', None)
    return home


def need(*names):
    missing = [n for n in names if not shutil.which(n)]
    if missing:
        pytest.skip(f'requires {", ".join(missing)} on PATH')


def make_vertical_clip(path: Path, seconds: int = 8) -> Path:
    """Synthetic 1080x1920 vertical clip: test pattern, timed text overlays, 440 Hz tone."""
    font = next((f for f in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                             '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf') if Path(f).is_file()), None)
    fontarg = f"fontfile={font}:" if font else ''
    text = (f"drawtext={fontarg}text='HOOK\\: 3 tips':fontsize=110:fontcolor=white:box=1:boxcolor=black@0.7:"
            "x=(w-text_w)/2:y=300:enable='lt(t,3)',"
            f"drawtext={fontarg}text='TIP 1 - batch your shoots':fontsize=70:fontcolor=yellow:box=1:boxcolor=black@0.7:"
            "x=(w-text_w)/2:y=900:enable='between(t,3,6)',"
            f"drawtext={fontarg}text='FOLLOW for part 2':fontsize=90:fontcolor=white:box=1:boxcolor=red@0.8:"
            "x=(w-text_w)/2:y=1500:enable='gte(t,6)'")
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
           '-f', 'lavfi', '-i', f'testsrc2=size=1080x1920:rate=30:duration={seconds}',
           '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=44100:duration={seconds}',
           '-vf', text, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast',
           '-c:a', 'aac', '-shortest', str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return path


@pytest.fixture(scope='session')
def vertical_clip(tmp_path_factory):
    need('ffmpeg', 'ffprobe')
    return make_vertical_clip(tmp_path_factory.mktemp('clips') / 'vertical.mp4')
