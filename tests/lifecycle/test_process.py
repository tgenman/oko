from tests.support.cards import read_all_cards
from tests.support.presence import presence, close_presence
from tests.support.processes import launch, connect, wait_cards, cleanup
import json
from pathlib import Path
import time


def test_restart_after_atomic_file_move(tmp_path: Path):
    process = launch(tmp_path, 'after_file_move')
    try:
        assert process.wait(timeout=10) == 79
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            assert client.post('/api/v3/collect').status_code==200
            assert len(wait_cards(client, 3)) == 3
    finally:
        cleanup(process)


def test_discovery_transaction_survives_process_death(tmp_path: Path):
    process = launch(tmp_path, 'before_discovery_commit')
    try:
        # Collection starts without a browser, including the injected crash.
        assert process.wait(timeout=10) == 79
        from tests.support.storage import database
        with database(tmp_path) as db:
            assert db.execute('SELECT count(*) FROM publications').fetchone()[0] == 0
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.post('/api/v3/collect').status_code == 200
            assert len(wait_cards(client, 3)) == 3
    finally:
        cleanup(process)


def test_viewed_publication_survives_process_death(tmp_path: Path):
    process = launch(tmp_path)
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            item = wait_cards(client, 3)[0]['id']
            before = client.get(f'/api/v3/publication/{item}').json()
            assert client.post(f'/api/v3/materials/{item}/viewed').status_code == 200
            process.kill()
        process.wait(timeout=5)
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert client.get(f'/api/v3/publication/{item}').json()['text'] == before['text']
            assert item not in [card['id'] for card in read_all_cards(client, '/api/v3/cards')]
            assert item in [card['id'] for card in read_all_cards(client, '/api/v3/cards?show_read=true')]
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
            assert client.get('/api/v3/status').json()['runtime_state'] == 'running'
            assert client.post('/api/v3/stop').status_code == 200
        assert process.wait(timeout=2) == 0
        assert not (tmp_path / 'runtime.json').exists()
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            client.post('/api/v3/stop')
        assert process.wait(timeout=2) == 0
    finally:
        cleanup(process)


def test_stop_exits_even_if_source_ignores_cancellation(tmp_path: Path):
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
            reply = client.post('/api/v3/stop')
            assert reply.status_code == 200
        assert process.wait(timeout=3) == 0
        assert time.monotonic() - started < 3
    finally:
        cleanup(process)


def test_folder_completion_survives_process_death(tmp_path: Path):
    process = launch(tmp_path)
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            originals = wait_cards(client, 3)
            preview = client.post('/api/v3/folders/fixture/completion/preview').json()
            assert client.post('/api/v3/folders/fixture/completion', json={'token': preview['token']}).status_code == 200
            process.kill()
        process.wait(timeout=5)
        process = launch(tmp_path)
        with connect(tmp_path, process) as client:
            assert read_all_cards(client, '/api/v3/cards') == []
            assert {card['id'] for card in read_all_cards(client, '/api/v3/cards?show_read=true')} == {card['id'] for card in originals}
            for card in originals:
                assert client.get('/api/v3/publication/' + card['id']).status_code == 200
    finally:
        cleanup(process)
