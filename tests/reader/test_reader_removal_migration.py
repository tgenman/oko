"""Legacy reader copies are removed only after their facts are preserved."""
import json
import psycopg
import pytest

from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


def test_migration_prefers_existing_blocks_and_title_and_removes_reader(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        content={'blocks':[{'type':'text','role':'body','text':'Body'},
                           {'type':'quote','text':'Canonical quote','author':'A','source_url':None},
                           {'type':'text','role':'description','text':'Canonical description'}],
                 'extra':{'_reader':{'title':'Old title','source_manifest':{'source_url':'https://example.test/p',
                     'post':{'text':'Body','description':'Old description','reply_preview':{'text':'Old quote','author':None,'source_url':None}},'resources':[]}},
                          'telegram':{'members':[11]}}}
        db.execute("INSERT INTO publications(id,platform,external_id,url,title,content) VALUES ('p','telegram','p','https://example.test/p','Canonical title',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS)
        saved,title=db.execute("SELECT content,title FROM publications WHERE id='p'").fetchone()
        assert title=='Canonical title' and saved['blocks']==content['blocks']
        assert saved['extra']=={'telegram':{'members':[11]}}


def test_migration_recovers_missing_quote_and_description(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        content={'blocks':[{'type':'text','role':'body','text':'Body'}],
                 'extra':{'_reader':{'source_manifest':{'post':{'text':'Body','description':'Description',
                     'reply_preview':{'text':'Quote','author':'Author','source_url':'https://example.test/q'}},'resources':[]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS)
        blocks=db.execute("SELECT content->'blocks' FROM publications WHERE id='p'").fetchone()[0]
        assert {'type':'text','role':'description','text':'Description'} in blocks
        assert {'type':'quote','text':'Quote','author':'Author','source_url':'https://example.test/q'} in blocks


@pytest.mark.parametrize('reader',[
    {'unrecognized':{'important':'data'}},
    {'source_manifest':{'post':{'text':'Different body'},'resources':[]}},
    {'attachments':[{'name':'missing','media_id':'missing','kind':'image','mime':'image/png'}]},
])
def test_migration_stops_before_discarding_unrepresented_data(postgres_database_url,reader):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        content={'blocks':[{'type':'text','role':'body','text':'Body'}],'extra':{'_reader':reader}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_migration_restores_media_block_with_circle_presentation(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        content={'blocks':[],'extra':{'_reader':{'attachments':[{'name':'clip','media_id':'m','kind':'circle','mime':'video/mp4'}]}}}
        db.execute("INSERT INTO publications(id,platform,external_id,url,content,media_ids) VALUES ('p','telegram','p','https://example.test/post',%s::jsonb,'[\"m\"]')",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,source_url,name,mime_type,status) VALUES ('m','p','clip','https://cdn.example/file','https://cdn.example/file','clip','video/mp4','pending_download')")
        migrate(db,MIGRATIONS)
        block=db.execute("SELECT content->'blocks'->0 FROM publications WHERE id='p'").fetchone()[0]
        assert block=={'type':'media','role':'video','presentation':'circle','media_id':'m',
                       'source_url':'https://cdn.example/file','publication_url':'https://example.test/post'}


def test_migration_stops_on_unrepresented_manifest_post_resource(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        content={'blocks':[],'extra':{'_reader':{'source_manifest':{'post':{'resources':[{'key':'lost','kind':'photo'}]},'resources':[]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):migrate(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_migration_adds_circle_presentation_to_existing_video_block(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        content={'blocks':[{'type':'media','role':'video','media_id':'m','source_url':'https://cdn.example/file','publication_url':'https://example.test/post'}],
                 'extra':{'_reader':{'attachments':[{'name':'clip','media_id':'m','kind':'circle','mime':'video/mp4'}]}}}
        db.execute("INSERT INTO publications(id,platform,external_id,url,content,media_ids) VALUES ('p','telegram','p','https://example.test/post',%s::jsonb,'[\"m\"]')",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,source_url,name,mime_type,status) VALUES ('m','p','clip','https://cdn.example/file','https://cdn.example/file','clip','video/mp4','pending_download')")
        migrate(db,MIGRATIONS)
        blocks=db.execute("SELECT content->'blocks' FROM publications WHERE id='p'").fetchone()[0]
        assert len(blocks)==1 and blocks[0]['presentation']=='circle'


@pytest.mark.parametrize('field',['description','reply_preview'])
def test_migration_stops_on_conflicting_legacy_copies_without_a_block(postgres_database_url,field):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        first='First' if field=='description' else {'text':'First','author':None,'source_url':None}
        second='Second' if field=='description' else {'text':'Second','author':None,'source_url':None}
        reader={field:first,'source_manifest':{'post':{field:second},'resources':[]}}
        content={'blocks':[],'extra':{'_reader':reader}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):migrate(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_migration_uses_manifest_description_when_reader_copy_is_null(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:112])
        content={'blocks':[],'extra':{'_reader':{'description':None,'source_manifest':{'post':{'description':'Description'},'resources':[]}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT content->'blocks' FROM publications WHERE id='p'").fetchone()[0]==[{'type':'text','role':'description','text':'Description'}]
