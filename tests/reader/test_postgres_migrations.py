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
        migrate(db, MIGRATIONS[:6])
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


def test_source_deletion_migration_preserves_marker_in_publication(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:10])
        db.execute("INSERT INTO items(id,platform,external_id) VALUES ('gone','rss','gone'),('present','rss','present')")
        db.execute("INSERT INTO source_deleted VALUES ('gone')")
        migrate(db, MIGRATIONS)
        assert db.execute('SELECT id,source_deleted FROM items_read ORDER BY id').fetchall() == [('gone', True), ('present', False)]
        assert db.execute("SELECT to_regclass('source_deleted')").fetchone() == (None,)
        names = ['reader_v_deleted_delete','reader_v_deleted_insert','reader_v_deleted_update']
        assert db.execute('SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname = ANY(%s)', (names,)).fetchone() == (0,)
        functions = ['oko_trigger_reader_v_deleted_delete','oko_trigger_reader_v_deleted_insert','oko_trigger_reader_v_deleted_update']
        assert db.execute('SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.proname = ANY(%s)', (functions,)).fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_source_deletion_migration_rejects_orphan_marker(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:10])
        db.execute("INSERT INTO source_deleted VALUES ('missing')")
        with pytest.raises(psycopg.errors.RaiseException, match='orphan source deletion'):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT item FROM source_deleted').fetchone() == ('missing',)
        verify(db, MIGRATIONS[:10])


def test_execution_settings_migration_preserves_attempt_provenance(connections):
    import json
    from oko.storage.postgres_schema import MIGRATIONS
    parameters = {'processor':'codex','model':'synthetic','reasoning_effort':'low','fast':False}
    with connections() as db:
        migrate(db, MIGRATIONS[:11])
        job = db.execute("INSERT INTO jobs(kind,item,generation) VALUES ('filter','item',3) RETURNING id").fetchone()[0]
        db.execute('INSERT INTO execution_settings VALUES (%s,%s,%s,%s)', (job, 2, 'signature', json.dumps(parameters)))
        migrate(db, MIGRATIONS)
        assert db.execute('SELECT execution_settings FROM jobs WHERE id=%s', (job,)).fetchone() == ({'generation':2,'signature':'signature','parameters':parameters},)
        assert db.execute("SELECT to_regclass('execution_settings')").fetchone() == (None,)
        verify(db, MIGRATIONS)


@pytest.mark.parametrize('invalid', ['orphan', 'generation', 'signature', 'parameters', 'invalid-json'])
def test_execution_settings_migration_rejects_invalid_records(connections, invalid):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:11])
        job = db.execute("INSERT INTO jobs(kind,item) VALUES ('filter','item') RETURNING id").fetchone()[0]
        db.execute('INSERT INTO execution_settings VALUES (%s,%s,%s,%s)',
                   (job + 1000 if invalid == 'orphan' else job,
                    None if invalid == 'generation' else 1,
                    None if invalid == 'signature' else 'signature',
                    '[]' if invalid == 'parameters' else '{' if invalid == 'invalid-json' else '{}'))
        with pytest.raises(psycopg.errors.RaiseException, match='invalid execution settings'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('execution_settings')").fetchone() == ('execution_settings',)
        verify(db, MIGRATIONS[:11])


def test_job_execution_settings_rejects_incomplete_snapshots(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS)
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            db.execute("INSERT INTO jobs(kind,item,execution_settings) VALUES ('filter','broken','{}')")


def test_processing_input_migration_moves_signatures_to_jobs(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:12])
        db.execute("INSERT INTO items(id) VALUES ('one')")
        db.execute("INSERT INTO jobs(kind,item,status) VALUES ('filter','one','pending'),('theses','one','done')")
        db.execute("INSERT INTO filter_inputs VALUES ('one','filter-signature')")
        db.execute("INSERT INTO thesis_inputs VALUES ('one','summary-signature')")
        migrate(db, MIGRATIONS)
        rows = db.execute("SELECT kind,input_snapshot FROM jobs WHERE item='one' ORDER BY kind").fetchall()
        assert rows == [('filter', {'signature':'filter-signature'}), ('theses', {'signature':'summary-signature'})]
        assert db.execute("SELECT to_regclass('filter_inputs'),to_regclass('thesis_inputs')").fetchone() == (None, None)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'reader_v_filter_input_%'").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.proname IN ('oko_trigger_reader_v_filter_input_delete','oko_trigger_reader_v_filter_input_insert','oko_trigger_reader_v_filter_input_update')").fetchone() == (0,)
        verify(db, MIGRATIONS)


