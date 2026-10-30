import json
import pytest
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store
from tests.support.read_problem import setup


def test_read_failure_retry_and_finish_are_atomic_without_status_request(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'read','feed',1)
        def current():
            return store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(job,1,'failure',code='source-connection')
                assert current() is not None
                raise ValueError('rollback')
        assert current() is None
        store.jobs.retry(job,1,'failure',code='source-connection')
        old=current();assert old
        assert not [candidate for candidate in store.problems.candidates(2) if candidate.get('operation')=='producer:read']
        before=store.db.execute('SELECT xmin::text,body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
        state=store.problems.snapshot(2)
        assert [p['id'] for p in state['current'] if p['code']=='source-connection']==[old['id']]
        assert store.db.execute('SELECT xmin::text,body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()==before
        with store.db:store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=store.jobs.next_job(1,feeds=['feed'])
        assert claimed and claimed.id==job_id
        assert json.loads(current()['body'])['state']=='retrying'
        with transaction(store.db):store.jobs.finish(claimed)
        assert current() is None
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_profile_problem_suppresses_and_releases_read_error_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'read','feed',1),1,'failure',code='identity-unconfirmed')
        identity='job:identity-unconfirmed:feed:read:feed'
        original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                assert not store.check_telegram_profile('personal',1,202,2)
                assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
                raise ValueError('rollback')
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]==original
        assert not store.check_telegram_profile('personal',1,202,2)
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert store.check_telegram_profile('personal',1,101,3)
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()


def test_read_state_error_controls_problem_without_status_request(tmp_path,postgres_url):
    from oko.storage.read_sync import ReadPlan
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','pending','waiting',NULL)")
        identity='job:work-read:feed:read:feed'
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        store.read_sync.failed('feed','sync failed')
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        with transaction(store.db):store.read_sync.completed('feed',ReadPlan(0,0,0,0),0)
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()


def test_legacy_read_problems_recover_without_get_and_resume_after_fault(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='read-problems-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT 'f-'||n,'f-'||n,'Feed','default','rss','rss','connected','[]' FROM generate_series(1,101) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'read','f-'||n,'failed','failure','source-connection' FROM generate_series(1,101) n")
        store.db.close()
        def fault(point):
            if point=='read_problem_recovery_batch':raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError,match='interrupted'):
            Store(tmp_path,fault,database_url=postgres_url)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()[0]==101
        assert store.db.execute("SELECT 1 FROM state WHERE key='read-problems-recovered-v1'").fetchone()
    finally:store.db.close()


def test_restart_recovers_running_read_problem_as_pending(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('read','feed','running',1,'failure','source-connection') RETURNING id").fetchone()[0]
            store.problems.sync_read_feed('feed',1)
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()
        assert json.loads(before['body'])['state']=='retrying'
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        after=store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(before['id'],)).fetchone()
        assert after and json.loads(after['body'])['state']=='current'
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job_id,)).fetchone()[0]=='pending'
    finally:store.db.close()


