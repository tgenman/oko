from tests.support.youtube_action import youtube_action
import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.youtube_admission import current


def test_early_unblock_closes_same_occurrence_and_rollback_restores(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        youtube_action(store, 'blocked', 100,900)
        old=current(store)['id']
        clock[0]=110
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                youtube_action(store, 'completed')
                raise RuntimeError('abort')
        assert current(store)['id']==old and store.youtube.status(clock[0])['paused']
        youtube_action(store, 'completed')
        row=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert tuple(row)==(110,'resolved') and current(store) is None
    finally:store.db.close()


def test_viewed_during_pause_keeps_affected_object(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','youtube','p','{}')")
            store.db.execute("UPDATE publications SET feed_id='yt' WHERE id='p'")
            store.db.execute("INSERT INTO jobs(kind,item,status,reason_code) VALUES ('fetch','p','pending','')")
            store.incomplete_draft.mark_job('fetch','p')
        youtube_action(store, 'blocked', 100,900)
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        with transaction(store.db):
            assert store.viewed_set(['p'])
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
    finally:store.db.close()
