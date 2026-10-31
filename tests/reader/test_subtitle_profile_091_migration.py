import psycopg
import pytest
from oko.storage.postgres_migrations import migrate, verify, Migration
from oko.storage.postgres_schema import MIGRATIONS

def test_upgrade_indexes_and_history_survive_restart(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:90])
  body=' {"code":"profile_unavailable","operation":"producer:status","objects":[]} '
  db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('p','job:profile_unavailable:f:download_media:x','background',%s,1)",(body,))
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
  assert db.execute("SELECT body FROM reader_problems WHERE id='p'").fetchone()[0]==body
  assert db.execute("SELECT to_regclass('jobs_subtitle_profile_recovery'),to_regclass('reader_subtitle_profile_legacy')").fetchone()==('jobs_subtitle_profile_recovery','reader_subtitle_profile_legacy')
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)

def test_091_failure_rolls_back_indexes_and_ledger(postgres_database_url):
 with psycopg.connect(postgres_database_url,autocommit=True) as db:
  migrate(db,MIGRATIONS[:90])
  broken=Migration(91,'subtitle-profile-problems',MIGRATIONS[90].sql+'\nSELECT 1/0;')
  with pytest.raises(psycopg.Error):migrate(db,(*MIGRATIONS[:90],broken))
  assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==90
  assert db.execute("SELECT to_regclass('jobs_subtitle_profile_recovery')").fetchone()[0] is None
  migrate(db,MIGRATIONS);verify(db,MIGRATIONS)
