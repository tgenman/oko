from tests.support.subscriptions import add_sources
import time
import pytest
import httpx
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS
from tests.support.youtube import CHANNEL, PNG


def video(n):
    return f'v{n:010d}'


class HistoryFixture:
    def __init__(self):
        self.calls = []

    async def fetch(self, channel, count, state):
        self.calls.append((channel, count))
        return dict(entries=[dict(id=video(n), title=f'Video {n}') for n in range(60, max(0, 60-count), -1)],
                    complete=True, state={}, error=None, requests=3)


def reader(root, history, requests, rss=None):
    def reply(request):
        requests.append(str(request.url))
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        return httpx.Response(200, text=rss() if rss else '<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title></feed>')
    return TestClient(create_app(root, token='test', rss_enabled=True, lease=60,
                                rss_transport=httpx.MockTransport(reply), youtube_history=history), headers=HEADERS)


def wait_history(client, feed, predicate):
    for _ in range(300):
        value = client.get('/api/settings/feed/' + feed).json()['feed']['initial_history']
        if value and predicate(value):
            return value
        time.sleep(.02)
    raise AssertionError(value)


def import_channel(client):
    feed = add_sources(client, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}', 'Channel', 'Без папки')]).json()['feeds'][0]['id']
    client.delete('/api/v3/feeds/'+feed+'/history')
    # Do not request captions in this enumeration/material-history scenario.
    client.patch('/api/settings/feed/' + feed, json={'theses': False})
    return feed


def test_history_materials_survive_restart_without_per_video_list_requests(tmp_path):
    # Six videos exceed the four-request budget, so per-video listing fails.
    # This complete-page fixture does not exercise playlist pagination.
    from tests.support.subscriptions import saved_sources
    # Connection/date-history are covered separately; start with a saved feed so
    # their concurrent RSS requests cannot contaminate the per-video budget.
    saved_sources(tmp_path, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}', 'Channel', 'Без папки')])
    history, requests = HistoryFixture(), []
    with reader(tmp_path, history, requests) as client:
        feed = client.get('/api/subscriptions').json()['feeds'][0]['id']
        client.patch('/api/settings/feed/' + feed, json={'theses': False})
        client.post('/api/tabs/history/heartbeat')
        response = client.post('/api/feeds/' + feed + '/history', json={'mode': 'count', 'count': 6})
        assert response.status_code == 200, response.text
        progress = wait_history(client, feed, lambda h: h['state'] == 'done')
        assert progress['loaded'] == 6 and progress['available'] == 6
        cards = client.get('/api/cards', params={'feed': feed}).json()
        assert len(cards) == 6
        assert [c['title'] for c in cards[:3]] == ['Video 60', 'Video 59', 'Video 58']
        assert all(not c['published_known'] for c in cards)
        assert len(history.calls) == 1
        assert len([u for u in requests if '/feeds/videos.xml' in u]) <= 4  # count-history must not list once per video
        viewed = cards[0]['id']
        assert client.post('/api/materials/' + viewed + '/viewed').status_code == 200
    with reader(tmp_path, history, requests) as client:
        client.post('/api/tabs/history/heartbeat')
        time.sleep(.1)
        assert len(history.calls) == 1
        assert len(client.get('/api/cards', params={'feed': feed}).json()) == 5


