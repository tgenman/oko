"""Real SQL failure boundaries for the three independent cron workers."""
from oko.application.collection_effects import CollectionApplication
from tests.support.storage_action import storage_action
from tests.support.subscription_action import subscription_action
from tests.support.job_claim import claim_job, retry_job
from tests.support.collection_acceptance import apply_simulated_collection, running_collection_job
from oko.domain.job import Job
import asyncio
import json
from tests.support.storage import database
import pytest
import httpx
from tests.support.storage import Store
from oko.storage.transaction import TransactionAborted, transaction
from oko.workers.collection import collection_service
from tests.support.synthetic_material import material as Material
from tests.support.worker_library import worker_library,workers,pass_worker,saved
from tests.support.filters import transport
from tests.support.source import StructuredSource


@pytest.mark.parametrize('phase',['processing_after_result','processing_after_finish','processing_after_commit'])
def test_result_and_done_survive_or_rollback_together(tmp_path,phase):
    def fail(point):
        if point==phase:raise OSError('synthetic failure')
    store,item,execution,tasks=worker_library(tmp_path,fault=fail)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    row=saved(store,item)
    job=dict(store.db.execute("SELECT * FROM jobs WHERE kind = 'filter' AND item = %s",(item,)).fetchone())
    committed=phase=='processing_after_commit'
    assert bool(row['filtered_content'])==committed
    assert (job['status']=='done')==committed
    assert row['status']==('pending_summary' if committed else 'pending_filtering')
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    assert bool(saved(reopened,item)['filtered_content'])==committed
    assert (reopened.db.execute("SELECT status FROM jobs WHERE kind = 'filter' AND item = %s",(item,)).fetchone()[0]=='done')==committed
    reopened.db.close()


def test_conditional_finish_zero_rows_rolls_back_domain_result(tmp_path):
    current=[None]
    def fail(point):
        if point=='processing_after_result':
            current[0].db.execute("UPDATE jobs SET generation=generation+1 WHERE kind='filter'")
    store,item,execution,tasks=worker_library(tmp_path,fault=fail)
    current[0]=store
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    assert saved(store,item)['filtered_content']=={}
    assert store.db.execute("SELECT status FROM jobs WHERE kind='filter'").fetchone()[0]=='cancelled'
    with transaction(store.db):store.db.execute("INSERT INTO state VALUES ('next-transaction','ok')")
    assert store.db.execute("SELECT value FROM state WHERE key='next-transaction'").fetchone()[0]=='ok'
    store.db.close()


@pytest.mark.parametrize('error',[False,True])
def test_new_input_rejects_old_success_and_old_error_without_harming_replacement(tmp_path,error):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':
                entered.set()
                await release.wait()
                if error:return httpx.Response(503,text='old failure')
            return native.handler(request)
        store,item,execution,tasks=worker_library(tmp_path)
        worker=workers(execution,tasks,httpx.MockTransport(model))[0]
        await worker.tick()
        await asyncio.wait_for(entered.wait(),2)
        old=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        source=StructuredSource(None)
        with store.jobs.transaction(execution._live):
            job_for_collection=running_collection_job(store,'fetch',item)
            _,effects=collection_service(execution.require_store).collected_content(item,source.describe(store.publication(item),Material('Title','New input')).adapter_content)
            apply_simulated_collection(store,effects,job=job_for_collection)
        changed=saved(store,item)
        assert '_revision' not in changed['processing_metadata']
        with store.jobs.transaction(execution._live):
            worker.service.reconcile()
        replacement=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        assert replacement['generation']>old['generation']
        release.set()
        await tasks._ai
        assert saved(store,item)['filtered_content']=={}
        latest=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        assert latest['generation']==replacement['generation'] and latest['reason_message'] is None and latest['status']=='pending'
        store.db.close()
    asyncio.run(scenario())


