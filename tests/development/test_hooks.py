"""Exercise manual index checks in an isolated Git repository, including partial staging."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]


def repo(tmp_path):
    for directory in ('scripts',):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    shutil.copy(ROOT / 'eslint.config.ts', tmp_path / 'eslint.config.ts')
    if (ROOT / 'node_modules').exists():
        (tmp_path / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
    env = {**os.environ, 'OKO_PYTHON': sys.executable}
    def run(*args):
        return subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run('git', 'init', '-q').returncode == 0
    return run


def test_staged_checks_index_not_working_copy(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.py'
    source.write_text('def broken(\n')
    assert run('git', 'add', 'example.py').returncode == 0
    source.write_text('value = 1\n')
    result = run('./scripts/check', 'staged')
    assert result.returncode != 0
    assert 'example.py' in result.stdout + result.stderr
    assert run('git', 'add', 'example.py').returncode == 0
    source.write_text('def broken(\n')
    result = run('./scripts/check', 'staged')
    assert result.returncode == 0, result.stdout + result.stderr


def test_staged_checks_browser_types_from_index_across_imports(tmp_path):
    run = repo(tmp_path)
    (tmp_path / 'tests/browser').mkdir(parents=True)
    (tmp_path / 'tsconfig.browser-tests.json').write_text(
        '{"compilerOptions":{"strict":true,"noEmit":true,"skipLibCheck":true,"types":[]},"include":["tests/browser/*.ts"]}')
    helper = tmp_path / 'tests/browser/fixture.ts'
    helper.write_text('export function fixture(name: string) { return name; }\n')
    source = tmp_path / 'tests/browser/test_fixture.ts'
    source.write_text("import {fixture} from './fixture'; fixture(42);\n")
    assert run('git', 'add', 'tests/browser', 'tsconfig.browser-tests.json').returncode == 0
    source.write_text("import {fixture} from './fixture'; fixture('valid');\n")
    failed = run('./scripts/check', 'staged')
    assert failed.returncode != 0
    assert 'TS2345' in failed.stdout + failed.stderr
    assert run('git', 'add', 'tests/browser/test_fixture.ts').returncode == 0
    source.write_text("import {fixture} from './fixture'; fixture(42);\n")
    passed = run('./scripts/check', 'staged')
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_staged_checks_tooling_types_from_exact_index(tmp_path):
    run = repo(tmp_path)
    import json
    names = ['scripts/example.cts', 'scripts/example.mts']
    (tmp_path / 'tsconfig.tooling.json').write_text(json.dumps({
        'compilerOptions': {'strict': True, 'noEmit': True, 'skipLibCheck': True,
                            'module': 'NodeNext', 'types': []}, 'files': names}))
    for name in names:
        (tmp_path / name).write_text("export const count: number = 'invalid';\n")
    assert run('git', 'add', *names, 'tsconfig.tooling.json').returncode == 0
    for name in names:
        (tmp_path / name).write_text('export const count: number = 1;\n')
    failed = run('./scripts/check', 'staged')
    assert failed.returncode != 0
    for name in names:
        assert name in failed.stdout + failed.stderr
    assert 'TS2322' in failed.stdout + failed.stderr
    assert run('git', 'add', *names).returncode == 0
    for name in names:
        (tmp_path / name).write_text("export const count: number = 'invalid';\n")
    passed = run('./scripts/check', 'staged')
    assert passed.returncode == 0, passed.stdout + passed.stderr



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
                    reason='JavaScript index checks require Node; full CI supplies it')
def test_staged_rejects_staged_javascript_syntax(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.js'
    source.write_text('export const broken = ;\n')
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export const value = 1;\n')
    result = run('./scripts/check', 'staged')
    assert result.returncode != 0
    assert 'SyntaxError' in result.stdout + result.stderr
    assert run('git', 'add', 'example.js').returncode == 0
    assert run('./scripts/check', 'staged').returncode == 0


@pytest.mark.skipif(shutil.which(os.environ.get('OKO_NODE', 'node')) is None,
                    reason='JavaScript hook requires Node')
def test_staged_rejects_undefined_staged_name_and_allows_valid_partial_stage(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.js'
    source.write_text('export function render() { return source.description; }\n')
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export function render(source) { return source.description; }\n')
    result = run('./scripts/check', 'staged')
    assert result.returncode != 0 and 'no-undef' in result.stdout + result.stderr
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export function render() { return source.description; }\n')
    assert run('./scripts/check', 'staged').returncode == 0



def test_batched_index_typescript_checks_last_file_even_if_worktree_is_fixed(tmp_path):
    run = repo(tmp_path)
    first, last = tmp_path / 'a.ts', tmp_path / 'z.tsx'
    first.write_text('export const value: number = 1;')
    last.write_text('export const broken: = <p>bad</p>;')
    assert run('git', 'add', 'a.ts', 'z.tsx').returncode == 0
    last.write_text('export const value = <p>fixed only in worktree</p>;')
    failed = run('./scripts/check', 'staged')
    assert failed.returncode != 0 and 'z.tsx' in failed.stdout + failed.stderr
    assert run('git', 'add', 'z.tsx').returncode == 0
    last.write_text('export const broken: = <p>bad</p>;')
    passed = run('./scripts/check', 'staged')
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_document_commit_does_not_scan_python_contracts(tmp_path):
    run = repo(tmp_path)
    (tmp_path / 'README.md').write_text('Documentation only\n')
    assert run('git', 'add', 'README.md').returncode == 0
    (tmp_path / 'scripts/check_test_contracts.py').write_text('raise AssertionError("unrelated contracts executed")\n')
    result = run('./scripts/check', 'staged')
    assert result.returncode == 0, result.stdout + result.stderr


def test_success_receipt_preserves_partial_index_and_checker_changes(tmp_path):
    run = repo(tmp_path)
    (tmp_path / 'tests').mkdir()
    source = tmp_path / 'tests/example.py'
    source.write_text('value = 1\n')
    run('git', 'add', 'tests/example.py')
    assert run('./scripts/check', 'staged').returncode == 0
    source.write_text('def broken(\n')  # Same index still valid.
    hit = run('./scripts/check', 'staged')
    assert hit.returncode == 0 and 'Staged checks cache hit' in hit.stdout + hit.stderr
    run('git', 'add', 'tests/example.py')
    source.write_text('value = 1\n')  # Worktree fix cannot hide the bad index.
    assert run('./scripts/check', 'staged').returncode != 0
    run('git', 'add', 'tests/example.py')
    checker = tmp_path / 'scripts/check_test_contracts.py'
    checker.write_text('raise AssertionError("checker changed")\n')
    failed = run('./scripts/check', 'staged')
    assert failed.returncode != 0 and 'checker changed' in failed.stdout + failed.stderr


def test_staged_project_selection_follows_cross_project_imports(tmp_path):
    import json
    run = repo(tmp_path)
    (tmp_path / 'tests/browser').mkdir(parents=True)
    config = {'compilerOptions': {'strict': True, 'noEmit': True, 'skipLibCheck': True, 'types': []}}
    for name, files in [('tsconfig.browser-tests.json', ['tests/browser/view.ts']),
                        ('tsconfig.tooling.json', ['scripts/value.ts'])]:
        (tmp_path / name).write_text(json.dumps({**config, 'files': files}))
    helper = tmp_path / 'scripts/value.ts'
    helper.write_text('export const value = "valid";\n')
    (tmp_path / 'tests/browser/view.ts').write_text('import {value} from "../../scripts/value"; const text: string = value;\n')
    run('git', 'add', 'scripts/value.ts', 'tests', 'tsconfig.browser-tests.json', 'tsconfig.tooling.json')
    assert run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
               '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'fixture').returncode == 0
    helper.write_text('export const value = 42;\n')
    run('git', 'add', 'scripts/value.ts')
    helper.write_text('export const value = "fixed only in worktree";\n')
    failed = run('./scripts/check', 'staged')
    assert failed.returncode != 0 and 'TS2322' in failed.stdout + failed.stderr
    assert 'tsconfig.browser-tests.json' in failed.stdout + failed.stderr


def test_staged_extended_config_cannot_be_hidden_by_worktree(tmp_path):
    import json
    run = repo(tmp_path)
    (tmp_path / 'tests/browser').mkdir(parents=True)
    base = tmp_path / 'tsconfig.base.json'
    base.write_text(json.dumps({'compilerOptions': {'strict': False, 'noEmit': True, 'skipLibCheck': True, 'types': []}}))
    (tmp_path / 'tsconfig.browser-tests.json').write_text(json.dumps({'extends': './tsconfig.base.json', 'files': ['tests/browser/view.ts']}))
    (tmp_path / 'tests/browser/view.ts').write_text('export function value(input) { return input; }\n')
    run('git', 'add', 'tests', 'tsconfig.base.json', 'tsconfig.browser-tests.json')
    assert run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
               '-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'fixture').returncode == 0
    base.write_text(base.read_text().replace('false', 'true'))
    run('git', 'add', 'tsconfig.base.json')
    base.write_text(base.read_text().replace('true', 'false'))
    result = run('./scripts/check', 'staged')
    assert result.returncode != 0 and 'TS7006' in result.stdout + result.stderr


def test_staged_types_ignore_git_smudge_filters(tmp_path):
    import json
    run = repo(tmp_path)
    (tmp_path / '.gitattributes').write_text('*.ts filter=fixture\n')
    assert run('git', 'config', 'filter.fixture.smudge', 'sed s/INVALID/1/g').returncode == 0
    (tmp_path / 'tsconfig.tooling.json').write_text(json.dumps({
        'compilerOptions': {'strict': True, 'noEmit': True, 'skipLibCheck': True, 'types': []},
        'files': ['scripts/value.ts']}))
    source = tmp_path / 'scripts/value.ts'
    source.write_text('export const value: number = INVALID;\n')
    run('git', 'add', '.gitattributes', 'tsconfig.tooling.json', 'scripts/value.ts')
    source.write_text('export const value: number = 1;\n')
    result = run('./scripts/check', 'staged')
    assert result.returncode != 0 and 'Cannot find name' in result.stdout + result.stderr
    assert 'INVALID' in result.stdout + result.stderr


def test_commit_does_not_run_index_checks(tmp_path):
    run = repo(tmp_path)
    (tmp_path / 'example.py').write_text('def broken(\n')
    assert run('git', 'add', 'example.py').returncode == 0
    result = run('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.test', 'commit', '-m', 'manual checks only')
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (ROOT / '.githooks/pre-commit').exists()
