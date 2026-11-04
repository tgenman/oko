"""Telegram HTTP budget is a durable Oko setting, separate from forced waits."""
import time

import httpx

from tests.support.demo_source import DemoSource
from tests.support.app import create_app
from tests.support.telegram_web import page
from tests.support.v3 import TestClient

HEADERS = {'Authorization': 'Bearer test'}
READ = '/api/v3/settings/telegram-http'
WRITE = '/api/v3/settings/oko/global'


def test_rate_setting_defaults_validates_scope_persists_and_resets(tmp_path):
    options = dict(token='test', api_only=True, source=DemoSource())
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get(READ).json() == {'value':2, 'origin':{'scope':'default'}}
        for invalid in [0, -.1, 10.1, .09, True, '2', [], {}]:
            assert client.patch(WRITE, json={'telegram_http_rps':invalid}).status_code == 422
        original=client.get(WRITE).json()
        assert client.patch(WRITE, json={'telegram_http_rps':.5}, headers={'X-Oko-Action':'rate-save'}).status_code == 200
        assert client.get(READ).json() == {'value':.5, 'origin':{'scope':'oko','owner':'global'}}
        assert client.get('/api/v3/commands/rate-save').json()['context']['keys'] == []
        assert client.get(WRITE).json()==original
        feed=client.get('/api/v3/subscriptions').json()['feeds'][0]
        for target in ('folder/'+feed['folder_id'], 'feed/'+feed['id']):
            for value in (1,None):
                assert client.patch('/api/v3/settings/'+target,json={'telegram_http_rps':value,'originals':False}).status_code==400
            assert client.get('/api/v3/settings/'+target).json()['overrides']=={}
        assert client.get(READ).json()['value'] == .5
    with TestClient(create_app(tmp_path, **options), headers=HEADERS) as client:
        assert client.get(READ).json()['value'] == .5
        assert client.patch(WRITE, json={'telegram_http_rps':None}).status_code == 200
        assert client.get(READ).json() == {'value':2, 'origin':{'scope':'default'}}


def test_live_setting_changes_spacing_but_cannot_clear_a_429_pause(tmp_path):
    calls, limited = [], [False]
    def response(request):
        calls.append(time.monotonic())
        return httpx.Response(429, headers={'Retry-After':'120'}) if limited[0] else httpx.Response(200,text=page([]))
    with TestClient(create_app(tmp_path,token='test',api_only=True,rss_transport=httpx.MockTransport(response)),headers=HEADERS) as client:
        assert client.patch(WRITE,json={'telegram_http_rps':10}).status_code == 200
        for name in ('firstchannel','secondchannel'):
            assert client.post('/api/v3/telegram/connect',json={'source_locator':'@'+name,'folder':'Inbox'}).status_code == 200
        assert len(calls)>=2
        assert .08 <= calls[1]-calls[0]
        limited[0] = True
        client.post('/api/v3/telegram/connect',json={'source_locator':'@thirdchannel','folder':'Inbox'})
        count=len(calls)
        assert client.patch(WRITE,json={'telegram_http_rps':1}).status_code == 200
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fourthchannel','folder':'Inbox'})
        assert len(calls)==count
        assert client.patch(WRITE,json={'telegram_http_rps':None}).status_code==200
        client.post('/api/v3/telegram/connect',json={'source_locator':'@fifthchannel','folder':'Inbox'})
        assert len(calls)==count
