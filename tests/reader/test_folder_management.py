"""Folder identity and mutations through the public HTTP boundary."""
from tests.support.storage import database

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.subscriptions import saved_sources, add_source
from tests.support.rss import rss
from jsonschema import Draft202012Validator
from pathlib import Path
import json

HEADERS = {'Authorization': 'Bearer test-token'}
PATH = '/api/v3/folders/{folder}'


def bundled():
    return json.loads((Path(__file__).resolve().parents[2] / 'oko/reader/generated/openapi.json').read_text())


def validate_reply(spec, path, method, response):
    schema = spec['paths'][path][method]['responses'][str(response.status_code)]['content']['application/json']['schema']
    Draft202012Validator(dict(schema, components=spec['components'])).validate(response.json())


def test_rename_preserves_legacy_identity_settings_and_feed_links_after_restart(tmp_path):
    saved_sources(tmp_path, [('https://source.test/feed', 'Alpha', 'Old'), ('https://b.test/feed', 'Beta', 'Other')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        catalog = client.get('/api/v3/subscriptions').json()
        original = next(f for f in catalog['folders'] if f['title'] == 'Old')
        url = '/api/v3/folders/' + original['id']
        settings = '/api/v3/settings/folder/' + original['id']
        assert client.patch(settings, json={'prompt_filters': 'Keep this'}).status_code == 200
        response = client.patch(url, json={'title': '  New  '}, headers={'X-Oko-Action': 'rename-test'})
        assert response.status_code == 200, response.text
        validate_reply(bundled(), PATH, 'patch', response)
        assert response.json() == dict(original, title='New')
        assert client.get('/api/v3/commands/rename-test').json()['context'] == {'operation': 'rename_folder', 'folder_id': original['id']}
        assert client.get('/api/v3/settings/feed/' + catalog['feeds'][0]['id']).json()['effective']['prompt_filters']['value'] == 'Keep this'
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        restored = client.get('/api/v3/subscriptions').json()
        assert next(f for f in restored['folders'] if f['id'] == original['id'])['title'] == 'New'
        assert restored['feeds'] == catalog['feeds']
        assert client.get(settings).json()['overrides'] == {'prompt_filters': 'Keep this'}
        for title, code in [('', 422), ('   ', 422), ('x' * 201, 422), ('Other', 409)]:
            response = client.patch(url, json={'title': title})
            assert response.status_code == code, response.text
            validate_reply(bundled(), PATH, 'patch', response)
        assert client.patch(url, json={'title': 'other'}).status_code == 200
        assert client.patch('/api/v3/folders/missing', json={'title': 'New'}).status_code == 404
        assert client.get('/api/v3/status').status_code == 200


def test_new_rss_folders_use_opaque_ids_and_resolve_renamed_titles(tmp_path):
    def reply(request):
        return httpx.Response(200, text=rss() if request.url.path.endswith('/feed') else '<article>Fixture</article>')
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        first = add_source(client, 'https://a.test/feed', 'A', 'Old')
        identity = first['folder_id']
        assert client.patch('/api/v3/folders/' + identity, json={'title': 'New'}).status_code == 200
        same = add_source(client, 'https://b.test/feed', 'B', 'New')
        reused_title = add_source(client, 'https://c.test/feed', 'C', 'Old')
        assert same['folder_id'] == identity
        assert reused_title['folder_id'] != identity
        assert client.patch('/api/v3/feeds/' + reused_title['id'], json={'folder_id': identity}).status_code == 200
        old_id = reused_title['folder_id']
        assert client.delete('/api/v3/folders/' + old_id).status_code == 200
        recreated = add_source(client, 'https://d.test/feed', 'D', 'Old')
        assert recreated['folder_id'] not in (identity, old_id)


@pytest.mark.parametrize('connection', ['needs_connection', 'deleting', 'unavailable'])
def test_nonempty_folder_blocks_deletion_even_without_visible_items(tmp_path, connection):
    saved_sources(tmp_path, [('https://source.test/feed', 'Alpha', 'Folder')])
    with database(tmp_path) as db:
        db.execute('UPDATE feeds SET connection=?', (connection,))
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        catalog = client.get('/api/v3/subscriptions').json()
        folder = catalog['folders'][0]['id']
        response = client.delete('/api/v3/folders/' + folder, headers={'X-Oko-Action': 'delete-nonempty'})
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'folder_not_empty'
        validate_reply(bundled(), PATH, 'delete', response)
        assert client.get('/api/v3/subscriptions').json() == catalog
        receipt = client.get('/api/v3/commands/delete-nonempty').json()
        assert receipt['outcome'] == 'failed' and receipt['context']['folder_id'] == folder
        status = client.get('/api/v3/status')
        assert status.status_code == 200, status.text
        assert any({'kind': 'folder', 'folder_id': folder} in p['affected'] for p in status.json()['problems']['current'])


def test_empty_folder_deletion_cleans_settings_and_rechecks_new_members(tmp_path):
    saved_sources(tmp_path, [('https://a.test/feed', 'A', 'Empty'), ('https://b.test/feed', 'B', 'Other')])
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        catalog = client.get('/api/v3/subscriptions').json()
        empty, other = (next(f for f in catalog['folders'] if f['title'] == title)['id'] for title in ['Empty', 'Other'])
        feed = next(f for f in catalog['feeds'] if f['folder_id'] == empty)['id']
        assert client.patch('/api/v3/settings/folder/' + empty, json={'theses': True}).status_code == 200
        assert client.patch('/api/v3/feeds/' + feed, json={'folder_id': other}).status_code == 200
        # A feed arrives after the client has observed the empty folder.
        assert client.patch('/api/v3/feeds/' + feed, json={'folder_id': empty}).status_code == 200
        assert client.delete('/api/v3/folders/' + empty).status_code == 409
        assert client.patch('/api/v3/feeds/' + feed, json={'folder_id': other}).status_code == 200
        response = client.delete('/api/v3/folders/' + empty)
        assert response.status_code == 200, response.text
        validate_reply(bundled(), PATH, 'delete', response)
        result = client.get('/api/v3/subscriptions').json()
        assert [f['id'] for f in result['folders']] == [other]
        assert len(result['feeds']) == 2
        assert client.get('/api/v3/settings/folder/' + empty).status_code == 404
        assert client.delete('/api/v3/folders/' + empty).status_code == 404
