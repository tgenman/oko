from pathlib import Path
from tests.support.storage import Store
from tests.support.postgres_work import scan_rows
from oko.storage.transaction import transaction

def test_recovery_does_not_scan_unrelated_jobs_or_problem_history(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='subtitle-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'download_media','foreign-'||n,'failed','e','other' FROM generate_series(1,10000)n")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT 'p-'||n,'job:other:f:download_media:'||n,'background',jsonb_build_object('code','other','operation','producer:status','objects',jsonb_build_array(jsonb_build_object('kind','material','id',n::text)))::text,1 FROM generate_series(1,10000)n")
  s.db._db.execute('ANALYZE jobs');s.db._db.execute('ANALYZE reader_problems')
  trace=[];s.db.set_trace_callback(trace.append)
  try:s.problems.recover_subtitles(profile_only=True)
  finally:s.db.set_trace_callback(None)
  recovery=next(q for q in trace if q.startswith('SELECT m.id AS file_id,j.id,j.subtitle_event_owned'))
  legacy=next(q for q in trace if q.startswith('SELECT p.id,p.identity FROM reader_problems p'))
  for query in (recovery,legacy):
   plan=s.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query).fetchone()[0][0]['Plan']
   assert scan_rows(plan)<1000,plan
 finally:s.db.close()
