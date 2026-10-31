from tests.support.read_problem import setup
from tests.support.storage import Store


def test_sync_reads_reactivates_cancelled_processor_group(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        setup(store)
        with store.db:
            store.db.execute("UPDATE telegram_read_state SET waiting='waiting' WHERE feed='feed'")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','cancelled','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        list(store.jobs.sync_reads_steps())
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='pending'
        assert store.problems.processor_current() is not None
    finally:store.db.close()
