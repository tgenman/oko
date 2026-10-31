from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_existing_null_media_removing_root_closes_its_occurrence(tmp_path,postgres_url):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a','rss','a'),('b','rss','b'),('p','other_web','p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','p'),('b','p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
  before={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
  assert len(before)==2
  s.remove_feed('x')
  after={r['identity']:r['id'] for r in s.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
  assert len(after)==1 and next(iter(after.values()))==before['job:work-download_media:y:download_media:m']
 finally:s.db.close()

def test_existing_null_media_adding_root_creates_new_owner(tmp_path,postgres_url):
 from oko.collection.adapters import ExternalKey,ExternalReference,Platform
 from oko.domain.collection import CollectionEffects
 from oko.storage import publication_links
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('p','telegraph','p','https://example.test/p')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('b','p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
  with transaction(s.db):
   effects=CollectionEffects()
   assert publication_links.sync(s,'a',(ExternalReference(ExternalKey(Platform.telegraph,'p'),'https://example.test/p'),),effects)
   s.jobs.apply_collection(effects)
  after={r['identity'] for r in s.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
  assert after=={'job:work-download_media:x:download_media:m','job:work-download_media:y:download_media:m'}
 finally:s.db.close()

def test_null_media_item_id_collision_adding_root_preserves_legacy_ownership(tmp_path,postgres_url):
 from oko.collection.adapters import ExternalKey,ExternalReference,Platform
 from oko.domain.collection import CollectionEffects
 from oko.storage import publication_links
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id,url) VALUES ('a','rss','a','https://example.test/a'),('b','rss','b','https://example.test/b'),('m','telegraph','m','https://example.test/m'),('owner','other_web','owner','https://example.test/owner')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('b','m')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','owner','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
  with transaction(s.db):
   effects=CollectionEffects()
   assert publication_links.sync(s,'a',(ExternalReference(ExternalKey(Platform.telegraph,'m'),'https://example.test/m'),),effects)
   s.jobs.apply_collection(effects)
  after={r['identity'] for r in s.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))}
  assert after=={'job:work-download_media:x:download_media:m','job:work-download_media:y:download_media:m'}
 finally:s.db.close()

def test_null_media_item_collision_removal_rolls_back_graph_and_history(tmp_path,postgres_url):
 import pytest
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('x','x','X','default','rss','rss','connected',ARRAY[]::text[]),('y','y','Y','default','rss','rss','connected',ARRAY[]::text[])")
   s.db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('a','rss','a'),('b','rss','b'),('m','telegraph','m'),('owner','other_web','owner')")
   s.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('x','a',true),('y','b',true)")
   s.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('a','m'),('b','m')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','owner','image','x','f','pending_download')")
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error) VALUES ('download_media','m','failed','failure') RETURNING id").fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
  before=[tuple(r) for r in s.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s ORDER BY id",(str(job),))]
  with pytest.raises(RuntimeError,match='rollback'):
   with transaction(s.db):
    s.remove_feed('x')
    raise RuntimeError('rollback')
  after=[tuple(r) for r in s.db.execute("SELECT id,identity,body,finished FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s ORDER BY id",(str(job),))]
  assert before==after
  s.remove_feed('x')
  active=[r['identity'] for r in s.db.execute("SELECT identity FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job),))]
  assert active==['job:work-download_media:y:download_media:m']
 finally:s.db.close()

def test_real_material_replacement_closes_null_media_and_rolls_back(tmp_path,postgres_url):
 import pytest
 from oko.collection.contracts import Material,Publication
 from tests.support.source import StructuredSource,publication_packet
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  s.ensure_demo();publication=Publication('p','https://example.test/p',1)
  with transaction(s.db):
   effects=s.collect_discovery([publication_packet(publication)],'{}');s.jobs.apply_collection(effects)
  item=s.db.execute("SELECT id FROM publications WHERE url='https://example.test/p'").fetchone()[0]
  file='00000000-0000-4000-8000-000000000001'
  with transaction(s.db):
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES (%s,%s,'image:1','https://example.test/file','file','unavailable')",(file,item))
   job=s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media',%s,'failed','failure',NULL) RETURNING id",(file,)).fetchone()[0]
   s.problems.sync_media_problem(job,[],100)
   old=s.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()[0]
  described=StructuredSource(None).describe(publication,Material('New','New text'))
  with pytest.raises(RuntimeError,match='rollback'):
   with transaction(s.db):
    _,effects=s.collect_material(item,described);s.jobs.apply_collection(effects)
    assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
    raise RuntimeError('rollback')
  assert s.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is None
  with transaction(s.db):_,effects=s.collect_material(item,described);s.jobs.apply_collection(effects)
  assert s.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()[0]=='irrelevant'
 finally:s.db.close()
