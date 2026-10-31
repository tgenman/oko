import json
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage.transaction import transaction

def test_disabled_stage_failure_persists_blocked_state(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','p','running',1) RETURNING id").fetchone()[0]
        with transaction(store.db):store.jobs.fail(Job(jid,'filter','p',1),'failure',code='source-network')
        row=store.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'source_group_identity' IS NOT NULL").fetchone()
        assert row is not None
        body=json.loads(row['body'])
        assert body['next_step']=='Включите обработку, чтобы повторить попытку.',body
    finally:store.db.close()

def test_standalone_disabled_stage_failure_rolls_back_as_one_unit(tmp_path,postgres_url,monkeypatch):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('filter','p','running',1) RETURNING id").fetchone()[0]
        from oko.storage import source_groups
        original=source_groups.sync_changed_rows
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('rollback')
        monkeypatch.setattr(source_groups,'sync_changed_rows',fail)
        with pytest.raises(RuntimeError,match='rollback'):store.jobs.fail(Job(jid,'filter','p',1),'failure',code='source-network')
        assert tuple(store.db.execute('SELECT status,error,problem_code FROM jobs WHERE id=%s',(jid,)).fetchone())==('running',None,None)
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'source_group_identity' IS NOT NULL AND finished IS NULL").fetchone()
    finally:store.db.close()
