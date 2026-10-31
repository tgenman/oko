import asyncio,json
from oko.collection.adapters import ExternalKey,ExternalReference,Platform
from oko.storage.transaction import transaction
from tests.support.worker_library import worker_library,workers,pass_worker

def test_saved_result_must_not_use_new_attempt_sources_as_its_provenance(tmp_path,postgres_url):
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,title FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("SELECT input_snapshot FROM jobs WHERE kind='theses' AND item=%s",(item,)).fetchone()
            sources=row[0]['sources'];assert any(source['id']=='linked' for source in sources)
            # Model a newer attempt whose selected blocks exclude linked, while
            # the earlier accepted result remains stored.
            store.db.execute("UPDATE jobs SET status='running',input_snapshot=jsonb_set(input_snapshot,'{sources}',%s::jsonb) WHERE kind='theses' AND item=%s",(json.dumps([s for s in sources if s['id']!='linked']),item))
            store.publications.identify('linked',ExternalReference(ExternalKey(Platform.other_web,'linked'),'https://example.test/new'))
        meta=json.loads(store.db.execute('SELECT processing_metadata FROM publications WHERE id=%s',(item,)).fetchone()[0])
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        assert store.processing.theses.read(item) is None
        assert meta['summary'].get('source_url_stale') is True
    finally:store.db.close()

def test_done_summary_is_requeued_when_current_snapshot_lost_old_source(tmp_path,postgres_url):
    from oko.subscriptions.subscriptions import Overrides
    store,item,execution,tasks=worker_library(tmp_path,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=''))
            store.settings_changed()
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked:linked','https://example.org/linked','other_web','linked',1,content,title FROM publications WHERE id=%s""",(item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        with transaction(store.db):
            row=store.db.execute("SELECT status,input_snapshot FROM jobs WHERE kind='theses' AND item=%s",(item,)).fetchone()
            assert row['status']=='done' and any(s['id']=='linked' for s in row['input_snapshot']['sources'])
            sources=[s for s in row['input_snapshot']['sources'] if s['id']!='linked']
            store.db.execute("UPDATE jobs SET input_snapshot=jsonb_set(input_snapshot,'{sources}',%s::jsonb) WHERE kind='theses' AND item=%s",(json.dumps(sources),item))
            store.publications.identify('linked',ExternalReference(ExternalKey(Platform.other_web,'linked'),'https://example.test/new'))
        row=store.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=%s",(item,)).fetchone()
        assert row['status']=='pending',row['status']
    finally:store.db.close()
