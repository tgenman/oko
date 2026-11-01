import json
import pytest
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.youtube_admission import current


def test_early_unblock_closes_same_occurrence_and_rollback_restores(tmp_path,postgres_url):
    clock=[100.]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        store.youtube.blocked(100,900)
        old=current(store)['id']
        clock[0]=110
        with pytest.raises(RuntimeError):
            with transaction(store.db):
                store.youtube.completed()
                raise RuntimeError('abort')
        assert current(store)['id']==old and store.youtube.status(clock[0])['paused']
        store.youtube.completed()
        row=store.db.execute('SELECT finished,outcome FROM reader_problems WHERE id=%s',(old,)).fetchone()
        assert tuple(row)==(110,'resolved') and current(store) is None
    finally:store.db.close()


def test_viewed_during_pause_never_restores_forgotten_object(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('yt','yt','YT','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','youtube','p',0,1,'{}',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('yt','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','p','pending','')")
            store.incomplete_draft.mark_job('fetch','p')
        store.youtube.blocked(100,900)
        assert json.loads(current(store)['body'])['objects']==[dict(kind='material',id='p')]
        assert store.viewed_set(['p'])
        for row in store.db.execute("SELECT body FROM reader_problems WHERE body::jsonb->>'code'='youtube-admission'"):
            assert all(o.get('id')!='p' for o in json.loads(row['body'])['objects'])
    finally:store.db.close()
