"""Read a real pre-removal YouTube snapshot without recollection or regeneration."""
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
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


@pytest.mark.parametrize('filter_state', ['pending', 'hidden'])
def test_upgrade_preserves_caption_theses_while_filter_blocks_reading(tmp_path, filter_state):
    from tests.support.models import model_transport
    from tests.support.reader import wait_for

    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/legacy-youtube-reader.json').read_text())
    with sqlite3.connect(tmp_path / 'state.sqlite') as db:
        db.executescript(fixture['sql'])
        if filter_state == 'pending':
            db.execute("UPDATE jobs SET status='pending',next=0 WHERE kind='filter'")
    for name, contents in fixture['files'].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes.fromhex(contents))
    if filter_state == 'hidden':
        for path in tmp_path.glob('content/**/filter.md'):
            raw, text = path.read_text().split('\n\n', 1)
            header = json.loads(raw)
            header['result'] = dict(hidden=True, reason='Synthetic hidden material', removed=[])
            path.write_text(json.dumps(header) + '\n\n' + text)
    calls = []
    native = model_transport([])
    def response(request):
        if request.url.path != '/v1/chat/completions':
            return native.handler(request)
        task = json.loads(json.loads(request.content)['messages'][1]['content'])
        calls.append(task['operation'])
        if task['operation'] != 'filter':
            return native.handler(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(dict(hidden=False, reason='', removed=[]))}}]})
    with TestClient(create_app(tmp_path, token='test', ai_transport=httpx.MockTransport(response)), headers={'Authorization': 'Bearer test'}) as client:
        expected = fixture['expected']
        if filter_state == 'hidden':
            assert client.post('/api/materials/' + expected['id'] + '/filter/restore').status_code == 200
        client.post('/api/tabs/test/heartbeat')
        material = wait_for(client, lambda: m if (m := client.get('/api/materials/' + expected['id']).json()).get('theses') else None)
        assert material['theses'] == expected['theses']
        assert material['provenance'] == expected['provenance']
        assert calls == (['filter'] if filter_state == 'pending' else [])