def test_claim_and_saved_context_are_one_transaction_and_survive_restart(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    context=store.db.execute("SELECT input_snapshot FROM jobs WHERE item=%s AND kind='filter'",(item,)).fetchone()[0]
    assert context['feed_id']=='fixture' and context['input_hash']
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    execution._store=reopened
    asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
    assert reopened.db.execute("SELECT input_snapshot FROM jobs WHERE item=%s AND kind='filter'",(item,)).fetchone()[0]==context
    assert '_pass' not in saved(reopened,item)['processing_metadata']
    assert saved(reopened,item)['summarized_content']['points']
    reopened.db.close()


def test_other_feed_settings_do_not_change_publication_processing_after_restart(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        second = subscription_action(store, 'add_feed', AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            subscription_action(store, 'update_settings', 'feed',second,Overrides(filter_enabled=False,theses=True,prompt_theses='Other feed rules'))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        assert store.db.execute("SELECT input_snapshot->>'feed_id' FROM jobs WHERE item=%s AND kind='filter'",(item,)).fetchone()[0]=='fixture'
        store.db.close()
        store = Store(tmp_path,lambda _:None)
        execution._store=store
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        result = saved(store,item)
        assert result['summarized_content']['points']
        assert result['processing_metadata']['summary']['prompt'] != 'Other feed rules'
        assert '_pass' not in result['processing_metadata']
    finally:
        store.db.close()


def test_competing_workers_commit_one_shared_processing_context(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    async def scenario():
        calls = []
        store,item,execution,tasks = worker_library(tmp_path)
        try:
            second = subscription_action(store, 'add_feed', AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
            with transaction(store.db):
                subscription_action(store, 'update_settings', 'feed',second,Overrides(filter_enabled=False,theses=True))
                store.processing.reconcile_accepted_item(item)
            filter_worker, summary_worker = workers(execution,tasks,transport(calls,theses=True))
            await asyncio.gather(filter_worker.tick(),summary_worker.tick())
            if tasks._ai:
                await tasks._ai
            assert '_pass' not in saved(store,item)['processing_metadata']
            chosen='fixture'
            assert len([call for call in calls if call.url.path=='/v1/chat/completions']) == 1
            completed = store.db.execute("SELECT kind,input_snapshot->>'feed_id' AS feed FROM jobs WHERE item=%s AND kind IN ('filter','theses') AND status='done'", (item,)).fetchall()
            assert len(completed)==1 and completed[0]['feed']==chosen
        finally:
            store.db.close()
    asyncio.run(scenario())


def test_late_result_from_deleted_selected_feed_is_saved(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        native = transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':
                entered.set()
                await release.wait()
            return native.handler(request)
        store,item,execution,tasks = worker_library(tmp_path)
        try:
            second = subscription_action(store, 'add_feed', AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
            with transaction(store.db):
                store.db.execute('UPDATE publications SET feed_id=%s WHERE id=%s', (second,item))
                subscription_action(store, 'update_settings', 'feed','fixture',Overrides(filter_enabled=False))
                subscription_action(store, 'update_settings', 'feed',second,Overrides(filter_enabled=True))
                store.processing.reconcile_accepted_item(item)
            worker = workers(execution,tasks,httpx.MockTransport(model))[0]
            await worker.tick()
            await asyncio.wait_for(entered.wait(),2)
            assert store.db.execute("SELECT input_snapshot->>'feed_id' FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[0]==second
            with transaction(store.db):
                store.remove_feed(second)
            release.set()
            await tasks._ai
            assert store.db.execute('SELECT 1 FROM publications WHERE id=%s',(item,)).fetchone() is not None
            assert store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[0]=='done'
        finally:
            store.db.close()
    asyncio.run(scenario())


def test_processing_waits_when_publication_feed_disallows_processor(tmp_path):
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        with transaction(store.db):
            subscription_action(store, 'update_settings', 'feed','fixture',Overrides(processor='codex'))
            storage_action(store, 'settings_changed', 'feed','fixture')
            store.processing.reconcile_accepted_item(item)
        worker = workers(execution,tasks)[0]
        asyncio.run(pass_worker(worker,tasks))
        job = store.db.execute("SELECT status,reason_code FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
        assert job['reason_code'] == 'ai-permission'
        assert saved(store,item)['filtered_content'] == {}
        with transaction(store.db):
            subscription_action(store, 'update_settings', 'feed','fixture',Overrides(processor='llamacpp'))
            storage_action(store, 'settings_changed', 'feed','fixture')
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(worker,tasks))
        assert store.filters.read(item)['status'] == 'done'
    finally:
        store.db.close()


def test_overlapping_cron_ticks_claim_one_attempt(tmp_path):
    async def scenario():
        calls=[]
        store,item,execution,tasks=worker_library(tmp_path)
        worker=workers(execution,tasks,transport(calls,theses=True))[0]
        await asyncio.gather(worker.tick(),worker.tick())
        await tasks._ai
        assert len([call for call in calls if call.url.path=='/v1/chat/completions'])==1
        assert store.db.execute("SELECT attempt,status FROM jobs WHERE kind='filter'").fetchone()[:]==(1,'done')
        store.db.close()
    asyncio.run(scenario())


def test_collection_failure_does_not_stop_filter_and_summary_crons(tmp_path):
    from tests.support.app import create_app
    from tests.support.reader import HEADERS,wait_for
    from tests.support.v3 import TestClient
    from oko.collection.contracts import SourceError
    class FailedSource:
        changes=None
        async def discover(self,cursor):raise SourceError('Synthetic unavailable source',retryable=False)
        async def fetch(self,publication):raise AssertionError('unexpected fetch')
    store,item,execution,tasks=worker_library(tmp_path)
    with store.db:
        store.db.execute("UPDATE feeds SET cursor='' WHERE id='fixture'")
    store.db.close()
    with TestClient(create_app(tmp_path,source=FailedSource(),token='test',api_only=True,ai_transport=transport([],theses=True)),headers=HEADERS) as client:
        wait_for(client,lambda:client.get('/api/v3/publication/'+item).json()['summarized_content'].get('points'))
        def stopped_source():
            with database(tmp_path) as db:
                return db.execute("SELECT status FROM jobs WHERE kind='discover'").fetchone()[0]=='failed'
        wait_for(client,stopped_source)
        with database(tmp_path) as db:
            assert db.execute("SELECT kind,status FROM jobs WHERE kind IN ('filter','theses') ORDER BY kind").fetchall()==[('filter','done'),('theses','done')]
        assert client.get('/api/v3/publication/'+item).status_code==200


def test_new_revision_does_not_shorten_source_wait(tmp_path):
    from oko.subscriptions.subscriptions import FeedPatch
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        deadline=store.wall_clock()+120
        next_at=store.clock()+120
        store.db.execute("UPDATE jobs SET status = 'pending' , wait_scope = 'source:synthetic' , retry_at = %s , next = %s , reason_message = 'source wait' WHERE kind = 'filter'",(deadline,next_at))
        subscription_action(store, 'update_feed', 'fixture',FeedPatch(importance='high'))
        storage_action(store, 'settings_changed', 'feed','fixture')
        worker.service.reconcile()
    row=store.db.execute("SELECT retry_at,next,wait_scope,reason_message FROM jobs WHERE kind='filter'").fetchone()
    assert tuple(row)==(deadline,next_at,'source:synthetic','source wait')
    with transaction(store.db):
        assert store.jobs.claim_processing('filter',store.clock()) is None
    store.db.close()


def test_linked_source_privacy_change_reconciles_parent_job_atomically(tmp_path):
    from oko.storage import memberships
    from oko.subscriptions.subscriptions import AddFeed, FeedPatch
    store, item, _, _ = worker_library(tmp_path)
    try:
        other = subscription_action(store, 'add_feed', AddFeed(url='https://fixture.test/linked-feed',title='Linked',folder='Other',verification='synthetic'))['id']
        with transaction(store.db):
            subscription_action(store, 'update_feed', 'fixture',FeedPatch(public_ai=True))
            subscription_action(store, 'update_feed', other,FeedPatch(public_ai=True))
            memberships.lock_graph(store.db)
            store.db.execute("""INSERT INTO publications(id,url,platform,external_id,status,content,title,discovered_at)
                SELECT 'linked','https://example.org/linked','rss','linked','ready',content,title,discovered_at FROM publications WHERE id=%s""", (item,))
            memberships.add(store.db,other,'linked',newly_created=True)
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')", (item,))
            store.processing.reconcile_accepted_item(item)
        before = store.db.execute("SELECT generation,input_snapshot->>'input_hash' FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[:]
        store.processing.background = True
        def change():
            subscription_action(store, 'update_feed', other,FeedPatch(public_ai=False))
            storage_action(store, 'settings_changed', 'feed',other)
            after = store.db.execute("SELECT generation,input_snapshot->>'input_hash' FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
            assert tuple(after)==before
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                change()
                raise ValueError('rollback')
        assert store.db.execute("SELECT generation,input_snapshot->>'input_hash' FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[:] == before
        with transaction(store.db):
            change()
    finally:
        store.db.close()


def test_discovery_page_and_continuation_roll_back_together(tmp_path):
    from oko.collection.contracts import DiscoveryResult
    from tests.support.legacy_source import Publication
    from tests.support.worker_library import collection_worker

    def fault(point):
        if point=='collection_after_continuation':
            raise RuntimeError('synthetic continuation failure')
    class Paginated:
        changes = None
        async def discover(self,cursor):
            return DiscoveryResult((Publication('next','https://example.org/next',123,Material('Next','Synthetic page')),),
                                   'page-two',continuation=30)
    store, _, _, _ = worker_library(tmp_path,fault=fault)
    try:
        with transaction(store.db):
            store.db.execute("UPDATE feeds SET cursor='' WHERE id='fixture'")
            store.jobs.enqueue_discovery('fixture')
        job=claim_job(store, store.clock(),feeds=['fixture'])
        assert job and job.kind=='discover'
        worker=collection_worker(tmp_path,store,StructuredSource(Paginated()))
        asyncio.run(worker._execute(job))
        assert store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0] == ''
        assert store.db.execute("SELECT 1 FROM publications WHERE url='https://example.org/next'").fetchone() is None
        assert not store.db.execute("SELECT last_collection_result FROM feeds WHERE id='fixture'").fetchone()[0].get('success_at')
        assert store.db.execute("SELECT status FROM jobs WHERE id=%s", (job.id,)).fetchone()[0] != 'done'
    finally:
        store.db.close()


def test_missing_subtitle_repair_is_rediscovered_after_restart(tmp_path):
    from oko.storage import youtube_media
    from oko.storage.transaction import read_snapshot
    store, item, _, _ = worker_library(tmp_path)
    try:
        with store.db:
            content = store.db.execute('SELECT content FROM publications WHERE id=%s', (item,)).fetchone()[0]
            content['extra']['youtube'] = {}
            content['blocks'].append(dict(type='media',role='subtitles',media_id='missing-caption',source_url=None,publication_url=None))
            store.db.execute("UPDATE publications SET platform='youtube',content=%s WHERE id=%s", (json.dumps(content),item))
            store.db.execute("""INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key)
                VALUES ('missing-caption',%s,'youtube:subtitles','https://example.test/captions','captions.txt','ready','missing-caption.txt')""", (item,))
        with read_snapshot(store.db):
            youtube_media.subtitle_path(store,'missing-caption')
            assert item in store.processing._repairs
        store.db.close()
        store = Store(tmp_path, lambda _: None)
        assert store.db.execute("SELECT status FROM media_files WHERE id='missing-caption'").fetchone()[0] == 'pending_download'
        assert store.db.execute("SELECT status FROM jobs WHERE kind='download_media' AND item='missing-caption'").fetchone()[0] == 'pending'
    finally:
        store.db.close()


@pytest.mark.parametrize('case',['same', 'changed', 'header_only'])
def test_legacy_quote_migration_reconciles_saved_processing_after_restart(tmp_path, case):
    from pathlib import Path
    import psycopg
    from tests.support.storage import database_url

    store,item,execution,tasks=worker_library(tmp_path)
    original=dict(type='quote',text='Original quote',author=None,source_url=None)
    with store.db:
        row=store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()
        content=row[0]
        if case != 'header_only':
            content['blocks'].append(original)
        store.db.execute('UPDATE publications SET content=%s WHERE id=%s',(json.dumps(content),item))
        store.processing.reconcile_accepted_item(item)
    filter_worker,summary_worker=workers(execution,tasks)
    asyncio.run(pass_worker(filter_worker,tasks))
    asyncio.run(pass_worker(summary_worker,tasks))
    assert store.filters.read(item)['status']=='done'
    assert store.processing.theses.read(item) is not None
    old_metadata=saved(store,item)['processing_metadata']
    store.db.close()
    with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
        content=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
        content['extra'].setdefault('_reader',{})['reply_preview']=dict(text='Changed quote' if case == 'changed' else 'Original quote',author=None,source_url=None)
        db.execute('UPDATE publications SET content=%s WHERE id=%s',(json.dumps(content),item))
        db.execute((Path(__file__).parents[2]/'oko/storage/postgres/103-publication-reader-blocks.sql').read_text())
    reopened=Store(tmp_path,lambda _:None)
    try:
        with transaction(reopened.db):
            reopened.processing.recover()
        if case == 'header_only':
            assert reopened.filters.read(item)['status'] != 'done'
            assert reopened.processing.theses.read(item) is None
            assert reopened.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[0] == 'pending'
        else:
            assert reopened.filters.read(item)['status'] == 'done'
            assert reopened.processing.theses.read(item) is not None
            assert saved(reopened,item)['processing_metadata']['filter'] == old_metadata['filter']
    finally:
        reopened.db.close()


def test_legacy_linked_quote_copy_does_not_invalidate_parent_after_restart(tmp_path):
    from pathlib import Path
    import psycopg
    from tests.support.storage import database_url
    from oko.storage import memberships

    store,item,execution,tasks=worker_library(tmp_path)
    with transaction(store.db):
        memberships.lock_graph(store.db)
        row=store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()
        content=row[0]
        content['blocks'].append(dict(type='quote',text='Original linked quote',author=None,source_url=None))
        store.db.execute("""INSERT INTO publications(id,url,platform,external_id,status,content,title)
            VALUES ('linked','https://example.org/linked','rss','linked','ready',%s,'Linked')""",(json.dumps(content),))
        store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
        store.processing.reconcile_accepted_item(item)
    filter_worker,summary_worker=workers(execution,tasks)
    asyncio.run(pass_worker(filter_worker,tasks))
    asyncio.run(pass_worker(summary_worker,tasks))
    assert store.filters.read(item)['status']=='done'
    assert store.processing.theses.read(item) is not None
    store.db.close()
    with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
        content=db.execute("SELECT content FROM publications WHERE id='linked'").fetchone()[0]
        content['extra'].setdefault('_reader',{})['reply_preview']=dict(text='Updated linked quote',author=None,source_url=None)
        db.execute("UPDATE publications SET content=%s WHERE id='linked'",(json.dumps(content),))
        db.execute((Path(__file__).parents[2]/'oko/storage/postgres/103-publication-reader-blocks.sql').read_text())
    reopened=Store(tmp_path,lambda _:None)
    try:
        with transaction(reopened.db):
            reopened.processing.recover()
        assert reopened.filters.read(item)['status'] == 'done'
        assert reopened.processing.theses.read(item) is not None
    finally:
        reopened.db.close()


def test_publication_title_wins_after_content_migration(tmp_path):
    from pathlib import Path
    import psycopg
    from tests.support.storage import database_url

    store,item,execution,tasks=worker_library(tmp_path)
    filter_worker,summary_worker=workers(execution,tasks)
    asyncio.run(pass_worker(filter_worker,tasks))
    asyncio.run(pass_worker(summary_worker,tasks))
    assert store.filters.read(item)['status']=='done'
    store.db.close()
    with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
        content=db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
        content['extra'].setdefault('_reader',{})['title']='Legacy visible title'
        db.execute('UPDATE publications SET content=%s WHERE id=%s',(json.dumps(content),item))
        db.execute((Path(__file__).parents[2]/'oko/storage/postgres/103-publication-reader-blocks.sql').read_text())
    reopened=Store(tmp_path,lambda _:None)
    try:
        with transaction(reopened.db):
            reopened.processing.recover()
        assert reopened.db.execute('SELECT title FROM publications WHERE id=%s',(item,)).fetchone()[0]=='Title'
        assert reopened.filters.read(item)['status']=='done'
        assert reopened.processing.theses.read(item) is not None
    finally:
        reopened.db.close()


def test_publication_linked_title_wins_after_content_migration(tmp_path):
    from pathlib import Path
    import psycopg
    from tests.support.storage import database_url
    from oko.storage import memberships

    store,item,execution,tasks=worker_library(tmp_path)
    with transaction(store.db):
        memberships.lock_graph(store.db)
        row=store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()
        content=row[0]
        content['extra'].setdefault('_reader',{})['title']='Legacy displayed title'
        store.db.execute("""INSERT INTO publications(id,url,platform,external_id,status,content,title)
            VALUES ('linked','https://example.org/linked','rss','linked','ready',%s,'Old source title')""",(json.dumps(content),))
        store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
        store.processing.reconcile_accepted_item(item)
    filter_worker,summary_worker=workers(execution,tasks)
    asyncio.run(pass_worker(filter_worker,tasks))
    asyncio.run(pass_worker(summary_worker,tasks))
    assert store.filters.read(item)['status']=='done'
    assert store.processing.theses.read(item) is not None
    store.db.close()
    with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
        db.execute((Path(__file__).parents[2]/'oko/storage/postgres/103-publication-reader-blocks.sql').read_text())
        title,raw=db.execute("SELECT title,content FROM publications WHERE id='linked'").fetchone()
        assert title=='Old source title'
        assert 'title' not in raw['extra']['_reader']
    reopened=Store(tmp_path,lambda _:None)
    try:
        with transaction(reopened.db):
            reopened.processing.recover()
        assert reopened.filters.read(item)['status'] == 'done'
        assert reopened.processing.theses.read(item) is not None
    finally:
        reopened.db.close()


def test_changed_telegram_album_key_is_rejected_without_duplicate_or_rekey(tmp_path):
    from dataclasses import replace
    from oko.collection.adapters import ExternalKey, ExternalReference, Platform
    from tests.support.legacy_source import Publication
    from oko.domain.publication import PublicationIdentityConflict
    from tests.support.synthetic_material import material as Material
    from tests.support.source import StructuredSource

    store,item,_,_=worker_library(tmp_path)
    old='https://t.me/fixture/6'
    new='https://t.me/fixture/5'
    try:
        with transaction(store.db):
            store.db.execute("UPDATE publications SET platform='telegram',external_id=%s,url=%s,content=jsonb_set(content,'{extra}',COALESCE(content->'extra','{}'::jsonb) || jsonb_build_object('telegram',jsonb_build_object('members',jsonb_build_array(6))),true) WHERE id=%s", (old,old,item))
        ref=ExternalReference(ExternalKey(Platform.telegram,new),new)
        from oko.collection.adapters import PublicationContent, PublicationMetadata
        incoming=PublicationContent(PublicationMetadata(ref,'',None),{'extra':{'telegram':{'members':[5,6]}}},needs_fetch=True)
        with pytest.raises(PublicationIdentityConflict):
            with transaction(store.db):
                store.discovery.accept([incoming],'{}','fixture')
        assert store.db.execute("SELECT id,external_id FROM publications WHERE platform='telegram'").fetchall() == [(item,old)]
        material=StructuredSource(None).describe(store.publication(item),Material('Title','New text'))
        material=replace(material,adapter_content=replace(material.adapter_content,
            metadata=replace(material.adapter_content.metadata,reference=ref)))
        with pytest.raises(PublicationIdentityConflict):
            with transaction(store.db):
                store.collected_publication.accept_content(item,material.adapter_content)
        assert store.db.execute('SELECT external_id FROM publications WHERE id=%s',(item,)).fetchone()[0]==old
    finally:
        store.db.close()


def test_existing_telegram_album_cannot_absorb_another_saved_publication(tmp_path):
    from oko.collection.adapters import ExternalKey, ExternalReference, Platform
    from tests.support.legacy_source import Publication
    from oko.domain.publication import PublicationIdentityConflict

    store,item,_,_=worker_library(tmp_path)
    first='https://t.me/fixture/5'
    second='https://t.me/fixture/6'
    try:
        with transaction(store.db):
            store.db.execute("UPDATE publications SET platform='telegram',external_id=%s,url=%s,content=jsonb_set(content,'{extra}',COALESCE(content->'extra','{}'::jsonb) || jsonb_build_object('telegram',jsonb_build_object('members',jsonb_build_array(6))),true) WHERE id=%s", (second,second,item))
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,feed_id,content) VALUES ('first','telegram',%s,%s,'fixture',jsonb_build_object('extra',jsonb_build_object('telegram',jsonb_build_object('members',jsonb_build_array(5)))))", (first,first))
        ref=ExternalReference(ExternalKey(Platform.telegram,first),first)
        from oko.collection.adapters import PublicationContent, PublicationMetadata
        incoming=PublicationContent(PublicationMetadata(ref,'',None),{'extra':{'telegram':{'members':[5,6]}}},needs_fetch=True)
        with pytest.raises(PublicationIdentityConflict):
            with transaction(store.db):
                store.discovery.accept([incoming],'{}','fixture')
        rows=store.db.execute("SELECT id,(content #> '{extra,telegram,members}')::text AS members FROM publications WHERE platform='telegram'").fetchall()
        assert {row['id']:row['members'] for row in rows} == {item:'[6]', 'first':'[5]'}
    finally:
        store.db.close()


def test_collection_diagnostics_are_scoped_to_owning_transaction(tmp_path):
    from tests.support.legacy_source import Publication
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                job_for_collection=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('new','https://example.test/new',100))],'{}','fixture')
                assert not hasattr(effects,'fetch_problems_before')
                raise ValueError('rollback')
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                with pytest.raises(RuntimeError,match='no capture'):
                    CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=Job(0,'discover','fixture',0))
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/new'").fetchone() is None
    finally:
        store.db.close()


@pytest.mark.parametrize('ending', ['commit','rollback'])
def test_collection_capture_does_not_outlive_failed_transaction_callback(tmp_path, ending):
    from tests.support.legacy_source import Publication
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    def broken_callback():
        raise RuntimeError('synthetic callback failure')
    try:
        with pytest.raises(RuntimeError,match='synthetic callback failure'):
            with transaction(store.db):
                if ending == 'commit':
                    store.db.after_commit(broken_callback)
                job_for_collection=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('new','https://example.test/new',100))],'{}','fixture')
                if ending == 'commit':
                    apply_simulated_collection(store,effects,job=job_for_collection)
                if ending == 'rollback':
                    store.db.after_rollback(broken_callback)
                    raise ValueError('force rollback')
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                with pytest.raises(RuntimeError,match='no capture'):
                    CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=Job(0,'discover','fixture',0))
    finally:
        store.db.close()


def test_unapplied_collection_effects_abort_publication_and_cursor_commit(tmp_path):
    from tests.support.legacy_source import Publication
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        previous=store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0]
        with pytest.raises(RuntimeError,match='Collection effects were not applied'):
            with transaction(store.db):
                job_for_collection=running_collection_job(store,'discover','fixture')
                store.discovery.accept([publication_packet(Publication('unapplied','https://example.test/unapplied',100))],'changed-cursor','fixture')
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/unapplied'").fetchone() is None
        assert store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0] == previous
    finally:
        store.db.close()


