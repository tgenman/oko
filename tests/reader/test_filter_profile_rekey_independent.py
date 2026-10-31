import json
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.processing_job_problems import managed_candidates

def test_foreign_new_root_not_adopted_after_preimage_marks_it_protected(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id) VALUES ('a'),('b'),('p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p')")
   j=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.problems.sync_processing_problem(j,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(j),)).fetchone();assert old
   foreign=json.loads(old['body']);foreign['objects']=[dict(kind='material',id='b')];foreign['reason']='foreign evidence'
   raw=json.dumps(foreign)
   s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(raw,old['id']))
   prior=managed_candidates(s,j,101)
   s.db.execute("DELETE FROM publication_links WHERE source_publication_id='a' AND target_publication_id='p'")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('b','p')")
   s.problems.sync_processing_problem(j,prior,101)
  row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert row['body']==raw and row['finished'] is None
 finally:s.db.close()


def test_production_link_writer_preserves_foreign_new_root_body(tmp_path,postgres_url):
 from oko.collection.adapters import ExternalKey,ExternalReference,Platform
 from oko.domain.collection import CollectionEffects
 from oko.storage import publication_links
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p')")
   j=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter','p','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.problems.sync_processing_problem(j,[],100)
   old=s.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'processing_job_id'=%s AND finished IS NULL",(str(j),)).fetchone()
   foreign=json.loads(old['body']);foreign['objects']=[dict(kind='material',id='b')];foreign['reason']='foreign evidence';raw=json.dumps(foreign)
   s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(raw,old['id']))
   effects=CollectionEffects()
   assert publication_links.sync(s,'a',(),effects)
   assert publication_links.sync(s,'b',(ExternalReference(ExternalKey(Platform.telegraph,'p'),'https://example.test/p'),),effects)
   s.jobs.apply_collection(effects)
  row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert row['body']==raw and row['finished'] is None
 finally:s.db.close()
