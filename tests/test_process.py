import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def launch(root: Path, fault: str = '', lease: str = '6', grace: str = '15'):
    env = dict(os.environ, PYTHONPATH=str(ROOT), OKO_TEST_FAULT=fault)
    return subprocess.Popen([sys.executable, '-m', 'tests.process_server', '--data', str(root), '--no-browser', '--demo', '--lease', lease, '--grace', grace], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def connect(root: Path, process):
    for _ in range(250):
        try:
            metadata = json.loads((root / 'runtime.json').read_text())
            client = httpx.Client(base_url=metadata['url'], headers={'Authorization': 'Bearer ' + metadata['token']}, timeout=2)
            client.get('/api/status').raise_for_status()
            client.close()
            return httpx.Client(base_url=metadata['url'], headers={'Authorization': 'Bearer ' + metadata['token']}, timeout=2)
        except (OSError, ValueError, httpx.HTTPError):
            assert process.poll() is None, 'server exited before readiness'
            time.sleep(.02)
    raise AssertionError('server not ready')


def wait_cards(client, count):
    for _ in range(250):
        cards = client.get('/api/cards').json()
        if len(cards) == count and all(c['complete'] for c in cards):
            return cards
        time.sleep(.02)
    raise AssertionError(cards)


def cleanup(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def test_restart_after_atomic_file_move(tmp_path: Path):
    process = launch(tmp_path, 'after_file_move')
    try:
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert all(not card['complete'] for card in client.get('/api/cards').json())
            client.post('/api/tabs/a/heartbeat')
            assert len(wait_cards(client, 3)) == 3
    finally:
        cleanup(process)


def test_discovery_transaction_survives_process_death(tmp_path: Path):
    process = launch(tmp_path, 'before_discovery_commit')
    try:
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
            assert len(wait_cards(client, 3)) == 3
    finally:
        cleanup(process)


def test_pending_deletion_survives_process_death(tmp_path: Path):
    process = launch(tmp_path, 'after_viewed_commit')
    try:
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
            cards = wait_cards(client, 3)
            item = cards[0]['id']
            try:
                client.post(f'/api/materials/{item}/viewed')
            except httpx.TransportError:
                pass
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.get(f'/api/materials/{item}').status_code == 404
            client.post('/api/tabs/a/heartbeat')
            assert len(wait_cards(client, 2)) == 2
            assert not (tmp_path / 'content' / item).exists()
            assert all('Око: первый материал'.encode() not in p.read_bytes()
                       for p in tmp_path.rglob('*') if p.is_file())
    finally:
        cleanup(process)


def test_single_instance_and_real_shutdown(tmp_path: Path):
    process = launch(tmp_path, lease='.2', grace='.3')
    try:
        with connect(tmp_path, process) as client:
            client.post('/api/tabs/a/heartbeat')
            client.post('/api/tabs/b/heartbeat')
            second = launch(tmp_path, lease='.2', grace='.3')
            # Keep both leases alive while the second launcher probes first.
            for _ in range(100):
                client.post('/api/tabs/b/heartbeat')
                if second.poll() is not None:
                    break
                time.sleep(.01)
            assert second.wait(timeout=2) == 0
            assert json.loads((tmp_path / 'runtime.json').read_text())['pid'] == process.pid
            client.post('/api/tabs/a/close')
            assert process.poll() is None
            client.post('/api/tabs/b/close')
        assert process.wait(timeout=2) == 0
        assert not (tmp_path / 'runtime.json').exists()
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            client.post('/api/stop')
        assert process.wait(timeout=2) == 0
    finally:
        cleanup(process)
