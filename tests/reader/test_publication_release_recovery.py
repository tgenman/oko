"""Recovery checks use an isolated synthetic PostgreSQL schema and files only."""
import json
import os
import shutil
import subprocess
import sys
import tarfile
from contextlib import nullcontext
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


def pg_environment(dsn):
    parameters=conninfo_to_dict(dsn)
    env=os.environ.copy()
    for name in ('host','port','user','password','dbname'):
        if name in parameters:env[{'dbname':'PGDATABASE'}.get(name,'PG'+name.upper())]=parameters[name]
    if 'options' in parameters:env['PGOPTIONS']=parameters['options']
    return env


def release(sha, version):
    return dict(version=1,sha=sha,platform='linux/amd64',
        image='ghcr.io/tgenman/oko-runtime@sha256:'+sha[0]*64,
        helper=dict(release=sha,protocol=1),
        codex=dict(version='0.164.0',target='x86_64-unknown-linux-musl',
            archive_url='https://github.com/openai/codex/releases/download/example/archive.tar.gz',
            archive_sha256='c'*64,binary_sha256='d'*64,code_mode_host_sha256='e'*64),
        schema=dict(minimum=version,maximum=version))




def test_rollback_after_new_writes_refuses_old_schema_without_deleting_data(postgres_database_url,tmp_path,monkeypatch):
    from deployment import deploy
    old,new='a'*40,'b'*40
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('new','rss','new',%s::jsonb)",
                   (json.dumps({'blocks':[{'type':'text','role':'body','text':'New data'}]}),))
        (tmp_path/'release.json').write_text(json.dumps(release(new,120)))
        (tmp_path/'previous-release.json').write_text(json.dumps(release(old,118)))
        media=tmp_path/'new-file'
        media.write_bytes(b'new synthetic bytes')
        monkeypatch.setattr(deploy,'prepare',lambda *args,**kwargs:None)
        monkeypatch.setattr(deploy,'maintenance',lambda root:nullcontext())
        monkeypatch.setattr(deploy,'database_state',lambda root,current:dict(version=db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]))
        stopped=[]
        monkeypatch.setattr(deploy,'stop',lambda root,current:stopped.append(current['sha']))
        with pytest.raises(ValueError,match='incompatible database schema'):
            deploy.change(tmp_path,None)
        assert stopped==[new]
        assert db.execute("SELECT content->'blocks'->0->>'text' FROM publications WHERE id='new'").fetchone()==('New data',)
        assert media.read_bytes()==b'new synthetic bytes'
        assert json.loads((tmp_path/'release.json').read_text())['sha']==new


@pytest.mark.parametrize('failure', ['prepare', 'database'])
def test_rollback_freezes_writes_even_if_recovery_checks_fail(tmp_path,monkeypatch,failure):
    from deployment import deploy
    old,new='a'*40,'b'*40
    (tmp_path/'release.json').write_text(json.dumps(release(new,120)))
    (tmp_path/'previous-release.json').write_text(json.dumps(release(old,118)))
    media=tmp_path/'new-file'
    media.write_bytes(b'new synthetic bytes')
    stopped=[]
    monkeypatch.setattr(deploy,'maintenance',lambda root:nullcontext())
    monkeypatch.setattr(deploy,'stop',lambda root,current:stopped.append(current['sha']))
    def check(name):
        assert stopped==[new]
        if name==failure:
            raise ValueError('synthetic '+name+' failure')
    monkeypatch.setattr(deploy,'prepare',lambda *args,**kwargs:check('prepare'))
    monkeypatch.setattr(deploy,'database_state',lambda *args:check('database'))
    with pytest.raises(ValueError,match='synthetic '+failure+' failure'):
        deploy.change(tmp_path,None)
    assert stopped==[new]
    assert media.read_bytes()==b'new synthetic bytes'
    assert json.loads((tmp_path/'release.json').read_text())['sha']==new


def test_explicit_restore_before_new_writes_recovers_matching_database_and_files(postgres_database_url,tmp_path):
    if not shutil.which('pg_dump') or not shutil.which('pg_restore'):
        pytest.skip('PostgreSQL client tools are not installed')
    env=pg_environment(postgres_database_url)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        server_major=int(db.execute('SHOW server_version_num').fetchone()[0])//10000
        client_major=int(subprocess.run(['pg_dump','--version'],check=True,capture_output=True,text=True).stdout.split()[-1].split('.')[0])
        if client_major<server_major:
            pytest.skip('pg_dump is older than the synthetic PostgreSQL server')
        migrate(db,MIGRATIONS)
        schema=db.execute('SELECT current_schema()').fetchone()[0]
        assert schema.startswith('test_')
        media_id='00000000-0000-4000-8000-000000000001'
        original={'blocks':[{'type':'text','role':'body','text':'Original'},
            {'type':'media','role':'image','media_id':media_id,'source_url':None,'publication_url':None}]}
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,platform,external_id,kind,transport,connection,original_urls) VALUES ('f','https://source.test/feed','Feed','default','rss','feed','rss','rss','connected',ARRAY['https://source.test/feed'])")
        db.execute("INSERT INTO publications(id,platform,external_id,feed_id,url,content,media_ids,status) VALUES ('p','rss','p','f','https://source.test/p',%s::jsonb,%s,'ready')",(json.dumps(original),json.dumps([media_id])))
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,mime_type,status,storage_key) VALUES (%s,'p','image','opaque','file','image/png','ready','media/p/file')",(media_id,))
        db.execute("UPDATE state SET value='false' WHERE key='work-enabled'")
        media=tmp_path/'library'/'media'/'p'/'file'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'synthetic-original')
        backup=tmp_path/'schema.dump'
        saved_files=tmp_path/'saved-media'
        shutil.copytree(media.parent.parent,saved_files)
        subprocess.run(['pg_dump','--format=custom','--no-owner','--schema',schema,'--file',str(backup)],env=env,check=True,capture_output=True)
        migrate(db,MIGRATIONS)
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==MIGRATIONS[-1].version
        media.unlink()
        db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
    subprocess.run(['pg_restore','--exit-on-error','--no-owner','--dbname',env['PGDATABASE'],str(backup)],env=env,check=True,capture_output=True)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        verify(db,MIGRATIONS)
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==MIGRATIONS[-1].version
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==original
    shutil.copytree(saved_files,media.parent.parent,dirs_exist_ok=True)
    assert media.read_bytes()==b'synthetic-original'
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    with TestClient(create_app(tmp_path / 'library', token='test', api_only=True, database_url=postgres_database_url), headers={'Authorization':'Bearer test'}) as client:
        response = client.get('/api/v3/publication/p')
        assert response.status_code == 200 and response.json()['content']['blocks'][0]['text'] == 'Original'
        path = next(item['url'] for item in response.json()['media'] if item['id'] == media_id)
        assert client.get(path).content == b'synthetic-original'
