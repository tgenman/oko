from pathlib import Path
import json
import shutil
import subprocess
import time
import sys
import os
import re

import pytest

from tests.support.app import create_app


def test_missing_build_fails_before_creating_reader_storage(tmp_path: Path, monkeypatch):
    monkeypatch.setattr('oko.reader.static.FRONTEND_ROOT', tmp_path / 'missing-build')
    data = tmp_path / 'reader'
    with pytest.raises(RuntimeError, match='npm run build'):
        create_app(data, token='test')
    assert not data.exists()


@pytest.mark.parametrize('failure', ['stale', 'damaged'])
def test_stale_or_damaged_build_fails_before_creating_reader_storage(tmp_path: Path, monkeypatch, failure):
    from oko.reader.static import FRONTEND_ROOT
    build = tmp_path / 'build'
    shutil.copytree(FRONTEND_ROOT, build)
    manifest = json.loads((build / 'current.json').read_text())
    if failure == 'stale':
        manifest['source'] = '0' * 64
        (build / 'current.json').write_text(json.dumps(manifest))
    else:
        (build / 'generations' / manifest['generation'] / 'index.html').write_text('damaged')
    monkeypatch.setattr('oko.reader.static.FRONTEND_ROOT', build)
    data = tmp_path / 'reader'
    with pytest.raises(RuntimeError, match='npm run build'):
        create_app(data, token='test')
    assert not data.exists()


def test_failed_build_preserves_previous_complete_generation(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    for name in ('frontend', 'scripts', 'api', 'oko', 'package.json', 'package-lock.json', 'tsconfig.json', 'tsconfig.tooling.json', 'vite.config.mts'):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, checkout / name, ignore=shutil.ignore_patterns('__pycache__', 'frontend_build'))
        else:
            shutil.copy(source, checkout / name)
    (checkout / 'node_modules').symlink_to(root / 'node_modules', target_is_directory=True)
    command = ['node', '--import', 'tsx', str(checkout / 'scripts/build-frontend.mts')]
    environment = dict(os.environ, OKO_PYTHON=sys.executable)
    first = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    current = checkout / 'oko/frontend_build/current.json'
    before = current.read_bytes()
    entry = re.search(r'<script[^>]+src="([^"]+)"', (checkout / 'frontend/index.html').read_text())
    assert entry is not None
    source_entry = Path('frontend') / entry[1].lstrip('/')
    (checkout / source_entry).write_text('import "./missing.js";')
    failed = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert failed.returncode != 0
    assert current.read_bytes() == before
    manifest = json.loads(before)
    page = checkout / 'oko/frontend_build/generations' / manifest['generation'] / 'index.html'
    assert page.is_file()
    shutil.copy(root / source_entry, checkout / source_entry)
    schema = checkout / 'api/openapi.yaml'
    # A documentation-only edit passes compatibility but changes canonical
    # bytes, so unchanged generated libraries must still fail the build.
    schema.write_text(schema.read_text().replace('title: Око Reader API', 'title: Synthetic drift title', 1))
    stale = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert stale.returncode != 0
    assert 'Stale Python API library' in stale.stderr
    assert current.read_bytes() == before


def test_watch_publishes_complete_generations_and_retains_open_tab_assets(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    for name in ('frontend', 'scripts', 'api', 'oko', 'package.json', 'package-lock.json', 'tsconfig.json', 'tsconfig.tooling.json', 'vite.config.mts'):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, checkout / name, ignore=shutil.ignore_patterns('__pycache__', 'frontend_build'))
        else:
            shutil.copy(source, checkout / name)
    (checkout / 'node_modules').symlink_to(root / 'node_modules', target_is_directory=True)
    build = checkout / 'oko/frontend_build'
    current = build / 'current.json'
    log = tmp_path / 'watch.log'
    # Each generation runs API checks and TypeScript before bundling; CI takes
    # longer than 15 seconds even for a successful build.
    build_timeout = 60
    with log.open('w') as output:
        process = subprocess.Popen(['node', '--import', 'tsx', str(checkout / 'scripts/build-frontend.mts'), '--watch'], cwd=checkout, env=dict(os.environ, OKO_PYTHON=sys.executable), stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + build_timeout
            while 'Watching frontend' not in log.read_text() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert 'Watching frontend' in log.read_text(), log.read_text()
            old = json.loads(current.read_text())
            style = checkout / 'frontend/style.css'
            style.write_text(style.read_text() + '\nbody { --watch-test: 1; }\n')
            deadline = time.monotonic() + build_timeout
            while time.monotonic() < deadline:
                manifest = json.loads(current.read_text())
                for name in manifest['assets']:
                    assert (build / 'generations' / manifest['generation'] / name).is_file()
                if manifest['source'] != old['source']:
                    break
                time.sleep(0.02)
            assert manifest['source'] != old['source'], log.read_text()
            assert manifest['generation'] != old['generation']
            for name in old['assets']:
                assert (build / 'generations' / old['generation'] / name).is_file()
        finally:
            process.terminate()
            process.wait(timeout=10)
