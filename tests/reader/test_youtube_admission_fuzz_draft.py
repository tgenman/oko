import random
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.youtube_admission_jobs import contributions

@pytest.mark.parametrize('seed',range(5))
def test_fuzz(tmp_path,postgres_url,seed):
 r=random.Random(seed);s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 s.youtube_admission_ready=False
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','Y','default','youtube','rss','connected',ARRAY[]::text[]),('rss','rss','R','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,viewed) SELECT 'p-'||n,0 FROM generate_series(0,29)n")
   for n in range(30):
    f=r.choice(['yt','rss',None])
    if f:s.db.execute('INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES (%s,%s,true)',(f,'p-'+str(n)))
   for n in range(10,30):
    root='p-'+str(r.randrange(0,10))
    s.db.execute('INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',(root,'p-'+str(n)))
   ids=[];codes=[None,'','work-fetch','ordinary','youtube-admission','source-network','processor-config','ai-permission']
   for n in range(30):
    code=r.choice(codes);err=r.choice([None,'failure']);status=r.choice(['pending','running','failed','done']);owned=r.choice([False,True]);fn=r.choice([False,True]);amb=r.choice([False,True]);feed=r.choice(['yt','rss',None])
    row=s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code,source_event_owned,fetch_null_owned,fetch_null_ambiguous) VALUES ('fetch',%s,%s,%s,%s,%s,%s::int::boolean,%s::int::boolean,%s::int::boolean) RETURNING id",('p-'+str(n),feed,status,err,code,owned,fn,amb)).fetchone();ids.append(row[0])
    if n<10:
     mid='m-'+str(n);s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'youtube:subtitles','x','file','pending_download')",(mid,'p-'+str(n)))
     row=s.db.execute("INSERT INTO jobs(kind,item,feed_id,status,error,problem_code,source_event_owned) VALUES ('download_media',%s,%s,%s,%s,%s,%s::int::boolean) RETURNING id",(mid,feed,status,err,code,owned)).fetchone();ids.append(row[0])
   s.youtube.blocked(100,900)
  exp={x['id'] for x in next(p for p in s.problems.candidates(101) if p['code']=='youtube-admission')['objects']}
  actual=set().union(*contributions(s,ids).values())
  assert actual==exp,(seed,actual-exp,exp-actual)
 finally:s.db.close()
