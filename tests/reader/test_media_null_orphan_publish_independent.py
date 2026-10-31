import json
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.domain.collection import CollectionEffects


def test_orphan_media_null_when_file_appears_moves_or_finishes_old_occurrence(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
   s.db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','rss','p',%s)",(json.dumps({'blocks':[],'extra':{'_reader':{'title':'P'}}}),))
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','old failure') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
  before=s.db.execute("SELECT id,identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),)).fetchone()
  assert before and before['identity']=='job:work-download_media:m:download_media:m'
  media=dict(id='m',publication_id='p',resource_key='image:1',source_locator='https://example.test/f',name='file',mime_type=None,size_bytes=None,storage_key=None,status='pending_download',reason='',resource_identity=None)
  with transaction(s.db):
   effects=CollectionEffects()
   s.publications.publish('p',({'blocks':[],'extra':{'_reader':{'title':'P'}}},[media]),'x',effects)
   s.jobs.apply_collection(effects)
  active=[dict(r) for r in s.db.execute("SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))]
  assert len(active)==1,active
  assert active[0]['identity']=='job:work-download_media:f:download_media:m',active
 finally:s.db.close()
