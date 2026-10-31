import pytest
from tests.support.storage import Store

@pytest.mark.parametrize('other,status,expected',[(False,'done',True),(True,'done',False),(False,'failed',False),(None,'done',False)])
def test_shared_legacy_identity_only_finishes_with_proven_foreign_owner(tmp_path,postgres_url,other,status,expected):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        identity='job:delete:feed:fetch:item:delete:part'
        body=dict(id=identity,code='delete',section='object',title='x',consequence='x',next_step='x',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
        with store.db:
            if other is not None:
                store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed',%s)",(status,))
            if other:
                store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('delete','part','feed:fetch:item','failed')")
            store.problems.sync_candidate(identity,body,1)
            store.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
        store.problems.recover_finished_foreign_fetch_deletions()
        store.problems.reconcile([],2,feeds={})
        row=store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()
        assert (row['finished'] is not None) == expected
    finally:store.db.close()

def test_active_foreign_candidate_does_not_replace_possible_delete_occurrence(tmp_path,postgres_url):
    import json
    from oko.storage.fetch_job_problems import managed_candidates
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        identity='job:delete:feed:fetch:item:delete:part'
        old=dict(id=identity,code='delete',section='object',title='old deletion',consequence='old',next_step='x',objects=[],action=None,operation='producer:status',state='current',next_at=None)
        with store.db:
            fetch=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','item:delete:part','feed','failed','x','delete') RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('delete','part','feed:fetch:item','failed','x','delete')")
            store.problems.sync_candidate(identity,old,1)
            store.problems.sync_fetch(fetch,[],2)
        body=json.loads(store.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0])
        assert body['title']=='old deletion'
        found=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'fetch_job_identity'=%s AND finished IS NULL",(identity,)).fetchone()
        assert found is not None
        with store.db:
            previous=managed_candidates(store,fetch,3)
            store.problems.sync_fetch(fetch,previous,3)
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(found['identity'],)).fetchone()['id']==found['id']
        with store.db:
            store.db.execute("UPDATE reader_problems SET finished=4,outcome='resolved' WHERE identity=%s",(identity,))
            previous=managed_candidates(store,fetch,4)
            store.problems.sync_fetch(fetch,previous,4)
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',(found['identity'],)).fetchone()['id']==found['id']
        with store.db:
            previous=managed_candidates(store,fetch,5)
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE id=%s",(fetch,))
            store.problems.sync_fetch(fetch,previous,5)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(found['id'],)).fetchone()['finished'] is not None
        assert store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()['finished']==4
    finally:store.db.close()

def test_regrouping_does_not_move_ambiguous_deletion_record(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        oldid='job:delete:feed:fetch:item:delete:part'
        base=dict(code='delete',section='object',title='old deletion',consequence='old',next_step='x',objects=[],action=None,operation='producer:status',state='current',next_at=None)
        with store.db:store.problems.sync_candidate(oldid,dict(base,id=oldid),1)
        foreign=dict(base,id='job:delete:newfeed:fetch:item:delete:part',title='foreign',operation='producer:fetch',objects=[dict(kind='material',id='item:delete:part')],_legacy_ids={oldid})
        store.problems.reconcile([foreign],2,feeds={})
        assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(oldid,)).fetchone()
    finally:store.db.close()

def test_legacy_job_lookup_is_indexed_and_bounded(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'fetch','foreign-'||n,'done' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
            store.db._db.execute('ANALYZE jobs')
        query='''SELECT j.kind,j.item,j.feed_id,j.status,j.error,j.problem_code,p.feed
            FROM unnest(%s::text[],%s::text[],%s::text[]) AS p(kind,item,feed)
            JOIN jobs j ON j.kind=p.kind AND j.item=p.item'''
        params=(['fetch','delete'],['item:delete:part','part'],['feed','feed:fetch:item'])
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,params).fetchone()[0][0]['Plan']
        assert plan['Shared Hit Blocks'] < 30
        assert plan['Actual Rows']==1
    finally:store.db.close()

