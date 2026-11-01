import httpx
from tests.support.v3 import TestClient
from tests.support.app import create_app
from tests.support.feed_url import HEADERS, RSS


def test_single_feed_default_and_strict_history(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True, rss_transport=httpx.MockTransport(lambda r: httpx.Response(200, text=RSS))), headers=HEADERS) as client:
        checked = client.post('/api/v3/feeds/verify', json={'url':'https://source.test/feed'}, headers={'X-Oko-Action':'verify-one'})
        assert checked.status_code == 200, checked.text
        value = checked.json()
        assert value['state'] == 'new'
        assert client.get('/api/v3/commands/verify-one').json()['context']=={'operation':'verify_feed'}
        body = dict(url=value['candidate']['url'], title=value['candidate']['title'], verification=value['verification'])
        for history in [None, {}, {'mode':'count'}, {'mode':'count','count':20}, {'mode':'count','count':'0'}, {'mode':'count','count':'01'}, {'mode':'count','count':'-1'}, {'mode':'count','count':'1e3'}, {'mode':'count','count':'20','timezone':'UTC'}, {'mode':'date','date':'2025-02-30','timezone':'UTC'}, {'mode':'date','date':'2025-01-01','timezone':'invalid'}]:
            response = client.post('/api/v3/feeds', json=dict(body, initial_history=history))
            assert response.status_code == 422, response.text
            assert client.get('/api/v3/subscriptions').json()['feeds'] == []
        response = client.post('/api/v3/feeds', json=body, headers={'X-Oko-Action':'add-one'})
        assert response.status_code == 200, response.text
        assert response.json()['history']['selection'] == {'mode':'count','count':'20'}
        assert client.get('/api/v3/commands/add-one').json()['outcome'] == 'succeeded'
        assert client.post('/api/v3/feeds/verify', json={'url':body['url']}).json()['state'] == 'existing'


def test_youtube_credentials_and_errors_do_not_expose_secrets(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        endpoint='/api/v3/youtube'
        response=client.patch(endpoint,json={'mode':'proxy','address':'http://proxy.test:8080','credentials':{'action':'replace','username':'secret-user','password':'secret-password'}})
        assert response.status_code==200, response.text
        assert response.json()['has_password'] and response.json()['has_username']
        for value in [{},{'credentials':{'action':'keep'}}]:
            assert client.patch(endpoint,json=value).json()['has_password']
        for value in [None,{}, {'action':'replace','username':'secret-user'}, {'action':'clear','password':'secret-password'}]:
            result=client.patch(endpoint,json={'credentials':value})
            assert result.status_code==422, result.text
            assert 'secret-' not in result.text
        response=client.patch(endpoint,json={'address':'http://secret-user:secret-password@proxy.test'})
        assert response.status_code==400
        assert 'secret-' not in response.text
        assert client.patch(endpoint,json={'credentials':{'action':'clear'}}).json()['has_password'] is False
        assert client.get(endpoint).json()['has_username'] is False
        assert 'secret-' not in client.get('/api/v3/diagnostics').text


def test_connection_errors_and_history_conflicts_have_canonical_receipts(tmp_path):
    from tests.support.subscriptions import configured_sources
    configured_sources(tmp_path, [('https://source.test/old','Old','Saved')],history=False)
    with TestClient(create_app(tmp_path, token='test', api_only=True), headers=HEADERS) as client:
        feed=client.get('/api/v3/subscriptions').json()['feeds'][0]
        assert feed['history']=={'state':'absent'}
        for method,body in [('patch',{'paused':True}),('delete',None)]:
            response=client.request(method,'/api/v3/feeds/'+feed['id']+'/history',**({'json':body} if body else {}),headers={'X-Oko-Action':method+'-history'})
            assert response.status_code==409, response.text
            assert response.json()['code']=='history_conflict'
            assert response.json()['details']['history']==feed['history']
            assert client.get('/api/v3/commands/'+method+'-history').json()['outcome']=='failed'
            assert client.request(method,'/api/v3/feeds/missing/history',**({'json':body} if body else {})).status_code==404
        assert client.post('/api/v3/feeds/missing/history',json={'mode':'count','count':'20'}).status_code==404
        started=client.post('/api/v3/feeds/'+feed['id']+'/history',json={'mode':'count','count':'20'},headers={'X-Oko-Action':'post-history'})
        assert started.status_code==200, started.text
        assert client.get('/api/v3/commands/post-history').json()['outcome']=='succeeded'
        result=client.post('/api/v3/feeds',json={'url':'https://source.test/new','title':'New','verification':'expired'})
        assert result.status_code==409 and result.json()['code']=='verification_expired'
        assert client.post('/api/v3/feeds/verify',json={'url':'https://t.me/example_channel'}).status_code==400
        assert client.post('/api/v3/feeds/verify',json={'url':'https://offline.test/feed'}).status_code==503
        for path in ['/api/v3/import','/api/v3/import/preview']:
            assert client.post(path,json={'opml':'<opml><body/></opml>'}).status_code in (404,405)
