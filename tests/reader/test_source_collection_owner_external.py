from tests.support.storage import Store
from oko.storage import source_groups
from oko.storage.job_types import Job
from oko.storage.transaction import transaction

def test_collection_start_updates_source_owner(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            jid=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code,generation) VALUES ('fetch','orphan','old-feed','running','failure','source-network',1) RETURNING id").fetchone()[0]
            source_groups.sync_jobs(store,[jid],1)
        store.collection_status.start(Job(jid,'fetch','orphan',1),'new-feed')
        rows=store.db.execute("SELECT body::jsonb->>'source_group_identity' AS identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'source_group_identity' IS NOT NULL").fetchall()
        assert [r['identity'] for r in rows]==['job:source-network:new-feed:fetch']
    finally:store.db.close()

def test_collection_owner_change_rolls_back_group_and_feed(tmp_path,postgres_url,monkeypatch):
    import pytest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            jid=store.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code,generation) VALUES ('fetch','orphan','old-feed','running','failure','source-network',1) RETURNING id").fetchone()[0]
            source_groups.sync_jobs(store,[jid],1)
        before=store.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:old-feed:fetch' AND finished IS NULL").fetchone()
        original=source_groups.sync_transition
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('abort')
        monkeypatch.setattr(source_groups,'sync_transition',fail)
        with pytest.raises(RuntimeError,match='abort'):
            with transaction(store.db):store.collection_status.start(Job(jid,'fetch','orphan',1),'new-feed')
        assert store.db.execute('SELECT feed_id FROM jobs WHERE id=%s',(jid,)).fetchone()[0]=='old-feed'
        assert tuple(store.db.execute('SELECT id,identity,body FROM reader_problems WHERE id=%s AND finished IS NULL',(before['id'],)).fetchone())==tuple(before)
    finally:store.db.close()
