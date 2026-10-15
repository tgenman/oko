"""Acceptance at the contract build CLI and existing reader HTTP boundaries."""
import json
from pathlib import Path
import subprocess
import sys
from functools import lru_cache
import copy

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from tests.support.app import create_app

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def bundled():
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/api.py'), 'bundle'], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def validate_reply(spec, path, method, response):
    schema = spec['paths'][path][method]['responses'][str(response.status_code)]['content']['application/json']['schema']
    Draft202012Validator(dict(schema, components=spec['components'])).validate(response.json())


def test_contract_build_covers_real_reader_and_validates_baseline(tmp_path):
    spec = bundled()
    with TestClient(create_app(tmp_path, token='synthetic')) as client:
        routes = {(route.path, method.lower()) for route in client.app.routes if getattr(route, 'path', '').startswith('/api/') for method in route.methods}
        described = {(path, method) for path, operations in spec['paths'].items() for method in operations if method in {'get','post','put','patch','delete'}}
        assert described == routes
        assert len({operation['operationId'] for operations in spec['paths'].values() for operation in operations.values()}) == len(routes)
        for path in ['/api/status', '/api/navigation', '/api/cards', '/api/subscriptions', '/api/settings/oko/global', '/api/youtube', '/api/debug']:
            response = client.get(path, headers={'Authorization':'Bearer synthetic'})
            assert response.status_code == 200
            validate_reply(spec, '/api/settings/{scope}/{owner}' if path.startswith('/api/settings/') else path, 'get', response)
        response = client.get('/api/status')
        assert response.status_code == 403
        validate_reply(spec, '/api/status', 'get', response)
        response = client.patch('/api/settings/oko/global',json={'theses':'wrong'},headers={'Authorization':'Bearer synthetic'})
        assert response.status_code == 422
        validate_reply(spec, '/api/settings/{scope}/{owner}', 'patch', response)


def test_real_settings_import_diagnostics_and_exact_history_obey_contract(tmp_path):
    spec = bundled()
    headers = {'Authorization':'Bearer synthetic'}
    with TestClient(create_app(tmp_path, token='synthetic')) as client:
        for payload in [{'prompt_theses':'Synthetic'}, {'prompt_theses':None}, {}]:
            response = client.patch('/api/settings/oko/global', json=payload, headers=headers)
            assert response.status_code == 200
            validate_reply(spec, '/api/settings/{scope}/{owner}', 'patch', response)
        response = client.post('/api/debug', json={'source':'synthetic','message':'example'}, headers=headers)
        assert response.status_code == 200
        validate_reply(spec, '/api/debug', 'post', response)
        assert response.json()['entry']['message'] == 'example'
        opml = '<opml><body><outline text="Synthetic" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id=synthetic"/></body></opml>'
        for path in ['/api/import/preview', '/api/import']:
            response = client.post(path,json={'opml':opml},headers=headers)
            assert response.status_code == 200
            validate_reply(spec,path,'post',response)
        feed = response.json()['feeds'][0]['id']
        response = client.post(f'/api/feeds/{feed}/history',json={'mode':'count','count':'9007199254740993'},headers=headers)
        assert response.status_code == 200
        validate_reply(spec,'/api/feeds/{identity}/history','post',response)
        assert response.json()['count_text'] == '9007199254740993'
        response = client.patch(f'/api/feeds/{feed}/history',json={'paused':True},headers=headers)
        assert response.status_code == 200
        validate_reply(spec,'/api/feeds/{identity}/history','patch',response)
        response = client.delete(f'/api/feeds/{feed}/history',headers=headers)
        assert response.status_code == 200
        validate_reply(spec,'/api/feeds/{identity}/history','delete',response)
        response = client.get('/api/materials/absent',headers=headers)
        assert response.status_code == 404
        validate_reply(spec,'/api/materials/{item}','get',response)


def test_build_rejects_external_references_before_fetching_them(tmp_path):
    spec = copy.deepcopy(bundled())
    spec['paths']['/api/status']['get']['responses']['200']['content']['application/json']['schema'] = {'$ref':'https://example.invalid/private'}
    invalid = tmp_path / 'invalid.json'
    invalid.write_text(json.dumps(spec))
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/api.py'), 'validate', '--spec', str(invalid)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'External contract references are not supported' in result.stderr
