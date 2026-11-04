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
        migrate(db, MIGRATIONS[:97])
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
        migrate(db, MIGRATIONS[:97])
        db.execute("INSERT INTO publications(id,source_id,platform,external_id,published,published_known,discovered_at) VALUES ('p','feed:p','rss','p',%s,%s,1)", (stamp, flag))
        with pytest.raises(psycopg.errors.RaiseException, match=error):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:97])
        assert db.execute("SELECT source_id FROM publications WHERE id='p'").fetchone() == ('feed:p',)


@pytest.mark.parametrize(('platform', 'external_id', 'discovered', 'error'), [
    ('unknown-platform', 'p', 1, 'invalid external identity'),
    ('rss', '', 1, 'invalid external identity'),
    ('rss', 'p', None, 'missing external identity or discovery time'),
])
def test_publication_identity_date_migration_rejects_unusable_identity(connections, platform, external_id, discovered, error):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:97])
        db.execute("INSERT INTO publications(id,source_id,platform,external_id,published_known,discovered_at) VALUES ('p','feed:p',%s,%s,0,%s)", (platform, external_id, discovered))
        with pytest.raises(psycopg.errors.RaiseException, match=error):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:97])
        assert db.execute("SELECT source_id FROM publications WHERE id='p'").fetchone() == ('feed:p',)


def test_publication_identity_date_migration_stays_in_target_schema(postgres_database_url):
    from psycopg import sql
    from uuid import uuid4
    from oko.storage.postgres_schema import MIGRATIONS
    neighbor = 'neighbor_' + uuid4().hex
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:97])
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
            verify(db, MIGRATIONS[:97])
        finally:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(neighbor)))


def test_publication_identity_date_migration_locks_before_validation(postgres_database_url):
    from concurrent.futures import ThreadPoolExecutor
    import time
    from oko.storage.postgres_schema import MIGRATIONS
    release = MIGRATIONS[97]
    marker = '    IF EXISTS (SELECT 1 FROM publications WHERE platform IS NULL'
    delayed = Migration(release.version, release.name, release.sql.replace(marker, '    PERFORM pg_sleep(2);\n' + marker))
    assert delayed.sql != release.sql
    with psycopg.connect(postgres_database_url, autocommit=True) as db, psycopg.connect(postgres_database_url, autocommit=True) as external:
        migrate(db, MIGRATIONS[:97])
        external.execute("SET lock_timeout='150ms'")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(migrate, db, (*MIGRATIONS[:97], delayed))
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
        verify(db, (*MIGRATIONS[:97], delayed))


def test_duplicate_reply_migration_preserves_old_and_conflicting_quotes(connections):
    from pathlib import Path
    from oko.storage.processing_input import blocks, digest

    sql = (Path(__file__).parents[2] / 'oko/storage/postgres/096-normalize-publication-content.sql').read_text()
    quote = dict(text='Old quote', author='Author', source_url='https://example.test/post')
    def document(blocks, reply):
        return json.dumps(dict(blocks=blocks, extra={'_reader': {'reply_preview': reply}}))
    with connections() as db:
        db.execute('CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, platform text, title text)')
        rows = [
            ('same', document([dict(type='quote', **quote)], quote)),
            ('legacy', document([], quote)),
            ('different', document([dict(type='quote', **dict(quote, text='New quote'))], quote)),
            ('later_match', document([dict(type='quote', **dict(quote, text='New quote')),
                                      dict(type='quote', **quote)], quote)),
        ]
        for identity, content in rows:
            db.execute('INSERT INTO publications (id, content) VALUES (%s, %s)', (identity, content))
        db.execute(sql)
        saved = {identity: json.loads(content) for identity, content in db.execute('SELECT id, content FROM publications')}
        assert 'reply_preview' not in saved['same']['extra']['_reader']
        assert saved['same']['blocks'] == [dict(type='quote', **quote)]
        assert saved['legacy']['blocks'] == [dict(type='quote', **quote)]
        assert saved['different']['blocks'] == [dict(type='quote', **quote)]
        assert saved['later_match']['blocks'] == [dict(type='quote', **quote), dict(type='quote', **quote)]
        assert all('reply_preview' not in saved[key]['extra']['_reader'] for key in ('legacy', 'different', 'later_match'))
        assert digest(blocks(saved['same'])) == digest(blocks(json.loads(rows[0][1])))
        assert digest(blocks(saved['legacy'])) != digest(blocks(json.loads(rows[1][1])))
        assert digest(blocks(saved['different'])) != digest(blocks(json.loads(rows[2][1])))
        db.execute(sql)
        assert {identity: json.loads(content) for identity, content in db.execute('SELECT id, content FROM publications')} == saved


def test_youtube_description_migration_preserves_existing_blocks(connections):
    from pathlib import Path

    sql = (Path(__file__).parents[2] / 'oko/storage/postgres/096-normalize-publication-content.sql').read_text()
    with connections() as db:
        db.execute('CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, platform text, title text)')
        old = {'blocks': [{'type': 'external_media', 'kind': 'youtube'}],
               'extra': {'_reader': {'description': 'Saved description'}, 'youtube': {'captions': 'available'}}}
        db.execute('INSERT INTO publications (id,content,platform) VALUES (%s, %s, %s)', ('youtube', json.dumps(old), 'youtube'))
        db.execute(sql)
        new = json.loads(db.execute('SELECT content FROM publications').fetchone()[0])
        assert new['blocks'][0] == old['blocks'][0]
        assert new['blocks'][1] == {'type': 'text', 'role': 'description', 'text': 'Saved description'}
        assert 'description' not in new['extra']['_reader']
        assert new['extra']['youtube'] == old['extra']['youtube']
        db.execute(sql)
        assert json.loads(db.execute('SELECT content FROM publications').fetchone()[0]) == new


