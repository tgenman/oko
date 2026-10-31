import json,threading
import psycopg
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.collection.contracts import Material,SourceManifest

def test_missing_file_recovery_preserves_graph_lock_order(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    graph=psycopg.connect(postgres_url,autocommit=False)
    updated=threading.Event();done=threading.Event();errors=[]
    try:
        content={'blocks':[],'extra':{'_reader':{'full':False,'source_manifest':{'post_web_unavailable':False,'post':{},'resources':[]}}}}
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('m','p','image','x','file','unavailable','Локальный файл вложения недоступен')")
        monkeypatch.setattr(store,'_has_unviewed',lambda _:True)
        monkeypatch.setattr(store,'publication',lambda _:None)
        monkeypatch.setattr(store,'collection_history',lambda *a:None)
        original=store.db.execute
        def traced(sql,*args,**kwargs):
            result=original(sql,*args,**kwargs)
            if sql.startswith("UPDATE media_files SET status = 'unavailable'"):updated.set()
            return result
        monkeypatch.setattr(store.db,'execute',traced)
        material=Material('t','',source_manifest=SourceManifest(post={},post_web_unavailable=True))
        def worker():
            try:store.collect_material('p',material)
            except Exception as exc:errors.append(exc)
            finally:done.set()
        with graph.transaction():
            graph.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))')
            graph.execute("SELECT id FROM publications WHERE id='p' FOR UPDATE")
            t=threading.Thread(target=worker);t.start()
            assert not updated.wait(.3)
            assert not done.is_set()
            graph.execute("SET LOCAL lock_timeout='300ms'")
            graph.execute("UPDATE media_files SET name=name WHERE id='m'")
        t.join(5)
        assert not t.is_alive() and not errors and updated.is_set()
    finally:graph.close();store.db.close()
