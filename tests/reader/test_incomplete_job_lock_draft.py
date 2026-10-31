import json
import threading
import psycopg
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.collection.adapters import AdapterIssue,AdapterErrorKind
from oko.storage import collection_jobs


@pytest.mark.parametrize('operation',['finish','cancel','enqueue','retry','replace_media_input','defer_captions','collection_fail'])
def test_job_event_takes_graph_before_job_row(tmp_path,postgres_url,operation):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    graph=psycopg.connect(postgres_url,autocommit=False)
    started=threading.Event();done=threading.Event();errors=[]
    kind='download_media' if operation in ('replace_media_input','defer_captions') else 'fetch'
    try:
        with transaction(store.db):
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_ready) VALUES ('p','rss','p',0,1,%s,0,'incomplete:f:p',true)",(raw,))
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'p','running',1) RETURNING id",(kind,)).fetchone()[0]
        job=Job(jid,kind,'p',1)
        def worker():
            try:
                with transaction(store.db):
                    started.set()
                    if operation=='finish':store.jobs.finish(job)
                    elif operation=='cancel':store.jobs.cancel(job)
                    elif operation=='enqueue':store.jobs.enqueue_fetch('p')
                    elif operation=='retry':store.jobs.retry(job,0,'failure',code='ordinary')
                    elif operation=='replace_media_input':store.jobs.replace_media_input(job)
                    elif operation=='defer_captions':store.jobs.defer_captions(job,0,1,'wait')
                    else:collection_jobs.fail(store,job,AdapterIssue('ordinary','failure',AdapterErrorKind.temporary))
            except Exception as e:errors.append(e)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            graph.execute("SELECT id FROM publications WHERE id='p' FOR UPDATE")
            thread=threading.Thread(target=worker);thread.start()
            assert started.wait(3) and not done.wait(.2)
            graph.execute("SET LOCAL lock_timeout='500ms'")
            graph.execute('UPDATE jobs SET generation=generation WHERE id=%s',(jid,))
        thread.join(5)
        assert not thread.is_alive() and not errors
    finally:graph.close();store.db.close()
