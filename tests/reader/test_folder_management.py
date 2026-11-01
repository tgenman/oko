"""Folder identity and mutations through the public HTTP boundary."""
from tests.support.storage import database

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.subscriptions import configured_sources
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


@pytest.mark.parametrize('connection', ['needs_connection', 'deleting', 'unavailable'])
def test_nonempty_folder_blocks_deletion_even_without_visible_items(tmp_path, connection):
    configured_sources(tmp_path, [('https://source.test/feed', 'Alpha', 'Folder')])
    with database(tmp_path) as db:
        db.execute('UPDATE feeds SET connection = %s', (connection,))
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
        status = client.get('/api/v3/status/details')
        assert status.status_code == 200, status.text
        assert any({'kind': 'folder', 'folder_id': folder} in p['affected'] for p in status.json()['problems']['current'])


def test_empty_folder_deletion_cleans_settings_and_rechecks_new_members(tmp_path):
    configured_sources(tmp_path, [('https://a.test/feed', 'A', 'Empty'), ('https://b.test/feed', 'B', 'Other')])
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
