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
        check=client.post('/api/v3/reading/check',json={'storage_id':None,'resources':[{'kind':'status','known_version':None}]}).json()
        storage=check['storage_id']
        old=client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':storage})
        assert old.status_code==200
        assert client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':storage,'If-None-Match':old.headers['ETag']}).status_code==304
        replacement=str(uuid4())
        def replace_identity():
            store=app.state.host.require_store()
            with store.db:store.db.execute('UPDATE reader_identity SET value=%s WHERE id=1',(replacement,))
            store.read_versions.identity=replacement
        client.portal.call(replace_identity)
        changed=client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':storage,'If-None-Match':old.headers['ETag']})
        assert changed.status_code==409 and changed.json()['code']=='storage_changed'
        fresh=client.get('/api/v3/publication/'+items[0],headers={'If-None-Match':old.headers['ETag']})
        assert fresh.status_code==200 and fresh.headers['ETag']!=old.headers['ETag']
        assert client.get('/api/v3/publication/'+items[0],headers={'X-Oko-Storage-Id':replacement}).status_code==200
        assert client.get('/api/v3/publication/unknown',headers={'X-Oko-Storage-Id':storage}).status_code==409
