import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import save_keys


def test_source_owned_failure_suppresses_incomplete_and_finish_restores_it(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected','[]')")
            content=json.dumps({'extra':{'_reader':{'full':False,'reason':'partial'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,%s,0)",(content,))
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('fetch','p','done',1) RETURNING id").fetchone()[0]
            save_keys(store.db,['p']);store.incomplete_draft.mark(['p'])
        old=store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]
        with transaction(store.db):
            store.db.execute("UPDATE jobs SET status='pending' WHERE id=%s",(job,))
            claim=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with transaction(store.db):store.jobs.retry(claim,store.clock(),'network',code='source-network')
        assert store.db.execute('SELECT source_event_owned FROM jobs WHERE id=%s',(job,)).fetchone()[0]
        source=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'source_group_identity'='job:source-network:f:fetch' AND finished IS NULL").fetchone()[0]
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(old,)).fetchone()[0] is not None
        with transaction(store.db):
            claim=store.jobs._claim(store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone())
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.jobs.finish(claim)
                raise RuntimeError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(source,)).fetchone()[0] is None
        assert not store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        with transaction(store.db):store.jobs.finish(claim)
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(source,)).fetchone()[0] is not None
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()[0]!=old
    finally:store.db.close()


def test_source_and_incomplete_cursors_both_restore_on_rollback(tmp_path,postgres_url,monkeypatch):
    from oko.storage import source_groups
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        versions=store.read_versions
        versions._source_legacy_gc_cursor='source-before'
        versions._incomplete_due_cursor=(42.,'incomplete-before')
        versions._next_source_legacy_gc=float('-inf')
        monkeypatch.setattr(source_groups,'cleanup_legacy_protected',lambda *_:('source-next',False))
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                versions.advance(now=100,wall=100)
                raise RuntimeError('rollback')
        assert versions._source_legacy_gc_cursor=='source-before'
        assert versions._incomplete_due_cursor==(42.,'incomplete-before')
    finally:store.db.close()
