from tests.support.subscriptions import saved_sources
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.collection.demo import DemoSource


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


def test_inheritance_and_atomic_feed_validation(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        folder = feed['folder_id']
        assert client.patch('/api/v3/settings/folder/' + folder, json={'originals': False}).status_code == 200
        path = '/api/v3/settings/feed/' + feed['id']
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.json()['effective']['originals'] == {'value': False, 'origin': {'scope': 'folder', 'owner': folder}}
        for field in ('folder_id', 'importance', 'initial_days', 'public_ai', 'read_history'):
            response = client.patch('/api/v3/feeds/' + feed['id'], json={field: None, 'importance': 'high'} if field != 'importance' else {'importance': None})
            assert response.status_code == 422, response.text
        assert client.get(path).json()['feed']['importance'] == feed['importance']


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


def test_diagnostics_validate_timezone_mask_and_deduplicate_across_restart(tmp_path):
    event = {'source': 'Synthetic client', 'message': 'token=secret-value', 'event_id': 'same-event', 'occurred_at': '2026-10-15T13:00:00+03:00'}
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        invalid = client.post('/api/v3/diagnostics', json={**event, 'occurred_at': '2026-10-15T13:00:00'})
        assert invalid.status_code == 422, invalid.text
        for _ in range(2):
            reply = client.post('/api/v3/diagnostics', json=event)
            assert reply.status_code == 200, reply.text
            assert reply.json()['state'] == 'accepted'
        entry = reply.json()['entry']
        assert entry['count'] == 1
        assert entry['last_at'] == '2026-10-15T10:00:00Z'
        assert 'secret-value' not in reply.text
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        assert client.get('/api/v3/diagnostics').json()['groups'][0]['count'] == 1
        assert client.delete('/api/v3/diagnostics').json() == {'state': 'cleared'}
        assert client.get('/api/v3/diagnostics').json()['groups'] == []


def test_delayed_server_result_wins_over_observation(tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from tests.support.codex import CodexService

    entered, release = threading.Event(), threading.Event()
    class Delayed(CodexService):
        async def catalog(self):
            entered.set()
            await asyncio.to_thread(release.wait, 5)
            return await super().catalog()

    with TestClient(create_app(tmp_path, token='test', api_only=True, codex_client=Delayed()), headers=HEADERS) as client:
        config = {key: {'model': 'synthetic', 'reasoning_effort': 'low', 'fast': False} for key in ('filter', 'theses')}
        with ThreadPoolExecutor() as pool:
            response = pool.submit(client.put, '/api/v3/processors/codex', json=config, headers={'X-Oko-Action': 'delayed'})
            try:
                assert entered.wait(2)
                assert client.get('/api/v3/commands/delayed').json()['outcome'] == 'pending'
                observed = client.post('/api/v3/command-observations', json={'id': 'delayed', 'context': {'operation': 'codex_save'}, 'result': 'failed'})
                assert observed.json()['outcome'] == 'pending'
            finally:
                release.set()
            assert response.result(timeout=3).status_code == 200
        assert client.get('/api/v3/commands/delayed').json()['outcome'] == 'succeeded'


def test_legacy_receipts_and_diagnostics_remain_readable(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False}, headers={'X-Oko-Action': 'legacy'}).status_code == 200
        client.post('/api/v3/diagnostics', json={'source': 'old client', 'message': 'old error'})
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        receipt = client.get('/api/v3/commands/legacy').json()
        assert receipt['outcome'] == 'succeeded'
        assert receipt['context']['operation'] == 'settings_patch'
        reply = client.get('/api/v3/diagnostics')
        assert reply.status_code == 200, reply.text
        assert reply.json()['groups'][0]['message'] == 'old error'


def test_diagnostic_persistence_failure_is_explicit(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        (tmp_path / 'debug.json').mkdir()
        reply = client.post('/api/v3/diagnostics', json={'source': 'client', 'message': 'not persisted'})
        assert reply.status_code == 200, reply.text
        assert reply.json()['state'] == 'persistence_failure'
        assert client.delete('/api/v3/diagnostics').json()['state'] == 'persistence_failure'


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
        problem = next(p for p in client.get('/api/v3/status').json()['problems']['current'] if p['command_id'] == 'bad-choice')
        assert problem['code'] == 'command_failed'
        assert problem['recovery'] == [{'kind': 'configure_processor', 'processor_id': 'codex'}]
        reply = client.post('/api/v3/problems/' + problem['id'] + '/abandon')
        assert reply.status_code == 200, reply.text
        assert client.get('/api/v3/commands/bad-choice').json()['outcome'] == 'abandoned'
        assert client.get('/api/v3/processors/codex').json()['configuration'] == config


def test_context_field_order_and_legacy_processor_are_preserved(tmp_path):
    from oko.storage.store import Store
    from oko.subscriptions.subscriptions import Overrides
    saved = Store(tmp_path, lambda _: None)
    saved.subscriptions.update_settings('oko','global',Overrides(processor='historical-choice'))
    saved.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'processor': 'historical-choice'}).status_code == 422
        reply = client.get('/api/v3/settings/oko/global')
        assert reply.status_code == 200, reply.text
        assert reply.json()['effective']['processor']['value'] == {'kind': 'legacy', 'value': 'historical-choice'}
        assert reply.json()['overrides']['processor'] == {'kind': 'legacy', 'value': 'historical-choice'}
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False}).status_code == 200
        response = client.patch('/api/v3/feeds/demo', json={'public_ai': False, 'importance': 'high'}, headers={'X-Oko-Action': 'feed-order'})
        assert response.status_code == 200, response.text
        observation = {'id': 'feed-order', 'context': {'operation': 'feed_patch', 'feed_id': 'demo', 'fields': ['public_ai', 'importance']}, 'result': 'unknown'}
        reply = client.post('/api/v3/command-observations', json=observation)
        assert reply.status_code == 200, reply.text
        assert reply.json()['outcome'] == 'succeeded'


