"""One runner smoke test; index cases exercise the real staged checker directly."""
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
        '{"compilerOptions":{"strict":true,"noEmit":true,"skipLibCheck":true,"lib":["ES2022"],"types":[]},"include":["tests/browser/*.ts"]}')
    helper = tmp_path / 'tests/browser/fixture.ts'
    helper.write_text('export function fixture(name: string) { return name; }\n')
    source = tmp_path / 'tests/browser/test_fixture.ts'
    source.write_text("import {fixture} from './fixture'; fixture(42);\n")
    assert run('git', 'add', 'tests/browser', 'tsconfig.browser-tests.json').returncode == 0
    source.write_text("import {fixture} from './fixture'; fixture('valid');\n")
    failed = run(sys.executable, 'scripts/check-staged')
    assert failed.returncode != 0
    assert 'TS2345' in failed.stdout + failed.stderr
    assert run('git', 'add', 'tests/browser/test_fixture.ts').returncode == 0
    source.write_text("import {fixture} from './fixture'; fixture(42);\n")
    passed = run(sys.executable, 'scripts/check-staged')
    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_staged_checks_tooling_types_from_exact_index(tmp_path):
    run = repo(tmp_path)
    import json
    names = ['scripts/example.cts', 'scripts/example.mts']
    (tmp_path / 'tsconfig.tooling.json').write_text(json.dumps({
        'compilerOptions': {'strict': True, 'noEmit': True, 'skipLibCheck': True,
                            'module': 'NodeNext', 'lib': ['ES2022'], 'types': []}, 'files': names}))
    for name in names:
        (tmp_path / name).write_text("export const count: number = 'invalid';\n")
    assert run('git', 'add', *names, 'tsconfig.tooling.json').returncode == 0
    for name in names:
        (tmp_path / name).write_text('export const count: number = 1;\n')
    failed = run(sys.executable, 'scripts/check-staged')
    assert failed.returncode != 0
    for name in names:
        assert name in failed.stdout + failed.stderr
    assert 'TS2322' in failed.stdout + failed.stderr
    assert run('git', 'add', *names).returncode == 0
    for name in names:
        (tmp_path / name).write_text("export const count: number = 'invalid';\n")
    passed = run(sys.executable, 'scripts/check-staged')
    assert passed.returncode == 0, passed.stdout + passed.stderr


@pytest.mark.skipif(shutil.which(os.environ.get('OKO_NODE', 'node')) is None,
                    reason='JavaScript hook requires Node')
def test_staged_rejects_undefined_staged_name_and_allows_valid_partial_stage(tmp_path):
    run = repo(tmp_path)
    source = tmp_path / 'example.js'
    source.write_text('export function render() { return source.description; }\n')
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export function render(source) { return source.description; }\n')
    result = run(sys.executable, 'scripts/check-staged')
    assert result.returncode != 0 and 'no-undef' in result.stdout + result.stderr
    assert run('git', 'add', 'example.js').returncode == 0
    source.write_text('export function render() { return source.description; }\n')
    assert run(sys.executable, 'scripts/check-staged').returncode == 0


def test_success_receipt_preserves_partial_index_and_checker_changes(tmp_path):
    run = repo(tmp_path)
    (tmp_path / 'tests').mkdir()
    source = tmp_path / 'tests/example.py'
    source.write_text('value = 1\n')
    run('git', 'add', 'tests/example.py')
    assert run(sys.executable, 'scripts/check-staged').returncode == 0
    source.write_text('def broken(\n')  # Same index still valid.
    hit = run(sys.executable, 'scripts/check-staged')
    assert hit.returncode == 0 and 'Staged checks cache hit' in hit.stdout + hit.stderr
    run('git', 'add', 'tests/example.py')
    source.write_text('value = 1\n')  # Worktree fix cannot hide the bad index.
    assert run(sys.executable, 'scripts/check-staged').returncode != 0
    run('git', 'add', 'tests/example.py')
    checker = tmp_path / 'scripts/check_test_contracts.py'
    checker.write_text('raise AssertionError("checker changed")\n')
    failed = run(sys.executable, 'scripts/check-staged')
    assert failed.returncode != 0 and 'checker changed' in failed.stdout + failed.stderr
