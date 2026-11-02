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
  migrate(db,MIGRATIONS[:92]);verify(db,MIGRATIONS[:92])
  assert db.execute("SELECT binding,indexed_role FROM telegram_bindings WHERE feed='orphan'").fetchone()==(json.loads(raw),'legacy-role')


@pytest.mark.parametrize('raw',['{bad','{"a":1,"a":2}','{"n":1e3}','{"n":-0.0}','{"n":1e5000}'])
def test_preserves_unconvertible_or_ambiguous_json_bytes(postgres_database_url,raw):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:91]);db.execute('INSERT INTO telegram_bindings(feed,binding) VALUES (%s,%s)',('orphan',raw))
  migrate(db,MIGRATIONS[:92]);verify(db,MIGRATIONS[:92])
  assert db.execute("SELECT binding,binding_raw FROM telegram_bindings WHERE feed='orphan'").fetchone()==(None,raw)


def test_raw_to_native_write_and_rollback(tmp_path,postgres_database_url):
 from tests.support.storage import Store
 from oko.storage.transaction import transaction
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS);db.execute("INSERT INTO telegram_bindings(feed,binding_raw,indexed_role) VALUES ('f',%s,'personal')",('{"future":-0.0}',))
 s=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
 try:
  with pytest.raises(RuntimeError):
   with transaction(s.db):
    s.subscriptions.telegram.put_binding('f',{'kind':'web','username':'fixture','preview_url':'https://t.me/s/fixture'})
    raise RuntimeError('rollback')
  row=s.db.execute("SELECT binding,binding_raw,indexed_role FROM telegram_bindings WHERE feed='f'").fetchone()
  assert tuple(row)==(None,'{"future":-0.0}','personal')
  with transaction(s.db):s.subscriptions.telegram.put_binding('f',{'kind':'web','username':'fixture','preview_url':'https://t.me/s/fixture'})
  row=s.db.execute("SELECT binding,binding_raw,indexed_role FROM telegram_bindings WHERE feed='f'").fetchone()
  assert tuple(row)==({'kind':'web','username':'fixture','preview_url':'https://t.me/s/fixture'},None,None)
 finally:s.db.close()