def test_concurrent_reuse_of_correlation_cannot_change_context(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        with ThreadPoolExecutor() as pool:
            replies = list(pool.map(lambda field: client.patch('/api/v3/settings/oko/global', json={field: False}, headers={'X-Oko-Action': 'shared'}), ['theses', 'originals']))
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        rejected = next(reply for reply in replies if reply.status_code == 409)
        assert rejected.json()['code'] == 'command_context_mismatch'
        assert client.get('/api/v3/commands/shared').json()['outcome'] == 'succeeded'


def test_imported_telegram_without_access_stays_unconfigured_and_patch_is_atomic(tmp_path):
    saved_sources(tmp_path, [('https://t.me/example_channel', 'Saved', 'Без папки')])
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        imported = client.get('/api/v3/subscriptions')
        assert imported.status_code == 200, imported.text
        feed = imported.json()['feeds'][0]
        path = '/api/v3/settings/feed/' + feed['id']
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.json()['feed']['access'] == {'kind': 'unconfigured', 'source_locator': 'telegram:example_channel'}
        assert response.json()['feed']['connection'] == 'needs_connection'
        for patch in ({'account': 'another'}, {'transport': 'telegram'}, {'access': {'kind': 'web'}}):
            response = client.patch('/api/v3/feeds/' + feed['id'], json={**patch, 'importance': 'high'})
            assert response.status_code == 409, response.text
            assert response.json()['code'] == 'access_immutable'
        response = client.patch('/api/v3/feeds/' + feed['id'], json={'auto_read': True, 'importance': 'high'})
        assert response.status_code == 400
        assert client.get(path).json()['feed']['importance'] == 'normal'


def test_late_diagnostic_for_viewed_material_is_forgotten(tmp_path):
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''})
        client.post('/api/v3/collect')
        cards = wait_for(client, lambda: client.get('/api/v3/cards').json())
        identity = cards[0]['id']
        event = {'source': 'material viewer', 'message': 'private synthetic material text', 'material_ids': [identity]}
        assert client.post('/api/v3/diagnostics', json=event).json()['state'] == 'accepted'
        assert client.post('/api/v3/materials/' + identity + '/viewed').status_code == 200
        reply = client.post('/api/v3/diagnostics', json=event)
        assert reply.status_code == 200, reply.text
        assert reply.json() == {'state': 'ignored', 'reason': 'material_viewed'}
        assert 'private synthetic material text' not in client.get('/api/v3/diagnostics').text


def test_receipt_keeps_confirmed_outcome_of_retired_operation(tmp_path):
    from oko.storage.store import Store
    store=Store(tmp_path, lambda _:None)
    store.problems.action_started('old-import', dict(identity='POST:/api/import:', title='Import', objects=[], action=None), 1.0)
    store.problems.action_finished('old-import', True, 2.0)
    store.db.close()
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        reply = client.get('/api/v3/commands/old-import')
        assert reply.status_code == 200, reply.text
        assert reply.json()['outcome'] == 'succeeded'
        assert reply.json()['context'] == {'operation': 'legacy', 'name': 'import_subscriptions'}
        assert client.post('/api/v3/command-observations', json={'id': 'old-import', 'context': reply.json()['context'], 'result': 'unknown'}).status_code == 422


