import asyncio
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from oko.reader.app import create_app
from tests.support.feed_url import HEADERS, add, verify


def wait_history(client, feed, predicate):
    for _ in range(200):
        history = client.get('/api/settings/feed/' + feed).json()['feed']['initial_history']
        if predicate(history):
            return history
        time.sleep(.01)
    assert predicate(history), history
    return history


@pytest.mark.parametrize('selection', [{'mode':'count','count':3}, {'mode':'date','count':None,'date':'2025-10-01'}])
def test_pause_keeps_snapshot_across_restart_but_fetches_new_posts(tmp_path, selection):
    blocked = True
    posts = [3, 2, 1]
    async def reply(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text='<rss><channel><title>История</title>' + ''.join(f'<item><guid>{n}</guid><title>{n}</title><link>https://source.test/post/{n}</link><pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate></item>' for n in posts) + '</channel></rss>')
        if blocked and request.url.path == '/post/2':
            await asyncio.Event().wait()
        return httpx.Response(200, text='<article>Текст</article>')
    def reader():
        return TestClient(create_app(tmp_path, token='test', rss_enabled=True, rss_transport=httpx.MockTransport(reply)), headers=HEADERS)
    with reader() as client:
        feed = add(client, verify(client, 'https://source.test/feed'), initial_history=selection).json()['id']
        wait_history(client, feed, lambda h: h['loaded'] == 1)
        result = client.patch('/api/feeds/' + feed + '/history', json={'paused':True})
        assert result.status_code == 200, result.text
        assert result.json()['state'] == 'paused'
    blocked = False
    posts.insert(0, 4)
    with reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        wait_history(client, feed, lambda h: h['paused'])
        for _ in range(200):
            cards = client.get('/api/cards').json()
            if any(c['url'].endswith('/4') and c['complete'] for c in cards): break
            time.sleep(.01)
        assert any(c['url'].endswith('/4') and c['complete'] for c in cards)
        history = wait_history(client, feed, lambda h: h['loaded'] == 1)
        assert history['state'] == 'paused'
        assert client.patch('/api/feeds/' + feed + '/history', json={'paused':False}).status_code == 200
        history = wait_history(client, feed, lambda h: h['state'] == 'done')
        assert history['loaded'] == history['available'] == 3
        assert len(client.get('/api/cards').json()) == 4


def test_telegram_paused_history_keeps_live_lane_and_resumes_album_snapshot(tmp_path):
    from tests.support.telegram_history import Archive
    class PausedArchive(Archive):
        blocked = True
        async def messages(self, account, peer, ids, **scope):
            if self.blocked and 6 in ids:
                await asyncio.Event().wait()
            return await super().messages(account, peer, ids, **scope)
    archive = PausedArchive()
    def reader():
        return TestClient(create_app(tmp_path, token='test', telegram_client=archive), headers=HEADERS)
    with reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        feed = client.post('/api/telegram/connect', json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=False, initial_history=dict(mode='count', count=4))).json()['id']
        wait_history(client, feed, lambda h: h['available'] == 2)
        assert client.patch('/api/feeds/' + feed + '/history', json={'paused':True}).status_code == 200
    archive.blocked = False
    archive.rows.append(dict(id=7, date=1759350007, text='new', group=None, media=None))
    with reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(200):
            cards = client.get('/api/cards').json()
            if any(c['members'] == [7] and c['complete'] for c in cards): break
            time.sleep(.01)
        assert any(c['members'] == [7] and c['complete'] for c in cards), cards
        assert wait_history(client, feed, lambda h: h['paused'])['available'] == 2
        assert client.patch('/api/feeds/' + feed + '/history', json={'paused':False}).status_code == 200
        history = wait_history(client, feed, lambda h: h['state'] == 'done')
        assert history['available'] == history['loaded'] == 4
        assert len(client.get('/api/cards').json()) == 5


def test_telegram_pause_before_snapshot_can_initialize_live_cursor():
    from tests.support.telegram_history import Archive
    from oko.collection.telegram.source import TelegramSource
    import json
    async def scenario():
        archive = Archive()
        source = TelegramSource(archive, 'fixture', 123, 1)
        source.initial_history = dict(mode='count',count=20,since=None,paused=True)
        items, cursor = await source.discover(None)
        assert not items
        assert json.loads(cursor)['upper'] == 6
        archive.rows.append(dict(id=7,date=1759350007,text='new',group=None,media=None))
        items, cursor = await source.discover(cursor)
        assert [p.members for p in items] == [(7,)]
        source.initial_history['paused'] = False
        items, cursor = await source.discover(cursor)
        assert {p.members for p in items} == {(6,), (4,5)}
    asyncio.run(scenario())


def test_youtube_paused_history_fetches_new_video_without_changing_selection(tmp_path):
    from tests.support.feed_url import YOUTUBE
    from tests.support.youtube import PNG
    blocked = True
    videos = ['abcdefghijk', 'lmnopqrstuv']
    async def reply(request):
        if request.url.host == 'i.ytimg.com':
            if blocked and '/lmnopqrstuv/' in request.url.path:
                await asyncio.Event().wait()
            return httpx.Response(200, content=PNG, headers={'content-type':'image/png'})
        return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"><title>Видео</title>' + ''.join(f'<entry><id>yt:video:{v}</id><title>{v}</title><link href="https://www.youtube.com/watch?v={v}"/><published>2025-10-01T12:00:00Z</published></entry>' for v in videos) + '</feed>')
    def reader():
        return TestClient(create_app(tmp_path,token='test',rss_enabled=True,rss_transport=httpx.MockTransport(reply)),headers=HEADERS)
    with reader() as client:
        feed=add(client,verify(client,YOUTUBE),initial_history=dict(mode='count',count=2)).json()['id']
        wait_history(client,feed,lambda h:h['loaded']==1)
        assert client.patch('/api/feeds/'+feed+'/history',json={'paused':True}).status_code==200
    blocked=False
    videos.append('wxyzabcdefg')
    with reader() as client:
        client.post('/api/tabs/reader/heartbeat')
        for _ in range(200):
            cards=client.get('/api/cards').json()
            if any(c['url'].endswith('wxyzabcdefg') and c['complete'] for c in cards):break
            time.sleep(.01)
        assert any(c['url'].endswith('wxyzabcdefg') and c['complete'] for c in cards)
        assert wait_history(client,feed,lambda h:h['paused'])['loaded']==1
        assert client.patch('/api/feeds/'+feed+'/history',json={'paused':False}).status_code==200
        assert wait_history(client,feed,lambda h:h['state']=='done')['loaded']==2
