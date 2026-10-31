import hashlib,json
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_protected_foreign_occurrence_stays_open(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url)
 try:
  identity='job:delete:feed:fetch:item:delete:part'
  body=dict(code='delete',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('protected',%s,'background',%s,1)",(identity,json.dumps(body)))
   key='fetch-null-protected:'+hashlib.sha256(b'protected').hexdigest()
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-fetch-null-v2','protected',identity])))
   s.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
  s.problems.recover_finished_foreign_fetch_deletions()
  assert s.db.execute("SELECT finished FROM reader_problems WHERE id='protected'").fetchone()[0] is None
 finally:s.db.close()

def test_concurrent_foreign_body_is_not_closed(tmp_path,postgres_url,monkeypatch):
 import threading,psycopg
 s=Store(tmp_path,lambda _:None,database_url=postgres_url)
 other=psycopg.connect(postgres_url,autocommit=True)
 try:
  identity='job:delete:feed:fetch:item:delete:part'
  body=dict(code='delete',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('victim',%s,'background',%s,1)",(identity,json.dumps(body)))
   s.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
  orig=s.problems._finished_foreign_legacy_jobs
  def race(records):
   proven=orig(records)
   other.execute("UPDATE reader_problems SET body=%s WHERE id='victim'",(json.dumps(dict(body,title='foreign',operation='producer:delete')),))
   return proven
  monkeypatch.setattr(s.problems,'_finished_foreign_legacy_jobs',race)
  s.problems.recover_finished_foreign_fetch_deletions()
  row=s.db.execute("SELECT body,finished FROM reader_problems WHERE id='victim'").fetchone()
  assert json.loads(row['body'])['title']=='foreign' and row['finished'] is None
 finally:other.close();s.db.close()

def test_marker_collision_and_gc_id_reuse_are_fail_closed(tmp_path,postgres_url):
 from oko.storage.fetch_job_problems import cleanup_null_protection
 s=Store(tmp_path,lambda _:None,database_url=postgres_url)
 try:
  identity='job:delete:feed:fetch:item:delete:part'
  body=dict(code='delete',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
  key='fetch-null-protected:'+hashlib.sha256(b'protected').hexdigest()
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('protected',%s,'background',%s,1)",(identity,json.dumps(body)))
   s.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,'not-json'))
   s.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
  s.problems.recover_finished_foreign_fetch_deletions()
  assert s.db.execute("SELECT finished FROM reader_problems WHERE id='protected'").fetchone()[0] is None
  with transaction(s.db):
   s.db.execute('UPDATE state SET value=%s WHERE key=%s',(json.dumps(['oko-fetch-null-v2','protected','old-identity']),key))
   s.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
  s.problems.recover_finished_foreign_fetch_deletions()
  assert s.db.execute("SELECT finished FROM reader_problems WHERE id='protected'").fetchone()[0] is None
  with transaction(s.db):
   cleanup_null_protection(s,None)
   assert s.db.execute('SELECT 1 FROM state WHERE key=%s',(key,)).fetchone() is None
   s.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
  s.problems.recover_finished_foreign_fetch_deletions()
  assert s.db.execute("SELECT finished FROM reader_problems WHERE id='protected'").fetchone()[0] is not None
 finally:s.db.close()
