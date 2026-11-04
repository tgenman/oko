import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


@pytest.mark.parametrize('kind',['filter','theses'])
def test_processing_failure_and_finish_are_saved_without_status_read(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'article','running',1) RETURNING id",(kind,)).fetchone()[0]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(Job(job_id,kind,'article',1),1,'failure',code='model-invalid')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'=%s AND finished IS NULL",('producer:'+kind,)).fetchone()
                raise ValueError('rollback')
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'=%s AND finished IS NULL",('producer:'+kind,)).fetchone()
        store.jobs.retry(Job(job_id,kind,'article',1),1,'failure',code='model-invalid')
        row=store.db.execute("SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
        assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
        with store.db:store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job_id,))
        with transaction(store.db):store.jobs.finish(Job(job_id,kind,'article',2))
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_settings_reconciliation_and_problem_roll_back_together(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.subscriptions.subscriptions import Overrides
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            row=store.db.execute("UPDATE jobs SET status='pending',reason_message='failure',reason_code='model-invalid' WHERE kind='filter' AND item=%s RETURNING id",(item,)).fetchone()
            store.problems.sync_processing_problem(row['id'],[],1)
            before=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(row['id']),)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.subscriptions.update_settings('feed','fixture',Overrides(filter_enabled=False))
                store.settings_changed('feed','fixture')
                assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='blocked'
                raise ValueError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='pending'
        after=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_settings_reenable_blocked_processing_with_same_input(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.subscriptions.subscriptions import Overrides
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        before=store.db.execute("SELECT id,input_snapshot FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()
        for enabled,expected in [(False,'blocked'),(True,'pending')]:
            with transaction(store.db):
                store.subscriptions.update_settings('feed','fixture',Overrides(filter_enabled=enabled))
                store.settings_changed('feed','fixture')
            after=store.db.execute('SELECT id,input_snapshot,status FROM jobs WHERE id=%s',(before['id'],)).fetchone()
            assert (after['id'],after['input_snapshot'],after['status'])==(before['id'],before['input_snapshot'],expected)
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_http_status_reads_recovered_processing_without_mutation(tmp_path,postgres_url,kind):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
        db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES (%s,'article','failed','failure','model-invalid')",(kind,))
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['id']==before[0] for p in response.json()['problems']['current'])
        etag=response.headers.get('etag')
        if etag:assert client.get('/api/v3/status/details',headers={'If-None-Match':etag}).status_code==304
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before


def test_processing_retry_clears_previous_reason_without_shortening_source_wait(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            deadline=store.wall_clock()+3600
            row=store.db.execute("UPDATE jobs SET status='failed',generation=5,reason_code='source-wait',reason_message='old',wait_scope='source',retry_at=%s,next=123 WHERE kind='filter' AND item=%s RETURNING id",(deadline,item)).fetchone()
            assert row is not None
            assert store.jobs.retry_processing(item,'filter')
            job=store.db.execute('SELECT status,generation,wait_scope,retry_at,reason_code,reason_message FROM jobs WHERE id=%s',(row['id'],)).fetchone()
            assert tuple(job)==('pending',6,'source',deadline,None,None)
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_processing_diagnostics_reads_job_reason(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES (%s,'article','failed','Current explanation','model-invalid') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],store.wall_clock())
            row=store.db.execute("SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
            assert row and json.loads(row['body'])['reason']=='Current explanation'
    finally:store.db.close()


def test_changed_processing_input_clears_previous_job_reason(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            row=store.db.execute("UPDATE jobs SET status='failed',reason_code='model-invalid',reason_message='old' WHERE kind='filter' AND item=%s RETURNING id",(item,)).fetchone()
            assert row is not None
            store.jobs.reconcile('filter',[(item,'changed-input-signature',False,'new-revision')])
            job=store.db.execute('SELECT status,reason_code,reason_message FROM jobs WHERE id=%s',(row['id'],)).fetchone()
            assert tuple(job)==('pending',None,None)
    finally:store.db.close()


def test_processing_wait_uses_current_job_reason(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            row=store.db.execute("UPDATE jobs SET status='pending',reason_code='ai-permission',reason_message='Current permission error' WHERE kind='filter' AND item=%s RETURNING id",(item,)).fetchone()
            assert row is not None
            wait=store.processing.waiting(item,'filter',store.clock(),store.wall_clock())
            assert wait is not None and wait.reason=='public_ai_not_allowed'
            store.db.execute('UPDATE jobs SET reason_message=NULL WHERE id=%s',(row['id'],))
            wait=store.processing.waiting(item,'filter',store.clock(),store.wall_clock())
            assert wait is not None and wait.reason=='public_ai_not_allowed'
    finally:store.db.close()


def test_removing_feed_clears_reason_of_cancelled_processing(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            row=store.db.execute("UPDATE jobs SET status='cancelled',input_snapshot=NULL,reason_code='old-code',reason_message='old' WHERE kind='filter' AND item=%s RETURNING id",(item,)).fetchone()
            store.db.execute('UPDATE publications SET feed_id=NULL WHERE id=%s',(item,))
            store.processing.memberships_removed([item])
            job=store.db.execute('SELECT status,reason_code,reason_message FROM jobs WHERE id=%s',(row['id'],)).fetchone()
            assert tuple(job)==('cancelled',None,None)
    finally:store.db.close()


def test_processing_waits_for_pending_initial_collection_even_with_text(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
            from oko.storage.processing_state import ProcessingRepository
            preparation=ProcessingRepository(store).prepare(item,'filter')
            assert preparation.outcome=='waiting' and preparation.reason=='input_pending'
    finally:store.db.close()


def test_processing_uses_saved_content_after_collection(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.storage.processing_state import ProcessingRepository
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("UPDATE publications SET status='pending_filtering' WHERE id=%s",(item,))
            preparation=ProcessingRepository(store).prepare(item,'filter')
            assert preparation.outcome=='model' and preparation.input is not None
            assert ProcessingRepository(store).candidates('filter',item=item)
    finally:store.db.close()


def test_work_status_uses_job_reason_and_ignores_completed_absence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
            store.db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('filter','article','pending','model-invalid','Current explanation')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('sub','article','subtitle','','subtitle','unavailable')")
            store.db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('download_media','sub','done','subtitles_absent','No subtitles')")
            work=store.work_summary(include_problems=True)
            assert len(work)==1 and work[0].error_code=='model-invalid'
            assert store.status()['errors']==['Current explanation']
            deadline=store.wall_clock()+120
            db=store.db
            db.execute("UPDATE jobs SET reason_code=NULL,reason_message=NULL,wait_scope='source',retry_at=%s,next=%s WHERE kind='filter' AND item='article'",(deadline,store.clock()+120))
            waiting=store.work_summary()
            assert len(waiting)==1 and waiting[0].error_code=='source-wait' and waiting[0].retry_at is not None
            db.execute("UPDATE jobs SET status='failed',wait_scope=NULL,retry_at=NULL WHERE kind='filter' AND item='article'")
            failed=store.work_summary(include_problems=True)
            assert len(failed)==1 and failed[0].status=='failed' and failed[0].error_code=='filter-failed'
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_failed_processing_without_optional_reason_is_visible(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status) VALUES (%s,'article','failed') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],store.wall_clock())
            row=store.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL',('job:work-'+kind+':feed:'+kind+':article',)).fetchone()
            assert row and json.loads(row['body'])['objects']==[dict(kind='material',id='article')]
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_restart_recovers_failed_processing_without_optional_reason(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db:
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
        store.db.execute("INSERT INTO jobs(kind,item,status) VALUES (%s,'article','failed')",(kind,))
    store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        rows=store.problems.snapshot(store.wall_clock())['current']
        assert any(p['code']=='work-'+kind and any(o['id']=='article' for o in p['objects']) for p in rows)
    finally:store.db.close()
