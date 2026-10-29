"""Release migration failures and concurrency on an actual, isolated PostgreSQL."""
from contextlib import contextmanager

import psycopg
import pytest

from oko.storage.postgres_migrations import Migration, SchemaMismatch, migrate, verify


@pytest.fixture
def connections(postgres_database_url):
    @contextmanager
    def connect():
        with psycopg.connect(postgres_database_url, autocommit=True) as db:
            yield db
    yield connect


def test_atomic_release_and_restart(connections):
    first = Migration(1, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
    broken = Migration(2, 'broken', 'CREATE TABLE partial(id text); SELECT missing_column FROM library')
    with connections() as db:
        with pytest.raises(SchemaMismatch, match='not been migrated'):
            verify(db, [first])
        with pytest.raises(psycopg.errors.UndefinedColumn):
            migrate(db, [first, broken])
        assert db.execute("SELECT to_regclass('library'),to_regclass('partial'),to_regclass('oko_schema_migrations')").fetchone() == (None, None, None)
        migrate(db, [first])
        db.execute("INSERT INTO library VALUES ('saved')")
    with connections() as restarted:
        migrate(restarted, [first])
        verify(restarted, [first])
        assert restarted.execute('SELECT id FROM library').fetchall() == [('saved',)]


def test_incompatible_or_changed_history_is_rejected(connections):
    first = Migration(1, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
    second = Migration(2, 'title', 'ALTER TABLE library ADD COLUMN title text')
    with connections() as db:
        migrate(db, [first])
        with pytest.raises(SchemaMismatch, match='needs migration'):
            verify(db, [first, second])
        with pytest.raises(SchemaMismatch, match='history'):
            migrate(db, [Migration(1, 'library', first.sql + '; SELECT 1')])
        migrate(db, [first, second])
        with pytest.raises(SchemaMismatch, match='newer'):
            verify(db, [first])
        db.execute('DELETE FROM oko_schema_migrations WHERE version=1')
        with pytest.raises(SchemaMismatch, match='history'):
            migrate(db, [first, second])


def test_second_migration_cannot_enter_locked_release(connections):
    from oko.storage.postgres_migrations import schema_lock_key
    first = Migration(1, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
    with connections() as owner, connections() as contender:
        key = schema_lock_key(owner)
        owner.execute('SELECT pg_advisory_lock(%s)', (key,))
        try:
            with pytest.raises(RuntimeError, match='another reader or migration'):
                migrate(contender, [first])
            assert contender.execute("SELECT to_regclass('oko_schema_migrations')").fetchone()[0] is None
        finally:
            owner.execute('SELECT pg_advisory_unlock(%s)', (key,))
        migrate(contender, [first])
        verify(contender, [first])


def test_verification_is_read_only_and_migration_requires_idle_owner(connections):
    first = Migration(1, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
    with connections() as db:
        with pytest.raises(ValueError, match='consecutive'):
            migrate(db, [Migration(2, 'gap', 'SELECT 1')])
        assert db.execute("SELECT to_regclass('oko_schema_migrations')").fetchone()[0] is None
        migrate(db, [first])
        with db.transaction():
            db.execute('SET TRANSACTION READ ONLY')
            verify(db, [first])
            with pytest.raises(ValueError, match='idle autocommit'):
                migrate(db, [first])
        verify(db, [first])


def test_native_library_constraints_and_persistent_job_identity(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS)
        assert db.execute("SELECT value FROM state WHERE key='work-enabled'").fetchone() == ('false',)
        with db.transaction():
            db.execute("INSERT INTO feeds(id,platform,external_id) VALUES ('first','rss','https://source.test'),('second','rss','https://other.test')")
            db.execute("INSERT INTO items(id,platform,external_id,published) VALUES ('article','rss','https://source.test/article',1792870000.123456)")
            db.execute("INSERT INTO feed_publications VALUES ('first','article'),('second','article')")
            db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,size_bytes) VALUES ('media','article','image','https://media.test/image','image','pending',5000000000)")
            first_job = db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch','article') RETURNING id").fetchone()[0]
        with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
            db.execute("INSERT INTO items(id,platform,external_id) VALUES ('duplicate','rss','https://source.test/article')")
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
            db.execute("DELETE FROM items WHERE id='article'")
    with connections() as restarted:
        second_job = restarted.execute("INSERT INTO jobs(kind,item) VALUES ('filter','article') RETURNING id").fetchone()[0]
        assert second_job > first_job
        assert restarted.execute("SELECT published FROM items WHERE id='article'").fetchone()[0] == 1792870000.123456
        assert restarted.execute("SELECT size_bytes FROM media_files WHERE id='media'").fetchone()[0] == 5000000000
        assert restarted.execute("SELECT count(*) FROM reading_memberships WHERE publication_id='article'").fetchone()[0] == 2


def test_telegram_claim_migration_preserves_owners(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:4])
        db.execute("INSERT INTO telegram_source_claims VALUES ('username','example')")
        db.execute("INSERT INTO telegram_claim_owners VALUES ('username','example','feed','verified')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('telegram_source_claims')").fetchone() == (None,)
        assert db.execute('SELECT kind,value,feed,evidence FROM telegram_claim_owners').fetchall() == [
            ('username', 'example', 'feed', 'verified')
        ]
        verify(db, MIGRATIONS)


def test_membership_upgrade_preserves_primary_and_pending_cleanup(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:5])
        db.execute("INSERT INTO feeds(id) VALUES ('first'),('second')")
        db.execute("INSERT INTO items(id,feed_id,platform,external_id) VALUES ('shared','second','youtube','video')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id) VALUES ('first','shared'),('second','shared')")
        db.execute("INSERT INTO items(id,feed_id,viewed,platform,external_id) VALUES ('cleaning','first',1,'youtube','old')")
        db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','cleaning','{\"other\":\"preserved\"}')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT feed_id FROM items_read WHERE id='shared'").fetchone() == ('second',)
        assert db.execute("SELECT feed_id FROM items_read WHERE id='cleaning'").fetchone() == ('first',)
        assert db.execute("SELECT payload::jsonb->>'other' FROM jobs WHERE kind='delete' AND item='cleaning'").fetchone() == ('preserved',)
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='items' AND column_name='feed_id'").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM feed_publications WHERE publication_id='shared' AND is_unviewed").fetchone() == (2,)
        db.execute("UPDATE items SET viewed=1 WHERE id='shared'")
        assert db.execute("SELECT count(*) FROM feed_publications WHERE publication_id='shared' AND is_unviewed").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM unviewed_items_by_feed WHERE id='shared'").fetchone() == (0,)
        db.execute("UPDATE items SET viewed=0 WHERE id='shared'")
        assert db.execute("SELECT count(*) FROM feed_publications WHERE publication_id='shared' AND is_unviewed").fetchone() == (2,)
        with pytest.raises(ValueError, match='rollback'), db.transaction():
            db.execute("UPDATE items SET viewed=1 WHERE id='shared'")
            assert db.execute("SELECT count(*) FROM feed_publications WHERE publication_id='shared' AND is_unviewed").fetchone() == (0,)
            raise ValueError('rollback')
        assert db.execute("SELECT count(*) FROM feed_publications WHERE publication_id='shared' AND is_unviewed").fetchone() == (2,)
        before = db.execute("SELECT version FROM reader_entities WHERE kind='status' AND identity='runtime'").fetchone()[0]
        db.execute("DELETE FROM feed_publications WHERE feed_id='second' AND publication_id='shared'")
        assert db.execute("SELECT feed_id FROM items_read WHERE id='shared'").fetchone() == ('first',)
        assert db.execute("SELECT version FROM reader_entities WHERE kind='status' AND identity='runtime'").fetchone()[0] > before


