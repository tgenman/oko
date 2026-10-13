"""Exercise installed hooks in an isolated Git repository, including partial staging."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]


def repo(tmp_path):
    for directory in ('scripts', '.githooks'):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    env = {**os.environ, 'OKO_PYTHON': sys.executable}
    def run(*args):
        return subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run('git', 'init', '-q').returncode == 0
    assert run('./scripts/install-hooks').returncode == 0
    return run


def test_precommit_checks_index_not_working_copy(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.py'
    source.write_text('def broken(\n')
    assert run('git', 'add', 'example.py').returncode == 0
    source.write_text('value = 1\n')
    result = run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'invalid')
    assert result.returncode != 0
    assert 'example.py' in result.stderr
    assert run('git', 'add', 'example.py').returncode == 0
    source.write_text('def broken(\n')
    result = run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'valid')
    assert result.returncode == 0, result.stdout + result.stderr


def test_installer_preserves_custom_hooks(tmp_path):
    run = repo(tmp_path)
    run('git', 'config', 'core.hooksPath', 'custom-hooks')
    assert run('./scripts/install-hooks').returncode != 0
    assert run('git', 'config', '--get', 'core.hooksPath').stdout.strip() == 'custom-hooks'


def test_prepush_propagates_failed_full_check(tmp_path):
    run = repo(tmp_path)
    check = tmp_path / 'scripts/check'
    check.write_text('#!/bin/sh\nprintf "%s" "$1" > selected-check\nexit 7\n')
    result = run('git', 'hook', 'run', 'pre-push')
    assert result.returncode != 0
    assert (tmp_path / 'selected-check').read_text() == 'all'


@pytest.mark.skipif(shutil.which(os.environ.get('OKO_NODE', 'node')) is None,
                    reason='JavaScript hook integration requires Node; full CI supplies it')
def test_precommit_rejects_staged_javascript_syntax(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.js'
    source.write_text('export const broken = ;\n')
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export const value = 1;\n')
    result = run('git', 'hook', 'run', 'pre-commit')
    assert result.returncode != 0
    assert 'SyntaxError' in result.stderr
    assert run('git', 'add', 'example.js').returncode == 0
    assert run('git', 'hook', 'run', 'pre-commit').returncode == 0


def test_installer_preserves_default_executable_hooks(tmp_path):
    run = repo(tmp_path)
    run('git', 'config', '--unset', 'core.hooksPath')
    hook = tmp_path / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 0\n')
    hook.chmod(0o755)
    result = run('./scripts/install-hooks')
    assert result.returncode != 0
    assert 'Existing executable hook' in result.stderr
    assert run('git', 'config', '--get', 'core.hooksPath').returncode == 1
    assert hook.read_text() == '#!/bin/sh\nexit 0\n'
