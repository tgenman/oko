"""Prepare one worker's persisted candidates for storage projection fixtures."""
from oko.storage.job_repository import JobRepository
from oko.services.processing import ProcessingService
from oko.storage.transaction import transaction

def reconcile(store, kind):
    with transaction(store.db):
        store.processing.sync()
        JobRepository(store).reconcile(kind,ProcessingService(lambda:store).candidates(kind))


def save_derivative(store, job, signature, result, text, *, provenance=None):
    with transaction(store.db):
        if not store.jobs.valid(job):return
        store.publications.accept_result(job.item,job.kind,signature,result,provenance)
        store.jobs.finish(job)
        if job.kind=='filter':store.read_versions.sync_filter(job.item,bool(result['hidden']))
        store.publications.status(job.item)
