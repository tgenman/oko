import psycopg
from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


def test_manual_once_upgrade_preserves_observable_truth_value(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:92])
  db.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','none',NULL),('fetch','zero',0),('fetch','one',1)")
  old={r[0]:None if r[1] is None else bool(r[1]) for r in db.execute('SELECT item,manual_once FROM jobs')}
  other_before=dict(db.execute("SELECT id,to_jsonb(j)-'manual_once'-'error'-'problem_code' FROM jobs j").fetchall())
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert dict(db.execute("SELECT id,to_jsonb(j)-'manual_once'-'reason_message'-'reason_code' FROM jobs j").fetchall())==other_before
  assert db.execute("SELECT data_type,is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='jobs' AND column_name='manual_once'").fetchone()==('boolean','YES')
  new=dict(db.execute('SELECT item,manual_once FROM jobs').fetchall())
  assert new==old
  db.execute("INSERT INTO jobs(kind,item) VALUES ('read','new')")
  assert db.execute("SELECT manual_once FROM jobs WHERE item='new'").fetchone()[0] is False


def test_unexpected_manual_integer_fails_without_loss(postgres_database_url):
 import pytest
 for number in (-5,7):
  with psycopg.connect(postgres_database_url,autocommit=True) as db:
   migrate(db,MIGRATIONS[:92])
   db.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','p',%s)",(number,))
   with pytest.raises(psycopg.errors.RaiseException,match='non-boolean legacy integer'):migrate(db,MIGRATIONS)
   assert db.execute("SELECT manual_once FROM jobs WHERE item='p'").fetchone()[0]==number
   assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==92
   verify(db,MIGRATIONS[:92])
   db.execute("DELETE FROM jobs WHERE item='p'")
