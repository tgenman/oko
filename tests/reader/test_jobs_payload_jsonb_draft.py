import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS

def test_payload_upgrade_preserves_unknown_fields_and_array_order(postgres_database_url):
 raw=' { "future": {"text":"雪", "values":[true,2,null]}, "cleanup_feed":"old", "input_revision":"r" } '
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p',%s)",(raw,));migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='jobs' AND column_name='payload'").fetchone()[0]=='jsonb'
  assert db.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]==json.loads(raw)

@pytest.mark.parametrize('raw',['[]','null','{"a":1,"a":2}','{"nested":{"a":1,"a":2}}','{"n":1e23}','{"n":-0.0}','{"n":1e5000}'])
def test_payload_refuses_unsafe_json_without_losing_bytes(postgres_database_url,raw):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p',%s)",(raw,))
  with pytest.raises(psycopg.Error):migrate(db,MIGRATIONS)
  assert db.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]==raw
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==89
  verify(db,MIGRATIONS[:89])


def test_corrupt_payload_if_legacy_index_is_missing_fails_without_loss(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89])
  # The live cleanup expression index itself rejects malformed JSON, so removing
  # it explicitly models a damaged legacy schema rather than a normal writer.
  db.execute('DROP INDEX jobs_cleanup_feed')
  db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p','{bad')")
  with pytest.raises(psycopg.Error):migrate(db,MIGRATIONS)
  assert db.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]=='{bad'
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==89

def test_payload_cleanup_index_default_and_object_constraint(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p',%s)",('{"cleanup_feed":"f","unknown":[1,2]}',))
  migrate(db,MIGRATIONS)
  assert db.execute("SELECT to_regclass('jobs_cleanup_feed')").fetchone()[0] is not None
  assert db.execute("SELECT item FROM jobs WHERE kind='delete' AND payload->>'cleanup_feed'='f'").fetchone()[0]=='p'
  db.execute("INSERT INTO jobs(kind,item) VALUES ('read','default')")
  assert db.execute("SELECT payload FROM jobs WHERE item='default'").fetchone()[0]=={}
  with pytest.raises(psycopg.errors.CheckViolation):db.execute("UPDATE jobs SET payload='[]'::jsonb WHERE item='p'")
  assert db.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]=={'cleanup_feed':'f','unknown':[1,2]}


def test_payload_external_view_failure_leaves_raw_data_and_schema(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);raw=' { "b": [2,1], "a": "雪" } '
  db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p',%s)",(raw,))
  db.execute('CREATE VIEW external_jobs_payload AS SELECT payload FROM jobs')
  with pytest.raises(psycopg.errors.FeatureNotSupported):migrate(db,MIGRATIONS)
  assert db.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]==raw
  assert db.execute('SELECT payload FROM external_jobs_payload').fetchone()[0]==raw
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==89
  verify(db,MIGRATIONS[:89])


def test_payload_column_grant_survives_type_change(postgres_database_url):
 from uuid import uuid4
 from psycopg import sql
 role='payload_reader_'+uuid4().hex
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:89]);schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p','{\"unknown\":\"safe\"}')")
  db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  try:
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('GRANT SELECT(payload) ON jobs TO {}').format(sql.Identifier(role)))
   migrate(db,MIGRATIONS)
   assert db.execute("SELECT has_column_privilege(%s,'jobs','payload','SELECT')",(role,)).fetchone()[0]
   db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
   try:assert db.execute('SELECT payload FROM jobs').fetchone()[0]=={'unknown':'safe'}
   finally:db.execute('RESET ROLE')
  finally:
   db.execute(sql.SQL('REVOKE SELECT(payload) ON jobs FROM {}').format(sql.Identifier(role)))
   db.execute(sql.SQL('REVOKE USAGE ON SCHEMA {} FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

def test_payload_old_reader_lease_blocks_upgrade_then_rejects_old_reader(postgres_database_url):
 from oko.storage.postgres_migrations import schema_lock_key,SchemaMismatch
 with psycopg.connect(postgres_database_url,autocommit=True) as reader,psycopg.connect(postgres_database_url,autocommit=True) as migrator:
  migrate(migrator,MIGRATIONS[:89])
  migrator.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p','{\"unknown\":\"value\"}')")
  key=schema_lock_key(reader);reader.execute('SELECT pg_advisory_lock_shared(%s)',(key,))
  try:
   with pytest.raises(RuntimeError,match='another reader or migration'):migrate(migrator,MIGRATIONS)
   assert migrator.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]=='{"unknown":"value"}'
  finally:reader.execute('SELECT pg_advisory_unlock_shared(%s)',(key,))
  migrate(migrator,MIGRATIONS)
  with pytest.raises(SchemaMismatch,match='newer'):verify(reader,MIGRATIONS[:89])
  verify(reader,MIGRATIONS)

def test_payload_preflight_waits_for_other_writer_then_validates_committed_value(postgres_database_url):
 import threading
 writer=psycopg.connect(postgres_database_url,autocommit=False)
 migrator=psycopg.connect(postgres_database_url,autocommit=True)
 started=threading.Event();done=threading.Event();errors=[]
 try:
  migrate(migrator,MIGRATIONS[:89]);migrator.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete','p','{}')")
  writer.execute("UPDATE jobs SET payload=%s WHERE item='p'",('{"a":1,"a":2}',))
  def run():
   started.set()
   try:migrate(migrator,MIGRATIONS)
   except BaseException as e:errors.append(e)
   finally:done.set()
  thread=threading.Thread(target=run);thread.start();assert started.wait(2) and not done.wait(.2)
  writer.commit();thread.join(10)
  assert done.is_set() and len(errors)==1 and 'ambiguous JSON' in str(errors[0])
  assert migrator.execute("SELECT payload FROM jobs WHERE item='p'").fetchone()[0]=='{"a":1,"a":2}'
  assert migrator.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==89
 finally:writer.close();migrator.close()
