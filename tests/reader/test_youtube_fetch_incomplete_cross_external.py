import json
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import save_keys


def test_youtube_null_fetch_timer_suppresses_incomplete_only_when_visible(tmp_path,postgres_url):
 clock=[100.]
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
   content=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
   s.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,%s,0)",(content,))
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('fetch','p','failed','waiting') RETURNING id").fetchone()[0]
   save_keys(s.db,['p']);s.problems.sync_fetch(job,[],100);s.incomplete_draft.mark(['p'])
  assert not any(p['code']=='material-incomplete' for p in s.problems.snapshot(100)['current'])
  s.youtube.start(100)
  during=s.problems.snapshot(105)
  assert not any(p['code']=='work-fetch' for p in during['current'])
  assert any(p['code']=='material-incomplete' for p in during['current'])
  clock[0]=111
  after=s.problems.snapshot(111)
  assert any(p['code']=='work-fetch' for p in after['current'])
  assert not any(p['code']=='material-incomplete' for p in after['current'])
 finally:s.db.close()
