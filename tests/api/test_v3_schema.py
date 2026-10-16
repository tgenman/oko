"""Public JSON Schema examples for the independent API candidate."""
import os
from functools import lru_cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def contract():
    return yaml.safe_load(Path(os.environ.get('OKO_V3_SCHEMA', ROOT / 'api/openapi.yaml')).read_text())


def accepts(name, value):
    spec = contract()
    validator = Draft202012Validator({'$ref': f'#/components/schemas/{name}', 'components': spec['components']}, format_checker=FormatChecker())
    return validator.is_valid(value)


def test_v3_exact_history_and_absence_are_distinct():
    assert accepts('HistorySelection', {'mode': 'count', 'count': '9007199254740993'})
    assert accepts('HistorySelection', {'mode': 'date', 'date': '2026-10-14', 'timezone': 'Europe/Moscow'})
    for invalid in [{}, None, {'mode': 'count'}, {'mode': 'count', 'count': 20}, {'mode': 'count', 'count': '020'}, {'mode': 'count', 'count': '20', 'date': None}]:
        assert not accepts('HistorySelection', invalid)
    assert accepts('HistoryState', {'state': 'absent', 'legacy_since': None})
    assert not accepts('HistoryState', {'state': 'absent', 'legacy_since': None, 'selection': {'mode': 'count', 'count': '20'}})


def test_settings_reset_is_not_feed_patch_null():
    for payload in [{}, {'theses': False}, {'theses': None}]:
        assert accepts('SettingsPatch', payload)
    for key in ['folder_id', 'importance', 'initial_days', 'public_ai', 'read_history']:
        assert not accepts('FeedPatchRequest', {key: None})
    assert not accepts('SettingsPatch', {'theses': 'false'})
    assert not accepts('SettingsPatch', {'events': True})


def test_telegram_legacy_is_response_only_and_web_does_not_accept_auto_read():
    legacy = {'kind': 'legacy_client', 'alias': 'old', 'source_locator': '@example', 'peer_id': None}
    assert accepts('TelegramAccess', legacy)
    assert not accepts('TelegramAccess', dict(legacy, role='personal'))
    assert not accepts('TelegramConnectRequest', {'source_locator': '@example', 'folder': 'News', 'access': legacy})
    assert accepts('TelegramConnectRequest', {'source_locator': '@example', 'folder': 'News'})
    assert not accepts('TelegramConnectRequest', {'source_locator': '@example', 'folder': 'News', 'auto_read': False})
    assert accepts('TelegramConnectRequest', {'source_locator': '-100123', 'folder': 'News', 'access': {'kind': 'client', 'profile': 'personal'}, 'auto_read': False})


def test_explicit_lifetime_cannot_contain_client_presence_timers():
    assert accepts('LifetimePolicy', {'kind': 'explicit'})
    assert not accepts('LifetimePolicy', {'kind': 'explicit', 'lease_seconds': 6})
    assert accepts('LifetimePolicy', {'kind': 'client_presence', 'lease_seconds': 6, 'grace_seconds': 15, 'renew_after_seconds': 2})
    assert not accepts('Timestamp', '2026-10-15T10:00:00')


def test_target_registry_and_examples_are_complete():
    spec = contract()
    operations = [(path, method, op) for path, methods in spec['paths'].items() for method, op in methods.items() if method in {'get', 'post', 'put', 'patch', 'delete'}]
    assert spec['info']['version'] == '3.0.0'
    assert len(operations) == 50
    assert all(path.startswith('/api/v3/') for path, _, _ in operations)
    assert len({op['operationId'] for _, _, op in operations}) == 50
    for path, method, operation in operations:
        assert operation['description'], (path, method)
        assert '422' in operation['responses'], (path, method)
        for response in operation['responses'].values():
            for content in response.get('content', {}).values():
                if content.get('schema', {}).get('format') == 'binary':
                    continue
                examples = [content['example']] if 'example' in content else [entry['value'] for entry in content['examples'].values()]
                assert examples, (path, method)
                for value in examples:
                    Draft202012Validator(dict(content['schema'], components=spec['components']), format_checker=FormatChecker()).validate(value)
    assert not any('reconnect_feed' in str(schema) for schema in spec['components']['schemas'].values())


