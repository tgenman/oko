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
