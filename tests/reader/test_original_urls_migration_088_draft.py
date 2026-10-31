import json
from pathlib import Path
import psycopg
import pytest
from oko.storage.postgres_migrations import Migration,migrate,verify,SchemaMismatch
from oko.storage.postgres_schema import MIGRATIONS

TARGET=MIGRATIONS[:88]
BASE=MIGRATIONS[:87]

def seed(db,values):
 for n,value in enumerate(values):
  db.execute('INSERT INTO feeds(id,identity,title,original_urls) VALUES (%s,%s,%s,%s)',(f'f{n}',f'i{n}',f'F{n}',value))

def snapshot(db):return db.execute('SELECT id,original_urls FROM feeds ORDER BY id').fetchall()
def type_name(db):return db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='feeds' AND column_name='original_urls'").fetchone()[0]


def test_legacy_array_values_and_ledger_are_preserved(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE)
  urls=['https://example.test/a?x=1,2','тест/雪','','same','same','quote"slash\\']
  seed(db,[json.dumps(urls,ensure_ascii=False),'[]'])
  migrate(db,TARGET);verify(db,TARGET)
  assert type_name(db)=='ARRAY' and [row[1] for row in snapshot(db)]==[urls,[]]
  assert db.execute('SELECT version FROM oko_schema_migrations ORDER BY version DESC LIMIT 1').fetchone()[0]==88
  with pytest.raises(psycopg.errors.CheckViolation):db.execute("UPDATE feeds SET original_urls=ARRAY[NULL]::text[] WHERE id='f0'")

@pytest.mark.parametrize('invalid',[None,'not-json','null','{}','[1]','[null]','[[]]','[["x"]]','[{}]','["\\u0000"]','["\\ud800"]'])
def test_unrepresentable_legacy_rolls_back_ddl_and_ledger(postgres_database_url,invalid):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE);seed(db,[' [ "safe" ] ',invalid]);before=snapshot(db)
  with pytest.raises(psycopg.errors.RaiseException,match='Cannot migrate feeds.original_urls'):migrate(db,TARGET)
  assert type_name(db)=='text' and snapshot(db)==before
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==87
  verify(db,BASE)


def test_external_view_blocks_ddl_and_preserves_ledger(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE);seed(db,['["url"]'])
  db.execute('CREATE VIEW external_feed_view AS SELECT original_urls FROM feeds')
  with pytest.raises(psycopg.errors.FeatureNotSupported):migrate(db,TARGET)
  assert type_name(db)=='text' and snapshot(db)==[('f0','["url"]')]
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==87
  assert db.execute('SELECT original_urls FROM external_feed_view').fetchone()[0]=='["url"]'


def test_later_migration_failure_rolls_back_conversion_and_both_ledger_entries(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE);seed(db,[' [ "url" ] ']);before=snapshot(db)
  with pytest.raises(psycopg.errors.DivisionByZero):migrate(db,(*TARGET,Migration(89,'synthetic-failure','SELECT 1/0')))
  assert type_name(db)=='text' and snapshot(db)==before
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==87
  verify(db,BASE)


def test_old_reader_schema_lease_blocks_migration_and_old_release_cannot_reopen(postgres_database_url):
 from oko.storage.postgres_migrations import schema_lock_key
 with psycopg.connect(postgres_database_url,autocommit=True) as reader, psycopg.connect(postgres_database_url,autocommit=True) as migrator:
  migrate(migrator,BASE);seed(migrator,['["url"]'])
  key=schema_lock_key(reader);reader.execute('SELECT pg_advisory_lock_shared(%s)',(key,))
  try:
   with pytest.raises(RuntimeError,match='another reader or migration'):migrate(migrator,TARGET)
   assert type_name(migrator)=='text'
  finally:reader.execute('SELECT pg_advisory_unlock_shared(%s)',(key,))
  migrate(migrator,TARGET)
  with pytest.raises(SchemaMismatch,match='newer'):verify(reader,BASE)
  verify(reader,TARGET)


