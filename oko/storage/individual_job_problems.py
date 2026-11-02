"""Synchronize individual Job problems around an attempt transition."""
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from oko.storage.job_writes import execute as job_write

if TYPE_CHECKING:
    from oko.storage.job_types import Job
    from oko.storage.store import Store


def _previous(store: 'Store', kind: str, job_id: int) -> list[dict[str, Any]]:
    if kind == 'fetch':
        from oko.storage.fetch_job_problems import previous_candidates
    elif kind == 'download_media':
        from oko.storage.media_job_problems import previous_candidates
    elif kind in ('filter', 'theses'):
        from oko.storage.processing_job_problems import managed_candidates as previous_candidates
    else:
        return []
    return previous_candidates(store, job_id, store.wall_clock())


def _sync(store: 'Store', kind: str, job_id: int, previous: list[dict[str, Any]]) -> None:
    if kind == 'fetch':
        store.problems.sync_fetch(job_id, previous, store.wall_clock())
    elif kind == 'download_media':
        store.problems.sync_media_problem(job_id, previous, store.wall_clock())
    elif kind in ('filter', 'theses'):
        store.problems.sync_processing_problem(job_id, previous, store.wall_clock())


_SOURCE_KINDS = ('fetch', 'download_media', 'filter', 'theses', 'delete', 'feed-delete')


def _sync_source(store: 'Store', kind: str, job_id: int, old_code: str | None, before: set[str]) -> None:
    from oko.storage import source_groups
    complete = source_groups.sync(store, kind, before | source_groups.job_keys(store, job_id),
        store.wall_clock(), evidence=source_groups.job_evidence_many(store, [(job_id, old_code)]))
    job_write(store.db, "UPDATE jobs SET source_event_owned=(%s=1) AND (problem_code LIKE 'source-%%') IS TRUE WHERE id=%s", (complete, job_id))


@dataclass
class FailureResult:
    changed: Any = None


@contextmanager
def failure(store: 'Store', job: 'Job', issue: Any, *, cancelled: bool) -> Iterator[FailureResult]:
    # Processor preparation takes locks between the media and processing reads.
    previous = _previous(store, job.kind, job.id) if job.kind in ('fetch', 'download_media') else []
    source_lock = not cancelled and issue.code.startswith('source-') and job.kind in _SOURCE_KINDS
    processor_previous = store.problems.prepare_processor_change([job.id], store.wall_clock(), source_lock=source_lock)
    if job.kind in ('filter', 'theses'):
        previous = _previous(store, job.kind, job.id)
    result = FailureResult()
    yield result
    old_code = result.changed['previous_code'] if result.changed else None
    if result.changed and job.kind in _SOURCE_KINDS and ((old_code or '').startswith('source-') or source_lock):
        from oko.storage import source_groups
        before = source_groups.job_keys(store, job.id, old_code) if (old_code or '').startswith('source-') else set()
        _sync_source(store, job.kind, job.id, old_code, before)
    store.problems.finish_processor_change([job.id], processor_previous, store.wall_clock())
    store.incomplete_draft.mark_job(job.kind, job.item, job.id)
    if job.kind == 'discover':
        store.problems.sync_discover(job.id, store.wall_clock())
    _sync(store, job.kind, job.id, previous)


@contextmanager
def claim(store: 'Store', row: Any) -> Iterator[None]:
    kind, job_id = row['kind'], row['id']
    source_before: set[str] = set()
    if kind in _SOURCE_KINDS and (row['problem_code'] or '').startswith('source-'):
        from oko.storage import source_groups
        source_before = source_groups.job_keys(store, job_id)
    previous = _previous(store, kind, job_id) if row['error'] is not None or row['problem_code'] is not None else []
    yield
    if source_before:
        _sync_source(store, kind, job_id, row['problem_code'], source_before)
    if kind == 'filter':
        store.processing.filter_changed(row['item'])
    if kind == 'read':
        store.problems.sync_read_feed(row['item'], store.wall_clock())
    if kind == 'fetch' and previous:
        _sync(store, kind, job_id, previous)
    store.incomplete_draft.mark_job(kind, row['item'], job_id)
    if kind != 'fetch' and previous:
        _sync(store, kind, job_id, previous)