@pytest.mark.parametrize(('source', 'reader', 'removed'), [
    ('available', 'available', True), ('pending', 'error', True),
    ('unavailable', 'available', False), ('not_requested', 'not_requested', True),
])
def test_caption_migration_preserves_conflicting_state(connections, source, reader, removed):
    from pathlib import Path
    sql = (Path(__file__).parents[2] / 'oko/storage/postgres/096-normalize-publication-content.sql').read_text()
    content = {'blocks': [], 'extra': {'youtube': {'captions': source}, '_reader': {'captions': reader}}}
    with connections() as db:
        db.execute('CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, platform text, title text)')
        db.execute('INSERT INTO publications (id,content,platform) VALUES (%s, %s, %s)', ('item', json.dumps(content), 'youtube'))
        db.execute(sql)
        result = json.loads(db.execute('SELECT content FROM publications').fetchone()[0])
        assert ('captions' not in result['extra']['_reader']) is removed
        assert result['extra']['youtube']['captions'] == source


def test_telegram_manifest_migration_removes_only_equal_copies(connections):
    from pathlib import Path
    sql = (Path(__file__).parents[2] / 'oko/storage/postgres/096-normalize-publication-content.sql').read_text()
    manifest = {'post': {'text': 'Saved'}, 'resources': []}
    with connections() as db:
        db.execute('CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, platform text, title text)')
        for identity, source in [('equal', manifest), ('different', {'post': {'text': 'Earlier'}, 'resources': []})]:
            content = {'blocks': [], 'extra': {'telegram': {'manifest': source, 'members': [1], 'description': 'Description'}, '_reader': {'source_manifest': manifest, 'members': [1] if identity=='equal' else [2], 'description': 'Description' if identity=='equal' else 'Other'}}}
            db.execute('INSERT INTO publications (id,content,platform) VALUES (%s, %s, %s)', (identity, json.dumps(content), 'telegram'))
        empty={'blocks':[], 'extra':{'telegram':{'description':''}, '_reader':{}}}
        db.execute('INSERT INTO publications (id,content,platform) VALUES (%s,%s,%s)',('empty',json.dumps(empty),'telegram'))
        db.execute(sql)
        rows = {identity: json.loads(content) for identity, content in db.execute('SELECT id,content FROM publications')}
        assert 'manifest' not in rows['equal']['extra']['telegram']
        assert rows['equal']['extra']['_reader']['source_manifest'] == manifest
        assert 'members' not in rows['equal']['extra']['telegram']
        assert 'description' not in rows['equal']['extra']['telegram']
        assert rows['different']['extra']['telegram']['manifest']['post']['text'] == 'Earlier'
        assert rows['different']['extra']['telegram']['members'] == [1]
        assert rows['different']['extra']['telegram']['description'] == 'Description'
        assert 'description' not in rows['empty']['extra']['telegram']


def test_content_migration_preserves_damaged_records(connections):
    from pathlib import Path
    sql = (Path(__file__).parents[2] / 'oko/storage/postgres/096-normalize-publication-content.sql').read_text()
    with connections() as db:
        db.execute('CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, platform text, title text)')
        for identity, value in [('invalid', '{broken'), ('nul', r'{"blocks":[],"bad":"\u0000"}'), ('huge', '{"x":1e1000000}'), ('scalar', '"unexpected"'), ('bad_blocks', '{"blocks":{},"extra":{"_reader":{}}}')]:
            db.execute('INSERT INTO publications (id,content,platform) VALUES (%s, %s, %s)', (identity, value, 'youtube'))
        before = db.execute('SELECT id,content FROM publications ORDER BY id').fetchall()
        db.execute(sql)
        assert db.execute('SELECT id,content FROM publications ORDER BY id').fetchall() == before


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


def test_title_migration_removes_only_matching_copies(connections):
    from pathlib import Path
    sql=(Path(__file__).parents[2]/'oko/storage/postgres/097-normalize-publication-titles.sql').read_text()
    with connections() as db:
        db.execute("CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, platform text, title text NOT NULL, processing_metadata text NOT NULL DEFAULT '{}')")
        db.execute('CREATE TABLE publication_links (source_publication_id text, target_publication_id text)')
        db.execute('CREATE TABLE jobs (item text, kind text, status text, input_snapshot jsonb)')
        for identity, shown in [('same','Stored'),('different','Displayed')]:
            content={'blocks':[], 'extra':{'_reader':{'title':shown}}}
            db.execute('INSERT INTO publications (id,content,platform,title) VALUES (%s,%s,%s,%s)', (identity,json.dumps(content),'rss','Stored'))
        db.execute(sql)
        result={identity:json.loads(content) for identity,content in db.execute('SELECT id,content FROM publications')}
        assert 'title' not in result['same']['extra']['_reader']
        assert 'title' not in result['different']['extra']['_reader']
        assert dict(db.execute('SELECT id,title FROM publications')) == {'same':'Stored','different':'Displayed'}


def test_title_migration_preserves_values_when_parent_metadata_is_invalid(connections):
    from pathlib import Path
    sql=(Path(__file__).parents[2]/'oko/storage/postgres/097-normalize-publication-titles.sql').read_text()
    with connections() as db:
        db.execute("CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, title text NOT NULL, processing_metadata text NOT NULL)")
        db.execute('CREATE TABLE publication_links (source_publication_id text, target_publication_id text)')
        db.execute('CREATE TABLE jobs (item text, kind text, status text, input_snapshot jsonb)')
        content=json.dumps({'blocks':[], 'extra':{'_reader':{'title':'Displayed'}}})
        db.execute('INSERT INTO publications VALUES (%s,%s,%s,%s)',('child',content,'Stored','{}'))
        db.execute('INSERT INTO publications VALUES (%s,%s,%s,%s)',('parent','{}','Parent','{broken'))
        db.execute("INSERT INTO publication_links VALUES ('parent','child')")
        db.execute(sql)
        assert db.execute("SELECT content,title FROM publications WHERE id='child'").fetchone()==(content,'Stored')
        db.execute(sql)
        assert db.execute("SELECT content,title FROM publications WHERE id='child'").fetchone()==(content,'Stored')


