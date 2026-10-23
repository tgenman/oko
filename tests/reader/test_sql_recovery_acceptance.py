"""An existing saved library survives index/version migrations and repeated startup."""
import sqlite3
from fastapi.testclient import TestClient

from oko.collection.contracts import Material
from oko.storage.store import Store
from oko.subscriptions.subscriptions import Overrides
from tests.support.performance_seed import seed
from tests.support.reading_cost import traced_app
from tests.support.reader import HEADERS


def test_existing_library_migrates_without_losing_work_or_content(tmp_path):
    seed(tmp_path,26,active=True)
    store=Store(tmp_path,lambda _:None)
    store.set_work_enabled(False)
    row=store.db.execute('SELECT id,feed_id FROM items ORDER BY id LIMIT 1').fetchone()
    identity,feed=row['id'],row['feed_id']
    store.subscriptions.update_settings('feed',feed,Overrides(originals=False))
    with store.db:
        store.db.execute("UPDATE jobs SET status='pending' WHERE kind='fetch' AND item=?",(identity,))
    job=store.next_job(store.clock(),feeds=[feed])
    assert job and job.item==identity and job.kind=='fetch'
    store.save(job,Material('Saved second revision','Persistent replacement text',captions='available'))
    revision=store.content_version(identity)
    original=store.path(identity).read_bytes()
    previous=store.versions.previous(identity)
    assert len(previous)==1
    with store.db:
        store.db.execute("UPDATE jobs SET status='running' WHERE kind='theses' AND item=?",(identity,))
        store.db.execute("INSERT INTO state VALUES ('acceptance-checkpoint','preserved')")
    store.db.close()
    # Model the schema that already stored the library before selective reading.
    # No app or runtime is active while the owned fixture drops service metadata.
    with sqlite3.connect(tmp_path/'state.sqlite') as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'reader_v_%'").fetchall():
            db.execute('DROP TRIGGER "'+name.replace('"','""')+'"')
        for name in ['reader_identity','reader_entities','reader_originals','reader_visibility','reader_feed_counts','reader_folder_counts','reader_lists','reader_job_time','reader_runtime','reader_youtube_time']:
            db.execute('DROP TABLE '+name)
        for name in ['items_unviewed_order','items_unviewed_feed','jobs_pending_next','jobs_item','history_members_item','reader_action_identity_sequence']:
            db.execute('DROP INDEX IF EXISTS '+name)
    storage_id=None
    for _ in range(2):
        app=traced_app(tmp_path)
        with TestClient(app,headers=HEADERS) as client:
            current=client.get('/api/v3/materials/'+identity)
            assert current.status_code==200,current.text
            material=current.json()
            assert material['content']['text']=='Persistent replacement text'
            assert material['content']['revision_id']==revision
            assert len(material['content']['previous_versions'])==0
            assert material['originals'] is False
            frame=client.get('/api/v3/reading/materials/'+identity).json()
            if storage_id:assert frame['storage_id']==storage_id
            storage_id=frame['storage_id']
            def inspect():
                current_store=app.state.runtime.require_store()
                assert current_store.path(identity).read_bytes()==original
                assert current_store.versions.previous(identity)==previous
                assert current_store.db.execute("SELECT value FROM state WHERE key='acceptance-checkpoint'").fetchone()[0]=='preserved'
                assert current_store.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=?",(identity,)).fetchone()[0]=='pending'
                assert current_store.db.execute('PRAGMA secure_delete').fetchone()[0]==1
                assert current_store.db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
            client.portal.call(inspect)
