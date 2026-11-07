"""Viewing a publication preserves its readable contents."""

from fastapi.testclient import TestClient
import pytest

from tests.support.presence import presence
from tests.support.processes import wait_cards
from tests.support.app import create_app
from tests.support.synthetic_source import SyntheticSource


def test_viewed_publication_remains_readable(tmp_path):
    with TestClient(create_app(tmp_path, source=SyntheticSource(), token='test-token'),
                    headers={'Authorization': 'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global', json={})
        presence(client, 'a')
        card = wait_cards(client, 3)[0]
        url = '/api/v3/publication/' + card['id']
        before = client.get(url)
        assert before.status_code == 200
        assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
        assert card['id'] not in [item['id'] for item in client.get('/api/v3/cards').json()['items']]
        after = client.get(url)
        assert after.status_code == 200
        assert after.json()['content'] == before.json()['content']
        assert after.json()['title'] == before.json()['title']
        shown = client.get('/api/v3/cards?show_read=true')
        assert shown.status_code == 200
        viewed = next(item for item in shown.json()['items'] if item['id'] == card['id'])
        assert viewed['viewed'] is True
        assert client.post('/api/v3/cards/unread', json={'ids': [card['id']]}).status_code == 204
        assert card['id'] in [item['id'] for item in client.get('/api/v3/cards').json()['items']]


def test_viewed_publication_survives_later_collection(tmp_path):
    from tests.support.reader import wait_for

    with TestClient(create_app(tmp_path, source=SyntheticSource(), token='test-token'),
                    headers={'Authorization': 'Bearer test-token'}) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        card = wait_cards(client, 3)[0]
        url = '/api/v3/publication/' + card['id']
        wait_for(client, lambda: client.get(url).json()['availability'] == 'available')
        assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: not any(work['phase'] == 'collecting' for work in client.get('/api/v3/status/details').json()['work']))
        assert client.get(url).status_code == 200


def test_rediscovered_viewed_publication_updates_without_losing_read_state(tmp_path):
    import httpx
    from tests.support.reader import wait_for
    from tests.support.rss import import_feed, rss, wait_cards as wait_rss_cards

    description = ['Original excerpt']
    def response(request):
        return httpx.Response(200, text=rss().replace('Анонс статьи', description[0]) if request.url.path == '/feed' else '<article>Full text</article>')

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        import_feed(client)
        presence(client, 'a')
        card = wait_rss_cards(client)[0]
        url = '/api/v3/publication/' + card['id']
        assert 'Original excerpt' in client.get(url).json()['text']
        assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
        description[0] = 'Changed excerpt'
        assert client.post('/api/v3/collect').status_code == 200
        updated = wait_for(client, lambda: (value if 'Changed excerpt' in (value := client.get(url).json())['text'] else None))
        assert updated['id'] == card['id']
        assert all(item['id'] != card['id'] for item in client.get('/api/v3/cards').json()['items'])
        assert any(item['id'] == card['id'] and item['viewed'] for item in client.get('/api/v3/cards?show_read=true').json()['items'])


def test_unlinked_article_is_preserved_and_readable_when_linked_again(tmp_path):
    import httpx
    from tests.support.reader import wait_for
    from tests.support.rss import import_feed, rss, wait_cards as wait_rss_cards

    link, offline = ['https://source.test/first'], [False]
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss(url=link[0]))
        if request.url.path == '/first':
            return httpx.Response(503) if offline[0] else httpx.Response(200, text='<article>Preserved first article</article>')
        return httpx.Response(200, text='<article>Second article</article>')

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        import_feed(client)
        presence(client, 'a')
        item = wait_rss_cards(client)[0]['id']
        url = '/api/v3/publication/' + item
        wait_for(client, lambda: 'Preserved first article' in client.get(url).json()['text'])
        link[0] = 'https://source.test/second'
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: 'Second article' in client.get(url).json()['text'])
        offline[0] = True
        link[0] = 'https://source.test/first'
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: 'Preserved first article' in client.get(url).json()['text'])


