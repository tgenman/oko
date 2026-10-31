import psycopg
import pytest
from oko.storage.postgres_migrations import Migration, migrate, verify
from oko.storage.postgres_schema import MIGRATIONS


def test_delete_problem_migration_and_rollback(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:65])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','item','failed','failure','delete')")
        before=db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs').fetchall()
        failing=Migration(66,MIGRATIONS[65].name,MIGRATIONS[65].sql+'; SELECT missing_column FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:65],failing))
        assert db.execute("SELECT to_regclass('jobs_delete_problem_recovery')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('jobs_unmanaged_problems')").fetchone()[0]=='jobs_unmanaged_problems'
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==65
        assert db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs').fetchall()==before
        migrate(db,MIGRATIONS)
        verify(db,MIGRATIONS)
        assert db.execute('SELECT id,kind,item,status,error,problem_code FROM jobs').fetchall()==before