def test_ready_processing_requires_a_result_and_legacy_problems_keep_raw_code():
    assert not accepts('FilterState', {'attempt': {'state': 'ready'}, 'result': None})
    assert accepts('FilterState', {'attempt': {'state': 'running'}, 'result': None, 'diagnostic_message': None})
    assert not accepts('ThesesState', {'attempt': {'state': 'ready'}, 'result': None})
    assert not accepts('CodexChoice', {'model': 'example', 'reasoning_effort': 'high'})
    assert accepts('CodexChoiceRequest', {'model': 'example', 'reasoning_effort': 'high'})


def test_every_named_variant_has_a_valid_literal_example():
    spec = contract()
    for name, schema in spec['components']['schemas'].items():
        assert schema.get('examples'), name
        for value in schema['examples']:
            Draft202012Validator({'$ref': f'#/components/schemas/{name}', 'components': spec['components']}, format_checker=FormatChecker()).validate(value)


def test_persisted_not_started_history_has_explicit_progress():
    selection = {'mode': 'count', 'count': '20'}
    assert not accepts('HistoryState', {'state': 'not_started', 'selection': selection})
    progress = dict(loaded=0, viewed=0, incomplete=0, available=0, retrying=0, unavailable=0, requests=0, errors=[], scan_complete=False, warnings=[], manifest_complete=False, discovery_problem=None)
    assert accepts('HistoryState', {'state': 'not_started', 'selection': selection, 'progress': progress})


def test_problem_keeps_title_and_consequence_separate_from_diagnostic_message():
    spec = contract()
    for name in ['CurrentProblem', 'RetryingProblem', 'FinishedProblem', 'LegacyCurrentProblem', 'LegacyRetryingProblem', 'LegacyFinishedProblem']:
        example = spec['components']['schemas'][name]['examples'][0]
        assert accepts(name, example)
        for field in ['title', 'consequence', 'message']:
            assert field in example
            assert not accepts(name, {key: value for key, value in example.items() if key != field})


def test_history_reports_manifest_pause_and_terminal_facts():
    spec = contract()
    paused = spec['components']['schemas']['HistoryPaused']['examples'][0]
    assert 'paused_phase' in paused
    assert 'manifest_complete' in paused['progress']
    assert not accepts('HistoryPaused', {k: v for k, v in paused.items() if k != 'paused_phase'})
    cancelled = spec['components']['schemas']['HistoryCancelled']['examples'][0]
    assert cancelled['outcome'] == 'cancelled'
    assert 'retry_at' not in cancelled
    assert 'reason' not in cancelled
    assert accepts('HistoryCancelled', dict(cancelled, progress=dict(cancelled['progress'], retrying=1)))


def test_existing_reading_fields_and_demo_position_are_preserved():
    assert accepts('SubscriptionFolder', {'id': 'demo', 'title': 'Тестовый источник', 'position': -1})
    assert accepts('ReadingComposition', {'folders': [], 'feeds': [], 'count': 0})
    assert not accepts('ReadingComposition', {'folders': [], 'feeds': []})
    content = contract()['components']['schemas']['AvailablePublicationContent']['examples'][0]
    literal = dict(content, description='Описание видео', display_text='Текст без скрытого фрагмента', text='Текст со скрытым фрагментом')
    assert accepts('AvailablePublicationContent', literal)
    for field in ['description', 'display_text']:
        assert not accepts('AvailablePublicationContent', {key: value for key, value in literal.items() if key != field})
    card = contract()['components']['schemas']['AvailableReadingCard']['examples'][0]
    assert accepts('ReadingCard', dict(card, images=['/api/v3/materials/publication/images/image-1.png'], attachments=[], loading=True, changed=True))


def test_saved_subscription_transport_and_unresolved_connection_are_preserved():
    feed = contract()['components']['schemas']['UnresolvedSubscriptionFeed']['examples'][0]
    assert accepts('SubscriptionFeed', dict(feed, transport=None, connection='ambiguous', original_urls=['@saved_name', '-100123']))
    assert accepts('SubscriptionFeed', dict(feed, transport='rss', connection='needs_connection'))
    legacy = contract()['components']['schemas']['TelegramLegacyClientSubscriptionFeed']['examples'][0]
    assert accepts('SubscriptionFeed', dict(legacy, connection='deleting'))


