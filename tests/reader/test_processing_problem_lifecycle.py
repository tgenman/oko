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
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
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


@pytest.mark.parametrize('kind',['filter','theses'])
def test_processing_cancel_closes_occurrence_without_get(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES (%s,'article','running',1,'failure','model-invalid') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            record=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
        with transaction(store.db):store.jobs.cancel(Job(job,kind,'article',1))
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(record,)).fetchone()[0]=='resolved'
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_successful_processing_finish_has_no_problem_lookup(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'article','running',1) RETURNING id",(kind,)).fetchone()[0]
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with transaction(store.db):store.jobs.finish(Job(job,kind,'article',1))
        finally:store.db.set_trace_callback(None)
        print(f'{kind} successful finish SQL: {len(sql)}')
        assert not any('processing_job_id' in q for q in sql),sql
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_processing_failure_is_recovered_at_startup(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','model-invalid')",(kind,))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_status_does_not_rewrite_processing_occurrence(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','model-invalid') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            before=store.db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
        assert not [c for c in store.problems.candidates(2) if c.get('operation')=='producer:'+kind and c.get('code')=='model-invalid']
        store.problems.snapshot(2)
        after=store.db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


def test_recovery_keeps_filter_and_theses_as_independent_occurrences(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='model-invalid',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:status',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','failed','failure','model-invalid'),('theses','article','failed','failure','model-invalid')")
            for kind in ('filter','theses'):
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",('old-'+kind,'job:model-invalid:feed:'+kind+':article',json.dumps(body)))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        for kind in ('filter','theses'):
            row=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:feed:'+kind+':article',)).fetchone()
            assert row and row[0]=='old-'+kind
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_removing_primary_feed_preserves_processing_occurrence(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','rss','x')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','article',true),('b','article',false)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','model-invalid') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:a:'+kind+':article',)).fetchone()[0]
        store.remove_feed('a')
        after=store.db.execute("SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:model-invalid:b:'+kind+':article',)).fetchone()
        assert after and after[0]==before
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_stage_reconciliation_closes_cancelled_error_without_get(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','pending','failure','model-invalid') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            record=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            store.jobs.reconcile(kind,[('article',None,False,'')])
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(record,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_processor_configuration_remains_on_existing_status_path(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','pending','missing model','processor-config'),('theses','article','pending','missing model','processor-config')")
            ids=[row['id'] for row in store.db.execute("SELECT id FROM jobs WHERE item='article'")]
            store.problems.finish_processor_change(ids,None,store.wall_clock())
        matches=[p for p in store.problems.snapshot(1)['current'] if p['code']=='processor-config']
        assert len(matches)==1 and matches[0]['code']=='processor-config'
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_explicit_work_code_is_not_lost(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'article','running',1) RETURNING id",(kind,)).fetchone()[0]
        store.jobs.retry(Job(job,kind,'article',1),1,'failure',code='work-'+kind)
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND body::jsonb->>'code'=%s AND finished IS NULL",(str(job),'work-'+kind)).fetchone()
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_null_error_code_owned_by_processing_event(tmp_path,postgres_url,kind):
    from oko.storage.processing_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure',NULL) RETURNING id",(kind,)).fetchone()[0]
        with store.db:store.problems.sync_processing_problem(job,[],1)
        assert managed_candidates(store,job,1)[0]['code']=='work-'+kind
        assert any(c['code']=='work-'+kind for c in store.problems.snapshot(1)['current'])
        assert not any(c['code']=='work-'+kind for c in store.problems.candidates(1))
    finally:store.db.close()


def test_recovery_mixed_processing_jobs_is_batched(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT CASE WHEN n%2=0 THEN 'filter' ELSE 'theses' END,'f-'||n,'failed','failure','model-invalid' FROM generate_series(1,1000) n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:model-invalid:f-'||n||':'||CASE WHEN n%2=0 THEN 'filter' ELSE 'theses' END||':f-'||n,'background',jsonb_build_object('code','model-invalid','section','object','title','x','consequence','c','next_step','n','operation','producer:status','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','f-'||n)))::text,1 FROM generate_series(1,1000,100) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_processing_problems()
        finally:store.db.set_trace_callback(None)
        print('mixed processing recovery SQL:',len(sql))
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation' IN ('producer:filter','producer:theses') AND finished IS NULL").fetchone()[0]==1000
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==10
    finally:store.db.close()


def test_filter_collision_with_foreign_producer_preserves_both_occurrences(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    foreign=dict(code='model-invalid',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:fetch',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','failed','failure','model-invalid') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:model-invalid:feed:filter:article','background',%s,1)",(json.dumps(foreign),))
            store.problems.sync_processing_problem(job,[],1)
            assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
            row=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
            assert row and row['identity'].startswith('job:processing-isolated:')
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        with store.db:
            store.db.execute("UPDATE reader_problems SET finished=2,outcome='resolved' WHERE id='foreign'")
            store.problems.sync_processing_problem(job,[],3)
        again=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert tuple(again)==tuple(row)
    finally:store.db.close()


def test_processing_recovery_resumes_after_batch_including_nonpositive_ids(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(id,kind,item,status,error,problem_code) SELECT n,'filter','f-'||n,'failed','failure','model-invalid' FROM generate_series(-1,100) n")
        store.db.close()
        def fault(point):
            if point=='processing_problem_recovery_batch':raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError,match='interrupted'):Store(tmp_path,fault,database_url=postgres_url)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:filter' AND finished IS NULL").fetchone()[0]==102
        assert store.db.execute("SELECT 1 FROM state WHERE key='processing-problems-recovered-v2'").fetchone()
    finally:store.db.close()


def test_status_ignores_ten_thousand_managed_processing_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT CASE WHEN n%2=0 THEN 'filter' ELSE 'theses' END,'foreign-'||n,'failed','failure','model-invalid' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.candidates(1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'SELECT jobs . *' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_settings_reconciliation_updates_existing_error_without_get(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.subscriptions.subscriptions import Overrides
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            row=store.db.execute("UPDATE jobs SET status='pending',error='failure',problem_code='model-invalid' WHERE kind='filter' AND item=%s RETURNING id",(item,)).fetchone()
            store.problems.sync_processing_problem(row['id'],[],1)
            record=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(row['id']),)).fetchone()[0]
        with transaction(store.db):
            store.subscriptions.update_settings('feed','demo',Overrides(filter_enabled=False))
            store.settings_changed('feed','demo')
            status=store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]
            problem=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(record,)).fetchone()
            assert status=='blocked' and problem['finished'] is None and json.loads(problem['body'])['next_step']=='Включите обработку, чтобы повторить попытку.'
    finally:store.db.close()


def test_settings_reconciliation_and_problem_roll_back_together(tmp_path,postgres_url):
    from tests.support.worker_library import worker_library
    from oko.subscriptions.subscriptions import Overrides
    store,item,_,_=worker_library(tmp_path,database_url=postgres_url)
    try:
        with store.db:
            row=store.db.execute("UPDATE jobs SET status='pending',error='failure',problem_code='model-invalid' WHERE kind='filter' AND item=%s RETURNING id",(item,)).fetchone()
            store.problems.sync_processing_problem(row['id'],[],1)
            before=store.db.execute("SELECT id,body,finished FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(row['id']),)).fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.subscriptions.update_settings('feed','demo',Overrides(filter_enabled=False))
                store.settings_changed('feed','demo')
                assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='blocked'
                raise ValueError('rollback')
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(row['id'],)).fetchone()[0]=='pending'
        after=store.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=%s',(before['id'],)).fetchone()
        assert tuple(after)==tuple(before)
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_source_code_remains_on_existing_status_path(tmp_path,postgres_url,kind):
    from oko.storage.processing_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','source-shared') RETURNING id",(kind,)).fetchone()[0]
        assert managed_candidates(store,job,1)==[]
        assert any(c['id']=='job:source-shared:feed:'+kind for c in store.problems.candidates(1))
    finally:store.db.close()


def test_recovery_existing_processing_errors_is_batched(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','f-'||n,'failed','failure','model-invalid' FROM generate_series(1,1000) n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'old-'||n,'job:model-invalid:f-'||n||':filter:f-'||n,'background',jsonb_build_object('code','model-invalid','section','object','title','x','consequence','c','next_step','n','operation','producer:filter','state','current','objects',jsonb_build_array(jsonb_build_object('kind','material','id','f-'||n)))::text,1 FROM generate_series(1,1000) n")
        sql=[];store.db.set_trace_callback(sql.append)
        try:store.problems.recover_processing_problems()
        finally:store.db.set_trace_callback(None)
        print('existing processing recovery SQL:',len(sql))
        assert len(sql)<500,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'old-%%' AND finished IS NULL").fetchone()[0]==1000
    finally:store.db.close()


def test_inconsistent_legacy_stage_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='model-invalid',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:filter',objects=[dict(kind='material',id='article')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:model-invalid:feed:theses:article','background',%s,1)",(json.dumps(body),))
        store.problems.recover_processing_problems();store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_unverified_legacy_processing_without_job_survives_recovery_and_get(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='model-invalid',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='unrelated')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('unrelated')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",('job:model-invalid:feed:'+kind+':ghost',json.dumps(body)))
        store.problems.recover_processing_problems();store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_manual_processing_retry_updates_occurrence_without_get(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,input_snapshot) VALUES (%s,'article','failed','failure','model-invalid',%s) RETURNING id",(kind,json.dumps({'signature':'old'}))).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            assert store.jobs.retry_processing('article',kind)
            row=store.db.execute('SELECT body FROM reader_problems WHERE id=%s AND finished IS NULL',(before,)).fetchone()
            assert row and json.loads(row[0])['state']=='retrying'
    finally:store.db.close()


def test_negative_processing_job_id_closes_problem_on_success(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(id,kind,item,status,generation,error,problem_code) VALUES (-1,'filter','article','running',1,'failure','model-invalid')")
            store.problems.sync_processing_problem(-1,[],1)
            old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'='-1' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):store.jobs.finish(Job(-1,'filter','article',1))
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()[0]=='resolved'
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
@pytest.mark.parametrize('marked',[False,True])
def test_unverified_mixed_materials_are_not_reassigned(tmp_path,postgres_url,kind,marked):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article'),('unrelated')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','model-invalid') RETURNING id",(kind,)).fetchone()[0]
            body=dict(code='model-invalid',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:'+kind if marked else 'producer:status',objects=[dict(kind='material',id='article'),dict(kind='material',id='unrelated')])
            if marked:body.update(processing_job_id=str(job),processing_kind=kind)
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",('job:model-invalid:feed:'+kind+':article',json.dumps(body)))
        store.problems.recover_processing_problems();store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_processing_projection_ignores_unrelated_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    from oko.storage.processing_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','failed','failure','model-invalid') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','foreign-'||n,'failed','failure','model-invalid' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:assert managed_candidates(store,job,1)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if q.lstrip().startswith('SELECT j.id,j.kind'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,([job],)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_sync_thousand_changed_processing_problems_is_batched(tmp_path,postgres_url):
    from oko.storage.processing_job_problems import managed_candidates_many
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','f-'||n,'pending','failure','model-invalid' FROM generate_series(1,1000) n")
            ids=[row[0] for row in store.db.execute("SELECT id FROM jobs WHERE kind='filter' ORDER BY id")]
            for start in range(0,len(ids),100):store.problems.recover_processing_batch([dict(id=job_id) for job_id in ids[start:start+100]],1)
            previous=managed_candidates_many(store,ids,2)
            store.db.execute("UPDATE jobs SET status='running' WHERE kind='filter'")
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with store.db:store.problems.sync_processing_problems(previous,2)
        finally:store.db.set_trace_callback(None)
        print('1000 changed processing problems SQL:',len(sql))
        assert len(sql)<100,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:filter' AND body::jsonb->>'state'='retrying' AND finished IS NULL").fetchone()[0]==1000
    finally:store.db.close()


def test_close_thousand_processing_problems_is_batched(tmp_path,postgres_url):
    from oko.storage.processing_job_problems import managed_candidates_many
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','f-'||n,'pending','failure','model-invalid' FROM generate_series(1,1000) n")
            ids=[row[0] for row in store.db.execute("SELECT id FROM jobs WHERE kind='filter' ORDER BY id")]
            for start in range(0,len(ids),100):store.problems.recover_processing_batch([dict(id=job_id) for job_id in ids[start:start+100]],1)
            previous=managed_candidates_many(store,ids,2)
            store.db.execute("UPDATE jobs SET status='cancelled',error=NULL,problem_code=NULL WHERE kind='filter'")
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with store.db:store.problems.sync_processing_problems(previous,2)
        finally:store.db.set_trace_callback(None)
        print('1000 closed processing problems SQL:',len(sql))
        assert len(sql)<100,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:filter' AND finished IS NOT NULL").fetchone()[0]==1000
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_http_status_reads_recovered_processing_without_mutation(tmp_path,postgres_url,kind):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id) VALUES ('article')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','model-invalid')",(kind,))
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


def test_batch_state_and_problem_rollback_together(tmp_path,postgres_url):
    from oko.storage.processing_job_problems import managed_candidates_many
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','f-'||n,'pending','failure','model-invalid' FROM generate_series(1,10) n")
            ids=[row[0] for row in store.db.execute("SELECT id FROM jobs WHERE kind='filter' ORDER BY id")]
            store.problems.recover_processing_batch([dict(id=job) for job in ids],1)
            previous=managed_candidates_many(store,ids,2)
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.db.execute("UPDATE jobs SET status='running' WHERE id=ANY(%s)",(ids,))
                store.problems.sync_processing_problems(previous,2)
                assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'state'='retrying' AND finished IS NULL").fetchone()[0]==10
                raise ValueError('rollback')
        assert store.db.execute("SELECT count(*) FROM jobs WHERE status='pending' AND id=ANY(%s)",(ids,)).fetchone()[0]==10
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'state'='current' AND finished IS NULL").fetchone()[0]==10
    finally:store.db.close()


def test_unidentifiable_legacy_processing_identity_survives_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='model-invalid',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:status',objects=[dict(kind='material',id='article')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:model-invalid:feed:filter','background',%s,1)",(json.dumps(body),))
        store.problems.recover_processing_problems();store.problems.snapshot(2)
        row=store.db.execute("SELECT finished,body FROM reader_problems WHERE id='old'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()


def test_discovery_owner_probe_ignores_ten_thousand_unrelated_processing_jobs(tmp_path,postgres_url):
    from oko.collection.contracts import Publication
    from tests.support.source import publication_packet
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo()
        page=[publication_packet(Publication(str(n),f'https://example.test/{n}',n)) for n in range(50)]
        with transaction(store.db):
            effects=store.collect_discovery(page,'{}');store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/0'").fetchone()[0]
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','foreign-'||n,'failed','failure','model-invalid' FROM generate_series(1,10000) n")
            store.db._db.execute('ANALYZE jobs')
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with transaction(store.db):store.collect_discovery(page,'{}')
        finally:store.db.set_trace_callback(None)
        query=next(q for q in sql if 'AS processing_problem_exists' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+query,(item,)).fetchone()[0][0]
        assert scan_rows(plan['Plan'])<1000,plan
    finally:store.db.close()


def test_recovery_keeps_oldest_processing_occurrence_when_current_identity_exists(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='model-invalid',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='article')],action=None,operation='producer:filter',state='current',next_at=None)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','failed','failure','model-invalid')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:model-invalid:a:filter:article','background',%s,1),('new','job:model-invalid:b:filter:article','background',%s,2)",(json.dumps(body),json.dumps(body)))
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:model-invalid:b:filter:article' AND finished IS NULL").fetchone()
        assert row and row[0]=='old'
    finally:store.db.close()


def test_recovery_isolates_ambiguous_stage_identity(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('item:theses:part')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','item:theses:part',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','item:theses:part','failed','failure','model-invalid') RETURNING id").fetchone()[0]
        store.problems.recover_processing_problems()
        row=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
        assert row and row[0].startswith('job:processing-isolated:')
    finally:store.db.close()


@pytest.mark.parametrize('kind',['filter','theses'])
def test_cleanup_closes_viewed_processing_error_without_get(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'article','failed','failure','model-invalid') RETURNING id",(kind,)).fetchone()[0]
            store.problems.sync_processing_problem(job,[],1)
            before=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
            store.db.execute("UPDATE publications SET viewed=1 WHERE id='article'")
            store.jobs.reconcile_cleanup()
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(before,)).fetchone()[0]=='irrelevant'
    finally:store.db.close()


def test_noop_stage_reconcile_does_not_reproject_error(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,input_snapshot,payload) VALUES ('filter','article','running','failure','model-invalid',%s::jsonb,%s)",(json.dumps({'signature':'sig'}),json.dumps({'input_revision':'rev'})))
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with store.db:store.jobs.reconcile('filter',[('article','sig',False,'rev')])
        finally:store.db.set_trace_callback(None)
        assert not any(query.lstrip().startswith('SELECT j.id,j.kind') for query in sql),sql
    finally:store.db.close()


def test_processing_wire_projection_sample(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id) VALUES ('article')")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','article','failed','failure','model-invalid')")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        matches=[p for p in response.json()['problems']['current'] if p.get('message')=='failure']
        assert matches,response.text
        print('processing wire',json.dumps({k:v for k,v in matches[0].items() if k not in ('id','created_at')},sort_keys=True))


