"""Real SQL failure boundaries for the three independent cron workers."""
import asyncio
import json
from tests.support.storage import database
import pytest
import httpx
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.services.collection import CollectionService
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
        old_revision=saved(store,item)['processing_metadata']['_revision']
        source=StructuredSource(None)
        with store.jobs.transaction(execution._live):
            CollectionService(execution.require_store).collected(item,source.describe(store.publication(item),Material('Title','New input')),manual=False)
        changed=saved(store,item)
        assert changed['processing_metadata']['_revision']!=old_revision
        with store.jobs.transaction(execution._live):
            worker.service.reconcile()
        replacement=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        assert replacement['generation']>old['generation']
        release.set()
        await tasks._ai
        assert saved(store,item)['filtered_content']=={}
        latest=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        assert latest['generation']==replacement['generation'] and latest['error'] is None and latest['status']=='pending'
        store.db.close()
    asyncio.run(scenario())


def test_claim_and_saved_context_are_one_transaction_and_survive_restart(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    context=saved(store,item)['processing_metadata']['_pass']
    assert context['feed_id']=='demo' and context['revision']
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    execution._store=reopened
    asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
    assert saved(reopened,item)['processing_metadata']['_pass']==context
    assert saved(reopened,item)['summarized_content']['points']
    reopened.db.close()


def test_shared_filter_winner_keeps_its_feed_for_summary_after_restart(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=False,theses=True,prompt_theses='Other feed rules'))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == 'demo'
        store.db.close()
        store = Store(tmp_path,lambda _:None)
        execution._store=store
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        result = saved(store,item)
        assert result['summarized_content']['points']
        assert result['processing_metadata']['_pass']['feed_id'] == 'demo'
        assert result['processing_metadata']['_pass']['settings']['values']['prompt_theses'] != 'Other feed rules'
    finally:
        store.db.close()


def test_competing_workers_commit_one_shared_processing_context(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    async def scenario():
        calls = []
        store,item,execution,tasks = worker_library(tmp_path)
        try:
            second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
            with transaction(store.db):
                store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
                store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=False,theses=True))
                store.processing.reconcile_accepted_item(item)
            filter_worker, summary_worker = workers(execution,tasks,transport(calls,theses=True))
            await asyncio.gather(filter_worker.tick(),summary_worker.tick())
            if tasks._ai:
                await tasks._ai
            chosen = saved(store,item)['processing_metadata']['_pass']['feed_id']
            assert chosen in ('demo',second)
            assert len([call for call in calls if call.url.path=='/v1/chat/completions']) == 1
            completed = store.db.execute("SELECT kind,input_snapshot->>'feed_id' AS feed FROM jobs WHERE item=%s AND kind IN ('filter','theses') AND status='done'", (item,)).fetchall()
            assert len(completed)==1 and completed[0]['feed']==chosen
        finally:
            store.db.close()
    asyncio.run(scenario())


def test_late_result_from_deleted_selected_feed_is_rejected(tmp_path):
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
            second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
            with transaction(store.db):
                store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
                store.subscriptions.update_settings('feed','demo',Overrides(filter_enabled=False))
                store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=True))
                store.processing.reconcile_accepted_item(item)
            worker = workers(execution,tasks,httpx.MockTransport(model))[0]
            await worker.tick()
            await asyncio.wait_for(entered.wait(),2)
            assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == second
            with transaction(store.db):
                store.remove_feed(second)
                store.jobs.remove_feed(second,{item})
            release.set()
            await tasks._ai
            result = saved(store,item)
            assert result['filtered_content'] == {}
            assert result['processing_metadata'].get('_pass',{}).get('feed_id') != second
        finally:
            store.db.close()
    asyncio.run(scenario())


