"""Durable retry policy shared by publication fetch and individual file jobs.

Mutations participate in the caller's transaction; no external work happens here.
"""
from oko.storage.job_writes import execute as job_write
from oko.storage.publication_owners import owner_expr
from oko.storage.database import expect_row
from typing import TYPE_CHECKING

from oko.collection.adapters import AdapterIssue, AdapterErrorKind

if TYPE_CHECKING:
    from oko.storage.store import Store
from oko.storage.job_types import Job

_TARGET = "kind IN ('discover','fetch','filter','theses','download_media')"


def interrupt(store: 'Store', feeds: set[str] | None = None, *, backoff: bool = True) -> None:
    from oko.storage import memberships,source_groups
    memberships.lock_graph(store.db)
    source_changed: list[int]=[]
    initial = store.subscriptions.retry_settings()['initial_seconds']
    for row in store.db.execute(f"SELECT * FROM jobs WHERE status = 'running' AND {_TARGET}").fetchall():
        owner = store.media.owner(row['item']) if row['kind']=='download_media' else row['item']
        feed = store.db.execute(f"SELECT {owner_expr('i')} AS feed_id FROM publications i WHERE i.id = %s", (owner,)).fetchone()
        if feeds is not None and (row['item'] not in feeds if row['kind']=='discover' else feed is None or feed[0] not in feeds):
            continue
        until = (max(row['retry_at'] or 0, store.wall_clock()+max(initial,row['retry_delay'])) if backoff
            else row['retry_at'] if row['wait_scope'] else None)
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        previous_fetch=managed_candidates(store,row['id'],store.wall_clock()) if row['kind']=='fetch' and (row['error'] is not None or row['problem_code'] is not None) else []
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(store,row['id'],store.wall_clock()) if row['kind']=='download_media' and (row['error'] is not None or row['problem_code'] is not None) else []
        from oko.storage.processing_job_problems import managed_candidates as processing_candidates
        previous_processing=processing_candidates(store,row['id'],store.wall_clock()) if row['kind'] in ('filter','theses') and (row['error'] is not None or row['problem_code'] is not None) else []
        job_write(store.db, "UPDATE jobs SET queued_wall=CASE WHEN kind IN ('discover','fetch','download_media') AND status!='pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END, status='pending', generation=generation+1, retry_at=%s, next=%s WHERE id=%s",
            (until,store.clock()+max(0,until-store.wall_clock()) if until else 0,row['id']))
        if row['kind'] in ('fetch','download_media','filter','theses') and (row['problem_code'] or '').startswith('source-'):source_changed.append(row['id'])
        manager=getattr(store,'incomplete_draft',None)
        if manager is not None:manager.mark_job(row['kind'],row['item'],row['id'])
        if row['kind']=='discover' and (row['error'] is not None or row['problem_code'] is not None):
            store.problems.sync_discover(row['id'],store.wall_clock())
        if previous_fetch:
            store.problems.sync_fetch(row['id'],previous_fetch,store.wall_clock())
        if previous_media:
            store.problems.sync_media_problem(row['id'],previous_media,store.wall_clock())
        if previous_processing:
            store.problems.sync_processing_problem(row['id'],previous_processing,store.wall_clock())
    if source_changed:source_groups.sync_jobs(store,source_changed,store.wall_clock())


def recover(store: 'Store') -> None:
    with store.db:
        interrupt(store)
        for row in store.db.execute(f"SELECT id , retry_at FROM jobs WHERE status = 'pending' AND {_TARGET}").fetchall():
            deadline = store.clock()+max(0,row['retry_at']-store.wall_clock()) if row['retry_at'] else 0
            job_write(store.db, 'UPDATE jobs SET next = %s WHERE id = %s AND next IS DISTINCT FROM %s',
                (deadline,row['id'],deadline))


def failure_status(issue: AdapterIssue) -> str:
    return ('pending' if issue.kind in (AdapterErrorKind.temporary,AdapterErrorKind.source_wait)
        else 'unavailable' if issue.kind==AdapterErrorKind.unavailable else 'failed')


def fail(store: 'Store', job: 'Job', issue: AdapterIssue, *, cancelled: bool = False) -> None:
    if job.kind not in ('read','discover','feed-delete'):
        from oko.storage import memberships
        memberships.lock_graph(store.db)
    if not store.jobs.valid(job):
        return
    from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
    previous=managed_candidates(store,job.id,store.wall_clock()) if job.kind=='fetch' else []
    from oko.storage.media_job_problems import previous_candidates as media_candidates
    previous_media=media_candidates(store,job.id,store.wall_clock()) if job.kind=='download_media' else []
    processor_previous=store.problems.prepare_processor_change([job.id],store.wall_clock(),source_lock=not cancelled and issue.code.startswith('source-') and job.kind in ('fetch','download_media','filter','theses','delete','feed-delete'))
    from oko.storage.processing_job_problems import managed_candidates as processing_candidates
    previous_processing=processing_candidates(store,job.id,store.wall_clock()) if job.kind in ('filter','theses') else []
    row = expect_row(store.db.execute('SELECT retry_delay FROM jobs WHERE id = %s',(job.id,)).fetchone())
    settings = store.subscriptions.retry_settings()
    delay = min(settings['max_seconds'],max(settings['initial_seconds'],row['retry_delay']*2))
    status = 'cancelled' if cancelled else failure_status(issue)
    until = max(store.wall_clock()+delay,issue.retry_at.timestamp() if issue.retry_at else 0) if status=='pending' else None
    changed=job_write(store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' AND %s = 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = %s , next = %s , retry_at = %s , retry_delay = %s , wait_scope = %s , error = %s , problem_code = %s WHERE id = %s AND generation = %s AND CASE WHEN kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND (problem_code LIKE 'source-%%' OR %s LIKE 'source-%%') THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.problem_code AS previous_code",(status,status,store.clock()+max(0,until-store.wall_clock()) if until else 0,
        until,delay,issue.scope if issue.kind==AdapterErrorKind.source_wait else None,None if cancelled else issue.message,None if cancelled else issue.code,job.id,job.generation,None if cancelled else issue.code)).fetchone()
    if changed and job.kind in ('fetch','download_media','filter','theses','delete','feed-delete') and ((changed['previous_code'] or '').startswith('source-') or (not cancelled and issue.code.startswith('source-'))):
        from oko.storage import source_groups
        previous_source=source_groups.job_keys(store,job.id,changed['previous_code']) if (changed['previous_code'] or '').startswith('source-') else set()
        complete=source_groups.sync(store,job.kind,previous_source | source_groups.job_keys(store,job.id),store.wall_clock(),evidence=source_groups.job_evidence_many(store,[(job.id,changed['previous_code'])]))
        job_write(store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND (problem_code LIKE 'source-%%') IS TRUE WHERE id=%s",(complete,job.id))
    store.problems.finish_processor_change([job.id],processor_previous,store.wall_clock())
    store.incomplete_draft.mark_job(job.kind,job.item,job.id)
    if job.kind=='discover':
        store.problems.sync_discover(job.id,store.wall_clock())
    if job.kind=='fetch':
        store.problems.sync_fetch(job.id,previous,store.wall_clock())
    if job.kind=='download_media':
        store.problems.sync_media_problem(job.id,previous_media,store.wall_clock())
    if job.kind in ('filter','theses'):
        store.problems.sync_processing_problem(job.id,previous_processing,store.wall_clock())