def test_telegram_manifest_migration_distinguishes_state_from_identity(connections):
    from pathlib import Path
    sql=(Path(__file__).parents[2]/'oko/storage/postgres/096-normalize-publication-content.sql').read_text()
    with connections() as db:
        db.execute('CREATE TABLE publications (id text PRIMARY KEY, content text NOT NULL, platform text, title text)')
        old={'post':{'text':'same'},'resources':[{'key':'m','origin':'url','status':'missing','name':'old','reason':'','sha256':None}]}
        ready={'post':{'text':'same'},'resources':[{'key':'m','origin':'url','status':'loaded','name':'new','reason':'','sha256':'digest'}]}
        different={'post':{'text':'same'},'resources':[{'key':'m','origin':'different','status':'loaded','name':'new','reason':'','sha256':'digest'}]}
        for identity, retained in [('state',ready),('identity',different)]:
            value={'blocks':[], 'extra':{'telegram':{'manifest':old},'_reader':{'source_manifest':retained}}}
            db.execute('INSERT INTO publications (id,content,platform) VALUES (%s,%s,%s)',(identity,json.dumps(value),'telegram'))
        db.execute(sql)
        rows={identity:json.loads(content) for identity,content in db.execute('SELECT id,content FROM publications')}
        assert 'manifest' not in rows['state']['extra']['telegram']
        assert rows['state']['extra']['_reader']['source_manifest']==ready
        assert rows['identity']['extra']['telegram']['manifest']==old


def test_retired_demo_preserves_publications_and_normalizes_discovery_target(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:98])
        db.execute("INSERT INTO folders(id,title,position) VALUES ('demo','Тестовый источник',-1)")
        db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,connection,transport,original_urls) VALUES ('demo','demo','Тестовый источник','demo','demo','connected','demo',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id,title,discovered_at,content) VALUES ('p','other_web','external-p','Saved',1,%s)", (json.dumps({'blocks': [], 'extra': {}}),))
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('demo','p',true,true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','','failed','saved error','source-error')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT kind,transport,connection FROM feeds WHERE id='demo'").fetchone() == ('unresolved',None,'needs_connection')
        assert db.execute("SELECT id FROM publications WHERE feed_id='demo'").fetchall() == [('p',)]
        assert db.execute("SELECT title FROM publications WHERE id='p'").fetchone() == ('Saved',)
        assert db.execute("SELECT item,status,reason_message,reason_code FROM jobs WHERE kind='discover'").fetchone() == ('demo','failed','saved error','source-error')


def test_retired_demo_migration_rejects_ambiguous_discovery_without_changes(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:98])
        db.execute("INSERT INTO folders(id,title,position) VALUES ('demo','Old feed',0)")
        db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,connection,transport,original_urls) VALUES ('demo','demo','Old feed','demo','demo','connected','demo',ARRAY[]::text[])")
        db.execute("INSERT INTO jobs(kind,item) VALUES ('discover',''),('discover','demo')")
        with pytest.raises(psycopg.errors.RaiseException, match='ambiguous legacy discovery jobs'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:98])
        assert db.execute("SELECT kind,transport FROM feeds WHERE id='demo'").fetchone() == ('demo','demo')
        assert db.execute("SELECT item FROM jobs WHERE kind='discover' ORDER BY item").fetchall() == [('',),('demo',)]


def test_retired_demo_is_readable_through_http_after_migration(postgres_database_url, tmp_path):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:98])
        db.execute("INSERT INTO folders(id,title,position) VALUES ('demo','Old synthetic feed',-1)")
        db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,connection,transport,original_urls) VALUES ('demo','demo','Old synthetic feed','demo','demo','connected','demo',ARRAY[]::text[])")
        content = {'blocks': [{'type': 'text', 'role': 'body', 'text': 'Saved text'}], 'extra': {'_reader': {'title': 'Saved', 'full': True, 'reason': '', 'members': [], 'captions': None, 'attachments': []}}}
        db.execute("INSERT INTO publications(id,platform,external_id,title,url,discovered_at,published_at,content,complete) VALUES ('p','other_web','https://example.org/p','Saved','https://example.org/p',1,1,%s,1)", (json.dumps(content),))
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('demo','p',true,true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','','failed','saved error','source-error')")
        migrate(db, MIGRATIONS)
    with TestClient(create_app(tmp_path, token='test', api_only=True, database_url=postgres_database_url), headers={'Authorization':'Bearer test'}) as client:
        feeds = client.get('/api/v3/subscriptions')
        assert feeds.status_code == 200, feeds.text
        feed = next(feed for feed in feeds.json()['feeds'] if feed['id'] == 'demo')
        assert (feed['kind'], feed['connection']) == ('unresolved', 'needs_connection')
        source = client.get('/api/v3/collection-status')
        assert source.status_code == 200, source.text
        assert any(item['feed_id'] == 'demo' for item in source.json()['sources'])
        material = client.get('/api/v3/publication/p')
        assert material.status_code == 200, material.text
        assert material.json()['title'] == 'Saved'
        assert material.json()['source']['kind'] == 'unresolved'
        assert 'Saved text' in material.text


def test_retired_demo_keeps_orphaned_discovery_after_feed_deletion(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:98])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','','failed','saved error','source-error')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT item,status,reason_message,reason_code FROM jobs WHERE kind='discover'").fetchone() == ('demo','failed','saved error','source-error')
        assert db.execute("SELECT id FROM feeds").fetchall() == []


def test_retired_demo_rejects_conflicting_saved_attempt_owner(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:98])
        db.execute("INSERT INTO jobs(kind,item,feed_id) VALUES ('discover','','other-feed')")
        with pytest.raises(psycopg.errors.RaiseException, match='owner conflicts'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:98])
        assert db.execute("SELECT item,feed_id FROM jobs WHERE kind='discover'").fetchone() == ('','other-feed')


