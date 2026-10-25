"""Operational failures must not produce a false recovery or publish private values."""
import json
from pathlib import Path
import runpy
import subprocess
import time
from urllib.error import URLError

ROOT=Path(__file__).resolve().parents[2]


def module(name):
    return runpy.run_path(str(ROOT/'deployment/observability'/name))


class Response:
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):pass


def test_unreachable_vm_does_not_resolve_existing_work_or_backup_alerts(monkeypatch):
    scope=module('github-notify.py');g=scope['probe'].__globals__
    monkeypatch.setitem(g,'urlopen',lambda *args,**kwargs:Response())
    def unavailable(*args,**kwargs):raise subprocess.TimeoutExpired('ssh',40)
    monkeypatch.setattr(subprocess,'run',unavailable)
    alerts,authoritative=scope['probe'](Path('synthetic-key'),Path('synthetic-hosts'),'192.0.2.1')
    assert set(alerts)=={'OkoVmUnreachable'}
    assert authoritative=={'OkoPublicHttpsUnavailable'}
    calls=[]
    def api(path,method='GET',payload=None):
        calls.append((path,method,payload))
        return [dict(number=n,body=g['PREFIX']+name+' -->') for n,name in enumerate(
            ['OkoWorkErrors','OkoBackupMissingOrStale','OkoPublicHttpsUnavailable'],1)] if method=='GET' else {}
    monkeypatch.setitem(g,'api',api)
    scope['synchronize'](alerts,authoritative,'https://github.com/tgenman/oko/actions/runs/1')
    assert [path for path,method,_ in calls if method=='PATCH']==['/issues/3']
    created=[payload for _,method,payload in calls if method=='POST' and payload.get('title')]
    assert len(created)==1 and created[0]['assignees']==['tgenman']


def test_dead_prometheus_does_not_resolve_its_rules_but_external_backup_check_runs(monkeypatch):
    scope=module('github-notify.py');g=scope['probe'].__globals__
    monkeypatch.setitem(g,'urlopen',lambda *args,**kwargs:Response())
    state=dict(version=1,observed_at=time.time(),prometheus_ok=False,alerts=[],backup=None)
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:subprocess.CompletedProcess([],0,stdout=json.dumps(state)))
    alerts,authoritative=scope['probe'](Path('synthetic-key'),Path('synthetic-hosts'),'192.0.2.1')
    assert set(alerts)=={'OkoMonitoringUnavailable','OkoBackupMissingOrStale'}
    assert '*' not in authoritative
    assert 'OkoVmUnreachable' in authoritative


def test_host_collector_exports_no_ids_or_private_docker_fields_and_unknown_backup(tmp_path,monkeypatch):
    scope=module('collect.py');g=scope['collect'].__globals__
    identity='a'*64
    container=dict(Id=identity,Config={'Labels':{'com.docker.compose.project':'oko-app','com.docker.compose.service':'reader','private':'PRIVATE'}},
        State={'Running':True,'OOMKilled':False},RestartCount=2,LogPath=f'/var/lib/docker/containers/{identity}/{identity}-json.log',
        HostConfig={'LogConfig':{'Type':'json-file'}},Env=['API_KEY=PRIVATE'])
    monkeypatch.setitem(g,'command',lambda *args: identity if args[1]=='ps' else json.dumps([container]))
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:subprocess.CompletedProcess([],0))
    monkeypatch.setattr(g['ssl']._ssl,'_test_decode_cert',lambda path:{'notAfter':'Oct 25 00:00:00 2027 GMT'})
    scope['collect'](tmp_path)
    metrics=(tmp_path/'observability/textfile/oko.prom').read_text()
    targets=(tmp_path/'observability/targets/docker.json').read_text()
    assert identity not in metrics and 'PRIVATE' not in metrics+targets
    assert 'oko_backup_last_success_timestamp_seconds 0' in metrics
    assert 'oko_backup_last_attempt_result{result="unknown"} 1' in metrics
    assert 'oko_container_running{service="postgres"} 0' in metrics
    assert 'oko_container_restarts_total{service="reader"} 2' in metrics
    scope['collect'](tmp_path)
    assert 'oko_container_restarts_total{service="reader"} 2' in (tmp_path/'observability/textfile/oko.prom').read_text()
    (tmp_path/'backup').mkdir()
    (tmp_path/'backup/status.json').write_text('{malformed private backup data')
    scope['collect'](tmp_path)
    after=(tmp_path/'observability/textfile/oko.prom').read_text()
    assert 'oko_host_collection_success 1' in after
    assert 'oko_backup_last_attempt_result{result="unknown"} 1' in after
    assert 'private backup' not in after



