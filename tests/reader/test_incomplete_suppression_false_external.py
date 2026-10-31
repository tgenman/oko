import json
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import suppressed_materials

def test_finished_stage_error_does_not_suppress(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','p','done','old','stage-error')")
  assert not suppressed_materials(s,['p'],100)
 finally:s.db.close()

def test_orphan_media_job_does_not_suppress_material_with_same_id(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('download_media','p','failed','e','processor-config',true)")
  assert not suppressed_materials(s,['p'],100)
 finally:s.db.close()


def test_media_with_colliding_publication_id_only_suppresses_actual_owner(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p'),('owner')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('p','owner','image','x','file','pending_download')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,processor_visible) VALUES ('download_media','p','failed','e','processor-config',true)")
  assert not suppressed_materials(s,['p'],100)
  assert suppressed_materials(s,['owner'],100)=={'owner'}
 finally:s.db.close()


def test_publication_jobs_still_suppress_own_material_with_media_id_collision(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('p'),('owner')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('p','owner','image','x','file','pending_download')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','e','media_unavailable'),('filter','p','failed','e','stage-error')")
  assert suppressed_materials(s,['p'],100)=={'p'}
  assert not suppressed_materials(s,['owner'],100)
 finally:s.db.close()
