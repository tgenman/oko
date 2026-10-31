import json
import pytest
from tests.support.storage import Store
from tests.support.postgres_work import scan_rows
from oko.storage.incomplete_group_draft import group_key,save_keys,UnknownIncompleteGroup


def test_key_respects_sticky_group_caption_and_colons():
    def doc(caption=None):return json.dumps({'extra':{'_reader':{'full':False,'captions':caption}}})
    assert group_key('item','feed',doc())=='incomplete:feed:item'
    assert group_key('item','feed',doc('missing'))=='incomplete:feed:missing'
    assert group_key('other','feed',doc('missing'))=='incomplete:feed:missing'
    assert group_key('item','feed:a',doc())=='incomplete:feed:a:item'
    assert group_key('a:item','feed',doc())=='incomplete:feed:a:item'
    assert group_key('item',None,doc()) is None
    with pytest.raises(UnknownIncompleteGroup):group_key('item','feed',json.dumps({'extra':{'_reader':{'full':{'bad':1}}}}))


def test_index_accesses_only_selected_group(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db._db.execute('CREATE INDEX synthetic_incomplete_group ON publications(incomplete_group_key,id) WHERE incomplete_group_key IS NOT NULL')
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0 FROM generate_series(1,10000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,10000)n")
            for start in range(1,10001,100):save_keys(store.db,['p-'+str(n) for n in range(start,start+100)])
            store.db._db.execute('ANALYZE publications')
        query="SELECT id FROM publications WHERE incomplete_group_key=%s ORDER BY id"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,('incomplete:f:p-9000',)).fetchone()[0][0]['Plan']
        print('rows touched:',scan_rows(plan),'buffers:',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_group_recovery_batches_restart_and_preserves_unknown(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import recover_group_keys
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='experimental-incomplete-groups-v1'")
            store.db._db.execute('CREATE INDEX synthetic_incomplete_pending_group ON publications(id) WHERE viewed=0 AND complete=1 AND NOT source_deleted AND incomplete_class IS DISTINCT FROM 1 AND NOT incomplete_group_ready')
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0 FROM generate_series(1,201)n",(raw,))
            unknown=json.dumps({'extra':{'_reader':{'full':{'unexpected':True}}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('unknown','rss','unknown',0,1,%s,2)",(unknown,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f',id,true FROM publications")
        before=[tuple(r) for r in store.db.execute('SELECT id,content FROM publications ORDER BY id')]
        store.fault=lambda point: (_ for _ in ()).throw(RuntimeError('interrupted')) if point=='experimental_incomplete_group_batch' else None
        with pytest.raises(RuntimeError,match='interrupted'):recover_group_keys(store)
        assert store.db.execute('SELECT count(*) FROM publications WHERE incomplete_group_ready').fetchone()[0]==100
        assert store.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-groups-v1'").fetchone() is None
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        recover_group_keys(store)
        assert store.db.execute('SELECT count(*) FROM publications WHERE incomplete_group_ready').fetchone()[0]==202
        row=store.db.execute("SELECT content,incomplete_group_unknown,incomplete_group_key FROM publications WHERE id='unknown'").fetchone()
        assert tuple(row)==(unknown,True,None)
        assert [tuple(r) for r in store.db.execute('SELECT id,content FROM publications ORDER BY id')]==before
        assert store.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-groups-v1'").fetchone()
    finally:store.db.close()


def test_group_projection_matches_legacy_including_sticky_wait(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import group_candidate
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:store.db.execute("DELETE FROM state WHERE key='experimental-incomplete-problems-v1'")
        with store.db:
            store.db._db.execute('CREATE INDEX synthetic_incomplete_group ON publications(incomplete_group_key,id) WHERE incomplete_group_key IS NOT NULL')
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            for item in ['a','b']:
                raw=json.dumps({'extra':{'_reader':{'full':False,'captions':'missing','reason':'partial'}}})
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES (%s,'rss',%s,0,1,%s,0)",(item,item,raw))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f',%s,true)",(item,))
            save_keys(store.db,['a','b'])
        key='incomplete:f:missing'
        initial=next(c for c in store.problems.candidates(1) if c['id']==key)
        assert group_candidate(store,key,1)==initial
        store.problems.snapshot(1)
        with store.db:store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','a','pending',NULL,NULL)")
        waiting=next(c for c in store.problems.candidates(2) if c['id']==key)
        assert group_candidate(store,key,2)==waiting
        assert waiting['state']=='retrying' and {o['id'] for o in waiting['objects']}=={'a','b'}
    finally:store.db.close()


@pytest.mark.parametrize('scenario',['generic_failure','fresh_wait','sticky_wait','youtube_pause','caption_wait','collision'])
def test_group_projection_matches_legacy_transitions(tmp_path,postgres_url,scenario):
    from oko.storage.incomplete_group_draft import group_candidate
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:store.db.execute("DELETE FROM state WHERE key='experimental-incomplete-problems-v1'")
        with store.db:
            store.db._db.execute('CREATE INDEX synthetic_incomplete_group ON publications(incomplete_group_key,id) WHERE incomplete_group_key IS NOT NULL')
            store.db._db.execute("CREATE INDEX synthetic_incomplete_objects ON reader_problems USING gin ((body::jsonb->'objects') jsonb_path_ops) WHERE finished IS NULL AND body::jsonb->>'code'='material-incomplete'")
            feed='f:x' if scenario=='collision' else 'f'
            kind='youtube' if scenario=='youtube_pause' else 'rss'
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,'F','default',%s,'rss','connected',ARRAY[]::text[])",(feed,feed,kind))
            raw=json.dumps({'extra':{'_reader':{'full':False,'captions':None if scenario=='collision' else 'missing','reason':'partial'}}})
            items=['item'] if scenario=='collision' else ['a','b']
            for item in items:
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES (%s,%s,%s,0,1,%s,0)",(item,'youtube' if scenario=='caption_wait' else 'rss',item,raw))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES (%s,%s,true)",(feed,item))
            if scenario=='collision':
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('x:item','rss','x:item',0,1,%s,0)",(raw,))
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','x:item',true)")
                items.append('x:item')
            save_keys(store.db,items)
        key='incomplete:f:x:item' if scenario=='collision' else 'incomplete:f:missing'
        if scenario=='sticky_wait':store.problems.snapshot(100)
        with store.db:
            if scenario=='generic_failure':store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','a','failed','bad','source-network')")
            if scenario in ('fresh_wait','sticky_wait'):store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','a','pending',NULL,NULL)")
            if scenario=='caption_wait':
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','a','youtube:subtitles','x','f','pending_download')")
                store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','pending',NULL,NULL)")
                store.db.execute("UPDATE youtube_access SET next_at=200 WHERE id=1")
        if scenario=='youtube_pause':store.youtube.blocked(100,900)
        original=next((c for c in store.problems.candidates(100) if c['id']==key),None)
        assert group_candidate(store,key,100)==original
    finally:store.db.close()


def test_group_event_owns_sticky_history_and_rollback(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import group_candidate,sync_group
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db._db.execute('CREATE INDEX synthetic_incomplete_group ON publications(incomplete_group_key,id) WHERE incomplete_group_key IS NOT NULL')
            store.db._db.execute("CREATE INDEX synthetic_incomplete_isolated ON reader_problems ((body::jsonb->>'incomplete_group_identity')) WHERE finished IS NULL AND body::jsonb->>'operation'='producer:incomplete'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
        key='incomplete:f:p'
        with store.db:store.db.execute("DELETE FROM state WHERE key='experimental-incomplete-problems-v1'")
        store.problems.snapshot(1)
        with store.db:store.db.execute("INSERT INTO state(key,value) VALUES ('experimental-incomplete-problems-v1','true')")
        old=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(key,)).fetchone()[0]
        with store.db:store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','pending',NULL,NULL)")
        with transaction(store.db):sync_group(store,key,2)
        row=store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(key,)).fetchone()
        assert row['id']==old and json.loads(row['body'])['state']=='retrying'
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.db.execute("UPDATE publications SET source_deleted=true WHERE id='p'")
                sync_group(store,key,3)
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
        with transaction(store.db):
            store.db.execute("UPDATE publications SET source_deleted=true WHERE id='p'")
            sync_group(store,key,4)
        assert tuple(store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone())==(4,'irrelevant')
    finally:store.db.close()


def test_group_event_preserves_foreign_occupied_identity(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import sync_group
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db._db.execute('CREATE INDEX synthetic_incomplete_group ON publications(incomplete_group_key,id) WHERE incomplete_group_key IS NOT NULL')
            store.db._db.execute("CREATE INDEX synthetic_incomplete_isolated ON reader_problems ((body::jsonb->>'incomplete_group_identity')) WHERE finished IS NULL AND body::jsonb->>'operation'='producer:incomplete'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            save_keys(store.db,['p'])
            foreign=json.dumps({'code':'foreign','operation':'producer:status','objects':[]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','incomplete:f:p','background',%s,1)",(foreign,))
        with transaction(store.db):sync_group(store,'incomplete:f:p',2)
        row=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'incomplete_group_identity'='incomplete:f:p' AND finished IS NULL").fetchone()
        assert row and row['identity'].startswith('incomplete:isolated:')
        with transaction(store.db):sync_group(store,'incomplete:f:p',3)
        assert store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'incomplete_group_identity'='incomplete:f:p' AND finished IS NULL").fetchone()[0]==row['id']
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0]==foreign
    finally:store.db.close()


def test_group_1000_caption_waiters_has_bounded_queries_and_ignores_foreign_history(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import group_candidate
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db._db.execute('CREATE INDEX synthetic_incomplete_group ON publications(incomplete_group_key,id) WHERE incomplete_group_key IS NOT NULL')
            store.db._db.execute("CREATE INDEX synthetic_incomplete_objects ON reader_problems USING gin ((body::jsonb->'objects') jsonb_path_ops) WHERE finished IS NULL AND body::jsonb->>'code'='material-incomplete'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False,'captions':'missing'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key) SELECT 'p-'||n,'youtube','p-'||n,0,1,%s,0,'incomplete:f:missing' FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','f','pending_download' FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','m-'||n,'pending',NULL,NULL FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','foreign-'||n,'failed','bad','source-network' FROM generate_series(1,10000)n")
            store.db.execute("UPDATE youtube_access SET next_at=200 WHERE id=1")
            store.db._db.execute('ANALYZE jobs')
        queries=[];store.db.set_trace_callback(queries.append)
        try:result=group_candidate(store,'incomplete:f:missing',100)
        finally:store.db.set_trace_callback(None)
        assert result is None
        print('total statements',len(queries),'youtube reads',sum('SELECT * FROM youtube_access' in q for q in queries))
        assert len(queries)<50
        assert sum('SELECT * FROM youtube_access' in q for q in queries)<5
        scoped=next(q for q in queries if q.startswith('SELECT jobs . *'))
        targets=['p-'+str(n) for n in range(1,1001)]+['m-'+str(n) for n in range(1,1001)]
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+scoped,(targets,targets)).fetchone()[0][0]['Plan']
        print('scoped scan rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks'))
        assert scan_rows(plan)<5000,plan
    finally:store.db.close()


def test_thousand_independent_groups_project_in_bounded_batches(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import group_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
        queries=[];store.db.set_trace_callback(queries.append)
        try:
            result={}
            for start in range(1,1001,100):result.update(group_candidates(store,['incomplete:f:p-'+str(n) for n in range(start,start+100)],1))
        finally:store.db.set_trace_callback(None)
        assert len(result)==1000
        print('1000 groups SQL statements:',len(queries))
        assert len(queries)<100
        from oko.storage.incomplete_group_draft import sync_groups_batch
        writes=[];store.db.set_trace_callback(writes.append)
        try:
            with store.db:
                for start in range(1,1001,100):sync_groups_batch(store,['incomplete:f:p-'+str(n) for n in range(start,start+100)],1)
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete' AND finished IS NULL").fetchone()[0]==1000
        print('1000 groups sync SQL statements:',len(writes))
        assert len(writes)<200
    finally:store.db.close()


def test_thousand_foreign_identity_collisions_allocate_in_batches(tmp_path,postgres_url):
    from oko.storage.incomplete_group_draft import sync_groups_batch
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) SELECT 'p-'||n,'rss','p-'||n,0,1,%s,0,'incomplete:f:p-'||n,'f',true FROM generate_series(1,1000)n",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,1000)n")
            foreign=json.dumps({'code':'foreign','operation':'producer:status','objects':[]})
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'foreign-'||n,'incomplete:f:p-'||n,'background',%s,1 FROM generate_series(1,1000)n",(foreign,))
        queries=[];store.db.set_trace_callback(queries.append)
        try:
            with store.db:
                for start in range(1,1001,100):sync_groups_batch(store,['incomplete:f:p-'+str(n) for n in range(start,start+100)],1)
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'foreign-%%' AND body=%s AND finished IS NULL",(foreign,)).fetchone()[0]==1000
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete' AND finished IS NULL").fetchone()[0]==1000
        print('1000 collisions SQL statements:',len(queries))
        assert len(queries)<200
    finally:store.db.close()


def test_batch_problem_id_collision_retries_then_rolls_back(tmp_path,postgres_url,monkeypatch):
    import uuid
    from oko.storage.incomplete_group_draft import sync_groups_batch
    from oko.storage.transaction import transaction
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_owner,incomplete_group_ready) VALUES ('p','rss','p',0,1,%s,0,'incomplete:f:p','f',true)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,'foreign','background',%s,1)",('0'*32,json.dumps({'code':'foreign','objects':[]})))
        monkeypatch.setattr(uuid,'uuid4',lambda:uuid.UUID(int=0))
        with pytest.raises(RuntimeError,match='Unable to allocate incomplete problem ID'):
            with transaction(store.db):sync_groups_batch(store,['incomplete:f:p'],1)
        assert store.db.execute("SELECT identity FROM reader_problems WHERE id=%s",('0'*32,)).fetchone()[0]=='foreign'
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:p'").fetchone()
    finally:store.db.close()
