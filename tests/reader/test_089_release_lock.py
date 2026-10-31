import psycopg
import pytest
from oko.storage.postgres_migrations import migrate, verify
from oko.storage.postgres_schema import MIGRATIONS


class Intercept:
 def __init__(self,db,callback):self.db,self.callback=db,callback
 def __getattr__(self,name):return getattr(self.db,name)
 def execute(self,query,*args,**kwargs):
  if query==MIGRATIONS[88].sql:self.callback()
  return self.db.execute(query,*args,**kwargs)


def test_089_runner_excludes_external_writer_before_preflight(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db, psycopg.connect(postgres_database_url,autocommit=True) as external:
  migrate(db,MIGRATIONS[:88]);db.execute("INSERT INTO feeds(id,config) VALUES ('f','{}')")
  external.execute("SET lock_timeout='200ms'")
  def concurrent_write():
   with pytest.raises(psycopg.errors.LockNotAvailable):external.execute("UPDATE feeds SET config=%s WHERE id='f'",('{"n":1e3}',))
  migrate(Intercept(db,concurrent_write),MIGRATIONS)
  verify(db,MIGRATIONS)
  assert db.execute("SELECT config FROM feeds WHERE id='f'").fetchone()[0]=={}


def test_089_runner_failure_rolls_back_and_releases_lock(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db, psycopg.connect(postgres_database_url,autocommit=True) as external:
  migrate(db,MIGRATIONS[:88]);db.execute("INSERT INTO feeds(id,config) VALUES ('f','{}')")
  def fail():raise RuntimeError('interrupted before 089')
  with pytest.raises(RuntimeError,match='interrupted before 089'):migrate(Intercept(db,fail),MIGRATIONS)
  verify(db,MIGRATIONS[:88])
  external.execute("SET lock_timeout='200ms'")
  external.execute("UPDATE feeds SET config=%s WHERE id='f'",('{"value":1.0}',))
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)


def test_089_already_applied_does_not_take_table_lock(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db, psycopg.connect(postgres_database_url,autocommit=True) as other:
  migrate(db,MIGRATIONS)
  with other.transaction():
   other.execute('LOCK TABLE feeds IN ACCESS EXCLUSIVE MODE')
   db.execute("SET lock_timeout='200ms'")
   migrate(db,MIGRATIONS)
  verify(db,MIGRATIONS)


def test_089_active_external_transaction_makes_runner_wait_or_fail_safely(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db, psycopg.connect(postgres_database_url,autocommit=True) as external:
  migrate(db,MIGRATIONS[:88]);db.execute("INSERT INTO feeds(id,config) VALUES ('f','{}')")
  db.execute("SET lock_timeout='200ms'")
  with external.transaction():
   external.execute("UPDATE feeds SET config=%s WHERE id='f'",('{"n":1e3}',))
   with pytest.raises(psycopg.errors.LockNotAvailable):migrate(db,MIGRATIONS)
  verify(db,MIGRATIONS[:88])
  with pytest.raises(psycopg.errors.RaiseException,match='a number would change'):migrate(db,MIGRATIONS)
  verify(db,MIGRATIONS[:88])
  assert db.execute("SELECT config FROM feeds WHERE id='f'").fetchone()[0]=='{"n":1e3}'


def test_089_failure_after_alter_rolls_back_schema_data_and_ledger(postgres_database_url):
 class FailLedger(Intercept):
  def execute(self,query,*args,**kwargs):
   if isinstance(query,str) and query.startswith('INSERT INTO oko_schema_migrations') and args and args[0][0]==89:raise RuntimeError('ledger fault')
   return self.db.execute(query,*args,**kwargs)
 with psycopg.connect(postgres_database_url,autocommit=True) as db, psycopg.connect(postgres_database_url,autocommit=True) as external:
  migrate(db,MIGRATIONS[:88]);raw=' {"n":1.5} ';db.execute("INSERT INTO feeds(id,config) VALUES ('f',%s)",(raw,))
  with pytest.raises(RuntimeError,match='ledger fault'):migrate(FailLedger(db,lambda:None),MIGRATIONS)
  verify(db,MIGRATIONS[:88]);assert db.execute("SELECT config FROM feeds WHERE id='f'").fetchone()[0]==raw
  external.execute("SET lock_timeout='200ms'");external.execute("UPDATE feeds SET config=%s WHERE id='f'",(raw,))
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)


def test_089_lock_is_held_after_alter_until_ledger_commit(postgres_database_url):
 class BeforeLedger(Intercept):
  def execute(self,query,*args,**kwargs):
   if isinstance(query,str) and query.startswith('INSERT INTO oko_schema_migrations') and args and args[0][0]==89:self.callback()
   return self.db.execute(query,*args,**kwargs)
 with psycopg.connect(postgres_database_url,autocommit=True) as db, psycopg.connect(postgres_database_url,autocommit=True) as external:
  migrate(db,MIGRATIONS[:88]);db.execute("INSERT INTO feeds(id,config) VALUES ('f','{}')")
  external.execute("SET lock_timeout='200ms'")
  def concurrent_write():
   with pytest.raises(psycopg.errors.LockNotAvailable):external.execute("UPDATE feeds SET config='{}' WHERE id='f'")
  migrate(BeforeLedger(db,concurrent_write),MIGRATIONS)
  external.execute("UPDATE feeds SET config='{}' WHERE id='f'")