def test_prometheus_alive_with_reader_down_or_pending_does_not_prove_work_recovery(monkeypatch):
    from datetime import datetime,timezone
    scope=module('snapshot.py');g=scope['snapshot'].__globals__
    now=datetime.now(timezone.utc).isoformat()
    targets=[dict(labels={'job':name},health='up',lastScrape=now) for name in ('reader','host','loki','alloy','prometheus','postgres')]
    rules=[dict(rules=[dict(name='OkoWorkErrors',state='inactive',lastEvaluation=now,health='ok',labels={})])]
    def read(path):
        if path.startswith('rules'):return {'groups':rules}
        if path=='targets':return {'activeTargets':targets}
        return {'result':[{'value':[time.time(),'1']}]}
    monkeypatch.setitem(g,'read',read)
    targets[0]['health']='down'
    state=scope['snapshot']()
    assert state['prometheus_ok'] is True and state['resolved']==[]
    targets[0]['health']='up';rules[0]['rules'][0]['state']='pending'
    assert scope['snapshot']()['resolved']==[]
    rules[0]['rules'][0]['state']='inactive'
    assert scope['snapshot']()['resolved']==['OkoWorkErrors']
    rules[0]['rules'][0]['health']='err'
    assert scope['snapshot']()['resolved']==[]
    rules[0]['rules'][0]['health']='ok'
    # Even an inactive rule cannot prove recovery with stale scrape data.
    targets[0]['lastScrape']='2020-01-01T00:00:00Z'
    assert scope['snapshot']()['resolved']==[]


def test_future_backup_success_is_not_a_healthy_heartbeat(monkeypatch):
    scope=module('github-notify.py');g=scope['probe'].__globals__
    monkeypatch.setitem(g,'urlopen',lambda *args,**kwargs:Response())
    state=dict(version=1,observed_at=time.time(),prometheus_ok=True,alerts=[],resolved=[],
               backup={'last_success_timestamp_seconds':time.time()+86400})
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:subprocess.CompletedProcess([],0,stdout=json.dumps(state)))
    alerts,_=scope['probe'](Path('synthetic-key'),Path('synthetic-hosts'),'192.0.2.1')
    assert 'OkoBackupMissingOrStale' in alerts


def test_configuration_directory_mounts_remain_traversable_with_private_root_umask(tmp_path,monkeypatch):
    import os
    scope=module('install.py')
    source=tmp_path/'source';source.mkdir()
    (source/'grafana/datasources').mkdir(parents=True)
    (source/'grafana/datasources/oko.yaml').write_text('apiVersion: 1\n')
    target=tmp_path/'config'
    secret=tmp_path/'private-secret';secret.write_text('synthetic');secret.chmod(0o600)
    monkeypatch.setattr(os,'fchown',lambda *args:None)
    old=os.umask(0o077)
    try:scope['copy_configuration'](source,target)
    finally:os.umask(old)
    assert target.stat().st_mode&0o777==0o755
    assert (target/'grafana').stat().st_mode&0o777==0o755
    assert (target/'grafana/datasources').stat().st_mode&0o777==0o755
    assert secret.stat().st_mode&0o777==0o600


def test_malformed_host_snapshot_cannot_partially_authorize_recovery(monkeypatch):
    scope=module('github-notify.py');g=scope['probe'].__globals__
    monkeypatch.setitem(g,'urlopen',lambda *args,**kwargs:Response())
    state=dict(version=1,observed_at=time.time(),prometheus_ok=True,
               alerts=[],resolved=['OkoWorkErrors'],backup={})
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:subprocess.CompletedProcess([],0,stdout=json.dumps(state)))
    alerts,authoritative=scope['probe'](Path('synthetic-key'),Path('synthetic-hosts'),'192.0.2.1')
    assert set(alerts)=={'OkoVmUnreachable'}
    assert authoritative=={'OkoPublicHttpsUnavailable'}