@pytest.mark.parametrize('invalid', ['orphan-filter', 'orphan-theses', 'orphan-item', 'null-signature', 'empty-signature', 'bad-payload', 'bad-payload-type'])
def test_processing_input_migration_rejects_ambiguous_rows(connections, invalid):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:12])
        if invalid != 'orphan-item':
            db.execute("INSERT INTO items(id) VALUES ('one')")
        kind = 'theses' if invalid == 'orphan-theses' else 'filter'
        if invalid not in ('orphan-filter', 'orphan-theses'):
            db.execute('INSERT INTO jobs(kind,item,payload) VALUES (%s,%s,%s)', (kind, 'one', '{' if invalid == 'bad-payload' else '[]' if invalid == 'bad-payload-type' else '{}'))
        table = 'thesis_inputs' if kind == 'theses' else 'filter_inputs'
        db.execute(f'INSERT INTO {table} VALUES (%s,%s)', ('one', None if invalid == 'null-signature' else '' if invalid == 'empty-signature' else 'signature'))
        with pytest.raises(psycopg.errors.RaiseException, match='invalid processing input'):
            migrate(db, MIGRATIONS)
        assert db.execute(f'SELECT item FROM {table}').fetchone() == ('one',)
        verify(db, MIGRATIONS[:12])


def test_journal_link_migration_preserves_material_references(connections):
    import json
    from oko.storage.postgres_schema import MIGRATIONS
    body = json.dumps({'objects':[{'kind':'material','id':'item'},{'kind':'feed','id':'feed'}]})
    with connections() as db:
        migrate(db, MIGRATIONS[:13])
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('action','identity',%s,1,'failed')", (body,))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('problem','problem-identity','action',%s,1)", (body,))
        assert db.execute("SELECT count(*) FROM reader_material_links WHERE item='item'").fetchone() == (2,)
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('reader_material_links')").fetchone() == (None,)
        assert db.execute("SELECT id FROM reader_action_results WHERE (body::jsonb->'objects') @> %s::jsonb", ('[{"kind":"material","id":"item"}]',)).fetchall() == [('action',)]
        assert db.execute("SELECT id FROM reader_problems WHERE (body::jsonb->'objects') @> %s::jsonb", ('[{"kind":"material","id":"item"}]',)).fetchall() == [('problem',)]
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'journal_links_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_journal_link_migration_rejects_inconsistent_projection(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:13])
        db.execute("INSERT INTO reader_material_links VALUES ('action','missing','item')")
        with pytest.raises(psycopg.errors.RaiseException, match='inconsistent journal links'):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT kind,record,item FROM reader_material_links').fetchone() == ('action','missing','item')
        verify(db, MIGRATIONS[:13])


