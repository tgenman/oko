import json,uuid
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage.transaction import transaction


def test_media_accept_changed_identity_closes_original_failure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'media-too-large','original failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        record=store.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert record and json.loads(record['body'])['objects']==[{'kind':'material','id':'p'}]
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.finish(Job(job,'download_media',old_media,1))
        store.problems.snapshot(store.wall_clock())
        row=store.db.execute('SELECT finished,outcome,body FROM reader_problems WHERE id=%s',(record['id'],)).fetchone()
        assert row['finished'] is not None and row['outcome']=='resolved',dict(row)
        assert json.loads(row['body'])['objects']==[{'kind':'material','id':'p'}]
    finally:store.db.close()


def test_media_accept_same_identity_closes_original_failure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=old_media
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'media-too-large','original failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        record=store.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert record and json.loads(record['body'])['objects']==[{'kind':'material','id':'p'}]
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.finish(Job(job,'download_media',old_media,1))
        store.problems.snapshot(store.wall_clock())
        row=store.db.execute('SELECT finished,outcome,body FROM reader_problems WHERE id=%s',(record['id'],)).fetchone()
        assert row['finished'] is not None and row['outcome']=='resolved',dict(row)
        assert json.loads(row['body'])['objects']==[{'kind':'material','id':'p'}]
    finally:store.db.close()


def test_media_accept_changed_identity_and_problem_roll_back_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'media-too-large','original failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        before=store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.media.accept(old_media,download,download.media,(new_media,download.final,9))
                store.jobs.finish(Job(job,'download_media',old_media,1))
                assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()[0] is not None
                raise ValueError('rollback')
        assert store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(old_media,)).fetchone()
        assert not store.db.execute('SELECT 1 FROM media_files WHERE id=%s',(new_media,)).fetchone()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='running'
        assert tuple(store.db.execute('SELECT id,identity,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone())==tuple(before)
    finally:store.db.close()


def test_saved_media_finish_lookup_is_indexed_with_unrelated_errors(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p-'||n,'job:other:'||n,'background',jsonb_build_object('operation','producer:download_media','media_job_id',n::text,'objects',jsonb_build_array(jsonb_build_object('kind','material','id','m-'||n)))::text,1 FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE reader_problems')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.saved_media_candidates([1])
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if q.startswith('SELECT identity,body FROM reader_problems'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(['1'],)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_media_accept_negative_job_id_closes_original_failure(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(id,kind,item,status,generation,problem_code,error) VALUES (-1,'download_media',%s,'running',1,'media-too-large','original failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
        record=store.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert record and json.loads(record['body'])['objects']==[{'kind':'material','id':'p'}]
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.finish(Job(job,'download_media',old_media,1))
        store.problems.snapshot(store.wall_clock())
        row=store.db.execute('SELECT finished,outcome,body FROM reader_problems WHERE id=%s',(record['id'],)).fetchone()
        assert row['finished'] is not None and row['outcome']=='resolved',dict(row)
        assert json.loads(row['body'])['objects']==[{'kind':'material','id':'p'}]
    finally:store.db.close()




def test_media_accept_preserves_unverified_extra_material(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    old_media=str(uuid.uuid4());new_media=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':old_media}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(old_media,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error) VALUES ('download_media',%s,'running',1,'media-too-large','original failure') RETURNING id",(old_media,)).fetchone()[0]
            store.problems.sync_media_problem(job,[],1)
            record=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
            body=json.loads(record['body']);body['objects'].append(dict(kind='material',id='unrelated'))
            store.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),record['id']))
        download=store.media.begin(old_media,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        with transaction(store.db):
            store.media.accept(old_media,download,download.media,(new_media,download.final,9))
            store.jobs.finish(Job(job,'download_media',old_media,1))
        row=store.db.execute('SELECT finished,body FROM reader_problems WHERE id=%s',(record['id'],)).fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_ordinary_media_accept_sql_count(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    media_id=str(uuid.uuid4())
    try:
        content={'blocks':[{'type':'media','role':'image','media_id':media_id}],'extra':{'_reader':{'full':True,'reason':'','attachments':[]}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url,content) VALUES ('p',0,'rss','p','https://p.test',%s)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key) VALUES (%s,'p','image','x','image','ready','media/old')",(media_id,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media',%s,'running',1) RETURNING id",(media_id,)).fetchone()[0]
        download=store.media.begin(media_id,'attempt',True)
        download.final.parent.mkdir(parents=True,exist_ok=True);download.final.write_bytes(b'synthetic')
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with transaction(store.db):
                store.media.accept(media_id,download,download.media,(media_id,download.final,9))
                store.jobs.finish(Job(job,'download_media',media_id,1))
        finally:store.db.set_trace_callback(None)
        print('ordinary media accept SQL:',len(sql))
    finally:store.db.close()
