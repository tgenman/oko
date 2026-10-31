import hashlib,json,threading,time
import psycopg
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_concurrent_protection_marker_is_not_missed_by_waiting_update(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url)
 other=psycopg.connect(postgres_url,autocommit=False)
 try:
  identity='job:delete:feed:fetch:item:delete:part'
  body=dict(code='delete',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='item:delete:part')],action=None,operation='producer:status',state='current',next_at=None)
  key='fetch-null-protected:'+hashlib.sha256(b'victim').hexdigest()
  with transaction(s.db):
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status) VALUES ('fetch','item:delete:part','feed','done')")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('victim',%s,'background',%s,1)",(identity,json.dumps(body)))
   s.db.execute("DELETE FROM state WHERE key='fetch-delete-legacy-finished-v1'")
  other.execute("SELECT 1 FROM reader_problems WHERE id='victim' FOR UPDATE")
  started=threading.Event();done=threading.Event();errors=[]
  original=s.problems._finished_foreign_legacy_jobs
  def on_proven(records):
   result=original(records);started.set();return result
  s.problems._finished_foreign_legacy_jobs=on_proven
  def run():
   try:s.problems.recover_finished_foreign_fetch_deletions()
   except BaseException as exc:errors.append(exc)
   finally:done.set()
  worker=threading.Thread(target=run);worker.start();assert started.wait(5)
  with psycopg.connect(postgres_url,autocommit=True) as observer:
   for _ in range(100):
    found=observer.execute("SELECT 1 FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE 'SELECT id FROM reader_problems WHERE id=ANY(%%' ").fetchone()
    if found:break
    time.sleep(.02)
   assert found,'recovery did not reach blocked row lock'
  other.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(key,json.dumps(['oko-fetch-null-v2','victim',identity])))
  other.commit();worker.join(5);assert done.is_set() and not errors
  row=s.db.execute("SELECT finished FROM reader_problems WHERE id='victim'").fetchone()
  assert row['finished'] is None
 finally:other.rollback();other.close();s.db.close()
