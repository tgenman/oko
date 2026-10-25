"""Maintenance contention, failed renewals and credential-preserving bootstrap."""
import json
import os
from pathlib import Path
import runpy
import ssl
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]


def operator():
    return runpy.run_path(str(ROOT/'deployment/okoctl.py'))


def test_maintenance_excludes_another_process_and_releases_on_error(tmp_path):
    scope=operator()
    code='import runpy,sys; from pathlib import Path; m=runpy.run_path(sys.argv[1]);\nwith m["maintenance"](Path(sys.argv[2])): print("acquired")'
    with pytest.raises(ValueError):
        with scope['maintenance'](tmp_path):
            result=subprocess.run([sys.executable,'-c',code,str(ROOT/'deployment/okoctl.py'),str(tmp_path)],capture_output=True,text=True)
            assert result.returncode != 0 and 'Another maintenance' in result.stderr
            raise ValueError('synthetic failure')
    with scope['maintenance'](tmp_path): pass


def test_existing_database_missing_credentials_cannot_regenerate(tmp_path,monkeypatch):
    scope=operator(); scope['initialize_files'].__globals__['ROOT']=tmp_path
    monkeypatch.setattr(os,'chown',lambda *args:None)
    (tmp_path/'postgres').mkdir()
    (tmp_path/'postgres/PG_VERSION').write_text('18')
    with pytest.raises(RuntimeError,match='original credentials'):
        scope['initialize_files']()
    assert not (tmp_path/'secrets/postgres-admin').exists()


def test_manifest_rejects_mutable_or_unrelated_images(tmp_path):
    path=tmp_path/'release.json'
    scope=operator(); sha='a'*40
    for image in ('ghcr.io/tgenman/oko:latest','ghcr.io/other/oko@sha256:'+'b'*64):
        path.write_text(json.dumps(dict(sha=sha,image=image,platform='linux/amd64',helper=dict(release=sha))))
        with pytest.raises(ValueError,match='manifest'):scope['manifest'](path)


def test_renewal_failure_restores_both_files_as_one_version(tmp_path):
    scope=operator(); globals_=scope['apply_certificate'].__globals__
    tls=tmp_path/'tls';tls.mkdir()
    old=Path('versions/old');new=Path('versions/new')
    (tls/'current').symlink_to(old)
    globals_['ROOT']=tmp_path
    globals_['certificate_version']=lambda *args:new
    globals_['installed']=lambda:dict(image='synthetic')
    globals_['reader_running']=lambda _:True
    original_point=scope['point_certificate']
    globals_['point_certificate']=lambda value:original_point(value,root=tmp_path)
    (tmp_path/'release.json').write_text('{}')
    observed=[]
    def compose(*args,**options):
        observed.append((tls/'current').readlink())
        if len(observed)==1:raise subprocess.CalledProcessError(1,['synthetic health'])
    globals_['compose']=compose
    with pytest.raises(subprocess.CalledProcessError):scope['apply_certificate'](Path('cert'),Path('key'))
    assert observed == [new,old]
    assert (tls/'current').readlink()==old


def test_rejected_key_cannot_replace_active_certificate(tmp_path):
    scope=operator();tls=tmp_path/'tls';tls.mkdir()
    (tls/'current').symlink_to('versions/retained')
    cert=tmp_path/'cert';key=tmp_path/'key'
    cert.write_text('invalid');key.write_text('invalid')
    with pytest.raises(ssl.SSLError):scope['certificate_version'](cert,key,root=tmp_path)
    assert (tls/'current').readlink()==Path('versions/retained')
    assert not (tls/'versions').exists()


def test_certificate_pointer_without_applied_receipt_is_retried(tmp_path):
    scope=operator(); globals_=scope['apply_certificate'].__globals__
    tls=tmp_path/'tls';tls.mkdir()
    target=Path('versions/new')
    (tls/'current').symlink_to(target)
    (tmp_path/'release.json').write_text('{}')
    globals_['ROOT']=tmp_path
    globals_['certificate_version']=lambda *args:target
    globals_['installed']=lambda:dict(image='synthetic')
    globals_['reader_running']=lambda _:True
    original_point=scope['point_certificate']
    globals_['point_certificate']=lambda value:original_point(value,root=tmp_path)
    globals_['atomic']=lambda path,value:path.write_text(value)
    restarts=[]
    globals_['compose']=lambda *args,**kwargs:restarts.append(args)
    scope['apply_certificate'](Path('cert'),Path('key'))
    assert len(restarts)==1 and '--force-recreate' in restarts[0]
    assert (tls/'applied-version').read_text().strip()==str(target)
    scope['apply_certificate'](Path('cert'),Path('key'))
    assert len(restarts)==1


def test_start_cannot_acknowledge_unloaded_certificate(tmp_path):
    scope=operator(); globals_=scope['start'].__globals__
    tls=tmp_path/'tls';tls.mkdir()
    (tls/'current').symlink_to('versions/new')
    globals_['ROOT']=tmp_path
    globals_['run']=lambda *args:None
    globals_['atomic']=lambda path,value:path.write_text(value)
    calls=[]
    globals_['compose']=lambda *args,**kwargs:calls.append(args)
    scope['start'](dict(image='synthetic'))
    assert '--force-recreate' in calls[-1]
    assert (tls/'applied-version').read_text()=='versions/new\n'
    scope['start'](dict(image='synthetic'))
    assert '--force-recreate' not in calls[-1]
