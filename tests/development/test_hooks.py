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
    shutil.copy(ROOT / 'eslint.config.cjs', tmp_path / 'eslint.config.cjs')
    if (ROOT / 'node_modules').exists():
        (tmp_path / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
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


def test_precommit_checks_typescript_and_jsx_index_not_working_copy(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.tsx'
    source.write_text('export const broken: = <p>bad</p>;\n')
    assert run('git', 'add', 'example.tsx').returncode == 0
    source.write_text('export const view = <p>good</p>;\n')
    failed = run('git', 'hook', 'run', 'pre-commit')
    assert failed.returncode != 0
    assert 'example.tsx' in failed.stdout + failed.stderr
    assert run('git', 'add', 'example.tsx').returncode == 0
    source.write_text('export const broken: = <p>bad</p>;\n')
    passed = run('git', 'hook', 'run', 'pre-commit')
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_precommit_checks_browser_types_from_index_across_imports(tmp_path):
    run = repo(tmp_path)
    (tmp_path / 'tests/browser').mkdir(parents=True)
    (tmp_path / 'tsconfig.browser-tests.json').write_text(
        '{"compilerOptions":{"strict":true,"noEmit":true,"skipLibCheck":true},"include":["tests/browser/*.ts"]}')
    helper = tmp_path / 'tests/browser/fixture.ts'
    helper.write_text('export function fixture(name: string) { return name; }\n')
    source = tmp_path / 'tests/browser/test_fixture.ts'
    source.write_text("import {fixture} from './fixture'; fixture(42);\n")
    assert run('git', 'add', 'tests/browser', 'tsconfig.browser-tests.json').returncode == 0
    source.write_text("import {fixture} from './fixture'; fixture('valid');\n")
    failed = run('git', 'hook', 'run', 'pre-commit')
    assert failed.returncode != 0
    assert 'TS2345' in failed.stdout + failed.stderr
    assert run('git', 'add', 'tests/browser/test_fixture.ts').returncode == 0
    source.write_text("import {fixture} from './fixture'; fixture(42);\n")
    passed = run('git', 'hook', 'run', 'pre-commit')
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_installer_preserves_custom_hooks(tmp_path):
    run = repo(tmp_path)
    run('git', 'config', 'core.hooksPath', 'custom-hooks')
    assert run('./scripts/install-hooks').returncode != 0
    assert run('git', 'config', '--get', 'core.hooksPath').stdout.strip() == 'custom-hooks'


def test_push_does_not_repeat_checks(tmp_path):
    run = repo(tmp_path)
    assert run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
               'commit', '--allow-empty', '-m', 'fixture').returncode == 0
    check = tmp_path / 'scripts/check'
    check.write_text('#!/bin/sh\nprintf "%s" "$1" > selected-check\nexit 7\n')
    remote = tmp_path / 'remote.git'
    assert run('git', 'init', '--bare', str(remote)).returncode == 0
    result = run('git', 'push', str(remote), 'HEAD:refs/heads/main')
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / 'selected-check').exists()


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


@pytest.mark.skipif(shutil.which(os.environ.get('OKO_NODE', 'node')) is None,
                    reason='JavaScript hook requires Node')
def test_precommit_rejects_undefined_staged_name_and_allows_valid_partial_stage(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.js'
    source.write_text('export function render() { return source.description; }\n')
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export function render(source) { return source.description; }\n')
    result = run('git', 'hook', 'run', 'pre-commit')
    assert result.returncode != 0 and 'no-undef' in result.stdout + result.stderr
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export function render() { return source.description; }\n')
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
