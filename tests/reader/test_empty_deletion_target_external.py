import pytest
from fastapi.testclient import TestClient
from tests.support.app import create_app
from tests.support.reader import HEADERS
from oko.storage.transaction import transaction
@pytest.mark.parametrize('kind',['delete','feed-delete'])
@pytest.mark.parametrize('target',[None,''])
@pytest.mark.parametrize('code',['other-delete-error','source-unavailable'])
def test_unaddressable_deletion_is_safe_generic(tmp_path,postgres_url,kind,target,code):
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):return s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,'failed','failure',%s) RETURNING id",(kind,target,code)).fetchone()[0]
  jid=client.portal.call(setup);r=client.get('/api/v3/status/details');assert r.status_code==200,r.text
  p=next(p for p in r.json()['problems']['current'] if p['id']==f'legacy-null-job:{jid}')
  assert p['affected']==[] and p['recovery']==[]
