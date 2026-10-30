import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store

@pytest.mark.parametrize('attempt',[{},123,[],True,1.5])
def test_pending_invalid_attempt_is_preserved_and_store_opens(tmp_path,postgres_database_url,attempt):
    body=dict(title='Action',objects=[],action=None,request_attempt=attempt)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_action_results(id,identity,body,started,result) VALUES ('old','old',%s,1,'pending')",(json.dumps(body),))
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert json.loads(store.db.execute("SELECT body FROM reader_action_results WHERE id='old'").fetchone()[0])==body
        current=store.problems.snapshot(2)['current']
        assert len(current)==1 and current[0]['code']=='action-unknown' and current[0]['objects']==[]
    finally:store.db.close()
