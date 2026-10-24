"""Repository transaction ownership against real PostgreSQL and independent readers."""
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


def test_close_discards_uncommitted_work_and_raw_commit_cannot_escape_owner(database):
    with database() as reader:
        with database() as writer:
            writer.execute("INSERT INTO state VALUES ('test','uncommitted')")
            with pytest.raises(ValueError, match='owned transaction'):
                writer.execute('COMMIT')
        assert reader.execute("SELECT 1 FROM state WHERE key='test'").fetchone() is None


def test_empty_store_starts_disabled_without_sqlite_or_runtime_ddl(postgres_url, tmp_path):
    from oko.storage.store import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        assert store.work_enabled() is False
        assert store.subscriptions.folders() == []
        assert store.subscriptions.feeds() == []
        assert not (tmp_path / 'state.sqlite').exists()
        assert not store.db.in_transaction
    finally:
        store.db.close()


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
        material = wait_for(client, lambda: (value if (value := client.get('/api/v3/materials/' + item).json())['content']['attachments'] else None))
        assert 'Collected text' in material['content']['text']
        frame = client.post('/api/v3/reading/check', json={'storage_id': None, 'resources': [
            {'kind': 'navigation', 'known_version': None},
            {'kind': 'list', 'options': {'target': {'scope': 'all'}, 'sort': 'newest', 'show_hidden': False}, 'known_version': None, 'known_cards': {}}
        ]})
        assert frame.status_code == 200, frame.text
        snapshot = frame.json()
        assert snapshot['state'] == 'current'
        assert [resource for resource in snapshot['resources'] if resource['kind'] == 'card'][0]['id'] == item
        media = material['content']['attachments'][0]['url']
        assert client.get(media).content == PNG
        previous = requests.count('/feed')
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: requests.count('/feed') > previous)
        assert len(client.get('/api/v3/cards').json()) == 1
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        assert not (tmp_path / 'state.sqlite').exists()
    with TestClient(app(), headers=HEADERS) as restarted:
        assert [value['id'] for value in restarted.get('/api/v3/cards').json()] == [item]
        assert restarted.get(media).content == PNG
        restored = restarted.get('/api/v3/reading/materials/' + item)
        assert restored.status_code == 200, restored.text
        assert restored.json()['storage_id'] == snapshot['storage_id']
        assert restarted.get('/api/v3/subscriptions').json()['folders'][0]['title'] == 'Renamed library'
        fail_delete[0] = delete_fault
        viewed = restarted.post('/api/v3/materials/' + item + '/viewed')
        assert viewed.status_code == 200, viewed.text
        if delete_fault:
            wait_for(restarted, lambda: bool(failed_deletions))
        assert restarted.get('/api/v3/cards').json() == []
        assert restarted.get('/api/v3/materials/' + item).status_code == 404
    if delete_fault:
        fail_delete[0] = False
        with TestClient(app(), headers=HEADERS) as recovered:
            # Oko remains powered off; an explicit replay resumes its retained cleanup.
            assert recovered.post('/api/v3/materials/' + item + '/viewed').status_code == 200
            wait_for(recovered, lambda: not any(path.is_file() for path in (tmp_path / 'media').rglob('*')))
            assert recovered.get(media).status_code == 404
            assert recovered.get('/api/v3/cards').json() == []


