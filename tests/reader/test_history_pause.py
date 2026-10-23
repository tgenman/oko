from tests.support.telegram import connected_feed
from tests.support.presence import presence, close_presence
from tests.support.telegram import connect_request
import asyncio
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.feed_url import HEADERS, add, verify


def wait_history(client, feed, predicate):
    for _ in range(200):
        history = client.get('/api/v3/settings/feed/' + feed).json()['feed']['history']
        if predicate(history):
            return history
        time.sleep(.01)
    assert predicate(history), history
    return history


@pytest.mark.parametrize('selection', [{'mode':'count','count':3}])
def test_removed_pause_preserves_history_snapshot_across_restart_and_fetches_new_posts(tmp_path, selection):
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
        wait_history(client, feed, lambda h: h['progress']['loaded'] == 3)
        result = client.patch('/api/v3/feeds/' + feed + '/history', json={'paused':True})
        assert result.status_code == 409, result.text
    blocked = False
    posts.insert(0, 4)
    with reader() as client:
        presence(client, 'reader')
        for _ in range(200):
            cards = client.get('/api/v3/cards').json()
            if any(c['source_url'].endswith('/4') and (c['availability'] == 'available') for c in cards): break
            time.sleep(.01)
        assert any(c['source_url'].endswith('/4') and (c['availability'] == 'available') for c in cards)
        history = wait_history(client, feed, lambda h: h['state'] == 'complete')
        assert client.patch('/api/v3/feeds/' + feed + '/history', json={'paused':False}).status_code == 200
        history = wait_history(client, feed, lambda h: h['state'] == 'complete')
        assert history['progress']['loaded'] == history['progress']['available'] == 3
        assert len(client.get('/api/v3/cards').json()) == 4


def test_telegram_history_resumes_album_snapshot_after_restart_without_manual_pause(tmp_path):
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
        presence(client, 'reader')
        feed = connected_feed(client, connect_request(client, json=dict(account='fixture', peer='@fixture', folder='Telegram', auto_read=False, initial_history=dict(mode='count', count=4))))['id']
        wait_history(client, feed, lambda h: h['progress']['available'] == 4)
        assert client.patch('/api/v3/feeds/' + feed + '/history', json={'paused':True}).status_code == 409
    archive.blocked = False
    archive.rows.append(dict(id=7, date=1759350007, text='new', group=None, media=None))
    with reader() as client:
        presence(client, 'reader')
        for _ in range(200):
            cards = client.get('/api/v3/cards').json()
            if any([int(identity) for identity in c['source']['message_ids']] == [7] and (c['availability'] == 'available') for c in cards): break
            time.sleep(.01)
        assert any([int(identity) for identity in c['source']['message_ids']] == [7] and (c['availability'] == 'available') for c in cards), cards
        assert wait_history(client, feed, lambda h: h['state']=='complete')['progress']['available'] == 4
        assert client.patch('/api/v3/feeds/' + feed + '/history', json={'paused':False}).status_code == 200
        history = wait_history(client, feed, lambda h: h['state'] == 'complete')
        assert history['progress']['available'] == history['progress']['loaded'] == 4
        assert len(client.get('/api/v3/cards').json()) == 5




def test_youtube_history_continues_after_restart_without_changing_selection(tmp_path):
    from tests.support.feed_url import YOUTUBE
    from tests.support.youtube import PNG, History, NoCaptions
    blocked = True
    videos = ['abcdefghijk', 'lmnopqrstuv']
    clock = [100.0]
    async def reply(request):
        if request.url.host == 'i.ytimg.com':
            if blocked and '/lmnopqrstuv/' in request.url.path:
                await asyncio.Event().wait()
            return httpx.Response(200, content=PNG, headers={'content-type':'image/png'})
        return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"><title>Видео</title>' + ''.join(f'<entry><id>yt:video:{v}</id><title>{v}</title><link href="https://www.youtube.com/watch?v={v}"/><published>2025-10-01T12:00:00Z</published></entry>' for v in videos) + '</feed>')
    def reader():
        return TestClient(create_app(tmp_path,token='test',rss_enabled=True,rss_transport=httpx.MockTransport(reply),youtube_history=History(['abcdefghijk','lmnopqrstuv']),youtube_captions=NoCaptions(),clock=lambda:clock[0],wall_clock=lambda:clock[0],lease=60),headers=HEADERS)
    with reader() as client:
        feed=add(client,verify(client,YOUTUBE),initial_history=dict(mode='count',count=2)).json()['id']
        wait_history(client,feed,lambda h:h['progress']['available']==2)
        assert client.patch('/api/v3/feeds/'+feed+'/history',json={'paused':True}).status_code==409
    blocked=False
    clock[0] += 10
    videos.append('wxyzabcdefg')
    with reader() as client:
        presence(client, 'reader')
        for _ in range(200):
            cards=client.get('/api/v3/cards').json()
            if any(c['source_url'].endswith('wxyzabcdefg') and (c['availability'] == 'available') and not c.get('loading') for c in cards):break
            time.sleep(.01)
        assert any(c['source_url'].endswith('wxyzabcdefg') and (c['availability'] == 'available') and not c.get('loading') for c in cards)
        clock[0] += 10
        assert client.patch('/api/v3/feeds/'+feed+'/history',json={'paused':False}).status_code==200
        assert wait_history(client,feed,lambda h:h['state']=='complete')['progress']['loaded']==2
