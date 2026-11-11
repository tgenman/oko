"""Reader problem cleanup uses its durable ID, not a duplicate rowid."""
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate, verify




def test_problem_cleanup_rolls_back_with_caller(tmp_path,postgres_url):
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old','old','background','{}',1,2,'resolved')")
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.read_versions.advance(now=0,wall=100000)
                assert not store.db.execute("SELECT 1 FROM reader_problems WHERE id='old'").fetchone()
                raise ValueError('rollback')
        assert store.db.execute("SELECT finished,outcome FROM reader_problems WHERE id='old'").fetchone()==(2,'resolved')
    finally:
        store.db.close()