def test_caught_collection_effect_failure_still_aborts_transaction(tmp_path):
    from tests.support.legacy_source import Publication
    from tests.support.source import publication_packet
    fail=[False]
    def fault(point):
        if fail[0] and point == 'collection_effects_before_completion':
            raise RuntimeError('synthetic effects failure')
    store,_,_,_=worker_library(tmp_path,fault=fault)
    try:
        previous=store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0]
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                job=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('failed','https://example.test/failed',100))],'failed-cursor','fixture')
                fail[0]=True
                try:
                    CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=job)
                except RuntimeError as error:
                    assert str(error)=='synthetic effects failure'
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/failed'").fetchone() is None
        assert store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0] == previous
    finally:
        store.db.close()


def test_retrying_caught_effect_failure_cannot_commit_partial_transaction(tmp_path):
    from tests.support.legacy_source import Publication
    from oko.storage.transaction import TransactionAborted
    from tests.support.source import publication_packet
    fail=[False]
    def fault(point):
        if fail[0] and point == 'collection_effects_before_completion':
            raise RuntimeError('synthetic effects failure')
    store,_,_,_=worker_library(tmp_path,fault=fault)
    try:
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                job=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('failed','https://example.test/retried',100))],'{}','fixture')
                fail[0]=True
                with pytest.raises(RuntimeError,match='synthetic effects failure'):
                    CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=job)
                fail[0]=False
                CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=job)
                store.jobs.finish(job)
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/retried'").fetchone() is None
    finally:
        store.db.close()


