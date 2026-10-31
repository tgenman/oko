import json
import math
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


@pytest.mark.parametrize('raw',['{}','[]','null','false','"old"','{"kind":"web","future":[1.25,"тест",null]}'])
def test_preserves_all_valid_legacy_json_including_orphans(postgres_database_url,raw):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);db.execute('INSERT INTO telegram_bindings(feed,binding,indexed_role) VALUES (%s,%s,%s)',('orphan',raw,'legacy-role'))
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute("SELECT binding,indexed_role FROM telegram_bindings WHERE feed='orphan'").fetchone()==(json.loads(raw),'legacy-role')


@pytest.mark.parametrize('raw',['{bad','{"a":1,"a":2}','{"n":1e3}','{"n":-0.0}','{"n":1e5000}'])
def test_preserves_unconvertible_or_ambiguous_json_bytes(postgres_database_url,raw):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);db.execute('INSERT INTO telegram_bindings(feed,binding) VALUES (%s,%s)',('orphan',raw))
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute("SELECT binding,binding_raw FROM telegram_bindings WHERE feed='orphan'").fetchone()==(None,raw)


def test_put_binding_preserves_numeric_change_with_raw_fallback(tmp_path,postgres_url):
 from tests.support.storage import Store
 from oko.storage.transaction import transaction
 s=Store(tmp_path,lambda _:None,database_url=postgres_url)
 try:
  with transaction(s.db):s.subscriptions.telegram.put_binding('f',{'future':-0.0})
  result=s.subscriptions.telegram.binding('f')
  assert result and math.copysign(1,result['future'])==-1
  row=s.db.execute("SELECT binding,binding_raw FROM telegram_bindings WHERE feed='f'").fetchone()
  assert row[0] is None and json.loads(row[1])=={'future':0.0}
 finally:s.db.close()


def test_bindings_and_material_read_preserve_empty_and_unknown_values(tmp_path,postgres_url):
 from tests.support.storage import Store
 from oko.storage.transaction import transaction
 from oko.storage.material_read import feed
 s=Store(tmp_path,lambda _:None,database_url=postgres_url)
 try:
  with transaction(s.db):
   s.db.execute("INSERT INTO feeds(id,kind,original_urls) VALUES ('f','telegram',ARRAY[]::text[])")
   s.subscriptions.telegram.put_binding('f',{})
  assert s.subscriptions.telegram.binding('f')=={}
  assert s.subscriptions.telegram.bindings(['f'])=={'f':{}}
  assert feed(s.db,'f')[1]=={}
  original={'kind':'web','unknown':[2.5,{'future':'x'}]}
  with transaction(s.db):s.subscriptions.telegram.put_binding('f',original)
  assert s.subscriptions.telegram.binding('f')==original
  assert feed(s.db,'f')[1]==original
 finally:s.db.close()


def test_concurrent_writer_is_locked_and_bad_value_rejected(postgres_database_url):
 import threading
 with psycopg.connect(postgres_database_url,autocommit=True) as db,psycopg.connect(postgres_database_url,autocommit=False) as writer:
  migrate(db,MIGRATIONS[:91]);db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('f','{}')")
  writer.execute("UPDATE telegram_bindings SET binding=%s WHERE feed='f'",('{"n":1e3}',))
  started=threading.Event();done=threading.Event();errors=[]
  def run():
   started.set()
   try:migrate(db,MIGRATIONS)
   except BaseException as error:errors.append(error)
   finally:done.set()
  thread=threading.Thread(target=run);thread.start();assert started.wait(2) and not done.wait(.2)
  writer.commit();thread.join(10)
  assert done.is_set() and not errors
  assert db.execute("SELECT binding,binding_raw FROM telegram_bindings WHERE feed='f'").fetchone()==(None,'{"n":1e3}')
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==92


def test_builtin_and_temp_shadow_cannot_skip_guard(postgres_database_url):
 from psycopg import sql
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('f',%s)",('{"n":1e3}',))
  db.execute("CREATE TEMP TABLE telegram_bindings(binding text NOT NULL)")
  db.execute(sql.SQL("CREATE FUNCTION {}.json_typeof(json) RETURNS text LANGUAGE sql IMMUTABLE AS 'SELECT ''string''::text'").format(sql.Identifier(schema)))
  try:
   migrate(db,MIGRATIONS)
   assert db.execute(sql.SQL("SELECT binding,binding_raw FROM {}.telegram_bindings WHERE feed='f'").format(sql.Identifier(schema))).fetchone()==(None,'{"n":1e3}')
   assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==92
  finally:db.execute(sql.SQL('DROP FUNCTION {}.json_typeof(json)').format(sql.Identifier(schema)))


