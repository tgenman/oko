"""Caption copies can disappear only after MediaFile and Job represent them."""
import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


@pytest.mark.parametrize(('caption','media_status','job_status','reason','key'),[
    ('not_requested','pending_download',None,None,None),
    ('pending','pending_download','pending',None,None),
    ('running','pending_download','running',None,None),
    ('error','unavailable','failed','caption-failed',None),
    ('unavailable','unavailable','done','subtitles_absent',None),
    ('available','ready','done',None,'media/p/subtitles'),
])
def test_caption_copy_removed_after_represented(postgres_database_url,caption,media_status,job_status,reason,key):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:113])
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m','source_url':None,'publication_url':None}],
                 'extra':{'youtube':{'captions':caption,'video':'preserved'}}}
        if caption=='available':content['extra']['youtube']['captions_digest']='a'*64
        db.execute("INSERT INTO publications(id,platform,external_id,content,media_ids) VALUES ('p','youtube','p',%s::jsonb,'[\"m\"]')",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','youtube:p:subtitles','captions',%s,%s)",(media_status,key))
        if job_status:
            db.execute("INSERT INTO jobs(kind,item,status,reason_code) VALUES ('download_media','m',%s,%s)",(job_status,reason))
        migrate(db,MIGRATIONS)
        saved=db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
        assert saved['extra']['youtube']=={'video':'preserved'}
        assert db.execute("SELECT status,storage_key FROM media_files WHERE id='m'").fetchone()==(media_status,key)
        if job_status:
            assert db.execute("SELECT status,reason_code FROM jobs WHERE item='m'").fetchone()==(job_status,reason)


def test_caption_copy_conflict_stops_before_removal(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:113])
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{'captions':'available'}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','youtube','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','youtube:p:subtitles','captions','unavailable')")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


@pytest.mark.parametrize(('caption','media_status','job_status'),[
    ('not_requested','ready',None),
    ('pending','unavailable','pending'),
    ('running','ready','running'),
    ('error','ready','failed'),
    ('error','pending_download','pending'),
    ('error','pending_download','running'),
])
def test_conflicting_media_status_stops_migration(postgres_database_url,caption,media_status,job_status):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:113])
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{'captions':caption}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','youtube','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','youtube:p:subtitles','captions',%s,%s)",(media_status,'media/p/captions' if media_status=='ready' else None))
        if job_status:
            db.execute("INSERT INTO jobs(kind,item,status) VALUES ('download_media','m',%s)",(job_status,))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_missing_subtitle_block_stops_migration(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:113])
        content={'blocks':[],'extra':{'youtube':{'captions':'available','captions_digest':'a'*64}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','youtube','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','youtube:p:subtitles','captions','ready','media/p/captions')")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content


def test_extra_subtitle_block_stops_migration(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:113])
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'wrong'},{'type':'media','role':'subtitles','media_id':'m'}],'extra':{'youtube':{'captions':'available'}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','youtube','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('m','p','youtube:subtitles','youtube:p:subtitles','captions','ready','media/p/captions')")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content
