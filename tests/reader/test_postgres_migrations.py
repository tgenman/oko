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
            db.execute("INSERT INTO publications(id,platform,external_id,published_at) VALUES ('article','rss','https://source.test/article',1792870000.123456)")
            db.execute("INSERT INTO feed_publications VALUES ('first','article'),('second','article')")
            db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,size_bytes) VALUES ('media','article','image','https://media.test/image','image','pending',5000000000)")
            first_job = db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch','article') RETURNING id").fetchone()[0]
        with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
            db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('duplicate','rss','https://source.test/article')")
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
            db.execute("DELETE FROM publications WHERE id='article'")
    with connections() as restarted:
        second_job = restarted.execute("INSERT INTO jobs(kind,item) VALUES ('filter','article') RETURNING id").fetchone()[0]
        assert second_job > first_job
        assert restarted.execute("SELECT published_at FROM publications WHERE id='article'").fetchone()[0] == 1792870000.123456
        assert restarted.execute("SELECT size_bytes FROM media_files WHERE id='media'").fetchone()[0] == 5000000000
        assert restarted.execute("SELECT count(*) FROM feed_publications WHERE publication_id='article'").fetchone()[0] == 2


def test_job_execution_settings_rejects_incomplete_snapshots(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS)
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            db.execute("INSERT INTO jobs(kind,item,execution_settings) VALUES ('filter','broken','{}')")


def test_publication_and_job_status_constraints_preserve_valid_rows(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:56])
        db.execute("INSERT INTO publications(id,status,platform,external_id,published_known,discovered_at) VALUES ('one','filter_out','rss','one',0,1)")
        db.execute("INSERT INTO jobs(kind,item,status) VALUES ('filter','one','blocked')")
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT status FROM publications WHERE id='one'").fetchone() == ('filter_out',)
        assert db.execute("SELECT status FROM jobs WHERE item='one'").fetchone() == ('blocked',)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE publications SET status='invented' WHERE id='one'")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE jobs SET status='invented' WHERE item='one'")
        verify(db,MIGRATIONS)


def test_job_operation_and_counters_are_constrained(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:57])
        db.execute("INSERT INTO jobs(kind,item,generation,attempt,retry_delay) VALUES ('filter','item',3,2,5)")
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT kind,generation,attempt,retry_delay FROM jobs WHERE item='item'").fetchone() == ('filter',3,2,5.0)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO jobs(kind,item) VALUES ('invented','other')")
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE jobs SET generation=-1 WHERE item='item'")
        verify(db,MIGRATIONS)


def test_current_schema_has_no_legacy_publication_views(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS)
        names=('items_read','items_by_feed','unviewed_items_by_feed','reading_memberships')
        assert db.execute('SELECT viewname FROM pg_views WHERE schemaname=current_schema() AND viewname=ANY(%s)', (list(names),)).fetchall() == []
        assert db.execute('SELECT viewname FROM pg_views WHERE schemaname=current_schema()').fetchall() == []
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal").fetchone()[0] == 0


def test_publication_identity_date_migration_preserves_facts_and_rejects_conflicts(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:95])
        db.execute("""INSERT INTO publications(id, source_id, platform, external_id, published, published_known, published_at, discovered_at)
                   VALUES ('known','feed:1','rss','external-1',100,1,100,10),
                          ('unknown','linked:unknown','other_web','external-2',0,0,NULL,11)""")
        db.execute("UPDATE publications SET published_at=101 WHERE id='known'")
        with pytest.raises(psycopg.errors.RaiseException, match='conflicting publication dates'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT source_id,published,published_at FROM publications WHERE id='known'").fetchone() == ('feed:1',100,101)
        db.execute("UPDATE publications SET published_at=100 WHERE id='known'")
        migrate(db, MIGRATIONS)
        assert db.execute('SELECT id,platform,external_id,published_at,discovered_at FROM publications ORDER BY id').fetchall() == [
            ('known','rss','external-1',100,10), ('unknown','other_web','external-2',None,11)]
        columns = {row[0] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications'").fetchall()}
        assert not {'source_id','published','published_known'} & columns


@pytest.mark.parametrize(('flag', 'stamp', 'error'), [(None, 1, 'invalid publication date flag'), (2, 1, 'invalid publication date flag'), (1, float('inf'), 'invalid timestamp')])
def test_publication_identity_date_migration_rejects_invalid_facts(connections, flag, stamp, error):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:95])
        db.execute("INSERT INTO publications(id,source_id,platform,external_id,published,published_known,discovered_at) VALUES ('p','feed:p','rss','p',%s,%s,1)", (stamp, flag))
        with pytest.raises(psycopg.errors.RaiseException, match=error):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:95])
        assert db.execute("SELECT source_id FROM publications WHERE id='p'").fetchone() == ('feed:p',)


