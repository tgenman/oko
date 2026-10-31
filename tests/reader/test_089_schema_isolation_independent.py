import uuid
import psycopg
import pytest
from psycopg import sql
from oko.storage.postgres_migrations import migrate,SchemaMismatch
from oko.storage.postgres_schema import MIGRATIONS

def test_missing_owned_feeds_must_not_migrate_table_in_later_search_path(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88])
  own=db.execute('SELECT current_schema()').fetchone()[0]
  foreign='synthetic_foreign_'+uuid.uuid4().hex
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(foreign)))
  try:
   db.execute(sql.SQL('ALTER TABLE feeds SET SCHEMA {}').format(sql.Identifier(foreign)))
   db.execute(sql.SQL('SET search_path TO {},{}').format(sql.Identifier(own),sql.Identifier(foreign)))
   with pytest.raises(SchemaMismatch,match='requires feeds in the current schema'):migrate(db,MIGRATIONS)
   assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==88
   assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='feeds' AND column_name='config'",(foreign,)).fetchone()[0]=='text'
  finally:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(own)))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(foreign)))

def test_quoted_schema_name_locks_the_correct_table(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  own=db.execute('SELECT current_schema()').fetchone()[0]
  unusual='Synthetic.dot "'+uuid.uuid4().hex
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(unusual)))
  try:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(unusual)))
   migrate(db,MIGRATIONS[:88]);db.execute("INSERT INTO feeds(id,config) VALUES ('f','{}')")
   migrate(db,MIGRATIONS)
   assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='feeds' AND column_name='config'",(unusual,)).fetchone()[0]=='jsonb'
  finally:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(own)))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(unusual)))
