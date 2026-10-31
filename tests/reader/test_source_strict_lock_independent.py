import threading
import psycopg
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage.transaction import transaction

def test_finish_waits_graph_before_job_or_reservation(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    graph=psycopg.connect(postgres_url,autocommit=False)
    done=threading.Event();errors=[]
    try:
        with transaction(store.db):
            store.db.execute("UPDATE youtube_access SET blocked_until=1000000000000,processor_reservation_id=NULL WHERE id=1")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,problem_code,error,processor_visible) VALUES ('fetch','p','running',1,'processor-config','problem',true) RETURNING id").fetchone()[0]
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,processor_visible,processor_caption) VALUES ('download_media','caption','pending','processor-config',true,true)")
        def finish():
            try:
                with transaction(store.db):store.jobs.finish(Job(job,'fetch','p',1))
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            t=threading.Thread(target=finish);t.start()
            assert not done.wait(.3)
            graph.execute("SET LOCAL lock_timeout='500ms'")
            graph.execute("UPDATE youtube_access SET processor_reservation_id='external' WHERE id=1")
            graph.execute("UPDATE jobs SET problem_code='source-network' WHERE id=%s AND status='running'",(job,))
        t.join(5)
        assert done.is_set() and not errors
    finally: graph.close();store.db.close()

def test_youtube_completed_waits_graph_before_reservation_row(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    graph=psycopg.connect(postgres_url,autocommit=False)
    done=threading.Event();errors=[]
    try:
        def completed():
            try:store.youtube.completed()
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            t=threading.Thread(target=completed);t.start()
            assert not done.wait(.3)
            graph.execute("SET LOCAL lock_timeout='500ms'")
            graph.execute('UPDATE youtube_access SET blocked_until=blocked_until WHERE id=1')
        t.join(5)
        assert done.is_set() and not errors
    finally:graph.close();store.db.close()

def test_claim_reloads_job_after_graph_lock_before_source_projection(tmp_path,postgres_url):
    import json
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    graph=psycopg.connect(postgres_url,autocommit=False)
    done=threading.Event();errors=[];claimed=[]
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','pending',1) RETURNING id").fetchone()[0]
            stale=store.db.execute('SELECT * FROM jobs WHERE id=%s',(jid,)).fetchone()
        def claim():
            try:claimed.append(store.jobs._claim(stale))
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            t=threading.Thread(target=claim);t.start()
            assert not done.wait(.3)
            graph.execute("UPDATE jobs SET problem_code='source-network',error='failure' WHERE id=%s",(jid,))
        t.join(5)
        assert done.is_set() and not errors and claimed[0] is not None
        row=store.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'source_group_identity'='job:source-network:p:fetch'").fetchone()
        assert row is not None and json.loads(row['body'])['state']=='retrying'
    finally:graph.close();store.db.close()

def test_preflight_refreshes_reservation_after_waiting_for_graph(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    graph=psycopg.connect(postgres_url,autocommit=False)
    seen=[];done=threading.Event();errors=[]
    try:
        with transaction(store.db):
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','running',1) RETURNING id").fetchone()[0]
            store.db.execute("UPDATE youtube_access SET processor_reservation_id=NULL WHERE id=1")
        original=store.problems.processor_resume_candidate
        def inspect(now,*,pointer=None):
            seen.append(pointer['processor_reservation_id'] if pointer else None)
            return original(now,pointer=pointer)
        monkeypatch.setattr(store.problems,'processor_resume_candidate',inspect)
        def prepare():
            try:
                with transaction(store.db):store.problems.prepare_processor_change([jid],0)
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            t=threading.Thread(target=prepare);t.start()
            assert not done.wait(.3)
            graph.execute("UPDATE youtube_access SET processor_reservation_id='concurrent' WHERE id=1")
        t.join(5)
        assert done.is_set() and not errors
        assert seen==['concurrent'],seen
    finally:graph.close();store.db.close()

def test_preflight_graph_lock_is_transaction_owned_without_outer_transaction(tmp_path,postgres_url):
    import psycopg
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    other=psycopg.connect(postgres_url,autocommit=True)
    try:
        with store.db:
            row=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','autocommit','running',1) RETURNING id").fetchone()
        with store.db:
            store.problems.prepare_processor_change([row['id']],store.wall_clock())
            locked=other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))').fetchone()[0]
            assert locked is False
        assert other.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))').fetchone()[0] is True
    finally:
        other.close();store.db.close()