@pytest.mark.parametrize(('platform', 'external_id', 'discovered', 'error'), [
    ('unknown-platform', 'p', 1, 'invalid external identity'),
    ('rss', '', 1, 'invalid external identity'),
    ('rss', 'p', None, 'missing external identity or discovery time'),
])
def test_publication_identity_date_migration_rejects_unusable_identity(connections, platform, external_id, discovered, error):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:95])
        db.execute("INSERT INTO publications(id,source_id,platform,external_id,published_known,discovered_at) VALUES ('p','feed:p',%s,%s,0,%s)", (platform, external_id, discovered))
        with pytest.raises(psycopg.errors.RaiseException, match=error):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:95])
        assert db.execute("SELECT source_id FROM publications WHERE id='p'").fetchone() == ('feed:p',)


def test_publication_identity_date_migration_stays_in_target_schema(postgres_database_url):
    from psycopg import sql
    from uuid import uuid4
    from oko.storage.postgres_schema import MIGRATIONS
    neighbor = 'neighbor_' + uuid4().hex
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:95])
        owner = db.execute('SELECT current_schema()').fetchone()[0]
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(neighbor)))
        try:
            db.execute(sql.SQL('CREATE TABLE {}.publications (id text, source_id text)').format(sql.Identifier(neighbor)))
            db.execute(sql.SQL("INSERT INTO {}.publications VALUES ('p','preserve')").format(sql.Identifier(neighbor)))
            db.execute('ALTER TABLE publications RENAME TO deliberately_missing_publications')
            db.execute(sql.SQL('SET search_path TO {}, {}').format(sql.Identifier(owner),sql.Identifier(neighbor)))
            with pytest.raises(psycopg.errors.RaiseException, match='current schema'):
                migrate(db, MIGRATIONS)
            assert db.execute(sql.SQL('SELECT source_id FROM {}.publications').format(sql.Identifier(neighbor))).fetchone() == ('preserve',)
            db.execute('ALTER TABLE deliberately_missing_publications RENAME TO publications')
            verify(db, MIGRATIONS[:95])
        finally:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(neighbor)))


def test_publication_identity_date_migration_locks_before_validation(postgres_database_url):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from oko.storage.postgres_schema import MIGRATIONS
    release = MIGRATIONS[95]
    marker = '    IF EXISTS (SELECT 1 FROM publications WHERE platform IS NULL'
    delayed = Migration(release.version, release.name, release.sql.replace(marker, '    PERFORM pg_sleep(2);\n' + marker))
    assert delayed.sql != release.sql
    with psycopg.connect(postgres_database_url, autocommit=True) as db, psycopg.connect(postgres_database_url, autocommit=True) as external:
        migrate(db, MIGRATIONS[:95])
        external.execute("SET lock_timeout='150ms'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(migrate, db, (*MIGRATIONS[:95], delayed))
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                if external.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE relation=to_regclass('publications') AND mode='AccessExclusiveLock' AND granted)").fetchone()[0]:
                    break
                time.sleep(.01)
            else:
                pytest.fail('Migration did not lock publications')
            with pytest.raises(psycopg.errors.LockNotAvailable):
                external.execute("INSERT INTO publications(id,platform,external_id) VALUES ('late','rss','late')")
            pending.result(timeout=8)
        verify(db, (*MIGRATIONS[:95], delayed))
