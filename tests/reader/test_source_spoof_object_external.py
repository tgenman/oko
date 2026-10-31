import json
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage.transaction import transaction

def test_foreign_source_marker_wrong_code_must_be_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        foreign=dict(code='source-network',operation='producer:fetch',source_group_identity='job:source-network:p:fetch',objects=[dict(kind='material',id='foreign')],section='object',title='foreign',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
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

def test_spoof_on_canonical_identity_survives_snapshot(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    identity='job:source-network:p:fetch'
    try:
        foreign=dict(code='source-network',operation='producer:fetch',source_group_identity=identity,objects=[dict(kind='material',id='foreign')],section='object',title='foreign',consequence='saved',next_step='retry',state='current',next_at=None,action=None)
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign',%s,'background',%s,1)",(identity,json.dumps(foreign)))
        store.jobs.retry(Job(jid,'fetch','p',1),1,'failure',code='source-network')
        store.problems.snapshot(2)
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==foreign
    finally:store.db.close()
