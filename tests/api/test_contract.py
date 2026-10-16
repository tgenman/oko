"""Acceptance at the contract build CLI and existing reader HTTP boundaries."""
import json
from pathlib import Path
from functools import lru_cache

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from tests.support.app import create_app

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def bundled():
    return json.loads((ROOT / 'oko/reader/generated/openapi.json').read_text())


def validate_reply(spec, path, method, response):
    schema = spec['paths'][path][method]['responses'][str(response.status_code)]['content']['application/json']['schema']
    Draft202012Validator(dict(schema, components=spec['components'])).validate(response.json())


def test_v3_contract_covers_all_real_reader_routes_and_responses(tmp_path):
    spec = bundled()
    with TestClient(create_app(tmp_path, token='synthetic')) as client:
        routes = {(path,method) for path, methods in client.app.openapi()['paths'].items() if path.startswith('/api/v3/') for method in methods if method in {'get','post','put','patch','delete'}}
        described = {(path, method) for path, operations in spec['paths'].items() for method in operations if method in {'get','post','put','patch','delete'}}
        assert described == routes
        assert len(routes) == 50
        assert len({operation['operationId'] for operations in spec['paths'].values() for operation in operations.values()}) == len(routes)
        for path in ['/api/v3/status', '/api/v3/reading-composition', '/api/v3/cards', '/api/v3/subscriptions', '/api/v3/settings/oko/global', '/api/v3/youtube', '/api/v3/diagnostics']:
            response = client.get(path, headers={'Authorization':'Bearer synthetic'})
            assert response.status_code == 200
            validate_reply(spec, '/api/v3/settings/{scope}/{owner}' if path.startswith('/api/v3/settings/') else path, 'get', response)
        response = client.get('/api/v3/status')
        assert response.status_code == 403
        validate_reply(spec, '/api/v3/status', 'get', response)
        response = client.patch('/api/v3/settings/oko/global',json={'theses':'wrong'},headers={'Authorization':'Bearer synthetic'})
        assert response.status_code == 422
        validate_reply(spec, '/api/v3/settings/{scope}/{owner}', 'patch', response)


def test_real_settings_diagnostics_and_exact_history_obey_contract(tmp_path):
    from tests.support.subscriptions import saved_sources
    saved_sources(tmp_path, [('https://www.youtube.com/feeds/videos.xml?channel_id=synthetic','Synthetic','Без папки')])
    spec = bundled()
    headers = {'Authorization':'Bearer synthetic'}
    with TestClient(create_app(tmp_path, token='synthetic')) as client:
        for payload in [{'prompt_theses':'Synthetic'}, {'prompt_theses':None}, {}]:
            response = client.patch('/api/v3/settings/oko/global', json=payload, headers=headers)
            assert response.status_code == 200
            validate_reply(spec, '/api/v3/settings/{scope}/{owner}', 'patch', response)
        response = client.post('/api/v3/diagnostics', json={'source':'synthetic','message':'example'}, headers=headers)
        assert response.status_code == 200
        validate_reply(spec, '/api/v3/diagnostics', 'post', response)
        assert response.json()['entry']['message'] == 'example'
        feed = client.get('/api/v3/subscriptions', headers=headers).json()['feeds'][0]['id']
        response = client.post(f'/api/v3/feeds/{feed}/history',json={'mode':'count','count':'9007199254740993'},headers=headers)
        assert response.status_code == 200
        validate_reply(spec,'/api/v3/feeds/{identity}/history','post',response)
        assert response.json()['history']['selection']['count'] == '9007199254740993'
        response = client.patch(f'/api/v3/feeds/{feed}/history',json={'paused':True},headers=headers)
        assert response.status_code == 200
        validate_reply(spec,'/api/v3/feeds/{identity}/history','patch',response)
        response = client.delete(f'/api/v3/feeds/{feed}/history',headers=headers)
        assert response.status_code == 200
        validate_reply(spec,'/api/v3/feeds/{identity}/history','delete',response)
        response = client.get('/api/v3/materials/absent',headers=headers)
        assert response.status_code == 404
        validate_reply(spec,'/api/v3/materials/{item}','get',response)
