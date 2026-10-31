import json,pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction

@pytest.mark.parametrize(('resource','code'),[('youtube:subtitles','profile_unavailable'),('image','profile_unavailable'),('image',None)])
def test_ambiguous_old_alias_not_adopted_during_owner_handoff(tmp_path,postgres_url,resource,code):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('p','rss','p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p',%s,'x','f','pending_download')",(resource,))
   job=s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('download_media','m','a','failed','failure',%s) RETURNING id",(code,)).fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
   old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone();assert old
   body=json.loads(old['body']);body['media_job_identity']=old['identity'];raw=json.dumps(body)
   s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(raw,old['id']))
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('duplicate','job:media-isolated:legacy','background',%s,1)",(raw,))
  s.remove_feed('a')
  for id in (old['id'],'duplicate'):
   row=s.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(id,)).fetchone()
   assert row and row['body']==raw and row['finished'] is None,(id,row and tuple(row))
 finally:s.db.close()
