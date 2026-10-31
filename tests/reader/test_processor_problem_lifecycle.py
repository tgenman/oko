import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_processor_problem_is_owned_by_retry_without_get_and_rolls_back(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            first=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed-a','running',1) RETURNING id").fetchone()[0]
            second=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed-b','running',1) RETURNING id").fetchone()[0]
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.retry(Job(first,'read','feed-a',1),1,'missing',code='processor-config')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
                raise RuntimeError('rollback')
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        store.jobs.retry(Job(first,'read','feed-a',1),1,'missing',code='processor-config')
        store.jobs.retry(Job(second,'read','feed-b',1),1,'missing',code='processor-config')
        problem=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        assert problem
        assert {obj['id'] for obj in store.problems.processor_configuration_candidate(2)['objects']}=={'feed-a','feed-b'}
        before=store.db.execute('SELECT xmin::text,body FROM reader_problems WHERE id=%s',(problem['id'],)).fetchone()
        assert store.problems.snapshot(2)['current']
        after=store.db.execute('SELECT xmin::text,body FROM reader_problems WHERE id=%s',(problem['id'],)).fetchone()
        assert after==before
    finally:store.db.close()


def test_processor_change_invalidates_http_etag_and_rollback_preserves_it(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        def seed():
            store=app.state.host.require_store()
            with store.db:
                return store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        job_id=client.portal.call(seed)
        first=client.get('/api/v3/status/details')
        assert first.status_code==200
        etag=first.headers['etag']
        assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        def fail_then_rollback():
            store=app.state.host.require_store()
            try:
                with transaction(store.db):
                    store.jobs.retry(Job(job_id,'read','feed',1),1,'missing',code='processor-config')
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
        client.portal.call(fail_then_rollback)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        def retry():
            store=app.state.host.require_store()
            store.jobs.retry(Job(job_id,'read','feed',1),1,'missing',code='processor-config')
        client.portal.call(retry)
        updated=client.get('/api/v3/status/details',headers={'If-None-Match':etag})
        assert updated.status_code==200 and updated.headers['etag']!=etag
        assert any(p['code']=='processor_not_configured' for p in updated.json()['problems']['current'])
        statements=[]
        client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(statements.append))
        try:
            assert client.get('/api/v3/status/details',headers={'If-None-Match':updated.headers['etag']}).status_code==304
        finally:
            client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(None))
        assert not any('WHERE processor_visible ORDER BY' in sql for sql in statements)


def test_filter_failure_produces_shared_problem_before_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'filter','item',1),1,'missing',code='processor-config')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_configure_codex_closes_last_preclaim_problem_before_status_get(tmp_path,postgres_url):
    from oko.processing.execution import CodexChoice, CodexSettings
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES ('read','feed','running',1,%s) RETURNING id",(json.dumps({'preclaim_blocked':True}),)).fetchone()[0]
        store.jobs.retry(Job(job_id,'read','feed',1),1,'missing',code='processor-config')
        choice=CodexChoice(model='synthetic',reasoning_effort='low',fast=False)
        store.configure_codex(CodexSettings(filter=choice,theses=choice))
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_last_processor_finish_preserves_occurrence_and_recent_objects(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'read','feed',1)
        store.jobs.retry(job,1,'missing',code='processor-config')
        previous=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]
        with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job_id,))
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.finish(job)
                assert not store.db.execute("SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL",(previous,)).fetchone()
                raise RuntimeError('rollback')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL",(previous,)).fetchone()
        with transaction(store.db):store.jobs.finish(job)
        saved=store.db.execute('SELECT body,outcome FROM reader_problems WHERE id=%s',(previous,)).fetchone()
        assert saved['outcome']=='resolved' and json.loads(saved['body'])['objects']==[{'kind':'feed','id':'feed'}]
    finally:store.db.close()


def test_single_processing_reconcile_cancels_last_processor_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'filter','item',1),1,'missing',code='processor-config')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):store.jobs.reconcile('filter',[('item',None,False,'revision')])
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_viewed_transition_updates_visibility_and_closes_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',0)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'filter','item',1),1,'missing',code='processor-config')
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.set_processor_viewed(['item'],True)
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL',(row[0],)).fetchone()
        with transaction(store.db):store.set_processor_viewed(['item'],True)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row[0],)).fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_mark_viewed_through_store_closes_processor_without_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,members) VALUES ('item','rss','item',0,'[]')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'filter','item',1),1,'missing',code='processor-config')
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        assert store.viewed_set(['item'])
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE id=%s AND finished IS NULL',(row[0],)).fetchone()
    finally:store.db.close()


