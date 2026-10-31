"""Repository transaction ownership against real PostgreSQL and independent readers."""
from tests.support.cards import read_all_cards
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
    from tests.support.storage import Store
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
        material = wait_for(client, lambda: (value if (value := client.get('/api/v3/publication/' + item).json()) and any(media['role'] == 'attachment' for media in value['media']) else None))
        assert 'Collected text' in material['text']
        frame = client.get('/api/v3/navigation')
        assert frame.status_code == 200, frame.text
        storage_id = frame.headers['X-Oko-Storage-Id']
        assert frame.json()['total'] == 1
        assert client.get('/api/v3/cards').json()['items'][0]['id'] == item
        media = [media for media in material['media'] if media['role'] == 'attachment'][0]['url']
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
        cards = wait_for(client, lambda: (cards if len(cards := read_all_cards(client, '/api/v3/cards')) == 1 and all(read_all_cards(client, '/api/v3/cards', params={'feed': feed['id']}) for feed in feeds) else None))
        item = cards[0]['id']
        material = wait_for(client, lambda: (value if (value := client.get('/api/v3/publication/' + item).json()) and any(media['role'] == 'attachment' for media in value['media']) else None))
        media = [media for media in material['media'] if media['role'] == 'attachment'][0]['url']
        primary = next(feed for feed in feeds if feed['id'] == material['feed_id'])
        remaining = next(feed for feed in feeds if feed['id'] != primary['id'])
        composition = client.get('/api/v3/reading-composition')
        assert composition.status_code == 200, composition.text
        assert composition.json()['count'] == 1 and [value['count'] for value in composition.json()['feeds']] == [1, 1]
        deletion = client.delete('/api/v3/feeds/' + primary['id'])
        assert deletion.status_code == 200, deletion.text
        wait_for(client, lambda: not any(feed['id'] == primary['id'] for feed in client.get('/api/v3/subscriptions').json()['feeds']))
        assert [card['id'] for card in read_all_cards(client, '/api/v3/cards', params={'feed': remaining['id']})] == [item]
        assert client.get(media).content == PNG
        assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        assert read_all_cards(client, '/api/v3/cards') == []
        assert client.get(media).status_code == 404


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


def test_incompatible_schema_is_an_explicit_http_storage_failure(postgres_url, tmp_path):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute('DELETE FROM oko_schema_migrations WHERE version=3')
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        status = client.get('/api/v3/status/details')
        assert status.status_code == 200, status.text
        assert status.json()['work'][0]['reason'] == 'storage_unavailable'
        assert client.get('/api/v3/cards').status_code == 503
        assert not (tmp_path / 'state.sqlite').exists()


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


def test_bound_values_and_leading_comments_preserve_transaction_ownership(database):
    value = "Текст ? :key % ' IS NOT; DELETE FROM state;"
    with database() as writer, database() as reader:
        with pytest.raises(RuntimeError, match='rollback'), transaction(writer):
            writer.execute('INSERT INTO state VALUES ( %s , %s )', ('lexical', value))
            assert reader.execute("SELECT value FROM state WHERE key='lexical'").fetchone() is None
            raise RuntimeError('rollback')
        assert reader.execute("SELECT value FROM state WHERE key='lexical'").fetchone() is None
        with transaction(writer):
            writer.execute('-- native PostgreSQL parameters\nINSERT INTO state VALUES (%(key)s,%(value)s)', {'key': 'lexical', 'value': value})
        assert reader.execute('SELECT value FROM state WHERE key = %s', ('lexical',)).fetchone()[0] == value
        with pytest.raises(ValueError, match='One repository statement'):
            writer.execute("SELECT 1; DELETE FROM state")
        assert not writer.in_transaction


def test_operational_summary_includes_terminal_problems_without_changing_reader_queue(postgres_url,tmp_path):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute('INSERT INTO jobs ( kind , item , status , error , problem_code ) VALUES ( %s , %s , %s , %s , %s )',
                             ('fetch','synthetic-failed','failed','private detail','source-failed'))
            store.db.execute('INSERT INTO jobs ( kind , item , status , problem_code ) VALUES ( %s , %s , %s , %s )',
                             ('download_media','synthetic-unavailable','unavailable','media-unavailable'))
        assert store.work_summary()==[]
        result=store.work_summary(include_problems=True)
        assert {(j.kind,j.status,j.count,j.error_code) for j in result}=={
            ('fetch','failed',1,'source-failed'),('download_media','unavailable',1,'media-unavailable')}
    finally:
        store.db.close()


