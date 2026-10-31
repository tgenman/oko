from tests.support.cards import read_all_cards
from tests.support.presence import presence, close_presence
from fastapi.testclient import TestClient
from tests.support.app import create_app
from oko.collection.demo import DemoSource
from tests.support.processes import wait_cards
from tests.support.reader import HEADERS


def test_navigation_counts_and_feed_selection(tmp_path):
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test'), headers=HEADERS) as client:
        assert client.get('/api/v3/reading-composition', headers={'Authorization': 'Bearer wrong'}).status_code == 403
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        cards = wait_cards(client, 3)
        navigation = client.get('/api/v3/reading-composition').json()
        assert navigation['count'] == 3
        assert next(f['count'] for f in navigation['folders'] if f['id'] == 'demo') == 3
        assert next(f['count'] for f in navigation['feeds'] if f['feed']['id'] == 'demo') == 3
        assert read_all_cards(client, '/api/v3/cards?feed=demo') == cards
        assert read_all_cards(client, '/api/v3/cards?feed=missing') == []
        assert client.get('/api/v3/cards?folder=missing&feed=demo').status_code == 422
        client.post('/api/v3/materials/' + cards[0]['id'] + '/viewed')
        assert client.get('/api/v3/reading-composition').json()['count'] == 2


def test_navigation_counts_loading_publications_and_empty_feeds(tmp_path):
    from tests.support.sources import LateSource
    from tests.support.reader import wait_for
    with TestClient(create_app(tmp_path, source=LateSource(), token='test'), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        assert client.get('/api/v3/reading-composition').json()['count'] == 0
        presence(client, 'a')
        wait_for(client, lambda: len(read_all_cards(client, '/api/v3/cards')) == 3)
        assert all(card['loading'] for card in read_all_cards(client, '/api/v3/cards'))
        assert client.get('/api/v3/reading-composition').json()['count'] == 3
        assert client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'] is True


def test_completion_metadata_includes_hidden_and_survives_restart(tmp_path):
    from tests.support.filters import FilterSource, transport, setup_filters
    from tests.support.reader import wait_for
    options = dict(source=FilterSource(), token='test', api_only=True, ai_transport=transport([]))
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        setup_filters(client)
        cards = wait_for(client, lambda: (rows if len(rows := read_all_cards(client, '/api/v3/cards'))==2 and all(client.get('/api/v3/publication/'+r['id']).json()['filter']['state']=='ready' for r in rows) else None))
        for card in cards:
            assert client.post('/api/v3/materials/'+card['id']+'/viewed').status_code == 200
        nav = client.get('/api/v3/reading-composition').json()
        assert nav['feeds'][0]['count'] == 0
        assert nav['feeds'][0]['has_unviewed'] is True
        assert len(read_all_cards(client, '/api/v3/cards?show_hidden=true')) == 1
        preview = client.post('/api/v3/folders/demo/completion/preview').json()
        assert client.post('/api/v3/folders/demo/completion',json={'token':preview['token']}).status_code == 200
        assert client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'] is False
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['id'] == 'demo'
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'] is False


def test_new_arrival_makes_completed_feed_unviewed_again(tmp_path):
    import httpx
    from tests.support.rss import rss, import_feed, wait_cards as rss_cards
    from tests.support.reader import wait_for
    current = [rss()]
    def respond(request):
        return httpx.Response(200,text=current[0] if request.url.path=='/feed' else '<article>Материал</article>')
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_enabled=True,rss_transport=httpx.MockTransport(respond)),headers=HEADERS) as client:
        feed = import_feed(client)['id']
        first = rss_cards(client,1)[0]
        client.post('/api/v3/materials/'+first['id']+'/viewed')
        assert client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'] is False
        current[0] = rss(guid='new',url='https://source.test/new')
        client.post('/api/v3/collect')
        wait_for(client,lambda: client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed'])
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['id'] == feed


def test_navigation_counts_are_consistent_during_concurrent_membership_change(tmp_path, postgres_url):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from tests.support.worker_library import worker_library

    store, item, _, _ = worker_library(tmp_path, database_url=postgres_url)
    store.set_work_enabled(False)
    store.db.close()
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url)
    with TestClient(app, headers=HEADERS) as client, psycopg.connect(postgres_url) as writer:
        initial = client.get('/api/v3/navigation')
        assert initial.status_code == 200, initial.text
        assert initial.json()['total'] == 1
        entered, release = threading.Event(), threading.Event()
        def trace(query):
            if 'SELECT m.feed_id AS feed' in query and not entered.is_set():
                entered.set()
                assert release.wait(5)
        client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(trace))
        try:
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(client.get, '/api/v3/navigation')
                assert entered.wait(5)
                writer.execute('DELETE FROM feed_publications WHERE publication_id=%s', (item,))
                writer.commit()
                release.set()
                result = pending.result(5)
            assert result.status_code == 200, result.text
            assert result.json() == initial.json()
        finally:
            release.set()
            client.portal.call(lambda: app.state.host.require_store().db.set_trace_callback(None))
        assert client.get('/api/v3/navigation').json()['total'] == 0
