"""Navigation and status share each isolated library, retaining separate budgets."""
from fastapi.testclient import TestClient
from tests.support.reading_cost import library,traced_app,tracing
from tests.support.reading_measurements import ReadingMeasurements
from tests.support.reader import HEADERS


def test_navigation_and_status_cost_does_not_grow_with_publication_count(tmp_path):
    costs=[]
    for count in (26,100):
        root=tmp_path/str(count)
        library(root,[count]*4)
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client,ReadingMeasurements(client,app,root) as measure:
            response,navigation=measure.request('navigation','GET','/api/v3/reading-composition')
            result=response.json()
            assert result['count']==count*4
            assert {feed['count'] for feed in result['feeds']}=={count}
            assert {folder['count'] for folder in result['folders']}=={count}
            response,status=measure.request('status','GET','/api/v3/status')
            assert response.json()['problems']['count']==0
            costs.append((navigation,status))
    assert costs[1][0]['sql']<=costs[0][0]['sql']+5,costs
    assert costs[1][1]['json_loads']<=costs[0][1]['json_loads']+5,costs


def test_navigation_and_status_batch_feed_metadata_settings_and_bindings(tmp_path):
    costs=[]
    for feeds in (4,40):
        root=tmp_path/str(feeds)
        library(root,[1]*feeds)
        app=traced_app(root)
        with TestClient(app,headers=HEADERS) as client:
            tracing(client,app)
            navigation=client.get('/api/v3/reading-composition')
            assert navigation.status_code==200,navigation.text
            assert len(navigation.json()['feeds'])==feeds and navigation.json()['count']==feeds
            status=client.get('/api/v3/status')
            assert status.status_code==200,status.text
            assert len(status.json()['initial_histories'])==feeds
            costs.append((int(navigation.headers['X-Test-SQL']),int(status.headers['X-Test-SQL'])))
    assert costs[1][0]<=costs[0][0]+10,costs
    assert costs[1][1]<=costs[0][1]+10,costs


def test_status_does_not_read_unused_titles_for_affected_publications(tmp_path):
    from tests.support.storage import Store
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
