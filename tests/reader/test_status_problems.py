from oko.collection.contracts import DiscoveryResult, FetchResult
from tests.support.presence import presence
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.codex import CodexService, configure_codex
from tests.support.reader import HEADERS
from tests.support.reader import wait_for
from oko.collection.contracts import Publication, SourceError
from tests.support.synthetic_material import material as Material


class FailedDownloads:
    changes = None
    fixed = False

    async def discover(self, cursor):
        return DiscoveryResult(tuple([Publication('one', 'https://source.test/one', 1800000000),
                 Publication('two', 'https://source.test/two', 1800000001)] if cursor is None else []), 'done')

    async def fetch(self, publication):
        if not self.fixed:
            raise SourceError('Синтетический сбой загрузки', code='source-connection')
        return FetchResult(Material('Заголовок ' + publication.id, 'Текст'))


def test_source_failures_group_objects_and_retry_is_not_completion(tmp_path):
    source = FailedDownloads()
    with TestClient(create_app(tmp_path, token='test', source=source), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''})
        presence(client, 'reader')
        state = wait_for(client, lambda: (state if len((state := client.get('/api/v3/status/details').json())['problems']['current']) and
                                         len(state['problems']['current']) == 1 and len(state['problems']['current'][0]['affected']) == 2 else None))
        problem = state['problems']['current'][0]
        assert problem['code'] == 'source_unavailable'
        assert problem['recovery'][0]['kind'] == 'retry_material'
        assert state['problems']['count'] == 1
        assert {o['kind'] for o in problem['affected']} == {'material'}
        item = problem['affected'][0]['material_id']
        assert client.post('/api/v3/materials/' + item + '/retry').status_code == 200
        retrying = client.get('/api/v3/status/details').json()['problems']
        assert retrying['count'] == 1
        assert retrying['current'][0]['id'] == problem['id']
        source.fixed = True
        for obj in problem['affected']:
            client.post('/api/v3/materials/' + obj.get('material_id') + '/retry')
        wait_for(client, lambda: client.get('/api/v3/status/details').json()['problems']['count'] == 0)


def test_missing_model_is_one_current_problem_and_settings_fix_it(tmp_path):
    with TestClient(create_app(tmp_path, token='test', codex_client=CodexService()), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':True})
        response = client.get('/api/v3/status/details')
        assert response.status_code == 200
        problems = response.json()['problems']
        assert len(problems['current']) == 1
        problem = problems['current'][0]
        assert problem['code'] == 'processor_not_configured'
        assert problem['recovery'] == [{'kind':'configure_processor','processor_id':'codex'}]
        assert problems['count'] == 1
        configure_codex(client)
        fixed = client.get('/api/v3/status/details').json()['problems']
        assert fixed['current'] == []
        assert fixed['count'] == 0


def test_settings_error_is_durable_and_successful_retry_completes_it(tmp_path):
    options = dict(token='test', codex_client=CodexService())
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        configure_codex(client)
        response = client.patch('/api/v3/settings/oko/global', json={'theses':'invalid'})
        assert response.status_code == 422
        client.post('/api/v3/command-observations',json={'id':'invalid-setting','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['theses']},'result':'failed'})
        problems = client.get('/api/v3/status/details').json()['problems']
        assert problems['count'] == 1
        problem = problems['current'][0]
        assert problem['code'] == 'command_failed'
        assert problem['recovery'] == [{'kind':'change_setting','target':{'scope':'oko','owner':'global'},'keys':['theses']}]
        assert client.delete('/api/v3/diagnostics').status_code == 200
        assert client.get('/api/v3/status/details').json()['problems']['count'] == 1
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/status/details').json()['problems']['current'][0]['id'] == problem['id']
        assert client.patch('/api/v3/settings/oko/global', json={'theses':False}).status_code == 200
        result = client.get('/api/v3/status/details').json()['problems']
        assert result['count'] == 0
        assert result['recent'][0]['outcome'] == 'resolved'


def test_lost_successful_reply_is_reconciled_and_only_failed_draft_can_be_abandoned(tmp_path):
    with TestClient(create_app(tmp_path, token='test', codex_client=CodexService()), headers=HEADERS) as client:
        configure_codex(client)
        assert client.patch('/api/v3/settings/oko/global', json={'theses':False}, headers={'X-Oko-Action':'lost-reply'}).status_code == 200
        response = client.post('/api/v3/command-observations', json={'id':'lost-reply','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['theses']},'result':'unknown'})
        assert response.status_code == 200
        assert response.json()['outcome'] == 'succeeded'
        assert client.get('/api/v3/status/details').json()['problems']['count'] == 0
        client.patch('/api/v3/settings/oko/global', json={'theses':'invalid'})
        client.post('/api/v3/command-observations',json={'id':'bad-setting','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['theses']},'result':'failed'})
        failed = client.get('/api/v3/status/details').json()['problems']['current'][0]
        assert client.post('/api/v3/problems/' + failed['id'] + '/abandon').status_code == 200
        result = client.get('/api/v3/status/details').json()['problems']
        assert result['count'] == 0
        assert result['recent'][0]['outcome'] == 'irrelevant'


def test_viewing_removes_scoped_diagnostic_content_from_server_and_disk(tmp_path):
    from tests.support.synthetic_source import SyntheticSource
    from tests.support.reader import setup_reader
    with TestClient(create_app(tmp_path, token='test', source=SyntheticSource()), headers=HEADERS) as client:
        item=setup_reader(client,theses=False)
        title=client.get('/api/v3/publication/'+item).json()['title']
        assert client.post('/api/v3/diagnostics',json={'source':'material','message':title,'material_ids':[item]}).status_code==200
        assert title in client.get('/api/v3/diagnostics').text
        assert client.post('/api/v3/materials/'+item+'/viewed').status_code==200
        assert title not in client.get('/api/v3/diagnostics').text
        assert title not in (tmp_path/'debug.json').read_text()
