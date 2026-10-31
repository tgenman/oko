import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.youtube_admission_jobs import contributions

@pytest.mark.parametrize(('code','error','owned','expected'),[
    (None,None,False,False),('',None,False,True),('work-fetch','failure',False,True),
    ('youtube-admission','failure',False,True),('other','failure',False,False),
    ('source-network','failure',False,True),('source-network','failure',True,False),
])
def test_addressed_fetch_projection_matches_status(tmp_path,postgres_url,code,error,owned,expected):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,source_event_owned) VALUES ('fetch','p','pending',%s,%s,%s::integer::boolean) RETURNING id",(error,code,owned)).fetchone()[0]
            store.youtube.blocked(100,900)
        admission=next(p for p in store.problems.candidates(101) if p['code']=='youtube-admission')
        objects=contributions(store,[job]).get(job,set())
        assert ('p' in objects) is expected
        assert objects=={o['id'] for o in admission['objects']}
    finally:store.db.close()


@pytest.mark.parametrize(('code','error','owned','expected'),[
    (None,None,False,False),(None,'failure',True,True),('',None,False,True),
    ('ordinary','failure',False,True),('processor-config','failure',False,False),
    ('source-network','failure',False,True),('source-network','failure',True,False),
])
def test_addressed_subtitles_projection_matches_status(tmp_path,postgres_url,code,error,owned,expected):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,source_event_owned) VALUES ('download_media','m','pending',%s,%s,%s::integer::boolean) RETURNING id",(error,code,owned)).fetchone()[0]
            store.youtube.blocked(100,900)
        admission=next(p for p in store.problems.candidates(101) if p['code']=='youtube-admission')
        objects=contributions(store,[job]).get(job,set())
        assert ('p' in objects) is expected
        assert objects=={o['id'] for o in admission['objects']}
    finally:store.db.close()


def test_addressed_projection_preserves_multiple_linked_roots(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('child')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','a',true),('yt','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','child'),('b','child')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','child','pending','failure','') RETURNING id").fetchone()[0]
            store.youtube.blocked(100,900)
        expected={o['id'] for o in next(p for p in store.problems.candidates(101) if p['code']=='youtube-admission')['objects']}
        assert contributions(store,[job])[job]==expected=={'a','b'}
    finally:store.db.close()


def test_contributor_probe_ignores_ten_thousand_foreign_jobs(tmp_path,postgres_url,monkeypatch):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','pending','failure','') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','foreign-'||n,'failed','failure','youtube-admission' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        captured=[];original=store.db.execute
        def trace(sql,params=()):
            if sql.startswith('SELECT j.id,CASE WHEN affected_publication'):captured.append((sql,params))
            return original(sql,params)
        monkeypatch.setattr(store.db,'execute',trace)
        assert contributions(store,[job])=={job:{'p'}}
        sql,params=captured[0]
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,params).fetchone()[0][0]['Plan']
        print('contributor scan rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks',0))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_flags_recovery_is_restartable_and_ignores_foreign_history(tmp_path,postgres_url,monkeypatch):
    from oko.storage.youtube_admission_jobs import recover_flags
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'yt','p-'||n,true FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','p-'||n,'pending','failure','' FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch','old-'||n,'done' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        sql="SELECT id FROM jobs WHERE kind IN ('fetch','download_media') AND status IN ('pending','running') ORDER BY id LIMIT 100"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<=101
        def fault(name):
            if name=='youtube_admission_jobs_recovery_batch':raise RuntimeError('interrupted')
        monkeypatch.setattr(store,'fault',fault)
        with pytest.raises(RuntimeError,match='interrupted'):recover_flags(store)
        assert not store.db.execute('SELECT youtube_admission_recovered FROM youtube_access WHERE id=1').fetchone()[0]
        monkeypatch.setattr(store,'fault',lambda _:None)
        recover_flags(store)
        assert store.db.execute("SELECT count(*) FROM jobs WHERE youtube_admission_candidate").fetchone()[0]==101
        assert store.db.execute('SELECT youtube_admission_recovered FROM youtube_access WHERE id=1').fetchone()[0]
    finally:store.db.close()


