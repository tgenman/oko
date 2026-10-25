#!/usr/bin/env python3
"""Forced-command SSH endpoint: read sanitized operational state, change nothing."""
from datetime import datetime
import json
import math
from pathlib import Path
import time
from urllib.request import urlopen


def read(path):
    with urlopen('http://127.0.0.1:9090/api/v1/'+path,timeout=10) as response:
        raw=json.load(response)
    if raw.get('status')!='success':raise ValueError('Prometheus failed')
    return raw['data']


def bounded_name(name):
    return isinstance(name,str) and name.startswith('Oko') and name.isascii() and name.isalnum() and len(name)<80


def snapshot():
    result=dict(version=1,observed_at=time.time(),prometheus_ok=False,alerts=[],resolved=[],backup=None)
    try:
        rules=read('rules?type=alert')['groups']
        targets=read('targets')['activeTargets']
        expected={'reader','host','loki','alloy','prometheus','postgres'}
        healthy=(len(targets)==len(expected) and {t['labels']['job'] for t in targets}==expected and
            all(t['health']=='up' and 0<=time.time()-datetime.fromisoformat(t['lastScrape'].replace('Z','+00:00')).timestamp()<120 for t in targets))
        # An HTTP 200 from Prometheus alone cannot prove recovery of data sources.
        query=('(oko_store_available==1) and on() (oko_host_collection_success==1) '
               'and on() (time()-oko_host_collection_timestamp_seconds<120) and on() (pg_up==1)')
        from urllib.parse import urlencode
        healthy=healthy and bool(read('query?'+urlencode({'query':query}))['result'])
        for group in rules:
            for rule in group['rules']:
                name=rule['name']
                if not bounded_name(name):continue
                if rule['state']=='firing':
                    severity=rule.get('labels',{}).get('severity')
                    result['alerts'].append(dict(name=name,severity=severity if severity in ('warning','critical') else 'warning'))
                elif rule['state']=='inactive' and healthy and rule.get('health')=='ok' and not rule.get('lastError'):
                    evaluated=datetime.fromisoformat(rule['lastEvaluation'].replace('Z','+00:00')).timestamp()
                    if 0<=time.time()-evaluated<120:result['resolved'].append(name)
                # Pending remains unresolved, including after a Prometheus restart.
        result['prometheus_ok']=True
    except Exception:
        result['resolved']=[]
    try:
        backup=json.loads(Path('/srv/oko/backup/status.json').read_text())
        value=backup['last_success_timestamp_seconds']
        if isinstance(value,(int,float)) and math.isfinite(value) and 0<value<=time.time()+120:
            result['backup']=dict(last_success_timestamp_seconds=value)
    except (OSError,ValueError,KeyError,TypeError):
        pass
    result['alerts']=sorted({a['name']:a for a in result['alerts']}.values(),key=lambda a:a['name'])
    return result


if __name__=='__main__':print(json.dumps(snapshot(),separators=(',',':')))
