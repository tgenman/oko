from tests.support.presence import presence, close_presence
from tests.support.rss import rss, import_feed, wait_cards
from tests.support.subscriptions import saved_sources
from tests.support.reader import wait_for
from pathlib import Path
import time
from datetime import datetime, timezone
import httpx
from fastapi.testclient import TestClient
from tests.support.app import create_app

HEADERS = {'Authorization': 'Bearer test-token'}
PNG = bytes.fromhex('89504e470d0a1a0a') + b'local-image'


def test_rss_article_images_are_local_and_viewed_identity_stays_deleted(tmp_path: Path):
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss(), headers={'Content-Type': 'application/rss+xml'})
        if request.url.path == '/article':
            return httpx.Response(200, text='<html><nav>Шум</nav><article><h1>Полная статья</h1><p>Полный текст статьи.</p><img src="/image.png"></article></html>')
        return httpx.Response(200, content=PNG, headers={'Content-Type': 'image/png'})
    kwargs = dict(token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        card = wait_cards(client)[0]
        material = client.get('/api/v3/materials/' + card['id']).json()
        assert (material['content']['completeness']['state']=='complete') is True
        assert 'Полный текст статьи.' in material['content']['text'] and 'Шум' not in material['content']['text']
        image = material['content']['images'][0]
        assert client.get(image).content == PNG
        assert client.post('/api/v3/materials/' + card['id'] + '/viewed').status_code == 200
        assert client.get(image).status_code == 404
        assert not list((tmp_path / 'content').rglob('*.png'))
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        time.sleep(.15)
        assert client.get('/api/v3/cards').json() == []


def test_incomplete_article_can_be_retried_without_losing_excerpt(tmp_path: Path):
    available = False
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        return httpx.Response(200, text='<article>Восстановленная статья</article>') if available else httpx.Response(403)
    saved_sources(tmp_path, [('https://source.test/feed', 'Источник', 'Новости')])
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        presence(client, 'test')
        card = wait_cards(client)[0]
        material = client.get('/api/v3/materials/' + card['id']).json()
        assert (material['content']['completeness']['state']=='complete') is False and material['content']['text'] == 'Анонс статьи'
        assert 'авторизация' in material['content']['completeness'].get('message','')
        available = True
        assert client.post('/api/v3/materials/' + card['id'] + '/retry').status_code == 200
        material = client.get('/api/v3/materials/' + wait_cards(client)[0]['id']).json()
        assert (material['content']['completeness']['state']=='complete') is True and material['content']['text'] == 'Восстановленная статья'


def test_initial_period_resume_atom_and_source_errors_are_independent(tmp_path: Path):
    date = datetime.fromtimestamp(time.time(), timezone.utc).isoformat()
    atom = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>atom-one</id><title>Atom</title><published>{date}</published><link href="/atom-article"/><summary>Кратко</summary></entry></feed>'
    current = rss() + ''
    def response(request):
        path = request.url.path
        if path == '/feed':
            return httpx.Response(200, text=current)
        if path == '/atom':
            return httpx.Response(200, text=atom)
        if path == '/denied':
            return httpx.Response(401)
        if path == '/limited':
            return httpx.Response(429, headers={'Retry-After': '120'})
        if path == '/down':
            return httpx.Response(503)
        return httpx.Response(200, text='<article>Полный текст</article>')
    kwargs = dict(token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    saved_sources(tmp_path, [('https://source.test/'+path, path, 'Новости') for path in ('feed', 'atom', 'denied', 'limited', 'down')])
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'test')
        cards = wait_cards(client, 2)
        assert {c['title'] for c in cards} == {'Atom', 'Заголовок'}
        state = client.get('/api/v3/status').json()
        assert state['total_sources'] == 5 and state['ready_sources'] == 2
        assert state['runtime_state']=='running'
        assert any(work['phase']=='waiting' for work in state['work'])
        assert set([p['message'] for p in state['problems']['current'] if p['code'] in ('source_unavailable','material_save_failed','material_read_failed')]) == {'Требуется авторизация', 'Ограничение частоты', 'Источник недоступен'}
        assert any(s['gap'] for s in state['sources'])
    # The old cursor is not a date cutoff advanced to "now"; an out-of-order
    # publication still present in RSS must be collected on the next run.
    current = rss().replace('</channel>', rss(guid='late', url='https://source.test/late', days=.5).split('<channel>')[1].split('</channel>')[0] + '</channel>')
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        assert len(wait_cards(client, 3)) == 3


def test_old_initial_items_excluded_and_ambiguous_guids_not_hidden(tmp_path: Path):
    old = rss(guid='old', url='https://source.test/old', days=8)
    recent = rss(guid='duplicate', url='https://source.test/a')
    other = rss(guid='duplicate', url='https://source.test/b')
    feed_xml = recent.replace('</channel>', other.split('<channel>')[1].split('</channel>')[0] + old.split('<channel>')[1].split('</channel>')[0] + '</channel>')
    def response(request):
        return httpx.Response(200, text=feed_xml if request.url.path == '/feed' else '<article>Текст</article>')
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        cards = wait_cards(client, 2)
        assert {c['source_url'] for c in cards} == {'https://source.test/a', 'https://source.test/b'}


def test_retry_missing_image_preserves_local_text_and_cleans_old_assets(tmp_path: Path):
    available = False
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/article':
            return httpx.Response(200, text='<article>Текст<img src="/ok.png"><img src="/later.png"></article>')
        if request.url.path == '/later.png' and not available:
            return httpx.Response(404)
        return httpx.Response(200, content=PNG, headers={'Content-Type': 'image/png'})
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        card = wait_cards(client)[0]
        assert (card['completeness']['state']=='complete') is False and len(card['images']) == 1
        available = True
        client.post('/api/v3/materials/' + card['id'] + '/retry')
        card = wait_cards(client)[0]
        assert (card['completeness']['state']=='complete') is True and len(card['images']) == 2
        client.post('/api/v3/materials/' + card['id'] + '/viewed')
        assert all(client.get(url).status_code == 404 for url in card['images'])


def test_source_disabled_switch_cannot_continue_collecting(tmp_path: Path):
    def response(request):
        return httpx.Response(200, text=rss() if request.url.path == '/feed' else '<article>Текст</article>')
    saved_sources(tmp_path, [('https://source.test/feed', 'Источник', 'Новости')])
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        # RSS collection can be disabled independently of immutable Telegram access.
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]
        client.patch('/api/v3/feeds/' + feed['id'], json={'transport': 'rss'})
        presence(client, 'test')
        wait_cards(client)
        client.patch('/api/v3/feeds/' + feed['id'], json={'transport': None})
        client.post('/api/v3/collect')
        assert client.get('/api/v3/status').json()['total_sources'] == 0


