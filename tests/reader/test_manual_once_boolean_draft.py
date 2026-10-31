import psycopg
from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


def test_manual_once_upgrade_preserves_observable_truth_value(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:92])
  db.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','none',NULL),('fetch','zero',0),('fetch','one',1)")
  old={r[0]:None if r[1] is None else bool(r[1]) for r in db.execute('SELECT item,manual_once FROM jobs')}
  other_before=dict(db.execute("SELECT id,to_jsonb(j)-'manual_once' FROM jobs j").fetchall())
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert dict(db.execute("SELECT id,to_jsonb(j)-'manual_once' FROM jobs j").fetchall())==other_before
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

def test_manual_type_and_data_roll_back_if_later_migration_fails(postgres_database_url):
 import pytest
 from oko.storage.postgres_migrations import Migration
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:92])
  db.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','none',NULL),('fetch','yes',1)")
  with pytest.raises(psycopg.errors.DivisionByZero):migrate(db,(*MIGRATIONS,Migration(94,'synthetic-failure','SELECT 1/0')))
  assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='jobs' AND column_name='manual_once'").fetchone()[0]=='bigint'
  assert dict(db.execute("SELECT item,manual_once FROM jobs").fetchall())=={'none':None,'yes':1}
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==92
  verify(db,MIGRATIONS[:92])


def test_manual_type_change_respects_external_view_and_preserves_data(postgres_database_url):
 import pytest
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:92]);db.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','p',NULL)")
  db.execute('CREATE VIEW external_manual_once AS SELECT manual_once FROM jobs')
  with pytest.raises(psycopg.errors.FeatureNotSupported):migrate(db,MIGRATIONS)
  assert db.execute('SELECT manual_once FROM external_manual_once').fetchone()[0] is None
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==92
  verify(db,MIGRATIONS[:92])


def test_manual_migration_cannot_fall_through_to_other_schema(postgres_database_url):
 import uuid,pytest
 from psycopg import sql
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:92]);own=db.execute('SELECT current_schema()').fetchone()[0];foreign='synthetic_other_'+uuid.uuid4().hex
  db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(foreign)))
  try:
   db.execute('ALTER TABLE jobs RENAME TO saved_jobs')
   db.execute(sql.SQL('CREATE TABLE {}.jobs(manual_once bigint DEFAULT 0)').format(sql.Identifier(foreign)))
   db.execute(sql.SQL('SET search_path TO {},{}').format(sql.Identifier(own),sql.Identifier(foreign)))
   with pytest.raises(psycopg.errors.RaiseException,match='missing from the current schema'):migrate(db,MIGRATIONS)
   assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==92
   assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name='jobs' AND column_name='manual_once'",(foreign,)).fetchone()[0]=='bigint'
  finally:
   db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(own)))
   db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(foreign)))

def test_manual_migration_waits_for_writer_before_validating(postgres_database_url):
 import threading
 writer=psycopg.connect(postgres_database_url,autocommit=False);migrator=psycopg.connect(postgres_database_url,autocommit=True)
 started=threading.Event();done=threading.Event();errors=[]
 try:
  migrate(migrator,MIGRATIONS[:92]);migrator.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','p',0)")
  writer.execute("UPDATE jobs SET manual_once=2 WHERE item='p'")
  def run():
   started.set()
   try:migrate(migrator,MIGRATIONS)
   except BaseException as e:errors.append(e)
   finally:done.set()
  t=threading.Thread(target=run);t.start();assert started.wait(2) and not done.wait(.2)
  writer.commit();t.join(10)
  assert done.is_set() and len(errors)==1 and 'non-boolean legacy integer' in str(errors[0])
  assert migrator.execute("SELECT manual_once FROM jobs WHERE item='p'").fetchone()[0]==2
  verify(migrator,MIGRATIONS[:92])
 finally:writer.close();migrator.close()


def test_manual_migration_ignores_temp_jobs_and_restores_search_path(postgres_database_url):
 from psycopg import sql
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:92]);own=db.execute('SELECT current_schema()').fetchone()[0]
  db.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','p',NULL)")
  db.execute('CREATE TEMP TABLE jobs(manual_once bigint)')
  original=db.execute('SHOW search_path').fetchone()[0]
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute('SHOW search_path').fetchone()[0]==original
  assert db.execute(sql.SQL("SELECT manual_once FROM {}.jobs WHERE item='p'").format(sql.Identifier(own))).fetchone()[0] is None
  assert db.execute("SELECT data_type FROM information_schema.columns WHERE table_schema=pg_my_temp_schema()::regnamespace::text AND table_name='jobs' AND column_name='manual_once'").fetchone()[0]=='bigint'

def test_manual_reader_preserves_unknown_false_and_true(tmp_path,postgres_database_url):
 from tests.support.storage import Store
 from oko.storage.job_types import Job
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:92])
  rows=db.execute("INSERT INTO jobs(kind,item,manual_once) VALUES ('fetch','unknown',NULL),('fetch','no',0),('fetch','yes',1) RETURNING id,item,generation").fetchall()
  migrate(db,MIGRATIONS)
 s=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
 try:
  found={item:s.jobs.manual(Job(identity,'fetch',item,generation)) for identity,item,generation in rows}
  assert found=={'unknown':False,'no':False,'yes':True}
  assert s.db.execute("SELECT manual_once FROM jobs WHERE item='unknown'").fetchone()[0] is None
 finally:s.db.close()
