"""Repository transaction ownership against real PostgreSQL and independent readers."""
from tests.support.storage_action import storage_action
from tests.support.publications import body_text
from tests.support.cards import read_all_cards
from tests.support.storage import ensure_fixture
from contextlib import contextmanager

import pytest

from oko.storage.postgres_connection import PostgresConnection
from oko.storage.transaction import TransactionAborted, transaction


@pytest.fixture
def database(postgres_url):
    @contextmanager
    def connect():
        db = PostgresConnection(postgres_url)
        try:
            yield db
        finally:
            db.close()
    yield connect


def test_one_commit_publishes_data_and_callbacks_to_an_independent_reader(database):
    with database() as writer, database() as reader:
        callbacks = []
        assert reader.execute('SELECT 1').fetchone()[0] == 1
        assert not reader.in_transaction
        with transaction(writer):
            writer.execute("INSERT INTO state VALUES ('test','saved')")
            with writer:
                writer.execute("INSERT INTO jobs(kind,item) VALUES ('fetch','article')")
            writer.after_commit(lambda: callbacks.append(reader.execute("SELECT value FROM state WHERE key='test'").fetchone()[0]))
            assert reader.execute("SELECT 1 FROM state WHERE key='test'").fetchone() is None
            assert callbacks == []
        assert callbacks == ['saved']
        assert dict(reader.execute("SELECT kind,item FROM jobs").fetchone()) == {'kind': 'fetch', 'item': 'article'}
        assert not reader.in_transaction


def test_caught_inner_failure_aborts_every_write_and_drops_callbacks(database):
    with database() as db:
        callbacks = []
        with pytest.raises(TransactionAborted), transaction(db):
            db.execute("INSERT INTO state VALUES ('test','uncommitted')")
            db.after_commit(lambda: callbacks.append('incorrect'))
            try:
                with db:
                    raise ValueError('caught repository failure')
            except ValueError:
                pass
        assert db.execute("SELECT 1 FROM state WHERE key='test'").fetchone() is None
        assert callbacks == []
        assert not db.in_transaction
        with transaction(db):
            db.execute("INSERT INTO state VALUES ('test','retry')")
        assert db.execute("SELECT value FROM state WHERE key='test'").fetchone()[0] == 'retry'


def test_projection_failure_rolls_back_data_and_jobs(database):
    with database() as db:
        def fail():
            raise RuntimeError('projection failure')
        db.before_commit = fail
        with pytest.raises(RuntimeError, match='projection failure'), transaction(db):
            db.execute("INSERT INTO state VALUES ('test','uncommitted')")
            db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch','article')")
        assert not db.in_transaction
        assert db.execute("SELECT 1 FROM state WHERE key='test'").fetchone() is None
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        db.before_commit = None


@pytest.mark.parametrize('delete_fault', [False, True])
def test_reader_http_collection_media_settings_viewed_and_restart(postgres_url, tmp_path, delete_fault):
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS, wait_for
    from tests.support.rss import rss, wait_cards
    from tests.support.subscriptions import add_source
    from tests.support.youtube import PNG

    fail_delete = [False]
    failed_deletions = []
    requests = []
    def fault(point):
        if point == 'before_delete' and fail_delete[0]:
            failed_deletions.append(point)
            raise OSError('synthetic interrupted file cleanup')
    def response(request):
        requests.append(request.url.path)
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/image.png':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        return httpx.Response(200, text='<html><title>Collected title</title><article><p>Collected text survives restart.</p><img src="https://source.test/image.png"></article></html>')

    def app():
        return create_app(tmp_path, token='test', api_only=True, database_url=postgres_url,
                          rss_enabled=True, rss_transport=httpx.MockTransport(response), fault=fault)

    with TestClient(app(), headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        settings = client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''})
        assert settings.status_code == 200, settings.text
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        feed = add_source(client, 'https://source.test/feed', 'PostgreSQL source', 'PostgreSQL library')
        renamed = client.patch('/api/v3/folders/' + feed['folder_id'], json={'title': 'Renamed library'})
        assert renamed.status_code == 200, renamed.text
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        card = wait_cards(client)[0]
        item = card['id']
        material = wait_for(client, lambda: (value if (value := client.get('/api/v3/publication/' + item).json()) and any(media['url'] is not None for media in value['media']) else None))
        assert 'Collected text' in body_text(material)
        frame = client.get('/api/v3/navigation')
        assert frame.status_code == 200, frame.text
        storage_id = frame.headers['X-Oko-Storage-Id']
        assert frame.json()['total'] == 1
        assert client.get('/api/v3/cards').json()['items'][0]['id'] == item
        media = [media for media in material['media'] if media['url'] is not None][0]['url']
        assert client.get(media).content == PNG
        previous = requests.count('/feed')
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: requests.count('/feed') > previous)
        assert len(read_all_cards(client, '/api/v3/cards')) == 1
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        assert not (tmp_path / 'state.sqlite').exists()
    with TestClient(app(), headers=HEADERS) as restarted:
        assert [value['id'] for value in read_all_cards(restarted, '/api/v3/cards')] == [item]
        assert restarted.get(media).content == PNG
        restored = restarted.get('/api/v3/publication/' + item)
        assert restored.status_code == 200, restored.text
        assert restarted.get('/api/v3/status/details').headers['X-Oko-Storage-Id'] == storage_id
        assert restarted.get('/api/v3/subscriptions').json()['folders'][0]['title'] == 'Renamed library'
        fail_delete[0] = delete_fault
        viewed = restarted.post('/api/v3/materials/' + item + '/viewed')
        assert viewed.status_code == 200, viewed.text
        if delete_fault:
            wait_for(restarted, lambda: bool(failed_deletions))
        assert read_all_cards(restarted, '/api/v3/cards') == []
        assert restarted.get('/api/v3/publication/' + item).status_code == 404
    if delete_fault:
        fail_delete[0] = False
        with TestClient(app(), headers=HEADERS) as recovered:
            # Oko remains powered off; an explicit replay resumes its retained cleanup.
            assert recovered.post('/api/v3/materials/' + item + '/viewed').status_code == 200
            wait_for(recovered, lambda: not any(path.is_file() for path in (tmp_path / 'media').rglob('*')))
            assert recovered.get(media).status_code == 404
            assert read_all_cards(recovered, '/api/v3/cards') == []


