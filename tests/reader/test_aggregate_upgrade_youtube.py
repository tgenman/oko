"""Read a real pre-removal YouTube snapshot without recollection or regeneration."""
import json
import sqlite3
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.support.app import create_app


def test_upgrade_preserves_youtube_caption_theses_after_metadata_revision(tmp_path):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/legacy-youtube-reader.json').read_text())
    # Captured with e8affc8 through synthetic RSS, captions and model adapters:
    # metadata changed, the filter reran, but captions and theses ran only once.
    assert fixture['generation']['calls'] == ['filter', 'theses', 'filter']
    assert fixture['generation']['captions_calls'] == ['abcdefghijk']
    assert len(set(fixture['generation']['metadata_versions'])) == 2
    with sqlite3.connect(tmp_path / 'state.sqlite') as db:
        db.executescript(fixture['sql'])
    for name, contents in fixture['files'].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes.fromhex(contents))

    calls = []

    def unexpected_http(request):
        calls.append(str(request.url))
        raise AssertionError('Upgrade must not fetch a source or call a model')

    class UnexpectedCaptions:
        async def fetch(self, video):
            calls.append(video)
            raise AssertionError('Saved captions must survive upgrade')

    transport = httpx.MockTransport(unexpected_http)
    expected = fixture['expected']
    assert expected['captions'] == 'available'
    assert expected['theses'] and expected['provenance'] and expected['previous']
    for _ in range(2):
        app = create_app(tmp_path, token='test', rss_enabled=True,
                         rss_transport=transport, ai_transport=transport,
                         youtube_captions=UnexpectedCaptions())
        with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
            cards = client.get('/api/cards').json()
            assert [card['id'] for card in cards] == [expected['id']]
            response = client.get('/api/materials/' + expected['id'])
            assert response.status_code == 200
            material = response.json()
            for field in ('title', 'text', 'version', 'captions', 'theses', 'provenance', 'filter', 'images', 'previous'):
                assert material[field] == expected[field], field
            for version, saved in zip(material['previous'], fixture['previous'], strict=True):
                response = client.get(version['url'])
                assert response.status_code == 200
                previous = response.json()
                assert previous == saved
            for url, contents in fixture['media'].items():
                response = client.get(url)
                assert response.status_code == 200
                assert response.content == bytes.fromhex(contents)
        assert calls == []
