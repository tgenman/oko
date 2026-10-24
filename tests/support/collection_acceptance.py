"""Synchronous acceptance boundary for synthetic storage fixtures."""
from typing import Any
from oko.storage.store import Store
from oko.storage.job_types import Job
from oko.collection.contracts import Publication,Material
from oko.storage.transaction import transaction
from oko.storage.job_repository import JobRepository
from oko.collection.adapters import AdapterIssue, AdapterErrorKind

def discovered(store: Store, publications: list[Publication], cursor: str, job: Job, feed: str='demo', namespace: str='', **options: Any) -> None:
    with transaction(store.db):
        jobs=JobRepository(store)
        if not jobs.valid(job): return
        effects=store.collect_discovery(publications,cursor,feed,namespace,**options)
        jobs.apply_collection(effects)
        jobs.finish(job)

def save(store: Store, job: Job, material: Material) -> None:
    with transaction(store.db):
        jobs=JobRepository(store)
        if not jobs.valid(job): return
        error,effects=store.collect_material(job.item,material)
        jobs.apply_collection(effects)
        if error:
            store.jobs.retry(job,store.clock(),error.label,issue=error.adapter_issue or AdapterIssue(error.code or 'fetch-failed',error.label,AdapterErrorKind.temporary if error.retryable else AdapterErrorKind.permanent))
        else: jobs.finish(job)
