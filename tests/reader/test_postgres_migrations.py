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
            db.execute("INSERT INTO publications(id,platform,external_id,published) VALUES ('article','rss','https://source.test/article',1792870000.123456)")
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
        assert restarted.execute("SELECT published FROM publications WHERE id='article'").fetchone()[0] == 1792870000.123456
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
        db.execute("INSERT INTO publications(id,status) VALUES ('one','filter_out')")
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


def test_removing_telegram_accounts_preserves_web_feed_and_clears_client_state(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    assert MIGRATIONS[-1].name == 'remove-telegram-client'
    with connections() as db:
        migrate(db, MIGRATIONS[:-1])
        db.execute("INSERT INTO feeds(id,kind,identity,title) VALUES ('web','telegram','web-fixture','Web')")
        db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('web',%s::jsonb)",
                   (json.dumps(dict(kind='web',username='fixture',preview_url='https://t.me/s/fixture')),))
        db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('post','telegram','post-1','https://t.me/fixture/1')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id) VALUES ('web','post')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('photo','post','image','https://cdn.example/photo','photo','pending_download')")
        db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state) VALUES ('personal','fixture',123,1,'ready')")
        db.execute("INSERT INTO telegram_read_state(feed,needs_sync) VALUES ('web',true)")
        db.execute("INSERT INTO telegram_read_members(feed,member) VALUES ('web',42)")
        db.execute("INSERT INTO state(key,value) VALUES ('telegram-retry:123','9')")
        db.execute("INSERT INTO jobs(kind,item) VALUES ('read','web')")
        for identity, operation, code in [
            ('profile:personal','producer:status','profile_unavailable'),
            ('job:read:web:read','producer:read','work-read'),
            ('job:profile:web:fetch','producer:fetch','profile_unavailable'),
            ('action:profile',json.dumps({'context':{'operation':'telegram_profile_save','role':'personal'},'error':None}),'action-failed'),
            ('job:other','producer:fetch','source-unavailable'),
            ('action:other',json.dumps({'context':{'operation':'telegram_connect'},'error':None}),'action-failed'),
        ]:
            body=json.dumps(dict(code=code,operation=operation,objects=[]))
            db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)", (identity,identity,body))
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT binding->>'kind' FROM telegram_bindings WHERE feed='web'").fetchone() == ('web',)
        assert db.execute("SELECT id FROM feeds WHERE id='web'").fetchone() == ('web',)
        assert db.execute("SELECT publication_id FROM feed_publications WHERE feed_id='web'").fetchone() == ('post',)
        assert db.execute("SELECT publication_id FROM media_files WHERE id='photo'").fetchone() == ('post',)
        assert db.execute('SELECT count(*) FROM telegram_profiles').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM telegram_read_state').fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM telegram_read_members').fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM state WHERE key LIKE 'telegram-retry:%'").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='read'").fetchone() == (0,)
        assert db.execute("SELECT identity,outcome FROM reader_problems WHERE finished IS NOT NULL ORDER BY identity").fetchall() == [('action:profile','irrelevant'),('job:profile:web:fetch','irrelevant'),('job:read:web:read','irrelevant'),('profile:personal','irrelevant')]
        assert db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL ORDER BY identity").fetchall() == [('action:other',),('job:other',)]


@pytest.mark.parametrize('kind', ['client', 'raw', 'adapter', 'missing', 'web_missing', 'web_null', 'web_extra', 'web_bad_url', 'web_wrong_channel'])
def test_telegram_account_removal_refuses_unsupported_feeds_atomically(connections, kind):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:-1])
        db.execute("INSERT INTO feeds(id,kind,identity,title) VALUES ('feed','telegram','fixture','Fixture')")
        if kind == 'client':
            db.execute("INSERT INTO telegram_bindings(feed,binding,indexed_role) VALUES ('feed',%s::jsonb,'personal')",
                       (json.dumps(dict(kind='client',role='personal',account_user_id='123',peer_id='456')),))
        elif kind == 'raw':
            db.execute("INSERT INTO telegram_bindings(feed,binding_raw) VALUES ('feed','unreadable')")
        elif kind == 'adapter':
            db.execute("UPDATE feeds SET adapter='telegram_client_adapter' WHERE id='feed'")
        elif kind.startswith('web_'):
            binding = {'kind':'web','username':'fixture','preview_url':'https://t.me/s/fixture'}
            if kind == 'web_missing': binding.pop('username')
            elif kind == 'web_null': binding['username'] = None
            elif kind == 'web_extra': binding['unexpected'] = True
            elif kind == 'web_bad_url': binding['preview_url'] = 'https://%'
            elif kind == 'web_wrong_channel': binding['preview_url'] = 'https://t.me/s/different'
            db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('feed',%s::jsonb)", (json.dumps(binding),))
        db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state) VALUES ('personal','fixture',123,1,'ready')")
        with pytest.raises(psycopg.errors.RaiseException, match='Unsupported or unreadable Telegram binding'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT account FROM telegram_profiles WHERE role='personal'").fetchone() == ('fixture',)
        assert db.execute("SELECT id FROM feeds WHERE id='feed'").fetchone() == ('feed',)
        verify(db, MIGRATIONS[:-1])


def test_telegram_removal_never_falls_through_to_neighbor_schema(postgres_database_url):
    from psycopg import sql
    from uuid import uuid4
    from oko.storage.postgres_schema import MIGRATIONS
    neighbor = 'neighbor_' + uuid4().hex
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:-1])
        owner = db.execute('SELECT current_schema()').fetchone()[0]
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(neighbor)))
        try:
            db.execute(sql.SQL('CREATE TABLE {}.telegram_profiles (role text,account text)').format(sql.Identifier(neighbor)))
            db.execute(sql.SQL("INSERT INTO {}.telegram_profiles VALUES ('personal','preserve')").format(sql.Identifier(neighbor)))
            db.execute('ALTER TABLE telegram_profiles RENAME TO deliberately_missing_profiles')
            db.execute(sql.SQL('SET search_path TO {}, {}').format(sql.Identifier(owner),sql.Identifier(neighbor)))
            with pytest.raises(psycopg.errors.RaiseException, match='current schema'):
                migrate(db, MIGRATIONS)
            assert db.execute(sql.SQL('SELECT account FROM {}.telegram_profiles').format(sql.Identifier(neighbor))).fetchone() == ('preserve',)
            db.execute('ALTER TABLE deliberately_missing_profiles RENAME TO telegram_profiles')
            verify(db, MIGRATIONS[:-1])
        finally:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(neighbor)))


