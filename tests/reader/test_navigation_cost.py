"""Navigation reads maintained visibility counts without loading each publication."""
from fastapi.testclient import TestClient
from tests.support.reading_cost import library,traced_app,tracing
from tests.support.reader import HEADERS


def test_navigation_sql_does_not_grow_with_publication_count(tmp_path):
    costs=[]
    for count in (26,100):
        root=tmp_path/str(count)
        library(root,[count]*4)
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client:
            tracing(client,app)
            response=client.get('/api/v3/reading-composition')
            assert response.status_code==200,response.text
            result=response.json()
            assert result['count']==count*4
            assert {feed['count'] for feed in result['feeds']}=={count}
            assert {folder['count'] for folder in result['folders']}=={count}
            costs.append(int(response.headers['X-Test-SQL']))
    assert costs[1]<=costs[0]+5,costs


def test_navigation_batches_feed_metadata(tmp_path):
    costs=[]
    for feeds in (4,40):
        root=tmp_path/str(feeds)
        library(root,[1]*feeds)
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client:
            tracing(client,app)
            response=client.get('/api/v3/reading-composition')
            assert response.status_code==200,response.text
            assert len(response.json()['feeds'])==feeds
            assert response.json()['count']==feeds
            costs.append(int(response.headers['X-Test-SQL']))
    assert costs[1]<=costs[0]+10,costs


def test_status_batches_settings_and_bindings(tmp_path):
    costs=[]
    for feeds in (4,40):
        root=tmp_path/str(feeds)
        library(root,[1]*feeds)
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client:
            tracing(client,app)
            response=client.get('/api/v3/status')
            assert response.status_code==200,response.text
            assert len(response.json()['initial_histories'])==feeds
            costs.append(int(response.headers['X-Test-SQL']))
    assert costs[1]<=costs[0]+10,costs


def test_status_does_not_decode_complete_publication_headers(tmp_path):
    from tests.support.reading_measurements import ReadingMeasurements
    costs=[]
    for count in (26,100):
        root=tmp_path/str(count)
        library(root,[count]*4)
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,root) as measure:
            response,cost=measure.request('status','GET','/api/v3/status')
            assert response.json()['problems']['count']==0
            costs.append(cost['json_loads'])
    assert costs[1]<=costs[0]+5,costs


def test_status_does_not_read_unused_titles_for_affected_publications(tmp_path):
    from oko.storage.store import Store
    costs=[]
    for count in (26,100):
        root=tmp_path/str(count)
        library(root,[count]*4)
        store=Store(root,lambda _:None)
        with store.db:
            store.db.execute("UPDATE jobs SET status='failed',error='Synthetic source failure',problem_code='source-connection' WHERE kind='fetch'")
        store.db.close()
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client:
            tracing(client,app)
            response=client.get('/api/v3/status')
            assert response.status_code==200,response.text
            objects=[obj for problem in response.json()['problems']['current'] for obj in problem['affected'] if obj['kind']=='material']
            assert len(objects)==count*4
            assert all('label' not in obj for obj in objects)
            costs.append(int(response.headers['X-Test-SQL']))
    assert costs[1]<=costs[0]+10,costs