def test_membership_upgrade_rejects_missing_old_owner_atomically(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:5])
        db.execute("INSERT INTO feeds(id) VALUES ('feed')")
        db.execute("INSERT INTO items(id,feed_id) VALUES ('missing','feed')")
        with pytest.raises(psycopg.errors.RaiseException, match='owner is missing'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT feed_id FROM items WHERE id='missing'").fetchone() == ('feed',)
        verify(db, MIGRATIONS[:5])


def test_storage_identity_migrates_without_changing_existing_identity(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:6])
        db.execute("INSERT INTO reader_identity VALUES (1, '4c50e13d-2af6-4e4b-b994-cd47881b92ac')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT value::jsonb #>> '{}' FROM state WHERE key='storage-id'").fetchone() == ('4c50e13d-2af6-4e4b-b994-cd47881b92ac',)
        assert db.execute("SELECT to_regclass('reader_identity')").fetchone() == (None,)
        verify(db, MIGRATIONS)


def test_storage_identity_migration_rejects_conflicting_state_atomically(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:6])
        db.execute("INSERT INTO reader_identity VALUES (1, '4c50e13d-2af6-4e4b-b994-cd47881b92ac')")
        db.execute("INSERT INTO state VALUES ('storage-id', '\"different\"')")
        with pytest.raises(psycopg.errors.RaiseException, match='conflicts with reader identity'):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT value FROM reader_identity WHERE id=1').fetchone() == ('4c50e13d-2af6-4e4b-b994-cd47881b92ac',)
        assert db.execute("SELECT value FROM state WHERE key='storage-id'").fetchone() == ('"different"',)
        verify(db, MIGRATIONS[:6])