def test_caught_discovery_conflict_cannot_commit_earlier_page_items(tmp_path):
    from tests.support.legacy_source import Publication
    from oko.domain.publication import PublicationOwnershipConflict
    from oko.storage.transaction import TransactionAborted
    from tests.support.source import publication_packet

    store,item,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,platform,external_id) VALUES ('other','rss','other')")
            store.db.execute("UPDATE publications SET feed_id='other' WHERE id=%s",(item,))
        existing=store.publication(item)
        assert existing is not None
        existing=publication_packet(existing)
        before=store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0]
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                job_for_collection=running_collection_job(store,'discover','fixture')
                try:
                    store.discovery.accept([publication_packet(Publication('new','https://example.test/page-new',100)),existing], 'changed','fixture')
                except PublicationOwnershipConflict:
                    pass
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/page-new'").fetchone() is None
        assert store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0] == before
    finally:
        store.db.close()


@pytest.mark.parametrize('ending',['forgotten','rejected'])
def test_collection_requires_original_job_outcome_before_commit(tmp_path, ending):
    from dataclasses import replace
    from tests.support.legacy_source import Publication
    from oko.domain.job import Job
    from oko.storage.job_repository import JobRejected
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            row=store.db.execute("UPDATE jobs SET status='running',generation=generation+1 WHERE kind='discover' AND item='fixture' RETURNING id,generation").fetchone()
            job=Job(row['id'],'discover','fixture',row['generation'])
        expected = TransactionAborted if ending == 'rejected' else RuntimeError
        message = 'A repository aborted this transaction' if ending == 'rejected' else 'Collection Job was not completed'
        with pytest.raises(expected,match=message):
            with transaction(store.db):
                store.jobs.begin_collection(job)
                effects=store.discovery.accept([publication_packet(Publication('new','https://example.test/job-outcome',100))],'changed','fixture')
                CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=job)
                if ending=='rejected':
                    with pytest.raises(JobRejected):
                        store.jobs.finish(replace(job,generation=job.generation+1))
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/job-outcome'").fetchone() is None
        assert store.jobs.valid(job)
    finally:
        store.db.close()


