"""Subject-specific navigation and status through the public HTTP interface."""
from fastapi.testclient import TestClient
from tests.support.reader import HEADERS
from tests.support.reading_cost import library, traced_app


def test_navigation_returns_folders_feeds_and_conditional_response(tmp_path):
    feeds, _ = library(tmp_path, [2, 1])
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        first = client.get('/api/v3/navigation')
        assert first.status_code == 200, first.text
        body = first.json()
        assert body['total'] == 3
        assert len(body['folders']) == 2
        listed = [feed for folder in body['folders'] for feed in folder['feeds']]
        assert {feed['id'] for feed in listed} == set(feeds)
        assert {folder['count'] for folder in body['folders']} == {1, 2}
        assert all({'id', 'title', 'avatar_url', 'count'} <= set(feed) for feed in listed)
        tag = first.headers['etag']
        unchanged = client.get('/api/v3/navigation', headers={'If-None-Match': tag})
        assert unchanged.status_code == 304 and unchanged.content == b''
        assert client.patch('/api/v3/feeds/'+feeds[0], json={'title':'Changed'}).status_code == 200
        updated = client.get('/api/v3/navigation', headers={'If-None-Match': tag})
        assert updated.status_code == 200 and updated.headers['etag'] != tag
        assert any(feed['title']=='Changed' for folder in updated.json()['folders'] for feed in folder['feeds'])


def test_compact_status_and_details_revalidate_independently(tmp_path):
    library(tmp_path, [1])
    with TestClient(traced_app(tmp_path), headers=HEADERS) as client:
        first = client.get('/api/v3/status')
        assert first.status_code == 200
        body = first.json()
        assert body['enabled'] is False
        assert body['collection']['state'] == 'idle'
        assert body['processing']['state'] == 'idle'
        assert isinstance(body['problems'], list)
        assert not {'work', 'initial_histories', 'telegram_reads', 'youtube'} & body.keys()
        assert body['runtime_state']=='disabled' and not body['work_allowed']
        repeated=client.get('/api/v3/status',headers={'If-None-Match':first.headers['etag']})
        assert repeated.status_code==304 and not repeated.content
        details=client.get('/api/v3/status/details')
        assert details.status_code==200
        assert {'current','recent','count','updated_at'} <= details.json()['problems'].keys()
        assert client.get('/api/v3/status/details',headers={'If-None-Match':details.headers['etag']}).status_code==304
        assert client.put('/api/v3/power',json={'enabled':True}).status_code==200
        changed=client.get('/api/v3/status',headers={'If-None-Match':first.headers['etag']})
        assert changed.status_code==200 and changed.json()['enabled'] is True


def test_status_survives_stop_and_legacy_check_is_absent(tmp_path):
    import time
    library(tmp_path, [1])
    app=traced_app(tmp_path)
    with TestClient(app,headers=HEADERS) as client:
        identity=client.get('/api/v3/status').headers['X-Oko-Storage-Id']
        assert client.post('/api/v3/reading/check',json={'storage_id':None,'resources':[]}).status_code==404
        assert client.post('/api/v3/stop').status_code==200
        for _ in range(100):
            if app.state.host.execution._store is None:break
            time.sleep(.01)
        stopped=client.get('/api/v3/status')
        assert stopped.status_code==200 and stopped.json()['runtime_state']=='stopped'
        assert stopped.headers['X-Oko-Storage-Id']==identity
        assert client.get('/api/v3/status',headers={'If-None-Match':stopped.headers['etag']}).status_code==304


def test_queue_size_is_not_reported_as_completed_progress(tmp_path):
    library(tmp_path,[2])
    with TestClient(traced_app(tmp_path,rss_enabled=False),headers=HEADERS) as client:
        assert client.put('/api/v3/power',json={'enabled':True}).status_code==200
        status=client.get('/api/v3/status').json()
        assert status['collection']=={'state':'running','completed':None,'total':None}
