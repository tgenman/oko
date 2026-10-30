"""Publication HTTP contract: one representation with conditional retrieval."""
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app


def test_open_publication_and_revalidate_without_body(tmp_path):
    _, items = library(tmp_path, [1])
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        response = client.get('/api/v3/publication/'+items[0])
        assert response.status_code == 200
        publication = response.json()
        assert publication['id'] == items[0]
        assert isinstance(publication['text'], str)
        assert isinstance(publication['media'], list)
        for kind in ('filter', 'theses'):
            assert publication[kind]['state'] in {'disabled','pending','processing','ready','error'}
            assert {'state','result','error'} <= publication[kind].keys()
        etag = response.headers['ETag']
        unchanged = client.get('/api/v3/publication/'+items[0], headers={'If-None-Match':etag})
        assert unchanged.status_code == 304
        assert unchanged.content == b''
        assert client.post('/api/v3/materials/'+items[0]+'/viewed').status_code == 200
        missing = client.get('/api/v3/publication/'+items[0], headers={'If-None-Match':etag})
        assert missing.status_code == 404


def test_finished_theses_change_etag_and_return_whole_publication(tmp_path):
    from oko.collection.contracts import Material
    from tests.support.source import save_material
    from tests.support.processing_jobs import reconcile, save_derivative
    _, items = library(tmp_path, [1])
    app=traced_app(tmp_path)
    with TestClient(app, headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':True}).status_code == 200
        def complete():
            store=app.state.host.require_store()
            job=store.jobs.next_job(store.clock(),feeds=[store.item_feed(items[0])])
            assert job and job.kind=='fetch'
            save_material(store,job,Material('Synthetic','Original text',captions='available'))
        client.portal.call(complete)
        first=client.get('/api/v3/publication/'+items[0])
        assert first.status_code==200
        def summarize():
            store=app.state.host.require_store()
            reconcile(store,'theses')
            job=store.jobs.next_job(store.clock(),ai_only=True)
            assert job and job.kind=='theses'
            signature=store.processing.theses.signature(job.item)
            assert signature is not None
            save_derivative(store,job,signature,{'points':[{'text':'Synthetic summary','sources':[{'id':job.item,'url':'https://example.org/0/0'}]}]},'Synthetic summary',provenance=None)
        client.portal.call(summarize)
        updated=client.get('/api/v3/publication/'+items[0],headers={'If-None-Match':first.headers['ETag']})
        assert updated.status_code==200
        assert updated.headers['ETag']!=first.headers['ETag']
        assert updated.json()['text']=='Original text'
        assert updated.json()['theses']['state']=='ready'
        assert updated.json()['theses']['result']['points']==['Synthetic summary']
        unchanged=client.get('/api/v3/publication/'+items[0],headers={'If-None-Match':updated.headers['ETag']})
        assert unchanged.status_code==304 and unchanged.content==b''


def test_running_retry_takes_precedence_over_previous_processing_error(tmp_path):
    from oko.collection.contracts import Material
    from tests.support.source import save_material
    from tests.support.processing_jobs import reconcile
    _, items=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':True}).status_code==200
        def prepare():
            store=app.state.host.require_store()
            fetch=store.jobs.next_job(store.clock(),feeds=[store.item_feed(items[0])])
            assert fetch and fetch.kind=='fetch'
            save_material(store,fetch,Material('Synthetic','Original text',captions='available'))
        client.portal.call(prepare)
        assert client.get('/api/v3/publication/'+items[0]).status_code==200
        def fail():
            store=app.state.host.require_store()
            reconcile(store,'theses')
            job=store.jobs.next_job(store.clock(),ai_only=True)
            assert job and job.kind=='theses'
            store.jobs.retry(job,store.clock(),'Previous failure',30,code='synthetic')
        client.portal.call(fail)
        failed=client.get('/api/v3/publication/'+items[0]).json()['theses']
        assert failed['state']=='error' and failed['error']=='Previous failure', failed
        def start():
            store=app.state.host.require_store()
            with store.db:store.db.execute("UPDATE jobs SET next=0 WHERE kind='theses' AND item=%s",(items[0],))
            job=store.jobs.next_job(store.clock(),ai_only=True)
            assert job and job.kind=='theses'
        client.portal.call(start)
        running=client.get('/api/v3/publication/'+items[0]).json()['theses']
        assert running['state']=='processing' and running['error'] is None


def test_storage_precondition_rejects_other_library_and_etag_tracks_identity(tmp_path):
    from uuid import uuid4
    _,items=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        details=client.get('/api/v3/status/details')
        storage=details.headers['X-Oko-Storage-Id']
        old=client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':storage})
        assert old.status_code==200
        assert client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':storage,'If-None-Match':old.headers['ETag']}).status_code==304
        replacement=str(uuid4())
        def replace_identity():
            store=app.state.host.require_store()
            import json
            with store.db:store.db.execute("UPDATE state SET value=%s WHERE key='storage-id'",(json.dumps(replacement),))
            store.read_versions.identity=replacement
        client.portal.call(replace_identity)
        changed=client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':storage,'If-None-Match':old.headers['ETag']})
        assert changed.status_code==409 and changed.json()['code']=='storage_changed'
        fresh=client.get('/api/v3/publication/'+items[0],headers={'If-None-Match':old.headers['ETag']})
        assert fresh.status_code==200 and fresh.headers['ETag']!=old.headers['ETag']
        assert client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':replacement}).status_code==200
        assert client.get('/api/v3/publication/unknown',headers={'X-Oko-Storage-Id':storage}).status_code==409


def test_open_publication_keeps_parent_and_linked_article_in_one_snapshot(tmp_path, postgres_url):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from tests.support.worker_library import worker_library

    store, parent, _, _ = worker_library(tmp_path, text='Parent before', database_url=postgres_url)
    try:
        store.set_work_enabled(False)
        with store.db:
            row = dict(store.db.execute('SELECT * FROM publications WHERE id = %s', (parent,)).fetchone())
            row.pop('source_deleted')  # New synthetic publications start as present.
            child_content = json.loads(row['content'])
            child_content['blocks'][0]['text'] = 'Child before'
            child = dict(row, id='snapshot-child', source_id='snapshot-child', external_id='snapshot-child',
                         platform='other_web', content=json.dumps(child_content))
            fields = list(child)
            store.db.execute('INSERT INTO publications('+','.join(fields)+') VALUES ('+','.join('%s' for _ in fields)+')', [child[f] for f in fields])
            store.db.execute('INSERT INTO publication_links VALUES (%s,%s)', (parent, 'snapshot-child'))
    finally:
        store.db.close()
    app = traced_app(tmp_path, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(postgres_url) as writer:
        path = '/api/v3/publication/' + parent
        initial = client.get(path)
        assert initial.status_code == 200, initial.text
        old = initial.json()['text']
        assert 'Parent before' in old and 'Child before' in old
        entered, release = threading.Event(), threading.Event()
        def trace(query):
            if 'SELECT l . source_publication_id AS reader_source' in query and not entered.is_set():
                entered.set()
                assert release.wait(5)
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(trace))
        try:
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(client.get, path)
                assert entered.wait(5)
                for identity, text in ((parent, 'Parent after'), ('snapshot-child', 'Child after')):
                    writer.execute("UPDATE publications SET content=jsonb_set(content::jsonb,'{blocks,0,text}',to_jsonb(%s::text))::text WHERE id=%s", (text, identity))
                writer.commit()
                release.set()
                result = pending.result(5)
            assert result.status_code == 200, result.text
            assert result.json()['text'] == old
        finally:
            release.set()
            client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))
        updated = client.get(path).json()['text']
        assert 'Parent after' in updated and 'Child after' in updated