def test_existing_reader_table_privileges_survive_type_change(postgres_database_url):
 from uuid import uuid4
 from psycopg import sql
 role='urls_reader_'+uuid4().hex
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE)
  schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  try:
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON feeds TO {}').format(sql.Identifier(role)))
   migrate(db,TARGET)
   db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
   try:
    db.execute("INSERT INTO feeds(id,identity,title,original_urls) VALUES ('f','i','F',ARRAY['url'])")
    assert db.execute("SELECT original_urls FROM feeds WHERE id='f'").fetchone()[0]==['url']
   finally:db.execute('RESET ROLE')
  finally:
   db.execute(sql.SQL('REVOKE ALL ON feeds FROM {}').format(sql.Identifier(role)))
   db.execute(sql.SQL('REVOKE USAGE ON SCHEMA {} FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_new_readers_and_writers_use_migrated_array_in_store_and_http(tmp_path,postgres_database_url):
 from tests.support.storage import Store
 from oko.subscriptions.subscriptions import AddFeed
 from fastapi.testclient import TestClient
 from tests.support.app import create_app
 from tests.support.reader import HEADERS
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE)
  db.execute("INSERT INTO folders(id,title,position) VALUES ('folder','Folder',1)")
  db.execute("INSERT INTO feeds(id,identity,title,kind,folder_id,original_urls,connection,transport) VALUES ('old','old','Old','rss','folder',%s,'connected','rss')",(json.dumps(['https://old.test/feed','https://old.test/feed']),))
  migrate(db,TARGET)
  assert db.execute("SELECT original_urls FROM feeds WHERE id='old'").fetchone()[0]==['https://old.test/feed','https://old.test/feed']
  migrate(db,MIGRATIONS)
 store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
 try:
  assert next(f for f in store.subscriptions.feeds() if f['id']=='old')['original_urls']==['https://old.test/feed','https://old.test/feed']
  created=store.subscriptions.add_feed(AddFeed(url='https://new.test/feed',title='New',folder='Folder',verification='synthetic'))
  store.publications.configure_feed(created['id'])
  telegram=store.subscriptions.telegram.connect({'kind':'web','username':'synthetic','preview_url':'https://t.me/s/synthetic'},{'title':'Synthetic','username':'synthetic'},'https://t.me/synthetic','Folder',None,False,store.subscriptions)
  assert created['original_urls']==['https://new.test/feed']
  assert telegram['original_urls']==['https://t.me/synthetic']
 finally:store.db.close()
 app=create_app(tmp_path,token='test',api_only=True,database_url=postgres_database_url)
 with TestClient(app,headers=HEADERS) as client:
  response=client.get('/api/v3/subscriptions')
  assert response.status_code==200,response.text
  rows={f['id']:f for f in response.json()['feeds']}
  assert rows['old']['original_urls']==['https://old.test/feed','https://old.test/feed']
  assert rows[created['id']]['original_urls']==['https://new.test/feed']
  assert rows[telegram['id']]['original_urls']==['https://t.me/synthetic']


def test_migrator_without_temp_permission_fails_without_schema_or_ledger_changes():
 import os
 from uuid import uuid4
 from psycopg import sql
 from psycopg.conninfo import make_conninfo
 name='urls_test_'+uuid4().hex
 role='urls_migrator_'+uuid4().hex
 base=os.environ['OKO_TEST_POSTGRES_URL']
 with psycopg.connect(base,autocommit=True) as admin:
  admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  try:
   admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
   try:
    admin.execute(sql.SQL('REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
    with psycopg.connect(make_conninfo(base,dbname=name),autocommit=True) as db:
     migrate(db,BASE);seed(db,['["url"]'])
     db.execute(sql.SQL('ALTER TABLE feeds OWNER TO {}').format(sql.Identifier(role)))
     db.execute(sql.SQL('GRANT USAGE,CREATE ON SCHEMA public TO {}').format(sql.Identifier(role)))
     db.execute(sql.SQL('GRANT SELECT,INSERT ON oko_schema_migrations TO {}').format(sql.Identifier(role)))
     db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
     try:
      with pytest.raises(psycopg.errors.InsufficientPrivilege,match='temporary tables'):migrate(db,TARGET)
     finally:db.execute('RESET ROLE')
     assert type_name(db)=='text' and snapshot(db)==[('f0','["url"]')]
     assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==87
   finally:admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
  finally:admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_upgrade_from_early_historical_schema_preserves_urls(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE[:7])
  expected=['https://t.me/synthetic','https://t.me/synthetic']
  db.execute("INSERT INTO feeds(id,kind,original_urls) VALUES ('old','telegram',%s)",(json.dumps(expected),))
  migrate(db,TARGET);verify(db,TARGET)
  assert db.execute("SELECT original_urls FROM feeds WHERE id='old'").fetchone()[0]==expected


def test_column_grant_survives_original_urls_type_change(postgres_database_url):
 from uuid import uuid4
 from psycopg import sql
 role='urls_column_'+uuid4().hex
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,BASE)
  schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO feeds(id,identity,title,original_urls) VALUES ('f','i','F','[\"url\"]')")
  db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  try:
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('GRANT SELECT(original_urls) ON feeds TO {}').format(sql.Identifier(role)))
   migrate(db,TARGET)
   assert db.execute("SELECT has_column_privilege(%s,'feeds','original_urls','SELECT'),has_table_privilege(%s,'feeds','SELECT')",(role,role)).fetchone()==(True,False)
   db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
   try:assert db.execute('SELECT original_urls FROM feeds').fetchone()[0]==['url']
   finally:db.execute('RESET ROLE')
  finally:
   db.execute(sql.SQL('REVOKE SELECT(original_urls) ON feeds FROM {}').format(sql.Identifier(role)))
   db.execute(sql.SQL('REVOKE USAGE ON SCHEMA {} FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