def test_collision_attempts_are_bounded_and_outer_transaction_rolls_back(tmp_path,postgres_url,monkeypatch):
    import hashlib
    import oko.storage.problems as module
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        identity='job:delete:feed:fetch:item:delete:part'
        base=dict(code='delete',section='object',title='old deletion',consequence='old',next_step='x',objects=[],action=None,operation='producer:status',state='current',next_at=None)
        foreign=dict(base,id=identity,title='foreign',operation='producer:fetch',objects=[dict(kind='material',id='item:delete:part')])
        digest=hashlib.sha256((identity+'\0producer:fetch').encode()).hexdigest()
        with store.db:
            store.problems.sync_candidate(identity,dict(base,id=identity),1)
            for token in ('a','b','c'):
                key='job:status-isolated:'+digest+':'+token
                store.problems.sync_candidate(key,dict(base,id=key,code='other'),1)
        before=store.db.execute('SELECT id,identity,body,finished FROM reader_problems ORDER BY id').fetchall()
        attempts=iter(['a','b','c']); seen=[]
        def token():
            value=next(attempts);seen.append(value);return value
        monkeypatch.setattr(module,'new_read_problem_token',token)
        with pytest.raises(RuntimeError,match='Unable to allocate status problem identity'):
            with store.db:
                store.db.execute("INSERT INTO state(key,value) VALUES ('collision-rollback-test','marker')")
                store.problems.reconcile([foreign],2,feeds={})
        assert seen==['a','b','c']
        assert store.db.execute('SELECT id,identity,body,finished FROM reader_problems ORDER BY id').fetchall()==before
        assert not store.db.execute("SELECT 1 FROM state WHERE key='collision-rollback-test'").fetchone()
    finally:store.db.close()


def test_isolated_identity_survives_restart(tmp_path,postgres_url):
    identity='job:delete:feed:fetch:item:delete:part'
    base=dict(code='delete',section='object',title='old deletion',consequence='old',next_step='x',objects=[],action=None,operation='producer:status',state='current',next_at=None)
    foreign=dict(base,id=identity,title='foreign',operation='producer:fetch',objects=[dict(kind='material',id='item:delete:part')])
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:store.problems.sync_candidate(identity,dict(base,id=identity),1)
        store.problems.reconcile([foreign],2,feeds={})
        saved=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(identity,)).fetchone()
        assert saved
    finally:store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.problems.reconcile([foreign],3,feeds={})
        again=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(identity,)).fetchone()
        assert again==saved
    finally:store.db.close()

