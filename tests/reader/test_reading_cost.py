"""Bound database work for local reading independently of machine speed."""
from fastapi.testclient import TestClient

from oko.collection.contracts import Material, Publication
from oko.storage.store import Store
from oko.subscriptions.subscriptions import AddFeed
from tests.support.app import create_app
from tests.support.reader import HEADERS


def test_feed_reading_does_not_rebuild_all_publications(tmp_path):
    store = Store(tmp_path, lambda _: None)
    feeds = []
    for index in range(4):
        feed = store.subscriptions.add_feed(AddFeed(
            url='https://www.youtube.com/channel/UC' + str(index).zfill(22),
            title=f'Synthetic {index}', folder=f'Folder {index}', verification='synthetic'))['id']
        feeds.append(feed)
        store.enqueue_discovery(feed)
        job = store.next_job(0, feeds=[feed])
        assert job is not None
        store.discovered([Publication(str(n), f'https://example.org/{index}/{n}', n,
                          teaser=Material(f'Video {n}', '', captions='not_requested', description='Synthetic video'))
                          for n in range(26)], '{}', job, feed)
    store.db.close()

    app = create_app(tmp_path, token='test')
    with TestClient(app, headers=HEADERS) as client:
        queries = []
        client.portal.call(lambda: app.state.runtime.require_store().db.set_trace_callback(queries.append))
        response = client.get('/api/v3/reading-composition')
        assert response.status_code == 200
        assert response.json()['count'] == 104
        assert len(queries) < 2000, f'navigation used {len(queries)} SQL statements for 104 publications'
        queries.clear()
        response = client.get('/api/v3/cards', params={'feed': feeds[0]})
        assert response.status_code == 200
        assert len(response.json()) == 26
        assert {card['feed_id'] for card in response.json()} == {feeds[0]}
        assert len(queries) < 3000, f'feed cards used {len(queries)} SQL statements for 26 publications'
        client.portal.call(lambda: app.state.runtime.require_store().db.set_trace_callback(None))
