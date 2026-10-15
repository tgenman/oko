"""Build/watch acceptance uses tiny fixtures, never rebuilds the whole reader."""
from pathlib import Path
import json
import subprocess
import time
import sys
import os
import re

from tests.support.build_tools import frontend_repository


def test_failed_build_preserves_previous_complete_generation(tmp_path: Path):
    checkout = tmp_path / 'checkout'
    frontend_repository(checkout)
    command = ['node', '--import', 'tsx', str(checkout / 'scripts/build-frontend.mts')]
    environment = dict(os.environ, OKO_PYTHON=sys.executable)
    first = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    current = checkout / 'oko/frontend_build/current.json'
    before = current.read_bytes()
    repeated = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert 'Frontend build cache hit' in repeated.stdout
    assert current.read_bytes() == before
    entry = re.search(r'<script[^>]+src="([^"]+)"', (checkout / 'frontend/index.html').read_text())
    assert entry is not None
    source_entry = Path('frontend') / entry[1].lstrip('/')
    original_entry = (checkout / source_entry).read_bytes()
    (checkout / source_entry).write_text('import "./missing.js";')
    failed = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert failed.returncode != 0
    assert current.read_bytes() == before
    manifest = json.loads(before)
    page = checkout / 'oko/frontend_build/generations' / manifest['generation'] / 'index.html'
    assert page.is_file()
    (checkout / source_entry).write_bytes(original_entry)
    schema = checkout / 'api/openapi.yaml'
    # A documentation-only edit passes compatibility but changes canonical
    # bytes, so unchanged generated libraries must still fail the build.
    document = json.loads(schema.read_text())
    document['info']['title'] = 'Synthetic drift title'
    schema.write_text(json.dumps(document))
    stale = subprocess.run(command, cwd=checkout, env=environment, capture_output=True, text=True)
    assert stale.returncode != 0
    assert 'Stale Python API library' in stale.stderr
    assert current.read_bytes() == before


def test_watch_publishes_complete_generations_and_retains_open_tab_assets(tmp_path: Path):
    checkout = tmp_path / 'checkout'
    frontend_repository(checkout)
    build = checkout / 'oko/frontend_build'
    current = build / 'current.json'
    log = tmp_path / 'watch.log'
    # Tiny fixture; a dead child must fail immediately, not consume the timeout.
    build_timeout = 15
    with log.open('w') as output:
        process = subprocess.Popen(['node', '--import', 'tsx', str(checkout / 'scripts/build-frontend.mts'), '--watch'], cwd=checkout, env=dict(os.environ, OKO_PYTHON=sys.executable), stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + build_timeout
            while 'Watching frontend' not in log.read_text() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert 'Watching frontend' in log.read_text(), log.read_text()
            old = json.loads(current.read_text())
            style = checkout / 'frontend/public/reader-surfaces.css'
            style.write_text(style.read_text() + '\nbody { --watch-test: 1; }\n')
            deadline = time.monotonic() + build_timeout
            while process.poll() is None and time.monotonic() < deadline:
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