def test_external_view_rolls_back_raw_binding_and_ledger(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);raw=' {"x":[1,2]} '
  db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('f',%s)",(raw,))
  db.execute('CREATE VIEW external_binding AS SELECT binding FROM telegram_bindings')
  with pytest.raises(psycopg.errors.FeatureNotSupported):migrate(db,MIGRATIONS)
  assert db.execute("SELECT binding FROM telegram_bindings WHERE feed='f'").fetchone()[0]==raw
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==91


def test_legacy_raw_decoding_retains_python_semantics_and_invalid_error(tmp_path,postgres_database_url):
 from tests.support.storage import Store
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91])
  db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('duplicate',%s),('bad',%s),('signed',%s)",('{"x":1,"x":2}','{invalid','{"x":-0.0}'))
  migrate(db,MIGRATIONS)
 s=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
 try:
  assert s.subscriptions.telegram.binding('duplicate')=={'x':2}
  assert math.copysign(1,s.subscriptions.telegram.binding('signed')['x'])==-1
  with pytest.raises(json.JSONDecodeError):s.subscriptions.telegram.binding('bad')
  assert s.subscriptions.telegram.bindings([])=={}
 finally:s.db.close()


def test_raw_to_native_write_and_rollback(tmp_path,postgres_database_url):
 from tests.support.storage import Store
 from oko.storage.transaction import transaction
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);db.execute("INSERT INTO telegram_bindings(feed,binding,indexed_role) VALUES ('f',%s,'personal')",('{"future":-0.0}',));migrate(db,MIGRATIONS)
 s=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
 try:
  with pytest.raises(RuntimeError):
   with transaction(s.db):
    s.subscriptions.telegram.put_binding('f',{'kind':'client','role':'collection'})
    raise RuntimeError('rollback')
  row=s.db.execute("SELECT binding,binding_raw,indexed_role FROM telegram_bindings WHERE feed='f'").fetchone()
  assert tuple(row)==(None,'{"future":-0.0}','personal')
  with transaction(s.db):s.subscriptions.telegram.put_binding('f',{'kind':'client','role':'collection'})
  row=s.db.execute("SELECT binding,binding_raw,indexed_role FROM telegram_bindings WHERE feed='f'").fetchone()
  assert tuple(row)==({'kind':'client','role':'collection'},None,'collection')
 finally:s.db.close()


def test_preexisting_column_grants_stop_without_changing_data_or_acl(postgres_database_url):
 from uuid import uuid4
 from psycopg import sql
 role='binding_reader_'+uuid4().hex
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('f','{bad')")
  db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  try:
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('GRANT SELECT(feed,binding) ON telegram_bindings TO {}').format(sql.Identifier(role)))
   with pytest.raises(psycopg.errors.RaiseException,match='explicit column privileges need review'):migrate(db,MIGRATIONS)
   assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==91
   assert db.execute("SELECT binding FROM telegram_bindings WHERE feed='f'").fetchone()[0]=='{bad'
   assert db.execute("SELECT has_column_privilege(%s,'telegram_bindings','binding','SELECT')",(role,)).fetchone()[0]
  finally:
   db.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('REVOKE USAGE ON SCHEMA {} FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_table_level_privileges_cover_fallback_and_native_values(postgres_database_url):
 from uuid import uuid4
 from psycopg import sql
 role='binding_table_reader_'+uuid4().hex
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('raw','{bad'),('native','{}')")
  db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  try:
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('GRANT SELECT ON telegram_bindings TO {}').format(sql.Identifier(role)))
   migrate(db,MIGRATIONS)
   db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
   try:assert db.execute('SELECT feed,binding,binding_raw FROM telegram_bindings ORDER BY feed').fetchall()==[('native',{},None),('raw',None,'{bad')]
   finally:db.execute('RESET ROLE')
  finally:
   db.execute(sql.SQL('REVOKE SELECT ON telegram_bindings FROM {}').format(sql.Identifier(role)))
   db.execute(sql.SQL('REVOKE USAGE ON SCHEMA {} FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_missing_owned_relation_does_not_touch_foreign_schema(postgres_database_url):
 from uuid import uuid4
 from psycopg import sql
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);own=db.execute('SELECT current_schema()').fetchone()[0];other='other_'+uuid4().hex
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(other)))
  try:
   db.execute('ALTER TABLE telegram_bindings RENAME TO saved_bindings')
   db.execute(sql.SQL('CREATE TABLE {}.telegram_bindings(feed text, binding text)').format(sql.Identifier(other)))
   db.execute(sql.SQL('SET search_path TO {},{}').format(sql.Identifier(own),sql.Identifier(other)))
   with pytest.raises(psycopg.errors.RaiseException,match='missing from the current schema'):migrate(db,MIGRATIONS)
   assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==91
   assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='telegram_bindings' AND column_name='binding'",(other,)).fetchone()[0]=='text'
  finally:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(own)))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(other)))
