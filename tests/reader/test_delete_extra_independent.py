from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job


def test_finished_extra_delete_does_not_close_one_of_ambiguous_duplicates(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','p','running','failure','delete:RuntimeError') RETURNING id").fetchone()[0]
   s.problems.sync_deletion(job,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'deletion_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
   import json
   body=json.loads(old['body']);body['deletion_job_identity']=old['identity']
   s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),old['id']))
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('duplicate','job:delete-isolated:other','background',%s,1)",(json.dumps(body),))
   before={r['id']:(r['body'],r['finished']) for r in s.db.execute("SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)",([old['id'],'duplicate'],))}
   s.jobs.finish(Job(job,'delete','p',0))
  after={r['id']:(r['body'],r['finished']) for r in s.db.execute("SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)",([old['id'],'duplicate'],))}
  assert after==before
 finally:s.db.close()


def test_changed_cause_keeps_ambiguous_previous_and_opens_new(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('delete','p','running',1,'failure','delete:RuntimeError') RETURNING id").fetchone()[0]
   s.problems.sync_deletion(job,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'deletion_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
   body=json.loads(old['body']);body['deletion_job_identity']=old['identity']
   s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),old['id']))
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('duplicate','job:delete-isolated:other','background',%s,1)",(json.dumps(body),))
   before={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
   s.jobs.retry(Job(job,'delete','p',1),101,'different',code='delete:ValueError')
  after={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
  assert after==before
  assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='delete:ValueError' AND body::jsonb->>'deletion_job_id'=%s",(str(job),)).fetchone()
 finally:s.db.close()