def test_telegram_opened_material_has_all_reading_parts_after_restart(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.telegram import Telegram, connect, settled

    def app():
        return create_app(tmp_path, token='test', api_only=True,
                          database_url=postgres_url, telegram_client=Telegram())

    identity = None
    for _ in range(2):
        with TestClient(app(), headers=HEADERS) as client:
            if identity is None:
                assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
                connect(client)
                identity = settled(client)[0]['id']
                assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
            response = client.get('/api/v3/publication/' + identity)
            assert response.status_code == 200, response.text
            material = response.json()
            assert {'filter', 'theses', 'media', 'text'} <= material.keys()
            assert material['source']['kind'] == 'telegram'
            assert material['source']['access']['kind'] == 'client'
            assert material['text'] == 'Текст канала'
            assert 'rowid' not in material


def test_saved_source_statuses_survive_locale_aware_collation(postgres_url, tmp_path):
    import psycopg
    from psycopg import sql
    from tests.support.storage import Store
    with psycopg.connect(postgres_url, autocommit=True) as admin:
        collation = admin.execute("SELECT collname FROM pg_collation WHERE collname IN ('en-US-x-icu','en_US.utf8') ORDER BY collname LIMIT 1").fetchone()
        assert collation, 'The PostgreSQL test image must provide its locale-aware collation'
        admin.execute(sql.SQL('ALTER TABLE state ALTER COLUMN key TYPE text COLLATE {}').format(sql.Identifier(collation[0])))
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        store.feed_status('demo', error='Synthetic saved failure')
        with store.db:
            store.db.execute("INSERT INTO state VALUES ('feed-status-other','{}')")
        assert store.source_statuses() == [dict(feed_id='demo', error='Synthetic saved failure')]
    finally:
        store.db.close()


def test_data_changing_cte_is_committed_or_rolled_back_with_owned_transaction(database):
    with database() as writer, database() as reader:
        query = "WITH inserted AS (INSERT INTO state VALUES (%s, %s) RETURNING key) SELECT key FROM inserted"
        with pytest.raises(RuntimeError, match='owned transaction'):
            writer.execute_write(query, ('cte', 'unowned'))
        with pytest.raises(RuntimeError, match='rollback'), transaction(writer):
            assert writer.execute_write(query, ('cte', 'rolled back')).fetchone()[0] == 'cte'
            assert reader.execute("SELECT value FROM state WHERE key='cte'").fetchone() is None
            raise RuntimeError('rollback')
        assert reader.execute("SELECT value FROM state WHERE key='cte'").fetchone() is None
        with transaction(writer):
            assert writer.execute_write(query, ('cte', 'committed')).fetchone()[0] == 'cte'
            assert reader.execute("SELECT value FROM state WHERE key='cte'").fetchone() is None
        assert reader.execute("SELECT value FROM state WHERE key='cte'").fetchone()[0] == 'committed'


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


def test_read_snapshot_cannot_schedule_a_write_commit_callback(database):
    from oko.storage.transaction import read_snapshot

    with database() as db:
        effects = []
        with pytest.raises(RuntimeError, match='read-only'), read_snapshot(db):
            db.after_commit(lambda: effects.append('unexpected'))
        with transaction(db):
            db.execute("INSERT INTO state VALUES ('later','write')")
        assert effects == []


def test_explicit_native_write_preserves_boolean_parameters(postgres_url, database):
    import psycopg

    with psycopg.connect(postgres_url, autocommit=True) as admin:
        admin.execute('CREATE TABLE synthetic_boolean_value(value boolean NOT NULL)')
    with database() as writer, database() as reader:
        with transaction(writer):
            writer.execute_write('INSERT INTO synthetic_boolean_value(value) VALUES (%s)', (True,))
        assert reader.execute('SELECT value FROM synthetic_boolean_value').fetchone()[0] is True


def test_storage_id_from_state_survives_reader_restart(postgres_url, tmp_path):
    from uuid import UUID
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS

    def app():
        return create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    with TestClient(app(), headers=HEADERS) as client:
        first = client.get('/api/v3/status').headers['X-Oko-Storage-Id']
        assert str(UUID(first)) == first
    with TestClient(app(), headers=HEADERS) as client:
        assert client.get('/api/v3/status').headers['X-Oko-Storage-Id'] == first


def test_source_deletion_and_reader_freshness_roll_back_together(postgres_url, tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.worker_library import worker_library

    store, item, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client:
        path = '/api/v3/publication/' + item
        before = client.get(path)
        assert before.status_code == 200 and before.json()['source_deleted'] is False
        def mark(fail):
            current = app.state.host.require_store()
            with transaction(current.db):
                current.publications.set_source_deleted([item], True)
                if fail:
                    raise RuntimeError('synthetic rollback')
        with pytest.raises(RuntimeError, match='synthetic rollback'):
            client.portal.call(mark, True)
        assert client.get(path, headers={'If-None-Match': before.headers['ETag']}).status_code == 304
        client.portal.call(mark, False)
        after = client.get(path, headers={'If-None-Match': before.headers['ETag']})
        assert after.status_code == 200 and after.json()['source_deleted'] is True
        client.portal.call(mark, False)
        assert client.get(path, headers={'If-None-Match': after.headers['ETag']}).status_code == 304


def test_graph_lock_serializes_membership_changes(postgres_url, tmp_path):
    import psycopg
    from oko.storage.memberships import lock_graph
    from tests.support.storage import Store
    store = Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with psycopg.connect(postgres_url) as other:
            with transaction(store.db):
                lock_graph(store.db)
                assert not other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),%s))', (0x4F4B4F4752415048,)).fetchone()[0]
            other.rollback()
            assert other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),%s))', (0x4F4B4F4752415048,)).fetchone()[0]
            other.rollback()
    finally:
        store.db.close()


