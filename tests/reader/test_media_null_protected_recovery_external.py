import hashlib,json
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_media_null_recovery_does_not_adopt_protected_legacy(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  ident='job:work-download_media:m:download_media:m'
  body=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')]))
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure')")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",(ident,body))
   marker='fetch-null-protected:'+hashlib.sha256(b'old').hexdigest()
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-fetch-null-v2','old',ident])))
  s.problems.recover_media_null_problems()
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
 finally:s.db.close()


def test_protected_owned_occurrence_is_not_closed_by_finish(tmp_path,postgres_url):
 from oko.storage.job_types import Job
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error) VALUES ('download_media','m','running',1,'failure') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
   marker='fetch-null-protected:'+hashlib.sha256(old['id'].encode()).hexdigest()
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-fetch-null-v2',old['id'],old['identity']])))
   s.jobs.finish(Job(job,'download_media','m',1))
  row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert row and row['body']==old['body'] and row['finished'] is None
 finally:s.db.close()


def test_corrupt_marker_is_not_overwritten_or_adopted(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  ident='job:work-download_media:m:download_media:m'
  body=json.dumps(dict(code='work-download_media',operation='producer:status',objects=[dict(kind='material',id='p')]))
  marker='fetch-null-protected:'+hashlib.sha256(b'old').hexdigest()
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='media-null-problems-recovered-v1'")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old',%s,'background',%s,1)",(ident,body))
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,'foreign'))
  s.problems.recover_media_null_problems()
  assert tuple(s.db.execute("SELECT body,finished FROM reader_problems WHERE id='old'").fetchone())==(body,None)
  assert s.db.execute('SELECT value FROM state WHERE key=%s',(marker,)).fetchone()[0]=='foreign'
  assert s.db.execute("SELECT 1 FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
 finally:s.db.close()


def test_protected_null_occurrence_survives_graph_change(tmp_path,postgres_url):
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
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
   marker='fetch-null-protected:'+hashlib.sha256(old['id'].encode()).hexdigest()
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-fetch-null-v2',old['id'],old['identity']])))
  with transaction(s.db):
   effects=CollectionEffects();assert publication_links.sync(s,'a',(ExternalReference(ExternalKey(Platform.telegraph,'p'),'https://example.test/p'),),effects)
   s.jobs.apply_collection(effects)
  row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert row and row['body']==old['body'] and row['finished'] is None
 finally:s.db.close()
