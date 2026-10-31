from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import suppressed_materials

def test_get_owned_profile_media_collision_matches_global_objects(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO publications(id) VALUES ('root'),('p'),('owner')")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('p','owner','file','x','file','unavailable')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','p','failed','e','identity-unconfirmed')")
  groups,_,_=s.problems.incomplete_generic_groups(100)
  assert any(obj['id']=='root' for group in groups.values() for obj in group['objects'] if obj['kind']=='material')
  assert suppressed_materials(s,['root'],100)=={'root'}
 finally:s.db.close()

def test_subtitle_admission_collision_matches_actual_admission_owner(tmp_path,postgres_url):
 from oko.storage.youtube_admission_jobs import contributions
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id) VALUES ('root'),('p'),('owner')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','root',true),('f','owner',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('p','owner','youtube:subtitles','x','file','unavailable')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','p','pending','e','youtube-admission') RETURNING id").fetchone()[0]
   s.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
  assert contributions(s,[job])=={job:{'root'}}
  assert suppressed_materials(s,['root'],100)=={'root'}
 finally:s.db.close()
