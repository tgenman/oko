from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_youtube_null_fetch_recovery_saves_before_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','waiting',NULL) RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        row=store.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
        assert row
        store.problems.snapshot(100)
        assert tuple(row)==tuple(store.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())
    finally:store.db.close()


def test_next_at_preserves_closed_history_and_reserves_next_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','waiting',NULL)")
        store.problems.recover_youtube_null_fetch()
        first=next(p for p in store.problems.snapshot(100)['current'] if p['code']=='work-fetch')
        store.youtube.start(100)
        during=store.problems.snapshot(105)
        assert not any(p['code']=='work-fetch' for p in during['current'])
        assert any(p['id']==first['id'] for p in during['recent'])
        after=store.problems.snapshot(111)
        second=next(p for p in after['current'] if p['code']=='work-fetch')
        assert first['id']!=second['id']
    finally:store.db.close()


def test_mixed_rss_and_youtube_owner_hides_only_youtube(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('r','r','R','default','rss','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('r','a',true),('y','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','article'),('b','article')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure')")
        store.problems.recover_youtube_null_fetch()
        before=[p for p in store.problems.snapshot(100)['current'] if p['code']=='work-fetch']
        assert len(before)==2
        by_obj={p['objects'][0]['id']:p['id'] for p in before}
        store.youtube.start(100)
        during=[p for p in store.problems.snapshot(105)['current'] if p['code']=='work-fetch']
        assert [(p['objects'][0]['id'],p['id']) for p in during]==[('a',by_obj['a'])]
        after=[p for p in store.problems.snapshot(111)['current'] if p['code']=='work-fetch']
        after_ids={p['objects'][0]['id']:p['id'] for p in after}
        assert after_ids['a']==by_obj['a'] and after_ids['b']!=by_obj['b']
    finally:store.db.close()


def test_recovery_keeps_youtube_admission_contribution(tmp_path,postgres_url):
    from oko.storage.youtube_admission_jobs import contributions,sync_ids
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','pending','waiting',NULL) RETURNING id").fetchone()[0]
            before=contributions(store,[job]);assert before
        store.problems.recover_youtube_null_fetch()
        with transaction(store.db):
            assert contributions(store,[job])==before
            sync_ids(store,[job])
        row=store.db.execute('SELECT youtube_admission_candidate FROM jobs WHERE id=%s',(job,)).fetchone()
        assert row[0]
    finally:store.db.close()


def test_advance_materializes_reserved_id_and_rollback_restores_it(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','waiting',NULL)")
        store.problems.recover_youtube_null_fetch()
        store.youtube.start(100)
        clock[0]=111
        before=next(p['id'] for p in store.problems.snapshot(111)['current'] if p['code']=='work-fetch')
        try:
            with transaction(store.db):
                store.read_versions.advance(111,111)
                assert store.db.execute('SELECT youtube_fetch_due FROM reader_problems WHERE id=%s',(before,)).fetchone()[0] is None
                raise RuntimeError('rollback')
        except RuntimeError:pass
        assert store.db.execute('SELECT youtube_fetch_due FROM reader_problems WHERE id=%s',(before,)).fetchone()[0] is not None
        store.read_versions.advance(111,111)
        assert store.db.execute('SELECT youtube_fetch_due FROM reader_problems WHERE id=%s',(before,)).fetchone()[0] is None
        assert next(p['id'] for p in store.problems.snapshot(111)['current'] if p['code']=='work-fetch')==before
    finally:store.db.close()


def test_finish_after_virtual_deadline_preserves_seen_id(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','pending','waiting',NULL) RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        store.youtube.start(100)
        clock[0]=111
        seen=next(p['id'] for p in store.problems.snapshot(111)['current'] if p['code']=='work-fetch')
        with transaction(store.db):
            row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone()
            claimed=store.jobs._claim(row)
            assert claimed
            store.jobs.finish(claimed)
        after=store.problems.snapshot(111)
        assert any(p['id']==seen for p in after['recent'])
    finally:store.db.close()


def test_recovery_avoids_ten_thousand_unrelated_rss_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]'),('r','r','R','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error) VALUES ('fetch','p','f','failed','waiting')")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error) SELECT 'fetch','rss-'||n,'r','failed','waiting' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_youtube_null_fetch()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.lstrip().startswith('WITH youtube_feeds AS MATERIALIZED'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100
    finally:store.db.close()


def test_recovery_during_pause_does_not_invent_past_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        store.youtube.start(100)
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
        store.problems.recover_youtube_null_fetch()
        during=store.problems.snapshot(105)
        assert not any(p['code']=='work-fetch' for p in during['current']+during['recent'])
        assert any(p['code']=='work-fetch' for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_foreign_canonical_is_preserved_during_pause_and_resume(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        foreign=json.dumps(dict(code='foreign',operation='producer:delete',objects=[dict(kind='material',id='other')],state='current'))
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-fetch:f:fetch:p','background',%s,1)",(foreign,))
        store.problems.recover_youtube_null_fetch()
        store.youtube.start(100)
        store.problems.snapshot(105)
        store.problems.snapshot(111)
        saved=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert saved['body']==foreign and saved['finished'] is None
        assert any(p['code']=='work-fetch' for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_removing_youtube_owner_while_waiting_cancels_only_its_future(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('r','r','R','default','rss','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('r','a',true),('y','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','article'),('b','article')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','failure')")
        store.problems.recover_youtube_null_fetch()
        rss=next(p['id'] for p in store.problems.snapshot(100)['current'] if p['code']=='work-fetch' and p['objects'][0]['id']=='a')
        store.youtube.start(100)
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()
        store.remove_feed('y')
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()
        after=[p for p in store.problems.snapshot(111)['current'] if p['code']=='work-fetch']
        assert any(p['objects'][0]['id']=='a' and p['id']==rss for p in after)
        assert not any(p.get('fetch_owner')=='y' for p in after)
    finally:store.db.close()


def test_null_to_explicit_after_deadline_keeps_visible_occurrence(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting') RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch();store.youtube.start(100)
        clock[0]=111
        seen=next(p['id'] for p in store.problems.snapshot(111)['current'] if p['code']=='work-fetch')
        with transaction(store.db):
            from oko.storage.fetch_job_problems import previous_candidates
            previous=previous_candidates(store,job,111)
            store.db.execute("UPDATE jobs SET problem_code='work-fetch' WHERE id=%s",(job,))
            store.problems.sync_fetch(job,previous,111)
        current=next(p['id'] for p in store.problems.snapshot(111)['current'] if p['code']=='work-fetch')
        assert current==seen
    finally:store.db.close()


def test_pause_rolls_back_timer_occurrence_and_flags_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting') RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        before=store.db.execute('SELECT id,identity,body,finished FROM reader_problems WHERE finished IS NULL AND body::jsonb->>\'fetch_job_id\'=%s',(str(job),)).fetchall()
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.youtube.start(100)
                assert store.db.execute('SELECT 1 FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()
                raise RuntimeError('rollback')
        after=store.db.execute('SELECT id,identity,body,finished FROM reader_problems WHERE finished IS NULL AND body::jsonb->>\'fetch_job_id\'=%s',(str(job),)).fetchall()
        assert [tuple(row) for row in before]==[tuple(row) for row in after]
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()
        assert store.youtube.ready_at()<=100
    finally:store.db.close()


def test_virtual_deadline_get_is_read_only_and_id_is_stable(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
        store.problems.recover_youtube_null_fetch();store.youtube.start(100)
        before=[tuple(row) for row in store.db.execute('SELECT id,identity,body,finished,xmin::text FROM reader_problems ORDER BY id')]
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            one=store.problems.snapshot(111);two=store.problems.snapshot(111)
        finally:store.db.set_trace_callback(None)
        after=[tuple(row) for row in store.db.execute('SELECT id,identity,body,finished,xmin::text FROM reader_problems ORDER BY id')]
        assert before==after
        assert [p['id'] for p in one['current'] if p['code']=='work-fetch']==[p['id'] for p in two['current'] if p['code']=='work-fetch']
        assert not any(q.lstrip().upper().startswith(('UPDATE ','INSERT ','DELETE ')) for q in trace)
    finally:store.db.close()


def test_admission_subtitle_and_fetch_share_pause_without_hiding_each_other(tmp_path,postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.youtube_admission_jobs import sync_ids
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            fetch=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','pending','fetch error') RETURNING id").fetchone()[0]
            media=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        with transaction(store.db):
            sync_ids(store,[fetch])
            store.youtube.blocked(100,20)
            store.jobs.defer_captions(Job(media,'download_media','m',1),100,10,'subtitle error')
        before=store.problems.snapshot(100)
        assert any(p['code']=='youtube-admission' for p in before['current'])
        assert not any(p['code'] in ('work-fetch','work-download_media') for p in before['current'])
        after=store.problems.snapshot(1001)
        assert not any(p['code']=='youtube-admission' for p in after['current'])
        assert {p['code'] for p in after['current']} >= {'work-fetch','work-download_media'}
    finally:store.db.close()


def test_recovery_preserves_unambiguous_legacy_occurrence(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','failure')")
            body=dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='p')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:work-fetch:f:fetch:p','background',%s,1)",(json.dumps(body),))
        store.problems.recover_youtube_null_fetch()
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:work-fetch:f:fetch:p' AND finished IS NULL").fetchone()[0]=='legacy'
        store.youtube.start(100)
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='legacy'").fetchone()[0]==100
    finally:store.db.close()


def test_http_etag_stable_when_virtual_fetch_is_materialized(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
                store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
            store.problems.recover_youtube_null_fetch();store.youtube.start(100)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup);clock[0]=111
        before=client.get('/api/v3/status/details');assert before.status_code==200
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(now=111,wall=111))
        after=client.get('/api/v3/status/details');assert after.status_code==200
        assert before.json()==after.json()
        assert before.headers['etag']==after.headers['etag']


def test_ambiguous_ten_thousand_isolated_records_are_preserved(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','err') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='work-fetch',operation='producer:fetch',fetch_job_id=str(job),fetch_job_identity='job:work-fetch:f:fetch:p',fetch_implicit_code=True,objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:fetch-isolated:'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):store.problems.sync_fetch(job,[],100)
        finally:store.db.set_trace_callback(None)
        flags=store.db.execute('SELECT fetch_null_owned,fetch_youtube_sensitive FROM jobs WHERE id=%s',(job,)).fetchone()
        assert not flags['fetch_null_owned'] and not flags['fetch_youtube_sensitive']
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE finished IS NULL').fetchone()[0]==10000
        query=next(q for q in trace if q.startswith('SELECT r.*,target.canonical') and 'fetch_job_identity' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['job:work-fetch:f:fetch:p'],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<500
    finally:store.db.close()


def test_unknown_deadline_keeps_youtube_fetch_reserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
            store.db.execute('UPDATE youtube_access SET next_at=NULL WHERE id=1')
        store.problems.recover_youtube_null_fetch()
        reserved=store.db.execute('SELECT id,youtube_fetch_due FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()
        assert reserved and reserved['youtube_fetch_due']==float('inf')
        assert not any(p['code']=='work-fetch' for p in store.problems.snapshot(10000)['current'])
        store.youtube.start(100)
        assert any(p['id']==reserved['id'] for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_timer_waits_for_graph_before_job_rows(tmp_path,postgres_url):
    import threading
    import psycopg
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    graph=psycopg.connect(postgres_url,autocommit=False)
    done=threading.Event();errors=[]
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','pending','waiting') RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        def start():
            try:store.youtube.start(100)
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            thread=threading.Thread(target=start);thread.start()
            assert not done.wait(.3)
            graph.execute("SET LOCAL lock_timeout='500ms'")
            graph.execute("UPDATE jobs SET error='new error' WHERE id=%s",(job,))
            graph.execute("UPDATE youtube_access SET strikes=0 WHERE id=1")
        thread.join(5)
        assert done.is_set() and not errors
    finally:graph.close();store.db.close()


def test_remove_feed_during_pause_rolls_back_reservation(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','youtube','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
        store.problems.recover_youtube_null_fetch();store.youtube.start(100)
        before=tuple(store.db.execute('SELECT id,identity,body,youtube_fetch_due FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone())
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.remove_feed('f')
                raise RuntimeError('rollback')
        assert tuple(store.db.execute('SELECT id,identity,body,youtube_fetch_due FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone())==before
        assert store.db.execute("SELECT 1 FROM feed_publications WHERE feed_id='f' AND publication_id='p'").fetchone()
    finally:store.db.close()


def test_recovery_resumes_batches_without_changing_reserved_ids(tmp_path,postgres_url,monkeypatch):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        store.youtube.start(100)
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'fetch','p-'||n,'failed','waiting' FROM generate_series(1,101)n")
        old=store.fault
        def fault(name):
            if name=='youtube_null_fetch_recovery_batch':raise RuntimeError('crash')
            return old(name)
        monkeypatch.setattr(store,'fault',fault)
        with pytest.raises(RuntimeError,match='crash'):store.problems.recover_youtube_null_fetch()
        before={row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE youtube_fetch_due IS NOT NULL')}
        assert len(before)==100
        monkeypatch.setattr(store,'fault',old)
        store.problems.recover_youtube_null_fetch()
        after={row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE youtube_fetch_due IS NOT NULL')}
        assert len(after)==101 and before.issubset(after)
    finally:store.db.close()


def test_first_start_runs_080_then_youtube_recovery_while_paused(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        store.youtube.start(100)
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key IN ('fetch-null-recovered-v1','youtube-null-fetch-recovered-v1')")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('r','r','R','default','rss','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('r','a',true),('y','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','article'),('b','article')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','waiting')")
        store.problems.recover_fetch_null()
        store.problems.recover_youtube_null_fetch()
        during=store.problems.snapshot(105)
        assert [p['objects'][0]['id'] for p in during['current'] if p['code']=='work-fetch']==['a']
        assert not any(p['code']=='work-fetch' for p in during['recent'])
        after=store.problems.snapshot(111)
        assert {p['objects'][0]['id'] for p in after['current'] if p['code']=='work-fetch'}=={'a','b'}
    finally:store.db.close()


def test_status_does_not_scan_ten_thousand_future_fetch_reservations(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='work-fetch',operation='producer:fetch',objects=[]))
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,youtube_fetch_due,youtube_fetch_reserved_owned) SELECT 'r-'||n,'youtube-fetch:reservation:r-'||n,'background',%s,1,-62135596800,'irrelevant',CASE WHEN n%%2=0 THEN 1 ELSE 1000 END,(n%%2<>0) FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.snapshot(100)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT * FROM reader_problems WHERE finished IS NULL OR finished >'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(100-86400,100,100)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_085_migration_rollback_then_startup_recovers_paused_legacy(tmp_path,postgres_database_url):
    import json
    import psycopg
    import pytest
    from oko.storage.postgres_migrations import Migration,migrate,verify
    from oko.storage.postgres_schema import MIGRATIONS
    legacy=json.dumps(dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='p')]))
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:84])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
        db.execute("INSERT INTO publications(id) VALUES ('p')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','failure')")
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:work-fetch:f:fetch:p','background',%s,1)",(legacy,))
        db.execute('UPDATE youtube_access SET next_at=110 WHERE id=1')
        mig=MIGRATIONS[84];bad=Migration(85,mig.name,mig.sql+'; SELECT nonexistent FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:84],bad))
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==84
        assert db.execute("SELECT body FROM reader_problems WHERE id='legacy'").fetchone()[0]==legacy
        migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='legacy'").fetchone()[0]==100
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE youtube_fetch_due=110 AND youtube_fetch_reserved_owned').fetchone()
        assert any(p['code']=='work-fetch' for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_owner_change_during_pause_preserves_reserved_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','youtube','rss','connected','[]'),('b','b','B','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','youtube','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
        store.problems.recover_youtube_null_fetch();store.youtube.start(100)
        prior=store.db.execute('SELECT id FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()[0]
        store.remove_feed('a')
        reserved=store.db.execute('SELECT id FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchall()
        assert [r[0] for r in reserved]==[prior]
        assert any(p['id']==prior for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_many_youtube_roots_due_tick_has_bounded_queries(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT 'f-'||n,'f-'||n,'F','default','youtube','rss','connected','[]' FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO publications(id) SELECT 'root-'||n FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f-'||n,'root-'||n,true FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'root-'||n,'p' FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
        store.youtube.start(100);store.problems.recover_youtube_null_fetch()
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()[0]==250
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            for _ in range(3):store.read_versions.advance(now=111,wall=111)
        finally:store.db.set_trace_callback(None)
        projections=[q for q in trace if q.startswith('SELECT j.id,j.kind,j.item,j.status,j.error,j.problem_code')]
        print('250 roots, 3 ticks:',len(trace),'SQL;',len(projections),'full graph projections')
        assert len(projections)<=3
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()[0]==0
    finally:store.db.close()


def test_preimage_is_refreshed_after_waiting_for_graph(tmp_path,postgres_url):
    import threading
    import psycopg
    from oko.storage.fetch_job_problems import previous_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    graph=psycopg.connect(postgres_url,autocommit=False)
    done=threading.Event();errors=[];captured=[]
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','pending','old') RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        def capture():
            try:
                with transaction(store.db):captured.extend(previous_candidates(store,job,100))
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            thread=threading.Thread(target=capture);thread.start()
            assert not done.wait(.3)
            graph.execute("UPDATE jobs SET error='new' WHERE id=%s",(job,))
        thread.join(5)
        assert done.is_set() and not errors
        assert [row['reason'] for row in captured]==['new']
    finally:graph.close();store.db.close()


def test_recovery_avoids_ten_thousand_youtube_publications_without_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error) VALUES ('fetch','p-1','f','failed','waiting')")
            store.db._db.execute('ANALYZE feed_publications');store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_youtube_null_fetch()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.lstrip().startswith('WITH youtube_feeds AS MATERIALIZED'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_recovery_finds_orphan_fetch_by_job_feed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            job=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error) VALUES ('fetch','missing','f','failed','waiting') RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'fetch_job_id'=%s",(str(job),)).fetchone()
    finally:store.db.close()


def test_corrupt_one_reservation_does_not_duplicate_other_on_fallback(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','youtube','rss','connected','[]'),('y','y','Y','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','article'),('b','article')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','article','failed','waiting')")
        store.youtube.start(100);store.problems.recover_youtube_null_fetch()
        with transaction(store.db):
            saved=store.db.execute("SELECT identity FROM reader_problems WHERE youtube_fetch_due IS NOT NULL AND body::jsonb->>'fetch_youtube_canonical'='job:work-fetch:y:fetch:article'").fetchone()
            store.db.execute('UPDATE state SET value=%s WHERE key=%s',('foreign',saved['identity']))
        store.youtube.start(101)
        assert not store.db.execute("SELECT fetch_null_owned FROM jobs WHERE kind='fetch' AND item='article'").fetchone()[0]
        current=[p for p in store.problems.snapshot(112)['current'] if p['code']=='work-fetch']
        owners=[p['objects'][0]['id'] for p in current if p['objects']]
        assert set(owners)=={'a','b'} and len(owners)==len(set(owners)),owners
        store.read_versions.advance(now=112,wall=112)
        current=[p for p in store.problems.snapshot(112)['current'] if p['code']=='work-fetch']
        owners=[p['objects'][0]['id'] for p in current if p['objects']]
        assert set(owners)=={'a','b'} and len(owners)==len(set(owners)),owners
    finally:store.db.close()


def test_preimage_reloads_ownership_after_waiting_for_graph(tmp_path,postgres_url):
    import threading
    import psycopg
    from oko.storage.fetch_job_problems import previous_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    graph=psycopg.connect(postgres_url,autocommit=False)
    done=threading.Event();errors=[];captured=[]
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','pending','old') RETURNING id").fetchone()[0]
        store.problems.recover_youtube_null_fetch();store.youtube.start(100)
        def capture():
            try:
                with transaction(store.db):captured.extend(previous_candidates(store,job,100))
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            thread=threading.Thread(target=capture);thread.start()
            assert not done.wait(.3)
            graph.execute("UPDATE jobs SET fetch_youtube_sensitive=false,fetch_null_owned=false,fetch_null_ambiguous=true WHERE id=%s",(job,))
        thread.join(5)
        assert done.is_set() and not errors
        assert captured==[]
    finally:graph.close();store.db.close()


def test_repause_after_due_closes_reserved_episode_without_get(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting')")
        store.youtube.start(100);store.problems.recover_youtube_null_fetch()
        previous=store.db.execute('SELECT id FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()[0]
        clock[0]=111;store.youtube.start(111)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(previous,)).fetchone()[0]==111
        future=store.db.execute('SELECT id FROM reader_problems WHERE youtube_fetch_due IS NOT NULL').fetchone()[0]
        assert future!=previous
        assert any(p['id']==previous for p in store.problems.snapshot(112)['recent'])
        assert any(p['id']==future for p in store.problems.snapshot(122)['current'])
    finally:store.db.close()


def test_recovery_cost_two_unrelated_ten_thousand_sides(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]'),('r','r','R','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error) SELECT 'fetch','rss-'||n,'r','failed','waiting' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error) VALUES ('fetch','p-1','f','failed','waiting')")
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE feed_publications')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_youtube_null_fetch()
        finally:store.db.set_trace_callback(None)
        pages=[q for q in trace if q.startswith("SELECT id FROM jobs WHERE kind='fetch'") and 'ORDER BY id LIMIT 100' in q]
        assert 100<=len(pages)<=102,len(pages)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+pages[0]).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<=100,scan_rows(plan)
    finally:store.db.close()
