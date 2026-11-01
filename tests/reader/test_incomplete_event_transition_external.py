import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction


def seed(store,status):
    with store.db:
        store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
        content=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
        store.db.execute("INSERT INTO publications(id,platform,external_id,url,published,viewed,complete,content,incomplete_class) VALUES ('p','rss','p','https://p.test',1,0,1,%s,0)",(content,))
        store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,generation) VALUES ('fetch','p',%s,NULL,NULL,1) RETURNING id",(status,)).fetchone()[0]
        from oko.storage.incomplete_group_draft import save_keys
        save_keys(store.db,['p'])
        store.incomplete_draft.mark(['p'])
    return job


def current(store):
    return store.db.execute("SELECT id,body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'code'='material-incomplete'").fetchone()


def test_fetch_finish_opens_incomplete_before_status_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'pending')
        assert not any(p['code']=='material-incomplete' for p in store.problems.snapshot(1)['current'])
        with transaction(store.db):
            claimed=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        assert claimed is not None
        with transaction(store.db):store.jobs.finish(claimed)
        row=current(store)
        assert row is not None and json.loads(row['body'])['objects']==[{'kind':'material','id':'p'}]
    finally:store.db.close()


def test_claim_incomplete_event_rolls_back_and_clears_ephemeral_state(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        job=seed(store,'failed');store.problems.snapshot(1)
        before=current(store)
        with store.db:store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
        with pytest.raises(RuntimeError,match='rollback'):
            with transaction(store.db):
                store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
                raise RuntimeError('rollback')
        row=current(store)
        assert tuple(row)==tuple(before)
        assert store.db.execute("SELECT to_regclass('pg_temp.oko_incomplete_work')").fetchone()[0] is None
        assert store.db.execute('SELECT status FROM jobs WHERE id=%s',(job,)).fetchone()[0]=='pending'
    finally:store.db.close()


def test_content_becoming_full_finishes_incomplete_before_status_get(tmp_path,postgres_url):
    from oko.collection.contracts import Material,Publication
    from tests.support.source import StructuredSource
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        seed(store,'done');store.problems.snapshot(1)
        old=current(store)['id']
        with store.db:store.db.execute("UPDATE publications SET source_id='rss:p' WHERE id='p'")
        publication=Publication('p','https://p.test',1)
        material=StructuredSource(None).describe(publication,Material('Full','Complete',full=True))
        with transaction(store.db):
            _,effects=store.collect_material('p',material);store.jobs.apply_collection(effects)
        assert tuple(store.db.execute('SELECT outcome,finished IS NOT NULL FROM reader_problems WHERE id=%s',(old,)).fetchone())==('resolved',True)
    finally:store.db.close()