def test_reader_role_needs_no_schema_ownership_or_migration_credentials(postgres_url, tmp_path):
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from oko.storage.postgres_migrations import migrate, Migration
    from oko.storage.postgres_schema import MIGRATIONS
    from tests.support.storage import Store

    role = 'reader_' + uuid4().hex
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        schema = admin.execute('SELECT current_schema()').fetchone()[0]
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal('synthetic-reader-only')))
        try:
            admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
            admin.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
            admin.execute(sql.SQL('REVOKE INSERT,UPDATE,DELETE ON oko_schema_migrations FROM {}').format(sql.Identifier(role)))
            admin.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}').format(sql.Identifier(schema), sql.Identifier(role)))
            reader_dsn = make_conninfo(postgres_url, user=role, password='synthetic-reader-only')
            store = Store(tmp_path, lambda _: None, database_url=reader_dsn)
            try:
                ensure_fixture(store.db)
                assert store.subscriptions.feeds()[0]['id'] == 'fixture'
                storage_action(store, 'set_work_enabled', True)
                assert store.work_enabled() is True
            finally:
                store.db.close()
            with psycopg.connect(reader_dsn, autocommit=True) as reader:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    reader.execute('CREATE TABLE reader_cannot_install_schema(id integer)')
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    migrate(reader, [*MIGRATIONS, Migration(len(MIGRATIONS) + 1, 'forbidden', 'CREATE TABLE reader_cannot_migrate(id integer)')])
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    reader.execute('DELETE FROM oko_schema_migrations')
        finally:
            admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_one_store_owns_recovery_even_with_a_different_data_root(postgres_url, tmp_path):
    from tests.support.storage import Store
    owner = Store(tmp_path / 'owner', lambda _: None, database_url=postgres_url)
    try:
        with pytest.raises(ValueError, match='Another Oko reader'):
            Store(tmp_path / 'contender', lambda _: None, database_url=postgres_url)
        assert owner.work_enabled() is False
    finally:
        owner.db.close()
    successor = Store(tmp_path / 'owner', lambda _: None, database_url=postgres_url)
    successor.db.close()


def test_migration_cannot_run_while_reader_owns_the_schema(postgres_url, tmp_path):
    import psycopg
    from tests.support.storage import Store
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    reader = Store(tmp_path, lambda _: None, database_url=postgres_url)
    with psycopg.connect(postgres_url, autocommit=True) as migrator:
        try:
            with pytest.raises(RuntimeError, match='another reader or migration'):
                migrate(migrator, MIGRATIONS)
            assert reader.work_enabled() is False
        finally:
            reader.db.close()
        migrate(migrator, MIGRATIONS)


def test_read_snapshot_is_consistent_and_rejects_writes(database):
    import psycopg
    from oko.storage.transaction import read_snapshot

    with database() as writer, database() as reader:
        with transaction(writer):
            writer.execute("INSERT INTO state VALUES ('snapshot','before')")
        with read_snapshot(reader):
            assert reader.execute("SELECT value FROM state WHERE key='snapshot'").fetchone()[0] == 'before'
            with transaction(writer):
                writer.execute("UPDATE state SET value='after' WHERE key='snapshot'")
            assert reader.execute("SELECT value FROM state WHERE key='snapshot'").fetchone()[0] == 'before'
        assert reader.execute("SELECT value FROM state WHERE key='snapshot'").fetchone()[0] == 'after'
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction), read_snapshot(reader):
            reader.execute("UPDATE state SET value='wrong' WHERE key='snapshot'")
        assert reader.execute("SELECT value FROM state WHERE key='snapshot'").fetchone()[0] == 'after'


def test_commit_callback_can_open_a_fresh_transaction(database):
    with database() as db:
        def after_commit():
            with transaction(db):
                db.execute("INSERT INTO state VALUES ('nested-callback','saved')")
        with transaction(db):
            db.execute("INSERT INTO state VALUES ('outer','saved')")
            db.after_commit(after_commit)
        assert db.execute("SELECT value FROM state WHERE key='nested-callback'").fetchone()[0] == 'saved'
