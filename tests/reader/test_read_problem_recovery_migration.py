"""The read-problem recovery index is an atomic, non-data migration."""
import psycopg
import pytest
from oko.storage.postgres_migrations import Migration, migrate, verify
from oko.storage.postgres_schema import MIGRATIONS


def test_read_problem_index_migration_preserves_jobs(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','source-connection'),('fetch','other','pending',NULL,NULL)")
        before=db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs ORDER BY id').fetchall()
        migrate(db,MIGRATIONS)
        assert db.execute("SELECT to_regclass('jobs_read_problem_recovery')").fetchone()[0]=='jobs_read_problem_recovery'
        assert db.execute("SELECT to_regclass('reader_read_problem_recovery')").fetchone()[0]=='reader_read_problem_recovery'
        assert db.execute("SELECT to_regclass('telegram_bindings_role_feed')").fetchone()[0]=='telegram_bindings_role_feed'
        assert db.execute("SELECT to_regclass('reader_read_problem_objects_gin')").fetchone()[0]=='reader_read_problem_objects_gin'
        assert db.execute("SELECT to_regclass('jobs_unmanaged_problems')").fetchone()[0]=='jobs_unmanaged_problems'
        assert db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs ORDER BY id').fetchall()==before
        verify(db,MIGRATIONS)


def test_read_problem_index_migration_rolls_back(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','failed','failure','source-connection')")
        failing=Migration(65,MIGRATIONS[64].name,MIGRATIONS[64].sql+'; SELECT missing_column FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):
            migrate(db,(*MIGRATIONS[:64],failing))
        assert db.execute("SELECT to_regclass('jobs_read_problem_recovery')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('reader_read_problem_recovery')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('telegram_bindings_role_feed')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('reader_read_problem_objects_gin')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('jobs_unmanaged_problems')").fetchone()[0] is None
        assert db.execute("SELECT status,error FROM jobs WHERE kind='read' AND item='feed'").fetchone()==('failed','failure')
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==64
        verify(db,MIGRATIONS[:64])
