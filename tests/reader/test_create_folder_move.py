"""Create a destination and move its feed in one public command."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from tests.support.app import create_app
from tests.support.subscriptions import saved_sources

HEADERS = {'Authorization': 'Bearer test-token'}


def test_create_and_move_preserves_identity_content_settings_and_survives_restart(tmp_path):
    saved_sources(tmp_path, [('https://a.test/feed', 'Alpha', 'Old'), ('https://b.test/feed', 'Beta', 'Other')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        before = client.get('/api/v3/subscriptions').json()
        alpha, beta = before['feeds']
        client.patch('/api/v3/settings/feed/' + alpha['id'], json={'prompt_filters': 'Keep this'})
        response = client.patch('/api/v3/feeds/' + alpha['id'], json={'new_folder': '  Cosmos  '}, headers={'X-Oko-Action': 'create-move'})
        assert response.status_code == 200, response.text
        spec = json.loads((Path(__file__).resolve().parents[2] / 'oko/reader/generated/openapi.json').read_text())
        schema = spec['paths']['/api/v3/feeds/{identity}']['patch']['responses']['200']['content']['application/json']['schema']
        Draft202012Validator(dict(schema, components=spec['components'])).validate(response.json())
        after = client.get('/api/v3/subscriptions').json()
        new = next(f for f in after['folders'] if f['title'] == 'Cosmos')
        assert response.json() == dict(alpha, folder_id=new['id'])
        assert next(f for f in after['feeds'] if f['id'] == beta['id']) == beta
        assert all(f in after['folders'] for f in before['folders'])
        assert client.get('/api/v3/settings/feed/' + alpha['id']).json()['overrides']['prompt_filters'] == 'Keep this'
        receipt = client.get('/api/v3/commands/create-move').json()
        assert receipt['outcome'] == 'succeeded'
        assert receipt['context'] == {'operation': 'feed_patch', 'feed_id': alpha['id'], 'fields': ['new_folder']}
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        assert client.get('/api/v3/subscriptions').json() == after


def test_invalid_create_move_never_leaves_an_empty_folder_or_moves_a_feed(tmp_path):
    saved_sources(tmp_path, [('https://a.test/feed', 'Alpha', 'Old')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        before = client.get('/api/v3/subscriptions').json()
        feed = before['feeds'][0]
        for body, status in [({'new_folder': ''}, 422), ({'new_folder': '   '}, 422), ({'new_folder': None}, 422),
                             ({'new_folder': 'x' * 201}, 422), ({'new_folder': ' Old '}, 400),
                             ({'new_folder': 'New', 'folder_id': feed['folder_id']}, 400),
                             ({'new_folder': 'New', 'read_history': True}, 400)]:
            response = client.patch('/api/v3/feeds/' + feed['id'], json=body)
            assert response.status_code == status, response.text
            assert client.get('/api/v3/subscriptions').json() == before
        assert client.patch('/api/v3/feeds/missing', json={'new_folder': 'Ghost'}).status_code == 404
        assert client.get('/api/v3/subscriptions').json() == before
        # Simulate a second client which saw the old catalog before another creation.
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'new_folder': 'New'}).status_code == 200
        accepted = client.get('/api/v3/subscriptions').json()
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'new_folder': 'New'}).status_code == 400
        assert client.get('/api/v3/subscriptions').json() == accepted
