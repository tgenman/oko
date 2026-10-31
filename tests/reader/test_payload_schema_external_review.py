import uuid
import psycopg
from psycopg import sql
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


def test_missing_local_jobs_never_changes_foreign_schema(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89])
  own=db.execute('select current_schema()').fetchone()[0];foreign='foreign_'+uuid.uuid4().hex
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(foreign)))
  try:
   db.execute('ALTER TABLE jobs RENAME TO saved_jobs')
   db.execute(sql.SQL("CREATE TABLE {}.jobs(payload text NOT NULL DEFAULT '{{}}')").format(sql.Identifier(foreign)))
   db.execute(sql.SQL('SET search_path TO {},{}').format(sql.Identifier(own),sql.Identifier(foreign)))
   with pytest.raises(Exception):migrate(db,MIGRATIONS)
   assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==89
   assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='jobs' AND column_name='payload'",(foreign,)).fetchone()[0]=='text'
  finally:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(own)))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(foreign)))


def test_payload_migration_restores_search_path_before_following_migration(postgres_database_url):
 from oko.storage.postgres_migrations import Migration,verify
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);own=db.execute('SELECT current_schema()').fetchone()[0]
  foreign='foreign_'+uuid.uuid4().hex
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(foreign)))
  try:
   db.execute(sql.SQL('CREATE TABLE {}.synthetic_probe(value integer)').format(sql.Identifier(foreign)))
   db.execute(sql.SQL('SET search_path TO {},{}').format(sql.Identifier(own),sql.Identifier(foreign)))
   target=(*MIGRATIONS,Migration(91,'synthetic-next','INSERT INTO synthetic_probe VALUES(1)'))
   migrate(db,target);verify(db,target)
   assert db.execute(sql.SQL('SELECT value FROM {}.synthetic_probe').format(sql.Identifier(foreign))).fetchone()[0]==1
  finally:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(own)))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(foreign)))

def test_payload_migration_works_with_quoted_schema_name(postgres_database_url):
 from oko.storage.postgres_migrations import verify
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  previous=db.execute('SELECT current_schema()').fetchone()[0];own='Synthetic.dot "'+uuid.uuid4().hex
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(own)))
  try:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(own)))
   migrate(db,MIGRATIONS[:89]);db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p','{}')")
   migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
   assert db.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]=={}
  finally:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(previous)))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(own)))

def test_payload_migration_never_alters_shadowing_temp_jobs(postgres_database_url):
 from oko.storage.postgres_migrations import verify
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);own=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p','{\"saved\":true}')")
  db.execute("CREATE TEMP TABLE jobs(payload text NOT NULL DEFAULT '{}')")
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='jobs' AND column_name='payload'",(own,)).fetchone()[0]=='jsonb'
  assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=pg_my_temp_schema()::regnamespace::text AND table_name='jobs' AND column_name='payload'").fetchone()[0]=='text'
  assert db.execute(sql.SQL("SELECT payload FROM {}.jobs WHERE item='p'").format(sql.Identifier(own))).fetchone()[0]=={'saved':True}