@pytest.mark.parametrize('invalid', ['json', 'objects', 'material-id'])
def test_journal_link_migration_rejects_unrepresentable_body(connections, invalid):
    import json
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:13])
        if invalid == 'json':
            body = '{'
            db.execute('ALTER TABLE reader_action_results DISABLE TRIGGER USER')
        elif invalid == 'objects':
            body = json.dumps({'objects': {'one': {'kind':'material', 'id':'item'}}})
        else:
            body = json.dumps({'objects': [{'kind':'material', 'id':123}]})
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('action','identity',%s,1,'failed')", (body,))
        if invalid == 'json':
            db.execute('ALTER TABLE reader_action_results ENABLE TRIGGER USER')
        with pytest.raises(psycopg.errors.RaiseException, match='invalid journal'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT body FROM reader_action_results WHERE id='action'").fetchone() == (body,)
        verify(db, MIGRATIONS[:13])


def test_receipt_cleanup_migration_preserves_records_and_removes_projection(connections):
    import json
    from oko.storage.postgres_schema import MIGRATIONS
    body = json.dumps({'objects':[]})
    with connections() as db:
        migrate(db, MIGRATIONS[:14])
        for identity, completed in [('old',1),('new',2)]:
            db.execute("INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (%s,'same',%s,0,%s,'succeeded')", (identity,body,completed))
        assert db.execute('SELECT count(*) FROM reader_action_superseded').fetchone() == (1,)
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT id FROM reader_action_results ORDER BY sequence").fetchall() == [('old',),('new',)]
        assert db.execute("SELECT to_regclass('reader_action_superseded')").fetchone() == (None,)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'journal_receipt_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_receipt_cleanup_migration_rejects_inconsistent_projection(connections):
    import json
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:14])
        body = json.dumps({'objects':[]})
        for identity in ('old','new'):
            db.execute("INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (%s,'same',%s,0,1,'succeeded')", (identity,body))
        db.execute('DELETE FROM reader_action_superseded')
        with pytest.raises(psycopg.errors.RaiseException, match='inconsistent superseded receipts'):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT count(*) FROM reader_action_results').fetchone() == (2,)
        verify(db, MIGRATIONS[:14])


def test_time_projection_migration_preserves_jobs_and_youtube_deadlines(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:15])
        db.execute("INSERT INTO jobs(kind,item,status,next) VALUES ('fetch','item','pending',123)")
        db.execute("UPDATE youtube_access SET blocked_until=456 WHERE id=1")
        db.execute("INSERT INTO reader_runtime VALUES (1,'{}')")
        db.execute("INSERT INTO reader_youtube_time VALUES (1,1)")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT next FROM jobs WHERE kind='fetch' AND item='item'").fetchone() == (123,)
        assert db.execute('SELECT blocked_until FROM youtube_access WHERE id=1').fetchone() == (456,)
        assert db.execute("SELECT to_regclass('reader_job_time'),to_regclass('reader_youtube_time'),to_regclass('reader_runtime')").fetchone() == (None,None,None)
        names = ['reader_v_job_time_insert','reader_v_job_time_update','reader_v_job_time_delete','reader_v_time_ready','reader_v_youtube_time','reader_v_runtime','reader_v_runtime_first']
        assert db.execute('SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname=ANY(%s)', (names,)).fetchone() == (0,)
        functions = ['oko_trigger_' + name for name in names]
        assert db.execute('SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.proname=ANY(%s)', (functions,)).fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_telegram_sync_migration_preserves_pending_feeds(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:16])
        db.execute("INSERT INTO feeds(id,kind,transport,connection) VALUES ('ready','telegram','telegram','ready'),('new','telegram','telegram','ready'),('rss','rss','rss','ready')")
        db.execute("INSERT INTO telegram_read_state(feed,since,initial,confirmed) VALUES ('ready',100,5,7)")
        db.execute("INSERT INTO telegram_read_dirty(feed) VALUES ('new'),('rss') ON CONFLICT DO NOTHING")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT since,initial,confirmed,needs_sync FROM telegram_read_state WHERE feed='ready'").fetchone() == (100,5,7,True)
        assert db.execute("SELECT since,needs_sync FROM telegram_read_state WHERE feed='new'").fetchone() == (None,True)
        assert db.execute("SELECT to_regclass('telegram_read_dirty')").fetchone() == (None,)
        names = ['telegram_read_initial_added','telegram_read_initial_changed','telegram_read_item_added','telegram_read_item_changed','telegram_read_member_insert','telegram_read_member_update']
        assert db.execute('SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname=ANY(%s)', (names,)).fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_telegram_read_reasons_migrate_without_losing_facts(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:17])
        db.execute("INSERT INTO telegram_read_members(feed,member,eligible,reason,missing) VALUES ('feed',1,1,'saved',0),('feed',2,1,'viewed',0),('feed',3,0,NULL,1)")
        migrate(db, MIGRATIONS)
        assert db.execute('SELECT member,saved,viewed,missing FROM telegram_read_members ORDER BY member').fetchall() == [(1,True,False,0),(2,False,True,0),(3,False,False,1)]
        verify(db, MIGRATIONS)