def test_viewed_publication_survives_restart(tmp_path):
    options = dict(source=SyntheticSource(), token='test-token')
    headers = {'Authorization': 'Bearer test-token'}
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        client.patch('/api/v3/settings/oko/global', json={'theses': False})
        presence(client, 'a')
        card = wait_cards(client, 3)[0]
        assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
    with TestClient(create_app(tmp_path, **options), headers=headers) as client:
        presence(client, 'b')
        shown = client.get('/api/v3/cards?show_read=true').json()['items']
        assert any(item['id'] == card['id'] and item['viewed'] for item in shown)
        assert client.get('/api/v3/publication/' + card['id']).status_code == 200


def test_diagnostics_for_viewed_publication_are_not_forgotten(tmp_path):
    with TestClient(create_app(tmp_path, source=SyntheticSource(), token='test-token'),
                    headers={'Authorization': 'Bearer test-token'}) as client:
        presence(client, 'a')
        item = wait_cards(client, 3)[0]['id']
        assert client.post('/api/v3/cards/read', json={'ids': [item]}).status_code == 204
        report = client.post('/api/v3/diagnostics', json={'source': 'reader', 'message': 'Synthetic failure', 'material_ids': [item]})
        assert report.status_code == 200
        assert report.json()['state'] == 'accepted'
    with TestClient(create_app(tmp_path, source=SyntheticSource(), token='test-token'),
                    headers={'Authorization': 'Bearer test-token'}) as client:
        entries = client.get('/api/v3/diagnostics').json()['groups']
        assert any(entry['message'] == 'Synthetic failure' for entry in entries)


def test_failed_collection_remains_visible_after_viewing(tmp_path):
    import httpx
    from tests.support.reader import wait_for
    from tests.support.rss import import_feed, rss

    def response(request):
        return httpx.Response(200, text=rss()) if request.url.path == '/feed' else httpx.Response(503)

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        feed = import_feed(client)
        presence(client, 'a')
        item = wait_for(client, lambda: next(iter(client.get('/api/v3/cards').json()['items']), None))['id']
        def problem():
            return next((value for value in client.get('/api/v3/status/details').json()['problems']['current']
                         if any(ref.get('material_id') == item for ref in value['affected'])), None)
        wait_for(client, problem)
        assert client.post('/api/v3/cards/read', json={'ids': [item]}).status_code == 204
        assert problem() is not None
        assert client.delete('/api/v3/feeds/' + feed['id']).status_code == 200
        assert problem() is None
        restored = import_feed(client)
        assert restored['id'] == feed['id']
        assert wait_for(client, problem)


def test_failure_after_viewing_is_reported(tmp_path):
    import asyncio
    import httpx
    from threading import Event
    from tests.support.reader import wait_for
    from tests.support.rss import import_feed, rss

    started, release = Event(), Event()
    async def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        started.set()
        await asyncio.to_thread(release.wait, 30)
        return httpx.Response(503)

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            import_feed(client)
            presence(client, 'a')
            assert started.wait(5)
            item = wait_for(client, lambda: next(iter(client.get('/api/v3/cards').json()['items']), None))['id']
            assert client.post('/api/v3/cards/read', json={'ids': [item]}).status_code == 204
            release.set()
            def problem():
                return next((value for value in client.get('/api/v3/status/details').json()['problems']['current']
                             if any(ref.get('material_id') == item for ref in value['affected'])), None)
            assert wait_for(client, problem)
        finally:
            release.set()


