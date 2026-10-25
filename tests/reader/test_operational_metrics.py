"""Operational credentials cannot become reader credentials or expose inputs."""
import json
from pathlib import Path

import pytest
import httpx
import time
from fastapi.testclient import TestClient

from oko.observability import Metrics, Snapshot, event, private_metrics_token
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
        response=client.get('/api/v3/materials/private-publication-identifier',headers={'Authorization':'Bearer service'})
        assert response.status_code in (404,422)
        client.get('/unmatched-secret-url?private_key=never-export-this')
        text=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN}).text
        assert '/api/v3/materials/{item}' in text
        assert 'private-publication-identifier' not in text
        assert 'never-export-this' not in text and 'unmatched-secret-url' not in text


def test_failed_sampling_clears_stale_values_and_does_not_log_details(capsys):
    fail=[False]
    def sample():
        if fail[0]:raise RuntimeError('PRIVATE ARTICLE CONTENT AND SECRET')
        return Snapshot(False,[('fetch','pending',2,True)],False,False)
    metrics=Metrics(sample)
    assert b'oko_jobs_current{kind="fetch",state="pending"} 2.0' in metrics.render()
    fail[0]=True;text=metrics.render()
    assert b'oko_store_available 0.0' in text and b'oko_jobs_current{' not in text
    message=capsys.readouterr().err
    assert 'PRIVATE ARTICLE' not in message and 'SECRET' not in message
    assert json.loads(message)['error_type']=='RuntimeError'
    other=Metrics(lambda:Snapshot(False,[],False,False));assert b'oko_store_available 1.0' in other.render()


def test_safe_job_events_never_include_exception_messages(capsys):
    event('job_failed',job_id=42,kind='fetch',level='error',error=ValueError('credential=PRIVATE'))
    data=json.loads(capsys.readouterr().err)
    assert data==dict(event='job_failed',service='reader',level='error',job_id='42',kind='fetch',error_type='ValueError')


def test_metrics_credential_is_a_private_bounded_file(tmp_path):
    path=tmp_path/'metrics';path.write_text(TOKEN);path.chmod(0o600)
    assert private_metrics_token(path)==TOKEN
    path.chmod(0o644)
    with pytest.raises(ValueError,match='private regular file'):private_metrics_token(path)
    path.chmod(0o600);path.write_text('short')
    with pytest.raises(ValueError,match='Invalid metrics credential'):private_metrics_token(path)


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
            cards=client.get('/api/v3/cards').json()
            if cards and cards[0]['availability']=='available':break
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


def test_terminal_problems_remain_visible_without_becoming_live_queue(tmp_path):
    app=create_app(tmp_path,api_only=True,token='service',metrics_token=TOKEN)
    with TestClient(app) as client:
        store=app.state.host.execution.require_store()
        async def seed():
          with store.db.access_lock, store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (?,?,?,?,?)",
                             ('fetch','synthetic-failed','failed','private error','source-failed'))
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES (?,?,?,?)",
                             ('download_media','synthetic-unavailable','unavailable','media-unavailable'))
          return [j.status for j in store.work_summary()]
        statuses=client.portal.call(seed)
        text=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN}).text
        assert 'oko_work_problem_groups_current{kind="fetch"} 1.0' in text
        assert 'oko_work_problem_groups_current{kind="media"} 1.0' in text
        assert 'oko_jobs_current{kind="fetch",state="running"} 0.0' in text
        assert 'private error' not in text
        assert not any(status in ('failed','unavailable') for status in statuses)