def test_caught_collected_publication_failure_cannot_commit_partial_content(tmp_path):
    from oko.storage.transaction import TransactionAborted
    from tests.support.synthetic_material import material as Material
    from tests.support.source import StructuredSource
    fail=[False]
    def fault(point):
        if fail[0] and point == 'collection_after_publication_write':
            raise RuntimeError('synthetic write failure')
    store,item,_,_=worker_library(tmp_path,fault=fault)
    try:
        before=store.db.execute('SELECT content,title FROM publications WHERE id=%s',(item,)).fetchone()[:]
        material=StructuredSource(None).describe(store.publication(item),Material('Changed','Changed body'))
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                job_for_collection=running_collection_job(store,'fetch',item)
                fail[0]=True
                try:
                    store.collected_publication.accept_content(item,material.adapter_content)
                except RuntimeError as error:
                    assert str(error)=='synthetic write failure'
        assert store.db.execute('SELECT content,title FROM publications WHERE id=%s',(item,)).fetchone()[:] == before
    finally:
        store.db.close()


@pytest.mark.parametrize('kind,target',[('discover','other'),('fetch','fixture')])
def test_collection_rejects_job_for_another_operation_or_target(tmp_path, kind, target):
    from tests.support.legacy_source import Publication
    from oko.storage.job_repository import JobRejected
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                job=running_collection_job(store,kind,target,bind=False)
                correct=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('new','https://example.test/wrong-job',100))],'changed','fixture')
                with pytest.raises(JobRejected,match='does not own this result'):
                    CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=job)
                store.jobs.finish(job)
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/wrong-job'").fetchone() is None
    finally:
        store.db.close()


