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
        store.problems.reconcile([],2,feeds={})
        row=store.db.execute('SELECT finished FROM reader_problems WHERE identity=%s',(identity,)).fetchone()
        assert (row['finished'] is not None) == expected
    finally:store.db.close()

def test_active_foreign_candidate_does_not_replace_possible_delete_occurrence(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        identity='job:delete:feed:fetch:item:delete:part'
        old=dict(id=identity,code='delete',section='object',title='old deletion',consequence='old',next_step='x',objects=[],action=None,operation='producer:status',state='current',next_at=None)
        foreign=dict(old,title='foreign fetch',objects=[dict(kind='material',id='item:delete:part')],operation='producer:fetch')
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','item:delete:part','feed','failed','x','delete'),('delete','part','feed:fetch:item','failed','x','delete')")
            store.problems.sync_candidate(identity,old,1)
        store.problems.reconcile([foreign],2,feeds={})
        body=json.loads(store.db.execute('SELECT body FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()[0])
        assert body['title']=='old deletion'
        found=store.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(identity,)).fetchone()
        assert found is not None
        store.problems.reconcile([foreign],3,feeds={})
        assert store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(identity,)).fetchone()['id']==found['id']
        with store.db:store.db.execute("UPDATE reader_problems SET finished=4,outcome='resolved' WHERE identity=%s",(identity,))
        store.problems.reconcile([foreign],5,feeds={})
        assert store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'status_job_identity'=%s AND finished IS NULL",(identity,)).fetchone()['id']==found['id']
        store.problems.reconcile([],6,feeds={})
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(found['id'],)).fetchone()['finished'] is not None
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
