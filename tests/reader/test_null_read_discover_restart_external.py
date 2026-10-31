import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
@pytest.mark.parametrize('kind',['read','discover'])
@pytest.mark.parametrize('target',[None,''])
@pytest.mark.parametrize('code',[None,'','ordinary','source-unavailable'])
def test_restart_after_legacy_job(tmp_path,postgres_url,kind,target,code):
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try:
  with transaction(s.db):
   s.db.execute("DELETE FROM state WHERE key IN ('read-problems-recovered-v1','discover-problems-recovered-v1')")
   s.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,'failed','failure',%s)",(kind,target,code))
 finally:s.db.close()
 s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
 try: assert s.problems.snapshot(100)['current'],(kind,target,code)
 finally:s.db.close()
