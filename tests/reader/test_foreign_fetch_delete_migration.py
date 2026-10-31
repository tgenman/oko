import psycopg
import pytest
from oko.storage.postgres_migrations import Migration, migrate, verify
from oko.storage.postgres_schema import MIGRATIONS


def test_foreign_fetch_delete_index_migration_rolls_back(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:83])
        migration=MIGRATIONS[83]
        bad=Migration(84,migration.name,migration.sql+'; SELECT nonexistent FROM jobs')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:83],bad))
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==83
        assert db.execute("SELECT to_regclass('reader_foreign_fetch_delete_recovery')").fetchone()[0] is None
        migrate(db,MIGRATIONS)
        verify(db,MIGRATIONS)
        assert db.execute("SELECT to_regclass('reader_foreign_fetch_delete_recovery')").fetchone()[0] is not None
