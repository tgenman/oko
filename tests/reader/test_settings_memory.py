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
        writer.execute("INSERT INTO settings(scope,owner,field,value) VALUES ('oko','global','theses','true')")
        assert client.get(path).json()['effective']['theses']['value'] is False
        writer.commit()
        assert client.get(path).json()['effective']['theses']['value'] is True
        writer.execute("UPDATE settings SET value='false' WHERE scope='oko' AND owner='global' AND field='theses'")
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
                writer.execute("UPDATE settings SET value='true' WHERE scope='oko' AND owner='global' AND field='theses'")
                writer.execute("UPDATE settings SET value=%s WHERE scope='folder' AND owner=%s AND field='prompt_filters'", ('\"after\"', folder))
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