def test_telegram_removal_locks_bindings_before_preflight(postgres_database_url):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from oko.storage.postgres_schema import MIGRATIONS
    release = MIGRATIONS[-1]
    marker = "    -- Refuse unknown, malformed or unsupported identities before removing state."
    assert marker in release.sql
    delayed = Migration(release.version, release.name, release.sql.replace(marker, '    PERFORM pg_sleep(2);\n' + marker))
    with psycopg.connect(postgres_database_url, autocommit=True) as db, psycopg.connect(postgres_database_url, autocommit=True) as external:
        migrate(db, MIGRATIONS[:-1])
        owner = external.execute('SELECT current_schema()').fetchone()[0]
        external.execute("SET lock_timeout='150ms'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(migrate, db, (*MIGRATIONS[:-1], delayed))
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                locked = external.execute("""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks l
                    JOIN pg_catalog.pg_class c ON c.oid=l.relation
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=%s AND c.relname='telegram_bindings'
                      AND l.mode='AccessExclusiveLock' AND l.granted)""", (owner,)).fetchone()[0]
                if locked:
                    break
                time.sleep(.01)
            assert locked, 'Migration did not acquire the binding lock'
            with pytest.raises(psycopg.errors.LockNotAvailable):
                external.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('late',%s::jsonb)",
                                 (json.dumps(dict(kind='client',role='personal')),))
            pending.result(timeout=8)
        verify(db, (*MIGRATIONS[:-1], delayed))
        assert external.execute("SELECT 1 FROM telegram_bindings WHERE feed='late'").fetchone() is None


def test_telegram_removal_keeps_historical_account_problems_out_of_current_status(tmp_path, postgres_database_url):
    from fastapi.testclient import TestClient
    from oko.storage.postgres_schema import MIGRATIONS
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:-1])
        db.execute("INSERT INTO feeds(id,kind,identity,title,connection) VALUES ('web','telegram','web','Web','connected')")
        db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('web',%s::jsonb)",
                   (json.dumps(dict(kind='web',username='fixture',preview_url='https://t.me/s/fixture')),))
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','web','failed','Old profile unavailable','profile_unavailable'),('fetch','unrelated','failed','Other failure','source-network')")
        context = {'operation':'telegram_profile_save','role':'personal'}
        action_identity = json.dumps(context)
        account_context = json.dumps({'context':context,'error':None})
        for identity, code, operation in [
            ('profile:personal','profile_unavailable','producer:status'),
            ('job:profile:old:fetch','profile_unavailable','producer:fetch'),
            ('action:'+action_identity,'action-failed',account_context),
        ]:
            body = dict(code=code, operation=operation, objects=[], section='action', title='Old account problem',
                        consequence='Old state', next_step='Check account', state='current', next_at=None, action=None)
            if identity.startswith('action:'): body['request_id'] = 'old-profile-receipt'
            db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",
                       (identity,identity,json.dumps(body)))
        receipt = dict(title='Old account problem',action=None,objects=[],command_context=context)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('old-profile-receipt',%s,%s,1,'failed')",
                   (action_identity,json.dumps(receipt)))
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT status,error,problem_code FROM jobs WHERE kind='discover' AND item='web'").fetchone() == ('pending',None,None)
        assert db.execute("SELECT status,error,problem_code FROM jobs WHERE kind='fetch' AND item='unrelated'").fetchone() == ('failed','Other failure','source-network')
    with TestClient(create_app(tmp_path, token='test', database_url=postgres_database_url, api_only=True), headers=HEADERS) as client:
        snapshot = client.get('/api/v3/status/details')
        assert snapshot.status_code == 200, snapshot.text
        problems = snapshot.json()['problems']
        assert all('profile' not in problem['id'] for problem in problems['current'])
        assert {problem['id'] for problem in problems['recent']} >= {'profile:personal','job:profile:old:fetch','action:'+action_identity}