def test_remove_source_order_preserves_publication_dates_across_restart(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:99])
        db.execute("INSERT INTO publications(id,platform,external_id,published_at,discovered_at,source_order) VALUES ('p','rss','external',NULL,2000,42)")
        migrate(db, MIGRATIONS)
    with connections() as db:
        verify(db, MIGRATIONS)
        assert db.execute("SELECT published_at,discovered_at FROM publications WHERE id='p'").fetchone() == (None, 2000)
        assert not db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='source_order'").fetchone()


def test_publication_feed_migration_refuses_all_conflicting_owners_atomically(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:100])
        db.execute("INSERT INTO feeds(id,platform,external_id) VALUES ('a','rss','a'),('b','rss','b'),('c','rss','c')")
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p1','rss','external-1'),('p2','rss','external-2')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','p1',true,true),('b','p1',false,true),('b','p2',true,true),('c','p2',false,true)")
        with pytest.raises(psycopg.errors.RaiseException) as failure:
            migrate(db, MIGRATIONS)
        message = str(failure.value)
        for value in ('p1', 'external-1', 'a', 'b', 'p2', 'external-2', 'c'):
            assert value in message
        verify(db, MIGRATIONS[:100])
        assert db.execute('SELECT count(*) FROM feed_publications').fetchone()[0] == 4
        assert not db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='feed_id'").fetchone()


def test_publication_feed_migration_preserves_owners_and_feedless_articles(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:100])
        db.execute("INSERT INTO feeds(id,platform,external_id) VALUES ('feed','rss','feed')")
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('post','rss','post'),('article','other_web','article')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','post',true,true)")
        db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('post','article')")
        migrate(db, MIGRATIONS)
    with connections() as db:
        verify(db, MIGRATIONS)
        assert db.execute('SELECT id,feed_id FROM publications ORDER BY id').fetchall() == [('article',None),('post','feed')]
        assert db.execute("SELECT to_regclass('feed_publications')").fetchone()[0] is None
        assert db.execute('SELECT source_publication_id,target_publication_id FROM publication_links').fetchall() == [('post','article')]


def test_publication_content_migrates_to_jsonb_without_changing_content(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    document = {'blocks': [{'type':'text','role':'body','text':'Hello'}], 'extra': {'telegram': {'members':['123']}}}
    with connections() as db:
        migrate(db, MIGRATIONS[:101])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('post','rss','post',%s)", (json.dumps(document),))
        migrate(db, MIGRATIONS)
    with connections() as db:
        verify(db, MIGRATIONS)
        value, kind = db.execute("SELECT content,pg_typeof(content)::text FROM publications WHERE id='post'").fetchone()
        assert kind == 'jsonb' and value == document


def test_legacy_content_keeps_existing_title_quote_and_description(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    existing = {'blocks': [{'type':'quote','text':'Canonical quote','author':'A','source_url':None},
                           {'type':'text','role':'description','text':'Canonical description'}],
                'extra': {'_reader': {'title':'Other title','reply_preview':{'text':'Other quote','author':None,'source_url':None},
                                      'description':'Other description','full':True}}}
    missing = {'blocks': [{'type':'text','role':'body','text':'Body'}],
               'extra': {'_reader': {'title':'Recovered title','reply_preview':{'text':'Recovered quote','author':'B','source_url':'https://example.test/quote'},
                                     'description':'Recovered description','full':True}}}
    with connections() as db:
        migrate(db, MIGRATIONS[:101])
        db.execute("INSERT INTO publications(id,platform,external_id,title,content) VALUES ('existing','rss','existing','Canonical title',%s),('missing','rss','missing','',%s)", (json.dumps(existing),json.dumps(missing)))
        migrate(db, MIGRATIONS)
        rows = {row[0]:(row[1],row[2]) for row in db.execute("SELECT id,title,content FROM publications ORDER BY id")}
        assert rows['existing'][0] == 'Canonical title'
        assert rows['existing'][1]['blocks'] == existing['blocks']
        assert rows['missing'][0] == ''
        assert rows['missing'][1]['blocks'] == [missing['blocks'][0], {'type':'quote','text':'Recovered quote','author':'B','source_url':'https://example.test/quote'}, {'type':'text','role':'description','text':'Recovered description'}]
        for _, content in rows.values():
            assert not {'title','reply_preview','description'} & content['extra']['_reader'].keys()


def test_legacy_release_keeps_original_title_and_quote_before_old_migrations(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    document = {'blocks': [{'type':'quote','text':'Canonical quote','author':None,'source_url':None}],
                'extra': {'_reader': {'title':'Other title','reply_preview':{'text':'Other quote','author':None,'source_url':None}}}}
    with connections() as db:
        migrate(db, MIGRATIONS[:95])
        db.execute("INSERT INTO publications(id,platform,external_id,title,published_known,discovered_at,content) VALUES ('post','rss','post','Canonical title',0,1,%s)", (json.dumps(document),))
        migrate(db, MIGRATIONS)
        title, content = db.execute("SELECT title,content FROM publications WHERE id='post'").fetchone()
        assert title == 'Canonical title'
        assert content['blocks'][0]['text'] == 'Canonical quote'


def test_invalid_publication_content_stops_jsonb_migration_without_data_loss(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:101])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('broken','rss','broken','not-json')")
        with pytest.raises(psycopg.errors.RaiseException, match='broken'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:101])
        assert db.execute("SELECT content,pg_typeof(content)::text FROM publications WHERE id='broken'").fetchone() == ('not-json','text')


def test_invalid_reader_content_stops_migration_without_discarding_data(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:101])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('broken','rss','broken',%s)", (json.dumps({'blocks':{},'extra':{'_reader':{'title':'kept'}}}),))
        with pytest.raises(psycopg.errors.RaiseException, match='broken'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:101])
        assert json.loads(db.execute("SELECT content FROM publications WHERE id='broken'").fetchone()[0])['extra']['_reader']['title'] == 'kept'


