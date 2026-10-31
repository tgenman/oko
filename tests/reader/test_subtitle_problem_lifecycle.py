from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job


def test_defer_captions_persists_problem_before_status_read(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        row=store.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert row
        store.problems.snapshot(100)
        assert tuple(row)==tuple(store.db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())
    finally:store.db.close()


def test_next_at_hides_null_error_then_reopens_as_new_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        before=store.problems.snapshot(100)
        first=next(p for p in before['current'] if p['code']=='work-download_media')
        store.youtube.start(100)
        during=store.problems.snapshot(105)
        assert not any(p['code']=='work-download_media' for p in during['current'])
        assert any(p['id']==first['id'] for p in during['recent'])
        after=store.problems.snapshot(111)
        current=next(p for p in after['current'] if p['code']=='work-download_media')
        assert current['id']!=first['id']
    finally:store.db.close()


def test_new_deferred_error_during_wait_is_only_visible_after_deadline(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        during=store.problems.snapshot(105)
        assert not any(p['code']=='work-download_media' for p in during['current']+during['recent'])
        after=store.problems.snapshot(111)
        assert any(p['code']=='work-download_media' for p in after['current'])
    finally:store.db.close()


def test_repeated_defer_during_wait_updates_one_reserved_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'first')
            saved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed
            store.jobs.defer_captions(claimed,100,10,'second')
        after=store.problems.snapshot(111)
        relevant=[p for p in after['current'] if p['code']=='work-download_media']
        assert len(relevant)==1 and relevant[0]['id']==saved and relevant[0]['reason']=='second'
        assert not any(p['code']=='work-download_media' for p in after['recent'])
    finally:store.db.close()


def test_success_during_wait_cancels_future_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            assert store.db.execute('SELECT 1 FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed
            store.jobs.finish(claimed)
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
        assert not any(p['code']=='work-download_media' for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_finish_after_virtual_deadline_keeps_visible_id_in_history(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        clock[0]=111
        seen=next(p['id'] for p in store.problems.snapshot(111)['current'] if p['code']=='work-download_media')
        with transaction(store.db):
            row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone()
            claimed=store.jobs._claim(row)
            assert claimed
            store.jobs.finish(claimed)
        after=store.problems.snapshot(111)
        assert any(p['id']==seen for p in after['recent'])
    finally:store.db.close()


def test_pause_suppresses_explicit_error_but_next_at_alone_does_not(tmp_path,postgres_url):
    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('media-unavailable','failure',AdapterErrorKind.temporary))
        first=next(p for p in store.problems.snapshot(100)['current'] if p['code']=='media-unavailable')
        store.youtube.start(100)
        assert any(p['id']==first['id'] for p in store.problems.snapshot(101)['current'])
        store.youtube.blocked(102,20)
        during=store.problems.snapshot(103)
        assert not any(p['code']=='media-unavailable' for p in during['current'])
        assert any(p['id']==first['id'] for p in during['recent'])
        clock[0]=103;store.youtube.completed()
        after=store.problems.snapshot(103)
        second=next(p for p in after['current'] if p['code']=='media-unavailable')
        assert second['id']!=first['id']
    finally:store.db.close()


def test_multiple_roots_each_get_reserved_episode(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('a-root'),('b-root'),('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','a-root',true),('b','b-root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a-root','p'),('b-root','p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        before=[p for p in store.problems.snapshot(100)['current'] if p['code']=='work-download_media']
        assert len(before)==2
        store.youtube.start(100)
        assert not [p for p in store.problems.snapshot(105)['current'] if p['code']=='work-download_media']
        after=[p for p in store.problems.snapshot(111)['current'] if p['code']=='work-download_media']
        assert len(after)==2 and not ({p['id'] for p in before}&{p['id'] for p in after})
    finally:store.db.close()


def test_success_during_pause_clears_all_owned_roots(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('a-root'),('b-root'),('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','a-root',true),('b','b-root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a-root','p'),('b-root','p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            assert store.db.execute('SELECT count(*) FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]==2
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed
            store.jobs.finish(claimed)
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]==0
    finally:store.db.close()


def test_recovery_moves_legacy_subtitle_without_changing_occurrence(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','old') RETURNING id").fetchone()[0]
            body=dict(code='work-download_media',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old-id','job:work-download_media:m:download_media:m','background',%s,1)",(json.dumps(body),))
        store.problems.recover_subtitles()
        active=store.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
        assert active and active[0]=='old-id'
    finally:store.db.close()


def test_timer_batches_many_unrelated_subtitle_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,100)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','f','pending_download' FROM generate_series(1,100)n")
            jobs=store.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'download_media','m-'||n,'failed','error' FROM generate_series(1,100)n RETURNING id").fetchall()
            for row in jobs:store.problems.sync_media_problem(row['id'],[],100)
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.youtube.start(100)
        finally:store.db.set_trace_callback(None)
        print('subtitle timer SQL',len(trace))
        assert len(trace)<100,len(trace)
    finally:store.db.close()


def test_recovery_batches_thousand_subtitle_errors(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','f','pending_download' FROM generate_series(1,1000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'download_media','m-'||n,'failed','error' FROM generate_series(1,1000)n")
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_subtitles()
        finally:store.db.set_trace_callback(None)
        print('subtitle recovery SQL',len(trace))
        assert len(trace)<500,len(trace)
        assert store.db.execute('SELECT count(*) FROM jobs WHERE subtitle_event_owned').fetchone()[0]==1000
    finally:store.db.close()


def test_recovery_during_wait_does_not_fabricate_recent_history(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,100)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','f','pending_download' FROM generate_series(1,100)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'download_media','m-'||n,'failed','error' FROM generate_series(1,100)n")
            store.db.execute('UPDATE youtube_access SET next_at=200 WHERE id=1')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_subtitles()
        finally:store.db.set_trace_callback(None)
        print('subtitle paused recovery SQL',len(trace))
        assert len(trace)<150,len(trace)
        before=store.problems.snapshot(100)
        assert not [p for p in before['recent'] if p['code']=='work-download_media']
        assert len([p for p in store.problems.snapshot(201)['current'] if p['code']=='work-download_media'])==100
    finally:store.db.close()


def test_removing_one_root_during_wait_keeps_only_other_reservation(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a-root','rss','a'),('b-root','rss','b'),('p','telegraph','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','a-root',true),('b','b-root',true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a-root','p'),('b-root','p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        store.remove_feed('a')
        after=[p for p in store.problems.snapshot(111)['current'] if p['code']=='work-download_media']
        assert len(after)==1 and after[0]['objects']==[dict(kind='material',id='b-root')]
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]==1
    finally:store.db.close()


def test_owner_transfer_during_wait_preserves_reserved_id(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            reserved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
            effects=CollectionEffects();publication_links.capture_media_problems(store,effects,{'p'})
            store.db.execute("UPDATE feed_publications SET is_primary=false WHERE feed_id='a' AND publication_id='p'")
            store.db.execute("UPDATE feed_publications SET is_primary=true WHERE feed_id='b' AND publication_id='p'")
            store.jobs.sync_captured_deletions(effects)
        after=[p for p in store.problems.snapshot(111)['current'] if p['code']=='work-download_media']
        assert len(after)==1 and after[0]['id']==reserved
    finally:store.db.close()


def test_foreign_reservation_with_same_job_is_not_overwritten(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'first')
            row=store.db.execute('SELECT id,body FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
            body=json.loads(row['body']);body['foreign']='must-survive';raw=json.dumps(body)
            store.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(raw,row['id']))
        store.youtube.blocked(101,20)
        after=store.db.execute('SELECT body FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()
        assert after and after[0]==raw
    finally:store.db.close()


def test_status_candidate_scan_ignores_owned_subtitles(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','f','pending_download' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,subtitle_event_owned) SELECT 'download_media','m-'||n,'failed','error','unavailable',true FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE media_files')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.candidates(100)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT jobs . *'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_timer_pause_and_reserved_episode_roll_back_atomically(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        before=[tuple(row) for row in store.db.execute('SELECT id,identity,body,finished,subtitle_due FROM reader_problems ORDER BY id')]
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.youtube.start(100)
                assert store.db.execute('SELECT 1 FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
                raise RuntimeError('rollback')
        assert [tuple(row) for row in store.db.execute('SELECT id,identity,body,finished,subtitle_due FROM reader_problems ORDER BY id')]==before
        assert store.youtube._limits()['next_at']==0
    finally:store.db.close()


def test_event_owned_subtitle_etag_changes_exactly_at_deadline(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO publications(id,platform) VALUES ('p','youtube')")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                store.youtube.start(100)
                store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        before=client.get('/api/v3/status/details')
        assert before.status_code==200
        clock[0]=109
        assert client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']}).status_code==304
        clock[0]=111
        after=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert after.status_code==200
        assert len(after.json()['problems']['current'])>len(before.json()['problems']['current'])
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(now=111,wall=111))
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304


def test_conflicting_subtitle_isolates_do_not_scan_unbounded_or_overwrite(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','error') RETURNING id").fetchone()[0]
            body=json.dumps(dict(code='work-download_media',operation='producer:download_media',media_job_id=str(job),media_file_id='m',media_job_identity='job:work-download_media:m:download_media:m',objects=[dict(kind='material',id='p')]))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:media-isolated:'||n,'background',%s,1 FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):store.problems.sync_media_problem(job,[],100)
        finally:store.db.set_trace_callback(None)
        assert not store.db.execute('SELECT subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE finished IS NULL').fetchone()[0]==10000
        query=next(q for q in trace if q.startswith('SELECT id FROM reader_problems WHERE finished IS NULL') and 'md5(substring' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,('m','m')).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<500
    finally:store.db.close()


def test_completion_after_virtual_deadline_preserves_id_and_created_time(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        clock[0]=111
        before=next(p for p in store.problems.snapshot(111)['current'] if p['code']=='work-download_media')
        store.youtube.completed()
        after=next(p for p in store.problems.snapshot(111)['current'] if p['code']=='work-download_media')
        assert (before['id'],before['created_at'])==(after['id'],after['created_at'])
    finally:store.db.close()


def test_many_legacy_subtitle_occurrences_survive_event_get_and_job_removal(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='work-download_media',section='object',title='old',consequence='old',next_step='old',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','error') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-download_media:foreign-'||n||':download_media:m','background',%s,1 FROM generate_series(1,1001)n",(body,))
            store.problems.sync_media_problem(job,[],100)
        assert not store.db.execute('SELECT subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'fetch-null-protected:%%'").fetchone()[0]==1001
        store.problems.snapshot(100)
        with transaction(store.db):store.db.execute('DELETE FROM jobs WHERE id=%s',(job,))
        store.problems.snapshot(100)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'r-%%' AND finished IS NULL").fetchone()[0]==1001
    finally:store.db.close()


def test_recovery_protects_oversized_legacy_subtitle_group(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='work-download_media',section='object',title='old',consequence='old',next_step='old',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','error') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:work-download_media:foreign-'||n||':download_media:m','background',%s,1 FROM generate_series(1,1001)n",(body,))
        store.problems.recover_subtitles()
        assert not store.db.execute('SELECT subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'fetch-null-protected:%%'").fetchone()[0]==1001
        store.problems.snapshot(100)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'r-%%' AND finished IS NULL").fetchone()[0]==1001
    finally:store.db.close()


def test_recovery_protects_oversized_explicit_isolated_subtitle_group(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='media-unavailable',section='object',title='old',consequence='old',next_step='old',objects=[dict(kind='material',id='p')],action=None,operation='producer:download_media',state='current',next_at=None,media_job_identity='job:media-unavailable:m:download_media:m'))
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','error','media-unavailable') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'r-'||n,'job:media-isolated:'||n,'background',%s,1 FROM generate_series(1,1001)n",(body,))
        store.problems.recover_subtitles()
        assert not store.db.execute('SELECT subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'fetch-null-protected:%%'").fetchone()[0]==1001
        store.problems.snapshot(100)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'r-%%' AND finished IS NULL").fetchone()[0]==1001
    finally:store.db.close()


def test_reader_tick_does_not_expire_future_subtitle_reservation(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            reserved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
        store.read_versions.advance(now=105,wall=105)
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s',(reserved,)).fetchone()
        store.read_versions.advance(now=111,wall=111)
        assert store.db.execute('SELECT finished,subtitle_due FROM reader_problems WHERE id=%s',(reserved,)).fetchone()[0] is None
    finally:store.db.close()


def test_status_does_not_scan_ten_thousand_future_reservations(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=json.dumps(dict(code='work-download_media',operation='producer:download_media',objects=[]))
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,subtitle_due,subtitle_reserved_owned) SELECT 'r-'||n,'subtitle:reservation:r-'||n,'background',%s,1,-62135596800,'irrelevant',CASE WHEN n%%2=0 THEN 1 ELSE 1000 END,(n%%2<>0) FROM generate_series(1,10000)n",(body,))
            store.db._db.execute('ANALYZE reader_problems')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.snapshot(100)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT * FROM reader_problems WHERE finished IS NULL OR finished >'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(100-86400,100)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_recovery_resumes_after_committed_batch_without_changing_reserved_ids(tmp_path,postgres_url,monkeypatch):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','f','pending_download' FROM generate_series(1,101)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'download_media','m-'||n,'failed','error' FROM generate_series(1,101)n")
            store.db.execute('UPDATE youtube_access SET next_at=200 WHERE id=1')
        old_fault=store.fault;seen=[]
        def fault(name):
            if name=='subtitle_problem_recovery_batch':
                seen.append(name)
                if len(seen)==1:raise RuntimeError('crash')
            return old_fault(name)
        monkeypatch.setattr(store,'fault',fault)
        import pytest
        with pytest.raises(RuntimeError,match='crash'):store.problems.recover_subtitles()
        before={row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL')}
        assert len(before)==100
        monkeypatch.setattr(store,'fault',old_fault)
        store.problems.recover_subtitles()
        after={row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL')}
        assert len(after)==101 and before.issubset(after)
    finally:store.db.close()


def test_get_at_virtual_deadline_does_not_materialize_reservation(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        before=store.db.execute('SELECT id,identity,body,subtitle_due,xmin::text FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
        assert before
        trace=[];store.db.set_trace_callback(trace.append)
        try:current=store.problems.snapshot(111)['current']
        finally:store.db.set_trace_callback(None)
        assert any(p['id']==before['id'] for p in current)
        assert not [sql for sql in trace if sql.lstrip().upper().startswith(('INSERT ','UPDATE ','DELETE '))]
        after=store.db.execute('SELECT id,identity,body,subtitle_due,xmin::text FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_foreign_reservation_pointer_is_never_overwritten(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            row=store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
            store.db.execute("UPDATE state SET value='foreign' WHERE key=%s",(row['identity'],))
        store.youtube.blocked(101,20)
        after=store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()
        assert tuple(row)==tuple(after)
        assert store.db.execute('SELECT value FROM state WHERE key=%s',(row['identity'],)).fetchone()[0]=='foreign'
        assert not store.db.execute('SELECT subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:store.db.close()


def test_timer_job_scan_ignores_ten_thousand_unrelated(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'download_media','unrelated-'||n,'pending' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.youtube.start(100)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith("SELECT id,problem_code FROM jobs WHERE kind='download_media' AND subtitle_event_owned"))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_legacy_negative_job_id_is_visible_after_deadline(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            store.db.execute("INSERT INTO jobs(id,kind,item,status,generation) VALUES (-1,'download_media','m','running',1)")
            store.youtube.start(100)
            store.jobs.defer_captions(Job(-1,'download_media','m',1),100,10,'waiting')
        assert any(p['code']=='work-download_media' for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_damaged_due_reservation_is_visible_as_generic_without_rewriting(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            row=store.db.execute('SELECT id,identity,body FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
            store.db.execute("UPDATE state SET value='foreign' WHERE key=%s",(row['identity'],))
        before=tuple(store.db.execute('SELECT id,identity,body,subtitle_due,xmin::text FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())
        current=store.problems.snapshot(111)['current']
        assert any(p['id']==row['id'] for p in current)
        after=tuple(store.db.execute('SELECT id,identity,body,subtitle_due,xmin::text FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())
        assert before==after
    finally:store.db.close()


def test_restart_while_running_during_wait_keeps_future_error(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed
            reserved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        clock[0]=111
        current=store.problems.snapshot(111)['current']
        assert any(p['id']==reserved and p['code']=='work-download_media' for p in current)
    finally:store.db.close()


def test_concurrent_pointer_change_rolls_back_timer_without_overwriting(tmp_path,postgres_url,monkeypatch):
    import psycopg,pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    other=psycopg.connect(postgres_url,autocommit=True)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        saved=store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
        original=store.db.execute;changed=[]
        def intercept(query,parameters=()):
            cursor=original(query,parameters)
            if not changed and query.startswith('SELECT key,value FROM state WHERE key=ANY'):
                other.execute("UPDATE state SET value='foreign' WHERE key=%s",(saved['identity'],));changed.append(True)
            return cursor
        monkeypatch.setattr(store.db,'execute',intercept)
        with pytest.raises(RuntimeError,match='pointer changed'):store.youtube.start(102)
        monkeypatch.setattr(store.db,'execute',original)
        assert changed and other.execute('SELECT value FROM state WHERE key=%s',(saved['identity'],)).fetchone()[0]=='foreign'
        after=store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()
        assert tuple(after)==tuple(saved)
        assert store.youtube._limits()['next_at']==110
    finally:other.close();store.db.close()


def test_reservation_pointer_gc_is_bounded_and_preserves_foreign(tmp_path,postgres_url):
    import json
    from oko.storage.subtitle_problems import cleanup_pointers
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO state(key,value) SELECT 'foreign-'||n,'unchanged' FROM generate_series(1,10000)n")
            for n in range(250):
                key='subtitle:reservation:'+format(n,'032x')
                store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-subtitle-v1','1','canonical'])))
            store.db.execute("INSERT INTO state(key,value) VALUES ('subtitle:reservation:bad','foreign')")
            store.db._db.execute('ANALYZE state')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            with transaction(store.db):cleanup_pointers(store,None)
        finally:store.db.set_trace_callback(None)
        assert store.db.execute("SELECT count(*) FROM state WHERE key LIKE 'subtitle:reservation:%%'").fetchone()[0]==151
        query=next(q for q in trace if q.startswith('SELECT key,value FROM state WHERE key COLLATE'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_migration_accepts_long_legacy_occurrence_and_media_id(postgres_database_url):
    import json,secrets,psycopg
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    item=secrets.token_hex(750);rid=secrets.token_hex(750)
    body=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')]))
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:81])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(rid,'job:work-download_media:f:download_media:'+item,body))
        migrate(db,MIGRATIONS)
        assert db.execute('SELECT 1 FROM reader_problems WHERE id=%s',(rid,)).fetchone()


def test_empty_explicit_code_does_not_wait_for_next_at(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','err','') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],100)
        store.youtube.start(100)
        assert any(p['code']=='work-download_media' for p in store.problems.snapshot(101)['current'])
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
    finally:store.db.close()


def test_recovery_does_not_scan_ten_thousand_unrelated_media(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'image','x','f','pending_download' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'download_media','m-'||n,'failed','error' FROM generate_series(1,10000)n")
            store.db._db.execute('ANALYZE jobs');store.db._db.execute('ANALYZE media_files')
        trace=[];store.db.set_trace_callback(trace.append)
        try:store.problems.recover_subtitles()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in trace if q.startswith('SELECT m.id AS file_id,j.id,j.subtitle_event_owned'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000
    finally:store.db.close()


def test_foreign_canonical_subtitle_body_is_preserved_on_event_and_get(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','error') RETURNING id").fetchone()[0]
            body=dict(code='work-download_media',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:download_media',state='current',next_at=None,media_job_id=str(job),media_file_id='m',foreign='preserve')
            raw=json.dumps(body)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:m:download_media:m','background',%s,1)",(raw,))
            store.problems.sync_media_problem(job,[],100)
        store.problems.snapshot(100)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row and tuple(row)==(raw,None)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id<>'foreign' AND finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:store.db.close()


def test_recovery_preserves_foreign_same_identity_body(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','error') RETURNING id").fetchone()[0]
            body=dict(code='work-download_media',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,foreign='preserve')
            raw=json.dumps(body)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:m:download_media:m','background',%s,1)",(raw,))
        store.problems.recover_subtitles()
        store.problems.snapshot(100)
        assert tuple(store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(raw,None)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id<>'foreign' AND finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
    finally:store.db.close()


def test_changed_active_subtitle_body_is_preserved_when_timer_starts(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            row=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
            body=json.loads(row['body']);body['reason']='foreign reason';raw=json.dumps(body)
            store.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(raw,row['id']))
        store.youtube.start(100)
        assert tuple(store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(row['id'],)).fetchone())==(raw,None)
        assert not store.db.execute('SELECT subtitle_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
    finally:store.db.close()


def test_recovery_during_wait_closes_preexisting_legacy_and_reserves_new(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        body=dict(code='work-download_media',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None)
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','old')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old-id','job:work-download_media:m:download_media:m','background',%s,1)",(json.dumps(body),))
            store.db.execute('UPDATE youtube_access SET next_at=200 WHERE id=1')
        store.problems.recover_subtitles()
        before=store.problems.snapshot(100)
        assert not any(p['code']=='work-download_media' for p in before['current'])
        assert any(p['id']=='old-id' for p in before['recent'])
        after=store.problems.snapshot(201)
        assert any(p['code']=='work-download_media' and p['id']!='old-id' for p in after['current'])
    finally:store.db.close()


def test_null_to_explicit_during_pause_preserves_reserved_occurrence(tmp_path,postgres_url):
    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.blocked(100,20)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            saved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed
            store.jobs.fail_media(claimed,AdapterIssue('media-unavailable','failure',AdapterErrorKind.temporary))
            current=store.db.execute('SELECT id,body FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchall()
            assert len(current)==1 and current[0]['id']==saved
        store.youtube.completed()
        assert any(p['id']==saved and p['code']=='media-unavailable' for p in store.problems.snapshot(100)['current'])
    finally:store.db.close()


def test_distinct_explicit_errors_during_pause_get_distinct_reserved_ids(tmp_path,postgres_url):
    from oko.collection.adapters import AdapterIssue,AdapterErrorKind
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.blocked(100,20)
            store.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue('media-unavailable','first',AdapterErrorKind.temporary))
            old=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
            assert claimed
            store.jobs.fail_media(claimed,AdapterIssue('media-too-large','second',AdapterErrorKind.temporary))
            rows=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchall()
            assert len(rows)==1 and rows[0][0]!=old
    finally:store.db.close()


def test_startup_migrates_existing_subtitles_before_serving(tmp_path,postgres_database_url):
    import psycopg
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:81])
        db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,101)n")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'youtube:subtitles','x','f','pending_download' FROM generate_series(1,101)n")
        db.execute("INSERT INTO jobs(kind,item,status,error) SELECT 'download_media','m-'||n,'failed','error' FROM generate_series(1,101)n")
        db.execute('UPDATE youtube_access SET next_at=200 WHERE id=1')
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url,wall_clock=lambda:100)
    try:
        assert store.db.execute('SELECT count(*) FROM jobs WHERE subtitle_event_owned').fetchone()[0]==101
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]==101
        assert not [p for p in store.problems.snapshot(100)['current'] if p['code']=='work-download_media']
        assert len([p for p in store.problems.snapshot(201)['current'] if p['code']=='work-download_media'])==101
    finally:store.db.close()


def test_restart_after_deadline_preserves_reserved_virtual_id(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            reserved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
        store.db.close();clock[0]=111
        store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        assert any(p['id']==reserved for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


@__import__('pytest').mark.parametrize('unknown',[None,float('nan'),float('inf')])
def test_unknown_next_time_can_be_retimed_without_losing_reservation(tmp_path,postgres_url,unknown):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.db.execute('UPDATE youtube_access SET next_at=%s WHERE id=1',(unknown,))
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            row=store.db.execute('SELECT id,subtitle_due FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
            assert row and row['subtitle_due']==float('inf')
        store.youtube.start(100)
        row_after=store.db.execute('SELECT id,subtitle_due FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
        assert tuple(row_after)==(row['id'],110)
        assert any(p['id']==row['id'] for p in store.problems.snapshot(111)['current'])
    finally:store.db.close()


def test_due_materialization_and_cursor_roll_back_together(tmp_path,postgres_url):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100)
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            before=tuple(store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone())
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.read_versions.advance(now=111,wall=111)
                raise RuntimeError('rollback')
        assert tuple(store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE id=%s',(before[0],)).fetchone())==before
        assert store.read_versions._subtitle_due_cursor is None
        store.read_versions.advance(now=111,wall=111)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(before[0],)).fetchone()[0] is None
    finally:store.db.close()


def test_many_roots_due_tick_has_bounded_queries(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT 'f-'||n,'f-'||n,'F','default','rss','rss','connected','[]' FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO publications(id) SELECT 'root-'||n FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f-'||n,'root-'||n,true FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'root-'||n,'p' FROM generate_series(1,250)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        trace=[];store.db.set_trace_callback(trace.append)
        try:
            for _ in range(3):store.read_versions.advance(now=111,wall=111)
        finally:store.db.set_trace_callback(None)
        projections=[q for q in trace if q.startswith('SELECT j.id,j.kind,j.item,j.status,j.error,j.problem_code')]
        print('250 roots due 3 ticks SQL',len(trace),'full graph projections',len(projections))
        assert len(projections)<=3
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]==0
    finally:store.db.close()


def test_concurrent_pointer_change_rolls_back_activation(tmp_path,postgres_url,monkeypatch):
    import psycopg,pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    other=psycopg.connect(postgres_url,autocommit=True)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        saved=store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
        original=store.db.execute;changed=[]
        def intercept(query,parameters=()):
            cursor=original(query,parameters)
            if not changed and query.startswith('SELECT key,value FROM state WHERE key=ANY'):
                other.execute("UPDATE state SET value='foreign' WHERE key=%s",(saved['identity'],));changed.append(True)
            return cursor
        monkeypatch.setattr(store.db,'execute',intercept)
        with pytest.raises(RuntimeError,match='pointer changed'):
            with transaction(store.db):
                from oko.storage.subtitle_problems import activate_due
                activate_due(store,111)
        monkeypatch.setattr(store.db,'execute',original)
        after=store.db.execute('SELECT id,identity,body,subtitle_due FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()
        assert changed and tuple(after)==tuple(saved)
        assert other.execute('SELECT value FROM state WHERE key=%s',(saved['identity'],)).fetchone()[0]=='foreign'
    finally:other.close();store.db.close()


def test_damaged_reservation_returns_http_status_without_mutation(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO publications(id) VALUES ('p')")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                store.youtube.start(100);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
                row=store.db.execute('SELECT id,identity,body FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
                store.db.execute("UPDATE state SET value='foreign' WHERE key=%s",(row['identity'],))
                return row['id'],row['body']
        identity,body=client.portal.call(setup)
        clock[0]=111
        response=client.get('/api/v3/status/details')
        assert response.status_code==200
        assert any(problem['id']==identity for problem in response.json()['problems']['current'])
        assert client.portal.call(lambda:app.state.host.require_store().db.execute('SELECT body FROM reader_problems WHERE id=%s',(identity,)).fetchone()[0])==body


def test_reservation_allocation_retries_foreign_identity(tmp_path,postgres_url,monkeypatch):
    import uuid
    from oko.storage import subtitle_problems
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            occupied='0'*31+'1';free='0'*31+'2'
            store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',('subtitle:reservation:'+occupied,'foreign'))
        proposals=iter([occupied,free])
        monkeypatch.setattr(subtitle_problems,'new_reservation_id',lambda:next(proposals))
        store.youtube.start(100)
        row=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()
        assert row and row[0]==free
        assert store.db.execute('SELECT value FROM state WHERE key=%s',('subtitle:reservation:'+occupied,)).fetchone()[0]=='foreign'
    finally:store.db.close()


def test_exhausted_reservation_identity_rolls_back_timer(tmp_path,postgres_url,monkeypatch):
    import pytest
    from oko.storage import subtitle_problems
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            occupied='0'*31+'1'
            store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',('subtitle:reservation:'+occupied,'foreign'))
            before=tuple(store.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE finished IS NULL").fetchone())
        monkeypatch.setattr(subtitle_problems,'new_reservation_id',lambda:occupied)
        with pytest.raises(RuntimeError,match='Unable to allocate'):store.youtube.start(100)
        assert tuple(store.db.execute('SELECT id,identity,body,finished FROM reader_problems WHERE id=%s',(before[0],)).fetchone())==before
        assert store.db.execute('SELECT value FROM state WHERE key=%s',('subtitle:reservation:'+occupied,)).fetchone()[0]=='foreign'
        assert store.youtube._limits()['next_at']==0
    finally:store.db.close()


def test_read_after_virtual_deadline_keeps_observed_occurrence_in_history(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
        clock[0]=111
        seen=next(p['id'] for p in store.problems.snapshot(111)['current'] if p['code']=='work-download_media')
        assert store.viewed('p')
        saved=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(seen,)).fetchone()
        assert saved and saved['finished'] is not None and saved['outcome']=='irrelevant'
    finally:store.db.close()


def test_activation_with_foreign_canonical_keeps_observed_reserved_id(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        foreign=json.dumps(dict(code='foreign-code',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:delete',state='current',next_at=None))
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.youtube.start(100);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
            reserved=store.db.execute('SELECT id FROM reader_problems WHERE subtitle_due IS NOT NULL').fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:m:download_media:m','background',%s,1)",(foreign,))
        assert any(p['id']==reserved for p in store.problems.snapshot(111)['current'])
        store.read_versions.advance(now=111,wall=111)
        row=store.db.execute('SELECT identity,finished FROM reader_problems WHERE id=%s',(reserved,)).fetchone()
        assert row and row['finished'] is None and row['identity'].startswith('job:media-isolated:')
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0]==foreign
    finally:store.db.close()


def test_http_body_and_etag_stay_stable_after_isolated_activation(tmp_path,postgres_url):
    import json
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with transaction(store.db):
                store.db.execute("INSERT INTO publications(id) VALUES ('p')")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
                store.youtube.start(100);store.jobs.defer_captions(Job(job,'download_media','m',1),100,10,'waiting')
                foreign=json.dumps(dict(code='foreign-code',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:delete',state='current',next_at=None))
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:work-download_media:m:download_media:m','background',%s,1)",(foreign,))
            app.state.host.execution.reader_advance=None
        client.portal.call(setup);clock[0]=111
        before=client.get('/api/v3/status/details');assert before.status_code==200
        client.portal.call(lambda:app.state.host.require_store().read_versions.advance(now=111,wall=111))
        after=client.get('/api/v3/status/details');assert after.status_code==200
        assert before.json()==after.json()
        assert before.headers['etag']==after.headers['etag']


def test_scoped_incomplete_projection_keeps_event_owned_explicit_subtitle(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','error','media-unavailable') RETURNING id").fetchone()[0]
            store.problems.sync_media_problem(job,[],100)
        groups,_,_=store.problems.incomplete_generic_groups(100,['m'])
        assert any(p['code']=='media-unavailable' for p in groups.values())
    finally:store.db.close()
