from tests.support.storage import Store
from oko.storage.incomplete_group_draft import TransactionGroupDraft
from oko.storage.transaction import transaction

def test_timed_youtube_jobs_are_not_materialized_in_one_unbounded_batch(tmp_path,postgres_url,monkeypatch):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO jobs(kind,item,status,error,incomplete_youtube_sensitive) SELECT 'fetch','p-'||n,'pending','failure',true FROM generate_series(1,10000)n")
        tracker=TransactionGroupDraft(store)
        sizes=[]
        monkeypatch.setattr(tracker,'mark_jobs',lambda rows,**_kwargs: sizes.append(len(rows)))
        with transaction(store.db):tracker.mark_youtube_groups(pause=False)
        print('batch sizes:',sizes)
        assert max(sizes,default=0)<=400
    finally:store.db.close()