def test_retry_after_wrong_collection_job_cannot_commit(tmp_path):
    from tests.support.legacy_source import Publication
    from oko.storage.job_repository import JobRejected
    from oko.storage.transaction import TransactionAborted
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                wrong=running_collection_job(store,'discover','other',bind=False)
                correct=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('new','https://example.test/wrong-then-right',100))],'changed','fixture')
                with pytest.raises(JobRejected):
                    CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=wrong)
                CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=correct)
                store.jobs.finish(correct)
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/wrong-then-right'").fetchone() is None
    finally:
        store.db.close()


def test_collection_result_cannot_move_to_new_job_generation(tmp_path):
    from dataclasses import replace
    from tests.support.legacy_source import Publication
    from oko.storage.job_repository import JobRejected
    from oko.storage.transaction import TransactionAborted
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                job=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('new','https://example.test/new-generation',100))],'changed','fixture')
                store.db.execute("UPDATE jobs SET generation=generation+1 WHERE id=%s",(job.id,))
                replacement=replace(job,generation=job.generation+1)
                with pytest.raises(JobRejected):
                    CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=replacement)
                store.jobs.finish(replacement)
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/new-generation'").fetchone() is None
    finally:
        store.db.close()


def test_retry_after_rejected_collection_finish_cannot_commit(tmp_path):
    from dataclasses import replace
    from tests.support.legacy_source import Publication
    from oko.storage.job_repository import JobRejected
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with pytest.raises(TransactionAborted):
            with transaction(store.db):
                job=running_collection_job(store,'discover','fixture')
                effects=store.discovery.accept([publication_packet(Publication('new','https://example.test/stale-finish',100))],'changed','fixture')
                CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=job)
                with pytest.raises(JobRejected):
                    store.jobs.finish(replace(job,generation=job.generation+1))
                store.jobs.finish(job)
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/stale-finish'").fetchone() is None
    finally:
        store.db.close()


