from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.storage.transaction import transaction
from oko.storage.job_writes import execute as job_write

def test_legacy_job_unknown_time_has_neutral_code_and_stable_etag(tmp_path,postgres_url):
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):return job_write(s.db,"INSERT INTO jobs(kind,item,status,error,problem_code,queued_wall) VALUES ('fetch',NULL,'failed','failure','ordinary',42) RETURNING id").fetchone()[0]
  jid=client.portal.call(setup)
  first=client.get('/api/v3/status/details');assert first.status_code==200,first.text
  p=next(p for p in first.json()['problems']['current'] if p['id']==f'legacy-null-job:{jid}')
  assert p['code']=='job_target_unknown' and p['created_at'] is None
  assert p['affected']==[] and p['recovery']==[]
  assert client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']}).status_code==304

def test_problem_contract_requires_nullable_created_at():
 import yaml
 from pathlib import Path
 schema=yaml.safe_load((Path(__file__).resolve().parents[2]/'api/openapi.yaml').read_text())['components']['schemas']
 assert 'job_target_unknown' in schema['ProblemCode']['enum']
 for name in ('CurrentProblem','RetryingProblem','FinishedProblem'):
  entry=schema[name]
  assert 'created_at' in entry['required']
  assert {'type':'null'} in entry['properties']['created_at']['anyOf']