@pytest.mark.parametrize(('eligible','reason'), [(1,'unexpected'), (2,'saved'), (None,None)])
def test_telegram_read_reasons_reject_unknown_eligible_reason(connections, eligible, reason):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:17])
        db.execute("INSERT INTO telegram_read_members(feed,member,eligible,reason) VALUES ('feed',1,%s,%s)", (eligible,reason))
        with pytest.raises(psycopg.errors.RaiseException, match='invalid Telegram read eligibility reason'):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT eligible,reason FROM telegram_read_members').fetchone() == (eligible,reason)
        verify(db, MIGRATIONS[:17])


def test_telegram_saved_backfill_is_scheduled_for_legacy_client_feeds(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:18])
        db.execute("INSERT INTO feeds(id,kind,transport,connection) VALUES ('client','telegram','telegram','connected'),('web','telegram',NULL,'connected')")
        db.execute("INSERT INTO telegram_bindings VALUES ('client','{\"kind\":\"client\",\"role\":\"personal\"}'),('web','{\"kind\":\"web\"}')")
        db.execute("INSERT INTO telegram_read_state(feed,since) VALUES ('client',123)")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT since,saved_backfill_after FROM telegram_read_state WHERE feed='client'").fetchone() == (123,'')
        assert db.execute("SELECT count(*) FROM telegram_read_state WHERE feed='web'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_unused_reader_lists_and_originals_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:19])
        db.execute("INSERT INTO feeds(id) VALUES ('feed')")
        db.execute("INSERT INTO settings(scope,owner,field,value) VALUES ('feed','feed','originals','false')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('reader_lists'),to_regclass('reader_originals')").fetchone() == (None,None)
        assert db.execute("SELECT value FROM settings WHERE scope='feed' AND owner='feed' AND field='originals'").fetchone() == ('false',)
        verify(db, MIGRATIONS)


def test_reader_counts_are_derived_from_memberships(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:20])
        db.execute("INSERT INTO folders(id,title) VALUES ('folder','Synthetic')")
        db.execute("INSERT INTO feeds(id,folder_id) VALUES ('feed','folder')")
        db.execute("INSERT INTO items(id) VALUES ('item')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id) VALUES ('feed','item')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('reader_feed_counts'),to_regclass('reader_folder_counts'),to_regclass('reader_membership_dirty')").fetchone() == (None,None,None)
        assert db.execute("SELECT feed_id,publication_id FROM feed_publications").fetchall() == [('feed','item')]
        verify(db, MIGRATIONS)


def test_reader_visibility_moves_to_publication_status(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:21])
        db.execute("INSERT INTO items(id,status) VALUES ('hidden','pending_filtering'),('visible','filter_out')")
        db.execute("UPDATE reader_visibility SET hidden=1 WHERE item='hidden'")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT to_regclass('reader_visibility')").fetchone() == (None,)
        assert db.execute("SELECT id,status FROM items ORDER BY id").fetchall() == [('hidden','filter_out'),('visible','pending_filtering')]
        verify(db, MIGRATIONS)


