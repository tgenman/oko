"""Settings reads and writes retain database transaction boundaries."""
from tests.support.cards import read_all_cards
import pytest

from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS


@pytest.fixture
def settings_database(request):
    return request.getfixturevalue('postgres_url')


def test_reading_cards_reuses_feed_processing_options_without_point_reads(tmp_path):
    from oko.collection.demo import DemoSource
    from tests.support.reader import wait_for

    app = create_app(tmp_path, token='test', api_only=True, source=DemoSource())
    with TestClient(app, headers=HEADERS) as client:
        wait_for(client, lambda: (cards if len(cards := read_all_cards(client, '/api/v3/cards')) == 3
                                 and all(client.get('/api/v3/publication/' + card['id']).json()['availability'] == 'available' for card in cards) else None))
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        queries = []
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(queries.append))
        try:
            for _ in range(3):
                response = client.get('/api/v3/cards')
                assert response.status_code == 200
                assert len(response.json()['items']) == 3
        finally:
            client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))
        point_reads = [query for query in queries
                       if 'FROM feeds f LEFT JOIN telegram_bindings' in query and 'WHERE f.id=' in query]
        assert point_reads == [], point_reads


def test_settings_edit_rollback_and_restart_keep_one_effective_value(tmp_path, settings_database):
    armed = False

    def fault(point):
        if armed and point == 'settings_after_values':
            raise RuntimeError('synthetic interrupted settings action')

    def app():
        return create_app(tmp_path, token='test', api_only=True,
                          database_url=settings_database, fault=fault)

    path = '/api/v3/settings/oko/global'
    with TestClient(app(), headers=HEADERS) as client:
        saved = client.patch(path, json={'theses': True, 'prompt_filters': 'Keep useful text'})
        assert saved.status_code == 200, saved.text
        assert saved.json()['effective']['theses']['value'] is True
        armed = True
        failed = client.patch(path, json={'theses': False, 'prompt_filters': 'Discard'})
        assert failed.status_code == 500
        current = client.get(path).json()['effective']
        assert current['theses']['value'] is True
        assert current['prompt_filters']['value'] == 'Keep useful text'
        armed = False
    with TestClient(app(), headers=HEADERS) as client:
        current = client.get(path).json()['effective']
        assert current['theses']['value'] is True
        assert current['prompt_filters']['value'] == 'Keep useful text'
        reset = client.patch(path, json={'theses': None, 'prompt_filters': None})
        assert reset.status_code == 200
        assert reset.json()['effective']['theses']['origin'] == {'scope': 'default'}
    with TestClient(app(), headers=HEADERS) as client:
        assert client.get(path).json()['effective']['theses']['origin'] == {'scope': 'default'}


def test_settings_memory_obeys_nested_rollback_and_failed_commit(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import TransactionAborted, transaction
    from oko.subscriptions.subscriptions import Overrides

    store = Store(tmp_path, lambda _: None, database_url=settings_database)
    settings = store.subscriptions
    try:
        settings.update_settings('oko', 'global', Overrides(prompt_filters='Committed'))
        with pytest.raises(TransactionAborted), transaction(store.db):
            settings.update_settings('oko', 'global', Overrides(prompt_filters='First temporary'))
            settings.update_settings('oko', 'global', Overrides(prompt_filters='Second temporary'))
            assert settings.effective('oko', 'global')['effective']['prompt_filters']['value'] == 'Second temporary'
            try:
                with store.db:
                    raise ValueError('caught nested failure')
            except ValueError:
                pass
        assert settings.effective('oko', 'global')['effective']['prompt_filters']['value'] == 'Committed'
        previous = store.db.before_commit
        def fail_commit():
            raise RuntimeError('synthetic projection failure')
        store.db.before_commit = fail_commit
        with pytest.raises(RuntimeError, match='projection failure'):
            settings.update_settings('oko', 'global', Overrides(prompt_filters='Not committed'))
        store.db.before_commit = previous
        assert settings.effective('oko', 'global')['effective']['prompt_filters']['value'] == 'Committed'
    finally:
        store.db.close()


def test_settings_source_revision_tracks_real_changes_and_rolls_back(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import Overrides
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        initial = store.subscriptions.source_revision()
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters='A'))
        first = store.subscriptions.source_revision()
        assert first > initial
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters='A'))
        assert store.subscriptions.source_revision() == first
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                store.subscriptions.update_settings('oko','global',Overrides(prompt_filters='B'))
                assert store.subscriptions.source_revision() > first
                raise ValueError('synthetic rollback')
        assert store.subscriptions.source_revision() == first
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters='B'))
        second = store.subscriptions.source_revision()
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters='A'))
        assert store.subscriptions.source_revision() > second > first
        restored = store.subscriptions.source_revision()
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=None))
        cleared = store.subscriptions.source_revision()
        assert cleared > restored
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=None))
        assert store.subscriptions.source_revision() == cleared
    finally:
        store.db.close()


