from tests.support.cards import read_all_cards
from tests.support.presence import presence, close_presence
from pathlib import Path
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource


def test_reader_keeps_opened_original_then_deletes_explicitly(tmp_path: Path):
    app = create_app(tmp_path, source=DemoSource(), token="test-token")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app, headers=headers) as client:
        import re
        html = client.get('/').text
        assert 'type="module"' in html
        entry = re.search(r'<script[^>]+src="([^"]+)"', html)
        assert entry is not None
        module = client.get(entry[1])
        assert module.status_code == 200
        assert module.headers['content-type'].startswith('text/javascript')
        assert presence(client, 'reader').status_code == 201
        import time
        for _ in range(100):
            cards = read_all_cards(client, '/api/v3/cards')
            if len(cards) == 3 and all(not c['loading'] for c in cards):
                break
            time.sleep(.02)
        assert len(cards) == 3
        item = cards[0]['id']
        material = client.get(f'/api/v3/publication/{item}').json()
        assert material['title'] == 'Око: первый материал'
        assert material['source_url'] == 'https://example.org/oko/1'
        assert len(read_all_cards(client, '/api/v3/cards')) == 3
        assert material['text']
        assert not list(tmp_path.glob('content/**/*.md'))
        assert client.post(f'/api/v3/materials/{item}/viewed').status_code == 200
        assert client.post(f'/api/v3/materials/{item}/viewed').status_code == 200
        assert client.get(f'/api/v3/publication/{item}').status_code == 404
    with TestClient(create_app(tmp_path, source=DemoSource(), token="test-token"), headers=headers) as client:
        presence(client, 'reader')
        time.sleep(.1)
        assert len(read_all_cards(client, '/api/v3/cards')) == 2
        assert not (tmp_path / 'content' / item).exists()