def test_changed_processing_error_code_creates_distinct_occurrence(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','article','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job,'filter','article',1),1,'first',code='first-error')
        old=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'code'='first-error' AND finished IS NULL").fetchone()[0]
        with store.db:store.db.execute("UPDATE jobs SET status='running',generation=2 WHERE id=%s",(job,))
        store.jobs.retry(Job(job,'filter','article',2),2,'second',code='second-error')
        new=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'code'='second-error' AND finished IS NULL").fetchone()[0]
        assert new!=old
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_cleanup_many_viewed_processing_problems_is_batched(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,viewed) SELECT 'p-'||n,0 FROM generate_series(1,1000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'filter','p-'||n,'failed','failure','model-invalid' FROM generate_series(1,1000) n")
            ids=[row[0] for row in store.db.execute("SELECT id FROM jobs WHERE kind='filter' ORDER BY id")]
            for start in range(0,len(ids),100):store.problems.recover_processing_batch([dict(id=job_id) for job_id in ids[start:start+100]],1)
            store.db.execute("UPDATE publications SET viewed=1 WHERE id LIKE 'p-%%'")
        sql=[];store.db.set_trace_callback(sql.append)
        try:
            with store.db:store.jobs.reconcile_cleanup()
        finally:store.db.set_trace_callback(None)
        print('1000 viewed processing cleanup SQL:',len(sql))
        assert len(sql)<100,len(sql)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE body::jsonb->>'operation'='producer:filter' AND outcome='irrelevant'").fetchone()[0]==1000
    finally:store.db.close()


def test_recovery_closes_missing_filter_even_when_theses_active(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    body=dict(code='model-invalid',section='object',title='old',consequence='c',next_step='n',state='current',operation='producer:filter',processing_kind='filter',processing_job_id='123',objects=[dict(kind='material',id='article')])
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='processing-problems-recovered-v2'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('feed','article',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','article','failed','failure','model-invalid')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:model-invalid:feed:filter:article','background',%s,1)",(json.dumps(body),))
        store.problems.recover_processing_problems()
        assert store.db.execute("SELECT outcome FROM reader_problems WHERE id='old'").fetchone()[0]=='resolved'
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'operation'='producer:theses' AND finished IS NULL").fetchone()
    finally:store.db.close()
