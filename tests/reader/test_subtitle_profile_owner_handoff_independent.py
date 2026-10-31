import json
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_recovery_preserves_legacy_occurrence_when_owner_changed_without_suppression(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  legacy=json.dumps(dict(code='profile_unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='subtitle-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('old','old','O','default','rss','rss','connected',ARRAY[]::text[]),('new','new','N','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('new','p',true)")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
   j=s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code) VALUES ('download_media','m','old','failed','failure','profile_unavailable') RETURNING id").fetchone()[0]
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old-id','job:profile_unavailable:old:download_media:m','background',%s,1)",(legacy,))
  s.problems.recover_subtitles(profile_only=True)
  row=s.db.execute("SELECT id,identity FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(j),)).fetchone()
  assert row and row['id']=='old-id',row
 finally:s.db.close()
