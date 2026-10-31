import json
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.domain.collection import CollectionEffects


def test_removed_and_cancelled_media_keep_aggregate_processor_history(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed,content) VALUES ('p','rss','p','https://p.test',0,%s)",(json.dumps({'blocks':[], 'extra':{'_reader':{'title':'P'}}}),))
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed) VALUES ('root-a','rss','ra','https://ra',0),('root-b','rss','rb','https://rb',0)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root-a','p'),('root-b','p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('a','p','image:a','x','a','unavailable'),('b','p','image:b','y','b','unavailable')")
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','a','failed','a failure','processor-config'),('download_media','b','running','b failure','processor-config') RETURNING id").fetchall()
            store.problems.finish_processor_change([r[0] for r in rows],None,100)
        before=store.problems.processor_current()['id']
        with transaction(store.db):
            b=dict(store.db.execute("SELECT id,publication_id,resource_key,source_locator,name,mime_type,size_bytes,storage_key,status,reason,resource_identity FROM media_files WHERE id='b'").fetchone())
            effects=CollectionEffects()
            store.publications.publish('p',({'blocks':[], 'extra':{'_reader':{'title':'P'}}},[b]),'fingerprint',effects)
            store.jobs.apply_collection(effects)
        saved=store.db.execute('SELECT finished,body FROM reader_problems WHERE id=%s',(before,)).fetchone();body=json.loads(saved['body'])
        assert saved['finished'] is not None
        assert body.get('reason')=='a failure' and {o['id'] for o in body['objects']}=={'root-a','root-b'},body
    finally:store.db.close()


def test_removed_and_cancelled_media_rollback(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed,content) VALUES ('p','rss','p','https://p.test',0,%s)",(json.dumps({'blocks':[], 'extra':{'_reader':{'title':'P'}}}),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('a','p','image:a','x','a','unavailable'),('b','p','image:b','y','b','unavailable')")
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','a','failed','a failure','processor-config'),('download_media','b','running','b failure','processor-config') RETURNING id").fetchall()
            store.problems.finish_processor_change([r[0] for r in rows],None,100)
        before=store.problems.processor_current()['id']
        import pytest
        with pytest.raises(RuntimeError,match='rollback'), transaction(store.db):
            b=dict(store.db.execute("SELECT id,publication_id,resource_key,source_locator,name,mime_type,size_bytes,storage_key,status,reason,resource_identity FROM media_files WHERE id='b'").fetchone())
            effects=CollectionEffects()
            store.publications.publish('p',({'blocks':[], 'extra':{'_reader':{'title':'P'}}},[b]),'fingerprint',effects)
            store.jobs.apply_collection(effects)
            raise RuntimeError('rollback')
        assert store.db.execute("SELECT count(*) FROM media_files WHERE id IN ('a','b')").fetchone()[0]==2
        assert store.problems.processor_current()['id']==before
        assert not store.problems._processor_transaction_captures
    finally:store.db.close()


def test_large_media_group_capture_is_batched_and_unrelated_jobs_are_indexed(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed,content) VALUES ('p','rss','p','https://p.test',0,%s)",(json.dumps({'blocks':[], 'extra':{'_reader':{'title':'P'}}}),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||lpad(n::text,4,'0'),'p','image:'||n,'x','file','unavailable' FROM generate_series(1,1000) n")
            ids=[r[0] for r in store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','m-'||lpad(n::text,4,'0'),CASE WHEN n<=500 THEN 'failed' ELSE 'running' END,'failure','processor-config' FROM generate_series(1,1000) n RETURNING id")]
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch','other-'||n,'done' FROM generate_series(1,10000) n")
            store.problems.finish_processor_change(ids,None,100)
            occurrence=store.problems.processor_current()['id']
            store.db._db.execute('ANALYZE jobs')
        queries=[];store.db.set_trace_callback(queries.append)
        try:
            with transaction(store.db):
                media=[dict(r) for r in store.db.execute("SELECT id,publication_id,resource_key,source_locator,name,mime_type,size_bytes,storage_key,status,reason,resource_identity FROM media_files WHERE id>='m-0501' ORDER BY id")]
                effects=CollectionEffects()
                store.publications.publish('p',({'blocks':[], 'extra':{'_reader':{'title':'P'}}},media),'fingerprint',effects)
                store.jobs.apply_collection(effects)
        finally:store.db.set_trace_callback(None)
        saved=store.db.execute('SELECT finished,body FROM reader_problems WHERE id=%s',(occurrence,)).fetchone()
        body=json.loads(saved['body'])
        assert saved['finished'] is not None and body.get('reason')=='failure'
        assert {obj['id'] for obj in body['objects']}=={'p'}
        grouped=[q for q in queries if 'SELECT jobs.id,jobs.kind' in q]
        print('processor group queries:',len(grouped))
        assert len(grouped)<=8
        cleanup=next(q for q in queries if q.startswith('SELECT j.id FROM jobs j WHERE (j.processor_visible'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+cleanup).fetchone()[0][0]['Plan']
        print('cleanup rows examined after event:',scan_rows(plan))
        assert scan_rows(plan)<5000
    finally:store.db.close()