def test_youtube_pause_suppresses_caption_processor_before_get(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        store.youtube.blocked(clock[0],900)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        clock[0]=1001.0
        store.read_versions.advance(wall=clock[0])
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
    finally:store.db.close()


@pytest.mark.parametrize('method',['cancel','finish_read','account_wait','defer_captions'])
def test_single_job_transition_closes_processor_problem(method,tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        kind='download_media' if method=='defer_captions' else 'read'
        with store.db:
            job_id=store.db.execute('INSERT INTO jobs(kind,item,status,generation) VALUES (%s,%s,%s,1) RETURNING id',(kind,'item','running')).fetchone()[0]
        job=Job(job_id,kind,'item',1)
        store.jobs.retry(job,1,'missing',code='processor-config')
        original=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]
        if method!='account_wait':
            with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job_id,))
        with transaction(store.db):
            if method=='cancel':store.jobs.cancel(job)
            elif method=='finish_read':store.jobs.finish_read(job,'waiting',1)
            elif method=='account_wait':store.jobs.account_wait(1,10,job)
            else:store.jobs.defer_captions(job,1,1,'waiting')
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(original,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_http_etag_after_caption_deadline_without_host_tick(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    clock=[100.0]
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:clock[0])
    with TestClient(app,headers=HEADERS) as client:
        def setup():
            store=app.state.host.require_store()
            with store.db:
                store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
                job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
            store.youtube.blocked(clock[0],900)
            app.state.host.execution.reader_advance=None
        client.portal.call(setup)
        paused=client.get('/api/v3/status/details')
        assert paused.status_code==200
        clock[0]=1001.0
        after=client.get('/api/v3/status/details',headers={'If-None-Match':paused.headers['etag']})
        assert after.status_code==200
        problem=next(p for p in after.json()['problems']['current'] if p['code']=='processor_not_configured')
        def before_tick():
            store=app.state.host.require_store()
            assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
            store.read_versions.advance(wall=clock[0])
            row=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
            assert row and row[0]==problem['id']
        client.portal.call(before_tick)
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304
        committed=client.get('/api/v3/status/details')
        assert committed.headers['etag']==after.headers['etag']
        assert committed.json()['problems']==after.json()['problems']


def test_youtube_pause_extension_keeps_same_future_identity(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        store.youtube.blocked(clock[0],900)
        clock[0]=200
        store.youtube.blocked(clock[0],1800)
        clock[0]=2001
        before=store.problems.snapshot(clock[0])
        items=[p for p in before['current'] if p['code']=='processor-config']
        assert len(items)==1
        identity=items[0]['id']
        store.read_versions.advance(wall=clock[0])
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]==identity
    finally:store.db.close()


def test_first_caption_failure_during_pause_visible_after_deadline_without_tick(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        clock[0]=1001
        assert any(p['code']=='processor-config' for p in store.problems.snapshot(clock[0])['current'])
    finally:store.db.close()


def test_pause_reservation_is_transactional_unique_and_survives_restart(tmp_path,postgres_url):
    import psycopg
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
                assert store.problems.processor_reservation()
                raise RuntimeError('rollback')
        assert store.problems.processor_reservation() is None
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        reserved=store.problems.processor_reservation()
        assert reserved
        with pytest.raises(psycopg.errors.UniqueViolation):
            with transaction(store.db):
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES (%s,'foreign','background','{}',1,2,'resolved')",(reserved['id'],))
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        snapshot=store.problems.snapshot(clock[0])
        assert not any(p['id']==reserved['id'] for p in snapshot['current']+snapshot['recent'])
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        assert store.problems.processor_reservation()['id']==reserved['id']
        clock[0]=float(reserved['deadline'])+1
        visible=store.problems.processor_resume_candidate(clock[0])
        assert visible and visible[0]==reserved['id']
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.read_versions.advance(wall=clock[0])
                assert store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]==reserved['id']
                raise RuntimeError('rollback')
        assert store.problems.processor_reservation()['id']==reserved['id']
        store.read_versions.advance(wall=clock[0])
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()[0]==reserved['id']
    finally:store.db.close()


def test_long_youtube_pause_keeps_reservation_out_of_history_cleanup(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],200000)
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        reserved=store.problems.processor_reservation()['id']
        clock[0]+=86401
        store.read_versions.advance(wall=clock[0])
        assert store.problems.processor_reservation()['id']==reserved
        assert not any(p['id']==reserved for p in store.problems.snapshot(clock[0])['recent'])
        clock[0]=200101
        assert store.problems.processor_resume_candidate(clock[0])[0]==reserved
    finally:store.db.close()


