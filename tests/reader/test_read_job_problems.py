"""Focused read-problem projection and bounded-work checks."""
import json
import pytest
from oko.storage.read_job_problems import candidates
from tests.support.read_problem import setup
from tests.support.storage import Store


@pytest.mark.parametrize('status',['pending','running','failed','unavailable','blocked'])
@pytest.mark.parametrize('code',['source-connection','source-reconnect','identity-unconfirmed','work-read',None])
def test_projection_for_read_job_states(tmp_path,postgres_url,status,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed',%s,'failure',%s) RETURNING id",(status,code)).fetchone()[0]
        projected=candidates(store,job_id,10)
        assert len(projected)==1
        body=projected[0].body
        effective_code=code or 'work-read'
        assert body['code']==effective_code
        assert body['objects']==[dict(kind='feed',id='feed')]
        assert body['operation']=='producer:read'
        assert body['state']==('retrying' if status=='running' else 'current')
        assert body['section']==('action' if code=='source-reconnect' else 'object')
        assert projected[0].legacy_identities==frozenset()
    finally:store.db.close()


def test_normal_wait_is_not_a_problem(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','waiting') RETURNING id").fetchone()[0]
        assert candidates(store,job_id,10)==[]
    finally:store.db.close()


def test_read_problem_uses_feed_even_if_publication_has_same_id(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('owner','owner','Owner','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('feed','rss','item',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('owner','feed',true,true)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','source-connection') RETURNING id").fetchone()[0]
        actual=candidates(store,job_id,10)
        assert len(actual)==1 and actual[0].body['objects']==[dict(kind='feed',id='feed')]
    finally:store.db.close()


@pytest.mark.parametrize('error,code',[('',None),(None,''),('', ''),(None,'source-connection')])
def test_empty_and_null_error_fields(tmp_path,postgres_url,error,code):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','pending',%s,%s) RETURNING id",(error,code)).fetchone()[0]
        actual=candidates(store,job_id,10)
        assert len(actual)==1 and actual[0].body['code']==(code or 'work-read')
        assert actual[0].body['state']=='retrying'
    finally:store.db.close()


@pytest.mark.parametrize('mode,frozen,complete',[('count',0,0),('count',1,0),('count',1,1),('date',0,0)])
def test_history_admission(tmp_path,postgres_url,mode,frozen,complete):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            selection=json.dumps(dict(mode=mode,count=10,since=None))
            store.db.execute('INSERT INTO initial_history(feed,selection,frozen,scan_complete) VALUES (%s,%s,%s,%s)',('feed',selection,frozen,complete))
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','failure') RETURNING id").fetchone()[0]
        expected = mode!='count' or bool(frozen and complete)
        assert bool(candidates(store,job_id,10)) == expected
    finally:store.db.close()


def test_point_projection_ignores_foreign_jobs(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'fetch','foreign-'||n,'failed','failure','source-connection' FROM generate_series(1,10000) n")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','source-connection') RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE jobs')
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:assert candidates(store,job_id,10)
        finally:store.db.set_trace_callback(None)
        sql=next(q for q in statements if 'FROM jobs j' in q and 'j.id=%s' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(job_id,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,plan
    finally:store.db.close()



def test_profile_suppression_ignores_foreign_problem_groups(tmp_path,postgres_url):
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created)
                SELECT 'foreign-'||n,'profile:foreign-'||n,'background',jsonb_build_object('objects',jsonb_build_array(jsonb_build_object('kind','feed','id','foreign-'||n)))::text,1
                FROM generate_series(1,10000) n""")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','identity-unconfirmed') RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE reader_problems')
        statements=[]
        store.db.set_trace_callback(statements.append)
        try: assert candidates(store,job_id,10)
        finally:store.db.set_trace_callback(None)
        sql=next(q for q in statements if 'body::jsonb' in q and '@>' in q)
        needle=json.dumps([dict(kind='feed',id='feed')])
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,(needle,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,plan
    finally:store.db.close()



def test_unknown_code_does_not_scan_channel_member_history(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            store.db.execute("INSERT INTO telegram_read_members(feed,member,viewed,saved) SELECT 'feed',n,true,false FROM generate_series(1,10000) n")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure',NULL) RETURNING id").fetchone()[0]
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:assert candidates(store,job_id,10)
        finally:store.db.set_trace_callback(None)
        assert not any('telegram_read_members' in sql for sql in statements),statements
    finally:store.db.close()



def test_admission_does_not_scan_initial_history_members(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute('INSERT INTO initial_history(feed,selection,frozen,scan_complete) VALUES (%s,%s,1,1)',('feed',json.dumps(dict(mode='count',count=10,since=None))))
            store.db.execute("INSERT INTO history_members(feed,item) SELECT 'feed','foreign-'||n FROM generate_series(1,10000) n")
            store.db.execute("UPDATE telegram_read_state SET error='failure' WHERE feed='feed'")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('read','feed','pending','failure') RETURNING id").fetchone()[0]
        statements=[]
        store.db.set_trace_callback(statements.append)
        try: assert candidates(store,job_id,10)
        finally:store.db.set_trace_callback(None)
        assert not any('history_members' in sql or 'telegram_read_members' in sql for sql in statements),statements
    finally:store.db.close()
