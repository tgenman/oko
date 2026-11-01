import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job


def test_theses_profile_real_http_get_read_only_and_etag_stable(tmp_path,postgres_url):
 from fastapi.testclient import TestClient
 from tests.support.app import create_app
 from tests.support.reader import HEADERS
 app=create_app(tmp_path,token='test',database_url=postgres_url,api_only=True,wall_clock=lambda:100)
 with TestClient(app,headers=HEADERS) as client:
  def setup():
   s=app.state.host.require_store();app.state.host.execution.reader_advance=None
   with transaction(s.db):
    s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
    s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('p','rss','p','https://example.test/p')")
    s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
    job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('theses','p','running',1) RETURNING id").fetchone()[0]
    s.jobs.fail(Job(job,'theses','p',1),'failure',code='profile_unavailable')
   return tuple(s.db.execute("SELECT id,body,xmin::text FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchone())
  before=client.portal.call(setup)
  first=client.get('/api/v3/status/details');assert first.status_code==200
  assert any(p['id']==before[0] for p in first.json()['problems']['current'])
  second=client.get('/api/v3/status/details',headers={'If-None-Match':first.headers['etag']});assert second.status_code==304
  after=client.portal.call(lambda:tuple(app.state.host.require_store().db.execute('SELECT id,body,xmin::text FROM reader_problems WHERE id=%s',(before[0],)).fetchone()))
  assert after==before


def test_theses_and_filter_profile_events_remain_independent(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   ids={}
   for kind in ('filter','theses'):
    job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES (%s,'p','running',1) RETURNING id",(kind,)).fetchone()[0]
    s.jobs.fail(Job(job,kind,'p',1),'failure',code='profile_unavailable');ids[kind]=job
  before={r['id']:r['body'] for r in s.db.execute("SELECT id,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(ids['filter']),))}
  assert len(before)==1
  with transaction(s.db):
   s.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(ids['theses'],))
   s.jobs.finish(Job(ids['theses'],'theses','p',1))
  after={r['id']:r['body'] for r in s.db.execute("SELECT id,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(ids['filter']),))}
  assert after==before
  assert not s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(ids['theses']),)).fetchone()
 finally:s.db.close()