def test_cancellation_completes_with_closed_logging_sink(tmp_path,monkeypatch):
    import asyncio
    import io
    import sys
    import threading
    started=threading.Event()
    calls=[0]
    async def response(request):
        calls[0]+=1
        if calls[0]>1:
            started.set()
            await asyncio.Event().wait()
        return httpx.Response(200,text='<rss version="2.0"><channel><title>Fixture</title></channel></rss>',
                              headers={'content-type':'application/rss+xml'})
    app=create_app(tmp_path,api_only=True,token='service',metrics_token=TOKEN,rss_enabled=True,rss_transport=httpx.MockTransport(response))
    with TestClient(app,headers={'Authorization':'Bearer service'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        client.put('/api/v3/power',json={'enabled':True})
        checked=client.post('/api/v3/feeds/verify',json={'url':'https://source.example/feed'}).json()
        added=client.post('/api/v3/feeds',json={'url':checked['candidate']['url'],'verification':checked['verification'],
            'title':'Fixture','folder':'Fixture','initial_history':{'mode':'count','count':'1'}})
        assert added.status_code==200 and started.wait(5)
        assert app.state.host.jobs._collection_active
        sink=io.StringIO();sink.close()
        with monkeypatch.context() as patch:
            patch.setattr(sys,'stderr',sink)
            event('closed_sink')
            result=client.put('/api/v3/power',json={'enabled':False})
        assert result.status_code==200
        assert not app.state.host.jobs._collection_active
        assert not app.state.host.jobs._lanes
        text=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN}).text
        assert 'oko_work_attempts_total{kind="discover",outcome="cancelled"} 1.0' in text


def test_metrics_do_not_scan_completed_job_archive(tmp_path):
    from oko.storage.store import Store
    seed=Store(tmp_path,lambda _:None)
    with seed.db:
        seed.db.executemany("INSERT INTO jobs(kind,item,status) VALUES ('fetch',?,'done')",[(f'old-{n}',) for n in range(20000)])
        seed.db.execute('ANALYZE')
    seed.set_work_enabled(False);seed.db.close()
    app=create_app(tmp_path,api_only=True,token='service',metrics_token=TOKEN)
    with TestClient(app) as client:
        store=app.state.host.execution.require_store()
        steps=[]
        client.portal.call(lambda:store.db.set_progress_handler(lambda:steps.append(1) or 0,100))
        try:response=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN})
        finally:client.portal.call(lambda:store.db.set_progress_handler(None,0))
        assert response.status_code==200
        assert len(steps)*100<10000


def test_youtube_admission_is_queue_wait_not_an_error(tmp_path):
    app=create_app(tmp_path,api_only=True,token='service',metrics_token=TOKEN)
    with TestClient(app) as client:
        store=app.state.host.execution.require_store()
        async def seed():
            with store.db:
                store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','synthetic-youtube','pending','youtube-admission')")
        client.portal.call(seed)
        text=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN}).text
        assert 'oko_jobs_current{kind="fetch",state="pending"} 1.0' in text
        assert 'oko_work_problem_groups_current{kind="fetch"} 0.0' in text


def test_cached_codex_check_expires_without_external_scrape_calls(monkeypatch):
    import oko.observability as observation
    now=[1000.]
    monkeypatch.setattr(observation.time,'monotonic',lambda:now[0])
    metrics=Metrics(lambda:Snapshot(True,[],False,False))
    assert b'oko_codex_status_known 0.0' in metrics.render()
    metrics.observe_codex(True)
    text=metrics.render()
    assert b'oko_codex_status_known 1.0' in text and b'oko_codex_available 1.0' in text
    now[0]+=301
    text=metrics.render()
    assert b'oko_codex_status_known 0.0' in text and b'oko_codex_available 0.0' in text


def test_worker_codex_checks_update_metrics_without_ui_check_or_new_scrape_requests(tmp_path):
    from oko.collection.demo import DemoSource
    from tests.support.codex import CodexService,configure_codex
    from tests.support.presence import presence
    from tests.support.reader import wait_for
    class Service(CodexService):
        checks=0
        async def check(self):
            self.checks+=1
            return await super().check()
    service=Service()
    app=create_app(tmp_path,token='service',source=DemoSource(),codex_client=service,metrics_token=TOKEN)
    with TestClient(app,headers={'Authorization':'Bearer service'}) as client:
        client.patch('/api/v3/settings/oko/global',json={'theses':False,'prompt_filters':''})
        presence(client,'reader')
        wait_for(client,lambda:len(client.get('/api/v3/cards').json())==3)
        configure_codex(client)
        client.patch('/api/v3/feeds/demo',json={'public_ai':True})
        client.patch('/api/v3/settings/oko/global',json={'theses':True})
        wait_for(client,lambda:all(card['theses']['result'] for card in client.get('/api/v3/cards').json()))
        assert service.checks>0
        before=service.checks
        text=client.get('/metrics',headers={'Authorization':'Bearer '+TOKEN}).text
        assert 'oko_codex_available 1.0' in text and 'oko_codex_status_known 1.0' in text
        assert service.checks==before
