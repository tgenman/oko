import json
from oko.collection.adapters import AdapterIssue,AdapterErrorKind
from oko.storage.job_types import Job
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
    from oko.collection.contracts import Publication
    from tests.support.synthetic_material import material as Material
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        ensure_fixture(store.db);publication=Publication('p','https://example.test/p',1)
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(publication)],'{}','fixture');store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        file='00000000-0000-4000-8000-000000000001'
        with store.db:
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'image:1','https://example.test/file','file','unavailable')",(file,item))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media',%s,'failed','failure','media-too-large') RETURNING id",(file,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        before=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                _,effects=store.collect_material(item,StructuredSource(None).describe(publication,Material('New','New text')))
                store.jobs.apply_collection(effects)
                assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(file,)).fetchone() is None
                raise ValueError('rollback')
        assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(file,)).fetchone()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='failed'
        after=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()
