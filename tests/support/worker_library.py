"""Real temporary storage for worker transaction and recovery scenarios."""
from tests.support.subscription_action import subscription_action
from tests.support.job_claim import claim_job
import json
from tests.support.storage import Store, ensure_fixture
from tests.support.legacy_source import Publication
from tests.support.synthetic_material import material as Material
from oko.subscriptions.subscriptions import Overrides
from oko.processing.config import ProcessorConfig
from oko.execution import Execution
from oko.storage.transaction import transaction
from oko.workers.control import JobControl
from oko.workers.processing import FilterWorker,SummaryWorker
from tests.support.collection_acceptance import discovered
from tests.support.source import publication_packet
from tests.support.codex import CodexService
from tests.support.filters import transport


def worker_library(root, *, text='Useful text', fault=lambda _:None, database_url=None):
    store=Store(root,fault,database_url=database_url)
    if database_url is not None:store.set_work_enabled(True)
    ensure_fixture(store.db)
    store.configure_processor(ProcessorConfig(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
    subscription_action(store, 'update_settings', 'oko','global',Overrides(processor='llamacpp',theses=True,prompt_filters='Keep useful text'))
    store.settings_changed()
    with transaction(store.db):
        store.jobs.enqueue_discovery('fixture')
    job=claim_job(store, store.clock(),feeds=['fixture'])
    assert job.kind=='discover'
    discovered(store,[publication_packet(Publication('one','https://example.org/one',100),Material('Title',text),needs_fetch=False)],'{}',job)
    item=store.db.execute('SELECT id FROM publications').fetchone()[0]
    execution=Execution(lambda:True)
    execution._store=store
    tasks=JobControl(execution,store.clock)
    return store,item,execution,tasks


def workers(execution,tasks,transport_override=None):
    model=transport_override or transport([],theses=True)
    return [kind(execution,tasks,execution.require_store().clock,CodexService(),model) for kind in (FilterWorker,SummaryWorker)]


async def pass_worker(worker,tasks):
    await worker.tick()
    if tasks._ai:
        await tasks._ai


def saved(store,item):
    return {key:json.loads(value) if key in ('filtered_content','summarized_content','processing_metadata') else value
        for key,value in dict(store.db.execute('SELECT * FROM publications WHERE id=%s',(item,)).fetchone()).items()}


def collection_worker(root,store,source):
    from tests.support.app import create_app
    app=create_app(root,token='test',api_only=True)
    worker=app.state.host.collection
    worker.execution._store=store
    worker.execution._admitted=lambda:True
    from oko.collection.sources import FeedRuntime,SourceSettings
    worker._sources['fixture']=FeedRuntime(source,SourceSettings('fixture','injected','https://fixture.invalid/feed',0,None),'injected')
    return worker
