from tests.support.collection_acceptance import apply_simulated_collection, running_collection_job
import json
from oko.collection.adapters import AdapterIssue,AdapterErrorKind
from oko.domain.job import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store, ensure_fixture


def test_media_failure_and_finish_without_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','file','running',1) RETURNING id").fetchone()[0]
            store.jobs.fail_media(Job(job,'download_media','file',1),AdapterIssue('media-too-large','failure',AdapterErrorKind.permanent))
            row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:media-too-large:feed:download_media:file' AND finished IS NULL").fetchone()
            assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
            store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job,))
            store.jobs.finish(Job(job,'download_media','file',2))
            assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_two_files_on_one_publication_keep_separate_errors(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('one','article','image:1','https://example.test/1','one','pending_download'),('two','article','image:2','https://example.test/2','two','pending_download')")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','one','running',1),('download_media','two','running',1) RETURNING id,item").fetchall()
            for row in jobs:store.jobs.fail_media(Job(row['id'],'download_media',row['item'],1),AdapterIssue('media-too-large','failure',AdapterErrorKind.permanent))
            rows=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL ORDER BY identity").fetchall()
            assert [row[0] for row in rows]==['job:media-too-large:feed:download_media:one','job:media-too-large:feed:download_media:two']
            first=jobs[0];store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(first['id'],));store.jobs.finish(Job(first['id'],'download_media',first['item'],2))
            assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:download_media' AND finished IS NULL").fetchone()[0]==1
    finally:store.db.close()


