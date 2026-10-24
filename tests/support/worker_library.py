"""Real temporary storage for worker transaction and recovery scenarios."""
import json
from oko.storage.store import Store
from oko.collection.contracts import Publication,Material
from oko.subscriptions.subscriptions import Overrides
from oko.processing.llamacpp import ProcessorConfig
from oko.execution import Execution
from oko.workers.control import JobControl
from oko.workers.processing import FilterWorker,SummaryWorker
from tests.support.collection_acceptance import discovered
from tests.support.source import publication_packet
from tests.support.codex import CodexService
from tests.support.filters import transport


def worker_library(root, *, text='Useful text', fault=lambda _:None):
    store=Store(root,fault)
    store.subscriptions.ensure_demo()
    store.configure_processor(ProcessorConfig(endpoint='http://localhost:9931',model='local:latest',local_execution=True))
    store.subscriptions.update_settings('oko','global',Overrides(processor='llamacpp',theses=True,prompt_filters='Keep useful text'))
    store.settings_changed()
    store.jobs.enqueue_discovery()
    job=store.jobs.next_job(store.clock())
    assert job.kind=='discover'
    discovered(store,[publication_packet(Publication('one','https://example.org/one',100),Material('Title',text),needs_fetch=False)],'{}',job)
    item=store.db.execute('SELECT id FROM items').fetchone()[0]
    execution=Execution(lambda:True)
    execution._store=store
    tasks=JobControl(execution,{},None,store.clock)
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
        for key,value in dict(store.db.execute('SELECT * FROM items WHERE id=?',(item,)).fetchone()).items()}


def collection_worker(root,store,source):
    from tests.support.app import create_app
    app=create_app(root,token='test',api_only=True)
    worker=app.state.host.collection
    worker.execution._store=store
    worker.execution._admitted=lambda:True
    worker._sources['demo']=source
    return worker
