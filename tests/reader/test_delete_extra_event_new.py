from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job


def test_real_collection_outer_catch_persists_extra_delete_error(tmp_path,postgres_url,monkeypatch):
    import asyncio
    from tests.support.worker_library import collection_worker
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            job_id=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','p','running',1) RETURNING id").fetchone()[0]
        worker=collection_worker(tmp_path,s,object())
        def fail_delete(_):raise OSError('synthetic delete failure')
        monkeypatch.setattr(s.jobs,'delete',fail_delete)
        retry=s.jobs.retry;calls=[]
        def fail_first(*args,**kwargs):
            calls.append(kwargs.get('code'))
            if len(calls)==1:raise RuntimeError('synthetic retry failure')
            return retry(*args,**kwargs)
        monkeypatch.setattr(s.jobs,'retry',fail_first)
        asyncio.run(worker._execute(Job(job_id,'delete','p',1)))
        assert calls==['delete','delete:RuntimeError']
        row=s.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'deletion_job_id'=%s",(str(job_id),)).fetchone()
        assert row and 'delete:RuntimeError' in row['body']
    finally:s.db.close()
