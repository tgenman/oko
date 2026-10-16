from tests.support.presence import presence, close_presence
from tests.support.reader import HEADERS, wait_for, setup_reader
from tests.support.models import model_transport
import json
import time
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource


def test_completed_theses_survive_restart_without_second_model_call(tmp_path: Path):
    calls = []
    options = dict(source=DemoSource(), token='test', ai_transport=model_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        wait_for(client, lambda: all(m['theses']['result'] for m in client.get('/api/v3/cards').json()))
    before = len([r for r in calls if r.url.path == '/v1/chat/completions'])
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        presence(client, 'reader')
        assert client.get('/api/v3/materials/' + item).json()['theses']['result']
        time.sleep(.1)
    assert len([r for r in calls if r.url.path == '/v1/chat/completions']) == before


def test_missing_theses_file_is_regenerated_after_restart(tmp_path: Path):
    calls = []
    options = dict(source=DemoSource(), token='test', ai_transport=model_transport(calls))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        item = setup_reader(client, theses=True)
        client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://127.0.0.1:8080', 'model': 'local:latest', 'local_execution': True})
        client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['theses']['result'])
    (tmp_path / 'content' / item / '1' / 'theses.md').unlink()
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        presence(client, 'reader')
        wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['theses']['result'])


def test_process_death_after_theses_move_retries_without_publishing_orphan(tmp_path: Path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from tests.support.processes import launch, connect, wait_cards, cleanup

    class ModelServer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/v1/models':
                self.answer({'data': [{'id': 'local:latest', 'owned_by': 'llamacpp', 'meta': {'size': 100}}]})
            else:
                self.answer({'model_path': '/models/local.gguf', 'build_info': 'b-test'})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            source = json.loads(body['messages'][1]['content'])['sources'][0]
            self.answer({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps({'points': [{'text': 'Контент удаляется при просмотре.', 'sources': [{'id': source['id'], 'url': source['url']}]}]})}}]})

    server = ThreadingHTTPServer(('127.0.0.1', 0), ModelServer)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    process = launch(tmp_path, 'after_theses_move', model_origin=f'http://127.0.0.1:{server.server_port}')
    try:
        with connect(tmp_path, process) as client:
            presence(client, 'a')
            cards = wait_cards(client, 3)
            item = cards[0]['id']
            client.put('/api/v3/processors/llamacpp', json={'endpoint': f'http://127.0.0.1:{server.server_port}', 'model': 'local:latest', 'local_execution': True})
            client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'theses': True})
        assert process.wait(timeout=5) == 79
        process = launch(tmp_path, model_origin=f'http://127.0.0.1:{server.server_port}')
        with connect(tmp_path, process) as client:
            assert client.get('/api/v3/materials/' + item).json()['theses']['result'] is None
            presence(client, 'a')
            result = wait_for(client, lambda: client.get('/api/v3/materials/' + item).json()['theses']['result'])
            assert result['points'][0]['text'] == 'Контент удаляется при просмотре.'
            client.post('/api/v3/materials/' + item + '/viewed')
            assert not (tmp_path / 'content' / item).exists()
            assert not (tmp_path / 'staging' / item).exists()
    finally:
        cleanup(process)
        server.shutdown()
        server.server_close()
        worker.join(2)
