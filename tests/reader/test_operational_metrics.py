from tests.support.cards import read_all_cards
"""Operational credentials cannot become reader credentials or expose inputs."""
import json

import pytest
import httpx
import time
from fastapi.testclient import TestClient

from oko.observability import event
from tests.support.app import create_app

TOKEN='synthetic-metrics-credential-00000000000000'


def test_metrics_have_a_separate_read_only_authorization_boundary(tmp_path):
    app=create_app(tmp_path,api_only=True,public_url='https://oko.observer',token='service',metrics_token=TOKEN)
    with TestClient(app,base_url='https://oko.observer',client=('198.51.100.9',5000)) as client:
        assert client.get('/metrics').status_code==403
        assert client.get('/metrics',headers={'Authorization':'Bearer service'}).status_code==403
        headers={'Authorization':'Bearer '+TOKEN}
        response=client.get('/metrics',headers=headers)
        assert response.status_code==200 and 'text/plain' in response.headers['content-type']
        assert 'oko_store_available 1.0' in response.text
        assert TOKEN not in response.text
        assert client.get('/metrics',headers=dict(headers,Host='reader:8765')).status_code==200
        assert client.get('/metrics',headers=dict(headers,Host='evil.example')).status_code==403
        assert client.get('/metrics',headers=dict(headers,Origin='https://evil.example')).status_code==403
        assert client.get('/api/v3/status',headers=headers).status_code==403
        assert client.put('/api/v3/power',json={'enabled':False},headers=dict(headers,Origin='https://oko.observer')).status_code==403
        assert client.get('/api/v3/status',headers=dict(headers,Host='reader:8765')).status_code==403
        assert client.post('/metrics',headers=headers).status_code==405


def test_request_labels_use_templates_not_material_ids_or_query_values(tmp_path):
    app=create_app(tmp_path,api_only=True,token='service',metrics_token=TOKEN)
    with TestClient(app) as client:
        response=client.get('/api/v3/publication/private-publication-identifier',headers={'Authorization':'Bearer service'})
        assert response.status_code in (404,422)
        client.get('/unmatched-secret-url?private_key=never-export-this')
        text=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN}).text
        assert '/api/v3/publication/{item}' in text
        assert 'private-publication-identifier' not in text
        assert 'never-export-this' not in text and 'unmatched-secret-url' not in text


def test_real_collection_attempts_export_timings_without_source_contents(tmp_path,capsys):
    canary='PRIVATE-ARTICLE-CONTENT-CANARY'
    feed=('<rss version="2.0"><channel><title>Private title</title><item><guid>one</guid>'
          '<link>https://source.example/post</link><title>Private title</title><description>'+canary+'</description>'
          '</item></channel></rss>')
    def response(request):
        return httpx.Response(200,text=feed if request.url.path=='/feed' else '<article>'+canary+'</article>',
                              headers={'content-type':'application/rss+xml' if request.url.path=='/feed' else 'text/html'})
    app=create_app(tmp_path,api_only=True,token='service',metrics_token=TOKEN,rss_enabled=True,rss_transport=httpx.MockTransport(response))
    with TestClient(app,headers={'Authorization':'Bearer service'}) as client:
        assert client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''}).status_code==200
        assert client.put('/api/v3/power',json={'enabled':True}).status_code==200
        checked=client.post('/api/v3/feeds/verify',json={'url':'https://source.example/feed'}).json()
        assert checked['state']=='new'
        added=client.post('/api/v3/feeds',json={'url':checked['candidate']['url'],'verification':checked['verification'],'title':'Private title','folder':'Fixture','initial_history':{'mode':'count','count':'1'}})
        assert added.status_code==200
        deadline=time.monotonic()+5
        while True:
            cards=read_all_cards(client, '/api/v3/cards')
            if cards and client.get('/api/v3/publication/'+cards[0]['id']).json()['content'].get('blocks') is not None:break
            assert time.monotonic()<deadline
            time.sleep(.02)
        text=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN}).text
        assert 'oko_work_attempt_duration_seconds_count{kind="discover"}' in text
        assert 'oko_work_attempts_total{kind="discover",outcome="returned"}' in text
        assert canary not in text and 'Private title' not in text
    captured=capsys.readouterr()
    assert canary not in captured.err and 'Private title' not in captured.err
    events=[json.loads(line) for line in captured.err.splitlines() if line.startswith('{')]
    assert any(e['event']=='job_started' and e.get('job_id') for e in events)
    assert any(e['event']=='job_finished' and 'elapsed_seconds' in e for e in events)
