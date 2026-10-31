import json
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage.transaction import transaction

def test_foreign_source_marker_wrong_code_must_be_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        foreign=dict(code='source-unrelated',operation='producer:fetch',source_group_identity='job:source-network:p:fetch',objects=[dict(kind='material',id='foreign')],section='object',title='foreign',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:foreign','background',%s,1)",(json.dumps(foreign),))
        store.jobs.retry(Job(jid,'fetch','p',1),1,'failure',code='source-network')
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==foreign
        assert any(problem['code']=='source-network' and problem['id']=='job:source-network:p:fetch' for problem in store.problems.candidates(2))
        store.problems.snapshot(2)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==foreign
    finally:store.db.close()

def test_spoofed_possible_but_inactive_code_is_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        identity='job:source-a:b:c:fetch'
        foreign=dict(code='source-a:b',operation='producer:fetch',source_group_identity=identity,objects=[dict(kind='material',id='p')],section='object',title='foreign',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('b:c','b:c','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b:c','p',true)")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:foreign','background',%s,1)",(json.dumps(foreign),))
        store.jobs.retry(Job(jid,'fetch','p',1),1,'failure',code='source-a')
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==foreign
        assert any(p['id']==identity and p['code']=='source-a' for p in store.problems.candidates(2))
        store.problems.snapshot(2)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==foreign
    finally:store.db.close()

def test_colliding_active_codes_stay_on_get_without_rewriting_marker(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        identity='job:source-a:b:c:fetch'
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('b:c','b:c','F','default','rss','rss','connected','[]'),('c','c','G','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p'),('q')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('b:c','p',true),('c','q',true)")
            first=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            second=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','q','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(first,'fetch','p',1),1,'failure',code='source-a')
        before=store.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'source_group_identity'=%s AND finished IS NULL",(identity,)).fetchone()
        store.jobs.retry(Job(second,'fetch','q',1),2,'failure',code='source-a:b')
        flags=store.db.execute('SELECT source_event_owned FROM jobs WHERE id=ANY(%s) ORDER BY id',([first,second],)).fetchall()
        assert [row[0] for row in flags]==[False,False]
        assert tuple(store.db.execute('SELECT id,body FROM reader_problems WHERE id=%s AND finished IS NULL',(before['id'],)).fetchone())==tuple(before)
        assert any(p['id']==identity for p in store.problems.candidates(3))
    finally:store.db.close()
