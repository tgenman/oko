import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction


def test_startup_recovery_resume_retains_nul_and_marker(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    content=json.dumps({'extra':{'_reader':{'full':False}},'payload':'\x00'})
    try:
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='incomplete-class-recovered-v1'")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) SELECT 'p-'||n,'rss','p-'||n,0,1,false,%s FROM generate_series(1,201)n",(content,))
        store.db.close()
        def fault(point):
            if point=='incomplete_class_recovery_batch':raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError,match='interrupted'):Store(tmp_path,fault,database_url=postgres_url)
        store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute('SELECT count(*) FROM publications WHERE incomplete_class=0 AND content=%s',(content,)).fetchone()[0]==201
        assert store.db.execute("SELECT 1 FROM state WHERE key='incomplete-class-recovered-v1'").fetchone()
    finally:store.db.close()


def test_real_collection_updates_derived_class_with_content(tmp_path,postgres_url):
    from oko.collection.contracts import Material,Publication
    from tests.support.source import StructuredSource,publication_packet
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo(); publication=Publication('p','https://example.test/p',1)
        with transaction(store.db):
            effects=store.collect_discovery([publication_packet(publication)],'{}');store.jobs.apply_collection(effects)
        item=store.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
        full=StructuredSource(None).describe(publication,Material('Title','Text',full=True))
        with transaction(store.db):
            _,effects=store.collect_material(item,full);store.jobs.apply_collection(effects)
        assert store.db.execute('SELECT incomplete_class FROM publications WHERE id=%s',(item,)).fetchone()[0]==1
        partial=StructuredSource(None).describe(publication,Material('Title','Partial',full=False,reason='partial'))
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.collect_material(item,partial)
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT incomplete_class FROM publications WHERE id=%s',(item,)).fetchone()[0]==1
        with transaction(store.db):
            _,effects=store.collect_material(item,partial);store.jobs.apply_collection(effects)
        row=store.db.execute('SELECT incomplete_class,content FROM publications WHERE id=%s',(item,)).fetchone()
        assert row['incomplete_class']==0
        assert json.loads(row['content'])['extra']['_reader']['full'] is False
        with store.db:
            store.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL WHERE kind='fetch' AND item=%s",(item,))
            store.incomplete_draft.mark_materials([item])
        assert any(p['code']=='material-incomplete' for p in store.problems.snapshot(10)['current'])
    finally:store.db.close()


def test_startup_recovery_excludes_second_store_until_completed(tmp_path,postgres_url):
    import threading
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db:
        store.db.execute("DELETE FROM state WHERE key='incomplete-class-recovered-v1'")
        store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) SELECT 'p-'||n,'rss','p-'||n,0,1,false,%s FROM generate_series(1,120)n",(json.dumps({'extra':{'_reader':{'full':False}}}),))
    store.db.close()
    entered=threading.Event();release=threading.Event();errors=[]
    def fault(point):
        if point=='incomplete_class_recovery_batch' and not entered.is_set():
            entered.set()
            if not release.wait(10):raise RuntimeError('timed out')
    def startup():
        try:
            other=Store(tmp_path,fault,database_url=postgres_url)
            other.db.close()
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=startup);thread.start()
    try:
        assert entered.wait(10)
        with pytest.raises(ValueError,match='Another Oko reader'):Store(tmp_path,lambda _:None,database_url=postgres_url)
    finally:
        release.set();thread.join(15)
    assert not thread.is_alive() and not errors
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        assert store.db.execute('SELECT count(*) FROM publications WHERE incomplete_class=0').fetchone()[0]==120
        assert store.db.execute("SELECT 1 FROM state WHERE key='incomplete-class-recovered-v1'").fetchone()
    finally:store.db.close()


@pytest.mark.parametrize('payload',[{},[],None,{'extra':None},{'extra':{'_reader':[]}},{'extra':{'_reader':{'full':None}}},{'extra':{'_reader':{'full':False}}},{'extra':{'_reader':{'full':True}}},{'extra':{'_reader':{'full':0}}},{'extra':{'_reader':{'full':2}}},{'extra':{'_reader':{'full':' 0 '}}},{'extra':{'_reader':{'full':'-1'}}},{'extra':{'_reader':{'full':'0'*5000}}}])
def test_classifier_matches_postgres_predicate(tmp_path,postgres_url,payload):
    from oko.storage.incomplete_index import content_class
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        raw=json.dumps(payload)
        row=store.db.execute("SELECT CASE WHEN jsonb_typeof(%s::jsonb#>'{extra,_reader,full}')='boolean' THEN NOT (%s::jsonb#>>'{extra,_reader,full}')::boolean ELSE (%s::jsonb#>>'{extra,_reader,full}')::integer=0 END AS eligible",(raw,raw,raw)).fetchone()
        assert (content_class(raw)==0)==(row['eligible'] is True)
    finally:store.db.close()


