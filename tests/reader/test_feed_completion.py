"""Feed completion at HTTP: exact scope, captured members and Telegram rules."""
from fastapi.testclient import TestClient
import httpx
from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.processes import wait_cards
from tests.support.presence import presence
from tests.support.rss import rss, import_feed, wait_cards as rss_cards
from tests.support.filters import FilterSource, transport, setup_filters
from tests.support.sources import LateSource
from oko.collection.demo import DemoSource


def test_feed_completion_includes_hidden_and_preserves_subscription(tmp_path):
    with TestClient(create_app(tmp_path, source=FilterSource(), token='test', ai_transport=transport([])), headers=HEADERS) as client:
        setup_filters(client)
        wait_for(client, lambda: len(rows := client.get('/api/v3/cards?show_hidden=true').json()) == 3 and all(r['filter']['attempt']['state']=='ready' for r in rows))
        assert len(client.get('/api/v3/cards').json()) == 2
        preview = client.post('/api/v3/feeds/demo/completion/preview').json()
        assert preview['publications_count'] == 3
        response = client.post('/api/v3/feeds/demo/completion', json={'token':preview['token']}, headers={'X-Oko-Action':'complete-demo'})
        assert response.status_code == 200, response.text
        assert client.get('/api/v3/cards?show_hidden=true').json() == []
        assert client.get('/api/v3/subscriptions').json()['feeds'][0]['id'] == 'demo'
        assert not client.get('/api/v3/reading-composition').json()['feeds'][0]['has_unviewed']
        receipt = client.get('/api/v3/commands/complete-demo').json()
        assert receipt['context'] == {'operation':'complete_feed','feed_id':'demo'}
        assert receipt['outcome'] == 'succeeded'
        assert client.post('/api/v3/feeds/demo/completion', json={'token':preview['token']}).status_code == 200


def test_feed_completion_isolated_from_sibling_feed_and_folder_token(tmp_path):
    def respond(request):
        return httpx.Response(200,text=rss() if request.url.path=='/feed' else '<article>Текст</article>')
    with TestClient(create_app(tmp_path, source=DemoSource(), token='test', rss_enabled=True, rss_transport=httpx.MockTransport(respond)),headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False})
        other = import_feed(client)['id']
        assert client.patch('/api/v3/feeds/'+other,json={'folder_id':'demo'}).status_code == 200
        presence(client,'a')
        rss_cards(client,4)
        folder_token=client.post('/api/v3/folders/demo/completion/preview').json()['token']
        assert client.post('/api/v3/feeds/demo/completion',json={'token':folder_token}).status_code == 409
        token=client.post('/api/v3/feeds/demo/completion/preview').json()['token']
        assert client.post('/api/v3/feeds/'+other+'/completion',json={'token':token}).status_code == 409
        assert client.post('/api/v3/feeds/demo/completion',json={'token':token}).status_code == 200
        assert len(client.get('/api/v3/cards').json()) == 1
        assert client.get('/api/v3/cards').json()[0]['feed_id'] == other


def test_new_feed_arrival_requires_confirmation_and_repeat_leaves_future_items(tmp_path):
    current=[rss()]
    def respond(request):
        return httpx.Response(200,text=current[0] if request.url.path=='/feed' else '<article>Текст</article>')
    with TestClient(create_app(tmp_path,token='test',rss_enabled=True,rss_transport=httpx.MockTransport(respond)),headers=HEADERS) as client:
        feed=import_feed(client)['id'];presence(client,'a');rss_cards(client,1)
        path='/api/v3/feeds/'+feed+'/completion'
        token=client.post(path+'/preview').json()['token']
        current[0]=rss(guid='two',url='https://source.test/two');client.post('/api/v3/collect');rss_cards(client,2)
        changed=client.post(path,json={'token':token})
        assert changed.status_code == 409
        accepted=changed.json()['details']['preview']['token']
        assert len(client.get('/api/v3/cards').json()) == 2
        assert client.post(path,json={'token':accepted}).status_code == 200
        current[0]=rss(guid='three',url='https://source.test/three');client.post('/api/v3/collect');rss_cards(client,1)
        assert client.post(path,json={'token':accepted}).status_code == 200
        assert len(client.get('/api/v3/cards').json()) == 1
        assert client.post('/api/v3/feeds/missing/completion/preview').status_code == 404
        assert client.post('/api/v3/feeds/missing/completion',json={'token':'wrong'}).status_code == 404


def test_feed_completion_covers_loading_and_rejects_late_fetch(tmp_path):
    with TestClient(create_app(tmp_path,source=LateSource(),token='test'),headers=HEADERS) as client:
        presence(client,'a')
        rows=wait_for(client,lambda: client.get('/api/v3/cards').json())
        assert any(r['availability']!='available' for r in rows)
        token=client.post('/api/v3/feeds/demo/completion/preview').json()['token']
        assert client.post('/api/v3/feeds/demo/completion',json={'token':token}).status_code == 200
        assert client.get('/api/v3/cards').json() == []
        assert not list((tmp_path/'content').glob('*/*'))
