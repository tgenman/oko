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
            store.jobs.reconcile('filter',worker.service.candidates('filter'))
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
        store.jobs.reconcile('filter',worker.service.candidates('filter'))
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


def test_revision_change_without_filter_signature_change_replaces_old_attempt(tmp_path):
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
            store.jobs.reconcile('filter',worker.service.candidates('filter'))
        replacement=dict(store.db.execute("SELECT * FROM jobs WHERE kind='filter'").fetchone())
        assert store.filters.signature(item)==signature
        assert replacement['generation']>original['generation'] and replacement['status']=='pending'
        release.set()
        await tasks._ai
        assert saved(store,item)['filtered_content']=={}
        assert store.db.execute("SELECT generation,status FROM jobs WHERE kind='filter'").fetchone()[:]==(replacement['generation'],'pending')
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
    with store.db:store.db.execute("UPDATE feeds SET cursor='' WHERE id='demo'")
    store.db.close()
    with TestClient(create_app(tmp_path,source=FailedSource(),token='test',api_only=True,ai_transport=transport([],theses=True)),headers=HEADERS) as client:
        wait_for(client,lambda:client.get('/api/v3/materials/'+item).json()['theses']['result'])
        def stopped_source():
            with database(tmp_path) as db:
                return db.execute("SELECT status FROM jobs WHERE kind='discover'").fetchone()[0]=='failed'
        wait_for(client,stopped_source)
        with database(tmp_path) as db:
            assert db.execute("SELECT kind,status FROM jobs WHERE kind IN ('filter','theses') ORDER BY kind").fetchall()==[('filter','done'),('theses','done')]
        assert client.get('/api/v3/materials/'+item).status_code==200


def test_uncommitted_result_is_invisible_to_an_independent_reader(tmp_path):
    observed=[]
    def observe(point):
        if point=='processing_after_result':
            with database(tmp_path) as db:
                observed.append(db.execute("SELECT filtered_content,(SELECT status FROM jobs WHERE kind='filter') FROM items").fetchone())
    store,item,execution,tasks=worker_library(tmp_path,fault=observe)
    asyncio.run(pass_worker(workers(execution,tasks)[0],tasks))
    assert observed==[('{}','running')]
    assert saved(store,item)['filtered_content']
    assert store.db.execute("SELECT status FROM jobs WHERE kind='filter'").fetchone()[0]=='done'
    store.db.close()


def test_recovery_keeps_execution_choice_for_the_interrupted_pass(tmp_path):
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        store.jobs.reconcile('filter',worker.service.candidates('filter'))
        old=store.jobs.claim_processing('filter',store.clock())
        prepared=worker.service.prepare(item,'filter').input
        chosen=dict(processor='codex',model='saved-model',reasoning_effort='low',fast=False)
        store.jobs.save_origin(old,prepared.signature,chosen)
    store.db.close()
    reopened=Store(tmp_path,lambda _:None)
    with reopened.jobs.transaction(lambda:True):
        new=reopened.jobs.claim_processing('filter',reopened.clock()+3601)
        assert new and new.generation>old.generation
        assert reopened.jobs.origin(new,prepared.signature)==chosen
    reopened.db.close()


def test_new_revision_does_not_shorten_source_wait(tmp_path):
    from oko.subscriptions.subscriptions import FeedPatch
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        store.jobs.reconcile('filter',worker.service.candidates('filter'))
        deadline=store.wall_clock()+120
        next_at=store.clock()+120
        store.db.execute("UPDATE jobs SET status = 'pending' , wait_scope = 'source:synthetic' , retry_at = %s , next = %s , error = 'source wait' WHERE kind = 'filter'",(deadline,next_at))
        store.subscriptions.update_feed('demo',FeedPatch(importance='high'))
        store.settings_changed('feed','demo')
        worker.service.reconcile()
        store.jobs.reconcile('filter',worker.service.candidates('filter'))
    row=store.db.execute("SELECT retry_at,next,wait_scope,error FROM jobs WHERE kind='filter'").fetchone()
    assert tuple(row)==(deadline,next_at,'source:synthetic','source wait')
    assert store.jobs.claim_processing('filter',store.clock()) is None
    store.db.close()


def test_input_changed_during_adapter_check_cancels_without_leaving_open_sql(tmp_path):
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
        source=StructuredSource(None)
        with store.jobs.transaction(lambda:True):
            CollectionService(execution.require_store).collected(item,source.describe(store.publication(item),Material('Title','Changed while checking')),manual=False)
        release.set()
        await tasks._ai
        assert not store.db.in_transaction
        assert saved(store,item)['filtered_content']=={}
        assert store.db.execute("SELECT status FROM jobs WHERE kind='filter'").fetchone()[0]=='cancelled'
        with store.jobs.transaction(lambda:True):
            worker.service.reconcile()
            store.jobs.reconcile('filter',worker.service.candidates('filter'))
        assert store.db.execute("SELECT status FROM jobs WHERE kind='filter'").fetchone()[0]=='pending'
        store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('status',['failed','unavailable','cancelled'])
def test_terminal_job_is_not_reopened_by_missing_payload_revision(tmp_path,status):
    store,item,execution,tasks=worker_library(tmp_path)
    worker=workers(execution,tasks)[0]
    with store.jobs.transaction(lambda:True):
        worker.service.reconcile()
        store.jobs.reconcile('filter',worker.service.candidates('filter'))
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
        if outcome=='waiting':store.db.execute('UPDATE items SET complete = 0 WHERE id = %s',(item,))
        if outcome=='disabled':
            store.subscriptions.update_settings('oko','global',Overrides(prompt_filters=''))
            store.settings_changed()
        worker.service.reconcile()
        store.jobs.reconcile('filter',worker.service.candidates('filter'))
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
        store.jobs.reconcile('filter',worker.service.candidates('filter'))
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
