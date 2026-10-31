import hashlib,json
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage.transaction import transaction

def test_protected_null_occurrence_is_not_adopted_on_explicit_same_code(tmp_path,postgres_url):
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
   s.jobs.retry(Job(job,'download_media','m',1),101,'different',code='work-download_media')
  row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert row and row['body']==old['body'] and row['finished'] is None
 finally:s.db.close()

def test_protected_explicit_occurrence_is_not_closed_on_null_same_code(tmp_path,postgres_url):
 from oko.storage.media_job_problems import previous_candidates
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure','work-download_media') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
   marker='fetch-null-protected:'+hashlib.sha256(old['id'].encode()).hexdigest()
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(marker,json.dumps(['oko-fetch-null-v2',old['id'],old['identity']])))
   previous=previous_candidates(s,job,101)
   s.db.execute("UPDATE jobs SET problem_code=NULL WHERE id=%s",(job,))
   s.problems.sync_media_problem(job,previous,101)
  row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert row and row['body']==old['body'] and row['finished'] is None
 finally:s.db.close()
