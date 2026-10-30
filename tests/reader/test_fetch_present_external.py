import json
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_ambiguous_legacy_objects_are_preserved(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article'),('other')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','fail','')")
            body=dict(code='work-fetch',operation='producer:status',objects=[dict(kind='material',id='article'),dict(kind='material',id='other')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:work-fetch:article:fetch:article','background',%s,1)",(json.dumps(body),))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='legacy'").fetchone()
        assert row['finished'] is None and json.loads(row['body'])==body
    finally:store.db.close()

def test_recovery_preserves_old_id_when_new_identity_is_foreign(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
            store.db.execute("INSERT INTO publications(id) VALUES ('article')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','article','failed','fail','other') RETURNING id").fetchone()[0]
            old=dict(code='work-fetch',operation='producer:fetch',objects=[dict(kind='material',id='article')],fetch_job_id=str(job),fetch_implicit_code=True)
            foreign=dict(code='other',operation='producer:filter',objects=[dict(kind='material',id='article')])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','job:work-fetch:article:fetch:article','background',%s,1),('foreign','job:other:article:fetch:article','background',%s,1)",(json.dumps(old),json.dumps(foreign)))
        store.problems.recover_fetch_problems()
        row=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'fetch_job_id' IS NOT NULL AND finished IS NULL").fetchone()
        assert row and row['id']=='legacy'
        assert json.loads(store.db.execute("SELECT body FROM reader_problems WHERE id='foreign'").fetchone()[0])==foreign
    finally:store.db.close()
