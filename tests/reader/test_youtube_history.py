from tests.support.cards import read_all_cards
from tests.support.presence import presence
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
        value = client.get('/api/v3/settings/feed/' + feed).json()['feed']['history']
        if value and predicate(value):
            return value
        time.sleep(.02)
    raise AssertionError(value)


def import_channel(client):
    feed = add_sources(client, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}', 'Channel', 'Без папки')]).json()['feeds'][0]['id']
    client.delete('/api/v3/feeds/'+feed+'/history')
    # Do not request captions in this enumeration/material-history scenario.
    client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
    return feed


def test_history_materials_survive_restart_without_per_video_list_requests(tmp_path):
    # Six videos exceed the four-request budget, so per-video listing fails.
    # This complete-page fixture does not exercise playlist pagination.
    from tests.support.subscriptions import configured_sources
    # Connection/date-history are covered separately; start with a saved feed so
    # their concurrent RSS requests cannot contaminate the per-video budget.
    configured_sources(tmp_path, [(f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}', 'Channel', 'Без папки')],history=False)
    history, requests = HistoryFixture(), []
    with reader(tmp_path, history, requests) as client:
        feed = client.get('/api/v3/subscriptions').json()['feeds'][0]['id']
        # Separate the automatic startup refresh from the history request budget.
        from tests.support.reader import wait_for
        wait_for(client, lambda: bool(history.calls) and client.get('/api/v3/status').json()['collection']['state'] == 'idle')
        requests.clear()
        client.patch('/api/v3/settings/feed/' + feed, json={'theses': False})
        response = client.post('/api/v3/feeds/' + feed + '/history', json={'mode': 'count', 'count': '6'})
        assert response.status_code == 200, response.text
        presence(client,'history')
        progress = wait_history(client, feed, lambda h: h['state'] == 'complete')
        assert progress['progress']['loaded'] == 6 and progress['progress']['available'] == 6
        cards = read_all_cards(client, '/api/v3/cards', params={'feed': feed})
        assert len(cards) == 6
        assert {c['title'] for c in cards} == {f'Video {n}' for n in range(55,61)}
        assert [c['id'] for c in cards] == sorted(c['id'] for c in cards)
        assert all(c['published_at'] is None for c in cards)
        assert 2<=len(history.calls)<=3
        history_calls=len(history.calls)
        assert len([u for u in requests if '/feeds/videos.xml' in u]) <= 4  # count-history must not list once per video
        viewed = cards[0]['id']
        assert client.post('/api/v3/materials/' + viewed + '/viewed').status_code == 200
    with reader(tmp_path, history, requests) as client:
        presence(client, 'history')
        time.sleep(.1)
        assert len(history.calls)==history_calls
        assert len(read_all_cards(client, '/api/v3/cards', params={'feed': feed})) == 5


def test_refresh_resumes_partial_history_error_and_collects_new_rss_publications(tmp_path):
    class Partial(HistoryFixture):
        async def fetch(self,channel,count,state):
            result=await super().fetch(channel,count,state)
            if count>=8 and not state.get('checkpoint'):
                result.update(entries=result['entries'][:3],complete=False,state={'checkpoint':3},error='YouTube unavailable')
            return result
    history, requests = Partial(), []
    xml = ['<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title></feed>']
    with reader(tmp_path, history, requests, lambda: xml[0]) as client:
        from tests.support.subscriptions import add_source
        presence(client,'history')
        feed=add_source(client,f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}','Channel','Без папки',count=8)['id']
        client.patch('/api/v3/settings/feed/'+feed,json={'theses':False})
        progress = wait_history(client, feed, lambda h: h['state'] == 'waiting')
        assert progress['progress']['available'] == 0 and progress['progress']['loaded'] == 0
        assert not progress['progress']['scan_complete']
        assert progress['progress']['discovery_problem']['message'] == 'YouTube unavailable'
        assert client.post('/api/v3/feeds/' + feed + '/history', json={'mode':'count','count':'9'}).status_code == 409
        xml[0] = f'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Channel</title>
        <entry><id>yt:video:{video(99)}</id><title>New arrival</title><link href="https://www.youtube.com/watch?v={video(99)}"/>
        <published>2026-10-13T12:00:00Z</published></entry></feed>'''
        client.post('/api/v3/collect')
        wait_history(client,feed,lambda h:h['state']=='complete')
        client.post('/api/v3/collect')  # New arrivals belong to the following serialized window.
        for _ in range(100):
            cards = read_all_cards(client, '/api/v3/cards')
            arrived = next((c for c in cards if c['title'] == 'New arrival'), None)
            if arrived and not arrived['loading']:
                break
            time.sleep(.02)
        assert arrived and not arrived['loading']
        wait_history(client, feed, lambda h: h['state'] == 'complete')
        assert len(history.calls) == 3
    with reader(tmp_path, history, requests, lambda: xml[0]) as client:
        presence(client, 'history')
        time.sleep(.15)
        assert len(history.calls) == 3
        assert client.post('/api/v3/collect',json={'scope':'feed','feed_id':feed}).status_code == 200
        progress = wait_history(client, feed, lambda h: h['state'] == 'complete')
        assert progress['progress']['loaded'] == 8 and progress['progress']['available'] == 8
        assert len(history.calls) == 3
        assert len(read_all_cards(client, '/api/v3/cards')) == 9
        assert {c['title'] for c in read_all_cards(client, '/api/v3/cards')} >= {'Video 60','Video 59','Video 58'}


def test_stop_then_new_selection_can_rediscover_viewed_and_retains_saved_materials(tmp_path):
    history, requests = HistoryFixture(), []
    with reader(tmp_path, history, requests) as client:
        feed = import_channel(client)
        presence(client, 'history')
        assert client.post('/api/v3/feeds/' + feed + '/history', json={'mode':'count','count':'4'}).status_code == 200
        wait_history(client, feed, lambda h: h['state'] == 'complete')
        cards = read_all_cards(client, '/api/v3/cards')
        # The first displayed card is now selected by timestamp and opaque ID;
        # explicitly view a video that the next two-item selection rediscovers.
        viewed = next(card for card in cards if card['source_url'].endswith(video(60)))
        assert client.post('/api/v3/materials/' + viewed['id'] + '/viewed').status_code == 200
        assert len(read_all_cards(client, '/api/v3/cards')) == 3
        assert client.delete('/api/v3/feeds/' + feed + '/history').json()['history']['state'] == 'cancelled'
        assert client.patch('/api/v3/feeds/' + feed + '/history', json={'paused': False}).status_code == 409
        assert client.post('/api/v3/feeds/' + feed + '/history', json={'mode':'count','count':'2'}).status_code == 200
        progress = wait_history(client, feed, lambda h: h['state'] == 'complete')
        assert progress['progress']['available'] == 2 and progress['progress']['viewed'] == 0
        restored=read_all_cards(client, '/api/v3/cards')
        assert len(restored) == 4
        assert {card['source_url'].split('=')[-1] for card in restored} == {video(n) for n in range(57,61)}
        assert [count for _,count in history.calls][-2:]==[1,2]