def test_rss_saved_jobs_recover_after_failed_file_commit(tmp_path: Path):
    failed = False
    def fault(point):
        nonlocal failed
        if point == 'after_file_move' and not failed:
            failed = True
            raise OSError('simulated interruption')
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/article':
            return httpx.Response(200, text='<article>Текст<img src="/image.png"></article>')
        return httpx.Response(200, content=PNG, headers={'Content-Type': 'image/png'})
    kwargs = dict(token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(create_app(tmp_path, fault=fault, **kwargs), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        for _ in range(100):
            if failed:
                break
            time.sleep(.02)
        assert failed
    with TestClient(create_app(tmp_path, **kwargs), headers=HEADERS) as client:
        presence(client, 'test')
        cards = wait_cards(client)
        assert len(cards) == 1 and cards[0]['completeness']['state']=='complete'
        assert client.get(cards[0]['images'][0]).content == PNG
        client.post('/api/v3/materials/' + cards[0]['id'] + '/viewed')
        assert not list((tmp_path / 'staging').iterdir())


def test_rss_viewed_during_image_download_blocks_late_result(tmp_path: Path):
    import asyncio
    import threading
    downloading = threading.Event()
    released = threading.Event()
    async def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/article':
            return httpx.Response(200, text='<article>Текст<img src="/image.png"></article>')
        downloading.set()
        while not released.is_set():
            try:
                await asyncio.sleep(.01)
            except asyncio.CancelledError:
                continue  # External provider returns after cancellation.
        return httpx.Response(200, content=PNG, headers={'Content-Type': 'image/png'})
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        assert downloading.wait(2)
        item = client.get('/api/v3/cards').json()[0]['id']
        assert client.post('/api/v3/materials/' + item + '/viewed').status_code == 200
        released.set()
        time.sleep(.1)
        assert client.get('/api/v3/materials/' + item).status_code == 404
        assert not (tmp_path / 'content' / item).exists()
        assert not (tmp_path / 'staging' / item).exists()


def test_url_identity_normalizes_default_port_and_fragment_without_guid(tmp_path: Path):
    first = rss(url='https://source.test:443/article#one').replace('<guid>one</guid>', '')
    second = rss(url='https://source.test/article#two').replace('<guid>one</guid>', '')
    feed_xml = first.replace('</channel>', second.split('<channel>')[1].split('</channel>')[0] + '</channel>')
    def response(request):
        return httpx.Response(200, text=feed_xml if request.url.path == '/feed' else '<article>Текст</article>')
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        assert len(wait_cards(client)) == 1


def test_atom_xhtml_keeps_text_images_and_xml_base(tmp_path: Path):
    date = datetime.now(timezone.utc).isoformat()
    atom = f'<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://source.test/news/"><entry><id>xhtml</id><title>XHTML</title><updated>{date}</updated><link href="article"/><content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Полный анонс</p><img src="/image.png"/></div></content></entry></feed>'
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=atom)
        if request.url.path == '/image.png':
            return httpx.Response(200, content=PNG, headers={'Content-Type': 'image/png'})
        return httpx.Response(404)
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        card = wait_cards(client)[0]
        assert card['source_url'] == 'https://source.test/news/article'
        assert (card['completeness']['state']=='complete') is False
        assert len(card['images']) == 1
        assert client.get('/api/v3/materials/' + card['id']).json()['content']['text'] == 'Полный анонс'


def test_reader_allows_authenticated_blob_images_without_remote_images(tmp_path: Path):
    with TestClient(create_app(tmp_path, token='test-token'), headers=HEADERS) as client:
        csp = client.get('/').headers['content-security-policy']
        assert "img-src 'self' blob:;" in csp
        assert 'https:' not in csp


def test_reused_guid_cannot_replace_saved_teaser_with_another_url(tmp_path: Path):
    reads = 0
    def response(request):
        nonlocal reads
        if request.url.path == '/feed':
            reads += 1
            body = rss(url='https://source.test/old') if reads <= 2 else rss(url='https://source.test/new').replace('Заголовок', 'Другой заголовок').replace('Анонс статьи', 'Другой анонс')
            return httpx.Response(200, text=body)
        return httpx.Response(404)
    with TestClient(create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        import_feed(client)
        presence(client, 'test')
        card = wait_cards(client)[0]
        client.post('/api/v3/collect')
        wait_for(client, lambda: reads >= 3 and not any(work['phase']=='collecting' for work in client.get('/api/v3/status').json()['work']))
        material = client.get('/api/v3/materials/' + card['id']).json()
        assert material['title'] == 'Заголовок' and material['content']['text'] == 'Анонс статьи'
        assert material['source_url'] == 'https://source.test/old'