@pytest.mark.parametrize('kind,target',[(None,None),('discover','other'),('fetch','fixture')])
def test_collection_accept_requires_matching_bound_job(tmp_path, kind, target):
    from tests.support.legacy_source import Publication
    from oko.storage.job_repository import JobRejected
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with pytest.raises(JobRejected,match='does not own this result'):
            with transaction(store.db):
                if kind is not None:
                    running_collection_job(store,kind,target)
                store.discovery.accept([publication_packet(Publication('new','https://example.test/unbound',100))],'changed','fixture')
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/unbound'").fetchone() is None
    finally:
        store.db.close()


def test_collection_result_and_retry_outcome_commit_together(tmp_path):
    from tests.support.legacy_source import Publication
    from tests.support.source import publication_packet

    store,_,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            job=running_collection_job(store,'discover','fixture')
            effects=store.discovery.accept([publication_packet(Publication('retry','https://example.test/retry',100))],'retry-cursor','fixture')
            CollectionApplication(store.jobs,store.publications,store.processing).apply(effects,job=job)
            retry_job(store, job,store.clock(),'temporary failure',code='source-temporary')
        assert store.db.execute("SELECT 1 FROM publications WHERE external_id='https://example.test/retry'").fetchone()
        assert store.db.execute("SELECT cursor FROM feeds WHERE id='fixture'").fetchone()[0]=='retry-cursor'
        row=store.db.execute('SELECT status,generation,reason_message FROM jobs WHERE id=%s',(job.id,)).fetchone()
        assert row[:] == ('pending',job.generation,'temporary failure')
    finally:
        store.db.close()