def test_legacy_title_migration_keeps_parent_processing_and_job_snapshot(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    from psycopg.types.json import Jsonb
    document = {'blocks': [], 'extra': {'_reader': {'title':'Legacy copy'}}}
    metadata = {'filter': {'signature':'filter-signature'}, 'summary': {'signature':'summary-signature'}}
    snapshot = {'signature':'running-input'}
    with connections() as db:
        migrate(db, MIGRATIONS[:95])
        db.execute("INSERT INTO publications(id,platform,external_id,title,published_known,discovered_at,content,processing_metadata) VALUES ('root','rss','root','Root',0,1,'{}',%s),('article','other_web','article','Canonical title',0,1,%s,'{}')", (json.dumps(metadata),json.dumps(document)))
        db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','article')")
        db.execute("INSERT INTO jobs(kind,item,status,input_snapshot) VALUES ('filter','root','done',%s)", (Jsonb(snapshot),))
        migrate(db, MIGRATIONS)
        assert json.loads(db.execute("SELECT processing_metadata FROM publications WHERE id='root'").fetchone()[0]) == metadata
        assert db.execute("SELECT input_snapshot FROM jobs WHERE kind='filter' AND item='root'").fetchone()[0] == snapshot
        assert db.execute("SELECT title FROM publications WHERE id='article'").fetchone()[0] == 'Canonical title'


@pytest.mark.parametrize('bad_block,reader', [
    ({'type':'quote'}, {'reply_preview':{'text':'Keep quote','author':None,'source_url':None}}),
    ({'type':'text','role':'description','text':123}, {'description':'Keep description'}),
])
def test_invalid_canonical_block_does_not_delete_valid_reader_copy(connections, bad_block, reader):
    from oko.storage.postgres_schema import MIGRATIONS
    document = {'blocks':[bad_block], 'extra':{'_reader':reader}}
    with connections() as db:
        migrate(db, MIGRATIONS[:101])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('broken','rss','broken',%s)", (json.dumps(document),))
        with pytest.raises(psycopg.errors.RaiseException, match='broken'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:101])
        assert json.loads(db.execute("SELECT content FROM publications WHERE id='broken'").fetchone()[0]) == document


def test_reader_block_migration_checksums_its_legacy_prelude(connections):
    from dataclasses import replace
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS)
        changed = replace(MIGRATIONS[102], prelude_sql=MIGRATIONS[102].prelude_sql + '\n-- altered')
        with pytest.raises(SchemaMismatch, match='history'):
            verify(db, (*MIGRATIONS[:102], changed, *MIGRATIONS[103:]))


@pytest.mark.parametrize('installed', [95,101])
def test_unknown_legacy_quote_fields_stop_migration_without_data_loss(connections, installed):
    from oko.storage.postgres_schema import MIGRATIONS
    document = {'blocks':[{'type':'quote','text':'Canonical','author':None,'source_url':None}],
                'extra':{'_reader':{'reply_preview':{'text':'Old','author':None,'source_url':None,'unrecognized':'keep'}}}}
    with connections() as db:
        migrate(db, MIGRATIONS[:installed])
        if installed < 98:
            db.execute("INSERT INTO publications(id,platform,external_id,published_known,discovered_at,content) VALUES ('broken','rss','broken',0,1,%s)", (json.dumps(document),))
        else:
            db.execute("INSERT INTO publications(id,platform,external_id,discovered_at,content) VALUES ('broken','rss','broken',1,%s)", (json.dumps(document),))
        with pytest.raises(psycopg.errors.RaiseException, match='broken'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:installed])
        assert json.loads(db.execute("SELECT content FROM publications WHERE id='broken'").fetchone()[0]) == document


def test_reader_prelude_blocks_external_writer_until_release_finishes(connections):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    import time
    from oko.storage.postgres_schema import MIGRATIONS

    with connections() as installer, connections() as writer:
        migrate(installer, MIGRATIONS[:95])
        delayed = replace(MIGRATIONS[102], prelude_sql=MIGRATIONS[102].prelude_sql + '\nSELECT pg_sleep(2);')
        release = (*MIGRATIONS[:102], delayed, *MIGRATIONS[103:])
        pid = installer.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(migrate, installer, release)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                locked = writer.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s AND relation='publications'::regclass AND mode='AccessExclusiveLock' AND granted)", (pid,)).fetchone()[0]
                if locked:
                    break
                time.sleep(.02)
            assert locked, 'Migration did not acquire publication lock'
            writer.execute("SET lock_timeout='150ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                writer.execute("INSERT INTO publications(id,platform,external_id,published_known,discovered_at) VALUES ('outside','rss','outside',0,1)")
            pending.result(timeout=10)
        verify(installer, release)


def test_media_ids_migration_preserves_order_and_media_after_restart(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    from psycopg.types.json import Jsonb
    with connections() as db:
        migrate(db, MIGRATIONS[:103])
        content={'blocks':[{'type':'media','role':'image','media_id':'m1'},{'type':'media','role':'image','media_id':'m2'}], 'extra':{}}
        db.execute("INSERT INTO publications(id,platform,external_id,media_ids,content) VALUES ('p','rss','p',%s,%s)", (json.dumps(['m2','m1']),Jsonb(content)))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m1','p','one','https://media.test/1','one','pending_download'),('m2','p','two','https://media.test/2','two','pending_download')")
        migrate(db, MIGRATIONS)
    with connections() as db:
        verify(db, MIGRATIONS)
        ids, saved = db.execute("SELECT media_ids,content FROM publications WHERE id='p'").fetchone()
        assert json.loads(ids)==['m2','m1'] and saved==content
        assert db.execute("SELECT id FROM media_files WHERE publication_id='p' ORDER BY id").fetchall()==[('m1',),('m2',)]


@pytest.mark.parametrize('case',['invalid_json','non_array','non_string','duplicate','missing','foreign','omitted','bad_block','missing_block_id'])
def test_media_ids_migration_rejects_conflicts_without_data_loss(connections, case):
    from oko.storage.postgres_schema import MIGRATIONS
    from psycopg.types.json import Jsonb
    with connections() as db:
        migrate(db, MIGRATIONS[:103])
        db.execute("INSERT INTO publications(id,platform,external_id,media_ids,content) VALUES ('p','rss','p','[\"m1\"]',%s),('other','rss','other','[\"foreign\"]',%s)", (Jsonb({'blocks':[],'extra':{}}),Jsonb({'blocks':[],'extra':{}})))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m1','p','one','https://media.test/1','one','pending_download'),('foreign','other','other','https://media.test/other','other','pending_download')")
        values={'invalid_json':'not-json','non_array':'{}','non_string':'[1]','duplicate':'["m1","m1"]',
                'missing':'["missing"]','foreign':'["foreign"]','omitted':'[]'}
        if case in values:
            db.execute("UPDATE publications SET media_ids=%s WHERE id='p'",(values[case],))
        else:
            block={'type':'media','role':'image'}
            if case=='bad_block':block['media_id']='foreign'
            db.execute("UPDATE publications SET content=%s WHERE id='p'",(Jsonb({'blocks':[block],'extra':{}}),))
        before=db.execute("SELECT media_ids,content FROM publications WHERE id='p'").fetchone()
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:103])
        assert db.execute("SELECT media_ids,content FROM publications WHERE id='p'").fetchone()==before
        assert db.execute("SELECT id,publication_id FROM media_files ORDER BY id").fetchall()==[('foreign','other'),('m1','p')]


