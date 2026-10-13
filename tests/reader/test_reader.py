from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource


def test_reader_keeps_opened_original_then_deletes_explicitly(tmp_path: Path):
    app = create_app(tmp_path, source=DemoSource(), token="test-token")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app, headers=headers) as client:
        assert 'type="module"' in client.get('/').text
        module = client.get('/opened-material.js')
        assert module.status_code == 200
        assert module.headers['content-type'].startswith('text/javascript')
        assert client.post('/api/tabs/reader/heartbeat').status_code == 200
        import time
        for _ in range(100):
            cards = client.get('/api/cards').json()
            if len(cards) == 3 and all(c['complete'] for c in cards):
                break
            time.sleep(.02)
        assert len(cards) == 3
        item = cards[0]['id']
        material = client.get(f'/api/materials/{item}').json()
        assert material['title'] == 'Око: первый материал'
        assert material['url'] == 'https://example.org/oko/1'
        assert len(client.get('/api/cards').json()) == 3
        assert list(tmp_path.glob('content/*/1/original.md'))
        assert client.post(f'/api/materials/{item}/viewed').status_code == 200
        assert client.post(f'/api/materials/{item}/viewed').status_code == 200
        assert client.get(f'/api/materials/{item}').status_code == 404
    with TestClient(create_app(tmp_path, source=DemoSource(), token="test-token"), headers=headers) as client:
        client.post('/api/tabs/reader/heartbeat')
        time.sleep(.1)
        assert len(client.get('/api/cards').json()) == 2
        assert not (tmp_path / 'content' / item).exists()
