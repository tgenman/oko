"""Release migration failures and concurrency on an actual, isolated PostgreSQL."""
from contextlib import contextmanager
import json

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
    first = Migration(128, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
    broken = Migration(129, 'broken', 'CREATE TABLE partial(id text); SELECT missing_column FROM library')
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
    first = Migration(128, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
    second = Migration(129, 'title', 'ALTER TABLE library ADD COLUMN title text')
    with connections() as db:
        migrate(db, [first])
        with pytest.raises(SchemaMismatch, match='needs migration'):
            verify(db, [first, second])
        with pytest.raises(SchemaMismatch, match='history'):
            migrate(db, [Migration(128, 'library', first.sql + '; SELECT 1')])
        migrate(db, [first, second])
        with pytest.raises(SchemaMismatch, match='newer'):
            verify(db, [first])
        db.execute('DELETE FROM oko_schema_migrations WHERE version=128')
        with pytest.raises(SchemaMismatch, match='history'):
            migrate(db, [first, second])


def test_second_migration_cannot_enter_locked_release(connections):
    from oko.storage.postgres_migrations import schema_lock_key
    first = Migration(128, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
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
    first = Migration(128, 'library', 'CREATE TABLE library(id text PRIMARY KEY)')
    with connections() as db:
        with pytest.raises(ValueError, match='consecutive'):
            migrate(db, [Migration(129, 'gap', 'SELECT 1')])
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
            db.execute("UPDATE publications SET feed_id='first' WHERE id='article'")
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
        assert restarted.execute("SELECT feed_id FROM publications WHERE id='article'").fetchone()[0] == 'first'


def test_job_execution_settings_rejects_incomplete_snapshots(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS)
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            db.execute("INSERT INTO jobs(kind,item,execution_settings) VALUES ('filter','broken','{}')")






def test_current_schema_has_no_legacy_publication_views(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS)
        names=('items_read','items_by_feed','unviewed_items_by_feed','reading_memberships')
        assert db.execute('SELECT viewname FROM pg_views WHERE schemaname=current_schema() AND viewname=ANY(%s)', (list(names),)).fetchall() == []
        assert db.execute('SELECT viewname FROM pg_views WHERE schemaname=current_schema()').fetchall() == []
        assert db.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal").fetchone()[0] == 0






















def test_content_inventory_reports_counts_without_publication_text(connections):
    from pathlib import Path
    audit=(Path(__file__).parents[2]/'scripts/audit-publication-content.sql').read_text()
    quote=dict(text='Sensitive fixture',author='Fixture',source_url=None)
    with connections() as db:
        db.execute('CREATE TABLE publications (platform text, content text, viewed integer, title text)')
        values=[('telegram','{broken',0),
                ('telegram',json.dumps({'blocks':[], 'extra':{'_reader':{'reply_preview':quote}}}),0),
                ('telegram',json.dumps({'blocks':[dict(type='quote', **dict(quote,text='Other'))], 'extra':{'_reader':{'reply_preview':quote}}}),0),
                ('telegram','{}',1),
                ('telegram',json.dumps({'blocks':{}, 'extra':{'_reader':{'reply_preview':quote}}}),0),
                ('telegram','null',0)]
        for row in values:
            db.execute('INSERT INTO publications (platform,content,viewed) VALUES (%s,%s,%s)',row)
        db.execute('INSERT INTO publications VALUES (%s,%s,%s,%s)',
                   ('rss', json.dumps({'blocks':[], 'extra':{'_reader':{'title':'Display'}}}),0,'Stored'))
        result=db.execute(audit).fetchone()
        assert result[:8] == (7,1,1,2,1,1,1,0)
        assert all(not isinstance(value,str) for value in result)






































































































































































def test_old_incomplete_ledger_is_not_adopted(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        db.execute("CREATE TABLE oko_schema_migrations (version integer PRIMARY KEY,name text NOT NULL,checksum text NOT NULL,applied_at timestamptz DEFAULT clock_timestamp())")
        db.execute("INSERT INTO oko_schema_migrations(version,name,checksum) VALUES (126,'old','old')")
        with pytest.raises(SchemaMismatch):
            migrate(db, MIGRATIONS)
        assert db.execute('SELECT version FROM oko_schema_migrations').fetchall() == [(126,)]
