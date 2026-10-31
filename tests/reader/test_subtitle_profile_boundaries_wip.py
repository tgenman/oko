import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction

@pytest.mark.parametrize('job_id',[0,-1,-9223372036854775808])
def test_recovery_handles_nonpositive_job_ids(tmp_path,postgres_url,job_id):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='subtitle-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO publications(id) VALUES ('p')")
   s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','x','f','pending_download')")
   s.db.execute("INSERT INTO jobs(id,kind,item,status,error,problem_code) VALUES (%s,'download_media','m','failed','failure','profile_unavailable')",(job_id,))
  s.problems.recover_subtitles(profile_only=True)
  assert s.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'media_job_id'=%s",(str(job_id),)).fetchone()
 finally:s.db.close()


def test_legacy_empty_problem_id_is_protected(tmp_path,postgres_url):
 import json
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  body=json.dumps(dict(code='profile_unavailable',section='object',title='old',consequence='saved',next_step='retry',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None))
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key='subtitle-profile-problems-recovered-v1'")
   s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('','job:profile_unavailable:feed:download_media:missing','background',%s,1)",(body,))
  s.problems.recover_subtitles(profile_only=True)
  before=s.db.execute("SELECT body,finished FROM reader_problems WHERE id=''").fetchone()
  s.problems.snapshot(101)
  after=s.db.execute("SELECT body,finished FROM reader_problems WHERE id=''").fetchone()
  assert tuple(before)==tuple(after) and after['finished'] is None
 finally:s.db.close()