def test_fetch_started_before_viewing_completes(tmp_path):
    import asyncio
    from threading import Event
    from tests.support.reader import wait_for
    from tests.support.synthetic_source import SyntheticAdapter

    started, release = Event(), Event()
    active_url = []

    class DelayedAdapter(SyntheticAdapter):
        async def fetch(self, publication):
            active_url.append(publication.url)
            started.set()
            await asyncio.to_thread(release.wait, 30)
            return await super().fetch(publication)

    class DelayedSource(SyntheticSource):
        def publication_adapter(self):
            return DelayedAdapter()

    with TestClient(create_app(tmp_path, source=DelayedSource(), token='test-token'),
                    headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            presence(client, 'a')
            assert started.wait(5)
            fetching = wait_for(client, lambda: next((card for card in client.get('/api/v3/cards').json()['items'] if card['source_url'] == active_url[0]), None))
            url = '/api/v3/publication/' + fetching['id']
            assert client.post('/api/v3/cards/read', json={'ids': [fetching['id']]}).status_code == 204
            release.set()
            wait_for(client, lambda: client.get(url).json()['availability'] == 'available')
            assert client.get(url).json()['text']
        finally:
            release.set()


def test_pending_fetch_continues_after_viewing(tmp_path):
    import asyncio
    from threading import Event
    from tests.support.reader import wait_for
    from tests.support.synthetic_source import SyntheticAdapter

    started, release = Event(), Event()
    active_url = []

    class DelayedAdapter(SyntheticAdapter):
        async def fetch(self, publication):
            if not active_url:
                active_url.append(publication.url)
                started.set()
                await asyncio.to_thread(release.wait, 30)
            return await super().fetch(publication)

    class DelayedSource(SyntheticSource):
        def publication_adapter(self):
            return DelayedAdapter()

    with TestClient(create_app(tmp_path, source=DelayedSource(), token='test-token'),
                    headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            presence(client, 'a')
            assert started.wait(5)
            pending = wait_for(client, lambda: next((card for card in client.get('/api/v3/cards').json()['items'] if card['source_url'] != active_url[0]), None))
            url = '/api/v3/publication/' + pending['id']
            assert client.post('/api/v3/cards/read', json={'ids': [pending['id']]}).status_code == 204
            release.set()
            wait_for(client, lambda: client.get(url).json()['availability'] == 'available')
            assert client.get(url).json()['text']
        finally:
            release.set()


def test_readding_deleted_feed_restores_saved_publication(tmp_path):
    import httpx
    from tests.support.rss import import_feed, rss, wait_cards as wait_rss_cards
    from tests.support.reader import wait_for
    from tests.support.subscriptions import add_source

    def response(request):
        return httpx.Response(200, text=rss() if request.url.path == '/feed' else '<article>Saved text</article>')

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        feed = import_feed(client)
        presence(client, 'a')
        card = wait_rss_cards(client)[0]
        assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
        assert client.delete('/api/v3/feeds/' + feed['id']).status_code == 200
        assert feed['id'] not in [item['id'] for item in client.get('/api/v3/subscriptions').json()['feeds']]
        assert feed['id'] not in [item['feed_id'] for item in client.get('/api/v3/status/details').json()['sources']]
        assert client.get('/api/v3/settings/feed/' + feed['id']).status_code == 404
        assert client.patch('/api/v3/settings/feed/' + feed['id'], json={'theses': True}).status_code == 404
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'title': 'Changed'}).status_code == 404
        assert client.get('/api/v3/publication/' + card['id']).status_code == 404
        for action in ('viewed', 'retry', 'filter/retry', 'theses/retry', 'regenerate'):
            assert client.post('/api/v3/materials/' + card['id'] + '/' + action).status_code == 404
        assert client.delete('/api/v3/folders/' + feed['folder_id']).status_code == 200
        restored = add_source(client, 'https://source.test/feed', 'Источник', 'Другая папка')
        assert restored['id'] == feed['id']
        assert client.get('/api/v3/publication/' + card['id']).status_code == 200
        wait_for(client, lambda: any(item['id'] == card['id'] and not item['viewed'] for item in client.get('/api/v3/cards').json()['items']))


def test_deleting_feed_during_discovery_keeps_late_publications(tmp_path):
    import asyncio
    import httpx
    from threading import Event
    from tests.support.reader import wait_for
    from tests.support.rss import rss
    from tests.support.subscriptions import add_source

    started, release = Event(), Event()
    block = [False]
    async def response(request):
        if request.url.path == '/feed':
            if block[0]:
                block[0] = False
                started.set()
                await asyncio.to_thread(release.wait, 30)
            return httpx.Response(200, text=rss())
        return httpx.Response(200, text='<article>Late saved article</article>')

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            presence(client, 'a')
            checked = client.post('/api/v3/feeds/verify', json={'url': 'https://source.test/feed'}).json()
            block[0] = True
            added = client.post('/api/v3/feeds', json={'url': checked['candidate']['url'], 'verification': checked['verification'],
                'title': 'Source', 'folder': 'Folder', 'initial_history': {'mode': 'count', 'count': '1'}})
            assert added.status_code == 200, added.text
            identity = added.json()['id']
            assert started.wait(5)
            assert client.delete('/api/v3/feeds/' + identity).status_code == 200
            hidden_status = client.get('/api/v3/status/details').json()
            assert all(work.get('feed_id') != identity for work in hidden_status['work'])
            restored = add_source(client, 'https://source.test/feed', 'Source', 'Folder', count=1)
            assert restored['id'] == identity
            release.set()
            card = wait_for(client, lambda: next(iter(client.get('/api/v3/cards').json()['items']), None))
            value = wait_for(client, lambda: (data if 'Late saved article' in str((data := client.get('/api/v3/publication/' + card['id']).json())) else None))
            assert 'Late saved article' in str(value)
            wait_for(client, lambda: next((feed for feed in client.get('/api/v3/subscriptions').json()['feeds'] if feed['id'] == identity and feed['history']['state'] == 'complete'), None))
        finally:
            release.set()


