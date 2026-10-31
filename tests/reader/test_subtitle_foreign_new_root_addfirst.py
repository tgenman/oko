import json,pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.domain.collection import CollectionEffects
from oko.collection.adapters import ExternalKey,ExternalReference,Platform
from oko.storage import publication_links

@pytest.mark.parametrize(('resource','code'),[('youtube:subtitles','profile_unavailable'),('image','profile_unavailable'),('image',None)])
def test_existing_foreign_new_root_body_is_not_adopted(tmp_path,postgres_url,resource,code):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','a',true),('f','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p',%s,'x','f','pending_download')",(resource,))
   j=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','failed','failure',%s) RETURNING id",(code,)).fetchone()[0]
   s.problems.sync_media_problem(j,[],100)
   old=s.db.execute("SELECT id,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(j),)).fetchone();assert old
   foreign=json.loads(old['body']);foreign['objects']=[dict(kind='material',id='b')];foreign['reason']='foreign evidence';raw=json.dumps(foreign)
   s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(raw,old['id']))
   effects=CollectionEffects();assert publication_links.sync(s,'b',(ExternalReference(ExternalKey(Platform.telegraph,'p'),'https://example.test/p'),),effects)
   assert publication_links.sync(s,'a',(),effects)
   s.jobs.apply_collection(effects)
  row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(old['id'],)).fetchone()
  assert row and row['body']==raw and row['finished'] is None
 finally:s.db.close()