def test_legacy_stale_read_problem_is_closed_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        old=dict(id='job:source-connection:feed:read',code='source-connection',section='object',title='Ошибка',consequence='Доступно',next_step='Повторите',objects=[dict(kind='feed',id='feed')],action=None,created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
        with store.db:
            store.problems.sync_candidate(old['id'],old,1)
            row=store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()
            body=json.loads(row['body']);body.pop('operation',None)
            store.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),row['id']))
            store.db.execute("DELETE FROM state WHERE key='read-problems-recovered-v1'")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row['id'],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_legacy_read_problem_keeps_identity_on_recovery(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('other','other','Other','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('read','feed','failed','failure','source-connection','other')")
            old=dict(id='job:source-connection:other:read',code='source-connection',section='object',title='Ошибка',consequence='Доступно',next_step='Повторите',objects=[dict(kind='feed',id='other')],action=None,operation='producer:read',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
            store.problems.sync_candidate(old['id'],old,1)
            original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
            store.db.execute("DELETE FROM state WHERE key='read-problems-recovered-v1'")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        current=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()
        assert current and current[0]==original
    finally:store.db.close()


def test_legacy_empty_object_problem_is_recovered_by_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        old=dict(id='job:source-connection:feed:read',code='source-connection',section='object',title='Ошибка',consequence='Доступно',next_step='Повторите',objects=[],action=None,operation='producer:read',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
        with store.db:
            store.problems.sync_candidate(old['id'],old,1)
            row_id=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
            store.db.execute("DELETE FROM state WHERE key='read-problems-recovered-v1'")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(row_id,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_feed_configuration_reconciles_read_problem_and_rolls_back(tmp_path,postgres_url):
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import FeedPatch
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','waiting')")
            store.problems.sync_read_feed('feed',1)
        identity='job:work-read:feed:read:feed'
        def active():return store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        original=active()[0]
        service=SubscriptionService(lambda:store)
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                service.patch_feed('feed',FeedPatch(auto_read=None))
                assert active() is None
                raise ValueError('rollback')
        assert active()[0]==original
        service.patch_feed('feed',FeedPatch(auto_read=None))
        assert active() is None
    finally:store.db.close()


def test_feed_removal_closes_read_problem_with_rollback(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'read','feed',1),1,'failure',code='source-connection')
        identity='job:source-connection:feed:read'
        def active():return store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        original=active()[0]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.remove_feed('feed')
                store.jobs.remove_feed('feed',set())
                assert active() is None
                raise ValueError('rollback')
        assert active()[0]==original
        with transaction(store.db):
            store.remove_feed('feed')
            store.jobs.remove_feed('feed',set())
        assert active() is None
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(original,)).fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_read_processor_configuration_error_remains_in_shared_projection(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','processor-config')")
            store.problems.sync_read_feed('feed',1)
        state=store.problems.snapshot(2)
        assert any(p['code']=='processor-config' for p in state['current'])
    finally:store.db.close()


def test_read_failure_rolls_back_when_problem_persistence_fails(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
        def fail(*args,**kwargs):raise RuntimeError('problem failure')
        with monkeypatch.context() as patch:
            patch.setattr(store.problems,'sync_read_feed',fail)
            with pytest.raises(RuntimeError,match='problem failure'):
                store.jobs.retry(Job(job_id,'read','feed',1),1,'failure',code='source-connection')
        row=store.db.execute('SELECT status,error,problem_code FROM jobs WHERE id=%s',(job_id,)).fetchone()
        assert tuple(row)==('running',None,None)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()
    finally:store.db.close()


def test_rekey_keeps_legacy_group_still_needed_by_other_feed(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('other','other','Other','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,feed_id) VALUES ('read','feed','failed','failure','source-connection','other'),('read','other','failed','failure','source-connection','other')")
            old=dict(id='job:source-connection:other:read',code='source-connection',section='object',title='Ошибка',consequence='Доступно',next_step='Повторите',objects=[dict(kind='feed',id='other')],action=None,operation='producer:read',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
            store.problems.sync_candidate(old['id'],old,1)
            original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
            store.problems.sync_read_feed('feed',2)
            store.problems.sync_read_feed('other',2)
        rows=store.db.execute("SELECT identity,id FROM reader_problems WHERE identity LIKE 'job:source-connection:%%' AND finished IS NULL ORDER BY identity").fetchall()
        assert [row['identity'] for row in rows]==['job:source-connection:feed:read','job:source-connection:other:read']
        assert rows[1]['id']==original
    finally:store.db.close()


def test_http_status_does_not_write_read_problem(tmp_path,postgres_url):
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True)
    with TestClient(app,headers=HEADERS) as client:
        def arrange():
            store=app.state.host.require_store()
            setup(store)
            with store.db:
                job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','feed','running',1) RETURNING id").fetchone()[0]
            store.jobs.retry(Job(job_id,'read','feed',1),1,'failure',code='source-connection')
            row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()
            return row[0]
        identity=client.portal.call(arrange)
        statements=[]
        client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(statements.append))
        try: response=client.get('/api/v3/status/details')
        finally:client.portal.call(lambda:app.state.host.require_store().db.set_trace_callback(None))
        assert response.status_code==200
        assert any(p['id']==identity for p in response.json()['problems']['current'])
        assert not any('UPDATE reader_problems' in sql or 'INSERT INTO reader_problems' in sql for sql in statements),statements


def test_stale_read_finish_does_not_publish_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('read','feed','running',2,'failure','source-connection') RETURNING id").fetchone()[0]
        with transaction(store.db):
            store.jobs.finish_read(Job(job_id,'read','feed',1),'waiting',1)
        assert store.db.execute("SELECT status,generation FROM jobs WHERE id=%s",(job_id,)).fetchone() == ('running',2)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:read'").fetchone()
    finally:store.db.close()


def test_unidentifiable_legacy_read_problem_is_not_silently_discarded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        old=dict(id='job:malformed',code='source-connection',section='object',title='Ошибка',consequence='Доступно',next_step='Повторите',objects=[],action=None,operation='producer:read',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
        with store.db:
            store.problems.sync_candidate(old['id'],old,1)
            row_id=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
            store.db.execute("DELETE FROM state WHERE key='read-problems-recovered-v1'")
        store.db.close()
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(row_id,)).fetchone()[0] is None
        assert store.db.execute("SELECT 1 FROM state WHERE key='read-problems-recovered-v1'").fetchone()
    finally:store.db.close()


def test_read_job_feed_wins_over_same_id_publication_in_status(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('other','other','Other','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('feed','rss','same-id',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('other','feed',true,true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','source-connection')")
            store.problems.sync_read_feed('feed',1)
        problems=store.problems.snapshot(2)['current']
        read=next(p for p in problems if p['operation']=='producer:read')
        assert read['objects']==[dict(kind='feed',id='feed')]
        assert store.db.execute('SELECT identity FROM reader_problems WHERE id=%s',(read['id'],)).fetchone()[0]=='job:source-connection:feed:read'
    finally:store.db.close()


def test_success_with_normal_wait_closes_failure_without_new_problem(tmp_path,postgres_url):
    from oko.storage.read_sync import ReadPlan
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('read','feed','running',1,'failure','source-connection') RETURNING id").fetchone()[0]
            store.problems.sync_read_feed('feed',1)
        original=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:feed:read' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.read_sync.completed('feed',ReadPlan(0,0,0,0,waiting='Ожидает более раннее сообщение Telegram'),0)
            store.jobs.finish_read(Job(job_id,'read','feed',1),'Ожидает более раннее сообщение Telegram',2)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(original,)).fetchone()[0]=='resolved'
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:read' AND finished IS NULL").fetchone()[0]==0
        assert store.db.execute('SELECT status,error,problem_code FROM jobs WHERE id=%s',(job_id,)).fetchone()==('pending','Ожидает более раннее сообщение Telegram',None)
    finally:store.db.close()


def test_empty_legacy_read_job_closes_its_demo_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.subscriptions.ensure_demo()
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('read','','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'read','',1)
        store.jobs.retry(job,1,'failure',code='source-connection')
        identity='job:source-connection:demo:read'
        original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]
        with store.db:store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job_id,))
        claimed=store.jobs.next_job(1,feeds=['demo'])
        assert claimed and claimed.id==job_id
        with transaction(store.db):store.jobs.finish(claimed)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(original,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_demo_alias_read_jobs_share_problem_until_both_finish(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.subscriptions.ensure_demo()
            rows=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','','failed','failure','source-connection'),('read','demo','failed','failure','source-connection') RETURNING id,item").fetchall()
            store.problems.sync_read_feed('',1)
            store.problems.sync_read_feed('demo',1)
        identity='job:source-connection:demo:read'
        current=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        assert current
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE kind='read' AND item='' ")
            store.problems.sync_read_feed('',2)
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0]==current[0]
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE kind='read' AND item='demo'")
            store.problems.sync_read_feed('demo',3)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(current[0],)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_saved_backfill_suppresses_then_restores_read_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','waiting')")
            store.problems.sync_read_feed('feed',1)
        identity='job:work-read:feed:read:feed'
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        with transaction(store.db):store.read_sync.schedule_saved_backfill('feed')
        assert not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        list(store.jobs.sync_reads_steps())
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
    finally:store.db.close()


def test_read_problem_update_does_not_scan_large_channel_history(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute('INSERT INTO initial_history(feed,selection,frozen,scan_complete) VALUES (%s,%s,1,1)',('feed',json.dumps(dict(mode='count',count=10,since=None))))
            store.db.execute("INSERT INTO history_members(feed,item) SELECT 'feed','foreign-'||n FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO telegram_read_members(feed,member,viewed,saved) SELECT 'feed',n,true,false FROM generate_series(1,10000) n")
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','failed','failure')")
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:
            with store.db:store.problems.sync_read_feed('feed',1)
        finally:store.db.set_trace_callback(None)
        assert not any('history_members' in q or 'telegram_read_members' in q for q in statements),statements
        assert len(statements)<20,len(statements)
    finally:store.db.close()


def test_read_recovery_page_ignores_foreign_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'read','feed-'||n,'failed','failure','source-connection' FROM generate_series(1,100) n")
            store.db._db.execute('ANALYZE jobs')
        sql="SELECT id,item FROM jobs WHERE kind='read' AND id>%s AND (error IS NOT NULL OR problem_code IS NOT NULL) ORDER BY id LIMIT 100"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(0,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_read_recovery_late_page_is_bounded(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'read','feed-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        cursor=store.db.execute("SELECT min(id)+9000 FROM jobs WHERE kind='read'").fetchone()[0]
        sql="SELECT id,item FROM jobs WHERE kind='read' AND id>%s AND (error IS NOT NULL OR problem_code IS NOT NULL) ORDER BY id LIMIT 100"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(cursor,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_read_recovery_interleaved_jobs_is_bounded(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT CASE WHEN n%100=0 THEN 'read' ELSE 'fetch' END,'item-'||n,'failed','failure','source-connection' FROM generate_series(1,100000) n")
            store.db._db.execute('ANALYZE jobs')
        sql="SELECT id,item FROM jobs WHERE kind='read' AND id>%s AND (error IS NOT NULL OR problem_code IS NOT NULL) ORDER BY id LIMIT 100"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(0,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
        first_sql=sql.replace(' AND id>%s','')
        first=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+first_sql).fetchone()[0][0]['Plan']
        assert scan_rows(first)<1000,first
    finally:store.db.close()


def test_read_problem_recovery_page_ignores_unrelated_problems(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'p-'||n,CASE WHEN n%100=0 THEN 'job:source-connection:f-'||n||':read' ELSE 'job:source-connection:f-'||n||':fetch' END,
                       'background',jsonb_build_object('code','source-connection','operation',CASE WHEN n%100=0 THEN 'producer:read' ELSE 'producer:fetch' END,'objects','[]'::jsonb)::text,1
                FROM generate_series(1,100000) n""")
            store.db._db.execute('ANALYZE reader_problems')
        sql="""SELECT id,identity,body FROM reader_problems WHERE id>%s
            AND finished IS NULL AND identity LIKE 'job:%%' AND coalesce(body::jsonb->>'code','')!='processor-config'
            AND (body::jsonb->>'operation'='producer:read' OR ((body::jsonb->>'operation' IS NULL OR body::jsonb->>'operation'='producer:status')
                AND (identity LIKE '%%:read' OR identity LIKE '%%:read:%%')))
            ORDER BY id LIMIT 100"""
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,('',)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
        first_sql=sql.replace('id>%s\n            AND ', '').replace('%%','%')
        first=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+first_sql).fetchone()[0][0]['Plan']
        assert scan_rows(first)<1000,first
    finally:store.db.close()


def test_profile_switch_selects_only_affected_bindings(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) SELECT 'foreign-'||n,'foreign-'||n,'Feed','default','telegram','telegram','connected','[]' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO telegram_bindings(feed,binding,indexed_role) SELECT 'foreign-'||n,'{\"kind\":\"client\",\"role\":\"collection\"}','collection' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE telegram_bindings')
        sql="SELECT feed FROM telegram_bindings WHERE indexed_role=ANY(%s) OR (indexed_role IS NULL AND feed=ANY(%s))"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(['personal'],['personal'])).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_read_sync_selects_only_problem_for_affected_feed(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'foreign-'||n,'job:source-connection:foreign-'||n||':read','background',
                    jsonb_build_object('code','source-connection','operation','producer:read','objects',jsonb_build_array(jsonb_build_object('kind','feed','id','foreign-'||n)))::text,1
                FROM generate_series(1,10000) n""")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome)
                SELECT 'old-'||n,'job:source-old-'||n||':feed:read','background',
                    jsonb_build_object('code','source-connection','operation','producer:read','objects',jsonb_build_array(jsonb_build_object('kind','feed','id','feed')))::text,1,2,'resolved'
                FROM generate_series(1,10000) n""")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','source-connection')")
            store.db._db.execute('ANALYZE reader_problems')
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:
            with store.db:store.problems.sync_read_feed('feed',1)
        finally:store.db.set_trace_callback(None)
        sql=next(q for q in statements if 'WITH affected AS MATERIALIZED' in q and '@>' in q)
        needle=json.dumps([dict(kind='feed',id='feed')])
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(needle,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
    finally:store.db.close()


def test_read_state_failure_and_problem_roll_back_together(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','waiting')")
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.read_sync.failed('feed','failure')
                assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:work-read:feed:read:feed' AND finished IS NULL").fetchone()
                raise ValueError('rollback')
        assert store.db.execute("SELECT error FROM telegram_read_state WHERE feed='feed'").fetchone()[0] is None
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:work-read:feed:read:feed'").fetchone()
    finally:store.db.close()
