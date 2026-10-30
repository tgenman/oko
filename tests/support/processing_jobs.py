"""Prepare one worker's persisted candidates for storage projection fixtures."""
from oko.storage.transaction import transaction

def reconcile(store, kind):
    with transaction(store.db):
        store.processing.sync()


def save_derivative(store, job, signature, result, text, *, provenance=None):
    with transaction(store.db):
        if not store.jobs.valid(job):return
        store.publications.accept_result(job.item,job.kind,signature,result,provenance)
        store.jobs.finish(job)
        store.publications.status(job.item)
