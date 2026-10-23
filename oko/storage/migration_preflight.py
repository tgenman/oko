"""Report legacy boundaries that cannot be guessed during offline conversion."""
import json
import sqlite3
from typing import Any,TYPE_CHECKING

if TYPE_CHECKING:
    from oko.storage.store import Store


def jobs(path: str) -> list[dict[str,Any]]:
    db=sqlite3.connect(path)
    db.row_factory=sqlite3.Row
    try:
        return [dict(row) for row in db.execute("SELECT kind,item,status FROM jobs WHERE status='running'")]
    finally:
        db.close()


def results(path: str) -> dict[str,set[str]]:
    db=sqlite3.connect(path)
    try:
        columns={r[1] for r in db.execute('PRAGMA table_info(items)')}
        if 'processing_metadata' not in columns:
            return {}
        return {r[0]:set(json.loads(r[1])) & {'filter','summary'} for r in db.execute("SELECT id,processing_metadata FROM items WHERE platform IS NOT NULL AND viewed=0")}
    finally:
        db.close()


def inspect(store: 'Store', report: dict[str,Any]) -> None:
    report['history_boundaries']=[]
    known={'discover','fetch','download_media','filter','theses','delete','feed-delete','read'}
    for row in store.db.execute("SELECT kind,item,status FROM jobs WHERE status NOT IN ('done','cancelled')"):
        if row['kind'] not in known:
            report['conflicts'].append(dict(job_kind=row['kind'],item=row['item'],reason='Unknown unfinished job; resolve with the previous release before migration'))
    for feed in store.subscriptions.feeds():
        binding=store.subscriptions.telegram.binding(feed['id'])
        config=json.loads(store.db.execute('SELECT config FROM feeds WHERE id=?',(feed['id'],)).fetchone()[0])
        if binding and binding['kind']=='legacy_client' and config.get('source_kind')!='group':
            report['conflicts'].append(dict(feed_id=feed['id'],reason='Confirm the saved Telegram profile before offline migration'))
        if binding and binding['kind']=='client' and config.get('source_kind') not in ('channel','group'):
            report['conflicts'].append(dict(feed_id=feed['id'],reason='Missing confirmed source_kind; refresh Telegram metadata before offline migration'))
        history=feed['initial_history']
        if history and history['mode']=='date':
            report['history_boundaries'].append(dict(feed_id=feed['id'],selection={key:history[key] for key in ('mode','date','timezone')},state=history['state']))
            if history['state'] not in ('done','cancelled'):
                report['unportable'].append(dict(feed_id=feed['id'],reason='Unfinished legacy date selection stopped at saved cursor; no count substituted'))
                with store.db:
                    store.subscriptions.history.cancel(feed['id'])