def test_feed_settings_preserve_configured_history_on_read_and_patch(tmp_path):
    saved_sources(tmp_path, [('https://www.youtube.com/feeds/videos.xml?channel_id=synthetic', 'History', 'Без папки')])
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        result = client.get('/api/v3/subscriptions')
        identity = result.json()['feeds'][0]['id']
        assert client.post('/api/v3/feeds/' + identity + '/history', json={'mode': 'count', 'count': '20'}).status_code == 200
        path = '/api/v3/settings/feed/' + identity
        response = client.get(path)
        assert response.status_code == 200, response.text
        history = response.json()['feed']['history']
        assert history['state'] != 'absent'
        assert history['selection'] == {'mode': 'count', 'count': '20'}
        assert 'feed_id' not in history
        response = client.patch(path, json={'theses': False})
        assert response.status_code == 200, response.text
        assert response.json()['feed']['history']['selection'] == history['selection']


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


def test_global_feed_refresh_interval_persists_and_resets(tmp_path):
    path = '/api/v3/settings/oko/global'
    read = '/api/v3/settings/feed-refresh'
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        assert client.get(read).json() == {'value': 15, 'origin': {'scope': 'default'}}
        saved = client.patch(path, json={'feed_refresh_minutes': 25})
        assert saved.status_code == 200, saved.text
        assert client.get(read).json() == {'value': 25, 'origin': {'scope': 'oko', 'owner': 'global'}}
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        assert client.get(read).json()['value'] == 25
        reset = client.patch(path, json={'feed_refresh_minutes': None})
        assert reset.status_code == 200, reset.text
        assert client.get(read).json() == {'value': 15, 'origin': {'scope': 'default'}}


def test_feed_refresh_interval_is_strict_and_global_only(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        path = '/api/v3/settings/oko/global'
        for invalid in (0, 1441, 1.5, True, '15'):
            response = client.patch(path, json={'feed_refresh_minutes': invalid})
            assert response.status_code == 422, response.text
        for valid in (1, 1440):
            response = client.patch(path, json={'feed_refresh_minutes': valid})
            assert response.status_code == 200, response.text
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        for target in ('folder/' + feed['folder_id'], 'feed/' + feed['id']):
            target_path = '/api/v3/settings/' + target
            for value in (20, None):
                response = client.patch(target_path, json={'feed_refresh_minutes': value, 'originals': False})
                assert response.status_code == 400, response.text
                assert response.json()['code'] == 'setting_scope_invalid'
            result = client.get(target_path).json()
            assert 'feed_refresh_minutes' not in result['effective']
            assert result['overrides'] == {}
        assert client.get('/api/v3/settings/feed-refresh').json()['value'] == 1440


def test_existing_closed_settings_response_stays_compatible_after_interval_save(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    pinned = json.loads((Path(__file__).parents[1] / 'fixtures/settings-v3-3.0.0.json').read_text())
    validator = Draft202012Validator(pinned['schema'])
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        path = '/api/v3/settings/oko/global'
        validator.validate(client.get(path).json())
        for patch in ({'feed_refresh_minutes': 25}, {'theses': True}, {'feed_refresh_minutes': None}):
            reply = client.patch(path, json=patch)
            assert reply.status_code == 200, reply.text
            validator.validate(reply.json())
            validator.validate(client.get(path).json())


def test_interval_command_outcomes_preserve_old_setting_keys(tmp_path):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    pinned = json.loads((Path(__file__).parents[1] / 'fixtures/settings-v3-3.0.0.json').read_text())['schema']
    context_validator = Draft202012Validator({'$ref': '#/components/schemas/CommandSettingsPatch', 'components': pinned['components']})
    with TestClient(create_app(tmp_path, token='test', api_only=True, source=DemoSource()), headers=HEADERS) as client:
        path = '/api/v3/settings/oko/global'
        assert client.patch(path, json={'feed_refresh_minutes': 25}, headers={'X-Oko-Action': 'interval-saved'}).status_code == 200
        saved = client.get('/api/v3/commands/interval-saved').json()
        assert saved['context']['keys'] == []
        context_validator.validate(saved['context'])
        context = saved['context']
        for outcome in ('failed', 'unknown'):
            response = client.post('/api/v3/command-observations', json={'id': 'interval-' + outcome, 'context': context, 'result': outcome})
            assert response.status_code == 200, response.text
            problems = client.get('/api/v3/status').json()['problems']['current']
            problem = next(problem for problem in problems if problem['command_id'] == 'interval-' + outcome)
            assert problem['affected'] == [{'kind': 'settings', 'target': {'scope': 'oko', 'owner': 'global'}}]
            assert problem['recovery'] == []  # A compatible general settings problem, without a new output enum.
        failed = client.get('/api/v3/commands/interval-failed').json()
        assert failed['outcome'] == 'failed'
        context_validator.validate(failed['context'])
        assert client.get('/api/v3/commands/interval-unknown').json()['outcome'] == 'unknown'
