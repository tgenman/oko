import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job


@pytest.mark.parametrize('code',['identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'])
def test_theses_profile_failure_is_saved_before_get(tmp_path,postgres_url,code):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('theses','p','running',1) RETURNING id").fetchone()[0]
   s.jobs.fail(Job(job,'theses','p',1),'failure',code=code)
  row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchone()
  assert row
 finally:s.db.close()


def test_theses_profile_suppression_keeps_other_shared_root_id(tmp_path,postgres_url):
 from oko.services.connections import ConnectionService
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','telegram','telegram','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
   s.subscriptions.telegram.put_binding('x',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
   s.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('theses','p','running',1) RETURNING id").fetchone()[0]
   s.jobs.fail(Job(job,'theses','p',1),'failure',code='profile_unavailable')
  before={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),))}
  assert len(before)==2
  assert not ConnectionService(lambda:s).checked('personal',1,202,2)
  after={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),))}
  assert after=={'job:profile_unavailable:y:theses:p':before['job:profile_unavailable:y:theses:p']}
 finally:s.db.close()


def test_theses_profile_recovery_preserves_isolated_status_id(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  canonical='job:profile_unavailable:p:theses:p'
  body=json.dumps(dict(code='profile_unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:theses',state='current',next_at=None,status_job_identity=canonical,status_job_target='p'))
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='theses-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:status-isolated:old','background',%s,1)",(body,))
  s.problems.recover_theses_profile_problems()
  row=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchone()
  assert row and row[0]=='old'
 finally:s.db.close()


def test_theses_profile_adds_shared_root_without_get(tmp_path,postgres_url):
 from oko.collection.adapters import ExternalKey,ExternalReference,Platform
 from oko.domain.collection import CollectionEffects
 from oko.storage import publication_links
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('b','p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('theses','p','running',1) RETURNING id").fetchone()[0]
   s.jobs.fail(Job(job,'theses','p',1),'failure',code='profile_unavailable')
  with transaction(s.db):
   effects=CollectionEffects();assert publication_links.sync(s,'a',(ExternalReference(ExternalKey(Platform.telegraph,'p'),'https://example.test/p'),),effects)
   s.jobs.apply_collection(effects)
  after={r['identity'] for r in s.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),))}
  assert after=={'job:profile_unavailable:x:theses:p','job:profile_unavailable:y:theses:p'}
 finally:s.db.close()


