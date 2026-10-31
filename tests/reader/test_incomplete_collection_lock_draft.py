import json,threading
import psycopg
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job

def test_collection_start_does_not_invert_graph_publication_job_order(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    graph=psycopg.connect(postgres_url,autocommit=False)
    marked=threading.Event();done=threading.Event();errors=[]
    try:
        with transaction(store.db):
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_ready) VALUES ('p','rss','p',0,1,%s,0,'incomplete:f:p',true)",(raw,))
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,generation,feed_id) VALUES ('fetch','p','running',1,'old') RETURNING id").fetchone()[0]
        original=store.db.incomplete_job_mark
        def marked_job(*args):original(*args);marked.set()
        store.db.incomplete_job_mark=marked_job
        def start():
            try:store.collection_status.start(Job(jid,'fetch','p',1),'new')
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            graph.execute("SELECT id FROM publications WHERE id='p' FOR UPDATE")
            t=threading.Thread(target=start);t.start()
            assert not marked.wait(.3)
            assert not done.is_set()
            graph.execute('UPDATE jobs SET generation=generation WHERE id=%s',(jid,))
        t.join(5)
        assert not t.is_alive() and not errors and marked.is_set()
    finally:graph.close();store.db.close()
