from tests.support.cards import read_all_cards
import base64
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.telegram import Telegram
from tests.support.telegram_web import page

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR9sAAAAASUVORK5CYII=')


class AvatarTelegram(Telegram):
    photo = base64.b64encode(PNG).decode()

    async def info(self, account, peer):
        return dict(await super().info(account,peer),avatar=self.photo)


def test_client_avatar_is_local_authenticated_and_survives_restart(tmp_path):
    tg = AvatarTelegram()
    options = dict(token='test', api_only=True, telegram_client=tg)
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'fixture'})
        result = client.post('/api/v3/telegram/connect', json=dict(source_locator='@fixture',folder='Telegram',access={'kind':'client','profile':'personal'}))
        assert result.status_code == 200, result.text
        feed = result.json()['feed_id']
        saved = next(f for f in client.get('/api/v3/subscriptions').json()['feeds'] if f['id']==feed)
        url = saved['avatar_url']
        navigation = client.get('/api/v3/reading-composition')
        assert navigation.status_code == 200, navigation.text
        assert navigation.json()['feeds'][0]['feed']['avatar_url'] == url
        response = client.get(url)
        assert response.content == PNG and response.headers['content-type'] == 'image/png'
        assert client.get(url, headers={'Authorization':'Bearer wrong'}).status_code == 403
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get(url).content == PNG
        tg.photo = None
        client.put('/api/v3/telegram/profiles/personal', json={'configuration_ref':'fixture'})
        client.post('/api/v3/collect', json={'scope':'all'})
        wait_for(client, lambda: 'avatar_url' not in next(f for f in client.get('/api/v3/subscriptions').json()['feeds'] if f['id']==feed))
        assert client.get(url).status_code == 404
        assert read_all_cards(client, '/api/v3/cards')


def test_web_avatar_is_saved_locally_without_browser_cdn_access(tmp_path):
    requests=[]
    def reply(request):
        requests.append(str(request.url))
        if request.url.host == 'cdn.example.org':
            return httpx.Response(200,content=PNG,headers={'Content-Type':'image/png'})
        return httpx.Response(200,text='<i class="tgme_page_photo_image bgcolor4"><img src="https://cdn.example.org/avatar.jpg"></i>'+page([(11,'Public post')]))
    with TestClient(create_app(tmp_path, token='test',api_only=True,rss_transport=httpx.MockTransport(reply)),headers=HEADERS) as client:
        result = client.post('/api/v3/telegram/connect',json=dict(source_locator='@fixture',folder='Web',access={'kind':'web'}))
        assert result.status_code == 200,result.text
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert client.get(feed['avatar_url']).content == PNG
        assert any('cdn.example.org' in url for url in requests)
        assert client.get('/api/v3/status').json()['diagnostic_count'] == 0
