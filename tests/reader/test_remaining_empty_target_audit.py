import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.storage.transaction import transaction
@pytest.mark.parametrize('kind',['fetch','filter','theses','download_media'])
@pytest.mark.parametrize('code',[None,'ordinary','profile_unavailable','source-unavailable',''])
def test_empty_target_status(tmp_path,postgres_url,kind,code):
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'','failed','failure',%s)",(kind,code))
  client.portal.call(setup)
  r=client.get('/api/v3/status/details');assert r.status_code==200,r.text
  assert len(r.json()['problems']['current'])>=1
