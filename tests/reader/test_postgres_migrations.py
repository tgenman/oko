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
        assert db.execute("SELECT item,status,error,problem_code FROM jobs WHERE kind='discover'").fetchone() == ('demo','failed','saved error','source-error')


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
        assert db.execute("SELECT item,status,error,problem_code FROM jobs WHERE kind='discover'").fetchone() == ('demo','failed','saved error','source-error')
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


def test_viewed_retention_migration_stops_legacy_cleanup_and_preserves_owners(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:99])
        db.execute("INSERT INTO folders(id,title,position) VALUES ('f','Folder',0)")
        db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,connection,transport,original_urls) VALUES ('active','rss:a','A','rss','f','connected','rss',ARRAY['https://a.test/feed']),('removed','rss:b','B','rss','f','deleting','rss',ARRAY['https://b.test/feed'])")
        db.execute("INSERT INTO publications(id,platform,external_id,title,url,discovered_at,viewed,complete) VALUES ('viewed','rss','a','A','https://a.test/1',1,1,1),('removed-item','rss','b','B','https://b.test/1',1,1,1)")
        db.execute("INSERT INTO jobs(kind,item,status,payload,error,problem_code) VALUES ('delete','viewed','pending','{\"cleanup_feed\":\"active\"}',NULL,NULL),('delete','removed-item','failed','{\"cleanup_feed\":\"removed\"}','old cleanup error','delete'),('feed-delete','removed','pending','{}',NULL,NULL)")
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('cleanup-problem','cleanup-problem','background',%s,1)", (json.dumps({'operation':'producer:delete','code':'delete'}),))
        migrate(db, MIGRATIONS[:100])
        assert db.execute("SELECT id,deleted FROM feeds ORDER BY id").fetchall() == [('active', False), ('removed', True)]
        assert db.execute("SELECT feed_id,publication_id FROM feed_publications ORDER BY feed_id").fetchall() == [('active','viewed'),('removed','removed-item')]
        assert db.execute("SELECT kind,item,status FROM jobs WHERE kind IN ('delete','feed-delete') ORDER BY kind,item").fetchall() == [('delete','removed-item','cancelled'),('delete','viewed','cancelled'),('feed-delete','removed','cancelled')]
        assert db.execute("SELECT id,complete FROM publications ORDER BY id").fetchall() == [('removed-item',0),('viewed',0)]
        assert db.execute("SELECT error,problem_code FROM jobs WHERE kind='delete' AND item='removed-item'").fetchone() == (None,None)
        assert db.execute("SELECT outcome FROM reader_problems WHERE id='cleanup-problem'").fetchone() == ('irrelevant',)


def test_single_feed_migration_rejects_conflicting_memberships_without_changes(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:100])
        db.execute("INSERT INTO feeds(id,identity) VALUES ('a','a'),('b','b')")
        db.execute("INSERT INTO publications(id,platform,external_id,discovered_at) VALUES ('p','rss','https://example.test/p',1)")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','p',true,true),('b','p',false,true)")
        with pytest.raises(psycopg.errors.RaiseException, match='multiple feeds'):
            migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS[:100])
        assert db.execute("SELECT feed_id FROM feed_publications WHERE publication_id='p' ORDER BY feed_id").fetchall() == [('a',),('b',)]


def test_single_feed_migration_preserves_owner_and_feedless_articles(connections):
    from oko.storage.postgres_schema import MIGRATIONS
    with connections() as db:
        migrate(db, MIGRATIONS[:100])
        db.execute("INSERT INTO feeds(id,identity) VALUES ('a','a')")
        db.execute("INSERT INTO publications(id,platform,external_id,discovered_at) VALUES ('p','rss','https://example.test/p',1),('article','other_web','https://example.test/article',1)")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','p',true,true)")
        migrate(db, MIGRATIONS)
        assert db.execute("SELECT id,feed_id FROM publications ORDER BY id").fetchall() == [('article',None),('p','a')]
        assert db.execute("SELECT to_regclass('feed_publications')").fetchone() == (None,)