def test_shared_publication_survives_primary_feed_deletion(postgres_url, tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS, wait_for
    from tests.support.subscriptions import add_source
    from tests.support.youtube import CHANNEL, PNG, feed_xml

    def response(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    class Captions:
        async def fetch(self, video):
            return 'Shared synthetic captions'

    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url,
                              rss_enabled=True, rss_transport=httpx.MockTransport(response), youtube_captions=Captions()), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''}).status_code == 200
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        feeds = [add_source(client, 'https://www.youtube.com/feeds/videos.xml?channel_id=' + channel, 'Source ' + str(index), 'Folder ' + str(index))
                 for index, channel in enumerate((CHANNEL, 'UC' + 'b' * 22))]
        cards = wait_for(client, lambda: (cards if len(cards := client.get('/api/v3/cards').json()) == 1 and all(client.get('/api/v3/cards', params={'feed': feed['id']}).json() for feed in feeds) else None))
        item = cards[0]['id']
        material = wait_for(client, lambda: (value if (value := client.get('/api/v3/materials/' + item).json())['content']['attachments'] else None))
        media = material['content']['attachments'][0]['url']
        primary = next(feed for feed in feeds if feed['id'] == material['feed_id'])
        remaining = next(feed for feed in feeds if feed['id'] != primary['id'])
        composition = client.get('/api/v3/reading-composition')
        assert composition.status_code == 200, composition.text
        assert composition.json()['count'] == 1 and [value['count'] for value in composition.json()['feeds']] == [1, 1]
        deletion = client.delete('/api/v3/feeds/' + primary['id'])
        assert deletion.status_code == 200, deletion.text
        wait_for(client, lambda: not any(feed['id'] == primary['id'] for feed in client.get('/api/v3/subscriptions').json()['feeds']))
        assert [card['id'] for card in client.get('/api/v3/cards', params={'feed': remaining['id']}).json()] == [item]
        assert client.get(media).content == PNG
        assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        assert client.get('/api/v3/cards').json() == []
        assert client.get(media).status_code == 404


def test_reader_role_needs_no_schema_ownership_or_migration_credentials(postgres_url, tmp_path):
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from oko.storage.postgres_migrations import migrate, Migration
    from oko.storage.postgres_schema import MIGRATIONS
    from oko.storage.store import Store

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
                store.subscriptions.ensure_demo()
                assert store.subscriptions.feeds()[0]['id'] == 'demo'
                store.set_work_enabled(True)
                assert store.work_enabled() is True
            finally:
                store.db.close()
            with psycopg.connect(reader_dsn, autocommit=True) as reader:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    reader.execute('CREATE TABLE reader_cannot_install_schema(id integer)')
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    migrate(reader, [*MIGRATIONS, Migration(4, 'forbidden', 'CREATE TABLE reader_cannot_migrate(id integer)')])
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    reader.execute('DELETE FROM oko_schema_migrations')
        finally:
            admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
            admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_one_store_owns_recovery_even_with_a_different_data_root(postgres_url, tmp_path):
    from oko.storage.store import Store
    owner = Store(tmp_path / 'owner', lambda _: None, database_url=postgres_url)
    try:
        with pytest.raises(ValueError, match='Another Oko reader'):
            Store(tmp_path / 'contender', lambda _: None, database_url=postgres_url)
        assert owner.work_enabled() is False
    finally:
        owner.db.close()
    successor = Store(tmp_path / 'owner', lambda _: None, database_url=postgres_url)
    successor.db.close()


def test_incompatible_schema_is_an_explicit_http_storage_failure(postgres_url, tmp_path):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute('DELETE FROM oko_schema_migrations WHERE version=3')
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        status = client.get('/api/v3/status')
        assert status.status_code == 200, status.text
        assert status.json()['work'][0]['reason'] == 'storage_unavailable'
        assert client.get('/api/v3/cards').status_code == 503
        assert not (tmp_path / 'state.sqlite').exists()


def test_migration_cannot_run_while_reader_owns_the_schema(postgres_url, tmp_path):
    import psycopg
    from oko.storage.store import Store
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


def test_bound_values_and_leading_comments_preserve_transaction_ownership(database):
    value = "Текст ? :key % ' IS NOT; DELETE FROM state;"
    with database() as writer, database() as reader:
        with pytest.raises(RuntimeError, match='rollback'), transaction(writer):
            writer.execute('-- owned write\nINSERT INTO state VALUES (?,?)', ('lexical', value))
            assert reader.execute("SELECT value FROM state WHERE key='lexical'").fetchone() is None
            raise RuntimeError('rollback')
        assert reader.execute("SELECT value FROM state WHERE key='lexical'").fetchone() is None
        with transaction(writer):
            writer.execute('INSERT INTO state VALUES (:key,:value)', {'key': 'lexical', 'value': value})
        assert reader.execute('SELECT value FROM state WHERE key=?', ('lexical',)).fetchone()[0] == value
        with pytest.raises(ValueError, match='One repository statement'):
            writer.execute("SELECT 1; DELETE FROM state")
        assert not writer.in_transaction
