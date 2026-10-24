from tests.support.presence import presence, close_presence
from tests.support.processes import launch, connect, wait_cards, cleanup
import json
from pathlib import Path
import time
import httpx
import pytest


def test_restart_after_atomic_file_move(tmp_path: Path):
    process = launch(tmp_path, 'after_file_move')
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert all(not (card['availability'] == 'available') for card in client.get('/api/v3/cards').json())
            presence(client, 'a')
            assert client.post('/api/v3/collect').status_code==200
            assert len(wait_cards(client, 3)) == 3
    finally:
        cleanup(process)


def test_discovery_transaction_survives_process_death(tmp_path: Path):
    process = launch(tmp_path, 'before_discovery_commit')
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            assert client.post('/api/v3/collect').status_code==200
            assert len(wait_cards(client, 3)) == 3
    finally:
        cleanup(process)


def test_pending_deletion_survives_process_death(tmp_path: Path):
    process = launch(tmp_path, 'after_viewed_commit')
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            cards = wait_cards(client, 3)
            item = cards[0]['id']
            try:
                client.post(f'/api/v3/materials/{item}/viewed')
            except httpx.TransportError:
                pass
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.get(f'/api/v3/materials/{item}').status_code == 404
            presence(client, 'a')
            assert len(wait_cards(client, 2)) == 2
            for _ in range(100):
                if not any(w['phase']=='deleting' for w in client.get('/api/v3/status').json()['work']):
                    break
                time.sleep(.02)
            assert not (tmp_path / 'content' / item).exists()
            client.post('/api/v3/stop')
        assert process.wait(timeout=5)==0
        # Scan stable files after SQLite closes its transient rollback journal.
        assert all('Око: первый материал'.encode() not in p.read_bytes()
                       for p in tmp_path.rglob('*') if p.is_file())
    finally:
        cleanup(process)


def test_single_instance_and_real_shutdown(tmp_path: Path):
    from tests.support.processes import ProcessClock
    clock = ProcessClock(tmp_path)
    process = launch(tmp_path, clock=clock, lease='.2', grace='.3')
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            presence(client, 'b')
            second = launch(tmp_path, clock=clock, lease='.2', grace='.3')
            # The controlled clock keeps both clients alive during the probe.
            for _ in range(100):
                presence(client, 'b')
                if second.poll() is not None:
                    break
                time.sleep(.01)
            assert second.wait(timeout=2) == 0
            assert json.loads((tmp_path / 'runtime.json').read_text())['pid'] == process.pid
            close_presence(client, 'a')
            assert process.poll() is None
            close_presence(client, 'b')
            clock.advance(.31)
        assert process.wait(timeout=2) == 0
        assert not (tmp_path / 'runtime.json').exists()
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            client.post('/api/v3/stop')
        assert process.wait(timeout=2) == 0
    finally:
        cleanup(process)


@pytest.mark.parametrize('action', ['stop', 'last-close'])
def test_stop_exits_even_if_source_ignores_cancellation(tmp_path: Path, action):
    from tests.support.processes import ProcessClock
    clock = ProcessClock(tmp_path)
    process = launch(tmp_path, 'resist_cancel', clock=clock, lease='.2', grace='.3')
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            for _ in range(100):
                if (tmp_path / 'fetch-started').exists():
                    break
                time.sleep(.02)
            assert (tmp_path / 'fetch-started').exists()
            started = time.monotonic()
            reply = client.post('/api/v3/stop') if action == 'stop' else close_presence(client,'a')
            assert reply.status_code == 200
            if action == 'last-close':
                clock.advance(.31)
        assert process.wait(timeout=3) == 0
        assert time.monotonic() - started < 3
    finally:
        cleanup(process)


@pytest.mark.parametrize('fault', ['after_viewed_commit', 'after_delete_files'])
def test_folder_completion_survives_death_during_atomic_set_or_physical_delete(tmp_path: Path, fault):
    process = launch(tmp_path, fault)
    try:
        with connect(tmp_path, process) as client:
            client.patch('/api/v3/settings/oko/global', json={})
            presence(client, 'a')
            originals = wait_cards(client, 3)
            preview = client.post('/api/v3/folders/demo/completion/preview').json()
            try:
                client.post('/api/v3/folders/demo/completion', json={'token': preview['token']})
            except httpx.TransportError:
                pass
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.get('/api/v3/cards').json() == []
            presence(client, 'b')
            for _ in range(150):
                if not any(w['phase']=='deleting' for w in client.get('/api/v3/status').json()['work']):
                    break
                time.sleep(.02)
            assert not any(w['phase']=='deleting' for w in client.get('/api/v3/status').json()['work'])
            for card in originals:
                assert client.get('/api/v3/materials/' + card['id']).status_code == 404
                assert not (tmp_path / 'content' / card['id']).exists()
            client.post('/api/v3/collect')
            time.sleep(.1)
            assert client.get('/api/v3/cards').json() == []
    finally:
        cleanup(process)
