"""Claim a test job at the same transaction boundary as a worker."""
from oko.storage.transaction import transaction
from oko.storage.job_types import Job
from oko.storage.store import Store
from typing import Any


def claim_job(store: Store, *args: Any, **kwargs: Any) -> Job | None:
    if store.db.transaction_depth:
        return store.jobs.next_job(*args, **kwargs)
    with transaction(store.db):
        return store.jobs.next_job(*args, **kwargs)


def retry_job(store: Store, *args: Any, **kwargs: Any) -> None:
    if store.db.transaction_depth:
        store.jobs.retry(*args, **kwargs)
    else:
        with transaction(store.db):
            store.jobs.retry(*args, **kwargs)
