"""Migration preserves only actual source URLs, not opaque adapter locators."""
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
import psycopg
import json
import pytest


def test_media_source_urls_preserve_direct_links_and_leave_opaque_locators_null(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','youtube','p')")
        db.execute("""INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status)
            VALUES ('image','p','image','https://cdn.example/image.png','image','pending_download'),
                   ('sub','p','subtitles','youtube:video:subtitles','sub','pending_download')""")
        migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT id,source_url FROM media_files ORDER BY id").fetchall()==[
            ('image','https://cdn.example/image.png'),('sub',None)]


def test_migration_preserves_each_album_message_url_in_the_media_block(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[{'type':'media','role':'image','media_id':'m'}],
                 'extra':{'_reader':{'source_manifest':{'resources':[{'key':'telegram-web:12:0:photo','origin':'telegram:sample/11'}]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,url,members,content) VALUES ('p','telegram','p','https://t.me/sample/11','[11,12]',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','telegram-web:12:0:photo','https://cdn.example/m.png','image','pending_download')")
        migrate(db,MIGRATIONS[:108])
        block=db.execute("SELECT content->'blocks'->0 FROM publications WHERE id='p'").fetchone()[0]
        assert block['source_url']=='https://cdn.example/m.png'
        assert block['publication_url']=='https://t.me/sample/12'


def test_migration_stops_on_conflicting_saved_media_source_urls(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[{'type':'media','role':'image','media_id':'m','source_url':'https://original.example/a'},
                           {'type':'media','role':'image','media_id':'m','source_url':'https://original.example/b'}]}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','rss','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','https://cdn.example/different','image','pending_download')")
        with pytest.raises(psycopg.errors.RaiseException,match='m'):
            migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_explicit_original_url_is_not_replaced_by_a_different_adapter_locator(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[{'type':'media','role':'image','media_id':'m','source_url':'https://original.example/file'}]}
        db.execute("INSERT INTO publications(id,platform,external_id,url,content) VALUES ('p','rss','p','https://example.test/post',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','https://cdn.example/transient','image','pending_download')")
        migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT source_url FROM media_files WHERE id='m'").fetchone()==('https://original.example/file',)


@pytest.mark.parametrize('existing',[False,True])
def test_migration_expresses_existing_link_as_a_single_publication_block(postgres_database_url,existing):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        blocks=[{'type':'text','role':'body','text':'Post'}]
        if existing:blocks.append({'type':'publication','publication_id':'article','source_url':'https://example.test/article'})
        db.execute("INSERT INTO publications(id,platform,external_id,url,content) VALUES ('root','rss','root','https://example.test/root',%s::jsonb),('article','other_web','article','https://example.test/article','{}'::jsonb)",(json.dumps({'blocks':blocks}),))
        db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','article')")
        migrate(db,MIGRATIONS[:108])
        result=db.execute("SELECT content->'blocks' FROM publications WHERE id='root'").fetchone()[0]
        assert [b for b in result if b['type']=='publication']==[{'type':'publication','publication_id':'article','source_url':'https://example.test/article'}]


def test_migration_stops_when_a_publication_block_conflicts_with_its_link(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[{'type':'publication','publication_id':'article','source_url':'https://wrong.example/article'}]}
        db.execute("INSERT INTO publications(id,platform,external_id,url,content) VALUES ('root','rss','root','https://example.test/root',%s::jsonb),('article','other_web','article','https://example.test/article','{}'::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','article')")
        with pytest.raises(psycopg.errors.RaiseException,match='root'):
            migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT content FROM publications WHERE id='root'").fetchone()[0]==content


@pytest.mark.parametrize('existing',[False,True])
def test_migration_moves_external_media_display_facts_to_common_block(postgres_database_url,existing):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        external={'kind':'audio','source_url':'https://t.me/channel/12','reason':'public_preview_unavailable',
                  'title':'Lesson','author':'Author','restriction':None,'duration':None,'size_label':None,'poster_key':None}
        blocks=[{'type':'external_media','kind':'audio','url':external['source_url']}] if existing else []
        content={'blocks':blocks,'extra':{'_reader':{'source_manifest':{'resources':[{'key':'audio','status':'external','external':external}]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,url,content) VALUES ('p','telegram','p','https://t.me/channel/12',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:108])
        result=db.execute("SELECT content->'blocks' FROM publications WHERE id='p'").fetchone()[0]
        external_blocks=[b for b in result if b['type']=='external_media']
        assert len(external_blocks)==1
        assert external_blocks[0]['url']=='https://t.me/channel/12'
        assert external_blocks[0]['title']=='Lesson' and external_blocks[0]['author']=='Author'


def test_migration_stops_when_external_media_metadata_conflicts(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        external={'kind':'audio','source_url':'https://t.me/channel/12','title':'Saved title'}
        content={'blocks':[{'type':'external_media','kind':'audio','url':external['source_url'],'title':'Different title'}],
                 'extra':{'_reader':{'source_manifest':{'resources':[{'key':'audio','status':'external','external':external}]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,url,content) VALUES ('p','telegram','p','https://t.me/channel/12',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_migration_preserves_telegram_members_in_content(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[{'type':'text','role':'body','text':'Album'}]}
        db.execute("INSERT INTO publications(id,platform,external_id,members,content) VALUES ('p','telegram','p','[11,12]',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT content #> '{extra,telegram,members}' FROM publications WHERE id='p'").fetchone()[0]==[11,12]


def test_migration_stops_on_conflicting_telegram_members(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[], 'extra':{'telegram':{'members':[11,13]}}}
        db.execute("INSERT INTO publications(id,platform,external_id,members,content) VALUES ('p','telegram','p','[11,12]',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_migration_normalizes_external_video_kind(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[{'type':'external_media','kind':'youtube','url':'https://youtube.example/watch'}]}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','youtube','p',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT content->'blocks'->0 FROM publications WHERE id='p'").fetchone()[0]=={
            'type':'external_media','kind':'video','url':'https://youtube.example/watch'}


def test_migration_stops_when_reader_and_telegram_members_conflict_without_column(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:107])
        content={'blocks':[], 'extra':{'telegram':{'members':[11,12]},'_reader':{'members':[11,13]}}}
        db.execute("INSERT INTO publications(id,platform,external_id,members,content) VALUES ('p','telegram','p','[]',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:108])
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content