@pytest.mark.parametrize('complete',[True,False])
def test_discovery_acceptance_invalidates_an_older_running_fetch(tmp_path,complete):
    from dataclasses import replace
    store,item,_,_=worker_library(tmp_path)
    try:
        publication=store.publication(item)
        assert publication is not None
        source=StructuredSource(None)
        packet=source.describe(publication,Material('Title','Discovered content',full=complete)).adapter_content
        assert packet is not None
        with transaction(store.db):
            store.db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
            old=running_collection_job(store,'fetch',item,bind=False)
        with transaction(store.db):
            discovery=running_collection_job(store,'discover','fixture')
            effects=store.discovery.accept([packet],'{}','fixture')
            apply_simulated_collection(store,effects,job=discovery)
        assert not store.jobs.valid(old)
        status=store.db.execute("SELECT status FROM jobs WHERE kind='fetch' AND item=%s",(item,)).fetchone()[0]
        assert status==('cancelled' if complete else 'pending')
        if not complete:
            assert store.db.execute('SELECT status FROM publications WHERE id=%s',(item,)).fetchone()[0]=='pending_collection'
    finally:
        store.db.close()


def test_collection_admission_ignores_media_url_completion_but_detects_body_change(tmp_path):
    from oko.workers.collection import collection_service
    store,item,_,_=worker_library(tmp_path)
    try:
        content={'blocks':[{'type':'text','role':'body','text':'Body'},
                           {'type':'media','role':'image','media_id':'m','source_url':None,'publication_url':None}], 'extra':{}}
        with transaction(store.db):
            store.db.execute('UPDATE publications SET content=%s::jsonb WHERE id=%s',(json.dumps(content),item))
        state=collection_service(lambda:store)
        original=state.revision(item)
        content['blocks'][1]['source_url']='https://cdn.example/image'
        with transaction(store.db):
            store.db.execute('UPDATE publications SET content=%s::jsonb WHERE id=%s',(json.dumps(content),item))
        assert state.revision(item)==original
        content['blocks'][0]['text']='Changed body'
        with transaction(store.db):
            store.db.execute('UPDATE publications SET content=%s::jsonb WHERE id=%s',(json.dumps(content),item))
        assert state.revision(item)!=original
    finally:
        store.db.close()


def test_unrequested_subtitles_do_not_complete_processing_when_theses_are_enabled(tmp_path):
    from oko.storage.processing_state import ProcessingRepository
    store,item,_,_=worker_library(tmp_path)
    try:
        with transaction(store.db):
            content=store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0]
            content['extra']['youtube']={}
            content['blocks'].append({'type':'media','role':'subtitles','media_id':'subtitles'})
            store.db.execute("UPDATE feeds SET kind='youtube' WHERE id='fixture'")
            store.db.execute("UPDATE publications SET platform='youtube',status='pending_filtering',content=%s WHERE id=%s",(json.dumps(content),item))
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('subtitles',%s,'youtube:subtitles','youtube:test:subtitles','captions','pending_download')",(item,))
        assert store.db.execute("SELECT 1 FROM jobs WHERE kind='download_media' AND item='subtitles'").fetchone() is None
        preparation=ProcessingRepository(store).prepare(item,'theses')
        assert preparation.outcome=='waiting' and preparation.reason=='input_pending'
        assert store.db.execute('SELECT summarized_content FROM publications WHERE id=%s',(item,)).fetchone()[0]=='{}'
    finally:
        store.db.close()
