from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import suppressed_materials
from oko.storage.media_job_problems import managed_candidates

def test_orphan_nonprocessor_event_objects_suppress_matching_incomplete(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','p','failed','e','media_unavailable') RETURNING id").fetchone()[0]
  projected=managed_candidates(s,job,100)
  assert projected and projected[0]['objects']==[{'kind':'material','id':'p'}]
  assert suppressed_materials(s,['p'],100)=={'p'}
 finally:s.db.close()
