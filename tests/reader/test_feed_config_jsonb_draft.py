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
