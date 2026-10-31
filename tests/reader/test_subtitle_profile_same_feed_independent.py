import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.domain.collection import CollectionEffects
from oko.storage import publication_links

@pytest.mark.parametrize(('resource','code'),[('youtube:subtitles','profile_unavailable'),('image','profile_unavailable'),('image',None)])
@pytest.mark.parametrize('rollback',[True,False])
def test_subtitle_profile_unlink_one_same_feed_root_keeps_occurrence(tmp_path,postgres_url,resource,code,rollback):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p',%s,'x','f','pending_download')",(resource,))
   j=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure',%s) RETURNING id",(code,)).fetchone()[0]
   s.problems.sync_media_problem(j,[],100)
  old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(j),)).fetchone()
  assert {o['id'] for o in json.loads(old['body'])['objects']}=={'a','b'}
  def unlink():
   with transaction(s.db):
    e=CollectionEffects();assert publication_links.sync(s,'a',(),e);s.jobs.apply_collection(e)
    if rollback:raise RuntimeError('rollback')
  if rollback:
   with pytest.raises(RuntimeError,match='rollback'):unlink()
  else:unlink()
  rows=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(j),)).fetchall()
  assert len(rows)==1 and rows[0]['id']==old['id']
  assert {o['id'] for o in json.loads(rows[0]['body'])['objects']}==({'a','b'} if rollback else {'b'})
  if rollback:assert s.db.execute("SELECT 1 FROM publication_links WHERE source_publication_id='a' AND target_publication_id='p'").fetchone()
 finally:s.db.close()


@pytest.mark.parametrize(('resource','code'),[('youtube:subtitles','profile_unavailable'),('image','profile_unavailable'),('image',None)])
def test_protected_old_record_stays_untouched_on_unlink(tmp_path,postgres_url,resource,code):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p',%s,'x','f','pending_download')",(resource,))
   j=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure',%s) RETURNING id",(code,)).fetchone()[0]
   s.problems.sync_media_problem(j,[],100)
  old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(j),)).fetchone()
  assert {o['id'] for o in json.loads(old['body'])['objects']}=={'a','b'}
  import hashlib
  with transaction(s.db):
   marker='fetch-null-protected:'+hashlib.sha256(old['id'].encode()).hexdigest()
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-fetch-null-v2',old['id'],old['identity']])))
   e=CollectionEffects();assert publication_links.sync(s,'a',(),e);s.jobs.apply_collection(e)
  saved=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert saved['body']==old['body'] and saved['finished'] is None
 finally:s.db.close()
