"""Durable retry policy shared by publication fetch and individual file jobs.

Mutations participate in the caller's transaction; no external work happens here.
"""
from typing import TYPE_CHECKING

from oko.collection.adapters import AdapterIssue, AdapterErrorKind

if TYPE_CHECKING:
    from oko.storage.store import Store, Job

_TARGET = "(kind='download_media' OR (kind IN ('fetch','filter','theses') AND item IN (SELECT id FROM items WHERE platform IS NOT NULL)))"


def interrupt(store: 'Store', feeds: set[str] | None = None) -> None:
    initial = store.subscriptions.retry_settings()['initial_seconds']
    for row in store.db.execute("SELECT * FROM jobs WHERE status='running' AND "+_TARGET).fetchall():
        owner = store.media.owner(row['item']) if row['kind']=='download_media' else row['item']
        feed = store.db.execute('SELECT feed_id FROM items WHERE id=?', (owner,)).fetchone()
        if feeds is not None and (feed is None or feed[0] not in feeds):
            continue
        until = max(row['retry_at'] or 0, store.wall_clock()+max(initial,row['retry_delay']))
        store.db.execute("UPDATE jobs SET status='pending',generation=generation+1,retry_at=?,next=? WHERE id=?",
            (until,store.clock()+max(0,until-store.wall_clock()),row['id']))


def recover(store: 'Store') -> None:
    with store.db:
        interrupt(store)
        for row in store.db.execute("SELECT id,retry_at FROM jobs WHERE status='pending' AND "+_TARGET).fetchall():
            store.db.execute('UPDATE jobs SET next=? WHERE id=?',
                (store.clock()+max(0,row['retry_at']-store.wall_clock()) if row['retry_at'] else 0,row['id']))


def failure_status(issue: AdapterIssue) -> str:
    return ('pending' if issue.kind in (AdapterErrorKind.temporary,AdapterErrorKind.source_wait)
        else 'unavailable' if issue.kind==AdapterErrorKind.unavailable else 'failed')


def fail(store: 'Store', job: 'Job', issue: AdapterIssue, *, cancelled: bool = False) -> None:
    if not store.valid(job):
        return
    row = store.db.execute('SELECT retry_delay FROM jobs WHERE id=?',(job.id,)).fetchone()
    settings = store.subscriptions.retry_settings()
    delay = min(settings['max_seconds'],max(settings['initial_seconds'],row['retry_delay']*2))
    status = 'cancelled' if cancelled else failure_status(issue)
    until = max(store.wall_clock()+delay,issue.retry_at.timestamp() if issue.retry_at else 0) if status=='pending' else None
    store.db.execute("""UPDATE jobs SET status=?,next=?,retry_at=?,retry_delay=?,wait_scope=?,error=?,problem_code=?
        WHERE id=? AND generation=?""",(status,store.clock()+max(0,until-store.wall_clock()) if until else 0,
        until,delay,issue.scope if issue.kind==AdapterErrorKind.source_wait else None,None if cancelled else issue.message,None if cancelled else issue.code,job.id,job.generation))
