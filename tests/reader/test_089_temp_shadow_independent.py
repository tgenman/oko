import psycopg
from psycopg import sql
from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS

def test_089_migrates_owned_feeds_not_shadowing_temp_feeds(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88]);own=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO feeds(id,config) VALUES ('f','{\"saved\":true}')")
  db.execute("CREATE TEMP TABLE feeds(config text NOT NULL DEFAULT '{}')")
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='feeds' AND column_name='config'",(own,)).fetchone()[0]=='jsonb'
  assert db.execute(sql.SQL("SELECT config FROM {}.feeds WHERE id='f'").format(sql.Identifier(own))).fetchone()[0]=={'saved':True}


def test_089_restores_search_path_before_next_migration(postgres_database_url):
 from oko.storage.postgres_migrations import Migration
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88]);before=db.execute("SELECT current_setting('search_path')").fetchone()[0]
  db.execute("CREATE TEMP TABLE feeds(config text NOT NULL DEFAULT '{}')")
  next_migration=Migration(90,'record-path',"INSERT INTO state(key,value) VALUES ('path-at-90',current_setting('search_path'))")
  migrate(db,(*MIGRATIONS[:89],next_migration))
  assert db.execute("SELECT value FROM state WHERE key='path-at-90'").fetchone()[0]==before
  assert db.execute("SELECT current_setting('search_path')").fetchone()[0]==before


def test_089_failure_restores_search_path_and_original_data(postgres_database_url):
 import pytest
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88]);before=db.execute("SELECT current_setting('search_path')").fetchone()[0]
  db.execute("INSERT INTO feeds(id,config) VALUES ('f',%s)",('{"n":1e3}',))
  db.execute("CREATE TEMP TABLE feeds(config text NOT NULL DEFAULT '{}')")
  with pytest.raises(psycopg.errors.RaiseException):migrate(db,MIGRATIONS)
  assert db.execute("SELECT current_setting('search_path')").fetchone()[0]==before
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==88
  schema=db.execute('SELECT current_schema()').fetchone()[0]
  assert db.execute(sql.SQL("SELECT config FROM {}.feeds WHERE id='f'").format(sql.Identifier(schema))).fetchone()[0]=='{"n":1e3}'


def test_089_quoted_schema_and_path_restore(postgres_database_url):
 from uuid import uuid4
 name='feeds, "'+uuid4().hex[:12]
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  original=db.execute("SELECT current_setting('search_path')").fetchone()[0]
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
  try:
   db.execute(sql.SQL('SET search_path TO {},pg_catalog').format(sql.Identifier(name)))
   migrate(db,MIGRATIONS[:88]);path=db.execute("SELECT current_setting('search_path')").fetchone()[0]
   db.execute("INSERT INTO feeds(id,config) VALUES ('f','{}')")
   db.execute("CREATE TEMP TABLE feeds(config text NOT NULL DEFAULT '{}')")
   migrate(db,MIGRATIONS)
   assert db.execute("SELECT current_setting('search_path')").fetchone()[0]==path
   assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='feeds' AND column_name='config'",(name,)).fetchone()[0]=='jsonb'
  finally:
   db.execute("SELECT set_config('search_path',%s,false)",(original,))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
