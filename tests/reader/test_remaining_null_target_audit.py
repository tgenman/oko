import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction

@pytest.mark.parametrize('kind',['fetch','filter','theses','download_media'])
def test_missing_legacy_target_status_does_not_crash(tmp_path,postgres_url,kind):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,NULL,'failed','failure','ordinary')",(kind,))
  s.problems.snapshot(100)
 finally:s.db.close()

def test_missing_legacy_target_breaks_status_http(tmp_path,postgres_url):
 from fastapi.testclient import TestClient
 from tests.support.app import create_app
 from tests.support.reader import HEADERS
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch',NULL,'failed','failure','ordinary')")
  client.portal.call(setup)
  assert client.get('/api/v3/status/details').status_code==200

@pytest.mark.parametrize('kind',['fetch','filter','theses','download_media'])
@pytest.mark.parametrize('target',[None,''])
def test_generic_http_is_stable_and_preserves_job(tmp_path,postgres_url,kind,target):
 from fastapi.testclient import TestClient
 from tests.support.app import create_app
 from tests.support.reader import HEADERS
 from oko.storage.job_writes import execute as job_write
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):return job_write(s.db,"INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,'failed','failure','ordinary') RETURNING id",(kind,target)).fetchone()[0]
  jid=client.portal.call(setup)
  first=client.get('/api/v3/status/details');assert first.status_code==200,first.text
  found=[p for p in first.json()['problems']['current'] if p['id']==f'legacy-null-job:{jid}']
  assert len(found)==1 and found[0]['affected']==[] and found[0]['recovery']==[]
  second=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert second.status_code==304
  def rollback():
   s=app.state.host.require_store()
   try:
    with transaction(s.db):
     job_write(s.db,"UPDATE jobs SET status='cancelled' WHERE id=%s",(jid,))
     raise RuntimeError('rollback')
   except RuntimeError:pass
   return tuple(s.db.execute('SELECT item,status,error,problem_code FROM jobs WHERE id=%s',(jid,)).fetchone())
  assert client.portal.call(rollback)==(target,'failed','failure','ordinary')
  assert client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']}).status_code==304
  def finish():
   s=app.state.host.require_store()
   with transaction(s.db):job_write(s.db,"UPDATE jobs SET status='cancelled' WHERE id=%s",(jid,))
  client.portal.call(finish)
  after=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert after.status_code==200
  assert not any(p['id']==f'legacy-null-job:{jid}' for p in after.json()['problems']['current'])

def test_generic_id_avoids_existing_problem_and_job_survives_reopen(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch',NULL,'failed','failure','ordinary') RETURNING id").fetchone()[0]
   raw=json.dumps(dict(code='processor-config',processor_group_identity='processor:configuration',section='action',title='foreign',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None))
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,'processor:configuration','background',%s,1)",(f'legacy-null-job:{job}',raw))
  before=s.problems.snapshot(100);entry=next(p for p in before['current'] if p['code']=='legacy-unaddressable')
  assert entry['id']==f'legacy-null-job:{job}:1'
  assert tuple(s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(f'legacy-null-job:{job}',)).fetchone())==(raw,None)
 finally:s.db.close()
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  after=s.problems.snapshot(100);assert any(p['id']==entry['id'] for p in after['current'])
  assert s.db.execute('SELECT item FROM jobs WHERE id=%s',(job,)).fetchone()[0] is None
 finally:s.db.close()

@pytest.mark.parametrize('kind',['fetch','filter','theses','download_media'])
@pytest.mark.parametrize('code',[None,'profile_unavailable','source-unavailable',''])
def test_null_target_codes_render_http(tmp_path,postgres_url,kind,code):
 from fastapi.testclient import TestClient
 from tests.support.app import create_app
 from tests.support.reader import HEADERS
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,NULL,'failed','failure',%s)",(kind,code))
  client.portal.call(setup)
  response=client.get('/api/v3/status/details')
  assert response.status_code==200,(kind,code,response.text)
  assert len(response.json()['problems']['current'])>=1

@pytest.mark.parametrize('kind',['fetch','filter','theses','download_media'])
@pytest.mark.parametrize('target',[None,''])
def test_null_source_problem_keeps_occurrence(tmp_path,postgres_url,kind,target):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES (%s,%s,'f','failed','failure','source-unavailable')",(kind,target))
   body=json.dumps(dict(code='source-unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:status',state='current',next_at=None))
   s.db.execute('INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,\'background\',%s,1)',('old',f'job:source-unavailable:f:{kind}',body))
  result=s.problems.snapshot(100)
  assert any(p['id']=='old' for p in result['current'])
  assert s.db.execute("SELECT finished FROM reader_problems WHERE id='old'").fetchone()[0] is None
 finally:s.db.close()

def test_null_source_foreign_previous_body_is_not_overwritten(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch',NULL,'f','failed','failure','source-unavailable')")
   body=json.dumps(dict(code='source-unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='unrelated')],action=None,operation='producer:status',state='current',next_at=None))
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-unavailable:f:fetch','background',%s,1)",(body,))
  s.problems.snapshot(100)
  row=s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone()
  assert tuple(row)==(body,None)
 finally:s.db.close()

def test_done_fetch_null_source_foreign_is_untouched(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch',NULL,'f','done','failure','source-unavailable')")
   body=json.dumps(dict(code='source-unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='foreign')],action=None,operation='producer:status',state='current',next_at=None))
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-unavailable:f:fetch','background',%s,1)",(body,))
  s.problems.snapshot(100)
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
 finally:s.db.close()