def test_storage_identity_migration_rejects_invalid_identity(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:6])
        db.execute("INSERT INTO reader_identity VALUES (1, 'not-a-uuid')")
        with pytest.raises(psycopg.errors.RaiseException, match='invalid reader identity'):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT value FROM reader_identity WHERE id=1').fetchone() == ('not-a-uuid',)
        verify(db, MIGRATIONS[:6])


def test_avatar_migration_moves_bytes_and_removes_table_and_triggers(connections):
    import hashlib
    from oko.storage.postgres_schema import MIGRATIONS
    image = b'\x89PNG\r\n\x1a\nsynthetic-avatar'
    digest = hashlib.sha256(image).hexdigest()
    with connections() as db:
        migrate(db, MIGRATIONS[:7])
        db.execute("INSERT INTO feeds(id,kind) VALUES ('feed','telegram')")
        db.execute('INSERT INTO telegram_avatars VALUES (%s,%s,%s,%s)', ('feed', image, 'image/png', digest))
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT avatar_image, avatar_mime, avatar_digest FROM feeds WHERE id='feed'").fetchone() == (image, 'image/png', digest)
        assert db.execute("SELECT to_regclass('telegram_avatars')").fetchone() == (None,)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'reader_v_telegram_avatars_%' AND NOT t.tgisinternal").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.proname IN ('oko_trigger_reader_v_telegram_avatars_delete','oko_trigger_reader_v_telegram_avatars_insert','oko_trigger_reader_v_telegram_avatars_update')").fetchone() == (0,)
        verify(db, MIGRATIONS)


@pytest.mark.parametrize('invalid', ['orphan', 'digest', 'oversize', 'mime'])
def test_avatar_migration_rejects_invalid_data_without_losing_it(connections, invalid):
    import hashlib
    from oko.storage.postgres_schema import MIGRATIONS
    image = b'\x89PNG\r\n\x1a\nsynthetic-avatar'
    if invalid == 'oversize':
        image += b'x' * (128 * 1024)
    digest = hashlib.sha256(image).hexdigest() if invalid != 'digest' else 'wrong'
    mime = 'image/png' if invalid != 'mime' else 'image/jpeg'
    with connections() as db:
        migrate(db, MIGRATIONS[:7])
        if invalid != 'orphan':
            db.execute("INSERT INTO feeds(id,kind) VALUES ('feed','telegram')")
        db.execute('INSERT INTO telegram_avatars VALUES (%s,%s,%s,%s)', ('feed', image, mime, digest))
        with pytest.raises(psycopg.errors.RaiseException, match='invalid stored avatar'):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT image,mime,digest FROM telegram_avatars WHERE feed=%s', ('feed',)).fetchone() == (image, mime, digest)
        verify(db, MIGRATIONS[:7])


def test_collection_status_migration_preserves_attempts_and_feed_results(connections):
    import json
    from oko.storage.postgres_schema import MIGRATIONS
    attempt = {'attempt_id': '1:2', 'started_at': 123.0, 'last_error': {'message': 'synthetic'}}
    result = {'success_at': 456.0, 'last_error': {'message': 'earlier'}}
    with connections() as db:
        migrate(db, MIGRATIONS[:8])
        db.execute("INSERT INTO feeds(id,kind) VALUES ('feed','rss')")
        job = db.execute("INSERT INTO jobs(kind,item) VALUES ('discover','feed') RETURNING id").fetchone()[0]
        db.execute('INSERT INTO collection_attempts VALUES (%s,%s,%s)', (job, 'feed', json.dumps(attempt)))
        db.execute('INSERT INTO collection_results VALUES (%s,%s)', ('feed', json.dumps(result)))
        migrate(db, MIGRATIONS)
        assert db.execute('SELECT feed_id, collection_attempt FROM jobs WHERE id=%s', (job,)).fetchone() == ('feed', attempt)
        assert db.execute("SELECT last_collection_result FROM feeds WHERE id='feed'").fetchone() == (result,)
        db.execute("INSERT INTO jobs(kind,item,feed_id) VALUES ('filter','publication','feed')")
        assert db.execute("SELECT to_regclass('collection_attempts'),to_regclass('collection_results')").fetchone() == (None, None)
        names = ('collection_feed_deleted', 'collection_job_deleted')
        assert db.execute('SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname = ANY(%s)', (list(names),)).fetchone() == (0,)
        verify(db, MIGRATIONS)


