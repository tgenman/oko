import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.collection.adapters import AdapterIssue,AdapterErrorKind

CODES=['identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch']


@pytest.mark.parametrize('code',CODES)
def test_profile_suppression_and_reopening_on_events(tmp_path,postgres_url,code):
 from oko.services.connections import ConnectionService
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','telegram','telegram','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
   s.subscriptions.telegram.put_binding('f',dict(kind='client',role='personal',account_user_id='101',peer_id='123'))
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('download_media','m','running',1) RETURNING id").fetchone()[0]
   s.jobs.fail_media(Job(job,'download_media','m',1),AdapterIssue(code,'failure',AdapterErrorKind.temporary))
  old=s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()[0]
  service=ConnectionService(lambda:s)
  assert not service.checked('personal',1,202,2)
  assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
  assert service.checked('personal',1,101,3)
  assert s.db.execute("SELECT id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
 finally:s.db.close()