def test_third_allocation_attempt_succeeds(tmp_path,postgres_url,monkeypatch):
    import hashlib
    import oko.storage.problems as module
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        identity='job:delete:feed:fetch:item:delete:part'
        base=dict(code='delete',section='object',title='old deletion',consequence='old',next_step='x',objects=[],action=None,operation='producer:status',state='current',next_at=None)
        foreign=dict(base,id=identity,title='foreign',operation='producer:fetch',objects=[dict(kind='material',id='item:delete:part')])
        digest=hashlib.sha256((identity+'\0producer:fetch').encode()).hexdigest()
        with store.db:
            store.problems.sync_candidate(identity,dict(base,id=identity),1)
            for token in ('a','b'):
                key='job:status-isolated:'+digest+':'+token
                store.problems.sync_candidate(key,dict(base,id=key,code='other'),1)
        attempts=iter(['a','b','c'])
        monkeypatch.setattr(module,'new_read_problem_token',lambda:next(attempts))
        store.problems.reconcile([foreign],2,feeds={})
        row=store.db.execute("SELECT identity FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(identity,)).fetchone()
        assert row['identity']=='job:status-isolated:'+digest+':c'
        assert store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()['finished'] is None
    finally:store.db.close()

def test_finished_foreign_ambiguous_legacy_is_recovered_at_startup(tmp_path, postgres_url):
    identity='job:delete:feed:fetch:item:delete:part'
    body=dict(id=identity,code='delete',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
            store.problems.sync_candidate(identity,body,1)
            store.db.execute("DELETE FROM state WHERE key IN ('fetch-problems-recovered-v2','fetch-delete-legacy-finished-v1')")
    finally:store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.problems.snapshot(2)
        row=store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()
        assert row and row['finished'] is not None
    finally:store.db.close()

def test_finished_foreign_recovery_waits_for_concurrent_delete(tmp_path, postgres_url):
    import threading
    import psycopg
    from oko.storage.memberships import _GRAPH_LOCK
    identity='job:delete:feed:fetch:item:delete:part'
    body=dict(id=identity,code='delete',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    other=psycopg.connect(postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
            store.problems.sync_candidate(identity,body,1)
            store.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
        other.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))',(_GRAPH_LOCK,))
        other.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('delete','part','feed:fetch:item','failed')")
        entered=threading.Event();done=threading.Event();errors=[]
        def recover():
            entered.set()
            try:store.problems.recover_finished_foreign_fetch_deletions()
            except BaseException as exc:errors.append(exc)
            finally:done.set()
        thread=threading.Thread(target=recover);thread.start()
        assert entered.wait(2) and not done.wait(.2)
        other.commit();thread.join(10)
        assert done.is_set() and not errors
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()
        assert row and row['finished'] is None and __import__('json').loads(row['body'])['title']=='legacy'
    finally:other.close();store.db.close()

def test_finished_foreign_recovery_rolls_back_update(tmp_path,postgres_url,monkeypatch):
    import pytest
    identity='job:delete:feed:fetch:item:delete:part'
    body=dict(id=identity,code='delete',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
            store.problems.sync_candidate(identity,body,1)
            store.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
        def fail(_):raise RuntimeError('rollback')
        with monkeypatch.context() as patch:
            patch.setattr('oko.storage.problems.status_changed',fail)
            with pytest.raises(RuntimeError,match='rollback'):store.problems.recover_finished_foreign_fetch_deletions()
        assert store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()['finished'] is None
        store.problems.recover_finished_foreign_fetch_deletions()
        assert store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()['finished'] is not None
    finally:store.db.close()

def test_finished_foreign_recovery_uses_bounded_index(tmp_path,postgres_url):
    import json
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            body=json.dumps(dict(code='other',operation='producer:status',objects=[],section='object',title='x',consequence='x',next_step='x',action=None,state='current',next_at=None))
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'u-'||n,'job:other:f:fetch:'||n,'background',%s,n FROM generate_series(1,10000)n",(body,))
            store.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
            store.db._db.execute('ANALYZE reader_problems')
        statements=[];store.db.set_trace_callback(statements.append)
        try:store.problems.recover_finished_foreign_fetch_deletions()
        finally:store.db.set_trace_callback(None)
        query=next(q for q in statements if q.startswith('SELECT id,identity,body,created FROM reader_problems') and "identity LIKE '%%:delete:%%'" in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(1,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,plan
    finally:store.db.close()

def test_finished_foreign_recovery_resumes_after_committed_page(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status) SELECT 'fetch',n||':delete:part','feed-'||n,'done' FROM generate_series(1,101)n")
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'legacy-'||n,'job:delete:feed-'||n||':fetch:'||n||':delete:part','background',
                jsonb_build_object('code','delete','section','object','title','legacy','consequence','saved','next_step','retry',
                'objects',jsonb_build_array(jsonb_build_object('kind','material','id',n||':delete:part')),
                'action',NULL,'operation','producer:status','state','current','next_at',NULL)::text,n FROM generate_series(1,101)n""")
            store.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
        def fail(point):
            if point=='fetch_delete_finished_legacy_recovery_batch':raise RuntimeError('interrupted')
        store.fault=fail
        with pytest.raises(RuntimeError,match='interrupted'):store.problems.recover_finished_foreign_fetch_deletions()
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'legacy-%%' AND finished IS NOT NULL").fetchone()[0]==100
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-delete-legacy-finished-v1'").fetchone() is None
        store.fault=lambda _:None
        store.problems.recover_finished_foreign_fetch_deletions()
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE id LIKE 'legacy-%%' AND finished IS NOT NULL").fetchone()[0]==101
        assert store.db.execute("SELECT 1 FROM state WHERE key='fetch-delete-legacy-finished-v1'").fetchone()
    finally:store.db.close()
