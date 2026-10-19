"""Update command through real local Git and replaceable build executables."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

CLI = Path(__file__).resolve().parents[2] / 'scripts/update-checkout'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.STDOUT).decode().strip()


def checkout(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    git(origin, 'init', '-q')
    git(origin, 'config', 'user.name', 'Test')
    git(origin, 'config', 'user.email', 'test@example.test')
    (origin / 'source').write_text('base')
    git(origin, 'add', '.')
    git(origin, 'commit', '-qm', 'base')
    root = tmp_path / 'checkout'
    git(tmp_path, 'clone', '-q', str(origin), str(root))
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.test')
    (root / 'scripts').mkdir()
    if CLI.exists():
        shutil.copy2(CLI, root / 'scripts/update-checkout')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    tool = binaries / 'tool'
    tool.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ['RECORD'], 'a') as out: out.write(name + '\\n')
sys.exit(int(os.environ.get('FAIL_' + name.upper(), '0')))
''')
    tool.chmod(0o755)
    (binaries / 'npm').symlink_to(tool)
    (binaries / 'python').symlink_to(tool)
    record = tmp_path / 'record'
    env = dict(os.environ, PATH=str(binaries)+os.pathsep+os.environ['PATH'], OKO_PYTHON=str(binaries/'python'), RECORD=str(record))
    return origin, root, record, env


def run(root, env):
    return subprocess.run([sys.executable, str(root/'scripts/update-checkout')], cwd=root, env=env, text=True, capture_output=True)


def test_update_builds_and_validates_only_after_fast_forward(tmp_path):
    origin, root, record, env = checkout(tmp_path)
    (origin / 'source').write_text('updated')
    git(origin, 'commit', '-qam', 'update')
    result = run(root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / 'source').read_text() == 'updated'
    assert record.read_text().splitlines() == ['npm', 'python']
    assert git(root, 'rev-parse', 'HEAD') in result.stdout


def test_update_stops_before_build_when_git_cannot_fast_forward(tmp_path):
    origin, root, record, env = checkout(tmp_path)
    (root / 'source').write_text('local')
    git(root, 'commit', '-qam', 'local')
    (origin / 'source').write_text('remote')
    git(origin, 'commit', '-qam', 'remote')
    result = run(root, env)
    assert result.returncode != 0
    assert not record.exists()
    assert (root / 'source').read_text() == 'local'


def test_update_keeps_dirty_files_and_does_not_build(tmp_path):
    _, root, record, env = checkout(tmp_path)
    (root / 'source').write_text('unsaved')
    result = run(root, env)
    assert result.returncode != 0
    assert not record.exists()
    assert (root / 'source').read_text() == 'unsaved'


def test_update_stops_before_validation_when_build_fails(tmp_path):
    _, root, record, env = checkout(tmp_path)
    result = run(root, dict(env, FAIL_NPM='7'))
    assert result.returncode == 7
    assert record.read_text().splitlines() == ['npm']


def test_update_reports_validation_failure_without_success_message(tmp_path):
    _, root, record, env = checkout(tmp_path)
    result = run(root, dict(env, FAIL_PYTHON='9'))
    assert result.returncode == 9
    assert record.read_text().splitlines() == ['npm', 'python']
    assert 'Updated, built and validated' not in result.stdout