def test_reader_visibility_migration_rejects_invalid_value(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:21])
        db.execute("INSERT INTO items(id) VALUES ('item')")
        db.execute("UPDATE reader_visibility SET hidden=2 WHERE item='item'")
        with pytest.raises(psycopg.errors.RaiseException, match='invalid reader visibility'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT hidden FROM reader_visibility WHERE item='item'").fetchone() == (2,)
        verify(db, MIGRATIONS[:21])


def test_filter_job_change_trigger_is_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:22])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname='processing_filter_changed'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_publication_processing_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:23])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname IN ('processing_item_added','processing_item_changed')").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_membership_effect_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:24])
        db.execute("INSERT INTO feeds(id) VALUES ('feed')")
        db.execute("INSERT INTO items(id) VALUES ('item')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id) VALUES ('feed','item')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname IN ('membership_owner_changed','reader_v_shared_insert','reader_v_shared_delete')").fetchone() == (0,)
        assert db.execute("SELECT feed_id,publication_id FROM feed_publications").fetchall() == [('feed','item')]
        verify(db, MIGRATIONS)


def test_unused_job_resource_version_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:25])
        migrate(db, MIGRATIONS)
        assert db.execute("""SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname='jobs'
            AND (t.tgname LIKE 'reader_v_job_filter_%' OR t.tgname LIKE 'reader_v_job_theses_%'
                 OR t.tgname LIKE 'reader_v_job_history_%' OR t.tgname LIKE 'reader_v_filter_eligibility_%')""").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname IN ('reader_v_feed_material','reader_v_item_inputs','reader_v_item_media_available')").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_card_job_version_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:26])
        migrate(db, MIGRATIONS)
        assert db.execute("""SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname='jobs'
            AND (t.tgname LIKE 'reader_v_job_card_%' OR t.tgname LIKE 'reader_v_job_fetch_%')""").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_settings_source_revision_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:27])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'source_configuration_settings_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_feed_source_revision_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:28])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'source_configuration_feeds_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_telegram_source_revision_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:29])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND (t.tgname LIKE 'source_configuration_telegram_bindings_%' OR t.tgname LIKE 'source_configuration_telegram_profiles_%')").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_history_source_revision_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:30])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND (t.tgname LIKE 'source_configuration_initial_history_%' OR t.tgname='source_read_initial')").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_settings_status_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:31])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'reader_v_settings_status_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_telegram_binding_and_profile_status_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:32])
        migrate(db, MIGRATIONS)
        names = ['reader_v_telegram_bindings_delete','reader_v_telegram_bindings_insert','reader_v_telegram_bindings_update','reader_v_profile','reader_v_profile_delete','reader_v_profile_insert']
        assert db.execute('SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname=ANY(%s)', (names,)).fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_history_status_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:33])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'reader_v_initial_history_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_history_member_status_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:34])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'reader_v_history_members_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_telegram_read_status_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:35])
        migrate(db, MIGRATIONS)
        assert db.execute("""SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema()
            AND (t.tgname LIKE 'reader_v_telegram_read_members_status_%' OR t.tgname LIKE 'reader_v_telegram_read_state_status_%')""").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_folder_reader_version_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:36])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'reader_v_folder_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_feed_reader_version_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:37])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname IN ('reader_v_feed_delete','reader_v_feed_insert','reader_v_feed_update')").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_youtube_status_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:38])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname LIKE 'reader_v_youtube_access_status_%'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_telegram_profile_message_trigger_is_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:39])
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname='reader_v_profile_message'").fetchone() == (0,)
        verify(db, MIGRATIONS)


def test_runtime_state_status_triggers_are_removed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:40])
        migrate(db, MIGRATIONS)
        names = ['reader_v_state_status_delete','reader_v_state_status_insert','reader_v_state_update']
        assert db.execute('SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND t.tgname=ANY(%s)', (names,)).fetchone() == (0,)
        verify(db, MIGRATIONS)
