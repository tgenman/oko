"""PostgreSQL receipt cleanup and scoped deletion through the reader HTTP boundary."""
import json
from tests.support.storage import Store
from tests.support.postgres_work import measure_reads


def test_receipt_cleanup_is_bounded_preserves_latest_and_rewinds_after_rollback(postgres_url, tmp_path):
    import pytest
    from oko.storage.transaction import transaction

    store = Store(tmp_path, lambda _: None, database_url=postgres_url)
    body = json.dumps({'objects':[]})
    try:
        rows = [('old', 'duplicate', body, 0, 0, 'succeeded')]
        rows += [(f'unique-{n}', f'unique-{n}', body, 0, n + 1, 'succeeded') for n in range(5000)]
        rows += [('new', 'duplicate', body, 0, 5001, 'succeeded')]
        with store.db:
            store.db.executemany('INSERT INTO reader_action_results(id,identity,body,started,completed,result) VALUES (%s,%s,%s,%s,%s,%s)', rows)
        from tests.support.storage import analyze
        from tests.support.postgres_work import scan_rows
        analyze(store.db)
        old_sequence = store.db.execute("SELECT sequence FROM reader_action_results WHERE id='old'").fetchone()[0]
        with pytest.raises(RuntimeError, match='plan rollback'), transaction(store.db):
            sql = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) DELETE FROM reader_action_results r WHERE r.sequence=ANY(%s) AND r.completed<=%s AND EXISTS (SELECT 1 FROM reader_action_results newer WHERE newer.identity=r.identity AND newer.sequence>r.sequence)"
            plan = store.db._db.execute(sql, ([old_sequence], 6000)).fetchone()[0][0]['Plan']
            assert scan_rows(plan) < 1000, plan
            raise RuntimeError('plan rollback')
        with pytest.raises(RuntimeError, match='rollback'), transaction(store.db):
            store.problems.cleanup_receipts(6000)
            assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='old'").fetchone() is None
            raise RuntimeError('rollback synthetic cleanup')
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='old'").fetchone() is not None
        for _ in range(26):
            with measure_reads(store.db) as work, transaction(store.db):
                store.problems.cleanup_receipts(6000)
            assert work.rows < 1000, work
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='old'").fetchone() is None
        assert store.db.execute("SELECT 1 FROM reader_action_results WHERE id='new'").fetchone() is not None
        assert store.db.execute("SELECT count(*) FROM reader_action_results").fetchone()[0] == 5001
    finally:
        store.db.close()
