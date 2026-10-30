"""Real SQL failure boundaries for the three independent cron workers."""
import asyncio
import json
from tests.support.storage import database
import pytest
import httpx
from tests.support.storage import Store
from oko.storage.transaction import transaction,TransactionAborted
from oko.storage.job_repository import JobRejected
from oko.services.collection import CollectionService
from oko.collection.contracts import Material
from tests.support.worker_library import worker_library,workers,pass_worker,saved
from tests.support.filters import transport
from tests.support.source import StructuredSource


def test_rediscovery_clears_old_cleanup_owner_without_discarding_other_job_data(tmp_path):
    from oko.domain.collection import CollectionEffects
    store,item,_,_=worker_library(tmp_path)
    try:
        with store.jobs.transaction(lambda:True):
            store.db.execute("INSERT INTO jobs(kind,item,payload) VALUES ('delete',%s,%s)",
                             (item,json.dumps({'cleanup_feed':'old-feed','other':'preserved'})))
            store.jobs.apply_collection(CollectionEffects(fetch={item:False}))
        row=store.db.execute("SELECT status,payload FROM jobs WHERE kind='delete' AND item=%s",(item,)).fetchone()
        assert row['status']=='cancelled'
        assert json.loads(row['payload'])=={'other':'preserved'}
    finally:
        store.db.close()


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


def test_changed_collection_updates_processing_job_in_same_transaction(tmp_path):
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        before = dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
        previous = saved(store,item)['content']
        previous_filter = saved(store,item)['filtered_content']
        assert previous_filter
        store.processing.background = True
        material = StructuredSource(None).describe(store.publication(item), Material('Title','Changed synthetic text'))

        def change():
            statements = []
            store.db.set_trace_callback(statements.append)
            try:
                CollectionService(execution.require_store).collected(item,material,manual=False)
            finally:
                store.db.set_trace_callback(None)
            assert not any('INSERT INTO processing_dirty' in statement for statement in statements)
            after = dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
            assert after['generation'] > before['generation']
            assert after['input_snapshot'] != before['input_snapshot']
            assert saved(store,item)['filtered_content'] == {}

        with pytest.raises(ValueError, match='synthetic rollback'):
            with transaction(store.db):
                change()
                raise ValueError('synthetic rollback')
        assert saved(store,item)['content'] == previous
        assert saved(store,item)['filtered_content'] == previous_filter
        assert dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()) == before
        with transaction(store.db):
            change()
    finally:
        store.db.close()


def test_changed_linked_article_updates_parent_job_in_same_transaction(tmp_path):
    from oko.storage import publication_links
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        with transaction(store.db):
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked-source','https://example.org/linked','other_web','linked',1,content,title FROM publications WHERE id=%s""", (item,))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')", (item,))
            store.processing.reconcile_accepted_item(item)
        before = dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
        content = json.loads(store.db.execute("SELECT content FROM publications WHERE id='linked'").fetchone()[0])
        next(block for block in content['blocks'] if block['type']=='text' and block['role']=='body')['text'] = 'Changed linked article'
        def change():
            store.db.execute("UPDATE publications SET content=%s WHERE id='linked'", (json.dumps(content),))
            statements = []
            store.db.set_trace_callback(statements.append)
            try:
                publication_links.notify_parents(store,'linked')
            finally:
                store.db.set_trace_callback(None)
            assert not any('INSERT INTO processing_dirty' in statement for statement in statements)
            after = dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
            assert after['generation'] > before['generation'] and after['input_snapshot'] != before['input_snapshot']
        with pytest.raises(ValueError, match='synthetic rollback'):
            with transaction(store.db):
                change()
                raise ValueError('synthetic rollback')
        assert dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()) == before
        with transaction(store.db):
            change()
    finally:
        store.db.close()


def test_discovered_complete_publication_gets_job_in_same_transaction(tmp_path):
    from oko.collection.contracts import Publication
    from tests.support.source import publication_packet
    store, _, _, _ = worker_library(tmp_path)
    try:
        store.processing.background = True
        store.jobs.enqueue_discovery()
        job = store.jobs.next_job(store.clock())
        assert job is not None and job.kind == 'discover'
        packet = publication_packet(Publication('two','https://example.org/two',101), Material('Second','Synthetic new text'), needs_fetch=False)
        statements = []
        store.db.set_trace_callback(statements.append)
        with pytest.raises(ValueError, match='synthetic rollback'):
            with transaction(store.db):
                store.collect_discovery([packet], '{}')
                item = store.db.execute("SELECT id FROM publications WHERE source_id='demo:two'").fetchone()[0]
                assert store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[0] == 'pending'
                raise ValueError('synthetic rollback')
        assert store.db.execute("SELECT id FROM publications WHERE source_id='demo:two'").fetchone() is None
        with transaction(store.db):
            store.collect_discovery([packet], '{}')
            item = store.db.execute("SELECT id FROM publications WHERE source_id='demo:two'").fetchone()[0]
            assert store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[0] == 'pending'
        assert not any('INSERT INTO processing_dirty' in statement for statement in statements)
    finally:
        store.db.set_trace_callback(None)
        store.db.close()


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


def test_saved_processing_feed_is_used_instead_of_display_owner(tmp_path):
    from oko.storage import processing_input
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, _, _ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/second', title='Second', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed',second,Overrides(prompt_filters='Second feed rules',filter_enabled=False))
            row = store.db.execute('SELECT processing_metadata FROM publications WHERE id=%s', (item,)).fetchone()
            metadata = json.loads(row[0])
            metadata['_pass'] = {'feed_id':second}
            store.db.execute('UPDATE publications SET processing_metadata=%s WHERE id=%s', (json.dumps(metadata),item))
        assert store.item_feed(item) == 'demo'
        assert processing_input.live_settings(store,item)['feed_id'] == second
        assert processing_input.live_settings(store,item)['values']['prompt_filters'] == 'Second feed rules'
        assert processing_input.stage_enabled(store,item,'filter') is False
        with transaction(store.db):
            processing_input.snapshot(store,item)
        assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == second
        signature = processing_input.signature(store,item,'filter')
        with transaction(store.db):
            store.subscriptions.update_settings('feed','demo',Overrides(prompt_filters='Different display feed rule'))
            store.settings_changed('feed','demo')
        assert processing_input.signature(store,item,'filter') == signature
        assert processing_input.live_settings(store,item)['feed_id'] == second
        store.db.close()
        store = Store(tmp_path,lambda _:None)
        assert processing_input.live_settings(store,item)['feed_id'] == second
        assert processing_input.stage_enabled(store,item,'filter') is False
    finally:
        store.db.close()


@pytest.mark.parametrize('first_kind', ['filter','theses'])
def test_shared_publication_offers_each_possible_first_stage(tmp_path, first_kind):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, _, _ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=False,theses=True))
            store.processing.reconcile_accepted_item(item)
            jobs = {row['kind']:row['status'] for row in store.db.execute("SELECT kind,status FROM jobs WHERE item=%s AND kind IN ('filter','theses')", (item,))}
            assert jobs == {'filter':'pending','theses':'pending'}
        before = saved(store,item)['processing_metadata']
        def claim():
            expected = second if first_kind == 'theses' else 'demo'
            job = store.jobs.claim_processing(first_kind, store.clock())
            assert job is not None and job.item == item
            assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == expected
            other_kind = 'filter' if first_kind == 'theses' else 'theses'
            other = store.db.execute('SELECT status FROM jobs WHERE item=%s AND kind=%s', (item,other_kind)).fetchone()[0]
            assert other == ('cancelled' if first_kind == 'theses' else 'blocked')
            from oko.storage.processing_state import ProcessingRepository
            prepared = ProcessingRepository(store).prepare(item,first_kind)
            assert prepared.input is not None
            assert prepared.input.request.operation == first_kind
            store.jobs.save_input(job,prepared.input)
            assert store.db.execute('SELECT input_snapshot->>\'feed_id\' FROM jobs WHERE id=%s', (job.id,)).fetchone()[0] == expected
            if first_kind == 'filter':
                from oko.services.processing import ProcessingService
                store.processing.reconcile_item(item)
                service = ProcessingService(lambda:store)
                store.jobs.reconcile('theses',service.candidates('theses',item=item))
                assert store.db.execute("SELECT status FROM jobs WHERE item=%s AND kind='theses'", (item,)).fetchone()[0] == 'blocked'
        with pytest.raises(ValueError, match='synthetic rollback'):
            with transaction(store.db):
                claim()
                raise ValueError('synthetic rollback')
        assert saved(store,item)['processing_metadata'] == before
        with transaction(store.db):
            claim()
        store.db.close()
        store = Store(tmp_path, lambda _:None)
        assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == (second if first_kind == 'theses' else 'demo')
    finally:
        store.db.close()


def test_summary_worker_can_start_shared_publication_from_second_feed(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=False,theses=True,prompt_theses='Second feed summary rules'))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        result = saved(store,item)
        assert result['summarized_content']['points']
        assert result['processing_metadata']['_pass']['feed_id'] == second
        assert result['processing_metadata']['_pass']['settings']['values']['prompt_theses'] == 'Second feed summary rules'
        job = store.db.execute("SELECT status,input_snapshot->>'feed_id' FROM jobs WHERE kind='theses' AND item=%s", (item,)).fetchone()
        assert job[:] == ('done',second)
        store.db.close()
        store = Store(tmp_path,lambda _:None)
        assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == second
        assert saved(store,item)['summarized_content']['points']
    finally:
        store.db.close()


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


