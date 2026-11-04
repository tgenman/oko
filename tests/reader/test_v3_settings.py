from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS
from tests.support.demo_source import DemoSource


def test_settings_inheritance_reset_and_command_receipt(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        path = '/api/v3/settings/oko/global'
        before = client.get(path)
        assert before.status_code == 200, before.text
        assert before.json()['effective']['theses']['origin'] == {'scope': 'default'}
        saved = client.patch(path, json={'theses': False}, headers={'X-Oko-Action': 'save-settings'})
        assert saved.status_code == 200, saved.text
        assert saved.json()['effective']['theses'] == {'value': False, 'origin': {'scope': 'oko', 'owner': 'global'}}
        receipt = client.get('/api/v3/commands/save-settings').json()
        assert receipt['outcome'] == 'succeeded'
        assert receipt['context'] == {'operation': 'settings_patch', 'target': {'scope': 'oko', 'owner': 'global'}, 'keys': ['theses']}
        assert client.patch(path, json={'theses': None}).json()['effective']['theses']['origin'] == {'scope': 'default'}
        assert client.patch(path, json={'theses': 'false'}).status_code == 422
        assert client.get('/api/v3/settings/oko/other').status_code == 400
        assert client.get('/api/v3/settings/folder/missing').status_code == 404
        assert client.get('/api/v3/commands/missing').json()['outcome'] == 'unknown'


def test_observations_cannot_replace_confirmed_outcome_and_survive_restart(tmp_path):
    context = {'operation': 'settings_patch', 'target': {'scope': 'oko', 'owner': 'global'}, 'keys': ['theses']}
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False}, headers={'X-Oko-Action': 'saved'}).status_code == 200
        for result in ('unknown', 'failed'):
            reply = client.post('/api/v3/command-observations', json={'id': 'saved', 'context': context, 'result': result})
            assert reply.status_code == 200, reply.text
            assert reply.json()['outcome'] == 'succeeded'
        reply = client.post('/api/v3/command-observations', json={'id': 'saved', 'context': {'operation': 'codex_save'}, 'result': 'unknown'})
        assert reply.status_code == 409, reply.text
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        assert client.get('/api/v3/commands/saved').json()['outcome'] == 'succeeded'


def test_processor_configuration_and_checks(tmp_path):
    from tests.support.codex import CodexService
    with TestClient(create_app(tmp_path, token='test', api_only=True, codex_client=CodexService()), headers=HEADERS) as client:
        assert client.get('/api/v3/processors/codex').status_code == 200
        assert client.get('/api/v3/processors/codex/models').json()['models'][0]['model'] == 'synthetic'
        config = {key: {'model': 'synthetic', 'reasoning_effort': 'low', 'fast': False} for key in ('filter', 'theses')}
        saved = client.put('/api/v3/processors/codex', json=config)
        assert saved.status_code == 200, saved.text
        assert client.get('/api/v3/processors/codex').json()['configuration'] == config
        checked = client.post('/api/v3/processors/codex/check')
        assert checked.status_code == 200, checked.text
        assert checked.json()['state'] == 'available'
        config['filter']['reasoning_effort'] = 'invalid'
        assert client.put('/api/v3/processors/codex', json=config).status_code == 400
        assert client.get('/api/v3/processors/llamacpp').json() == {'configuration': None}


def test_failed_edit_can_be_discarded_without_undoing_saved_configuration(tmp_path):
    from tests.support.codex import CodexService
    with TestClient(create_app(tmp_path, token='test', api_only=True, codex_client=CodexService()), headers=HEADERS) as client:
        config = {key: {'model': 'synthetic', 'reasoning_effort': 'low', 'fast': False} for key in ('filter', 'theses')}
        assert client.put('/api/v3/processors/codex', json=config).status_code == 200
        invalid = {**config, 'theses': {**config['theses'], 'model': 'not-in-catalog'}}
        reply = client.put('/api/v3/processors/codex', json=invalid, headers={'X-Oko-Action': 'bad-choice'})
        assert reply.status_code == 400
        receipt = client.get('/api/v3/commands/bad-choice').json()
        assert receipt['outcome'] == 'failed'
        assert receipt['error']['code'] == 'processor_choice_invalid'
        problem = next(p for p in client.get('/api/v3/status/details').json()['problems']['current'] if p['command_id'] == 'bad-choice')
        assert problem['code'] == 'command_failed'
        assert problem['recovery'] == [{'kind': 'configure_processor', 'processor_id': 'codex'}]
        reply = client.post('/api/v3/problems/' + problem['id'] + '/abandon')
        assert reply.status_code == 200, reply.text
        assert client.get('/api/v3/commands/bad-choice').json()['outcome'] == 'abandoned'
        assert client.get('/api/v3/processors/codex').json()['configuration'] == config


def test_general_theses_off_youtube_defaults_on_with_public_ai_and_explicit_denial_survives(tmp_path):
    from tests.support.subscriptions import add_source
    import httpx
    from tests.support.youtube import feed_xml
    options = dict(token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(lambda request:httpx.Response(200,text=feed_xml())))
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        assert client.get('/api/v3/settings/oko/global').json()['effective']['theses']['value'] is False
        feed = add_source(client,'https://www.youtube.com/feeds/videos.xml?channel_id=UCYO_jab_esuFRV4b17AJtAw')
        path = '/api/v3/settings/feed/'+feed['id']
        assert feed['public_ai'] is True
        assert client.get(path).json()['effective']['theses'] == {'value':True,'origin':{'scope':'default'}}
        client.patch('/api/v3/settings/folder/'+feed['folder_id'],json={'theses':False})
        assert client.get(path).json()['effective']['theses']['value'] is False
        client.patch('/api/v3/feeds/'+feed['id'],json={'public_ai':False})
    with TestClient(create_app(tmp_path,**options),headers=HEADERS) as client:
        assert client.get(path).json()['effective']['theses']['value'] is False
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['public_ai'] is False


def test_enabling_filter_requires_effective_prompt_and_rolls_back_invalid_patch(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        path = '/api/v3/settings/oko/global'
        rejected = client.patch(path, json={'filter_enabled': True})
        assert rejected.status_code == 400, rejected.text
        assert client.get(path).json()['effective']['filter_enabled']['value'] is False
        valid = client.patch(path, json={'filter_enabled': True, 'prompt_filters': 'Keep useful text'})
        assert valid.status_code == 200, valid.text
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        folder_path = '/api/v3/settings/folder/' + feed['folder_id']
        rejected = client.patch(folder_path, json={'prompt_filters': ''})
        assert rejected.status_code == 400, rejected.text
        effective = client.get('/api/v3/settings/feed/' + feed['id']).json()['effective']
        assert effective['filter_enabled']['value'] is True
        assert effective['prompt_filters']['value'] == 'Keep useful text'
        assert client.patch(folder_path, json={'filter_enabled': False, 'prompt_filters': ''}).status_code == 200
