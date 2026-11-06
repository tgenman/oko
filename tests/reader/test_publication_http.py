"""Publication HTTP contract: one representation with conditional retrieval."""
from tests.support.job_claim import claim_job, retry_job
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app


def test_running_retry_takes_precedence_over_previous_processing_error(tmp_path):
    from tests.support.synthetic_material import material as Material
    from tests.support.source import save_material
    from tests.support.processing_jobs import reconcile
    _, items=library(tmp_path,[1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':True}).status_code==200
        def prepare():
            store=app.state.host.require_store()
            fetch=claim_job(store, store.clock(),feeds=[store.item_feed(items[0])])
            assert fetch and fetch.kind=='fetch'
            save_material(store,fetch,Material('Synthetic','Original text',captions='available'))
        client.portal.call(prepare)
        response = client.get('/api/v3/publication/'+items[0])
        assert response.status_code == 200
        publication = response.json()
        assert 'source_position' not in publication and 'originals' not in publication
        assert all('id' in media for media in publication['media'])
        assert client.get('/api/v3/publication/'+items[0], headers={'If-None-Match': response.headers['etag']}).status_code == 304
        def fail():
            store=app.state.host.require_store()
            reconcile(store,'theses')
            job=claim_job(store, store.clock(),ai_only=True)
            assert job and job.kind=='theses'
            retry_job(store, job,store.clock(),'Previous failure',30,code='synthetic')
        client.portal.call(fail)
        failed=next(job for job in client.get('/api/v3/publication/'+items[0]).json()['jobs'] if job['operation']=='theses')
        assert failed['status']=='pending' and failed['reason_message']=='Previous failure', failed
        def start():
            store=app.state.host.require_store()
            with store.db:store.db.execute("UPDATE jobs SET next=0 WHERE kind='theses' AND item=%s",(items[0],))
            job=claim_job(store, store.clock(),ai_only=True)
            assert job and job.kind=='theses'
        client.portal.call(start)
        running=next(job for job in client.get('/api/v3/publication/'+items[0]).json()['jobs'] if job['operation']=='theses')
        assert running['status']=='running'