def test_no_feed_is_chosen_when_every_candidate_is_disallowed(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, _, _ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed','demo',Overrides(processor='codex'))
            store.subscriptions.update_settings('feed',second,Overrides(processor='codex'))
            store.processing.reconcile_accepted_item(item)
        with transaction(store.db):
            assert store.jobs.claim_processing('filter',store.clock()) is None
            assert not saved(store,item)['processing_metadata'].get('_pass')
        with transaction(store.db):
            store.subscriptions.update_settings('feed',second,Overrides(processor='llamacpp'))
            store.settings_changed('feed',second)
            store.processing.reconcile_accepted_item(item)
            job = store.jobs.claim_processing('filter',store.clock())
            assert job is not None
            assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == second
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
        store.db.execute("UPDATE feeds SET cursor='' WHERE id='demo'")
    store.db.close()
    with TestClient(create_app(tmp_path,source=FailedSource(),token='test',api_only=True,ai_transport=transport([],theses=True)),headers=HEADERS) as client:
        wait_for(client,lambda:client.get('/api/v3/publication/'+item).json()['theses']['result'])
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
        store.db.execute("UPDATE jobs SET status = 'pending' , wait_scope = 'source:synthetic' , retry_at = %s , next = %s , error = 'source wait' WHERE kind = 'filter'",(deadline,next_at))
        store.subscriptions.update_feed('demo',FeedPatch(importance='high'))
        store.settings_changed('feed','demo')
        worker.service.reconcile()
    row=store.db.execute("SELECT retry_at,next,wait_scope,error FROM jobs WHERE kind='filter'").fetchone()
    assert tuple(row)==(deadline,next_at,'source:synthetic','source wait')
    assert store.jobs.claim_processing('filter',store.clock()) is None
    store.db.close()


def test_linked_source_privacy_change_reconciles_parent_job_atomically(tmp_path):
    from oko.storage import memberships
    from oko.subscriptions.subscriptions import AddFeed, FeedPatch
    store, item, _, _ = worker_library(tmp_path)
    try:
        other = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/linked-feed',title='Linked',folder='Other',verification='synthetic'))['id']
        with transaction(store.db):
            store.subscriptions.update_feed('demo',FeedPatch(public_ai=True))
            store.subscriptions.update_feed(other,FeedPatch(public_ai=True))
            memberships.lock_graph(store.db)
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked-source','https://example.org/linked','rss','linked',1,content,title FROM publications WHERE id=%s""", (item,))
            memberships.add(store.db,other,'linked')
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')", (item,))
            store.processing.reconcile_accepted_item(item)
        before = store.db.execute("SELECT generation,input_snapshot->>'signature' FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[:]
        store.processing.background = True
        def change():
            store.subscriptions.update_feed(other,FeedPatch(public_ai=False))
            store.settings_changed('feed',other)
            after = store.db.execute("SELECT generation,input_snapshot->>'signature' FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
            assert after[0] > before[0] and after[1] != before[1]
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                change()
                raise ValueError('rollback')
        assert store.db.execute("SELECT generation,input_snapshot->>'signature' FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[:] == before
        with transaction(store.db):
            change()
    finally:
        store.db.close()


def test_discovery_page_and_continuation_roll_back_together(tmp_path):
    from oko.collection.contracts import DiscoveryResult, Publication
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
        with store.db:
            store.db.execute("UPDATE feeds SET cursor='' WHERE id='demo'")
            store.jobs.enqueue_discovery()
        job=store.jobs.next_job(store.clock(),feeds=['demo'])
        assert job and job.kind=='discover'
        worker=collection_worker(tmp_path,store,StructuredSource(Paginated()))
        asyncio.run(worker._execute(job))
        assert store.db.execute("SELECT cursor FROM feeds WHERE id='demo'").fetchone()[0] == ''
        assert store.db.execute("SELECT 1 FROM publications WHERE url='https://example.org/next'").fetchone() is None
        assert not store.db.execute("SELECT last_collection_result FROM feeds WHERE id='demo'").fetchone()[0].get('success_at')
        assert store.db.execute("SELECT status FROM jobs WHERE id=%s", (job.id,)).fetchone()[0] != 'done'
    finally:
        store.db.close()


def test_missing_subtitle_repair_is_rediscovered_after_restart(tmp_path):
    from oko.storage import youtube_media
    from oko.storage.transaction import read_snapshot
    store, item, _, _ = worker_library(tmp_path)
    try:
        with store.db:
            content = json.loads(store.db.execute('SELECT content FROM publications WHERE id=%s', (item,)).fetchone()[0])
            content['extra']['youtube'] = {'captions':'available','captions_digest':'missing'}
            content['blocks'].append(dict(type='media',role='subtitles',media_id='missing-caption'))
            store.db.execute("UPDATE publications SET platform='youtube',content=%s WHERE id=%s", (json.dumps(content),item))
            store.db.execute("""INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key)
                VALUES ('missing-caption',%s,'youtube:subtitles','https://example.test/captions','captions.txt','ready','missing-caption.txt')""", (item,))
        with read_snapshot(store.db):
            assert youtube_media.subtitle_path(store,'missing-caption') is None
        store.db.close()
        store = Store(tmp_path, lambda _: None)
        assert store.db.execute("SELECT status FROM media_files WHERE id='missing-caption'").fetchone()[0] == 'pending_download'
        assert store.db.execute("SELECT status FROM jobs WHERE kind='download_media' AND item='missing-caption'").fetchone()[0] == 'pending'
    finally:
        store.db.close()


def test_failed_delete_problem_follows_job_without_status_read(tmp_path, postgres_url):
    import json
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',1)")
            row=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES ('delete','item','running',1,%s) RETURNING id", (json.dumps({'cleanup_feed':'demo'}),)).fetchone()
        job=Job(row['id'],'delete','item',1)
        def current():
            return store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:delete:demo:delete:item' AND finished IS NULL").fetchone()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.retry(job,1,'Ошибка удаления',code='delete')
                assert current() is not None
                raise ValueError('rollback')
        assert current() is None
        store.jobs.retry(job,1,'Ошибка удаления',code='delete')
        problem=current()
        assert problem is not None
        assert not any(c['id']=='job:delete:demo:delete:item' for c in store.problems.candidates(5))
        with store.db:
            store.db.execute("UPDATE jobs SET next=0 WHERE id=%s", (job.id,))
        claimed=store.jobs.next_job(1,deleting_only=True)
        assert claimed is not None and claimed.id==job.id
        assert json.loads(current()['body'])['state']=='retrying'
        with pytest.raises(ValueError,match='finish rollback'):
            with transaction(store.db):
                store.jobs.delete(claimed)
                assert current() is None
                raise ValueError('finish rollback')
        assert current() is not None
        assert store.jobs.valid(claimed)
        store.jobs.delete(claimed)
        assert current() is None
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem['id'],)).fetchone()['outcome']=='resolved'
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
        content=json.loads(row[0])
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
        content=json.loads(db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()[0])
        content['extra']['_reader']['reply_preview']=dict(text='Changed quote' if case == 'changed' else 'Original quote',author=None,source_url=None)
        db.execute('UPDATE publications SET content=%s WHERE id=%s',(json.dumps(content),item))
        sql=(Path(__file__).parents[2]/'oko/storage/postgres/096-normalize-publication-content.sql').read_text()
        db.execute(sql)
    reopened=Store(tmp_path,lambda _:None)
    try:
        reopened.processing.recover()
        if case != 'same':
            assert reopened.filters.read(item)['status'] != 'done'
            assert reopened.processing.theses.read(item) is None
            assert reopened.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[0] == 'pending'
        else:
            assert reopened.filters.read(item)['status'] == 'done'
            assert reopened.processing.theses.read(item) is not None
            assert saved(reopened,item)['processing_metadata']['filter'] == old_metadata['filter']
    finally:
        reopened.db.close()


def test_legacy_linked_quote_migration_invalidates_parent_after_restart(tmp_path):
    from pathlib import Path
    import psycopg
    from tests.support.storage import database_url
    from oko.storage import memberships

    store,item,execution,tasks=worker_library(tmp_path)
    with transaction(store.db):
        memberships.lock_graph(store.db)
        row=store.db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone()
        content=json.loads(row[0])
        content['blocks'].append(dict(type='quote',text='Original linked quote',author=None,source_url=None))
        store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
            VALUES ('linked','linked-source','https://example.org/linked','rss','linked',1,%s,'Linked')""",(json.dumps(content),))
        store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')",(item,))
        store.processing.reconcile_accepted_item(item)
    filter_worker,summary_worker=workers(execution,tasks)
    asyncio.run(pass_worker(filter_worker,tasks))
    asyncio.run(pass_worker(summary_worker,tasks))
    assert store.filters.read(item)['status']=='done'
    assert store.processing.theses.read(item) is not None
    store.db.close()
    with psycopg.connect(database_url(tmp_path),autocommit=True) as db:
        content=json.loads(db.execute("SELECT content FROM publications WHERE id='linked'").fetchone()[0])
        content['extra']['_reader']['reply_preview']=dict(text='Updated linked quote',author=None,source_url=None)
        db.execute("UPDATE publications SET content=%s WHERE id='linked'",(json.dumps(content),))
        db.execute((Path(__file__).parents[2]/'oko/storage/postgres/096-normalize-publication-content.sql').read_text())
    reopened=Store(tmp_path,lambda _:None)
    try:
        reopened.processing.recover()
        assert reopened.filters.read(item)['status'] != 'done'
        assert reopened.processing.theses.read(item) is None
        assert reopened.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[0]=='pending'
    finally:
        reopened.db.close()
