"""Reader problem cleanup uses its durable ID, not a duplicate rowid."""
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate, verify


def test_problem_rowid_cutover_preserves_current_and_history(postgres_database_url):
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:63])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('open','a','background','{}',1,NULL,NULL),('old','b','action','{}',2,3,'resolved')")
        before=db.execute('SELECT id,identity,origin,body,created,finished,outcome FROM reader_problems ORDER BY id').fetchall()
        migrate(db,MIGRATIONS)
        assert db.execute('SELECT id,identity,origin,body,created,finished,outcome FROM reader_problems ORDER BY id').fetchall()==before
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='reader_problems' AND column_name='rowid'").fetchone()[0]==0
        verify(db,MIGRATIONS)


def test_problem_rowid_cutover_rollback_restores_everything(postgres_database_url):
    from oko.storage.postgres_schema import MIGRATIONS
    from oko.storage.postgres_migrations import Migration
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:63])
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old','b','action','{}',2,3,'resolved')")
        before=db.execute('SELECT id,rowid,body,finished,outcome FROM reader_problems').fetchall()
        failing=Migration(64,MIGRATIONS[63].name,MIGRATIONS[63].sql+'; SELECT missing_column FROM reader_problems')
        with pytest.raises(psycopg.errors.UndefinedColumn):
            migrate(db,(*MIGRATIONS[:63],failing))
        assert db.execute('SELECT id,rowid,body,finished,outcome FROM reader_problems').fetchall()==before
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==63
        verify(db,MIGRATIONS[:63])


def test_problem_cleanup_is_bounded_and_preserves_current(tmp_path,postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.executemany("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES (%s,%s,'background','{}',1,2,'resolved')",[(f'old-{i:04}',f'old-{i:04}') for i in range(450)])
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('open','open','background','{}',1,NULL,NULL)")
        store.read_versions.advance(now=0,wall=100000)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NOT NULL").fetchone()[0]==250
        assert store.db.execute("SELECT min(id) FROM reader_problems WHERE finished IS NOT NULL").fetchone()[0]=='old-0200'
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='open' AND finished IS NULL").fetchone()
        # An earlier equal-time insertion and a deletion between batches are
        # reconsidered on the next pass; there is no persisted page cursor.
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old-0100x','late','background','{}',1,2,'resolved')")
            store.db.execute("DELETE FROM reader_problems WHERE id='old-0200'")
        store.read_versions.advance(now=0,wall=100000)
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NOT NULL").fetchone()[0]==50
        assert store.db.execute("SELECT min(id) FROM reader_problems WHERE finished IS NOT NULL").fetchone()[0]=='old-0400'
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE id='old-0100x'").fetchone()
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE id='open' AND finished IS NULL").fetchone()
    finally:
        store.db.close()


def test_problem_cleanup_uses_bounded_index_with_unrelated_history(tmp_path,postgres_url):
    from oko.storage.transaction import transaction
    from tests.support.postgres_work import scan_rows
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) SELECT 'recent-'||n,'recent-'||n,'background','{}',1,1000,'resolved' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) SELECT 'old-'||n,'old-'||n,'background','{}',1,2,'resolved' FROM generate_series(1,450) n")
            store.db._db.execute('ANALYZE reader_problems')
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                select_sql="SELECT id FROM reader_problems WHERE finished<=%s ORDER BY finished,id LIMIT 200"
                selected=store.db._db.execute(select_sql,(2,)).fetchall()
                selection=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+select_sql,(2,)).fetchone()[0][0]['Plan']
                delete=store.db._db.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) DELETE FROM reader_problems WHERE id=ANY(%s) AND finished<=%s",([r[0] for r in selected],2)).fetchone()[0][0]['Plan']
                assert scan_rows(selection)+scan_rows(delete)<1200,(selection,delete)
                raise ValueError('rollback')
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE finished=2').fetchone()[0]==450
        assert store.db.execute('SELECT count(*) FROM reader_problems WHERE finished=1000').fetchone()[0]==10000
    finally:
        store.db.close()


def test_problem_cleanup_rechecks_expiry_after_concurrent_update(tmp_path,postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('old','old','background','{}',1,2,'resolved'),('renewed','renewed','background','{}',1,2,'resolved')")
        with psycopg.connect(postgres_url,autocommit=True) as writer:
            updated=[]
            def trace(sql):
                if sql.startswith('DELETE FROM reader_problems WHERE id=ANY') and not updated:
                    writer.execute("UPDATE reader_problems SET finished=200000 WHERE id='renewed'")
                    updated.append(True)
            store.db.set_trace_callback(trace)
            try:
                store.read_versions.advance(now=0,wall=100000)
            finally:
                store.db.set_trace_callback(None)
        assert updated
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE id='old'").fetchone()
        assert store.db.execute("SELECT finished FROM reader_problems WHERE id='renewed'").fetchone()[0]==200000
    finally:
        store.db.close()


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
