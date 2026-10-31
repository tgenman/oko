import json,pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction

@pytest.mark.parametrize('transition',['cancel','delete'])
def test_null_source_cancellation_does_not_finish_unrelated_record(tmp_path,postgres_url,transition):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  body=json.dumps(dict(code='source-unavailable',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='foreign')],action=None,operation='producer:status',state='current',next_at=None))
  with transaction(s.db):
   jid=s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch',NULL,'f','failed','failure','source-unavailable') RETURNING id").fetchone()[0]
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-unavailable:f:fetch','background',%s,1)",(body,))
  s.problems.snapshot(100)
  with transaction(s.db):
   if transition=='cancel':s.db.execute("UPDATE jobs SET status='cancelled' WHERE id=%s",(jid,))
   else:s.db.execute('DELETE FROM jobs WHERE id=%s',(jid,))
  s.problems.snapshot(101)
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
 finally:s.db.close()
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  s.problems.snapshot(102)
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
 finally:s.db.close()


def test_null_delete_with_custom_code_status_does_not_crash(tmp_path,postgres_url):
 from fastapi.testclient import TestClient
 from tests.support.app import create_app
 from tests.support.reader import HEADERS
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete',NULL,'failed','failure','other-delete-error')")
  client.portal.call(setup)
  response=client.get('/api/v3/status/details')
  assert response.status_code==200,response.text


def test_many_valid_foreign_ids_do_not_break_status(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   jid=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch',NULL,'failed','failure','ordinary') RETURNING id").fetchone()[0]
   body=json.dumps(dict(code='profile_unavailable',section='action',title='foreign',consequence='saved',next_step='retry',objects=[],action=None,operation='producer:profile',state='current',next_at=None))
   for n in range(100):
    name=f'legacy-null-job:{jid}' + (f':{n}' if n else '')
    s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(name,f'profile:foreign:{n}',body))
  result=s.problems.snapshot(100)
  assert any(p['code']=='legacy-unaddressable' for p in result['current'])
 finally:s.db.close()