def test_partial_error_requires_manual_resume_and_rss_remains_independent(tmp_path):
    class Partial(HistoryFixture):
        async def fetch(self, channel, count, state):
            result = await super().fetch(channel, count, state)
            if len(self.calls) == 1:
                result.update(entries=result['entries'][:3], complete=False, state={'checkpoint': 3}, error='YouTube unavailable')
            else:
                assert state == {'checkpoint': 3}
                for entry in result['entries'][:3]:
                    entry.pop('title')  # Persisted cursor keeps IDs, not content copies.
            return result
    history, requests = Partial(), []
    xml = ['<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title></feed>']
    with reader(tmp_path, history, requests, lambda: xml[0]) as client:
        feed = import_channel(client)
        client.post('/api/tabs/history/heartbeat')
        assert client.post('/api/feeds/' + feed + '/history', json={'count': 8}).status_code == 200
        progress = wait_history(client, feed, lambda h: h['state'] == 'waiting')
        assert progress['available'] == 3 and progress['loaded'] == 0
        assert not progress['scan_complete']
        assert progress['discovery_error'] == 'YouTube unavailable'
        assert client.post('/api/feeds/' + feed + '/history', json={'count': 9}).status_code == 409
        xml[0] = f'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title>
        <entry><id>yt:video:{video(99)}</id><title>New arrival</title><link href="https://www.youtube.com/watch?v={video(99)}"/>
        <published>2026-10-13T12:00:00Z</published></entry></feed>'''
        client.post('/api/collect')
        for _ in range(100):
            cards = client.get('/api/cards').json()
            arrived = next((c for c in cards if c['title'] == 'New arrival'), None)
            if arrived and not arrived['loading']:
                break
            time.sleep(.02)
        assert arrived and not arrived['loading']
        assert len(history.calls) == 1
    with reader(tmp_path, history, requests, lambda: xml[0]) as client:
        client.post('/api/tabs/history/heartbeat')
        time.sleep(.15)
        assert len(history.calls) == 1
        assert client.patch('/api/feeds/' + feed + '/history', json={'paused': False}).status_code == 200
        progress = wait_history(client, feed, lambda h: h['state'] == 'done')
        assert progress['loaded'] == 8 and progress['available'] == 8
        assert len(history.calls) == 2
        assert len(client.get('/api/cards').json()) == 9
        assert {c['title'] for c in client.get('/api/cards').json()} >= {'Video 60','Video 59','Video 58'}


def test_stop_then_new_selection_retains_viewed_tombstones_and_saved_materials(tmp_path):
    history, requests = HistoryFixture(), []
    with reader(tmp_path, history, requests) as client:
        feed = import_channel(client)
        client.post('/api/tabs/history/heartbeat')
        assert client.post('/api/feeds/' + feed + '/history', json={'count': 4}).status_code == 200
        wait_history(client, feed, lambda h: h['state'] == 'done')
        cards = client.get('/api/cards').json()
        client.post('/api/materials/' + cards[0]['id'] + '/viewed')
        assert client.delete('/api/feeds/' + feed + '/history').json()['state'] == 'cancelled'
        assert client.patch('/api/feeds/' + feed + '/history', json={'paused': False}).status_code == 400
        assert client.post('/api/feeds/' + feed + '/history', json={'count': 2}).status_code == 200
        progress = wait_history(client, feed, lambda h: h['state'] == 'done')
        assert progress['available'] == 2 and progress['viewed'] == 1
        assert len(client.get('/api/cards').json()) == 3
        assert len(history.calls) == 2


def test_recovered_rss_window_does_not_import_old_entries_outside_n(tmp_path):
    from datetime import datetime, timezone
    recovered = False
    history = HistoryFixture()
    new_date = datetime.now(timezone.utc).timestamp() + 60
    def entry(n, stamp):
        date = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        return f'<entry><id>yt:video:{video(n)}</id><title>Video {n}</title><link href="https://www.youtube.com/watch?v={video(n)}"/><published>{date}</published></entry>'
    def reply(request):
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type':'image/png'})
        streams = request.url.params.get('playlist_id', '').startswith('UULV')
        if streams and not recovered:
            return httpx.Response(503)
        items = entry(40, 1735689600) + entry(99, new_date) if streams else entry(60, 1735689600)
        return httpx.Response(200, text=f'<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title>{items}</feed>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, lease=60,
                              rss_transport=httpx.MockTransport(reply), youtube_history=history), headers=HEADERS) as client:
        feed = import_channel(client)
        client.post('/api/tabs/history/heartbeat')
        client.post('/api/feeds/' + feed + '/history', json={'count':1})
        wait_history(client, feed, lambda h:h['state']=='done')
        recovered = True
        client.post('/api/collect')
        time.sleep(.4)
        titles = {c['title'] for c in client.get('/api/cards').json()}
        assert titles == {'Video 60','Video 99'}


def test_smaller_history_after_new_upload_preserves_retained_order(tmp_path):
    from tests.support.youtube import History
    history = History([video(60), video(59), video(58)])
    with reader(tmp_path, history, []) as client:
        feed = import_channel(client)
        client.post('/api/tabs/history/heartbeat')
        client.post('/api/feeds/' + feed + '/history', json={'count': 3})
        wait_history(client, feed, lambda h: h['state'] == 'done')
        history.videos = (video(61), video(60), video(59), video(58))
        client.post('/api/feeds/' + feed + '/history', json={'count': 2})
        wait_history(client, feed, lambda h: h['state'] == 'done' and h['count'] == 2)
        for sort, numbers in [('newest', [61, 60, 59, 58]), ('oldest', [58, 59, 60, 61])]:
            cards = client.get('/api/cards', params={'feed': feed, 'sort': sort}).json()
            assert [c['url'].split('=')[-1] for c in cards] == [video(n) for n in numbers]


@pytest.mark.parametrize('all_failed', [False, True])
def test_date_boundary_survives_failed_rss_windows(tmp_path, all_failed):
    clock = [0.]
    recovered = False
    requests = []
    def reply(request):
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        if 'channel_id' in request.url.params:
            return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title></feed>')
        playlist = request.url.params.get('playlist_id', '')
        requests.append(playlist)
        if not recovered and (all_failed or playlist.startswith('UULV')):
            return httpx.Response(503)
        items = ''.join(f'<entry><id>yt:video:{video(n)}</id><title>Video {n}</title><link href="https://www.youtube.com/watch?v={video(n)}"/><published>{date}</published></entry>'
                        for n, date in ([(1, '2020-01-01T00:00:00Z'), (2, '2026-02-01T00:00:00Z')] if playlist.startswith('UULV') else [(2, '2026-02-01T00:00:00Z')]))
        return httpx.Response(200, text=f'<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title>{items}</feed>')
    with TestClient(create_app(tmp_path, token='test', rss_enabled=True, lease=600, clock=lambda: clock[0],
                              rss_transport=httpx.MockTransport(reply)), headers=HEADERS) as client:
        client.patch('/api/settings/oko/global', json={'theses': False})
        client.post('/api/tabs/history/heartbeat')
        url = f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}'
        checked = client.post('/api/feeds/verify', json={'url': url})
        assert checked.status_code == 200, checked.text
        result = client.post('/api/feeds', json={'url': url,
            'title':'Channel', 'folder':'Video', 'verification':checked.json()['verification'],
            'initial_history':{'mode':'date','count':None,'date':'2026-01-01','timezone':'UTC'}})
        assert result.status_code == 200, result.text
        client.post('/api/tabs/history/heartbeat')
        for _ in range(100):
            if len(requests) >= 2:
                break
            time.sleep(.02)
        time.sleep(.1)
        recovered = True
        clock[0] += 61
        client.post('/api/tabs/history/heartbeat')
        client.post('/api/collect')
        for _ in range(100):
            cards = client.get('/api/cards').json()
            if len(requests) >= 4 and cards and all(not c['loading'] for c in cards):
                break
            time.sleep(.02)
        assert [c['url'] for c in cards] == [f'https://www.youtube.com/watch?v={video(2)}']
