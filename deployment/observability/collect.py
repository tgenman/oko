#!/usr/bin/env python3
"""Root-only collection of bounded host metadata; never reads application content."""
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time

ROOT = Path('/srv/oko')
SERVICES = {'oko-app': {'reader'}, 'oko-infra': {'postgres'},
            'oko-monitoring': {'loki','alloy','grafana','prometheus','node-exporter','postgres-exporter'}}


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent,prefix='.'+path.name)
    try:
        with os.fdopen(fd,'w') as output:
            output.write(data);output.flush();os.fchmod(output.fileno(),mode);os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def command(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=10).stdout


def collect(root=ROOT):
    lines=['# Collected by root from a bounded allowlist, without source contents.']
    targets=[]
    state_path=root/'observability/container-state.json'
    previous=json.loads(state_path.read_text()) if state_path.exists() else {}
    state={}
    ids=command('docker','ps','-aq','--filter','label=com.docker.compose.project').split()
    containers=json.loads(command('docker','inspect',*ids)) if ids else []
    seen=set()
    for container in containers:
        labels=container['Config'].get('Labels') or {}
        project,service=labels.get('com.docker.compose.project'),labels.get('com.docker.compose.service')
        if project not in SERVICES or service not in SERVICES[project]:continue
        if service in seen:raise ValueError('Duplicate monitored service')
        seen.add(service)
        identity=container['Id']
        old=previous.get(service,{})
        restart=int(container['RestartCount']);oom=int(bool(container['State'].get('OOMKilled')))
        same=old.get('id')==identity
        total=old.get('restarts',0)+(max(0,restart-old.get('restart',0)) if same else restart)
        killed=old.get('ooms',0)+(max(0,oom-old.get('oom',0)) if same else oom)
        state[service]=dict(id=identity,restart=restart,restarts=total,oom=oom,ooms=killed)
        lines += [f'oko_container_restarts_total{{service="{service}"}} {total}',
                  f'oko_container_oom_total{{service="{service}"}} {killed}',
                  f'oko_container_running{{service="{service}"}} {int(container["State"]["Running"])}']
        path=container.get('LogPath','')
        if container['HostConfig']['LogConfig']['Type']!='json-file':
            raise ValueError('Monitored Docker service must use json-file logging')
        if path!=f'/var/lib/docker/containers/{identity}/{identity}-json.log':
            raise ValueError('Unexpected Docker log path')
        targets.append(dict(targets=['localhost'],labels={'__path__':path,'deployment':'oko','service':service,'level':'info'}))
    # Missing services remain explicit zeroes; disappeared counters retain their totals.
    for project_services in SERVICES.values():
        for service in project_services-seen:
            if service in previous:state[service]=previous[service]
            lines.append(f'oko_container_running{{service="{service}"}} 0')
    helper=subprocess.run(['systemctl','is-active','--quiet','oko-codex.service'],timeout=10).returncode==0
    lines.append(f'oko_helper_active {int(helper)}')
    cert=ssl._ssl._test_decode_cert(str(root/'tls/current/fullchain.pem'))
    lines.append(f'oko_tls_expiry_timestamp_seconds {ssl.cert_time_to_seconds(cert["notAfter"])}')
    # Written atomically by the backup producer only after completion. Unknown is not success.
    heartbeat=root/'backup/status.json'
    try:
        backup=json.loads(heartbeat.read_text())
        values={}
        for key in ('last_success_timestamp_seconds','last_success_size_bytes','last_attempt_timestamp_seconds'):
            value=backup[key]
            if not isinstance(value,(int,float)) or not 0<=value<1e18:raise ValueError('Invalid backup heartbeat')
            if key.endswith('timestamp_seconds') and value>time.time()+120:raise ValueError('Future backup heartbeat')
            values[key]=value
        result=backup['last_attempt_result']
        if result not in ('unknown','success','error'):raise ValueError('Invalid backup result')
    except (OSError,ValueError,KeyError,TypeError):
        values=dict(last_success_timestamp_seconds=0,last_success_size_bytes=0,last_attempt_timestamp_seconds=0)
        result='unknown'
    for key,value in values.items():lines.append(f'oko_backup_{key} {value}')
    for outcome in ('unknown','success','error'):
        lines.append(f'oko_backup_last_attempt_result{{result="{outcome}"}} {int(result==outcome)}')
    lines += ['oko_host_collection_success 1',f'oko_host_collection_timestamp_seconds {time.time()}']
    atomic(root/'observability/targets/docker.json',json.dumps(targets)+'\n')
    atomic(state_path,json.dumps(state)+'\n',0o600)
    atomic(root/'observability/textfile/oko.prom','\n'.join(lines)+'\n')


if __name__=='__main__':
    try:
        collect()
    except Exception:
        # No raw Docker fields, paths, secret values, or exception messages in the journal.
        atomic(ROOT/'observability/textfile/oko.prom',
               f'oko_host_collection_success 0\noko_host_collection_timestamp_seconds {time.time()}\n')
        raise SystemExit('Oko host metadata collection failed') from None