def test_removing_selected_secondary_feed_revokes_its_running_job(tmp_path):
    from oko.storage.reader_facts import ReaderFacts
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, _, _ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=False,theses=True))
            store.processing.reconcile_accepted_item(item)
        with transaction(store.db):
            job = store.jobs.claim_processing('theses',store.clock())
            assert job is not None
        assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == second
        members = ReaderFacts(lambda:store).feed_members(second)
        assert item in members
        with transaction(store.db):
            store.remove_feed(second)
            store.jobs.remove_feed(second,members)
            metadata = saved(store,item)['processing_metadata']
            current = store.db.execute('SELECT status,generation FROM jobs WHERE id=%s', (job.id,)).fetchone()
            assert metadata.get('_pass',{}).get('feed_id') != second
            assert current['status'] != 'running' and current['generation'] > job.generation
    finally:
        store.db.close()


def test_removing_other_shared_feed_keeps_selected_running_job(tmp_path):
    from oko.storage.reader_facts import ReaderFacts
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, _, _ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=False,theses=True))
            store.processing.reconcile_accepted_item(item)
        with transaction(store.db):
            job = store.jobs.claim_processing('filter',store.clock())
            assert job is not None
        assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == 'demo'
        members = ReaderFacts(lambda:store).feed_members(second)
        with transaction(store.db):
            store.remove_feed(second)
            store.jobs.remove_feed(second,members)
        assert store.jobs.valid(job)
        assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == 'demo'
    finally:
        store.db.close()


