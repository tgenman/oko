from tests.support.cards import read_all_cards
import base64
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.telegram_web import page

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR9sAAAAASUVORK5CYII=')






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