@pytest.mark.parametrize('content',[{'blocks':{'type':'media','media_id':'foreign'}},{'blocks':None},['invalid'],None])
def test_media_migration_rejects_invalid_current_content_shape(connections, content):
    from oko.storage.postgres_schema import MIGRATIONS
    from psycopg.types.json import Jsonb
    with connections() as db:
        migrate(db, MIGRATIONS[:103])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','rss','p',%s)", (Jsonb(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:103])
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0] == content


def test_media_migration_allows_empty_pending_content(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:103])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('pending','rss','pending')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='pending'").fetchone()[0] == {}


def test_job_reason_migration_preserves_error_and_subtitle_absence(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('video','youtube','video',%s::jsonb)",(json.dumps({'extra':{'youtube':{'captions':'unavailable'}}}),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('sub','video','youtube:subtitles','','subtitles','unavailable','No subtitles')")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','sub','done',NULL,NULL),('fetch','video','pending','Network failed','network'),('filter','video','failed','','')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT reason_code,reason_message FROM jobs WHERE kind='fetch' AND item='video'").fetchone() == ('network','Network failed')
        assert db.execute("SELECT reason_code,reason_message FROM jobs WHERE kind='filter' AND item='video'").fetchone() == ('','')
        assert db.execute("SELECT reason_code,reason_message FROM jobs WHERE kind='download_media' AND item='sub'").fetchone() == ('subtitles_absent','No subtitles')
        assert db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='jobs' AND column_name IN ('problem_code','error')").fetchone() is None


def test_migration_retains_absent_subtitles_when_old_job_was_cleaned(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('video','youtube','video',%s::jsonb)",(json.dumps({'extra':{'youtube':{'captions':'unavailable'}}}),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('sub','video','youtube:subtitles','','subtitles','unavailable','No subtitles')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='download_media' AND item='sub'").fetchone() == ('done','subtitles_absent','No subtitles')


@pytest.mark.parametrize('caption,code,message', [('error','source-network','HTTP 503'),('unavailable','source-network','HTTP 503'),('unavailable',None,'HTTP 503'),('error',None,None)])
def test_migration_rejects_ambiguous_subtitle_outcome(connections,caption,code,message):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('video','youtube','video',%s::jsonb)",(json.dumps({'extra':{'youtube':{'captions':caption}}}),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('sub','video','youtube:subtitles','','subtitles','unavailable',%s)",(message or '',))
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','sub','done',%s,%s)",(message,code))
        with pytest.raises(psycopg.errors.RaiseException,match='sub'):
            migrate(db,MIGRATIONS)


@pytest.mark.parametrize('outcome',[None,(None,None),('',''),(None,'')])
def test_migration_rejects_unavailable_media_without_explanation(connections,outcome):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('m','p','image','','image','unavailable','')")
        if outcome is not None:
            db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','m','unavailable',%s,%s)",outcome)
        with pytest.raises(psycopg.errors.RaiseException,match='m'):
            migrate(db,MIGRATIONS)


def test_migration_rejects_subtitle_absence_on_image(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('m','p','image','','image','unavailable','')")
        db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('download_media','m','done','subtitles_absent')")
        with pytest.raises(psycopg.errors.RaiseException,match='m'):
            migrate(db,MIGRATIONS)


@pytest.mark.parametrize('job_status',['done','unavailable'])
def test_migration_rejects_terminal_unavailable_job_for_pending_media(connections,job_status):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('m','p','image','','image','pending_download','')")
        db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES ('download_media','m',%s,'access-denied','Denied')",(job_status,))
        with pytest.raises(psycopg.errors.RaiseException,match='m'):
            migrate(db,MIGRATIONS)


def test_migration_marks_never_requested_subtitles_pending_without_job(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('video','youtube','video',%s::jsonb)",(json.dumps({'extra':{'youtube':{'captions':'not_requested'}}}),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('sub','video','youtube:subtitles','','subtitles','unavailable','Субтитры не запрошены')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT status FROM media_files WHERE id='sub'").fetchone() == ('pending_download',)
        assert db.execute("SELECT id FROM jobs WHERE kind='download_media' AND item='sub'").fetchone() is None


@pytest.mark.parametrize('job_status',['pending','running','blocked'])
@pytest.mark.parametrize('caption',['unavailable','error','not_requested'])
def test_migration_clears_stale_subtitle_absence_when_retry_is_active(connections,job_status,caption):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('video','youtube','video',%s::jsonb)",(json.dumps({'extra':{'youtube':{'captions':caption}}}),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('sub','video','youtube:subtitles','','subtitles','pending_download','')")
        db.execute("INSERT INTO jobs(kind,item,status) VALUES ('download_media','sub',%s)",(job_status,))
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT content #>> '{extra,youtube,captions}' FROM publications WHERE id='video'").fetchone()==('pending',)


