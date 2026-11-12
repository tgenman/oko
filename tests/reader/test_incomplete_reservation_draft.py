from tests.support.youtube_action import youtube_action
import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.incomplete_group_draft import sync_groups_time_batch,virtual_due_groups


def test_pause_reservation_stable_through_deadline_tick_and_restart(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
            content=json.dumps({'blocks':[],'extra':{'youtube':{'captions':'pending'}}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,content,status) VALUES ('p','youtube','p',%s,'pending_collection')",(content,))
            store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
            store.db.execute("UPDATE youtube_access SET blocked_until=200 WHERE id=1")
        with transaction(store.db):sync_groups_time_batch(store,['incomplete:f:p'],100)
        reserved=store.db.execute("SELECT id FROM reader_problems WHERE body::jsonb->>'operation'='producer:incomplete-reservation'").fetchone()
        assert reserved is not None and virtual_due_groups(store,100)==[]
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone() is None
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        clock[0]=201
        virtual=virtual_due_groups(store,201)
        assert len(virtual)==1 and virtual[0]['id']==reserved['id'] and virtual[0]['created_at']==200
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                sync_groups_time_batch(store,['incomplete:f:p'],201)
                raise RuntimeError('rollback')
        assert virtual_due_groups(store,201)[0]['id']==reserved['id']
        with transaction(store.db):sync_groups_time_batch(store,['incomplete:f:p'],201)
        current=store.db.execute("SELECT id,created FROM reader_problems WHERE identity='incomplete:f:p' AND finished IS NULL").fetchone()
        assert current['id']==reserved['id'] and current['created']==200
    finally:store.db.close()


def test_feed_removal_after_deadline_does_not_restore_forgotten_material(tmp_path,postgres_url):
    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
            raw=json.dumps({'blocks':[],'extra':{}})
            store.db.execute("INSERT INTO publications(id,platform,external_id,content,discovered_at,status) VALUES ('p','youtube','p',%s,1,'pending_collection')",(raw,))
            store.db.execute("UPDATE publications SET feed_id='f' WHERE id='p'")
        youtube_action(store, 'blocked', 100,900);clock[0]=1001
        seen=virtual_due_groups(store,clock[0]);assert len(seen)==1
        with transaction(store.db):
            store.remove_feed('f')
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(seen[0]['id'],)).fetchone()
        assert row and row['finished'] is not None
        assert not any(o.get('id')=='p' for o in json.loads(row['body'])['objects'])
    finally:store.db.close()