def test_feed_source_revision_changes_only_for_source_configuration(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import AddFeed, FeedPatch
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        before = store.subscriptions.source_revision()
        feed = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/feed',title='First',folder='Folder',verification='synthetic'))
        created = store.subscriptions.source_revision()
        assert created > before
        store.subscriptions.update_feed(feed['id'],FeedPatch(title='Renamed'))
        assert store.subscriptions.source_revision() == created
        with transaction(store.db):
            destination = store.subscriptions.ensure_folder('Other')
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                store.subscriptions.update_feed(feed['id'],FeedPatch(folder_id=destination))
                assert store.subscriptions.source_revision() > created
                raise ValueError('synthetic rollback')
        assert store.subscriptions.source_revision() == created
        store.subscriptions.update_feed(feed['id'],FeedPatch(folder_id=destination))
        moved = store.subscriptions.source_revision()
        store.subscriptions.update_feed(feed['id'],FeedPatch(folder_id=feed['folder_id']))
        assert store.subscriptions.source_revision() > moved > created
        restored = store.subscriptions.source_revision()
        with transaction(store.db):
            store.remove_feed(feed['id'])
        assert store.subscriptions.source_revision() > restored
    finally:
        store.db.close()


def test_telegram_source_revision_tracks_binding_and_identity_changes(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        telegram = store.subscriptions.telegram
        before = store.subscriptions.source_revision()
        with transaction(store.db):
            telegram.put_binding('synthetic-feed', {'kind':'web','username':'fixture'})
        bound = store.subscriptions.source_revision()
        assert bound > before
        with transaction(store.db):
            telegram.put_binding('synthetic-feed', {'kind':'web','username':'fixture'})
        assert store.subscriptions.source_revision() == bound
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                telegram.put_binding('synthetic-feed', {'kind':'web','username':'different'})
                assert store.subscriptions.source_revision() > bound
                raise ValueError('synthetic rollback')
        assert store.subscriptions.source_revision() == bound
        with transaction(store.db):
            telegram.save_profile('personal','synthetic',123,0,1.0)
        saved = store.subscriptions.source_revision()
        assert saved > bound
        with transaction(store.db):
            assert telegram.checked('personal',1,123,2.0)
        assert store.subscriptions.source_revision() == saved
        with transaction(store.db):
            assert not telegram.checked('personal',1,456,3.0)
        assert store.subscriptions.source_revision() > saved
        with transaction(store.db):
            before_release = store.subscriptions.source_revision()
            telegram.release('synthetic-feed')
            assert store.subscriptions.source_revision() > before_release
    finally:
        store.db.close()


def test_telegram_profile_restart_marks_source_revision_once(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    with transaction(store.db):
        store.subscriptions.telegram.save_profile('personal','synthetic',123,0,1.0)
    ready = store.subscriptions.source_revision()
    store.db.close()
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        unavailable = store.subscriptions.source_revision()
        assert unavailable > ready
        assert store.subscriptions.telegram.profile('personal')['state'] == 'unavailable'
    finally:
        store.db.close()
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        assert store.subscriptions.source_revision() == unavailable
    finally:
        store.db.close()


def test_telegram_profile_message_changes_status_without_source_revision(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    with transaction(store.db):
        store.subscriptions.telegram.save_profile('personal','synthetic',123,0,1.0)
        assert not store.subscriptions.telegram.checked('personal',1,None,2.0)
    revision = store.subscriptions.source_revision()
    status = store.read_versions.version('status','runtime')['version']
    store.db.close()
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        assert store.subscriptions.source_revision() == revision
        assert store.read_versions.version('status','runtime')['version'] > status
    finally:
        store.db.close()


def test_history_source_revision_ignores_progress_but_tracks_selection(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    from oko.subscriptions.history import InitialHistory
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        before = store.subscriptions.source_revision()
        with transaction(store.db):
            store.subscriptions.history.configure('synthetic',InitialHistory(count=2))
        configured = store.subscriptions.source_revision()
        assert configured > before
        with transaction(store.db):
            store.subscriptions.history.warnings('synthetic',['Synthetic warning'])
            store.subscriptions.history.discovery_state('synthetic',{'history_requests':2})
        assert store.subscriptions.source_revision() == configured
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                store.subscriptions.history.enroll('synthetic',[],False)
                assert store.subscriptions.source_revision() > configured
                raise ValueError('synthetic rollback')
        assert store.subscriptions.source_revision() == configured
        with transaction(store.db):
            store.subscriptions.history.enroll('synthetic',[],False)
        selected = store.subscriptions.source_revision()
        with transaction(store.db):
            store.subscriptions.history.enroll('synthetic',[],False)
        assert store.subscriptions.source_revision() == selected
        with transaction(store.db):
            store.subscriptions.history.pause('synthetic',True)
        paused = store.subscriptions.source_revision()
        with transaction(store.db):
            store.subscriptions.history.pause('synthetic',False)
        assert store.subscriptions.source_revision() > paused > selected
    finally:
        store.db.close()


def test_telegram_initial_boundary_advances_source_revision_atomically(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.read_sync import ReadPlan
    from oko.storage.transaction import transaction
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO telegram_read_state(feed,since) VALUES ('synthetic',0)")
        before = store.subscriptions.source_revision()
        plan = ReadPlan(initial=5,confirmed=0,until=5,candidate=5)
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                store.read_sync.completed('synthetic',plan,5)
                assert store.subscriptions.source_revision() > before
                raise ValueError('synthetic rollback')
        assert store.subscriptions.source_revision() == before
        with transaction(store.db):
            store.read_sync.completed('synthetic',plan,5)
        changed = store.subscriptions.source_revision()
        assert changed > before
        with transaction(store.db):
            store.read_sync.completed('synthetic',plan,5)
        assert store.subscriptions.source_revision() == changed
    finally:
        store.db.close()


def test_history_member_status_signal_is_idempotent_and_rolls_back(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        with transaction(store.db):
            store.subscriptions.history.enroll('synthetic',['item'],False)
        before = store.read_versions.version('status','runtime')['version']
        with transaction(store.db):
            store.subscriptions.history.saved('item',True)
        loaded = store.read_versions.version('status','runtime')['version']
        assert loaded > before
        with transaction(store.db):
            store.subscriptions.history.saved('item',True)
        assert store.read_versions.version('status','runtime')['version'] == loaded
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                store.subscriptions.history.saved('item',False)
                assert store.read_versions.version('status','runtime')['version'] > loaded
                raise ValueError('synthetic rollback')
        assert store.read_versions.version('status','runtime')['version'] == loaded
        assert store.db.execute("SELECT outcome FROM history_members WHERE item='item'").fetchone()[0] == 'loaded'
    finally:
        store.db.close()


def test_telegram_read_status_signal_is_idempotent_and_rolls_back(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    store = Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,kind,transport,connection) VALUES ('synthetic','telegram','telegram','connected')")
        before = store.read_versions.version('status','runtime')['version']
        with transaction(store.db):
            store.read_sync.eligible('synthetic',[11],'viewed')
        viewed = store.read_versions.version('status','runtime')['version']
        assert viewed > before
        with transaction(store.db):
            store.read_sync.eligible('synthetic',[11],'viewed')
        assert store.read_versions.version('status','runtime')['version'] == viewed
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                store.read_sync.eligible('synthetic',[29],'viewed')
                assert store.read_versions.version('status','runtime')['version'] > viewed
                raise ValueError('synthetic rollback')
        assert store.read_versions.version('status','runtime')['version'] == viewed
        assert store.db.execute("SELECT 1 FROM telegram_read_members WHERE feed='synthetic' AND member=29").fetchone() is None
    finally:
        store.db.close()


def test_folder_settings_follow_rollback_and_new_folder_identity(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import Overrides

    store = Store(tmp_path, lambda _: None, database_url=settings_database)
    try:
        with store.db:
            folder = store.subscriptions.ensure_folder('Original folder')
        store.subscriptions.update_settings('folder', folder, Overrides(originals=False))
        with pytest.raises(RuntimeError, match='rollback'), transaction(store.db):
            store.subscriptions.remove_folder(folder)
            raise RuntimeError('rollback folder deletion')
        assert store.subscriptions.effective('folder', folder)['effective']['originals']['value'] is False
        store.subscriptions.remove_folder(folder)
        with store.db:
            replacement = store.subscriptions.ensure_folder('Original folder')
        assert replacement != folder
        assert store.subscriptions.effective('folder', replacement)['effective']['originals']['scope'] == 'default'
    finally:
        store.db.close()


def test_connection_close_runs_rollback_callbacks_once(settings_database):
    from oko.storage.postgres_connection import PostgresConnection

    db = PostgresConnection(settings_database)
    called = []
    db.execute("INSERT INTO state VALUES ('memory-close-probe','uncommitted')")
    db.after_rollback(lambda: called.append('first'))
    db.after_rollback(lambda: called.append('second'))
    db.close()
    assert called == ['second', 'first']
    db.close()
    assert called == ['second', 'first']


def test_processor_configuration_writes_are_visible_and_survive_restart(tmp_path, settings_database):
    from tests.reader.test_codex_settings import Models, configuration

    codex = configuration()
    local = {'endpoint': 'http://localhost:9931', 'model': 'local:synthetic', 'local_execution': True}
    def app():
        return create_app(tmp_path, token='test', api_only=True, codex_client=Models(), database_url=settings_database)
    with TestClient(app(), headers=HEADERS) as client:
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        assert client.put('/api/v3/processors/codex', json=codex).status_code == 200
        assert client.put('/api/v3/processors/llamacpp', json=local).status_code == 200
        assert client.get('/api/v3/processors/codex').json()['configuration'] == codex
        assert client.get('/api/v3/processors/llamacpp').json()['configuration'] == local
    with TestClient(app(), headers=HEADERS) as client:
        assert client.get('/api/v3/processors/codex').json()['configuration'] == codex
        assert client.get('/api/v3/processors/llamacpp').json()['configuration'] == local


def test_processor_configuration_memory_follows_transaction_rollback(tmp_path, settings_database):
    from oko.processing.execution import CodexSettings
    from oko.processing.llamacpp import ProcessorConfig
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    from tests.reader.test_codex_settings import configuration

    store = Store(tmp_path, lambda _: None, database_url=settings_database)
    old = CodexSettings.model_validate(configuration())
    new = CodexSettings.model_validate(configuration('test-large', 'high'))
    local = ProcessorConfig(endpoint='http://localhost:9931', model='local:synthetic', local_execution=True)
    try:
        with pytest.raises(RuntimeError, match='rollback'), transaction(store.db):
            store.configure_codex(old)
            store.configure_processor(local)
            assert store.codex_config() == old and store.processor_config() == local
            raise RuntimeError('rollback first configuration')
        assert store.codex_config() is None and store.processor_config() is None
        store.configure_codex(old)
        store.configure_processor(local)
        with pytest.raises(RuntimeError, match='rollback'), transaction(store.db):
            store.configure_codex(new)
            store.configure_codex(old)
            store.configure_codex(new)
            store.configure_processor(local.model_copy(update={'model': 'local:other'}))
            raise RuntimeError('rollback changed configuration')
        assert store.codex_config() == old and store.processor_config() == local
    finally:
        store.db.close()


def test_processor_configuration_rolls_back_when_processing_reconciliation_fails(tmp_path, settings_database, monkeypatch):
    from oko.processing.llamacpp import ProcessorConfig
    from tests.support.storage import Store

    store = Store(tmp_path, lambda _: None, database_url=settings_database)
    initial = ProcessorConfig(endpoint='http://localhost:9931', model='local:one', local_execution=True)
    changed = initial.model_copy(update={'endpoint': 'http://localhost:9932'})
    try:
        store.configure_processor(initial)
        version = store.settings_version()
        def fail():
            raise RuntimeError('reconciliation failed')
        monkeypatch.setattr(store.processing, 'sync', fail)
        with pytest.raises(RuntimeError, match='reconciliation failed'):
            store.configure_processor(changed)
        assert store.processor_config() == initial
        assert store.settings_version() == version
    finally:
        store.db.close()


def test_feed_configuration_memory_follows_edits_and_rollback(tmp_path, settings_database):
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import AddFeed, FeedPatch

    store = Store(tmp_path, lambda _: None, database_url=settings_database)
    try:
        feed = store.subscriptions.add_feed(AddFeed(url='https://source.test/feed', title='Source', folder='Original', verification='synthetic'))
        original = store.subscriptions.effective('feed', feed['id'], details=False)['feed']
        assert original == {'id': feed['id'], 'kind': 'rss', 'folder_id': feed['folder_id'],
                            'importance': 'normal', 'public_ai': False, 'transport': 'rss'}
        with pytest.raises(RuntimeError, match='rollback'), transaction(store.db):
            store.subscriptions.update_feed(feed['id'], FeedPatch(public_ai=True, importance='high', new_folder='Temporary'))
            changed = store.subscriptions.effective('feed', feed['id'], details=False)['feed']
            assert changed['public_ai'] is True and changed['importance'] == 'high'
            assert changed['folder_id'] != original['folder_id']
            raise RuntimeError('rollback feed settings')
        assert store.subscriptions.effective('feed', feed['id'], details=False)['feed'] == original
        store.subscriptions.update_feed(feed['id'], FeedPatch(public_ai=True, importance='high'))
    finally:
        store.db.close()
    store = Store(tmp_path, lambda _: None, database_url=settings_database)
    try:
        restored = store.subscriptions.effective('feed', feed['id'], details=False)['feed']
        assert restored['public_ai'] is True and restored['importance'] == 'high'
        assert restored['folder_id'] == original['folder_id']
    finally:
        store.db.close()


def test_material_immediately_uses_moved_feed_settings_and_delete_removes_cache(tmp_path, settings_database):
    from oko.collection.demo import DemoSource
    from tests.support.reader import wait_for

    app = create_app(tmp_path, token='test', api_only=True, source=DemoSource(), database_url=settings_database)
    with TestClient(app, headers=HEADERS) as client:
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        item = wait_for(client, lambda: (cards[0]['id'] if len(cards := read_all_cards(client, '/api/v3/cards')) == 3
                                        and all(client.get('/api/v3/publication/' + card['id']).json()['availability'] == 'available' for card in cards) else None))
        moved = client.patch('/api/v3/feeds/demo', json={'new_folder': 'Moved', 'importance': 'high', 'public_ai': True})
        assert moved.status_code == 200, moved.text
        folder = moved.json()['folder_id']
        assert client.patch('/api/v3/settings/folder/'+folder, json={'originals': False}).status_code == 200
        material = client.get('/api/v3/publication/'+item)
        assert material.status_code == 200
        assert material.json()['originals'] is False
        assert client.patch('/api/v3/settings/folder/'+folder, json={'originals': True}).status_code == 200
        assert client.get('/api/v3/publication/'+item).json()['originals'] is True
        assert client.delete('/api/v3/feeds/demo').status_code == 200
        wait_for(client, lambda: not client.get('/api/v3/subscriptions').json()['feeds'])
        with pytest.raises(ValueError, match='Feed not found'):
            client.portal.call(lambda: app.state.host.require_store().subscriptions.effective('feed', 'demo', details=False))


def test_postgres_failed_rollback_discards_pending_memory_and_connection(tmp_path, postgres_url):
    import psycopg
    from tests.support.storage import Store
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import Overrides

    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        store.subscriptions.update_settings('oko', 'global', Overrides(prompt_filters='Committed'))
        with pytest.raises(psycopg.Error), transaction(store.db):
            store.subscriptions.update_settings('oko', 'global', Overrides(prompt_filters='Pending'))
            assert store.subscriptions.effective('oko', 'global')['effective']['prompt_filters']['value'] == 'Pending'
            identity = store.db.execute('SELECT pg_backend_pid()').fetchone()[0]
            with psycopg.connect(postgres_url, autocommit=True) as administrator:
                assert administrator.execute('SELECT pg_terminate_backend(%s)', (identity,)).fetchone()[0]
            raise RuntimeError('abort owned synthetic transaction')
        with pytest.raises(psycopg.Error):
            store.subscriptions.effective('oko', 'global')
        with pytest.raises(psycopg.Error):
            store.db.execute('SELECT 1')
    finally:
        store.db.close()
    reopened = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        assert reopened.subscriptions.effective('oko', 'global')['effective']['prompt_filters']['value'] == 'Committed'
    finally:
        reopened.db.close()


def test_http_settings_reads_committed_changes_from_another_connection(tmp_path, settings_database):
    import psycopg
    from oko.collection.demo import DemoSource

    app = create_app(tmp_path, token='test', api_only=True, source=DemoSource(), database_url=settings_database)
    path = '/api/v3/settings/oko/global'
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(settings_database) as writer:
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        assert client.get(path).json()['effective']['theses']['value'] is False
        writer.execute("INSERT INTO settings(field,value) VALUES ('theses','true')")
        assert client.get(path).json()['effective']['theses']['value'] is False
        writer.commit()
        assert client.get(path).json()['effective']['theses']['value'] is True
        writer.execute("UPDATE settings SET value='false' WHERE folder_id IS NULL AND feed_id IS NULL AND field='theses'")
        writer.rollback()
        assert client.get(path).json()['effective']['theses']['value'] is True


def test_http_inherited_settings_use_one_consistent_database_snapshot(tmp_path, settings_database):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from oko.collection.demo import DemoSource

    app = create_app(tmp_path, token='test', api_only=True, source=DemoSource(), database_url=settings_database)
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(settings_database) as writer:
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        folder = client.get('/api/v3/subscriptions').json()['feeds'][0]['folder_id']
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False}).status_code == 200
        path = '/api/v3/settings/folder/' + folder
        assert client.patch(path, json={'prompt_filters': 'before'}).status_code == 200
        entered, release = threading.Event(), threading.Event()
        def trace(query):
            if 'SELECT field, value FROM settings' in query and not entered.is_set():
                entered.set()
                assert release.wait(5)
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(trace))
        try:
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(client.get, path)
                assert entered.wait(5)
                writer.execute("UPDATE settings SET value='true' WHERE folder_id IS NULL AND feed_id IS NULL AND field='theses'")
                writer.execute("UPDATE settings SET value=%s WHERE folder_id=%s AND feed_id IS NULL AND field='prompt_filters'", ('\"after\"', folder))
                writer.commit()
                release.set()
                result = pending.result(5)
            assert result.status_code == 200, result.text
            assert result.json()['effective']['theses']['value'] is False
            assert result.json()['effective']['prompt_filters']['value'] == 'before'
        finally:
            release.set()
            client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))


def test_http_processor_settings_follow_committed_storage(tmp_path, settings_database):
    import json
    import psycopg
    from tests.reader.test_codex_settings import Models, configuration

    app = create_app(tmp_path, token='test', api_only=True, codex_client=Models(), database_url=settings_database)
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(settings_database) as writer:
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        assert client.get('/api/v3/processors/codex').json()['configuration'] is None
        config = configuration()
        writer.execute('INSERT INTO state(key,value) VALUES (%s,%s)', ('processor:codex', json.dumps(config)))
        assert client.get('/api/v3/processors/codex').json()['configuration'] is None
        writer.commit()
        assert client.get('/api/v3/processors/codex').json()['configuration'] == config


def test_feed_processing_settings_follow_committed_storage(tmp_path, settings_database):
    import psycopg
    from tests.support.storage import Store
    from oko.subscriptions.subscriptions import AddFeed

    store = Store(tmp_path, lambda _: None, database_url=settings_database)
    try:
        feed = store.subscriptions.add_feed(AddFeed(url='https://source.test/feed', title='Source', folder='Folder', verification='synthetic'))
        identity = feed['id']
        with psycopg.connect(settings_database) as writer:
            writer.execute('UPDATE feeds SET public_ai=1 WHERE id=%s', (identity,))
            assert store.subscriptions.effective('feed', identity, details=False)['feed']['public_ai'] is False
            writer.commit()
            assert store.subscriptions.effective('feed', identity, details=False)['feed']['public_ai'] is True
    finally:
        store.db.close()


def test_codex_configuration_problem_rolls_back_with_settings(tmp_path, settings_database):
    from oko.storage.transaction import transaction
    from oko.subscriptions.subscriptions import Overrides
    from oko.services.subscriptions import SubscriptionService
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        service=SubscriptionService(lambda:store)
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                service.update_settings('oko','global',Overrides(processor='codex',theses=True))
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:codex-choice' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:codex-choice'").fetchone() is None
        with transaction(store.db):
            service.update_settings('oko','global',Overrides(processor='codex',theses=True))
        identity=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:codex-choice' AND finished IS NULL").fetchone()[0]
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=settings_database)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:codex-choice' AND finished IS NULL").fetchone()[0]==identity
        with transaction(store.db):
            service=SubscriptionService(lambda:store)
            service.update_settings('oko','global',Overrides(theses=False))
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id=%s",(identity,)).fetchone()[0]=='irrelevant'
    finally:
        store.db.close()


def test_telegram_profile_problem_rolls_back_with_identity_check(tmp_path, settings_database, monkeypatch):
    import json
    from oko.services.connections import ConnectionService
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('telegram','telegram','Telegram','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('telegram',%s)", (json.dumps(dict(kind='client',role='personal',account_user_id='101',peer_id='123')),))
        service=ConnectionService(lambda:store)
        with monkeypatch.context() as patch:
            def fail(*args,**kwargs):
                raise RuntimeError('problem persistence failed')
            patch.setattr(store.problems,'sync_profiles',fail)
            with pytest.raises(RuntimeError,match='problem persistence failed'):
                service.checked('personal',1,202,2)
        assert store.subscriptions.telegram.profile('personal')['state']=='ready'
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                assert not service.checked('personal',1,202,2)
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        assert store.subscriptions.telegram.profile('personal')['state']=='ready'
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='profile:personal'").fetchone() is None
        with transaction(store.db):
            assert not service.checked('personal',1,202,2)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()
    finally:
        store.db.close()


def test_shared_telegram_profile_problem_keeps_remaining_feed(tmp_path, settings_database):
    import json
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        with store.db:
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'unavailable',1)")
            for feed in ('first','second'):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,%s,'default','telegram','telegram','connected','[]')", (feed,feed,feed))
                store.db.execute('INSERT INTO telegram_bindings(feed,binding) VALUES (%s,%s)',(feed,json.dumps(dict(kind='client',role='personal',account_user_id='101',peer_id=feed))))
            store.problems.sync_profiles(store.wall_clock(),{'personal'})
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()
        assert {v['id'] for v in json.loads(before['body'])['objects']}=={'first','second'}
        store.remove_feed('first')
        after=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()
        assert after['id']==before['id']
        assert [v['id'] for v in json.loads(after['body'])['objects']]==['second']
        store.remove_feed('second')
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()[0]=='irrelevant'
    finally:
        store.db.close()


def test_profile_check_does_not_load_unrelated_feed_details(tmp_path, settings_database):
    import json
    from oko.services.connections import ConnectionService
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        with store.db:
            store.db.executemany("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,%s,'default','rss','rss','connected','[]')", [(f'other-{i}',f'other-{i}',f'Other {i}') for i in range(150)])
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('telegram','telegram','Telegram','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('telegram',%s)", (json.dumps(dict(kind='client',role='personal',account_user_id='101',peer_id='123')),))
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:
            assert not ConnectionService(lambda:store).checked('personal',1,202,2)
        finally:
            store.db.set_trace_callback(None)
        assert not any('FROM feeds ORDER BY rowid' in query for query in statements), statements
    finally:
        store.db.close()


def test_profile_problem_order_is_stable_across_feed_batches(tmp_path, settings_database):
    import json
    from oko.services.connections import ConnectionService
    from oko.storage.reading import reading_snapshot
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=settings_database)
    try:
        with store.db:
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            for i in range(405):
                feed=f'feed-{404-i:04}'
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,%s,'default','telegram','telegram','connected','[]')",(feed,feed,feed))
                store.db.execute('INSERT INTO telegram_bindings(feed,binding) VALUES (%s,%s)',(feed,json.dumps(dict(kind='client',role='personal',account_user_id='101',peer_id=feed))))
        assert not ConnectionService(lambda:store).checked('personal',1,202,2)
        saved=json.loads(store.db.execute("SELECT body FROM reader_problems WHERE identity='profile:personal' AND finished IS NULL").fetchone()[0])
        with reading_snapshot():
            candidate=next(c for c in store.problems.candidates(2) if c['id']=='profile:personal')
        assert saved['objects']==candidate['objects']
        assert len(saved['objects'])==405
    finally:
        store.db.close()
