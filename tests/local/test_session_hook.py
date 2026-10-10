"""L1/L2: the SessionStart hook never fails the session and has a timeout."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from conftest import REPO

HOOK = REPO / '.claude' / 'hooks' / 'session-start.sh'


def run_hook(env_extra, tmp_path):
    env = {'PATH': os.environ['PATH'], 'CLAUDE_PROJECT_DIR': str(REPO), **env_extra}
    return subprocess.run(['bash', str(HOOK)], capture_output=True, text=True, env=env, timeout=60)


def test_local_session_is_a_noop(tmp_path):
    proc = run_hook({'HOME': str(tmp_path)}, tmp_path)
    assert proc.returncode == 0 and not proc.stdout and not proc.stderr
    assert not (tmp_path / '.cache').exists()


@pytest.mark.parametrize('home', ['/proc/urban-spoon-cannot-create', '/dev/null/not-a-dir'])
def test_unwritable_home_exits_zero_with_note(tmp_path, home):
    proc = run_hook({'HOME': home, 'CLAUDE_CODE_REMOTE': 'true'}, tmp_path)
    assert proc.returncode == 0
    assert 'urban-spoon session-start' in proc.stderr


def test_missing_python_exits_zero_with_note(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for tool in ('mkdir', 'cat', 'sha256sum', 'cut', 'dirname'):
        os.symlink(shutil.which(tool), bindir / tool)
    env = {'PATH': str(bindir), 'CLAUDE_PROJECT_DIR': str(REPO), 'HOME': str(tmp_path), 'CLAUDE_CODE_REMOTE': 'true'}
    proc = subprocess.run([shutil.which('bash'), str(HOOK)], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0
    assert 'urban-spoon session-start' in proc.stderr


def test_settings_register_hook_with_timeout():
    hooks = json.loads((REPO / '.claude' / 'settings.json').read_text())['hooks']['SessionStart'][0]['hooks']
    assert hooks[0]['timeout'] == 300 and hooks[0]['command'].endswith('/.claude/hooks/session-start.sh')