@pytest.mark.parametrize('invalid', ['orphan-job', 'orphan-feed', 'orphan-result', 'bad-attempt', 'bad-result', 'invalid-json', 'wrong-kind'])
def test_collection_status_migration_rejects_ambiguous_data(connections, invalid):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:8])
        db.execute("INSERT INTO feeds(id,kind) VALUES ('feed','rss')")
        job = db.execute('INSERT INTO jobs(kind,item) VALUES (%s,%s) RETURNING id',
                         ('filter' if invalid == 'wrong-kind' else 'discover', 'feed')).fetchone()[0]
        if invalid in ('bad-result', 'orphan-result'):
            db.execute('INSERT INTO collection_results VALUES (%s,%s)',
                       ('missing' if invalid == 'orphan-result' else 'feed', '[]' if invalid == 'bad-result' else '{}'))
        else:
            db.execute('INSERT INTO collection_attempts VALUES (%s,%s,%s)',
                       (job + 1000 if invalid == 'orphan-job' else job,
                        'missing' if invalid == 'orphan-feed' else 'feed',
                        '[]' if invalid == 'bad-attempt' else '{' if invalid == 'invalid-json' else '{}'))
        with pytest.raises(psycopg.errors.RaiseException, match='invalid collection status'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('collection_attempts'),to_regclass('collection_results')").fetchone() == ('collection_attempts', 'collection_results')
        verify(db, MIGRATIONS[:8])


def test_feed_details_migration_preserves_missing_empty_and_saved_values(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:9])
        db.execute("INSERT INTO feeds(id,description,created_at) VALUES ('missing','',NULL),('empty','',10),('saved','Saved',20),('detail-only','',NULL)")
        db.execute("INSERT INTO feed_details(feed,created,modified,updated,marked_read,description) VALUES ('missing',NULL,NULL,NULL,NULL,NULL),('empty',10,11,12,13,''),('saved',20,21,22,23,'Saved'),('detail-only',30,31,32,33,'Only in details')")
        migrate(db, MIGRATIONS)
        rows = db.execute('SELECT id,description,created_at,modified_at,last_discovery_at,last_viewed_at FROM feeds ORDER BY id').fetchall()
        assert rows == [('detail-only','Only in details',30,31,32,33), ('empty','',10,11,12,13), ('missing',None,None,None,None,None), ('saved','Saved',20,21,22,23)]
        assert db.execute("SELECT to_regclass('feed_details')").fetchone() == (None,)
        verify(db, MIGRATIONS)


@pytest.mark.parametrize('conflict', ['orphan', 'description', 'created', 'missing-description', 'missing-created', 'nonfinite'])
def test_feed_details_migration_rejects_conflicting_data(connections, conflict):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:9])
        db.execute("INSERT INTO feeds(id,description,created_at) VALUES ('feed','old',10)")
        if conflict == 'orphan':
            db.execute("INSERT INTO feed_details(feed,description) VALUES ('missing','orphan')")
        elif conflict == 'description':
            db.execute("INSERT INTO feed_details(feed,created,description) VALUES ('feed',10,'different')")
        elif conflict == 'created':
            db.execute("INSERT INTO feed_details(feed,created,description) VALUES ('feed',20,'old')")
        elif conflict == 'missing-description':
            db.execute("INSERT INTO feed_details(feed,created,description) VALUES ('feed',10,NULL)")
        elif conflict == 'missing-created':
            db.execute("INSERT INTO feed_details(feed,created,description) VALUES ('feed',NULL,'old')")
        else:
            db.execute("INSERT INTO feed_details(feed,created,modified,description) VALUES ('feed',10,'NaN'::float8,'old')")
        with pytest.raises(psycopg.errors.RaiseException, match='conflicting feed details'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('feed_details')").fetchone() == ('feed_details',)
        verify(db, MIGRATIONS[:9])
