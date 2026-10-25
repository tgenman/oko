#!/usr/bin/env python3
"""Install monitoring independently from application and PostgreSQL lifecycles."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('oko_operator',SOURCE/'okoctl.py')
operator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)
ROOT=operator.ROOT


def run(*args,**kwargs):
    return subprocess.run(list(map(str,args)),check=True,**kwargs)


def copy_configuration(source,config):
    config.mkdir(parents=True,exist_ok=True)
    config.chmod(0o755)
    for path in source.rglob('*'):
        if path.is_file() and path.suffix!='.pyc' and '__pycache__' not in path.parts:
            operator.atomic(config/path.relative_to(source),path.read_bytes(),mode=0o644)
    for directory in config.rglob('*'):
        if directory.is_dir():directory.chmod(0o755)


def install():
    if os.geteuid()!=0:raise RuntimeError('Run as root on the Oko VM')
    # Fail before mutations if any reviewed image cannot be obtained.
    images=json.loads((SOURCE/'observability/images.json').read_text())
    for image in images.values():
        run('docker','pull',image['image'])
        actual=json.loads(run('docker','image','inspect',image['image'],capture_output=True,text=True).stdout)[0]
        if actual['Architecture']!='amd64':raise ValueError('Monitoring image must be amd64')
    with operator.maintenance():
        for name,uid,mode in [('loki',10001,0o750),('alloy',0,0o700),('prometheus',65534,0o750),('grafana',472,0o750)]:
            path=ROOT/name;path.mkdir(exist_ok=True)
            # Do not recursively take ownership of an existing, foreign installation.
            if path.stat().st_uid!=uid and any(path.iterdir()):raise RuntimeError('Unexpected persistent directory owner')
            os.chown(path,uid,uid);path.chmod(mode)
        for name in ('targets','textfile'):
            directory=ROOT/'observability'/name;directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o755)
        secret=ROOT/'secrets/reader-metrics'
        if not secret.exists():operator.atomic(secret,secrets.token_urlsafe(48)+'\n',uid=10001,gid=10001)
        operator.atomic(ROOT/'secrets/prometheus-reader-metrics',secret.read_bytes(),uid=65534,gid=65534)
        secret=ROOT/'secrets/grafana-admin'
        if not secret.exists():operator.atomic(secret,secrets.token_urlsafe(48)+'\n',uid=472,gid=472)
        secret=ROOT/'secrets/postgres-monitor'
        if not secret.exists():operator.atomic(secret,secrets.token_hex(32)+'\n',uid=65534,gid=65534)
        password=secret.read_text().strip()
        if len(password)!=64 or any(c not in '0123456789abcdef' for c in password):raise ValueError('Invalid monitor credential')
        sql=f"""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='oko_monitor') THEN CREATE ROLE oko_monitor LOGIN; END IF; END $$;
ALTER ROLE oko_monitor WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{password}';
GRANT CONNECT ON DATABASE oko TO oko_monitor;
GRANT pg_monitor TO oko_monitor;
"""
        operator.compose('infra','exec','-T','postgres','psql','-U','postgres','-d','oko','-v','ON_ERROR_STOP=1',input=sql,text=True)
        copy_configuration(SOURCE/'observability',ROOT/'config/observability')
        operator.atomic(ROOT/'config/compose.monitoring.yaml',(SOURCE/'compose.monitoring.yaml').read_bytes(),mode=0o644)
        journal=Path('/etc/systemd/journald.conf.d/oko.conf')
        operator.atomic(journal,'[Journal]\nStorage=persistent\nSystemMaxUse=200M\nMaxRetentionSec=7day\n',mode=0o644)
        run('mkdir','-p','/var/log/journal')
        run('systemd-tmpfiles','--create','--prefix','/var/log/journal')
        run('systemctl','restart','systemd-journald.service')
        run('journalctl','--flush')
        for suffix in ('service','timer'):
            name='oko-observability-collect.'+suffix
            operator.atomic(Path('/etc/systemd/system')/name,(SOURCE/'systemd'/name).read_bytes(),mode=0o644)
        run('systemctl','daemon-reload')
        run('systemctl','start','oko-observability-collect.service')
        run('systemctl','enable','--now','oko-observability-collect.timer')
        run('docker','compose','-f',ROOT/'config/compose.monitoring.yaml','up','-d','--force-recreate')
    print('Monitoring installed; HTTPS metrics require the matching reader release. Validate service readiness separately.')


if __name__=='__main__':install()
