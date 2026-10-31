import json
from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_publication_replace_preserves_processor_failure_history(tmp_path,postgres_url):
    from oko.collection.contracts import Material,Publication
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo();publication=Publication('p','https://example.test/p',1)
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(publication)],'{}');store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        file='00000000-0000-4000-8000-000000000001'
        with transaction(store.db):
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'image:1','https://example.test/file','file','unavailable')",(file,item))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media',%s,'failed','original failure','processor-config') RETURNING id",(file,)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
            before=store.problems.processor_current()['id']
            described=StructuredSource(None).describe(publication,Material('New','New text'))
            _,effects=store.collect_material(item,described)
            store.jobs.apply_collection(effects)
        saved=store.db.execute('SELECT finished,body FROM reader_problems WHERE id=%s',(before,)).fetchone()
        body=json.loads(saved['body'])
        assert saved['finished'] is not None
        assert body.get('reason')=='original failure'
        print('recorded objects',body['objects'],'publication',item)
        assert any(obj.get('id')==item for obj in body['objects']),body
    finally:store.db.close()


def test_publication_capture_batches_1001_processor_media_jobs(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','rss','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p','image-'||n,'synthetic','file','pending_download' FROM generate_series(1,1001) n")
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) SELECT 'download_media','m-'||n,'failed','x','processor-config',true FROM generate_series(1,1001) n RETURNING id").fetchall()
        queries=[];store.db.set_trace_callback(queries.append)
        try:
            with pytest.raises(RuntimeError):
                with transaction(store.db):
                    store.problems.capture_processor_histories([row[0] for row in rows],1)
                    assert len(store.problems._processor_transaction_captures)==1001
                    raise RuntimeError('rollback')
        finally:store.db.set_trace_callback(None)
        assert not store.problems._processor_transaction_captures
        grouped=[q for q in queries if 'SELECT jobs.id,jobs.kind' in q]
        assert len(grouped)==3,len(grouped)
    finally:store.db.close()


def test_publication_capture_ignores_10000_unrelated_visible_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) SELECT 'read','foreign-'||n,'failed','x','processor-config',true FROM generate_series(1,10000) n")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('read','selected','failed','x','processor-config',true) RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE jobs')
        queries=[];store.db.set_trace_callback(queries.append)
        try:
            with transaction(store.db):store.problems.capture_processor_histories([job],1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in queries if 'SELECT jobs.id,jobs.kind' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,([job],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_publish_detach_last_root_preserves_original_processor_history(tmp_path,postgres_url):
    from oko.collection.contracts import Material,Publication
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo();publication=Publication('root','https://example.test/root',1)
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(publication)],'{}');store.jobs.apply_collection(effects)
        root=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/root'").fetchone()[0]
        file='00000000-0000-4000-8000-000000000001'
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed) VALUES ('child','other_web','https://child.test','https://child.test',0)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'child')",(root,))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,'child','image:1','https://example.test/file','file','unavailable')",(file,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media',%s,'failed','original failure','processor-config') RETURNING id",(file,)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
            before=store.problems.processor_current()['id']
            assert store.problems.processor_configuration_candidate(1)['objects']==[{'kind':'material','id':root}]
            described=StructuredSource(None).describe(publication,Material('New','New text'))
            _,effects=store.collect_material(root,described)
            store.jobs.apply_collection(effects)
        saved=store.db.execute('SELECT finished,body FROM reader_problems WHERE id=%s',(before,)).fetchone();body=json.loads(saved['body'])
        assert saved['finished'] is not None
        assert any(obj.get('id')==root for obj in body['objects']),body
    finally:store.db.close()


def test_link_detach_preserves_processor_old_root_in_history(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url) VALUES ('root',0,'rss','root','https://root'),('target',0,'rss','target','https://target')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','target')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('00000000-0000-4000-8000-000000000001','target','image:1','https://file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','00000000-0000-4000-8000-000000000001','failed','original failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        before=store.problems.processor_current()['id']
        before_candidate=store.problems.processor_configuration_candidate(2)
        assert any(obj.get('id')=='root' for obj in before_candidate['objects'])
        import pytest
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                failed=CollectionEffects()
                publication_links.sync(store,'root',(),failed)
                store.jobs.apply_collection(failed)
                raise RuntimeError('rollback')
        assert not store.problems._processor_transaction_captures
        assert store.db.execute("SELECT 1 FROM publication_links WHERE source_publication_id='root' AND target_publication_id='target'").fetchone()
        assert store.problems.processor_current()['id']==before
        with transaction(store.db):
            effects=CollectionEffects()
            publication_links.sync(store,'root',(),effects)
            store.jobs.apply_collection(effects)
        saved=store.db.execute('SELECT finished,body FROM reader_problems WHERE id=%s',(before,)).fetchone()
        assert saved['finished'] is not None
        body=json.loads(saved['body'])
        assert any(obj.get('id')=='root' for obj in body['objects']),body
    finally:store.db.close()


def test_two_root_detach_one_transaction_preserves_both_history_objects(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url) VALUES ('r1',0,'rss','r1','https://r1'),('r2',0,'rss','r2','https://r2'),('target',0,'rss','target','https://target')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','r1',true),('f','r2',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('r1','target'),('r2','target')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('00000000-0000-4000-8000-000000000001','target','image:1','https://file','file','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','00000000-0000-4000-8000-000000000001','failed','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        old=store.problems.processor_current()['id']
        assert {obj['id'] for obj in store.problems.processor_configuration_candidate(2)['objects']}=={'r1','r2'}
        with transaction(store.db):
            effects=CollectionEffects()
            publication_links.sync(store,'r1',(),effects)
            publication_links.sync(store,'r2',(),effects)
            store.jobs.apply_collection(effects)
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert row['finished'] is not None
        assert {obj['id'] for obj in json.loads(row['body'])['objects']}=={'r1','r2'}
    finally:store.db.close()


def test_unchanged_links_do_not_capture_processor_job(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed,platform,external_id,url) VALUES ('root',0,'rss','root','https://root'),('target',0,'other_web','https://target','https://target')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','target')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','target','failed','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        ref=ExternalReference(ExternalKey(Platform.other_web,'https://target'),'https://target')
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with transaction(store.db):assert not publication_links.sync(store,'root',(ref,),CollectionEffects())
        finally:store.db.set_trace_callback(None)
        assert not any('SELECT jobs.id,jobs.kind' in q for q in sql)
    finally:store.db.close()
