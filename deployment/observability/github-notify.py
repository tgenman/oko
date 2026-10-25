#!/usr/bin/env python3
"""External monitor: HTTPS and forced read-only SSH, alerts delivered by repository issues."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPOSITORY='tgenman/oko'
PREFIX='<!-- oko-operational-alert:'


def api(path,method='GET',payload=None):
    request=Request('https://api.github.com/repos/'+REPOSITORY+path,method=method,
        headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2022-11-28','User-Agent':'oko-monitor'},
        data=json.dumps(payload).encode() if payload is not None else None)
    with urlopen(request,timeout=20) as response:return json.load(response)


def probe(ssh_key,known_hosts,host):
    alerts={}
    authoritative={"OkoPublicHttpsUnavailable"}
    try:
        with urlopen('https://oko.observer/',timeout=15) as response:
            if response.status!=200:raise ValueError('HTTPS did not return success')
    except (OSError,ValueError,HTTPError,URLError):
        alerts['OkoPublicHttpsUnavailable']='Public HTTPS is unavailable or certificate validation failed.'
    try:
        completed=subprocess.run(['ssh','-i',str(ssh_key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known_hosts),
            '-o','ConnectTimeout=10','-o','ConnectionAttempts=1','oko-monitor@'+host],
            check=True,capture_output=True,text=True,timeout=40)
        state=json.loads(completed.stdout)
        if state['version']!=1 or abs(time.time()-state['observed_at'])>120:raise ValueError('Stale host snapshot')
        authoritative.update(('OkoVmUnreachable','OkoBackupMissingOrStale','OkoMonitoringUnavailable'))
        if state['prometheus_ok']:
            authoritative.update(name for name in state.get('resolved',[]) if name.startswith('Oko') and name.isascii() and name.isalnum() and len(name)<80)
        if not state['prometheus_ok']:
            alerts['OkoMonitoringUnavailable']='Prometheus cannot provide operational alerts.'
        for alert in state['alerts']:
            name=alert['name']
            if name.startswith('Oko') and name.isascii() and name.isalnum() and len(name)<80:
                alerts[name]='The Prometheus rule '+name+' is firing. Inspect Grafana through the SSH tunnel.'
        backup=state['backup']
        if backup is None or not 0<backup['last_success_timestamp_seconds']<=time.time()+120 or time.time()-backup['last_success_timestamp_seconds']>129600:
            alerts['OkoBackupMissingOrStale']='External check has no successful backup heartbeat within 36 hours.'
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        # Partial/malformed snapshots prove no host-side recovery, even if an
        # earlier field happened to look healthy. HTTPS was checked separately.
        authoritative={'OkoPublicHttpsUnavailable'}
        alerts={key:value for key,value in alerts.items() if key=='OkoPublicHttpsUnavailable'}
        alerts['OkoVmUnreachable']='The VM read-only SSH check failed. Host downtime also prevents backup heartbeat verification.'
    return alerts,authoritative


def synchronize(alerts,authoritative,run_url):
    # Fetch all current alert issues; do not create duplicates after a transient pagination boundary.
    existing={}
    for page in range(1,101):
        issues=api(f'/issues?state=open&labels=operations-alert&per_page=100&page={page}')
        for issue in issues:
            body=issue.get('body') or ''
            if body.startswith(PREFIX):
                name=body[len(PREFIX):].split(' -->',1)[0]
                existing[name]=issue
        if len(issues)<100:break
    else:raise RuntimeError('Too many alert issues; refusing an incomplete synchronization')
    for name,message in sorted(alerts.items()):
        if name not in existing:
            api('/issues','POST',dict(title='[Oko alert] '+name,body=PREFIX+name+' -->\n\n'+message+'\n\nDetected by [external GitHub Actions check]('+run_url+').',labels=['operations-alert'],assignees=['tgenman']))
    for name,issue in existing.items():
        if name not in alerts and name in authoritative:
            api(f'/issues/{issue["number"]}/comments','POST',dict(body='Recovered in [external check]('+run_url+').'))
            api(f'/issues/{issue["number"]}','PATCH',dict(state='closed',state_reason='completed'))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--ssh-key',type=Path,required=True)
    parser.add_argument('--known-hosts',type=Path,required=True)
    args=parser.parse_args()
    alerts,authoritative=probe(args.ssh_key,args.known_hosts,'201.79.3.253')
    synchronize(alerts,authoritative,os.environ['GITHUB_SERVER_URL']+'/'+REPOSITORY+'/actions/runs/'+os.environ['GITHUB_RUN_ID'])
    print(json.dumps(dict(alerts=sorted(alerts))))