def test_flag_updates_roll_back_with_job(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import sync_ids
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','') RETURNING id").fetchone()[0]
            sync_ids(store,[job])
        assert store.db.execute('SELECT youtube_admission_candidate FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.db.execute("UPDATE jobs SET status='done' WHERE id=%s",(job,))
                sync_ids(store,[job])
                raise RuntimeError('rollback')
        assert tuple(store.db.execute('SELECT status,youtube_admission_candidate FROM jobs WHERE id=%s',(job,)).fetchone())==('pending',True)
    finally:store.db.close()


def test_flags_recovery_includes_zero_and_negative_job_ids(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import recover_flags
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','a',true),('yt','b',true)")
            store.db.execute("INSERT INTO jobs(id,kind,item,status,problem_code) VALUES (-1,'fetch','a','pending',''),(0,'fetch','b','pending','')")
        recover_flags(store)
        assert [r[0] for r in store.db.execute('SELECT id FROM jobs WHERE youtube_admission_candidate ORDER BY id')]==[-1,0]
    finally:store.db.close()


def test_indexed_group_projection_matches_status_without_foreign_jobs(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import recover_flags,group_objects
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','foreign-'||n,'failed','failure','youtube-admission' FROM generate_series(1,10000)n")
            store.youtube.blocked(100,900)
        recover_flags(store)
        expected=next(p for p in store.problems.candidates(101) if p['code']=='youtube-admission')['objects']
        assert [dict(kind='material',id=item) for item in group_objects(store)]==expected
        store.db._db.execute('ANALYZE jobs')
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT id FROM jobs WHERE youtube_admission_candidate ORDER BY id LIMIT 100').fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_sync_changed_root_includes_complete_and_linked_material_jobs(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import sync_items,group_objects
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,complete,incomplete_class) VALUES ('root',1,1),('child',1,1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','child','pending','')")
            assert sync_items(store,['root'])
        assert group_objects(store)==['root']
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='done' WHERE kind='fetch' AND item='child'")
            assert sync_items(store,['root'])
        assert group_objects(store)==[]
    finally:store.db.close()


def test_capture_before_detach_clears_old_contribution_with_rollback(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import capture_items,sync_captured,sync_ids,group_objects
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('root'),('child')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','child','pending','') RETURNING id").fetchone()[0]
            sync_ids(store,[job])
        assert group_objects(store)==['root']
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                capture_items(store,['root'])
                store.db.execute("DELETE FROM publication_links WHERE source_publication_id='root'")
                capture_items(store,['root'])
                assert sync_captured(store)
                assert group_objects(store)==[]
                raise RuntimeError('rollback')
        assert group_objects(store)==['root']
        with transaction(store.db):
            capture_items(store,['root'])
            store.db.execute("DELETE FROM publication_links WHERE source_publication_id='root'")
            capture_items(store,['root'])
            assert sync_captured(store)
        assert group_objects(store)==[]
    finally:store.db.close()


def test_capture_remembers_contributor_after_job_deletion(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import capture_items,sync_captured,sync_ids,group_objects
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','') RETURNING id").fetchone()[0]
            sync_ids(store,[job])
        assert group_objects(store)==['p']
        with transaction(store.db):
            capture_items(store,['p'])
            store.db.execute('DELETE FROM jobs WHERE id=%s',(job,))
            assert sync_captured(store)
        assert group_objects(store)==[]
    finally:store.db.close()


def test_capture_large_affected_graph_is_streamed(tmp_path,postgres_url,monkeypatch):
    from oko.storage.youtube_admission_jobs import capture_items
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('root')")
            store.db.execute("INSERT INTO publications(id) SELECT 'child-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'root','child-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) SELECT 'fetch','child-'||n,'pending','' FROM generate_series(1,10000)n")
        pages=[];original=store.db.incomplete_row_pages
        def tracked(sql,params=()):
            for rows in original(sql,params):
                pages.append(len(rows));yield rows
        monkeypatch.setattr(store.db,'incomplete_row_pages',tracked)
        with transaction(store.db):
            capture_items(store,['root'])
            assert store.db.execute('SELECT count(*) FROM pg_temp.oko_youtube_admission_work').fetchone()[0]==10000
        assert sum(pages)==10000 and max(pages)<=100
    finally:store.db.close()


@pytest.mark.parametrize(('item_owner','media_owner','expected'),[('youtube','rss',True),('rss','youtube',False)])
def test_media_id_collision_uses_same_owner_precedence_as_status(tmp_path,postgres_url,item_owner,media_owner,expected):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    with store.db:store.db.execute("UPDATE youtube_access SET youtube_admission_recovered=false WHERE id=1")
    try:
        store.youtube_admission_ready=False
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[]),('rss','rss','RSS','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('m'),('owner')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES (%s,'m',true),(%s,'owner',true)",('yt' if item_owner=='youtube' else 'rss','yt' if media_owner=='youtube' else 'rss'))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','owner','youtube:subtitles','x','file','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','pending','bad','ordinary') RETURNING id").fetchone()[0]
            store.youtube.blocked(100,900)
        actual=contributions(store,[job]).get(job,set())
        expected_objects={o['id'] for o in next(p for p in store.problems.candidates(101) if p['code']=='youtube-admission')['objects']}
        assert bool(actual) is expected
        assert actual==expected_objects
    finally:store.db.close()


def test_delta_probe_uses_index_with_ten_thousand_other_contributors(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,youtube_admission_candidate,youtube_admission_objects) SELECT 'fetch','p-'||n,'pending',true,jsonb_build_array('p-'||n) FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        sql='''SELECT v.item,EXISTS(SELECT 1 FROM jobs j WHERE j.youtube_admission_candidate AND j.youtube_admission_objects @> jsonb_build_array(v.item)) AS present FROM unnest(%s::text[]) v(item)'''
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(['p-1'],)).fetchone()[0][0]['Plan']
        print('delta probe rows:',scan_rows(plan),'buffers:',plan.get('Shared Hit Blocks',0))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_unknown_probe_ignores_ten_thousand_valid_contributors(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,youtube_admission_candidate,youtube_admission_objects) SELECT 'fetch','p-'||n,'pending',true,jsonb_build_array('p-'||n) FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        sql='SELECT 1 FROM jobs WHERE youtube_admission_unknown LIMIT 1'
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql).fetchone()[0][0]['Plan']
        print('unknown probe rows:',scan_rows(plan),'buffers:',plan.get('Shared Hit Blocks',0))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_status_generic_query_excludes_ten_thousand_admission_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','foreign-'||n,'failed','failure','youtube-admission' FROM generate_series(1,10000)n")
            store.youtube.blocked(100,900)
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.snapshot(101)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT jobs . *'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        print('status generic rows:',scan_rows(plan),'buffers:',plan.get('Shared Hit Blocks',0))
        assert scan_rows(plan)<100
    finally:store.db.close()


@pytest.mark.parametrize(('code','owned','ambiguous','expected'),[(None,True,False,False),('ordinary',False,True,True),('ordinary',False,False,False)])
def test_real_080_fetch_flags_match_status_membership(tmp_path,postgres_url,code,owned,ambiguous,expected):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    store.youtube_admission_ready=False
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,fetch_null_owned,fetch_null_ambiguous) VALUES ('fetch','p','pending','failure',%s,%s::int::boolean,%s::int::boolean) RETURNING id",(code,owned,ambiguous)).fetchone()[0]
            store.youtube.blocked(100,900)
        previous={o['id'] for o in next(p for p in store.problems.candidates(101) if p['code']=='youtube-admission')['objects']}
        assert ('p' in contributions(store,[job]).get(job,set())) is expected
        assert contributions(store,[job]).get(job,set())==previous
    finally:store.db.close()


def test_feed_digest_collision_keeps_exact_job_boundary(tmp_path,postgres_url):
    from psycopg import sql
    from oko.storage.youtube_admission_jobs import capture_feed
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','youtube','rss','connected',ARRAY[]::text[]),('b','b','B','default','youtube','rss','connected',ARRAY[]::text[])")
            ids=[r['id'] for r in store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,problem_code) VALUES ('fetch','p-a','a','pending',''),('fetch','p-b','b','pending','') RETURNING id")]
        with transaction(store.db):
            schema=store.db._db.execute('SELECT current_schema()').fetchone()[0]
            store.db._db.execute(sql.SQL("CREATE FUNCTION {}.md5(text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT 'collision'::text $$").format(sql.Identifier(schema)))
            store.db._db.execute("SELECT set_config('search_path',%s,true)",(f'"{schema}",pg_catalog',))
            assert store.db._db.execute("SELECT md5('a')=md5('b')").fetchone()[0]
            capture_feed(store,'a')
            assert [r[0] for r in store.db.execute('SELECT job_id FROM pg_temp.oko_youtube_admission_work')]==[ids[0]]
    finally:store.db.close()


def test_feed_capture_index_ignores_ten_thousand_other_feeds(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) SELECT 'fetch','p-'||n,'foreign-'||n,'pending' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','target','yt','pending')")
            store.db._db.execute('ANALYZE jobs')
        query="SELECT id FROM jobs WHERE feed_id IS NOT NULL AND md5(feed_id)=md5(%s) AND feed_id=%s AND kind IN ('fetch','download_media') AND (status IN ('pending','running') OR youtube_admission_candidate)"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,('yt','yt')).fetchone()[0][0]['Plan']
        print('feed capture rows',scan_rows(plan),'buffers',plan.get('Shared Hit Blocks',0))
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_capture_items_avoids_ten_thousand_foreign_jobs(tmp_path,postgres_url,monkeypatch):
    from oko.storage.youtube_admission_jobs import capture_items
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','')")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) SELECT 'fetch','foreign-'||n,'pending','' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        captured=[];original=store.db.execute
        def trace(sql,params=()):
            if sql.startswith('SELECT DISTINCT id FROM ('):captured.append((sql,params))
            return original(sql,params)
        monkeypatch.setattr(store.db,'execute',trace)
        with transaction(store.db):
            capture_items(store,['p'])
            sql,params=captured[0]
            plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,params).fetchone()[0][0]['Plan']
            print('capture item rows:',scan_rows(plan),'buffers:',plan.get('Shared Hit Blocks',0))
            assert scan_rows(plan)<100
    finally:store.db.close()
