"""Offline end-to-end: a synthetic vertical "social" clip through watch.py (local engine).

This proves the frame + report pipeline on a 1080x1920 clip with on-screen text and an
audio track. It does NOT prove any platform download; those are pending network access.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import WATCH, need


def run_watch(source, out_dir, *args):
    env = {**os.environ, 'WATCH_ENGINE': 'local'}
    env.pop('GEMINI_API_KEY', None)
    return subprocess.run([sys.executable, str(WATCH), str(source), '--no-whisper', '--engine', 'local',
                           '--out-dir', str(out_dir), *args], capture_output=True, text=True, env=env)


def frame_lines(report: str):
    return re.findall(r'^- `([^`]+\.jpg)` \(t=(\d\d:\d\d(?:\.\d+)?), reason=([a-z-]+)\)$', report, re.M)


def test_synthetic_vertical_clip_produces_frames_and_report(tmp_path, vertical_clip):
    need('ffmpeg', 'ffprobe')
    proc = run_watch(vertical_clip, tmp_path / 'out')
    assert proc.returncode == 0, proc.stderr
    report = proc.stdout
    assert '# watch: video report' in report
    assert '**Resolution:** 1080x1920' in report
    assert '**Duration:** 00:08' in report
    frames = frame_lines(report)
    assert len(frames) >= 3, report
    stamps = []
    for path, stamp, _reason in frames:
        assert Path(path).is_file() and Path(path).stat().st_size > 0
        minutes, seconds = stamp.split(':')
        stamps.append(int(minutes) * 60 + float(seconds))
    assert stamps == sorted(stamps)
    assert stamps[0] < 3.0, 'no frame inside the hook window (first 3 s)'
    assert stamps[-1] >= 6.0, 'no frame inside the CTA window (last 2 s)'
    # Evidence limits are reported honestly: no captions for a local file, fallback disabled.
    assert '**Transcript:** fallback disabled; no captions available' in report


def test_frames_are_portrait_and_scaled(tmp_path, vertical_clip):
    need('ffmpeg', 'ffprobe')
    proc = run_watch(vertical_clip, tmp_path / 'out', '--resolution', '512')
    assert proc.returncode == 0, proc.stderr
    path = frame_lines(proc.stdout)[0][0]
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                            'stream=width,height', '-of', 'csv=p=0', path], capture_output=True, text=True)
    width, height = map(int, probe.stdout.strip().split(','))
    assert width == 512 and height > width


def test_cue_timestamps_pin_hook_and_cta(tmp_path, vertical_clip):
    need('ffmpeg', 'ffprobe')
    proc = run_watch(vertical_clip, tmp_path / 'out', '--timestamps', '0:01,0:07', '--detail', 'transcript')
    assert proc.returncode == 0, proc.stderr
    frames = frame_lines(proc.stdout)
    assert [f[1] for f in frames] == ['00:01', '00:07'], proc.stdout
    assert all(f[2] == 'transcript-cue' for f in frames)


def test_local_sidecar_subtitles_are_not_supported(tmp_path, vertical_clip):
    """Upstream ignores sidecar .srt/.vtt next to a local file; the report must say transcript is missing."""
    srt = vertical_clip.with_suffix('.srt')
    srt.write_text('1\n00:00:00,000 --> 00:00:02,000\nHere are 3 tips\n', encoding='utf-8')
    try:
        proc = run_watch(vertical_clip, tmp_path / 'out')
    finally:
        srt.unlink()
    assert proc.returncode == 0, proc.stderr
    assert 'Here are 3 tips' not in proc.stdout
    assert '_Fallback disabled; no captions available._' in proc.stdout


@pytest.mark.live
def test_live_generic_direct_media_url(tmp_path):
    """Opt-in (LIVE_TESTS=1): real yt-dlp download over the network of a small public MP4
    on raw.githubusercontent.com (Chromium test data). Exercises the URL path, not a social platform."""
    if os.environ.get('LIVE_TESTS') != '1':
        pytest.skip('set LIVE_TESTS=1 to run network tests')
    need('yt-dlp', 'ffmpeg', 'ffprobe')
    url = 'https://raw.githubusercontent.com/chromium/chromium/main/media/test/data/bear-1280x720.mp4'
    proc = run_watch(url, tmp_path / 'out')
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert '**Resolution:** 1280x720' in proc.stdout
    assert frame_lines(proc.stdout)
