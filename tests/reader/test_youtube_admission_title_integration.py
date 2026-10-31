import json
from oko.collection.contracts import Material
from oko.services.collection import CollectionService
from oko.storage.transaction import transaction
from oko.storage.youtube_admission import current
from oko.storage.youtube_admission_jobs import contributions
from tests.support.source import StructuredSource
from tests.support.worker_library import worker_library


def test_linked_title_writer_keeps_admission_in_sync(tmp_path):
    store,item,execution,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            feed=store.db.execute('SELECT feed_id FROM feed_publications WHERE publication_id=%s AND is_primary',(item,)).fetchone()[0]
            store.db.execute("UPDATE feeds SET kind='youtube' WHERE id=%s",(feed,))
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title,published_known)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,'Old title',0 FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code) VALUES ('fetch','linked','pending','') RETURNING id").fetchone()[0]
            store.incomplete_draft.mark_materials([item,'linked'])
        store.youtube.blocked(store.wall_clock(),900)
        before=current(store);assert before is not None
        expected_before={v for values in contributions(store,[job]).values() for v in values}
        assert {o['id'] for o in json.loads(before['body'])['objects']}==expected_before
        with transaction(store.db):
            material=StructuredSource(None).describe(store.publication('linked'),Material('New title','Useful text'))
            _,effects=CollectionService(execution.require_store).collected('linked',material,manual=False)
            store.jobs.apply_collection(effects)
        after=current(store);assert after is not None and after['id']==before['id']
        expected_after={v for values in contributions(store,[job]).values() for v in values}
        assert {o['id'] for o in json.loads(after['body'])['objects']}==expected_after
        assert store.db.execute("SELECT title FROM publications WHERE id='linked'").fetchone()[0]=='New title'
    finally:store.db.close()