def test_viewed_membership_changes_and_rollback_together(postgres_url, tmp_path):
    from oko.storage import memberships
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        store.subscriptions.ensure_demo()
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('item')")
            memberships.lock_graph(store.db)
            memberships.add(store.db, 'demo', 'item')
        with pytest.raises(RuntimeError, match='rollback'):
            with transaction(store.db):
                memberships.lock_graph(store.db)
                memberships.set_viewed(store.db, ['item'], True)
                assert store.db.execute("SELECT is_unviewed FROM feed_publications WHERE publication_id='item'").fetchone()[0] is False
                raise RuntimeError('rollback')
        assert store.db.execute("SELECT viewed FROM publications WHERE id='item'").fetchone()[0] == 0
        assert store.db.execute("SELECT is_unviewed FROM feed_publications WHERE publication_id='item'").fetchone()[0] is True
        with transaction(store.db):
            memberships.lock_graph(store.db)
            memberships.set_viewed(store.db, ['item'], True)
        assert store.db.execute("SELECT is_unviewed FROM feed_publications WHERE publication_id='item'").fetchone()[0] is False
        with transaction(store.db):
            memberships.lock_graph(store.db)
            memberships.set_viewed(store.db, ['item'], False)
        assert store.db.execute("SELECT is_unviewed FROM feed_publications WHERE publication_id='item'").fetchone()[0] is True
    finally:
        store.db.close()


def test_discovery_queue_time_is_preserved_until_requeued(postgres_url, tmp_path):
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        store.jobs.enqueue_discovery('feed')
        first = store.db.execute("SELECT queued_wall FROM jobs WHERE kind='discover' AND item='feed'").fetchone()[0]
        assert first is not None
        store.jobs.enqueue_discovery('feed')
        assert store.db.execute("SELECT queued_wall FROM jobs WHERE kind='discover' AND item='feed'").fetchone()[0] == first
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',queued_wall=1 WHERE kind='discover' AND item='feed'")
        with pytest.raises(RuntimeError, match='rollback'):
            with transaction(store.db):
                store.jobs.enqueue_discovery('feed')
                assert store.db.execute("SELECT queued_wall FROM jobs WHERE kind='discover' AND item='feed'").fetchone()[0] > 1
                raise RuntimeError('rollback')
        assert store.db.execute("SELECT status,queued_wall FROM jobs WHERE kind='discover' AND item='feed'").fetchone() == ('done',1)
        store.jobs.enqueue_discovery('feed')
        assert store.db.execute("SELECT queued_wall FROM jobs WHERE kind='discover' AND item='feed'").fetchone()[0] > 1
    finally:
        store.db.close()


