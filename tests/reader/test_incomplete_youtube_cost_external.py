import json
from tests.support.storage import Store
from tests.support.postgres_work import measure_reads
from oko.storage.transaction import transaction

def test_scoped_paused_youtube_does_not_scan_other_publications(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) SELECT 'f','p-'||n,true FROM generate_series(1,10000)n")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p-1','failed','e','media_unavailable')")
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) SELECT 'fetch','p-'||n,'f','pending','e','' FROM generate_series(2,10000)n")
   s.db.execute("UPDATE youtube_access SET blocked_until=200 WHERE id=1")
   s.db._db.execute('ANALYZE jobs');s.db._db.execute('ANALYZE feed_publications')
  with measure_reads(s.db) as cost:s.problems.incomplete_generic_groups(100,['p-1'])
  assert cost.rows<1000,cost
 finally:s.db.close()


def test_scoped_paused_youtube_includes_targeted_pending_owner(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
   s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('fetch','p','f','pending','e','')")
   s.db.execute('UPDATE youtube_access SET blocked_until=200 WHERE id=1')
  groups,_,_=s.problems.incomplete_generic_groups(100,['p'])
  assert groups['youtube:admission']['objects']==[{'kind':'material','id':'p'}]
 finally:s.db.close()