def test_ambiguous_profile_previous_is_preserved_when_cause_changes(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('theses','p','running',1,'failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.problems.sync_processing_problem(job,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
   body=json.loads(old['body']);body['processing_job_identity']=old['identity']
   s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),old['id']))
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('duplicate','job:processing-isolated:other','background',%s,1)",(json.dumps(body),))
   before={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
   s.jobs.fail(Job(job,'theses','p',1),'different',code='processing-failed')
  after={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
  assert after==before
  assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='processing-failed' AND body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchone()
 finally:s.db.close()


def test_theses_profile_recovery_preserves_protected_foreign_body(tmp_path,postgres_url):
 import hashlib,json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  ident='job:profile_unavailable:p:theses:p'
  body=json.dumps(dict(code='profile_unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='theses-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable')")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",(ident,body))
   marker='fetch-null-protected:'+hashlib.sha256(b'old').hexdigest()
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-fetch-null-v2','old',ident])))
  s.problems.recover_theses_profile_problems();s.problems.snapshot(100)
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
 finally:s.db.close()


def test_theses_profile_does_not_adopt_status_alias_owned_by_other_job(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  canonical='job:profile_unavailable:p:theses:p'
  body=json.dumps(dict(code='profile_unavailable',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:theses',state='current',next_at=None,status_job_identity=canonical,status_job_target='p',processing_job_id='999',processing_kind='theses'))
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='theses-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable')")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:status-isolated:foreign','background',%s,1)",(body,))
  s.problems.recover_theses_profile_problems()
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(body,None)
 finally:s.db.close()


def test_theses_recovery_preserves_unsuppressed_legacy_shared_root_id(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='theses-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','telegram','telegram','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'identity_mismatch',1)")
   s.subscriptions.telegram.put_binding('x',dict(kind='client',role='personal',account_user_id='101',peer_id='123'));s.problems.sync_profiles(100)
   s.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable')")
   for feed,item in (('x','a'),('y','b')):
    body=json.dumps(dict(code='profile_unavailable',section='object',title='legacy',consequence='saved',next_step='retry',objects=[dict(kind='material',id=item)],action=None,operation='producer:status',state='current',next_at=None))
    s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(f'old-{feed}',f'job:profile_unavailable:{feed}:theses:p',body))
  s.problems.recover_theses_profile_problems()
  assert s.db.execute("SELECT finished FROM reader_problems WHERE id='old-x'").fetchone()[0] is not None
  row=s.db.execute("SELECT id FROM reader_problems WHERE identity='job:profile_unavailable:y:theses:p' AND finished IS NULL").fetchone()
  assert row and row[0]=='old-y'
 finally:s.db.close()


def test_theses_profile_failure_rolls_back_job_and_problem(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('theses','p','running',1) RETURNING id").fetchone()[0]
  with pytest.raises(RuntimeError,match='rollback'):
   with transaction(s.db):
    s.jobs.fail(Job(job,'theses','p',1),'failure',code='profile_unavailable')
    assert s.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
    raise RuntimeError('rollback')
  row=s.db.execute('SELECT status,error,problem_code,identity_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()
  assert tuple(row)==('running',None,None,False)
  assert not s.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchone()
 finally:s.db.close()


def test_ambiguous_kind_status_alias_is_preserved_not_adopted(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  feed='x:delete:q';canonical='job:profile_unavailable:'+feed+':theses:p'
  body=json.dumps(dict(code='profile_unavailable',section='object',title='foreign',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,status_job_identity=canonical,status_job_target='p'))
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='theses-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,'F','default','rss','rss','connected',ARRAY[]::text[])",(feed,feed))
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES (%s,'p',true)",(feed,))
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign','job:status-isolated:foreign','background',%s,1)",(body,))
  s.problems.recover_theses_profile_problems()
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='foreign'").fetchone())==(body,None)
  assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchone()
 finally:s.db.close()


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


def test_removing_one_shared_root_keeps_other_theses_profile_id(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a','rss','a'),('b','rss','b'),('p','other_web','p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.problems.sync_processing_problem(job,[],100)
  before={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),))}
  assert len(before)==2
  s.remove_feed('x')
  after={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),))}
  assert after=={'job:profile_unavailable:y:theses:p':before['job:profile_unavailable:y:theses:p']}
 finally:s.db.close()


def test_unlink_one_shared_root_keeps_other_theses_profile_id(tmp_path,postgres_url):
 from oko.domain.collection import CollectionEffects
 from oko.storage import publication_links
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.problems.sync_processing_problem(job,[],100)
  before={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),))}
  assert len(before)==2
  with transaction(s.db):
   effects=CollectionEffects();assert publication_links.sync(s,'a',(),effects);s.jobs.apply_collection(effects)
  after={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),))}
  assert after=={'job:profile_unavailable:y:theses:p':before['job:profile_unavailable:y:theses:p']}
 finally:s.db.close()


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


def test_theses_recovery_resumes_after_interrupted_batch(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='theses-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
  original=s.fault
  def fault(name):
   if name=='theses_profile_problems_recovery_batch':raise RuntimeError('interrupted')
  s.fault=fault
  with pytest.raises(RuntimeError,match='interrupted'):s.problems.recover_theses_profile_problems()
  before=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchone()[0]
  s.fault=original
  s.problems.recover_theses_profile_problems()
  after=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'processing_job_id'=%s",(str(job),)).fetchall()
  assert [r[0] for r in after]==[before]
 finally:s.db.close()