@pytest.mark.parametrize('restore', [False, True])
def test_deleted_feed_keeps_retry_after_and_resumes_started_discovery(tmp_path, restore):
    import httpx
    import time
    from datetime import datetime
    from tests.support.reader import Clock, wait_for
    from tests.support.rss import rss

    clock = Clock()
    clock.now = time.time()
    calls, limited_calls, limited = [], [], [False]
    def response(request):
        if request.url.path == '/feed':
            calls.append(clock.now)
            if limited[0]:
                limited_calls.append(clock.now)
            return httpx.Response(429, headers={'Retry-After': '120'}) if limited[0] else httpx.Response(200, text=rss())
        return httpx.Response(200, text='<article>Article</article>')

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response), **clock.options())
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        presence(client, 'a')
        verified = client.post('/api/v3/feeds/verify', json={'url':'https://source.test/feed'}).json()
        body = {'url':verified['candidate']['url'], 'verification':verified['verification'], 'title':'Source', 'folder':'Folder', 'initial_history':{'mode':'count','count':'1'}}
        limited[0] = True
        feed = client.post('/api/v3/feeds', json=body).json()['id']
        waiting = wait_for(client, lambda: next((work for work in client.get('/api/v3/status/details').json()['work'] if limited_calls and work['phase']=='waiting' and work['retry_at']), None))
        deadline = datetime.fromisoformat(waiting['retry_at'].replace('Z', '+00:00')).timestamp()
        assert deadline >= clock.now + 120
        before = len(calls)
        assert client.delete('/api/v3/feeds/' + feed).status_code == 200
        if restore:
            result = client.post('/api/v3/feeds', json=body)
            assert result.status_code == 200 and result.json()['id'] == feed
        clock.now = deadline - 1
        time.sleep(.15)
        assert len(calls) == before
        clock.now = deadline + 1
        wait_for(client, lambda: len(calls) > before)


def test_restoring_feed_preserves_terminal_discovery_failure(tmp_path):
    import httpx
    from tests.support.reader import wait_for
    from tests.support.rss import rss

    denied = [False]
    def response(request):
        return httpx.Response(401) if denied[0] else httpx.Response(200, text=rss())

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        presence(client, 'a')
        verified = client.post('/api/v3/feeds/verify', json={'url':'https://source.test/feed'}).json()
        body = {'url':verified['candidate']['url'], 'verification':verified['verification'], 'title':'Source', 'folder':'Folder', 'initial_history':{'mode':'count','count':'1'}}
        denied[0] = True
        feed = client.post('/api/v3/feeds', json=body).json()['id']
        def failure():
            return next((source for source in client.get('/api/v3/status/details').json()['sources'] if source['feed_id']==feed and source['state']=='unavailable'), None)
        first = wait_for(client, failure)
        assert client.delete('/api/v3/feeds/' + feed).status_code == 200
        restored = client.post('/api/v3/feeds', json=body)
        assert restored.status_code == 200 and restored.json()['id'] == feed
        after = wait_for(client, failure)
        assert after['message'] == first['message']


