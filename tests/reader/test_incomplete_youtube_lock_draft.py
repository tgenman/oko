import json,threading
import psycopg
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_youtube_start_does_not_invert_graph_publication_row_order(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    graph=psycopg.connect(postgres_url,autocommit=False)
    marked=threading.Event(); done=threading.Event(); errors=[]
    try:
        with transaction(store.db):
            raw=json.dumps({'extra':{'_reader':{'full':False}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class,incomplete_group_key,incomplete_group_ready) VALUES ('p','rss','p',0,1,%s,0,'incomplete:f:p',true)",(raw,))
            store.db.execute("INSERT INTO jobs(kind,item,status,error,incomplete_youtube_sensitive) VALUES ('fetch','p','pending','failure',true)")
        original=store.incomplete_draft.mark_youtube_groups
        def marked_groups(*args,**kwargs):
            original(*args,**kwargs);marked.set()
        monkeypatch.setattr(store.incomplete_draft,'mark_youtube_groups',marked_groups)
        def start():
            try:store.youtube.start(100)
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            graph.execute("SELECT id FROM publications WHERE id='p' FOR UPDATE")
            t=threading.Thread(target=start);t.start()
            assert not marked.wait(.3)
            assert not done.is_set()
            graph.execute('UPDATE youtube_access SET blocked_until=blocked_until WHERE id=1')
        t.join(5)
        assert not t.is_alive() and not errors and marked.is_set()
    finally:graph.close();store.db.close()
