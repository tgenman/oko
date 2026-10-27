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