def test_migration_restores_confirmed_subtitle_absence_from_job(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('video','youtube','video',%s::jsonb)",(json.dumps({'extra':{'youtube':{'captions':'error'}}}),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('sub','video','youtube:subtitles','','subtitles','unavailable','')")
        db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('download_media','sub','done','subtitles_absent')")
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT content #>> '{extra,youtube,captions}' FROM publications WHERE id='video'").fetchone()==('unavailable',)


def test_migration_keeps_media_reason_when_legacy_job_is_missing(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('file','article','image:1','','image','unavailable','Access restricted')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT status,reason_message FROM jobs WHERE kind='download_media' AND item='file'").fetchone() == ('unavailable','Access restricted')


def test_migration_fills_missing_job_reason_from_legacy_media(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('file','article','image:1','','image','unavailable','Access restricted')")
        db.execute("INSERT INTO jobs(kind,item,status) VALUES ('download_media','file','unavailable')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT reason_message FROM jobs WHERE kind='download_media' AND item='file'").fetchone() == ('Access restricted',)


def test_migration_rejects_conflicting_media_and_job_reasons(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('file','article','image:1','','image','unavailable','First reason')")
        db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','file','unavailable','Second reason')")
        with pytest.raises(psycopg.errors.RaiseException,match='file'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT reason FROM media_files WHERE id='file'").fetchone() == ('First reason',)
        assert db.execute("SELECT error FROM jobs WHERE kind='download_media' AND item='file'").fetchone() == ('Second reason',)


def test_migration_restores_completed_job_for_saved_media(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('file','article','image:1','','image','ready','media/article/file')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='download_media' AND item='file'").fetchone()==('done',None,None)


def test_migration_does_not_invent_outcome_for_ready_file_with_unknown_error(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key,reason) VALUES ('file','article','image:1','','image','ready','media/article/file','Unknown previous attempt')")
        with pytest.raises(psycopg.errors.RaiseException,match='file'):
            migrate(db, MIGRATIONS)
        assert db.execute("SELECT status,reason FROM media_files WHERE id='file'").fetchone()==('ready','Unknown previous attempt')


def test_migration_moves_pending_media_reason_and_removes_media_column(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('file','article','image:1','','image','pending_download','Temporary failure')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT status,reason_message FROM jobs WHERE kind='download_media' AND item='file'").fetchone()==('pending','Temporary failure')
        assert db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='media_files' AND column_name='reason'").fetchone() is None


def test_migration_removes_publication_complete_without_losing_partial_content(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:105])
        content={'blocks':[{'type':'text','role':'body','text':'Saved fragment'}],'extra':{}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,complete,content) VALUES ('article','other_web','article','pending_collection',0,%s::jsonb)",(json.dumps(content),))
        migrate(db, MIGRATIONS)
        row=db.execute("SELECT status,content FROM publications WHERE id='article'").fetchone()
        assert row==('pending_collection',content)
        assert db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='complete'").fetchone() is None


def test_migration_preserves_legacy_done_fetch_failure_as_failed(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','done','Access denied','fetch-denied')")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='fetch' AND item='article'").fetchone()==('failed','fetch-denied','Access denied')


def test_migration_rebuilds_incomplete_projection_without_losing_job_problem(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:105])
        db.execute("INSERT INTO feeds(id,platform,external_id,url) VALUES ('feed','rss','feed','https://example.test/feed')")
        content={'blocks':[{'type':'text','role':'body','text':'Saved'}], 'extra':{'_reader':{'full':True,'reason':''}}}
        db.execute("INSERT INTO publications(id,platform,external_id,feed_id,status,content,incomplete_class,incomplete_group_key,incomplete_group_ready) VALUES ('p','rss','p','feed','ready',%s::jsonb,0,'incomplete:feed:p',true)",(json.dumps(content),))
        legacy={'code':'material-incomplete','operation':'producer:incomplete','objects':[{'kind':'material','id':'p'}]}
        actual={'code':'source-network','operation':'producer:fetch','objects':[{'kind':'material','id':'p'}]}
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','incomplete:feed:p','background',%s,1),('actual','job:source-network:feed:fetch','background',%s,1)",(json.dumps(legacy),json.dumps(actual)))
        db.execute("INSERT INTO state(key,value) VALUES ('incomplete-class-recovered-v1','true'),('experimental-incomplete-groups-v1','true'),('experimental-incomplete-problems-v1','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT finished IS NOT NULL FROM reader_problems WHERE id='legacy'").fetchone()==(True,)
        assert db.execute("SELECT finished FROM reader_problems WHERE id='actual'").fetchone()==(None,)
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name LIKE 'incomplete_%%'").fetchone()==(0,)
        assert db.execute("SELECT count(*) FROM state WHERE key IN ('incomplete-class-recovered-v1','experimental-incomplete-groups-v1','experimental-incomplete-problems-v1')").fetchone()==(0,)


def test_migration_stops_on_ambiguous_incomplete_projection(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:105])
        db.execute("INSERT INTO publications(id,platform,external_id,incomplete_group_unknown) VALUES ('p','rss','p',true)")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT complete,incomplete_group_unknown FROM publications WHERE id='p'").fetchone()==(0,True)


def test_migration_removes_publication_incomplete_projection_columns(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','rss','p','pending_collection',%s::jsonb)",(json.dumps({'blocks':[{'type':'text','role':'body','text':'fragment'}],'extra':{}}),))
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name LIKE 'incomplete_%%'").fetchall()==[]
        assert db.execute("SELECT status,content->'blocks'->0->>'text' FROM publications WHERE id='p'").fetchone()==('pending_collection','fragment')


def test_migration_removes_content_completeness_after_preserving_job_reason(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[{'type':'text','role':'body','text':'fragment'}], 'extra':{'_reader':{'full':False,'reason':'Access restricted'},'_collection_reason':'Access restricted'}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','rss','p','pending_collection',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('fetch','p','unavailable','source-unavailable','Access restricted')")
        migrate(db,MIGRATIONS)
        saved=db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
        assert saved['blocks']==content['blocks']
        assert '_collection_reason' not in saved['extra']
        assert 'full' not in saved['extra']['_reader'] and 'reason' not in saved['extra']['_reader']
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='fetch' AND item='p'").fetchone()==('unavailable','source-unavailable','Access restricted')


def test_migration_preserves_unfinished_legacy_reason_without_existing_job(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[{'type':'text','role':'body','text':'fragment'}],'extra':{'_reader':{'full':False,'reason':'Partial source'},'_collection_reason':'Partial source'}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','rss','p','pending_collection',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='fetch' AND item='p'").fetchone()==('pending','legacy-incomplete','Partial source')
        assert db.execute("SELECT content #> '{extra,_reader,reason}' FROM publications WHERE id='p'").fetchone()==(None,)


def test_migration_converts_done_legacy_unavailable_publication_to_unavailable_job(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[],'extra':{'_reader':{'full':False,'reason':'','source_manifest':{'post_web_unavailable':True,'post':{},'resources':[]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','telegram','p','pending_collection',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','done',3)")
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT status,generation,reason_code FROM jobs WHERE kind='fetch' AND item='p'").fetchone()==('unavailable',4,'post-web-unavailable')


def test_migration_rejects_unowned_legacy_collection_reason(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[{'type':'text','role':'body','text':'Saved'}],'extra':{'_reader':{'full':True,'reason':'Unknown source condition'},'_collection_reason':'Unknown source condition'}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','rss','p','ready',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content #>> '{extra,_collection_reason}' FROM publications WHERE id='p'").fetchone()==('Unknown source condition',)


def test_migration_keeps_ready_copy_and_records_legacy_source_restriction(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[{'type':'text','role':'body','text':'Saved'}], 'extra':{'_reader':{'full':False,'reason':'Source restricted','source_manifest':{'post_web_unavailable':True,'post':{},'resources':[]}},'_collection_reason':'Source restricted'}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','telegram','p','ready',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','done',2)")
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT status,content->'blocks'->0->>'text' FROM publications WHERE id='p'").fetchone()==('ready','Saved')
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='fetch' AND item='p'").fetchone()==('unavailable','post-web-unavailable','Source restricted')


def test_migrated_unavailable_reason_is_recovered_in_diagnostics(tmp_path,postgres_database_url):
    from oko.storage.postgres_schema import MIGRATIONS
    from tests.support.storage import Store
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:106])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        content={'blocks':[],'extra':{'_reader':{'full':False,'reason':'Restricted','source_manifest':{'post_web_unavailable':True,'post':{},'resources':[]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,feed_id,status,content) VALUES ('p','rss','p','f','pending_collection',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO jobs(kind,item,status) VALUES ('fetch','p','done')")
        db.execute("INSERT INTO state(key,value) VALUES ('fetch-problems-recovered-v2','true') ON CONFLICT(key) DO UPDATE SET value='true'")
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        problems=store.problems.snapshot(store.wall_clock())['current']
        assert any(p['code']=='post-web-unavailable' and any(o['id']=='p' for o in p['objects']) for p in problems)
    finally:store.db.close()


def test_migrated_media_reason_is_recovered_in_diagnostics(tmp_path,postgres_database_url):
    from oko.storage.postgres_schema import MIGRATIONS
    from tests.support.storage import Store
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('p','rss','p','f')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('m','p','image','','image','unavailable','Access restricted')")
        db.execute("INSERT INTO state(key,value) VALUES ('media-null-problems-recovered-v1','true'),('media-problems-recovered-v2','true') ON CONFLICT(key) DO UPDATE SET value='true'")
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        problems=store.problems.snapshot(store.wall_clock())['current']
        assert any(p['code']=='work-download_media' and any(o['id']=='p' for o in p['objects']) for p in problems)
    finally:store.db.close()


def test_migration_rejects_ambiguous_done_processing_with_error(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','p','done','Old error','model-error')")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT status,error,problem_code FROM jobs WHERE kind='filter' AND item='p'").fetchone()==('done','Old error','model-error')


def test_migration_rejects_done_media_error_without_code(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:104])
        db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','missing','done','Old error')")
        with pytest.raises(psycopg.errors.RaiseException,match='missing'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT status,error FROM jobs WHERE item='missing'").fetchone()==('done','Old error')


def test_migration_rejects_unowned_reader_reason_on_ready_publication(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[{'type':'text','role':'body','text':'Saved'}],'extra':{'_reader':{'full':False,'reason':'Unknown source condition'}}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','rss','p','ready',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content #>> '{extra,_reader,reason}' FROM publications WHERE id='p'").fetchone()==('Unknown source condition',)


def test_migration_does_not_discard_unrelated_reader_reason_when_media_exists(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[],'extra':{'_reader':{'reason':'Unknown source condition'}}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','rss','p','ready',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','','image','pending_download')")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)


@pytest.mark.parametrize('flag',[False,0,'0','+0','-0','000',' 0 '])
def test_migration_rejects_unowned_incomplete_flag(connections,flag):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db,MIGRATIONS[:106])
        content={'blocks':[],'extra':{'_reader':{'full':flag,'reason':''}}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','rss','p','ready',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content #> '{extra,_reader,full}' FROM publications WHERE id='p'").fetchone()==(flag,)