def test_membership_removal_records_effects_and_rolls_back_together(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed
    store,item,_,_ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.read_versions.memberships_changed([item])
        before = store.read_versions.version('status','runtime')['version']
        def remove():
            store.remove_feed(second)
            assert store.read_versions.version('status','runtime')['version'] > before
        with pytest.raises(ValueError,match='synthetic rollback'):
            with transaction(store.db):
                remove()
                raise ValueError('synthetic rollback')
        assert store.read_versions.version('status','runtime')['version'] == before
        assert store.db.execute('SELECT 1 FROM feed_publications WHERE feed_id=%s AND publication_id=%s', (second,item)).fetchone()
        with transaction(store.db):
            remove()
    finally:
        store.db.close()


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


def test_removing_selected_feed_preserves_its_saved_summary(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store,item,execution,tasks = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed',second,Overrides(filter_enabled=False,theses=True,prompt_theses='Second feed rules'))
            store.processing.reconcile_accepted_item(item)
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        before = saved(store,item)
        assert before['summarized_content']['points']
        with transaction(store.db):
            store.remove_feed(second)
            store.jobs.remove_feed(second,{item})
        store.processing.sync()
        after = saved(store,item)
        assert after['summarized_content'] == before['summarized_content']
        assert after['processing_metadata']['summary'] == before['processing_metadata']['summary']
        assert after['processing_metadata'].get('_pass',{}).get('feed_id') != second
    finally:
        store.db.close()


def test_first_claim_skips_feed_whose_public_processor_is_not_permitted(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, _, _ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.subscriptions.update_settings('feed','demo',Overrides(processor='codex'))
            store.subscriptions.update_settings('feed',second,Overrides(processor='llamacpp'))
            store.processing.reconcile_accepted_item(item)
        with transaction(store.db):
            job = store.jobs.claim_processing('filter',store.clock())
            assert job is not None
            assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == second
    finally:
        store.db.close()


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


def test_unconfigured_processor_does_not_choose_feed_and_configuration_unblocks(tmp_path):
    from oko.processing.execution import CodexChoice, CodexSettings
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, _, _ = worker_library(tmp_path)
    try:
        second = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)', (second,item))
            store.db.execute('UPDATE feeds SET public_ai=1 WHERE id IN (%s,%s)', ('demo',second))
            store.subscriptions.update_settings('feed','demo',Overrides(processor='codex'))
            store.subscriptions.update_settings('feed',second,Overrides(processor='codex'))
            store.processing.reconcile_accepted_item(item)
        with transaction(store.db):
            assert store.jobs.claim_processing('filter',store.clock()) is None
            assert not saved(store,item)['processing_metadata'].get('_pass')
            assert store.db.execute("SELECT problem_code FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[0] == 'processor-config'
        choice = CodexChoice(model='synthetic',reasoning_effort='low',fast=False)
        with transaction(store.db):
            store.configure_codex(CodexSettings(filter=choice,theses=choice))
            assert store.jobs.claim_processing('filter',store.clock()) is not None
            assert saved(store,item)['processing_metadata']['_pass']['feed_id'] == 'demo'
    finally:
        store.db.close()


def test_stale_filter_result_is_preserved_when_rules_change(tmp_path):
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        before = saved(store,item)
        assert before['filtered_content'] and before['processing_metadata']['filter']['signature']
        with transaction(store.db):
            store.subscriptions.update_settings('feed','demo',Overrides(prompt_filters='Different rules'))
            store.settings_changed('feed','demo')
            store.processing.reconcile_accepted_item(item)
            after = saved(store,item)
            assert after['filtered_content'] == before['filtered_content']
            assert after['processing_metadata']['filter'] == before['processing_metadata']['filter']
            assert store.filters.read(item)['status'] != 'done'
    finally:
        store.db.close()


def test_stale_summary_is_preserved_but_not_treated_as_current(tmp_path):
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        before = saved(store,item)
        assert before['summarized_content']['points']
        with transaction(store.db):
            store.subscriptions.update_settings('feed','demo',Overrides(prompt_theses='Different summary rules'))
            store.settings_changed('feed','demo')
            store.processing.reconcile_accepted_item(item)
            after = saved(store,item)
            assert after['summarized_content'] == before['summarized_content']
            assert after['processing_metadata']['summary'] == before['processing_metadata']['summary']
            assert store.processing.theses.read(item) is None
    finally:
        store.db.close()


def test_disabled_admission_never_begins_and_next_transaction_is_usable(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    statements=[]
    store.db.set_trace_callback(statements.append)
    with pytest.raises(InterruptedError):
        with store.jobs.transaction(lambda:False):
            pytest.fail('closed admission entered transaction')
    assert not any(query.startswith('BEGIN') for query in statements)
    with store.jobs.transaction(lambda:True):store.db.execute("INSERT INTO state VALUES ('after-closed-gate','ok')")
    assert sum(query.startswith('BEGIN') for query in statements)==1
    store.db.close()


def test_filter_claim_and_rollback_preserve_attempt_state(tmp_path):
    store, item, _, _ = worker_library(tmp_path)
    try:
        with transaction(store.db):
            store.processing.reconcile_accepted_item(item)
        before = store.db.execute("SELECT status,generation FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[:]
        with pytest.raises(ValueError, match='synthetic rollback'):
            with transaction(store.db):
                job = store.jobs.claim_processing('filter',store.clock())
                assert job is not None and store.jobs.valid(job)
                raise ValueError('synthetic rollback')
        assert store.db.execute("SELECT status,generation FROM jobs WHERE kind='filter' AND item=%s",(item,)).fetchone()[:] == before
        with transaction(store.db):
            job = store.jobs.claim_processing('filter',store.clock())
            assert job is not None and store.jobs.valid(job)
    finally:
        store.db.close()


@pytest.mark.parametrize('outcome', ['finish','retry','cancel','block'])
def test_filter_job_outcome_reconciles_dependants_in_same_transaction(tmp_path,outcome):
    from oko.processing.contracts import FilterResult
    from oko.services.processing import ProcessingService
    from oko.subscriptions.subscriptions import AddFeed, Overrides
    store, item, execution, _ = worker_library(tmp_path)
    try:
        other=store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other',title='Other',folder='Other',verification='synthetic'))['id']
        service=ProcessingService(execution.require_store)
        with transaction(store.db):
            store.db.execute('INSERT INTO feed_publications(feed_id,publication_id) VALUES (%s,%s)',(other,item))
            store.subscriptions.update_settings('feed',other,Overrides(filter_enabled=False,theses=True))
            store.processing.reconcile_accepted_item(item)
            job=store.jobs.claim_processing('filter',store.clock())
            assert job is not None
            prepared=service.prepare(item,'filter')
            assert prepared.input is not None
            store.jobs.save_input(job,prepared.input)
        def stages():
            return {row['kind']:(row['status'],row['generation']) for row in store.db.execute(
                "SELECT kind,status,generation FROM jobs WHERE item=%s AND kind IN ('filter','theses')",(item,))}
        before=stages()
        assert before['filter'][0]=='running' and before['theses'][0]=='blocked'
        def apply():
            if outcome=='finish':
                service.accept(prepared.input,FilterResult(hidden=False,reason='',removed=[]),None)
                store.jobs.finish(job)
            elif outcome=='retry':
                store.jobs.retry(job,store.clock(),'Synthetic error')
            else:
                getattr(store.jobs,outcome)(job)
            states=stages()
            assert states['filter'][0]=={'finish':'done','retry':'pending','cancel':'cancelled','block':'blocked'}[outcome]
            assert states['theses'][0]=={'finish':'pending','retry':'blocked','cancel':'cancelled','block':'pending'}[outcome]
        with pytest.raises(ValueError, match='synthetic rollback'):
            with transaction(store.db):
                apply()
                raise ValueError('synthetic rollback')
        assert stages()==before and store.jobs.valid(job)
        assert saved(store,item)['filtered_content']=={}
        with transaction(store.db):
            apply()
    finally:
        store.db.close()

def test_nested_service_uses_one_begin_and_a_caught_failure_cannot_commit(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    statements=[]
    store.db.set_trace_callback(statements.append)
    with pytest.raises(TransactionAborted):
        with store.jobs.transaction(lambda:True):
            store.db.execute("INSERT INTO state VALUES ('outer-write','bad')")
            try:
                with store.db:
                    store.db.execute("INSERT INTO state VALUES ('inner-write','bad')")
                    raise ValueError('caught nested failure')
            except ValueError:pass
    assert sum(query.startswith('BEGIN') for query in statements)==1
    assert store.db.execute("SELECT 1 FROM state WHERE key IN ('outer-write','inner-write')").fetchone() is None
    with store.jobs.transaction(lambda:True):store.db.execute("INSERT INTO state VALUES ('next-write','ok')")
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


@pytest.mark.parametrize('state',['failed','unavailable','cancelled','source_wait'])
def test_periodic_pass_keeps_stopped_work_and_source_wait_after_restart(tmp_path,state):
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        job=store.jobs.claim_processing('filter',store.clock())
        assert job
        worker.service.prepare(item,'filter')
        store.db.execute("UPDATE jobs SET status = %s , wait_scope = %s , retry_at = %s , next = %s , error = 'saved failure' WHERE id = %s",
            ('pending' if state=='source_wait' else state,'source:synthetic' if state=='source_wait' else None,
             store.wall_clock()+120 if state=='source_wait' else None,store.clock()+120 if state=='source_wait' else 0,job.id))
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    execution._store=reopened
    calls=[]
    asyncio.run(pass_worker(workers(execution,tasks,transport(calls,theses=True))[0],tasks))
    row=reopened.db.execute('SELECT status , wait_scope , retry_at , error FROM jobs WHERE id = %s',(job.id,)).fetchone()
    assert row['status']==('pending' if state=='source_wait' else state)
    assert row['error']=='saved failure' and not calls
    if state=='source_wait':assert row['wait_scope']=='source:synthetic' and row['retry_at']>reopened.wall_clock()
    reopened.db.close()


def test_common_feed_context_survives_restart_without_second_feed_substitution(tmp_path):
    from oko.subscriptions.subscriptions import AddFeed,Overrides
    store,item,execution,tasks=worker_library(tmp_path)
    second=store.subscriptions.add_feed(AddFeed(url='https://second.example/feed',title='Second',folder='Other',verification='synthetic'))['id']
    with store.db:
        store.db.execute('INSERT INTO feed_publications VALUES ( %s , %s )',(second,item))
    store.subscriptions.update_settings('feed',second,Overrides(prompt_filters='Second filter',prompt_theses='Second summary',theses=False))
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    context=saved(store,item)['processing_metadata']['_pass']
    assert context['feed_id']=='demo'
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    execution._store=reopened
    calls=[]
    asyncio.run(pass_worker(workers(execution,tasks,transport(calls,theses=True))[1],tasks))
    assert saved(reopened,item)['processing_metadata']['_pass']==context
    request=json.loads(json.loads(next(call.content for call in calls if call.url.path=='/v1/chat/completions'))['messages'][1]['content'])
    assert request['prompt']==context['settings']['values']['prompt_theses']!='Second summary'
    reopened.db.close()


def test_unchanged_filter_input_does_not_replace_running_attempt(tmp_path):
    from oko.subscriptions.subscriptions import FeedPatch
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/v1/chat/completions':entered.set();await release.wait()
            return native.handler(request)
        store,item,execution,tasks=worker_library(tmp_path)
        worker=workers(execution,tasks,httpx.MockTransport(model))[0]
        await worker.tick()
        await asyncio.wait_for(entered.wait(),2)
        original=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        signature=store.filters.signature(item)
        with store.jobs.transaction(lambda:True):
            store.subscriptions.update_feed('demo',FeedPatch(importance='high'))
            store.settings_changed('feed','demo')
            worker.service.reconcile()
        replacement=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        assert store.filters.signature(item)==signature
        assert replacement['generation']==original['generation'] and replacement['status']=='running'
        release.set()
        await tasks._ai
        assert saved(store,item)['filtered_content']
        assert store.db.execute("SELECT generation,status FROM jobs WHERE kind='filter'").fetchone()[:]==(replacement['generation'],'done')
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


def test_uncommitted_result_is_invisible_to_an_independent_reader(tmp_path):
    observed=[]
    def observe(point):
        if point=='processing_after_result':
            with database(tmp_path) as db:
                observed.append(db.execute("SELECT filtered_content,(SELECT status FROM jobs WHERE kind='filter') FROM publications").fetchone())
    store,item,execution,tasks=worker_library(tmp_path,fault=observe)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    assert observed==[('{}','running')]
    assert saved(store,item)['filtered_content']
    assert store.db.execute("SELECT status FROM jobs WHERE kind='filter'").fetchone()[0]=='done'
    store.db.close()


def test_recovery_preserves_old_choice_without_reusing_it_for_a_new_attempt(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        old=store.jobs.claim_processing('filter',store.clock())
        prepared=worker.service.prepare(item,'filter').input
        chosen=dict(processor='codex',model='saved-model',reasoning_effort='low',fast=False)
        store.jobs.save_origin(old,prepared.signature,chosen)
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    with reopened.jobs.transaction(lambda:True):
        new=reopened.jobs.claim_processing('filter',reopened.clock()+3601)
        assert new and new.generation>old.generation
        assert reopened.jobs.origin(new,prepared.signature) is None
        saved = reopened.db.execute('SELECT execution_settings FROM jobs WHERE id=%s', (new.id,)).fetchone()[0]
        assert saved['generation'] == old.generation and saved['parameters'] == chosen
    reopened.db.close()


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


def test_input_changed_during_adapter_check_preserves_replacement_without_open_sql(tmp_path):
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        native=transport([],theses=True)
        async def model(request):
            if request.url.path=='/props':entered.set();await release.wait()
            return native.handler(request)
        store,item,execution,tasks=worker_library(tmp_path)
        worker=workers(execution,tasks,httpx.MockTransport(model))[0]
        await worker.tick()
        await asyncio.wait_for(entered.wait(),2)
        old_generation=store.db.execute("SELECT generation FROM jobs WHERE kind='filter'").fetchone()[0]
        source=StructuredSource(None)
        with store.jobs.transaction(lambda:True):
            CollectionService(execution.require_store).collected(item,source.describe(store.publication(item),Material('Title','Changed while checking')),manual=False)
        replacement=store.db.execute("SELECT status,generation FROM jobs WHERE kind='filter'").fetchone()[:]
        assert replacement[0]=='pending' and replacement[1]>old_generation
        release.set()
        await tasks._ai
        assert not store.db.in_transaction
        assert saved(store,item)['filtered_content']=={}
        assert store.db.execute("SELECT status,generation FROM jobs WHERE kind='filter'").fetchone()[:]==replacement
        store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('status',['failed','unavailable','cancelled'])
def test_terminal_job_is_not_reopened_by_missing_payload_revision(tmp_path,status):
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        store.db.execute("UPDATE jobs SET status = %s , error = 'Permanent failure' WHERE kind = 'filter'",(status,))
        store.db.execute("UPDATE jobs SET payload='{}' WHERE kind='filter'")
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    execution._store=reopened
    calls=[]
    asyncio.run(pass_worker(workers(execution,tasks,transport(calls,theses=True))[0],tasks))
    assert reopened.db.execute("SELECT status,error FROM jobs WHERE kind='filter'").fetchone()[:]==(status,'Permanent failure')
    assert not calls
    reopened.db.close()


@pytest.mark.parametrize('outcome',['model','no_input','waiting','disabled'])
def test_preparation_distinguishes_all_four_domain_outcomes(tmp_path,outcome):
    from oko.subscriptions.subscriptions import Overrides
    store,item,execution,tasks=worker_library(tmp_path,text='' if outcome=='no_input' else 'Useful text')
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        if outcome=='waiting':store.db.execute('UPDATE publications SET complete = 0 WHERE id = %s',(item,))
        if outcome=='disabled':
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=''))
            store.settings_changed()
        worker.service.reconcile()
        value=worker.service.prepare(item,'filter')
        assert value.outcome==outcome
        assert (value.input is not None)==(outcome in ('model','no_input'))
    store.db.close()


def test_return_to_previous_input_does_not_reuse_a_processing_revision(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    source=StructuredSource(None)
    service=CollectionService(execution.require_store)
    first=saved(store,item)['processing_metadata']['_revision']
    versions=[]
    for text in ('Different text','Useful text'):
        with store.jobs.transaction(lambda:True):
            _,effects=service.collected(item,source.describe(store.publication(item),Material('Title',text)),manual=False)
            store.jobs.apply_collection(effects)
        versions.append(saved(store,item)['processing_metadata']['_revision'])
    assert len(set([first,*versions]))==3
    store.db.close()


def test_task_cancelled_before_sql_entry_never_begins_or_owns_the_connection(tmp_path):
    async def scenario():
        store,item,execution,tasks=worker_library(tmp_path)
        worker=workers(execution,tasks)[0]
        trace=[]
        store.db.set_trace_callback(trace.append)
        await execution._actions.acquire()
        pending=asyncio.create_task(worker.tick())
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):await pending
        execution._actions.release()
        assert not any(statement.startswith('BEGIN') for statement in trace)
        with store.jobs.transaction(lambda:True):store.db.execute("INSERT INTO state VALUES ('after-cancel','ok')")
        assert not store.db.in_transaction
        store.db.close()
    asyncio.run(scenario())


def test_restart_after_new_input_before_the_processing_cron_keeps_the_work(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        interrupted=store.jobs.claim_processing('filter',store.clock())
        worker.service.prepare(item,'filter')
        source=StructuredSource(None)
        _,effects=CollectionService(execution.require_store).collected(item,source.describe(store.publication(item),Material('Title','Persisted newer input')),manual=False)
        store.jobs.apply_collection(effects)
        revision=saved(store,item)['processing_metadata']['_revision']
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    execution._store=reopened
    calls=[]
    asyncio.run(pass_worker(workers(execution,tasks,transport(calls,theses=True))[0],tasks))
    result=saved(reopened,item)
    assert result['processing_metadata']['_revision']==revision
    assert result['filtered_content']
    request=json.loads(json.loads(next(call.content for call in calls if call.url.path=='/v1/chat/completions'))['messages'][1]['content'])
    assert request['sources'][0]['text']=='Persisted newer input'
    row=reopened.db.execute('SELECT status , generation FROM jobs WHERE id = %s',(interrupted.id,)).fetchone()
    assert row['status']=='done' and row['generation']>interrupted.generation
    reopened.db.close()


def test_accepted_publication_reconciles_jobs_without_stage_queue(tmp_path):
    store, item, _, _ = worker_library(tmp_path)
    statements = []
    try:
        store.db.set_trace_callback(statements.append)
        with transaction(store.db):
            store.processing.reconcile_accepted_item(item)
            assert store.db.execute("SELECT 1 FROM jobs WHERE item=%s AND kind='filter'", (item,)).fetchone()
        assert not any('INSERT INTO processing_stage_dirty' in statement for statement in statements)
    finally:
        store.db.set_trace_callback(None)
        store.db.close()


def test_filter_claim_revokes_obsolete_theses_atomically(tmp_path):
    store, item, _, _ = worker_library(tmp_path)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,generation,input_snapshot) VALUES ('theses',%s,'running',7,%s::jsonb) ON CONFLICT(kind,item) DO UPDATE SET status='running',generation=7,input_snapshot=excluded.input_snapshot", (item,'{"signature":"old"}'))
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                assert store.jobs.claim_processing('filter',store.clock()) is not None
                assert store.db.execute("SELECT status,generation FROM jobs WHERE kind='theses' AND item=%s", (item,)).fetchone()[:] == ('cancelled',8)
                raise ValueError('rollback')
        assert store.db.execute("SELECT status,generation FROM jobs WHERE kind='theses' AND item=%s", (item,)).fetchone()[:] == ('running',7)
        with transaction(store.db):
            assert store.jobs.claim_processing('filter',store.clock()) is not None
        assert store.db.execute("SELECT status,generation FROM jobs WHERE kind='theses' AND item=%s", (item,)).fetchone()[:] == ('cancelled',8)
    finally:
        store.db.close()


def test_viewed_publication_kept_as_link_revokes_own_processing_atomically(tmp_path):
    from oko.storage import memberships
    store, item, _, _ = worker_library(tmp_path)
    try:
        with transaction(store.db):
            memberships.lock_graph(store.db)
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'root','root-source','https://example.org/root','rss','root',1,content,title FROM publications WHERE id=%s""", (item,))
            memberships.add(store.db,'demo','root')
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root',%s)", (item,))
            job = store.jobs.claim_processing('filter',store.clock())
            assert job is not None and job.item == item
        with store.db:
            store.db.execute("UPDATE publications SET filtered_content=%s,summarized_content=%s WHERE id=%s", ('{"hidden":false,"reason":"","removed":[]}','{"points":[]}',item))
        original = store.db.execute('SELECT content FROM publications WHERE id=%s', (item,)).fetchone()[0]
        store.processing.background = True
        def view():
            assert store.viewed_set([item])
            assert not store.jobs.valid(job)
            row = store.db.execute('SELECT complete,viewed,content,filtered_content,summarized_content FROM publications WHERE id=%s', (item,)).fetchone()
            assert row[:] == (1,0,original,'{}','{}')
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                view()
                raise ValueError('rollback')
        assert store.jobs.valid(job)
        assert store.db.execute('SELECT filtered_content FROM publications WHERE id=%s', (item,)).fetchone()[0] != '{}'
        assert store.db.execute('SELECT 1 FROM feed_publications WHERE publication_id=%s', (item,)).fetchone()
        with transaction(store.db):
            view()
    finally:
        store.db.close()


def test_startup_reconciles_publications_without_seeding_dirty_queue(tmp_path):
    store, item, _, _ = worker_library(tmp_path)
    statements = []
    try:
        with store.db:
            store.db.execute("DELETE FROM jobs WHERE kind='filter' AND item=%s", (item,))
        store.db.set_trace_callback(statements.append)
        for _ in store.processing.recovery_steps(seed=True):
            pass
        assert store.db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[0] == 'pending'
        assert not any('INSERT INTO processing_dirty' in statement for statement in statements)
    finally:
        store.db.set_trace_callback(None)
        store.db.close()


def test_read_repair_reconciles_without_queue_and_survives_rollback(tmp_path):
    store, item, _, _ = worker_library(tmp_path)
    try:
        store.processing.repair(item)
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                store.processing.sync()
                assert item not in store.processing._repairs
                raise ValueError('rollback')
        assert item in store.processing._repairs
        store.processing.sync()
        assert item not in store.processing._repairs
    finally:
        store.db.close()


def test_rule_round_trip_revokes_running_attempt_before_background_reconciliation(tmp_path):
    from oko.services.processing import ProcessingService
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, _ = worker_library(tmp_path)
    try:
        store.processing.background = True
        with transaction(store.db):
            job = store.jobs.claim_processing('filter',store.clock())
            assert job is not None
            prepared = ProcessingService(execution.require_store).prepare(item,'filter')
            assert prepared.input is not None
            store.jobs.save_input(job,prepared.input)
        original = store.subscriptions.effective('feed','demo',details=False)['effective']['prompt_filters']['value']
        service = SubscriptionService(execution.require_store)
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                service.update_settings('feed','demo',Overrides(prompt_filters='New rules'))
                assert not store.jobs.valid(job)
                raise ValueError('rollback')
        assert store.jobs.valid(job)
        with transaction(store.db):
            service.update_settings('feed','demo',Overrides(prompt_filters='New rules'))
        with transaction(store.db):
            service.update_settings('feed','demo',Overrides(prompt_filters=original))
        assert not store.jobs.valid(job)
    finally:
        store.db.close()


def test_inherited_noop_and_theses_rule_do_not_revoke_running_filter(tmp_path):
    from oko.services.processing import ProcessingService
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, _ = worker_library(tmp_path)
    try:
        service = SubscriptionService(execution.require_store)
        original = store.subscriptions.effective('feed','demo',details=False)['effective']['prompt_filters']['value']
        with transaction(store.db):
            service.update_settings('feed','demo',Overrides(prompt_filters=original))
            store.processing.reconcile_accepted_item(item)
            job = store.jobs.claim_processing('filter',store.clock())
            assert job is not None
            prepared = ProcessingService(execution.require_store).prepare(item,'filter')
            assert prepared.input is not None
            store.jobs.save_input(job,prepared.input)
        store.processing.background = True
        with transaction(store.db):
            service.update_settings('oko','global',Overrides(prompt_filters='Overridden global rule'))
        assert store.jobs.valid(job)
        with transaction(store.db):
            service.update_settings('feed','demo',Overrides(prompt_theses='New summary rules'))
        assert store.jobs.valid(job)
    finally:
        store.db.close()


def test_content_round_trip_rejects_old_filter_attempt(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        native = transport([],theses=True)
        async def model(request):
            if request.url.path == '/v1/chat/completions':
                entered.set()
                await release.wait()
            return native.handler(request)
        store, item, execution, tasks = worker_library(tmp_path)
        try:
            worker = workers(execution,tasks,httpx.MockTransport(model))[0]
            await worker.tick()
            await asyncio.wait_for(entered.wait(),2)
            old = dict(store.db.execute("SELECT id,generation FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
            source = StructuredSource(None)
            for text in ('Temporary change','Useful text'):
                material = source.describe(store.publication(item),Material('Title',text))
                with transaction(store.db):
                    CollectionService(execution.require_store).collected(item,material,manual=False)
            current = dict(store.db.execute("SELECT generation,status FROM jobs WHERE id=%s", (old['id'],)).fetchone())
            assert current['generation'] > old['generation'] and current['status'] == 'pending'
            release.set()
            await tasks._ai
            assert saved(store,item)['filtered_content'] == {}
            assert store.db.execute('SELECT generation,status FROM jobs WHERE id=%s', (old['id'],)).fetchone()[:] == (current['generation'],'pending')
        finally:
            release.set()
            store.db.close()
    asyncio.run(scenario())


def test_display_only_setting_does_not_enqueue_processing(tmp_path):
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, _ = worker_library(tmp_path)
    try:
        store.processing.background = True
        before = tuple(store.db.execute("SELECT status,generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
        with transaction(store.db):
            SubscriptionService(execution.require_store).update_settings('feed','demo',Overrides(originals=False))
        assert tuple(store.db.execute("SELECT status,generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()) == before
    finally:
        store.db.close()


def test_rule_change_updates_pending_job_without_a_dirty_marker(tmp_path):
    from oko.services.subscriptions import SubscriptionService
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, _ = worker_library(tmp_path)
    try:
        store.processing.background = True
        before = dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
        service = SubscriptionService(execution.require_store)
        def change():
            service.update_settings('feed','demo',Overrides(prompt_filters='New rules'))
            after = dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone())
            assert after['generation'] > before['generation']
            assert after['input_snapshot'] != before['input_snapshot']
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                change()
                raise ValueError('rollback')
        assert dict(store.db.execute("SELECT generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()) == before
        with transaction(store.db):
            change()
    finally:
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


def test_missing_subtitles_schedule_one_read_only_repair(tmp_path):
    from oko.storage import material_read, youtube_media
    from oko.storage.transaction import read_snapshot
    store, item, _, _ = worker_library(tmp_path)
    try:
        with store.db:
            row = store.db.execute('SELECT content FROM publications WHERE id=%s', (item,)).fetchone()
            content = json.loads(row[0])
            content['extra']['youtube'] = {'captions':'available','captions_digest':'missing'}
            content['blocks'].append(dict(type='media',role='subtitles',media_id='missing-caption'))
            store.db.execute("UPDATE publications SET platform='youtube',content=%s WHERE id=%s", (json.dumps(content),item))
            store.db.execute("""INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status,storage_key)
                VALUES ('missing-caption',%s,'youtube:subtitles','https://example.test/captions','captions.txt','ready','missing-caption.txt')""", (item,))
        with read_snapshot(store.db):
            assert youtube_media.subtitle_path(store,'missing-caption') is None
            assert item in store.processing._repairs
        assert store.processing.pending()
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                store.processing.sync()
                raise ValueError('rollback')
        assert item in store.processing._repairs
        assert store.db.execute("SELECT status FROM media_files WHERE id='missing-caption'").fetchone()[0] == 'ready'
        assert store.db.execute("SELECT 1 FROM jobs WHERE kind='download_media' AND item='missing-caption'").fetchone() is None
        store.processing.sync()
        assert item not in store.processing._repairs
        row = store.db.execute("SELECT status,storage_key FROM media_files WHERE id='missing-caption'").fetchone()
        assert row[:] == ('pending_download', None)
        assert store.db.execute("SELECT status FROM jobs WHERE kind='download_media' AND item='missing-caption'").fetchone()[0] == 'pending'
        store.db.close()
        store = Store(tmp_path, lambda _: None)
        assert store.db.execute("SELECT status FROM jobs WHERE kind='download_media' AND item='missing-caption'").fetchone()[0] == 'pending'
    finally:
        store.db.close()


def test_viewing_one_component_does_not_clear_unrelated_orphan(tmp_path):
    store, item, _, _ = worker_library(tmp_path)
    try:
        with store.db:
            store.db.execute("""INSERT INTO publications(id,url,platform,external_id,complete,content,title)
                SELECT 'unrelated','https://example.test/unrelated','other_web','unrelated',1,content,title
                FROM publications WHERE id=%s""", (item,))
        before = store.db.execute("SELECT content,complete,viewed FROM publications WHERE id='unrelated'").fetchone()[:]
        with transaction(store.db):
            assert store.viewed_set([item])
        assert store.db.execute("SELECT content,complete,viewed FROM publications WHERE id='unrelated'").fetchone()[:] == before
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


@pytest.mark.parametrize('parent_first',[True,False])
def test_discovery_page_shares_linked_article_with_its_own_membership(tmp_path,parent_first):
    from dataclasses import replace
    from oko.collection.contracts import Publication
    from tests.support.source import publication_packet
    store, _, _, _ = worker_library(tmp_path)
    try:
        parent = publication_packet(Publication('parent','https://fixture.test/parent',1),Material('Parent','Parent text'),needs_fetch=False)
        child = publication_packet(Publication('child','https://fixture.test/child',2),Material('Child','Child text'),needs_fetch=False)
        assert parent.teaser and parent.teaser.adapter_content and child.adapter_reference
        parent = replace(parent,teaser=replace(parent.teaser,adapter_content=replace(
            parent.teaser.adapter_content,linked_publications=(child.adapter_reference,))))
        page = [parent,child] if parent_first else [child,parent]
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.collect_discovery(page,'{}')
                assert store.db.execute("SELECT 1 FROM publications WHERE url='https://fixture.test/child'").fetchone()
                raise ValueError('rollback')
        assert store.db.execute("SELECT 1 FROM publications WHERE url IN ('https://fixture.test/parent','https://fixture.test/child')").fetchone() is None
        with transaction(store.db):
            store.collect_discovery(page,'{}')
        rows=store.db.execute("SELECT id,complete,content FROM publications WHERE url='https://fixture.test/child'").fetchall()
        assert len(rows)==1 and rows[0]['complete']==1 and 'Child text' in rows[0]['content']
        assert store.db.execute("SELECT 1 FROM feed_publications WHERE feed_id='demo' AND publication_id=%s", (rows[0]['id'],)).fetchone()
        parent_id = store.db.execute("SELECT source_publication_id FROM publication_links WHERE target_publication_id=%s", (rows[0]['id'],)).fetchone()[0]
        from oko.storage.processing_input import signature
        job = store.db.execute("SELECT input_snapshot->>'signature' FROM jobs WHERE kind='filter' AND item=%s", (parent_id,)).fetchone()
        assert job and job[0] == signature(store, parent_id, 'filter')
        child_id = rows[0]['id']
        assert store.publication(child_id).id == 'child'
        # A pre-existing link placeholder with a membership is repaired too.
        with transaction(store.db):
            store.db.execute('UPDATE publications SET source_id=%s WHERE id=%s', ('linked:'+child_id,child_id))
            store.collect_discovery([child],'{}')
        assert store.publication(child_id).id == 'child'
        store.db.close()
        store = Store(tmp_path,lambda _:None)
        assert store.publication(child_id).id == 'child'
    finally:
        store.db.close()


def test_changed_title_discards_old_results_and_rolls_back_atomically(tmp_path):
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution, tasks)[0], tasks))
        asyncio.run(pass_worker(workers(execution, tasks)[1], tasks))
        before = saved(store, item)
        assert before['filtered_content'] and before['summarized_content']
        material = StructuredSource(None).describe(store.publication(item), Material('New title', 'Useful text'))
        def change():
            CollectionService(execution.require_store).collected(item, material, manual=False)
            after = saved(store, item)
            assert after['filtered_content'] == {} and after['summarized_content'] == {}
            assert 'filter' not in after['processing_metadata'] and 'summary' not in after['processing_metadata']
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                change()
                raise ValueError('rollback')
        assert saved(store, item)['filtered_content'] == before['filtered_content']
        assert saved(store, item)['summarized_content'] == before['summarized_content']
        with transaction(store.db):
            change()
    finally:
        store.db.close()


def test_linked_membership_privacy_change_revokes_parent_attempt(tmp_path):
    from dataclasses import replace
    from oko.collection.contracts import Publication
    from oko.storage import memberships
    from oko.subscriptions.subscriptions import AddFeed, FeedPatch
    from tests.support.source import publication_packet

    store, item, _, _ = worker_library(tmp_path)
    try:
        other = store.subscriptions.add_feed(AddFeed(url='https://fixture.test/other', title='Other', folder='Other', verification='synthetic'))['id']
        packet = publication_packet(Publication('child', 'https://fixture.test/child', 2), Material('Child', 'Child text'), needs_fetch=False)
        with transaction(store.db):
            store.subscriptions.update_feed('demo', FeedPatch(public_ai=True))
            store.db.execute("""INSERT INTO publications(id,source_id,url,platform,external_id,complete,content,title)
                SELECT 'linked','linked:linked',%s,%s,%s,1,content,title FROM publications WHERE id=%s""",
                (packet.url,packet.adapter_reference.key.platform.value,packet.adapter_reference.key.external_id,item))
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES (%s,'linked')", (item,))
            store.processing.reconcile_accepted_item(item)
            job = store.jobs.claim_processing('filter', store.clock())
            assert job and job.item == item
        observed = replace(packet, teaser=None, observed_only=True)
        def change():
            store.collect_discovery([observed], '{}', feed=other)
            assert not store.jobs.valid(job)
        with pytest.raises(ValueError, match='rollback'):
            with transaction(store.db):
                change()
                raise ValueError('rollback')
        assert store.jobs.valid(job)
        with transaction(store.db):
            change()
            second = store.jobs.claim_processing('filter', store.clock())
            assert second and second.item == item
        with transaction(store.db):
            store.remove_feed(other)
            assert not store.jobs.valid(second)
        assert not store.jobs.valid(job)
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


def test_discovery_page_saves_success_with_its_continuation_atomically(tmp_path, monkeypatch):
    from oko.collection.contracts import DiscoveryResult, Publication
    from tests.support.worker_library import collection_worker
    class Paginated:
        changes = None
        async def discover(self, cursor):
            return DiscoveryResult((Publication('next','https://example.org/next',123,Material('Next','Synthetic page')),),
                                   'page-two',continuation=30)
    store, _, _, _ = worker_library(tmp_path)
    try:
        with store.db:
            store.db.execute("UPDATE feeds SET cursor='',last_collection_result='{}'::jsonb WHERE id='demo'")
            store.jobs.enqueue_discovery()
        job=store.jobs.next_job(store.clock(),feeds=['demo'])
        assert job and job.kind=='discover'
        worker=collection_worker(tmp_path,store,StructuredSource(Paginated()))
        class ProcessStopped(BaseException):
            pass
        def stop_after_commit(*args, **kwargs):
            raise ProcessStopped()
        monkeypatch.setattr(worker.service, 'feed_status', stop_after_commit)
        with pytest.raises(ProcessStopped):
            asyncio.run(worker._execute(job))
        row=store.db.execute("SELECT cursor,last_collection_result FROM feeds WHERE id='demo'").fetchone()
        assert json.loads(row['cursor'])['synthetic_cursor']=='page-two'
        assert row['last_collection_result'].get('success_at')
        assert store.db.execute("SELECT status FROM jobs WHERE id=%s", (job.id,)).fetchone()[0]=='pending'
    finally:
        store.db.close()


def test_summary_prompt_change_keeps_completed_filter_attempt(tmp_path):
    from oko.subscriptions.subscriptions import Overrides
    store, item, execution, tasks = worker_library(tmp_path)
    try:
        asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
        asyncio.run(pass_worker(workers(execution,tasks)[1],tasks))
        before = store.db.execute("SELECT status,generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[:]
        assert before[0] == 'done'
        with transaction(store.db):
            store.subscriptions.update_settings('feed','demo',Overrides(prompt_theses='Different summary prompt'))
            store.settings_changed('feed','demo')
        after = store.db.execute("SELECT status,generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[:]
        assert after == before
        with transaction(store.db):
            store.processing.reconcile_accepted_item(item)
            store.jobs.invalidate_processing()
        assert store.db.execute("SELECT status,generation,input_snapshot FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()[:] == before
        assert store.processing.theses.read(item) is None
    finally:
        store.db.close()


def test_failed_discovery_and_diagnostic_roll_back_together(tmp_path, monkeypatch):
    from oko.collection.contracts import SourceError
    from tests.support.worker_library import collection_worker
    class Failing:
        changes = None
        async def discover(self, cursor):
            raise SourceError('Synthetic source failure')
    store, _, _, _ = worker_library(tmp_path)
    try:
        with store.db:
            store.jobs.enqueue_discovery()
        job = store.jobs.next_job(store.clock(), feeds=['demo'])
        assert job and job.kind == 'discover'
        worker = collection_worker(tmp_path,store,StructuredSource(Failing()))
        def fail_diagnostic(*args, **kwargs):
            raise RuntimeError('diagnostic failure')
        monkeypatch.setattr(worker,'_finish_collection',fail_diagnostic)
        with pytest.raises(RuntimeError, match='diagnostic failure'):
            asyncio.run(worker._execute(job))
        row = store.db.execute('SELECT status,error FROM jobs WHERE id=%s',(job.id,)).fetchone()
        assert row[:] == ('running',None)
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
        candidates=store.problems.candidates(5)
        assert candidates, candidates
        legacy=next(c for c in candidates if c['id']=='job:delete:demo:delete:item')
        legacy.setdefault('operation','producer:delete')
        legacy.pop('_legacy_ids',None)
        assert store.problems.deletion_candidates(job.id,5)[0] == legacy
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


def test_failed_feed_delete_problem_finishes_with_feed_removal(tmp_path, postgres_url):
    import json
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','deleting','[]')")
            row=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('feed-delete','feed','running',1) RETURNING id").fetchone()
        job=Job(row['id'],'feed-delete','feed',1)
        store.jobs.retry(job,1,'Ошибка удаления',code='delete')
        problem=store.db.execute("SELECT id,body FROM reader_problems WHERE identity='job:delete:feed:feed-delete:feed' AND finished IS NULL").fetchone()
        assert problem is not None and json.loads(problem['body'])['objects']==[{'kind':'feed','id':'feed'}]
        legacy=next(c for c in store.problems.candidates(5) if c['id']=='job:delete:feed:feed-delete:feed')
        legacy.setdefault('operation','producer:feed-delete')
        legacy.pop('_legacy_ids',None)
        assert store.problems.deletion_candidates(job.id,5)[0] == legacy
        with store.db:
            store.db.execute('UPDATE jobs SET next=0 WHERE id=%s',(job.id,))
        claimed=store.jobs.next_job(1,deleting_only=True)
        assert claimed is not None and claimed.id==job.id
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.finish_feed_deletion(claimed)
                assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem['id'],)).fetchone()['outcome']=='resolved'
                raise ValueError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(problem['id'],)).fetchone()['finished'] is None
        store.jobs.finish_feed_deletion(claimed)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem['id'],)).fetchone()['outcome']=='resolved'
        assert store.db.execute("SELECT 1 FROM feeds WHERE id='feed'").fetchone() is None
    finally:
        store.db.close()


def test_resurrected_publication_closes_failed_delete_problem_atomically(tmp_path, postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',1)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        job=Job(job_id,'delete','item',1)
        store.jobs.retry(job,1,'Ошибка удаления',code='delete')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:item:delete:item' AND finished IS NULL").fetchone()['id']
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.jobs.apply_collection(CollectionEffects(fetch={'item':False}))
                assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem,)).fetchone()['outcome']=='resolved'
                raise ValueError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(problem,)).fetchone()['finished'] is None
        with transaction(store.db):
            store.jobs.apply_collection(CollectionEffects(fetch={'item':False}))
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem,)).fetchone()['outcome']=='resolved'
    finally:
        store.db.close()



@pytest.mark.parametrize('kind', ['delete','feed-delete'])
@pytest.mark.parametrize('status', ['pending','running','failed','unavailable','blocked'])
@pytest.mark.parametrize('code,error', [(None,'synthetic failure'),('delete','synthetic failure'),('',None),(None,'')])
def test_delete_problem_matches_status_projection(tmp_path, postgres_url, kind, status, code, error):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job_id=store.db.execute('INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,%s,%s,%s,%s) RETURNING id',
                                    (kind,'item',status,error,code)).fetchone()[0]
        current=store.problems.deletion_candidates(job_id,7)[0]
        candidates=store.problems.candidates(7)
        expected=next(c for c in candidates if c['id']==current['id'])
        expected.setdefault('operation','producer:'+kind)
        expected.pop('_legacy_ids',None)
        assert current == expected
    finally:
        store.db.close()


def test_status_groups_source_failures_by_publication_owner_without_job_feed(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('one','rss','one',0),('two','rss','two',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','one',true,true),('feed','two',true,true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','one','failed','failure','source-connection'),('fetch','two','failed','failure','source-connection')")
        problems=store.problems.snapshot(store.wall_clock())['current']
        related=[p for p in problems if p['code']=='source-connection']
        assert len(related)==1
        assert store.db.execute('SELECT identity FROM reader_problems WHERE id=%s',(related[0]['id'],)).fetchone()[0]=='job:source-connection:feed:fetch'
        assert {obj['id'] for obj in related[0]['objects']}=={'one','two'}
    finally:
        store.db.close()


def test_status_groups_media_failures_by_publication_owner_without_job_feed(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('one','rss','one',0),('two','rss','two',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','one',true,true),('feed','two',true,true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m1','one','m1','https://a.test','one','pending_download'),('m2','two','m2','https://b.test','two','pending_download')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m1','failed','failure','source-connection'),('download_media','m2','failed','failure','source-connection')")
        related=[p for p in store.problems.snapshot(store.wall_clock())['current'] if p['code']=='source-connection']
        assert len(related)==1
        assert {obj['id'] for obj in related[0]['objects']}=={'one','two'}
    finally:
        store.db.close()


def test_status_uses_root_owner_for_linked_publication_failure(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('root','rss','root',0),('linked','other_web','linked',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','root',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','linked')")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','linked','failed','failure','source-connection')")
        related=[p for p in store.problems.snapshot(store.wall_clock())['current'] if p['code']=='source-connection']
        assert len(related)==1
        assert store.db.execute('SELECT identity FROM reader_problems WHERE id=%s',(related[0]['id'],)).fetchone()[0]=='job:source-connection:feed:fetch'
        assert related[0]['objects']==[{'kind':'material','id':'root'}]
    finally:
        store.db.close()


def test_profile_problem_suppresses_publication_job_without_saved_job_feed(tmp_path, postgres_url):
    import json
    from oko.services.connections import ConnectionService
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','telegram','telegram','connected','[]')")
            store.db.execute("INSERT INTO telegram_profiles(role,account,user_id,generation,state,checked) VALUES ('personal','one',101,1,'ready',1)")
            store.db.execute('INSERT INTO telegram_bindings(feed,binding) VALUES (%s,%s)',('feed',json.dumps(dict(kind='client',role='personal',account_user_id='101',peer_id='123'))))
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','telegram','item',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','failed','failure','identity-unconfirmed')")
        assert not ConnectionService(lambda:store).checked('personal',1,202,2)
        problems=store.problems.snapshot(2)['current']
        assert [p['code'] for p in problems]==['account_identity_mismatch']
    finally:
        store.db.close()


def test_youtube_pause_counts_publication_when_job_feed_is_missing(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','youtube','youtube','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','youtube','item',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','pending','paused','youtube-admission')")
            store.youtube.blocked(1,900)
        problems=store.problems.snapshot(2)['current']
        paused=next(p for p in problems if p['code']=='youtube-admission')
        assert paused['objects']==[{'kind':'material','id':'item'}]
    finally:
        store.db.close()


def test_delete_problem_uses_cleanup_owner_without_saved_job_feed(tmp_path, postgres_url):
    import json
    from oko.storage.job_types import Job
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',1)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES ('delete','item','running',1,%s) RETURNING id",(json.dumps({'cleanup_feed':'feed'}),)).fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        assert store.db.execute("SELECT 1 FROM reader_problems WHERE identity='job:delete:feed:delete:item' AND finished IS NULL").fetchone()
    finally:
        store.db.close()


def test_delete_problem_tracks_all_linked_root_owners_and_cancellation(tmp_path, postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('r1','rss','r1',0),('r2','rss','r2',0),('item','other_web','item',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','r1',true,true),('b','r2',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('r1','item'),('r2','item')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        rows=store.db.execute("SELECT identity FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL ORDER BY identity").fetchall()
        assert [r[0] for r in rows]==['job:delete:a:delete:item','job:delete:b:delete:item']
        with transaction(store.db):
            store.jobs.apply_collection(CollectionEffects(fetch={'item':False}))
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL").fetchone()[0]==0
    finally:
        store.db.close()


def test_delete_problem_prefetch_ignores_unrelated_jobs(tmp_path, postgres_url):
    from tests.support.postgres_work import scan_rows
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) SELECT 'delete','foreign-'||n,'pending','failure','delete' FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','selected','pending','failure','delete')")
            store.db._db.execute('ANALYZE jobs')
        query="SELECT id,item FROM jobs WHERE kind='delete' AND item=ANY(%s) AND (error IS NOT NULL OR problem_code IS NOT NULL)"
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query, (['selected','missing'],)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,plan
    finally:
        store.db.close()


def test_grouping_correction_preserves_an_existing_problem_occurrence(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','failed','failure','source-connection')")
            old=dict(id='job:source-connection:item:fetch',code='source-connection',section='object',title='Не удалось загрузить материал',consequence='Сохранённое содержимое остаётся доступным.',next_step='Повторите загрузку вручную.',objects=[dict(kind='material',id='item')],action=None,operation='producer:fetch',created_at=1,state='current',finished_at=None,outcome=None,next_at=None,reason='failure')
            store.problems.sync_candidate(old['id'],old,1)
        original=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:source-connection:item:fetch' AND finished IS NULL").fetchone()[0]
        result=store.problems.snapshot(2)
        current=next(p for p in result['current'] if p['code']=='source-connection')
        assert current['id']==original
        assert not [p for p in result['recent'] if p['id']==original]
    finally:
        store.db.close()


def test_grouping_correction_merges_old_problems_without_losing_history(tmp_path, postgres_url):
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('one','rss','one',0),('two','rss','two',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','one',true,true),('feed','two',true,true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','one','failed','failure','source-connection'),('fetch','two','failed','failure','source-connection')")
            for index,item in enumerate(('one','two'),1):
                old=dict(id='job:source-connection:'+item+':fetch',code='source-connection',section='object',title='Не удалось загрузить материал',consequence='Сохранённое содержимое остаётся доступным.',next_step='Повторите загрузку вручную.',objects=[dict(kind='material',id=item)],action=None,operation='producer:fetch',created_at=index,state='current',finished_at=None,outcome=None,next_at=None,reason='failure')
                store.problems.sync_candidate(old['id'],old,index)
        originals=store.db.execute("SELECT id FROM reader_problems WHERE identity LIKE 'job:source-connection:%%' ORDER BY created").fetchall()
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                result=store.problems.snapshot(3)
                assert len(result['current'])==1 and result['current'][0]['id']==originals[0][0]
                raise ValueError('rollback')
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'job:source-connection:%%' AND finished IS NULL").fetchone()[0]==2
        result=store.problems.snapshot(3)
        assert len(result['current'])==1 and result['current'][0]['id']==originals[0][0]
        merged=next(p for p in result['recent'] if p['id']==originals[1][0])
        assert merged['outcome']=='irrelevant'
        assert {obj['id'] for obj in result['current'][0]['objects']}=={'one','two'}
    finally:
        store.db.close()


def test_grouping_correction_splits_one_occurrence_without_false_resolution(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        def candidate(identity):
            return dict(id=identity,code='source-connection',section='object',title='Ошибка',consequence='Сохранено',next_step='Повторите',objects=[dict(kind='material',id='item')],action=None,operation='producer:fetch',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
        old=candidate('job:source-connection:item:fetch')
        with store.db:store.problems.sync_candidate(old['id'],old,1)
        original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
        first=candidate('job:source-connection:a:fetch');first['_legacy_ids']={old['id']}
        second=candidate('job:source-connection:b:fetch');second['_legacy_ids']={old['id']}
        result=store.problems.reconcile([first,second],2,{})
        assert len(result['current'])==2
        assert original in {problem['id'] for problem in result['current']}
        assert not [problem for problem in result['recent'] if problem['id']==original]
    finally:
        store.db.close()


def test_grouping_correction_does_not_merge_distinct_jobs_sharing_root(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        def candidate(identity):
            return dict(id=identity,code='specific-failure',section='object',title='Ошибка',consequence='Сохранено',next_step='Повторите',objects=[dict(kind='material',id='root')],action=None,operation='producer:fetch',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
        old1=candidate('job:specific-failure:child1:fetch:child1')
        old2=candidate('job:specific-failure:child2:fetch:child2')
        with store.db:
            store.problems.sync_candidate(old1['id'],old1,1)
            store.problems.sync_candidate(old2['id'],old2,2)
        original={r['identity']:r['id'] for r in store.db.execute("SELECT identity,id FROM reader_problems WHERE finished IS NULL")}
        first=candidate('job:specific-failure:feed:fetch:child1');first['_legacy_ids']={old1['id']}
        second=candidate('job:specific-failure:feed:fetch:child2');second['_legacy_ids']={old2['id']}
        result=store.problems.reconcile([first,second],3,{})
        assert {p['id'] for p in result['current']}==set(original.values())
        assert result['recent']==[]
    finally:
        store.db.close()


def test_delete_retry_rekeys_existing_problem_without_waiting_for_status(tmp_path, postgres_url):
    import json
    from oko.storage.job_types import Job
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',1)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code,payload) VALUES ('delete','item','running',1,'failure','delete',%s) RETURNING id",(json.dumps({'cleanup_feed':'feed'}),)).fetchone()[0]
            old=dict(id='job:delete:item:delete:item',code='delete',section='object',title='Удаление не завершено',consequence='Око продолжает попытки удаления.',next_step='Око повторит попытку автоматически.',objects=[],action=None,operation='producer:delete',created_at=1,state='retrying',finished_at=None,outcome=None,next_at=None)
            store.problems.sync_candidate(old['id'],old,1)
        original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),2,'failure',code='delete')
        current=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:feed:delete:item' AND finished IS NULL").fetchone()
        assert current and current[0]==original
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE finished IS NULL").fetchone()[0]==1
    finally:
        store.db.close()


def test_delete_problem_owner_lookup_ignores_unrelated_links(tmp_path, postgres_url):
    from tests.support.postgres_work import scan_rows
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('root','rss','root',0),('item','other_web','item',1)")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) SELECT 'foreign-'||n,'other_web','foreign-'||n,0 FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'root','foreign-'||n FROM generate_series(1,10000) n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','item')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('delete','item','pending','failure','delete') RETURNING id").fetchone()[0]
            store.db._db.execute('ANALYZE publication_links')
        statements=[]
        store.db.set_trace_callback(statements.append)
        try:
            assert store.problems.deletion_candidates(job_id,1)
        finally:
            store.db.set_trace_callback(None)
        query=next(q for q in statements if 'FROM jobs j LEFT JOIN publications p' in q)
        plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,(job_id,)).fetchone()[0][0]['Plan']
        assert scan_rows(plan)<100,plan
    finally:
        store.db.close()


def test_grouping_correction_preserves_legacy_problem_without_operation(tmp_path, postgres_url):
    import json
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','item',true,true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','item','failed','failure','source-connection')")
            old=dict(id='job:source-connection:item:fetch',code='source-connection',section='object',title='Ошибка',consequence='Сохранено',next_step='Повторите',objects=[dict(kind='material',id='item')],action=None,created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
            store.problems.sync_candidate(old['id'],old,1)
            row=store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()
            body=json.loads(row['body']);body.pop('operation')
            store.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),row['id']))
        result=store.problems.snapshot(2)
        assert len(result['current'])==1 and result['current'][0]['id']==row['id']
        assert not [p for p in result['recent'] if p['id']==row['id']]
    finally:
        store.db.close()


def test_regroup_does_not_rekey_unrelated_problem_with_same_material(tmp_path, postgres_url):
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        def candidate(identity):
            return dict(id=identity,code='source-connection',section='object',title='Ошибка',consequence='Сохранено',next_step='Повторите',objects=[dict(kind='material',id='item')],action=None,operation='producer:fetch',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
        old=candidate('job:source-connection:other-feed:fetch')
        with store.db:store.problems.sync_candidate(old['id'],old,1)
        original=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
        current=candidate('job:source-connection:feed:fetch')
        current['_legacy_ids']={'job:source-connection:item:fetch'}
        result=store.problems.reconcile([current],2,{})
        assert len(result['current'])==1 and result['current'][0]['id']!=original
        assert next(p for p in result['recent'] if p['id']==original)['outcome']=='resolved'
    finally:
        store.db.close()


def test_review_real_discovery_captures_cleanup_problem_before_new_owner(tmp_path, postgres_url):
    import json
    from oko.collection.contracts import Publication
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.source import publication_packet
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('old','old','Old','default','rss','rss','connected','[]'),('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,source_id,url,platform,external_id,viewed) VALUES ('item','old:one','https://example.test/item','other_web','https://example.test/item',1)")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation,payload) VALUES ('delete','item','running',1,%s) RETURNING id",(json.dumps({'cleanup_feed':'old'}),)).fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:old:delete:item' AND finished IS NULL").fetchone()['id']
        packet=publication_packet(Publication('one','https://example.test/item',123))
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                effects=store.collect_discovery([packet],'{}','new')
                store.jobs.apply_collection(effects)
                assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem,)).fetchone()['outcome']=='resolved'
                raise ValueError('rollback')
        assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(problem,)).fetchone()['finished'] is None
        with transaction(store.db):
            effects=store.collect_discovery([packet],'{}','new')
            store.jobs.apply_collection(effects)
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem,)).fetchone()['outcome']=='resolved'
    finally:
        store.db.close()

def test_review_discovery_captures_multiple_prior_root_problems(tmp_path, postgres_url):
    from oko.collection.contracts import Publication
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.source import publication_packet
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]'),('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,source_id,url,platform,external_id,viewed) VALUES ('r1','a:r1','https://example.test/r1','other_web','https://example.test/r1',0),('r2','b:r2','https://example.test/r2','other_web','https://example.test/r2',0),('item','old:one','https://example.test/item','other_web','https://example.test/item',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','r1',true,true),('b','r2',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('r1','item'),('r2','item')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL").fetchone()[0]==2
        packet=publication_packet(Publication('one','https://example.test/item',123))
        with transaction(store.db):
            effects=store.collect_discovery([packet],'{}','new')
            store.jobs.apply_collection(effects)
            assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL").fetchone()[0]==0
    finally:
        store.db.close()

def test_review_link_removal_keeps_delete_problem_occurrence(tmp_path, postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from oko.storage import publication_links
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed) VALUES ('root','rss','root','https://example.test/root',0),('item','other_web','item','https://example.test/item',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','root',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','item')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:feed:delete:item' AND finished IS NULL").fetchone()['id']
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                effects=CollectionEffects()
                publication_links.sync(store,'root',(),effects)
                store.jobs.apply_collection(effects)
                assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:item:delete:item' AND finished IS NULL").fetchone()['id']==problem
                raise ValueError('rollback')
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:feed:delete:item' AND finished IS NULL").fetchone()['id']==problem
        with transaction(store.db):
            effects=CollectionEffects()
            publication_links.sync(store,'root',(),effects)
            store.jobs.apply_collection(effects)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:item:delete:item' AND finished IS NULL").fetchone()['id']==problem
    finally:
        store.db.close()

def test_review_discovery_closes_legacy_problem_before_status(tmp_path, postgres_url):
    import json
    from oko.collection.contracts import Publication
    from oko.storage.transaction import transaction
    from tests.support.source import publication_packet
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('old','old','Old','default','rss','rss','connected','[]'),('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,source_id,url,platform,external_id,viewed) VALUES ('item','old:one','https://example.test/item','other_web','https://example.test/item',1)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,payload) VALUES ('delete','item','pending','failure','delete',%s)",(json.dumps({'cleanup_feed':'old'}),))
            old=dict(id='job:delete:item:delete:item',code='delete',section='object',title='Ошибка',consequence='Сохранено',next_step='Повторите',objects=[],action=None,operation='producer:delete',created_at=1,state='current',finished_at=None,outcome=None,next_at=None)
            store.problems.sync_candidate(old['id'],old,1)
        problem=store.db.execute('SELECT id FROM reader_problems WHERE identity=%s',(old['id'],)).fetchone()[0]
        packet=publication_packet(Publication('one','https://example.test/item',123))
        with transaction(store.db):
            effects=store.collect_discovery([packet],'{}','new')
            store.jobs.apply_collection(effects)
            assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(problem,)).fetchone()[0]=='resolved'
    finally:
        store.db.close()

def test_review_removing_one_root_keeps_other_root_problem(tmp_path, postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from oko.storage import publication_links
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,url,viewed) VALUES ('r1','rss','r1','https://example.test/r1',0),('r2','rss','r2','https://example.test/r2',0),('item','other_web','item','https://example.test/item',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','r1',true,true),('b','r2',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('r1','item'),('r2','item')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        before={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL")}
        assert set(before)=={'job:delete:a:delete:item','job:delete:b:delete:item'}
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                effects=CollectionEffects();publication_links.sync(store,'r1',(),effects);store.jobs.apply_collection(effects)
                assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(before['job:delete:a:delete:item'],)).fetchone()[0]=='irrelevant'
                assert store.db.execute('SELECT finished FROM reader_problems WHERE id=%s',(before['job:delete:b:delete:item'],)).fetchone()[0] is None
                raise ValueError('rollback')
        assert store.db.execute("SELECT count(*) FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL").fetchone()[0]==2
        with transaction(store.db):
            effects=CollectionEffects();publication_links.sync(store,'r1',(),effects);store.jobs.apply_collection(effects)
        assert store.db.execute('SELECT id FROM reader_problems WHERE identity=%s AND finished IS NULL',('job:delete:b:delete:item',)).fetchone()[0]==before['job:delete:b:delete:item']
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(before['job:delete:a:delete:item'],)).fetchone()[0]=='irrelevant'
    finally:
        store.db.close()

def test_review_new_owner_rekeys_delete_problem_of_linked_child(tmp_path, postgres_url):
    from oko.collection.contracts import Publication
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.source import publication_packet
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('new','new','New','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,source_id,url,platform,external_id,viewed) VALUES ('root','linked:root','https://example.test/root','other_web','https://example.test/root',0),('child','linked:child','https://example.test/child','other_web','https://example.test/child',1)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','child','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','child',1),1,'failed',code='delete')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:child:delete:child' AND finished IS NULL").fetchone()['id']
        packet=publication_packet(Publication('one','https://example.test/root',123))
        with transaction(store.db):
            effects=store.collect_discovery([packet],'{}','new')
            store.jobs.apply_collection(effects)
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:new:delete:child' AND finished IS NULL").fetchone()[0]==problem
    finally:
        store.db.close()

def test_review_removing_primary_feed_rekeys_child_delete_problem(tmp_path, postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('root','rss','root',0),('child','other_web','child',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','root',true,true),('b','root',false,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','child','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','child',1),1,'failed',code='delete')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:a:delete:child' AND finished IS NULL").fetchone()['id']
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                store.remove_feed('a')
                assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:b:delete:child' AND finished IS NULL").fetchone()[0]==problem
                raise ValueError('rollback')
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:a:delete:child' AND finished IS NULL").fetchone()[0]==problem
        store.remove_feed('a')
        row=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:b:delete:child' AND finished IS NULL").fetchone()
        assert row is not None and row['id']==problem
    finally:
        store.db.close()

def test_review_viewed_root_rekeys_child_delete_problem_atomically(tmp_path, postgres_url):
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('root','rss','root',0),('child','other_web','child',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('feed','root',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','child')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','child','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','child',1),1,'failed',code='delete')
        problem=store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:feed:delete:child' AND finished IS NULL").fetchone()['id']
        with pytest.raises(ValueError,match='rollback'):
            with transaction(store.db):
                assert store.viewed_set(['root'])
                assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:child:delete:child' AND finished IS NULL").fetchone()[0]==problem
                raise ValueError('rollback')
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:feed:delete:child' AND finished IS NULL").fetchone()[0]==problem
        with transaction(store.db): assert store.viewed_set(['root'])
        assert store.db.execute("SELECT id FROM reader_problems WHERE identity='job:delete:child:delete:child' AND finished IS NULL").fetchone()[0]==problem
    finally:
        store.db.close()

def test_review_primary_change_preserves_both_delete_occurrences(tmp_path, postgres_url):
    from oko.storage.job_types import Job
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]'),('c','c','C','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('r1','rss','r1',0),('r2','rss','r2',0),('item','other_web','item',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','r1',true,true),('c','r1',false,true),('b','r2',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('r1','item'),('r2','item')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        before={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL")}
        store.remove_feed('a')
        after={r['identity']:r['id'] for r in store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL")}
        assert after=={'job:delete:b:delete:item':before['job:delete:b:delete:item'], 'job:delete:c:delete:item':before['job:delete:a:delete:item']}
    finally:
        store.db.close()

def test_review_two_owner_changes_preserve_both_occurrence_ids(tmp_path, postgres_url):
    from oko.domain.collection import CollectionEffects
    from oko.storage.job_types import Job
    from oko.storage.transaction import transaction
    from oko.storage import memberships, publication_links
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            for feed in ('a','b','c','d'):
                store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES (%s,%s,%s,'default','rss','rss','connected','[]')",(feed,feed,feed))
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('r1','rss','r1',0),('r2','rss','r2',0),('item','other_web','item',1)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','r1',true,true),('b','r2',true,true)")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('r1','item'),('r2','item')")
            job_id=store.db.execute("INSERT INTO jobs(kind,item,status,generation) VALUES ('delete','item','running',1) RETURNING id").fetchone()[0]
        store.jobs.retry(Job(job_id,'delete','item',1),1,'failed',code='delete')
        before={r['id'] for r in store.db.execute("SELECT id FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL")}
        with transaction(store.db):
            memberships.lock_graph(store.db)
            effects=CollectionEffects();publication_links.capture_root_deletion_problems(store,effects,{'r1','r2'})
            memberships.remove(store.db,items=['r1','r2'])
            memberships.add(store.db,'c','r1');memberships.add(store.db,'d','r2')
            store.jobs.sync_captured_deletions(effects)
        rows=store.db.execute("SELECT id,identity FROM reader_problems WHERE identity LIKE 'job:delete:%%' AND finished IS NULL").fetchall()
        assert {r['id'] for r in rows}==before
        assert {r['identity'] for r in rows}=={'job:delete:c:delete:item','job:delete:d:delete:item'}
    finally:
        store.db.close()
