import json,uuid
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage.transaction import transaction


def test_media_accept_changed_identity_and_problem_roll_back_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,content) VALUES ('p','rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,reason_code,reason_message) VALUES ('download_media',%s,'running',1,'media-too-large','original failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        before=store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.media.accept(old_media,download,download.media,(new_media,download.final,9))
                store.jobs.finish(Job(job,'download_media',old_media,1))
                store.jobs.retarget_completed_media(Job(job,'download_media',old_media,1),new_media,'p')
                assert store.db.execute('SELECT item FROM jobs WHERE id=%s',(job,)).fetchone()[0]==new_media
                assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()[0] is not None
                raise ValueError('rollback')
        assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(old_media,)).fetchone()
        assert not store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(new_media,)).fetchone()
        row=store.db.execute('SELECT status,item FROM jobs WHERE id=%s',(job,)).fetchone()
        assert tuple(row)==('running',old_media)
        assert tuple(store.db.execute('SELECT id,identity,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone())==tuple(before)
    finally:store.db.close()