def test_command_context_covers_checks_and_empty_patches():
    assert accepts('CommandContext', {'operation': 'codex_check'})
    assert accepts('CommandContext', {'operation': 'processor_check'})
    assert accepts('CommandContext', {'operation': 'verify_feed'})
    assert accepts('CommandContext', {'operation': 'preview_completion', 'folder_id': 'demo'})
    assert accepts('CommandContext', {'operation': 'settings_patch', 'target': {'scope': 'oko', 'owner': 'global'}, 'keys': []})
    assert accepts('CommandContext', {'operation': 'feed_patch', 'feed_id': 'demo', 'fields': []})


def test_current_problem_snapshot_does_not_accept_finished_problems():
    spec = contract()
    snapshot = {'current': [spec['components']['schemas']['FinishedProblem']['examples'][0]], 'recent': [], 'count': 1, 'updated_at': '2026-10-15T10:00:00Z'}
    assert not accepts('ProblemSnapshot', snapshot)


def test_profile_roles_and_command_problem_correlation_are_explicit():
    assert accepts('TelegramProfiles', {'personal': {'state': 'unconfigured', 'role': 'personal'}, 'collection': {'state': 'unconfigured', 'role': 'collection'}, 'configurations': []})
    assert not accepts('TelegramProfiles', {'personal': {'state': 'unconfigured', 'role': 'collection'}, 'collection': {'state': 'unconfigured', 'role': 'collection'}, 'configurations': []})
    unavailable = {'state': 'unavailable', 'local': None, 'auth': 'unknown', 'version': None, 'limits_checked': False, 'quota_windows': [], 'reason': 'unavailable', 'message': 'Availability has not been checked'}
    assert accepts('CodexAvailability', unavailable)
    problem = dict(contract()['components']['schemas']['CurrentProblem']['examples'][0], command_id='lost-request-1')
    assert accepts('CurrentProblem', problem)
    assert 'updated_at' not in problem
    assert 'count' not in problem


def test_saved_model_bound_processor_and_unknown_historical_choice_are_explicit():
    assert accepts('SettingsPatch', {'processor': 'llamacpp:local/model-v2'})
    assert not accepts('SettingsPatch', {'processor': 'unknown-processor'})
    assert accepts('StoredOverrides', {'processor': {'kind': 'legacy', 'value': 'unknown-processor'}})
    assert not accepts('SettingsPatch', {'processor': {'kind': 'legacy', 'value': 'unknown-processor'}})
    assert accepts('CodexAvailability', {'state': 'unavailable', 'local': None, 'auth': 'unchecked', 'version': None, 'limits_checked': False, 'quota_windows': [], 'reason': 'unavailable', 'message': 'Not checked'})


def test_discriminator_mappings_use_wire_literals_for_every_variant():
    schemas = contract()['components']['schemas']
    for name, schema in schemas.items():
        discriminator = schema.get('discriminator')
        if not discriminator:
            continue
        expected = {}
        for variant in schema['oneOf']:
            ref = variant['$ref']
            field = schemas[ref.split('/')[-1]]['properties'][discriminator['propertyName']]
            values = [field['const']] if 'const' in field else field['enum']
            for value in values:
                assert value not in expected, (name, value)
                expected[value] = ref
        assert discriminator.get('mapping') == expected, name


def test_recovery_variants_require_their_domain_targets_and_reject_reconnect():
    for invalid in [
        {'kind':'reconnect_feed','feed_id':'old'},
        {'kind':'configure_telegram_profile'},
        {'kind':'retry_material'},
        {'kind':'retry_material','material_ids':[]},
        {'kind':'change_setting','target':{'scope':'oko','owner':'global'}},
        {'kind':'retry_collection','target':{'scope':'feed'}},
    ]:
        assert not accepts('RecoveryIntent',invalid)
    assert accepts('RecoveryIntent',{'kind':'configure_telegram_profile','role':'personal'})
    assert accepts('RecoveryIntent',{'kind':'retry_collection','target':{'scope':'feed','feed_id':'saved'}})
