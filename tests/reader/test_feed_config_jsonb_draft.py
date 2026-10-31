import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


def test_config_upgrade_preserves_valid_objects(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88])
  db.execute("INSERT INTO feeds(id,config) VALUES ('a',%s),('b',%s)",(json.dumps({'source_kind':'channel','v':[1,{'x':'текст'}]}),'{}'))
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='feeds' AND column_name='config'").fetchone()[0]=='jsonb'
  assert db.execute("SELECT config FROM feeds WHERE id='a'").fetchone()[0]=={'source_kind':'channel','v':[1,{'x':'текст'}]}
  assert db.execute("SELECT config FROM feeds WHERE id='b'").fetchone()[0]=={}

@pytest.mark.parametrize('invalid',['{broken','[]','null','{"a":1,"a":2}','{"nested":{"a":1,"a":2}}','{"n":1e999999}'])
def test_config_upgrade_rejects_unsafe_values_without_partial_migration(postgres_database_url,invalid):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88])
  db.execute('INSERT INTO feeds(id,config) VALUES (%s,%s)',('bad',invalid))
  with pytest.raises(psycopg.Error):migrate(db,MIGRATIONS)
  assert db.execute('SELECT config FROM feeds WHERE id=%s',('bad',)).fetchone()[0]==invalid
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==88

def test_new_config_default_and_object_constraint(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS)
  db.execute("INSERT INTO feeds(id) VALUES ('defaulted')")
  assert db.execute("SELECT config FROM feeds WHERE id='defaulted'").fetchone()[0]=={}
  with pytest.raises(psycopg.errors.CheckViolation):
   db.execute("UPDATE feeds SET config='[]'::jsonb WHERE id='defaulted'")
  assert db.execute("SELECT config FROM feeds WHERE id='defaulted'").fetchone()[0]=={}

def test_089_external_view_failure_rolls_back_data_and_ledger(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88])
  raw=' { "b": [2, 1], "a": "тест" } '
  db.execute('INSERT INTO feeds(id,config) VALUES (%s,%s)',('f',raw))
  db.execute('CREATE VIEW external_config_view AS SELECT config FROM feeds')
  with pytest.raises(psycopg.errors.FeatureNotSupported):migrate(db,MIGRATIONS)
  assert db.execute("SELECT config FROM feeds WHERE id='f'").fetchone()[0]==raw
  assert db.execute("SELECT config FROM external_config_view").fetchone()[0]==raw
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==88
  verify(db,MIGRATIONS[:88])


def test_089_preserves_column_grants(postgres_database_url):
 from uuid import uuid4
 from psycopg import sql
 role='config_reader_'+uuid4().hex
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88]);schema=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO feeds(id,config) VALUES ('f','{\"url\":\"x\"}')")
  db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  try:
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('GRANT SELECT(config) ON feeds TO {}').format(sql.Identifier(role)))
   migrate(db,MIGRATIONS)
   assert db.execute("SELECT has_column_privilege(%s,'feeds','config','SELECT')",(role,)).fetchone()[0]
   db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
   try:assert db.execute('SELECT config FROM feeds').fetchone()[0]=={'url':'x'}
   finally:db.execute('RESET ROLE')
  finally:
   db.execute(sql.SQL('REVOKE SELECT(config) ON feeds FROM {}').format(sql.Identifier(role)))
   db.execute(sql.SQL('REVOKE USAGE ON SCHEMA {} FROM {}').format(sql.Identifier(schema),sql.Identifier(role)))
   db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))

@pytest.mark.parametrize('number',['1e5000','-1e5000','1e309','1.2e400','1e23','1e3','-0.0','-0e2','9'*4301])
def test_rejects_numbers_that_would_change_or_break_python_decoder(postgres_database_url,number):
 raw='{"source_kind":"channel","extra":{"nested":['+number+']}}'
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88]);db.execute('INSERT INTO feeds(id,config) VALUES (%s,%s)',('f',raw))
  with pytest.raises(psycopg.errors.RaiseException,match='a number would change or become unreadable'):migrate(db,MIGRATIONS)
  assert db.execute("SELECT config FROM feeds WHERE id='f'").fetchone()[0]==raw
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==88


def test_representable_large_integer_and_numeric_text_are_accepted(postgres_database_url):
 number='9'*600
 raw='{"value":'+number+',"text":"1e5000","tiny":1e-5000}'
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88]);db.execute('INSERT INTO feeds(id,config) VALUES (%s,%s)',('f',raw));migrate(db,MIGRATIONS)
  result=db.execute("SELECT config::text FROM feeds WHERE id='f'").fetchone()[0]
  assert json.loads(result)==json.loads(raw)


def test_preserves_float_type_and_negative_subnormal(postgres_database_url):
 import math
 raw='{ "x": 1.25, "y": 1e-3, "zero": -1e-5000, "z": 0.0 }'
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:88]);db.execute("INSERT INTO feeds(id,config) VALUES ('f',%s)",(raw,));migrate(db,MIGRATIONS)
  before=json.loads(raw);after=json.loads(db.execute("SELECT config::text FROM feeds WHERE id='f'").fetchone()[0])
  assert before==after and all(type(before[k]) is type(after[k]) for k in before)
  assert math.copysign(1,before['zero'])==math.copysign(1,after['zero'])==-1
