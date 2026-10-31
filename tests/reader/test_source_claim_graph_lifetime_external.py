import psycopg
from tests.support.storage import Store
from oko.storage.transaction import transaction
import oko.storage.job_repository as repo

def test_claim_without_outer_transaction_keeps_graph_until_update(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    other=psycopg.connect(postgres_url,autocommit=True)
    probes=[]
    try:
        with transaction(store.db):
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','pending',1) RETURNING id").fetchone()[0]
            row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(jid,)).fetchone()
        original=repo.job_write
        def traced(db,sql,*args,**kwargs):
            if sql.startswith("UPDATE jobs SET status='running'"):
                probes.append(other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))').fetchone()[0])
            return original(db,sql,*args,**kwargs)
        monkeypatch.setattr(repo,'job_write',traced)
        assert store.jobs._claim(row) is not None
        assert probes==[False]
        assert other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))').fetchone()[0]
    finally:other.close();store.db.close()
