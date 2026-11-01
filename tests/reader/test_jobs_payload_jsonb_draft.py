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
