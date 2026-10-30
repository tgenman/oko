"""Durable retry policy shared by publication fetch and individual file jobs.

Mutations participate in the caller's transaction; no external work happens here.
"""
from oko.storage.job_writes import execute as job_write
from oko.storage.database import expect_row
from typing import TYPE_CHECKING

from oko.collection.adapters import AdapterIssue, AdapterErrorKind

if TYPE_CHECKING:
    from oko.storage.store import Store
from oko.storage.job_types import Job

_TARGET = "kind IN ('discover','fetch','filter','theses','download_media')"


def interrupt(store: 'Store', feeds: set[str] | None = None, *, backoff: bool = True) -> None:
    initial = store.subscriptions.retry_settings()['initial_seconds']
    for row in store.db.execute(f"SELECT * FROM jobs WHERE status = 'running' AND {_TARGET}").fetchall():
        owner = store.media.owner(row['item']) if row['kind']=='download_media' else row['item']
        feed = store.db.execute('SELECT feed_id FROM items_read WHERE id = %s', (owner,)).fetchone()
        if feeds is not None and (row['item'] not in feeds if row['kind']=='discover' else feed is None or feed[0] not in feeds):
            continue
        until = (max(row['retry_at'] or 0, store.wall_clock()+max(initial,row['retry_delay'])) if backoff
            else row['retry_at'] if row['wait_scope'] else None)
        job_write(store.db, "UPDATE jobs SET queued_wall=CASE WHEN kind IN ('discover','fetch','download_media') AND status!='pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END, status='pending', generation=generation+1, retry_at=%s, next=%s WHERE id=%s",
            (until,store.clock()+max(0,until-store.wall_clock()) if until else 0,row['id']))


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
    if not store.jobs.valid(job):
        return
    row = expect_row(store.db.execute('SELECT retry_delay FROM jobs WHERE id = %s',(job.id,)).fetchone())
    settings = store.subscriptions.retry_settings()
    delay = min(settings['max_seconds'],max(settings['initial_seconds'],row['retry_delay']*2))
    status = 'cancelled' if cancelled else failure_status(issue)
    until = max(store.wall_clock()+delay,issue.retry_at.timestamp() if issue.retry_at else 0) if status=='pending' else None
    job_write(store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' AND %s = 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = %s , next = %s , retry_at = %s , retry_delay = %s , wait_scope = %s , error = %s , problem_code = %s WHERE id = %s AND generation = %s",(status,status,store.clock()+max(0,until-store.wall_clock()) if until else 0,
        until,delay,issue.scope if issue.kind==AdapterErrorKind.source_wait else None,None if cancelled else issue.message,None if cancelled else issue.code,job.id,job.generation))