def test_fetch_queue_time_survives_repeat_and_changes_after_retry(postgres_url, tmp_path):
    from oko.collection.adapters import AdapterErrorKind, AdapterIssue
    from oko.storage.job_types import Job
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        with store.db:
            store.jobs.enqueue_fetch('item')
        first = store.db.execute("SELECT id,generation,queued_wall FROM jobs WHERE kind='fetch' AND item='item'").fetchone()
        assert first['queued_wall'] is not None
        with store.db:
            store.jobs.enqueue_fetch('item')
        assert store.db.execute("SELECT queued_wall FROM jobs WHERE id=%s", (first['id'],)).fetchone()[0] == first['queued_wall']
        with store.db:
            store.db.execute("UPDATE jobs SET status='running',queued_wall=1 WHERE id=%s", (first['id'],))
            current = store.db.execute('SELECT generation FROM jobs WHERE id=%s', (first['id'],)).fetchone()[0]
            store.jobs.retry(Job(first['id'],'fetch','item',current),0,'temporary',issue=AdapterIssue('test','temporary',AdapterErrorKind.temporary))
        queued = store.db.execute("SELECT status,queued_wall FROM jobs WHERE id=%s", (first['id'],)).fetchone()
        assert queued['status'] == 'pending' and queued['queued_wall'] > 1
    finally:
        store.db.close()


def test_action_status_freshness_is_atomic_and_ignores_duplicate_start(postgres_url, tmp_path):
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    context = dict(identity='action', title='Action', objects=[], action=None)
    def revision():
        row = store.read_versions.version('status','runtime')
        return row[0] if row else 0
    try:
        first = revision()
        with pytest.raises(RuntimeError, match='rollback'):
            with transaction(store.db):
                store.problems.action_started('receipt', context, 1)
                assert revision() > first
                raise RuntimeError('rollback')
        assert revision() == first
        store.problems.action_started('receipt', context, 1)
        started = revision()
        assert started > first
        store.problems.action_started('receipt', context, 1)
        assert revision() == started
        store.problems.action_finished('receipt', True, 2)
        finished = revision()
        assert finished > started
        store.problems.action_finished('receipt', True, 2)
        assert revision() == finished
    finally:
        store.db.close()


def test_problem_status_freshness_is_atomic_and_unchanged_on_repeat(postgres_url, tmp_path):
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    candidate = dict(id='test', code='test', objects=[], state='current', message='first')
    def revision():
        row = store.read_versions.version('status','runtime')
        return row[0] if row else 0
    try:
        before = revision()
        with pytest.raises(RuntimeError, match='rollback'):
            with transaction(store.db):
                store.problems.reconcile([candidate.copy()], 1, {})
                assert revision() > before
                raise RuntimeError('rollback')
        assert revision() == before
        store.problems.reconcile([candidate.copy()], 1, {})
        created = revision()
        assert created > before
        store.problems.reconcile([candidate.copy()], 1, {})
        assert revision() == created
        store.problems.reconcile([dict(candidate, message='changed')], 2, {})
        updated = revision()
        assert updated > created
        store.problems.reconcile([], 3, {})
        assert revision() > updated
    finally:
        store.db.close()


def test_viewed_status_version_changes_atomically(postgres_url, tmp_path):
    from oko.storage import memberships
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    def revision():
        return store.read_versions.version('status','runtime')[0]
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id) VALUES ('item')")
        before = revision()
        with pytest.raises(RuntimeError, match='rollback'):
            with transaction(store.db):
                memberships.lock_graph(store.db)
                memberships.set_viewed(store.db,['item'],True)
                assert revision() > before
                raise RuntimeError('rollback')
        assert revision() == before
        with transaction(store.db):
            memberships.lock_graph(store.db)
            memberships.set_viewed(store.db,['item'],True)
        after = revision()
        assert after > before
        with transaction(store.db):
            memberships.lock_graph(store.db)
            memberships.set_viewed(store.db,['item'],True)
        assert revision() == after
    finally:
        store.db.close()