def test_actual_get_uses_index_and_python_valid_non_jsonb_is_not_lost(tmp_path,postgres_url):
    from oko.storage.incomplete_index import recover
    from tests.support.postgres_work import scan_rows
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        full=json.dumps({'extra':{'_reader':{'full':True}},'payload':'\x00'})
        partial=json.dumps({'extra':{'_reader':{'full':False,'reason':'truncated'}},'payload':'\x00'})
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='incomplete-class-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) SELECT 'p-'||n,'rss','p-'||n,0,1,false,%s FROM generate_series(1,10000)n",(full,))
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) VALUES ('partial','rss','partial',0,1,false,%s)",(partial,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','partial',true)")
        recover(store)
        from oko.storage.incomplete_group_draft import recover_group_keys,recover_group_problems
        with store.db:
            store.db.execute("DELETE FROM state WHERE key IN ('experimental-incomplete-groups-v1','experimental-incomplete-problems-v1')")
        recover_group_keys(store);recover_group_problems(store)
        with store.db:store.db._db.execute('ANALYZE publications')
        sql=[];store.db.set_trace_callback(sql.append)
        try: snapshot=store.problems.snapshot(100)
        finally:store.db.set_trace_callback(None)
        assert any(p['code']=='material-incomplete' and any(o['id']=='partial' for o in p['objects']) for p in snapshot['current'])
        assert store.db.execute("SELECT content FROM publications WHERE id='partial'").fetchone()[0]==partial
        assert not any('AS media_waiting' in q for q in sql)
        assert not [q for q in sql if q.lstrip().upper().startswith(('UPDATE READER_PROBLEMS','INSERT INTO READER_PROBLEMS','DELETE FROM READER_PROBLEMS'))]
    finally:store.db.close()


def test_restoring_source_classifies_previously_ineligible_content(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        content=json.dumps({'extra':{'_reader':{'full':False}}})
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) VALUES ('p','rss','p',0,1,true,%s)",(content,))
            store.publications.set_source_deleted(['p'],False)
        assert store.db.execute("SELECT incomplete_class FROM publications WHERE id='p'").fetchone()[0]==0
    finally:store.db.close()


def test_unknown_full_is_not_silently_discarded(tmp_path,postgres_url):
    from oko.storage.incomplete_index import recover
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        raw=json.dumps({'extra':{'_reader':{'full':{'unexpected':'value'}}}})
        with store.db:
            store.db.execute("DELETE FROM state WHERE key='incomplete-class-recovered-v1'")
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) VALUES ('p','rss','p',0,1,false,%s)",(raw,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        recover(store)
        from oko.storage.incomplete_group_draft import save_keys
        with store.db:save_keys(store.db,['p'])
        assert tuple(store.db.execute("SELECT content,incomplete_class FROM publications WHERE id='p'").fetchone())==(raw,2)
        with pytest.raises(ValueError,match='Unrecognized publication completeness'):store.problems.snapshot(1)
    finally:store.db.close()


def test_http_status_displays_python_valid_jsonb_invalid_partial(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    raw=json.dumps({'extra':{'_reader':{'full':False,'reason':'truncated'}},'payload':'\x00'})
    with psycopg.connect(postgres_url,autocommit=True) as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
        db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,source_deleted,content) VALUES ('p','rss','p',0,1,false,%s)",(raw,))
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
        assert any(p['code']=='material_incomplete' and any(o.get('material_id')=='p' for o in p['affected']) for p in response.json()['problems']['current'])
    with psycopg.connect(postgres_url,autocommit=True) as db:
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==raw


def test_retry_captions_refreshes_class_atomically(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        content={'blocks':[{'type':'media','role':'subtitles','media_id':'m'}], 'extra':{'_reader':{'full':False},'youtube':{'captions':'error'}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,1)",(json.dumps(content),))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','file','unavailable')")
        monkeypatch.setattr(store.subscriptions,'youtube_theses',lambda _:True)
        with transaction(store.db):store.jobs.retry_media('p',revalidate_ready=False)
        row=store.db.execute("SELECT incomplete_class,content FROM publications WHERE id='p'").fetchone()
        assert row['incomplete_class']==0
        assert json.loads(row['content'])['extra']['youtube']['captions']=='pending'
    finally:store.db.close()


def test_unavailable_preview_refreshes_derived_class(tmp_path,postgres_url):
    from oko.collection.contracts import Material,SourceManifest
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        store.ensure_demo()
        manifest=SourceManifest(post={},post_web_unavailable=True)
        content={'blocks':[],'extra':{'_reader':{'full':False,'source_manifest':manifest.model_dump()}}}
        with store.db:
            store.db.execute("INSERT INTO publications(id,source_id,platform,external_id,url,published,viewed,complete,content,incomplete_class) VALUES ('p','rss:p','rss','p','https://example.test/p',1,0,1,%s,1)",(json.dumps(content),))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('demo','p',true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,reason) VALUES ('m','p','image:1','x','file','unavailable','Локальный файл вложения недоступен')")
        with transaction(store.db):
            error,_=store.collect_material('p',Material('Title','Text',source_manifest=manifest))
        assert error and error.code=='media-unavailable'
        row=store.db.execute("SELECT incomplete_class,content FROM publications WHERE id='p'").fetchone()
        from oko.storage.incomplete_index import content_class
        assert row['incomplete_class']==content_class(row['content'])
    finally:store.db.close()


def test_forget_many_resets_content_and_class_together(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(json.dumps({'extra':{'_reader':{'full':False}}}),))
        with transaction(store.db):store.publications.forget_many(['p'])
        row=store.db.execute("SELECT content,incomplete_class FROM publications WHERE id='p'").fetchone()
        assert tuple(row)==('{}',1)
    finally:store.db.close()