def test_reservation_skips_occupied_id_and_preserves_legacy_row(tmp_path,postgres_url,monkeypatch):
    import uuid
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            occupied='processor-reserved-'+uuid.UUID(int=1).hex
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES (%s,'foreign','background','{}',1,2,'resolved')",(occupied,))
        store.youtube.blocked(clock[0],900)
        values=iter((uuid.UUID(int=1),uuid.UUID(int=2)))
        monkeypatch.setattr('oko.storage.problems.uuid.uuid4',lambda:next(values))
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        assert store.problems.processor_reservation()['id']=='processor-reserved-'+uuid.UUID(int=2).hex
        assert store.db.execute('SELECT identity,body FROM reader_problems WHERE id=%s',(occupied,)).fetchone()==('foreign','{}')
    finally:store.db.close()


def test_youtube_completed_early_activates_reserved_id_at_completion(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        reserved=store.problems.processor_reservation()['id']
        clock[0]=200
        store.youtube.completed()
        row=store.db.execute("SELECT id,created FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        assert row and row['id']==reserved and row['created']==200
    finally:store.db.close()


def test_foreign_processor_identity_is_not_overwritten_or_silenced(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            foreign=dict(id='processor:configuration',code='other',section='object',title='foreign',consequence='c',next_step='n',objects=[],action=None,operation='producer:fetch',state='current',next_at=None)
            store.problems.sync_candidate(foreign['id'],foreign,1)
            before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'read','feed',1),1,'missing',code='processor-config')
        assert store.db.execute("SELECT id,body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()==before
        state=store.problems.snapshot(2)
        assert any(p['code']=='processor-config' for p in state['current'])
        assert any(p['code']=='other' for p in state['current'])
        assert store.db.execute("SELECT id,body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()==before
        processor=next(p for p in state['current'] if p['code']=='processor-config')['id']
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert next(p for p in store.problems.snapshot(3)['current'] if p['code']=='processor-config')['id']==processor
        with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        with transaction(store.db):store.jobs.finish(Job(job,'read','feed',1))
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(processor,)).fetchone()[0]=='resolved'
        assert store.db.execute("SELECT id,body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()==before
    finally:store.db.close()


def test_caption_reservation_with_foreign_processor_identity(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            foreign=dict(id='processor:configuration',code='other',section='object',title='foreign',consequence='c',next_step='n',objects=[],action=None,operation='producer:fetch',state='current',next_at=None)
            store.problems.sync_candidate(foreign['id'],foreign,1)
            before=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        store.jobs.retry(Job(job_id,'download_media','m',1),1,'missing',code='processor-config')
        reserved=store.problems.processor_reservation()['id']
        clock[0]=1001
        assert store.problems.processor_resume_candidate(clock[0])[0]==reserved
        store.read_versions.advance(wall=clock[0])
        assert store.problems.processor_current()['id']==reserved
        assert store.db.execute("SELECT id,body FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()==before
    finally:store.db.close()


def test_unrelated_legacy_reservation_prefix_is_not_hidden(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        value=dict(code='other',section='object',title='legacy',consequence='c',next_step='n',objects=[],action=None,operation='producer:fetch',state='finished',next_at=None)
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('legacy','processor:reservation:foreign','background',%s,1,2,'resolved')",(json.dumps(value),))
        assert any(p['id']=='legacy' for p in store.problems.snapshot(3)['recent'])
    finally:store.db.close()


def test_adapter_change_closes_read_processor_problem_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'read','feed',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        with transaction(store.db):store.jobs.change_adapter('feed')
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_malformed_legacy_processor_occurrence_is_not_overwritten(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        raw='{"code":"processor-config","objects":[null]}'
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','processor:configuration','background',%s,1)",(raw,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'read','feed',1),1,'missing',code='processor-config')
        state=store.problems.snapshot(2)
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='legacy'").fetchone()[0]==raw
        assert any(p['code']=='processor-config' and p['id']!='legacy' for p in state['current'])
    finally:store.db.close()


def test_reconcile_cleanup_closes_orphaned_media_processor_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','missing','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','missing',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_cancel_history_clears_done_without_error_but_keeps_other_issue(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','pending',NULL,'processor-config') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO history_members(feed,item) VALUES ('feed','item')")
            store.problems.finish_processor_change([job],None,1)
        saved=store.problems.processor_current()
        with transaction(store.db):store.jobs.cancel_history('feed')
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_finish_feed_deletion_closes_processor_group(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','deleting',ARRAY[]::text[])")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('feed-delete','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'feed-delete','feed',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        store.jobs.finish_feed_deletion(Job(job,'feed-delete','feed',1))
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0] is not None
    finally:store.db.close()


def test_feed_remove_cancels_read_processor_without_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'read','feed',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        with transaction(store.db):
            store.remove_feed('feed')
            store.jobs.remove_feed('feed',set())
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0] is not None
    finally:store.db.close()


def test_processing_invalidation_closes_stale_processor_group(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',0)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES ('filter','item','running',1,%s) RETURNING id",(json.dumps({'input_revision':'old'}),)).fetchone()[0]
        store.jobs.retry(Job(job,'filter','item',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        with transaction(store.db):store.jobs.invalidate_processing()
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_more_than_100_malformed_isolated_rows_do_not_hide_real_group(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','processor:configuration','background','{}',1)")
            for n in range(101):
                body=json.dumps(dict(code='processor-config',processor_group_identity='processor:configuration',objects=[None]))
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(f'bad-{n}',f'processor:configuration-isolated:bad-{n}',body))
            valid=dict(id='processor:configuration-isolated:valid',code='processor-config',processor_group_identity='processor:configuration',section='action',title='valid',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
            store.problems.sync_candidate(valid['id'],valid,1)
        assert store.problems.processor_current()['identity']==valid['id']
        assert any(p['id']!='foreign' and p['code']=='processor-config' for p in store.problems.snapshot(2)['current'])
    finally:store.db.close()


def test_startup_merges_duplicate_valid_processor_groups_preserving_oldest(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        base=dict(code='processor-config',section='action',title='valid',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('read','feed','failed','failure','processor-config',true)")
            store.problems.sync_candidate('processor:configuration-isolated:old',dict(base,id='processor:configuration-isolated:old',processor_group_identity='processor:configuration'),1)
            store.problems.sync_candidate('processor:configuration',dict(base,id='processor:configuration'),2)
            first=store.db.execute("SELECT id FROM reader_problems WHERE identity='processor:configuration-isolated:old' AND finished IS NULL").fetchone()[0]
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.problems.processor_current()['id']==first
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE identity='processor:configuration' ORDER BY created DESC LIMIT 1").fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_collection_effect_cancels_delete_processor_problem_without_get(tmp_path,postgres_url):
    from oko.domain.collection import CollectionEffects
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'delete','item',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        with transaction(store.db):store.jobs.apply_collection(CollectionEffects(fetch={'item':False}))
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_enqueue_fetch_reactivates_processor_group_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','done',NULL,'processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        with transaction(store.db):store.jobs.enqueue_fetch('item')
        assert store.problems.processor_current() is not None
    finally:store.db.close()


def test_link_change_invalidates_processor_status_revision_without_get(tmp_path,postgres_url):
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed) VALUES ('root','rss','root','https://root.test',0),('child','other_web','https://child.test','https://child.test',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','root',true,true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','child','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','child',1),1,'missing',code='processor-config')
        assert store.problems.processor_configuration_candidate(1)['objects']==[{'kind':'material','id':'child'}]
        version=store.read_versions.version('status','runtime')['version']
        ref=ExternalReference(ExternalKey(Platform.other_web,'https://child.test'),'https://child.test')
        with transaction(store.db):assert publication_links.sync(store,'root',(ref,),CollectionEffects())
        assert store.problems.processor_configuration_candidate(1)['objects']==[{'kind':'material','id':'root'}]
        assert store.read_versions.version('status','runtime')['version']>version
    finally:store.db.close()


def test_retry_media_closes_processor_group_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','rss','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','synthetic','image','pending_download')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'download_media','m',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        with transaction(store.db):store.jobs.retry_media('p',revalidate_ready=False)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_remove_feed_closes_selected_filter_processor_without_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,processing_metadata) VALUES ('item','rss','item',0,%s)",(json.dumps({'_pass':{'feed_id':'feed'}}),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'filter','item',1),1,'missing',code='processor-config')
        saved=store.problems.processor_current()
        store.remove_feed('feed')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0] is not None
    finally:store.db.close()


@pytest.mark.parametrize('field,value',[('state','unknown'),('reason',3),('next_at',123),('objects',[{'kind':'invalid','id':'x'}]),('objects',[{'kind':'feed','id':'x','extra':1}]),('objects',[{'kind':'feed','id':''}]),('request_id','bad id')])
def test_malformed_processor_fields_are_preserved_and_isolated(tmp_path,postgres_url,field,value):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code='processor-config',section='action',title='legacy',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
        body[field]=value;raw=json.dumps(body)
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','processor:configuration','background',%s,1)",(raw,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'read','feed',1),1,'missing',code='processor-config')
        assert any(p['code']=='processor-config' and p['id']!='legacy' for p in store.problems.snapshot(2)['current'])
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='legacy'").fetchone()[0]==raw
    finally:store.db.close()


@pytest.mark.parametrize('field,value',[('state','unknown'),('reason',3),('next_at',123),('objects',[{'kind':'invalid','id':'x'}]),('objects',[{'kind':'feed','id':'x','extra':1}]),('objects',[{'kind':'feed','id':''}]),('request_id','bad id')])
def test_malformed_isolated_processor_fields_do_not_shadow_valid(tmp_path,postgres_url,field,value):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        base=dict(code='processor-config',processor_group_identity='processor:configuration',section='action',title='legacy',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
        bad=dict(base);bad[field]=value
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','processor:configuration-isolated:bad','background',%s,1)",(json.dumps(bad),))
            store.problems.sync_candidate('processor:configuration-isolated:valid',dict(base,id='processor:configuration-isolated:valid'),2)
        assert store.problems.processor_current()['identity']=='processor:configuration-isolated:valid'
    finally:store.db.close()


def test_processor_legacy_invalid_creation_time_does_not_corrupt_current(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code='processor-config',section='action',title='legacy',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
        raw=json.dumps(body)
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','processor:configuration','background',%s,'Infinity')",(raw,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'read','feed',1),1,'missing',code='processor-config')
        current=store.problems.snapshot(2)['current']
        assert any(p['id']!='legacy' and p['code']=='processor-config' for p in current)
        assert store.db.execute("SELECT body FROM reader_problems WHERE id='legacy'").fetchone()[0]==raw
    finally:store.db.close()


def test_revalidate_media_reactivates_processor_group_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','rss','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','synthetic','image','ready')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','done','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        with transaction(store.db):store.jobs.revalidate_media('m')
        assert store.problems.processor_current() is not None
    finally:store.db.close()


def test_schedule_media_cancels_processor_group_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','rss','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','synthetic','image','unavailable')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','pending','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        saved=store.problems.processor_current()
        with transaction(store.db):store.jobs.schedule_media('p')
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(saved['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_enqueue_discovery_reactivates_processor_group_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('discover','feed','done',NULL,'processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        store.jobs.enqueue_discovery('feed')
        assert store.problems.processor_current() is not None
    finally:store.db.close()


def test_delete_publication_reveals_equal_id_read_processor_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('same','rss','same',1)")
            read=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','same','failed','missing','processor-config') RETURNING id").fetchone()[0]
            delete=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','same','running',1) RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([read],None,1)
        original=store.problems.processor_current()['id']
        store.jobs.delete(Job(delete,'delete','same',1))
        assert store.problems.processor_current()['id']==original
    finally:store.db.close()


def test_refresh_feed_reactivates_done_fetch_processor_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete) VALUES ('item','rss','item',0,0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','done',NULL,'processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        store.jobs.refresh_feed('feed')
        assert store.problems.processor_current() is not None
    finally:store.db.close()


def test_normal_processor_event_after_deadline_keeps_reserved_visible_id(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            caption=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
            normal=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        store.jobs.retry(Job(caption,'download_media','m',1),1,'missing',code='processor-config')
        clock[0]=1001
        reserved=store.problems.processor_resume_candidate(clock[0])[0]
        store.jobs.retry(Job(normal,'read','feed',1),1,'missing',code='processor-config')
        assert store.problems.processor_current()['id']==reserved
    finally:store.db.close()


def test_second_pause_after_deadline_preserves_visible_interval_in_history(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        store.jobs.retry(Job(job,'download_media','m',1),1,'missing',code='processor-config')
        clock[0]=1001
        shown=store.problems.processor_resume_candidate(clock[0])[0]
        store.youtube.blocked(clock[0],900)
        row=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(shown,)).fetchone()
        assert row and row['finished'] is not None and row['outcome']=='resolved'
        assert store.problems.processor_reservation()['id']!=shown
    finally:store.db.close()


def test_job_finishes_after_deadline_before_tick_preserves_visible_history(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        store.jobs.retry(Job(job,'download_media','m',1),1,'missing',code='processor-config')
        clock[0]=1001
        shown=store.problems.processor_resume_candidate(clock[0])[0]
        with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        with transaction(store.db):store.jobs.finish(Job(job,'download_media','m',1))
        row=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(shown,)).fetchone()
        assert row and row['finished'] is not None and row['outcome']=='resolved'
    finally:store.db.close()


@pytest.mark.parametrize('first,second',[('processor-config','fetch-failed'),('fetch-failed','processor-config')])
def test_fetch_and_processor_ownership_switch_atomically(first,second,tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',0)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'fetch','item',1),1,'failure',code=first)
        def current_codes():
            return {json.loads(row['body'])['code'] for row in store.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND (identity='processor:configuration' OR body::jsonb->>'operation'='producer:fetch')")}
        assert first in current_codes()
        with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.retry(Job(job,'fetch','item',1),1,'failure',code=second)
                assert second in current_codes() and first not in current_codes()
                raise RuntimeError('rollback')
        assert first in current_codes() and second not in current_codes()
        store.jobs.retry(Job(job,'fetch','item',1),1,'failure',code=second)
        assert second in current_codes() and first not in current_codes()
    finally:store.db.close()


def test_youtube_completed_after_deadline_preserves_visible_created_at(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
        store.youtube.blocked(clock[0],900)
        store.jobs.retry(Job(job,'download_media','m',1),1,'missing',code='processor-config')
        clock[0]=1001
        before=store.problems.processor_resume_candidate(clock[0])
        store.youtube.completed()
        row=store.db.execute("SELECT id,created FROM reader_problems WHERE identity='processor:configuration' AND finished IS NULL").fetchone()
        assert row and row['id']==before[0] and row['created']==before[2]
    finally:store.db.close()


def test_link_change_invalidates_conditional_http_status(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.collection.adapters import ExternalKey,ExternalReference,Platform
    from oko.domain.collection import CollectionEffects
    from oko.storage import publication_links
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        def seed():
            store=app.state.host.require_store()
            with store.db:
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
                store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed) VALUES ('root','rss','root','https://root.test',0),('child','other_web','https://child.test','https://child.test',0)")
                store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','root',true,true)")
                job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','child','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job,'fetch','child',1),1,'missing',code='processor-config')
        client.portal.call(seed)
        before=client.get('/api/v3/status/details');assert before.status_code==200
        def link():
            store=app.state.host.require_store()
            ref=ExternalReference(ExternalKey(Platform.other_web,'https://child.test'),'https://child.test')
            with transaction(store.db):assert publication_links.sync(store,'root',(ref,),CollectionEffects())
        client.portal.call(link)
        after=client.get('/api/v3/status/details',headers={'If-None-Match':before.headers['etag']})
        assert after.status_code==200 and after.headers['etag']!=before.headers['etag']
        assert client.get('/api/v3/status/details',headers={'If-None-Match':after.headers['etag']}).status_code==304


def test_cleanup_reactivates_cancelled_delete_processor_group(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',1)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','item','cancelled','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        with transaction(store.db):store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='pending'
        assert store.problems.processor_current() is not None
    finally:store.db.close()