def test_job_status_version_tracks_queue_and_rollback(postgres_url, tmp_path):
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    def revision():
        return store.read_versions.version('status','runtime')[0]
    try:
        store.subscriptions.ensure_demo()
        before = revision()
        with pytest.raises(RuntimeError, match='rollback'):
            with transaction(store.db):
                store.jobs.enqueue_discovery('demo')
                assert revision() > before
                raise RuntimeError('rollback')
        assert revision() == before
        store.jobs.enqueue_discovery('demo')
        added = revision()
        assert added > before
        store.jobs.enqueue_discovery('demo')
        assert revision() == added
        job = store.jobs.next_job(0)
        assert job is not None
        claimed = revision()
        assert claimed > added
        with store.db:
            store.jobs.finish(job)
        assert revision() > claimed
    finally:
        store.db.close()


def test_repeated_fetch_enqueue_keeps_status_revision(postgres_url, tmp_path):
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        with store.db:
            store.jobs.enqueue_fetch('item')
        before = store.read_versions.version('status','runtime')[0]
        with store.db:
            store.jobs.enqueue_fetch('item')
        assert store.read_versions.version('status','runtime')[0] == before
    finally:
        store.db.close()


def test_read_confirmation_preserves_legacy_null_frontier(postgres_url, tmp_path):
    from oko.storage.read_sync import ReadPlan
    from tests.support.storage import Store
    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO telegram_read_state(feed,initial,confirmed) VALUES ('feed',1,NULL)")
            store.read_sync.completed('feed', ReadPlan(1,0,10,10), 10)
            assert store.db.execute("SELECT confirmed FROM telegram_read_state WHERE feed='feed'").fetchone()[0] is None
    finally:
        store.db.close()


@pytest.mark.parametrize('change', ['settings','feed','processor'])
def test_configuration_takes_graph_lock_before_writes(postgres_url, tmp_path, change):
    import psycopg
    from oko.processing.llamacpp import ProcessorConfig
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import FeedPatch, Overrides
    from tests.support.worker_library import worker_library

    store, _, execution, _ = worker_library(tmp_path,database_url=postgres_url)
    statements = []
    try:
        store.db.set_trace_callback(statements.append)
        with psycopg.connect(postgres_url) as other, transaction(store.db):
            if change=='settings':
                SubscriptionService(execution.require_store).update_settings('feed','demo',Overrides(prompt_filters='Changed rules'))
            elif change=='feed':
                SubscriptionService(execution.require_store).patch_feed('demo',FeedPatch(public_ai=True))
            else:
                store.configure_processor(ProcessorConfig(endpoint='http://localhost:9932',model='other',local_execution=True))
            assert not other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),%s))', (0x4F4B4F4752415048,)).fetchone()[0]
        lock = next(i for i,query in enumerate(statements) if 'pg_advisory_xact_lock' in query)
        mutation = next(i for i,query in enumerate(statements) if query.lstrip().upper().startswith(('INSERT INTO SETTINGS','UPDATE FEEDS','INSERT INTO STATE')))
        assert lock < mutation, statements
    finally:
        store.db.set_trace_callback(None)
        store.db.close()


