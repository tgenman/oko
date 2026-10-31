from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.domain.collection import CollectionEffects
from oko.storage import publication_links
import json

def test_unlink_one_root_of_same_feed_keeps_occurrence_and_other_object(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   j=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('theses','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.problems.sync_processing_problem(j,[],100)
  old=s.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(j),)).fetchone()
  assert {o['id'] for o in json.loads(old['body'])['objects']}=={'a','b'}
  with transaction(s.db):
   e=CollectionEffects();assert publication_links.sync(s,'a',(),e);s.jobs.apply_collection(e)
  rows=s.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(j),)).fetchall()
  print('before',old['id'],old['body'],'after',[(r['id'],r['body']) for r in rows])
  assert len(rows)==1 and rows[0]['id']==old['id']
  assert [o['id'] for o in json.loads(rows[0]['body'])['objects']]==['b']
 finally:s.db.close()