def test_real_material_replacement_rolls_back_file_job_and_problem(tmp_path,postgres_url):
    import pytest
    from tests.support.legacy_source import Publication
    from tests.support.synthetic_material import material as Material
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        ensure_fixture(store.db);publication=Publication('p','https://example.test/p',1)
        with transaction(store.db):
            job_for_collection=running_collection_job(store,'discover','fixture')
            effects=store.discovery.accept([publication_packet(publication)],'{}','fixture');apply_simulated_collection(store,effects,job=job_for_collection)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        file='00000000-0000-4000-8000-000000000001'
        with store.db:
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'image:1','https://example.test/file','file','unavailable')",(file,item))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('download_media',%s,'failed','failure','media-too-large') RETURNING id",(file,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        before=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                job_for_collection=running_collection_job(store,'fetch',item)
                _,effects=store.collected_publication.accept_content(item,StructuredSource(None).describe(publication,Material('New','New text')).adapter_content)
                apply_simulated_collection(store,effects,job=job_for_collection)
                assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(file,)).fetchone() is None
                raise ValueError('rollback')
        assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(file,)).fetchone()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='failed'
        after=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_media_job_reason_tracks_failure_and_success(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','file','running',1) RETURNING id").fetchone()[0]
            store.jobs.fail_media(Job(job,'download_media','file',1),AdapterIssue('media-too-large','Too large',AdapterErrorKind.permanent))
            row=store.db.execute('SELECT reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('media-too-large','Too large')
            store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job,))
            store.jobs.finish(Job(job,'download_media','file',2))
            row=store.db.execute('SELECT reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==(None,None)
    finally:store.db.close()


def test_retry_unavailable_media_clears_reason_but_preserves_source_wait(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            deadline=store.wall_clock()+3600
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,wait_scope,retry_at,next,reason_code,reason_message) VALUES ('download_media','file','unavailable',1,'source',%s,123,'access','Restricted') RETURNING id",(deadline,)).fetchone()[0]
            store.jobs.retry_media('article',revalidate_ready=False)
            row=store.db.execute('SELECT status,generation,wait_scope,retry_at,reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('pending',2,'source',deadline,None,None)
            assert store.db.execute("SELECT status FROM media_files WHERE id='file'").fetchone()[0]=='pending_download'
    finally:store.db.close()


def test_media_diagnostics_reads_job_reason(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('download_media','file','unavailable','Current explanation','access-denied') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],store.wall_clock())
            row=store.db.execute("SELECT body FROM reader_problems WHERE identity='job:access-denied:feed:download_media:file' AND finished IS NULL").fetchone()
            assert row and json.loads(row['body'])['reason']=='Current explanation'
    finally:store.db.close()


def test_caption_defer_records_current_reason_on_job(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('video','youtube','video')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('sub','video','youtube:subtitles','','subtitles','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','sub','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','sub',1),store.clock(),60,'Source asks to wait')
            row=store.db.execute('SELECT status,reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('pending',None,'Source asks to wait')
    finally:store.db.close()


def test_revalidate_saved_file_clears_reason_without_deleting_bytes(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        saved=tmp_path/'media'/'article'/'file'
        saved.parent.mkdir(parents=True,exist_ok=True)
        saved.write_bytes(b'previous bytes')
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES ('file','article','image:1','https://example.test/file','file','ready','media/article/file')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,reason_code,reason_message) VALUES ('download_media','file','failed',4,'network','old error') RETURNING id").fetchone()[0]
            store.jobs.revalidate_media('file')
            row=store.db.execute('SELECT status,generation,reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('pending',5,None,None)
            assert store.db.execute("SELECT status,storage_key FROM media_files WHERE id='file'").fetchone()[0]=='ready'
            assert saved.read_bytes()==b'previous bytes'
    finally:store.db.close()


def test_source_change_records_media_reason_in_job_and_revokes_old_attempt(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,platform,external_id,url,kind,folder_id) VALUES ('feed','telegram','feed','https://t.me/feed','telegram','default')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','telegram','article','feed')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','file','running',4) RETURNING id").fetchone()[0]
            store.publications.changed_adapter('feed')
            row=store.db.execute('SELECT status,generation,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('unavailable',5,'Обновляется описание вложения после смены доступа')
            file=store.db.execute("SELECT status FROM media_files WHERE id='file'").fetchone()
            assert tuple(file)==('unavailable',)
            store.db.execute("UPDATE media_files SET status='pending_download' WHERE id='file'")
            store.jobs.schedule_media('article')
            row=store.db.execute('SELECT status,generation,reason_code,reason_message FROM jobs WHERE id=%s',(job,)).fetchone()
            assert tuple(row)==('pending',6,None,None)
    finally:store.db.close()


def test_unavailable_media_without_optional_reason_remains_visible(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','','image','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('download_media','file','unavailable') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],store.wall_clock())
            row=store.db.execute("SELECT body FROM reader_problems WHERE identity='job:work-download_media:feed:download_media:file' AND finished IS NULL").fetchone()
            assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
    finally:store.db.close()


def test_restart_preserves_unavailable_media_without_optional_reason(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db:
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
        store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','','image','unavailable')")
        job=store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('download_media','file','unavailable') RETURNING id").fetchone()[0]
        store.problems.sync_media_problem(job,[],store.wall_clock())
    store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        rows=store.problems.snapshot(store.wall_clock())['current']
        assert any(p['code']=='work-download_media' and any(o['id']=='article' for o in p['objects']) for p in rows)
    finally:store.db.close()


def test_retired_job_cleanup_rejects_new_generation_or_restored_target(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','','image','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES ('download_media','file','cancelled',2,%s::jsonb) RETURNING id",(json.dumps({'retired_media_key':'media/article/file'}),)).fetchone()[0]
            store.jobs.finish_retired_media_cleanup(Job(job,'download_media','file',2),'media/article/file')
            assert store.db.execute('SELECT id FROM jobs WHERE id=%s',(job,)).fetchone()
            store.db.execute("DELETE FROM media_files WHERE id='file'")
            store.db.execute('UPDATE jobs SET generation=3 WHERE id=%s',(job,))
            store.jobs.finish_retired_media_cleanup(Job(job,'download_media','file',2),'media/article/file')
            assert store.db.execute('SELECT id FROM jobs WHERE id=%s',(job,)).fetchone()
            store.jobs.finish_retired_media_cleanup(Job(job,'download_media','file',3),'media/article/file')
            assert store.db.execute('SELECT id FROM jobs WHERE id=%s',(job,)).fetchone() is None
    finally:store.db.close()