def test_settings_wait_for_membership_commit_and_include_new_owner(postgres_url, tmp_path):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    from tests.support.worker_library import worker_library

    store, item, execution, _ = worker_library(tmp_path,database_url=postgres_url)
    try:
        other_feed = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/new-owner',title='New owner',folder='Other',verification='synthetic'))['id']
        with transaction(store.db):
            store.subscriptions.update_settings('feed','demo',Overrides(filter_enabled=False))
            store.settings_changed('feed','demo')
        entered = threading.Event()
        def update_settings():
            entered.set()
            with transaction(store.db):
                SubscriptionService(execution.require_store).update_settings('feed',other_feed,Overrides(prompt_filters='New owner rules'))
        with ThreadPoolExecutor(max_workers=1) as pool, psycopg.connect(postgres_url) as other:
            other.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))', (0x4F4B4F4752415048,))
            other.execute('INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES (%s,%s,false)', (other_feed,item))
            backend = store.db._db.info.backend_pid
            future = pool.submit(update_settings)
            assert entered.wait(2)
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                waiting = other.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', (backend,)).fetchone()
                if waiting and waiting[0]=='Lock':
                    break
                time.sleep(.01)
            assert waiting and waiting[0]=='Lock'
            assert not future.done()
            other.commit()
            future.result(timeout=5)
        actual = store.db.execute("SELECT input_snapshot->>'signature' FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
        assert actual and actual[0] == store.filters.signature(item)
    finally:
        store.db.close()


def test_processor_configuration_does_not_interrupt_unrelated_fetch(postgres_url, tmp_path):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.worker_library import worker_library

    store, item, _, _ = worker_library(tmp_path,database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    app = create_app(tmp_path,token='test',api_only=True,database_url=postgres_url)
    with TestClient(app,headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            db.execute("""INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch',%s,'running',42)
                ON CONFLICT(kind,item) DO UPDATE SET status='running',generation=42""", (item,))
        response = client.put('/api/v3/processors/llamacpp',json={'endpoint':'http://localhost:9932','model':'other','local_execution':True})
        assert response.status_code==200, response.text
        with psycopg.connect(postgres_url) as db:
            assert db.execute("SELECT status,generation FROM jobs WHERE kind='fetch' AND item=%s", (item,)).fetchone() == ('running',42)


def test_scheduled_discovery_deadline_survives_restart_and_clock_change(postgres_url, tmp_path):
    from tests.support.storage import Store
    monotonic = [100.0]
    wall = [1000.0]
    options = dict(database_url=postgres_url,clock=lambda:monotonic[0],wall_clock=lambda:wall[0])
    store = Store(tmp_path,lambda _:None,**options)
    try:
        store.subscriptions.ensure_demo()
        store.jobs.enqueue_discovery('demo',160.0)
        assert store.jobs.next_job(159.0) is None
    finally:
        store.db.close()
    monotonic[0],wall[0] = 500.0,1020.0
    reopened = Store(tmp_path,lambda _:None,**options)
    try:
        saved = reopened.db.execute("SELECT next,retry_at FROM jobs WHERE kind='discover' AND item=''").fetchone()
        assert saved[:] == (540.0,1060.0)
        assert reopened.jobs.next_job(539.0) is None
        job = reopened.jobs.next_job(540.0)
        assert job and job.kind=='discover'
    finally:
        reopened.db.close()


def test_membership_rejects_deleting_feed_and_rolls_back(postgres_url, tmp_path):
    from oko.storage import memberships
    from tests.support.worker_library import worker_library
    store, item, _, _ = worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,connection) VALUES ('deleting','deleting')")
        with pytest.raises(ValueError,match='feed is deleting'):
            with transaction(store.db):
                memberships.lock_graph(store.db)
                memberships.add(store.db,'deleting',item)
        assert store.db.execute("SELECT 1 FROM feed_publications WHERE feed_id='deleting' AND publication_id=%s", (item,)).fetchone() is None
    finally:
        store.db.close()


def test_discovery_locks_graph_before_identity_resolution(postgres_url, tmp_path):
    from oko.collection.contracts import Material, Publication
    from tests.support.source import publication_packet
    from tests.support.worker_library import worker_library
    store, _, _, _ = worker_library(tmp_path,database_url=postgres_url)
    statements = []
    try:
        packet = publication_packet(Publication('another','https://example.org/another',123),Material('Another','Synthetic text'),needs_fetch=False)
        store.db.set_trace_callback(statements.append)
        with transaction(store.db):
            store.collect_discovery([packet],'{}')
        lock = next(i for i,query in enumerate(statements) if 'pg_advisory_xact_lock' in query)
        identity = next(i for i,query in enumerate(statements) if 'SELECT id,source_id,ARRAY[]::bigint[] AS source_jobs FROM publications' in query)
        assert lock < identity
    finally:
        store.db.set_trace_callback(None)
        store.db.close()
