import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store


def test_invalid_orphan_binding_preserved_through_upgrade(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('orphan','{invalid')")
    # The orphan is outside the selected set of existing feeds.
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        assert db.execute('SELECT feed FROM telegram_bindings WHERE feed=ANY(%s)',([],)).fetchall()==[]
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT binding_raw FROM telegram_bindings WHERE feed='orphan'").fetchone()[0]=='{invalid'


def test_invalid_problem_json_before_migration(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        with pytest.raises(psycopg.errors.InvalidTextRepresentation):
            db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','job:source-x:feed:read','background','{invalid',1)")


@pytest.mark.parametrize('body',[None, [], {'objects':None,'code':'source-connection','operation':'producer:read'}, {'objects':[None],'code':'source-connection','operation':'producer:read'}])
def test_valid_but_nonconforming_problem_json_upgrade(tmp_path,postgres_database_url,body):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('bad','job:source-connection:feed:read','background',%s,1)",(json.dumps(body),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        row=store.db.execute("SELECT body,finished FROM reader_problems WHERE id='bad'").fetchone()
        assert json.loads(row['body'])==body and row['finished'] is None
    finally:store.db.close()


def test_profile_selection_skips_invalid_orphan_and_uses_role_index(tmp_path,postgres_database_url):
    from tests.support.postgres_work import scan_rows
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','telegram','telegram','connected','[]')")
        db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
        db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('feed','{\"kind\":\"client\",\"role\":\"personal\"}'),('orphan','{invalid')")
        db.execute("INSERT INTO telegram_bindings(feed,binding) SELECT 'foreign-'||n,'{\"kind\":\"client\",\"role\":\"collection\"}' FROM generate_series(1,10000) n")
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        store.db._db.execute('ANALYZE telegram_bindings')
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:
            assert not store.check_telegram_profile('personal',1,202,2)
        finally:store.db.set_trace_callback(None)
        query=next(q for q in statements if q.startswith('SELECT feed FROM telegram_bindings WHERE'))
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(['personal'],['personal'])).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<1000,plan
        assert store.db.execute("SELECT binding_raw FROM telegram_bindings WHERE feed='orphan'").fetchone()[0]=='{invalid'
    finally:store.db.close()
