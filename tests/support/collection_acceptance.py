"""Synchronous acceptance boundary for synthetic storage fixtures."""
from typing import Any
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.collection.contracts import Publication,Material
from oko.storage.transaction import transaction
from oko.storage.job_repository import JobRepository
from oko.collection.adapters import AdapterIssue, AdapterErrorKind

def discovered(store: Store, publications: list[Publication], cursor: str, job: Job, feed: str='fixture', namespace: str='', **options: Any) -> None:
    with transaction(store.db):
        jobs=JobRepository(store)
        if not jobs.valid(job): return
        jobs.begin_collection(job)
        effects=store.discovery.accept(publications,cursor,feed,namespace,**options)
        jobs.apply_collection(effects,job=job)
        jobs.finish(job)

def save(store: Store, job: Job, material: Material) -> None:
    with transaction(store.db):
        jobs=JobRepository(store)
        if not jobs.valid(job): return
        jobs.begin_collection(job)
        error,effects=store.collected_publication.accept(job.item,material)
        jobs.apply_collection(effects,job=job)
        if error:
            store.jobs.retry(job,store.clock(),error.label,issue=error.adapter_issue or AdapterIssue(error.code or 'fetch-failed',error.label,AdapterErrorKind.temporary if error.retryable else AdapterErrorKind.permanent))
        else: jobs.finish(job)


def running_collection_job(store: Store, kind: str, target: str, *, bind: bool = True) -> Job:
    """Prepare a synthetic running collection attempt in the caller's transaction."""
    row=store.db.execute("""INSERT INTO jobs(kind,item,status,generation) VALUES (%s,%s,'running',1)
        ON CONFLICT(kind,item) DO UPDATE SET status='running',generation=jobs.generation+1
        RETURNING id,generation""", (kind,target)).fetchone()
    assert row is not None
    job=Job(row['id'],kind,target,row['generation'])
    if bind:
        store.jobs.begin_collection(job)
    return job


def apply_simulated_collection(store: Store, effects: Any, *, job: Job) -> None:
    store.jobs.apply_collection(effects,job=job)
    store.jobs.finish(job)