def test_readding_deleted_telegram_channel_restores_identity(tmp_path):
    import httpx
    from tests.support.telegram_web import page

    def response(request):
        return httpx.Response(200, text=page([(11, 'Saved post')]))

    app = create_app(tmp_path, token='test-token', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        presence(client, 'a')
        body = {'source_locator': '@fixture', 'folder': 'Inbox'}
        first = client.post('/api/v3/telegram/connect', json=body)
        assert first.status_code == 200, first.text
        identity = first.json()['feed_id']
        assert client.delete('/api/v3/feeds/' + identity).status_code == 200
        restored = client.post('/api/v3/telegram/connect', json={**body, 'folder': 'New folder'})
        assert restored.status_code == 200, restored.text
        assert restored.json()['feed_id'] == identity


def test_viewed_publication_keeps_downloaded_media_accessible(tmp_path):
    import httpx
    from tests.support.telegram import configure_fast_web, settled
    from tests.support.telegram_web import page

    photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'

    def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200, content=b'synthetic-image', headers={'content-type': 'image/jpeg'})
        return httpx.Response(200, text=page([(11, 'Photo' + photo)]))

    app = create_app(tmp_path, token='test-token', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        configure_fast_web(client)
        assert client.post('/api/v3/telegram/connect', json={'source_locator': '@fixture', 'folder': 'Inbox'}).status_code == 200
        card = settled(client)[0]
        publication = client.get('/api/v3/publication/' + card['id']).json()
        url = next(media['url'] for media in publication['media'] if media['role'] == 'attachment')
        assert client.get(url).content == b'synthetic-image'
        assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
        assert client.get(url).content == b'synthetic-image'


def test_media_download_started_before_viewing_completes(tmp_path):
    import asyncio
    import httpx
    from threading import Event
    from tests.support.reader import wait_for
    from tests.support.telegram import configure_fast_web
    from tests.support.telegram_web import page

    started, release = Event(), Event()
    photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'

    async def response(request):
        if request.url.host == 'cdn.example':
            started.set()
            await asyncio.to_thread(release.wait, 30)
            return httpx.Response(200, content=b'delayed-image', headers={'content-type': 'image/jpeg'})
        return httpx.Response(200, text=page([(11, 'Photo' + photo)]))

    app = create_app(tmp_path, token='test-token', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            configure_fast_web(client)
            assert client.post('/api/v3/telegram/connect', json={'source_locator': '@fixture', 'folder': 'Inbox'}).status_code == 200
            assert started.wait(10)
            card = wait_for(client, lambda: next(iter(client.get('/api/v3/cards').json()['items']), None))
            assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
            release.set()
            def saved_media():
                result = client.get('/api/v3/publication/' + card['id']).json()
                return next((media['url'] for media in result['media'] if media.get('url')), None)
            media_url = wait_for(client, saved_media)
            assert client.get(media_url).content == b'delayed-image'
        finally:
            release.set()


def test_deleted_feed_finishes_media_after_its_folder_is_removed(tmp_path):
    import asyncio
    import httpx
    from threading import Event
    from tests.support.reader import wait_for
    from tests.support.telegram import configure_fast_web
    from tests.support.telegram_web import page

    started, release = Event(), Event()
    photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
    async def response(request):
        if request.url.host == 'cdn.example':
            started.set()
            await asyncio.to_thread(release.wait, 30)
            return httpx.Response(200, content=b'preserved-image', headers={'content-type': 'image/jpeg'})
        return httpx.Response(200, text=page([(11, 'Photo' + photo)]))

    app = create_app(tmp_path, token='test-token', api_only=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            configure_fast_web(client)
            added = client.post('/api/v3/telegram/connect', json={'source_locator': '@fixture', 'folder': 'Inbox'})
            assert added.status_code == 200
            feed = added.json()['feed_id']
            assert started.wait(10)
            item = wait_for(client, lambda: next(iter(client.get('/api/v3/cards').json()['items']), None))['id']
            folder = client.get('/api/v3/subscriptions').json()['feeds'][0]['folder_id']
            assert client.delete('/api/v3/feeds/' + feed).status_code == 200
            assert client.delete('/api/v3/folders/' + folder).status_code == 200
            release.set()
            restored = client.post('/api/v3/telegram/connect', json={'source_locator': '@fixture', 'folder': 'Other'})
            assert restored.status_code == 200 and restored.json()['feed_id'] == feed
            def saved_media():
                result = client.get('/api/v3/publication/' + item).json()
                return next((media['url'] for media in result['media'] if media.get('url')), None)
            assert client.get(wait_for(client, saved_media)).content == b'preserved-image'
        finally:
            release.set()


def test_started_theses_are_saved_after_viewing(tmp_path):
    import asyncio
    import json
    from threading import Event
    from tests.support.codex import CodexService, configure_codex
    from tests.support.reader import wait_for

    started, release = Event(), Event()
    items = []

    class HeldCodex(CodexService):
        async def complete(self, prompt, schema, directory, choice):
            source = json.loads(prompt.split('\n', 1)[1])['sources'][0]
            items.append(source['id'])
            started.set()
            await asyncio.to_thread(release.wait, 30)
            return json.dumps({'points': [{'text': 'Synthetic result', 'sources': [{'id': source['id'], 'url': source['url']}]}]})

    with TestClient(create_app(tmp_path, source=SyntheticSource(), token='test-token', codex_client=HeldCodex()),
                    headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            presence(client, 'a')
            wait_cards(client, 3)
            configure_codex(client)
            assert client.patch('/api/v3/feeds/fixture', json={'public_ai': True}).status_code == 200
            assert client.patch('/api/v3/settings/oko/global', json={'theses': True}).status_code == 200
            assert started.wait(5)
            item = items[0]
            assert client.post('/api/v3/cards/read', json={'ids': [item]}).status_code == 204
            release.set()
            result = wait_for(client, lambda: client.get('/api/v3/publication/' + item).json()['theses']['result'])
            assert result['points'][0] == 'Synthetic result'
        finally:
            release.set()


def test_viewed_reload_preserves_stale_theses_without_automatic_reprocessing(tmp_path):
    import httpx
    from tests.support.codex import CodexService, configure_codex
    from tests.support.reader import wait_for
    from tests.support.rss import import_feed, rss, wait_cards as wait_rss_cards

    text = ['Original synthetic text']
    def response(request):
        return httpx.Response(200, text=rss() if request.url.path == '/feed' else '<article>' + text[0] + '</article>')

    model = CodexService()
    app = create_app(tmp_path, token='test-token', rss_enabled=True, codex_client=model, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        feed = import_feed(client)
        presence(client, 'a')
        card = wait_rss_cards(client)[0]
        url = '/api/v3/publication/' + card['id']
        configure_codex(client)
        assert client.patch('/api/v3/feeds/' + feed['id'], json={'public_ai': True}).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'theses': True}).status_code == 200
        result = wait_for(client, lambda: client.get(url).json()['theses']['result'])
        calls = len(model.inputs)
        assert client.post('/api/v3/cards/read', json={'ids': [card['id']]}).status_code == 204
        text[0] = 'Updated synthetic text'
        assert client.post('/api/v3/materials/' + card['id'] + '/retry').status_code == 200
        updated = wait_for(client, lambda: (value if 'Updated synthetic text' in (value := client.get(url).json())['text'] else None))
        assert updated['theses']['result']['points'] == result['points']
        assert updated['theses']['stale'] is True
        assert len(model.inputs) == calls
        assert client.post('/api/v3/materials/' + card['id'] + '/regenerate').status_code == 200
        regenerated = wait_for(client, lambda: (value if (value := client.get(url).json())['theses']['result'] and not value['theses']['stale'] else None))
        assert regenerated['theses']['result'] is not None
        assert len(model.inputs) == calls + 1


def test_viewed_hidden_publication_keeps_stale_filter_until_refiltered(tmp_path):
    import asyncio
    import httpx
    from threading import Event
    from tests.support.filters import transport
    from tests.support.reader import wait_for
    from tests.support.rss import import_feed, rss, wait_cards as wait_rss_cards

    excerpt = ['Реклама: original']
    calls = []
    holding, started, release = [False], Event(), Event()
    def response(request):
        return httpx.Response(200, text=rss().replace('Анонс статьи', excerpt[0]) if request.url.path == '/feed' else '<article>Body text</article>')

    model = transport(calls)
    async def model_response(request):
        if holding[0] and request.url.path == '/v1/chat/completions':
            started.set()
            await asyncio.to_thread(release.wait, 30)
        return model.handler(request)

    app = create_app(tmp_path, token='test-token', rss_enabled=True,
                     rss_transport=httpx.MockTransport(response), ai_transport=httpx.MockTransport(model_response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        import_feed(client)
        presence(client, 'a')
        item = wait_rss_cards(client)[0]['id']
        assert client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True}).status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'filter_enabled': True, 'prompt_filters': 'Filter advertisements'}).status_code == 200
        url = '/api/v3/publication/' + item
        wait_for(client, lambda: (value if (value := client.get(url).json())['filter']['result'] and value['filter']['result']['hidden'] else None))
        assert client.post('/api/v3/cards/read', json={'ids': [item]}).status_code == 204
        excerpt[0] = 'Revised content'
        assert client.post('/api/v3/collect').status_code == 200
        changed = wait_for(client, lambda: (value if 'Revised content' in (value := client.get(url).json())['text'] else None))
        assert changed['filter']['result']['hidden'] is True
        assert changed['filter']['stale'] is True
        assert client.get('/api/v3/cards?show_read=true').json()['items'] == []
        assert [card['id'] for card in client.get('/api/v3/cards?show_read=true&show_hidden=true').json()['items']] == [item]
        holding[0] = True
        try:
            assert client.post('/api/v3/cards/unread', json={'ids': [item]}).status_code == 204
            assert started.wait(5)
            assert client.get(url).json()['filter']['result']['hidden'] is True
            assert client.get('/api/v3/cards').json()['items'] == []
        finally:
            release.set()
        refiltered = wait_for(client, lambda: (value if (value := client.get(url).json())['filter']['result'] and not value['filter']['result']['hidden'] and not value['filter']['stale'] else None))
        assert refiltered['filter']['state'] == 'ready'


def test_same_publication_in_another_feed_is_reported_as_conflict(tmp_path):
    import httpx
    from tests.support.reader import wait_for
    from tests.support.subscriptions import add_source
    from tests.support.youtube import CHANNEL, VIDEO, PNG, feed_xml

    def response(request):
        if request.url.path == '/feeds/videos.xml':
            return httpx.Response(200, text=feed_xml())
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})

    app = create_app(tmp_path, token='test-token', rss_enabled=True, rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        presence(client, 'a')
        first = add_source(client, 'https://www.youtube.com/feeds/videos.xml?channel_id=' + CHANNEL, 'First', 'Folder', count=1)
        original = wait_for(client, lambda: next(iter(client.get('/api/v3/cards').json()['items']), None))
        second = add_source(client, 'https://www.youtube.com/feeds/videos.xml?channel_id=UC' + 'b'*22, 'Second', 'Folder', count=1)
        status = wait_for(client, lambda: (value if VIDEO in (value := str(client.get('/api/v3/status/details').json())) and first['id'] in value and second['id'] in value else None))
        assert original['id'] in status
        assert [card['id'] for card in client.get('/api/v3/cards').json()['items']] == [original['id']]


def test_unviewing_keeps_stale_theses_until_new_result_is_accepted(tmp_path):
    import asyncio
    import httpx
    from threading import Event
    from tests.support.codex import CodexService, configure_codex
    from tests.support.reader import wait_for
    from tests.support.rss import import_feed, rss, wait_cards as wait_rss_cards

    hold, started, release = [False], Event(), Event()
    class Model(CodexService):
        async def complete(self, prompt, schema, directory, choice):
            if hold[0]:
                started.set()
                await asyncio.to_thread(release.wait, 30)
            return await super().complete(prompt, schema, directory, choice)

    excerpt = ['Original excerpt']
    def response(request):
        return httpx.Response(200, text=rss().replace('Анонс статьи', excerpt[0]) if request.url.path == '/feed' else '<article>Body text</article>')

    app = create_app(tmp_path, token='test-token', rss_enabled=True, codex_client=Model(), rss_transport=httpx.MockTransport(response))
    with TestClient(app, headers={'Authorization': 'Bearer test-token'}) as client:
        try:
            feed = import_feed(client)
            presence(client, 'a')
            item = wait_rss_cards(client)[0]['id']
            configure_codex(client)
            assert client.patch('/api/v3/feeds/' + feed['id'], json={'public_ai': True}).status_code == 200
            assert client.patch('/api/v3/settings/oko/global', json={'theses': True}).status_code == 200
            url = '/api/v3/publication/' + item
            previous = wait_for(client, lambda: client.get(url).json()['theses']['result'])
            assert client.post('/api/v3/cards/read', json={'ids':[item]}).status_code == 204
            excerpt[0] = 'Updated excerpt'
            assert client.post('/api/v3/collect').status_code == 200
            wait_for(client, lambda: client.get(url).json()['theses']['stale'])
            hold[0] = True
            assert client.post('/api/v3/cards/unread', json={'ids':[item]}).status_code == 204
            assert started.wait(5)
            current = client.get(url).json()['theses']
            assert current['stale'] is True and current['result'] == previous
            release.set()
            wait_for(client, lambda: not client.get(url).json()['theses']['stale'])
        finally:
            release.set()
