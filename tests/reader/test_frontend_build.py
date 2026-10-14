from pathlib import Path
import json
import shutil
import subprocess
import time
import sys
import os

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
    for name in ('frontend', 'scripts', 'api', 'oko', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.mjs'):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, checkout / name, ignore=shutil.ignore_patterns('__pycache__', 'frontend_build'))
        else:
            shutil.copy(source, checkout / name)
    (checkout / 'node_modules').symlink_to(root / 'node_modules', target_is_directory=True)
    command = ['node', str(checkout / 'scripts/build-frontend.mjs')]
    environment = dict(os.environ, OKO_PYTHON=sys.executable)
    first = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    current = checkout / 'oko/frontend_build/current.json'
    before = current.read_bytes()
    (checkout / 'frontend/app.js').write_text('import "./missing.js";')
    failed = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert failed.returncode != 0
    assert current.read_bytes() == before
    manifest = json.loads(before)
    page = checkout / 'oko/frontend_build/generations' / manifest['generation'] / 'index.html'
    assert page.is_file()
    shutil.copy(root / 'frontend/app.js', checkout / 'frontend/app.js')
    schema = checkout / 'api/openapi.yaml'
    schema.write_text(schema.read_text().replace('version: 0.1.0', 'version: 0.2.0'))
    # Change canonical bytes without silently refreshing either saved library.
    schema.write_text(schema.read_text() + '\nx-drift-test: true\n')
    stale = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert stale.returncode != 0
    assert 'Stale Python API library' in stale.stderr
    assert current.read_bytes() == before


def test_watch_publishes_complete_generations_and_retains_open_tab_assets(tmp_path: Path):
    root = Path(__file__).resolve().parents[2]
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    for name in ('frontend', 'scripts', 'api', 'oko', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.mjs'):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, checkout / name, ignore=shutil.ignore_patterns('__pycache__', 'frontend_build'))
        else:
            shutil.copy(source, checkout / name)
    (checkout / 'node_modules').symlink_to(root / 'node_modules', target_is_directory=True)
    build = checkout / 'oko/frontend_build'
    current = build / 'current.json'
    log = tmp_path / 'watch.log'
    with log.open('w') as output:
        process = subprocess.Popen(['node', str(checkout / 'scripts/build-frontend.mjs'), '--watch'], cwd=checkout, env=dict(os.environ, OKO_PYTHON=sys.executable), stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 15
            while 'Watching frontend' not in log.read_text() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert 'Watching frontend' in log.read_text(), log.read_text()
            old = json.loads(current.read_text())
            style = checkout / 'frontend/style.css'
            style.write_text(style.read_text() + '\nbody { --watch-test: 1; }\n')
            deadline = time.monotonic() + 15
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
