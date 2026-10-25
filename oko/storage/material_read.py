"""Bounded, request-local inputs shared by material parts and their admission rules.

Only the synchronous versioned reader opens this scope. Nothing survives it;
write paths use the same accessors against current storage without a snapshot.
"""
from oko.storage.database import Database, Row
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any

from oko.storage.reading import reading_value


@dataclass
class Inputs:
    db: Database
    items: dict[str, Row] = field(default_factory=dict)
    jobs: dict[str, dict[str, Row]] = field(default_factory=dict)
    media_jobs: dict[str,list[Row]] = field(default_factory=dict)
    feeds: dict[str, Row] = field(default_factory=dict)
    state: dict[str, str] = field(default_factory=dict)
    state_keys: set[str] = field(default_factory=set)


_current: ContextVar[Inputs | None] = ContextVar('material_inputs', default=None)


def current(db: Database) -> Inputs | None:
    value = _current.get()
    return value if value is not None and value.db is db else None


_ITEM = '''SELECT i.*,d.item AS source_deleted FROM items i LEFT JOIN source_deleted d ON d.item=i.id'''
_FEED = 'SELECT f.*,b.binding AS reader_binding FROM feeds f LEFT JOIN telegram_bindings b ON b.feed=f.id'


@reading_value
def item(db: Database, identity: str) -> Row | None:
    value = current(db)
    return value.items.get(identity) if value and identity in value.items else db.execute('SELECT * FROM items WHERE id=?', (identity,)).fetchone()






@reading_value
def jobs(db: Database, identity: str) -> dict[str, Row]:
    value = current(db)
    return value.jobs.get(identity, {}) if value and identity in value.items else {r['kind']: r for r in db.execute('SELECT * FROM jobs WHERE item=?', (identity,))}




@reading_value
def feed(db: Database, identity: str) -> dict[str, Any]:
    value = current(db)
    row = value.feeds.get(identity) if value and identity in value.feeds else db.execute(_FEED+' WHERE f.id=?', (identity,)).fetchone()
    if row is None:
        raise ValueError('Feed not found')
    result = dict(row)
    from oko.storage.publications import FEED_COLUMNS
    for field in FEED_COLUMNS:
        result.pop(field, None)
    for name in ('public_ai', 'read_history', 'auto_read'):
        result[name] = None if result[name] is None else bool(result[name])
    return result


def state(db: Database, key: str) -> str | None:
    value = current(db)
    if value is not None and key in value.state_keys:
        return value.state.get(key)
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return str(row[0]) if row else None


@contextmanager
def material_inputs(db: Database, identities: set[str]) -> Iterator[None]:
    """Six indexed reads for up to one API batch; independent of foreign data."""
    value = Inputs(db)
    ids = sorted(identities)
    for start in range(0, len(ids), 400):
        batch = ids[start:start+400]
        marks = ','.join('?' for _ in batch)
        value.items.update({r['id']: r for r in db.execute(_ITEM+f' WHERE i.id IN ({marks})',batch)})
        for r in db.execute(f'''SELECT j.*,coalesce(m.publication_id,j.item) AS reader_item FROM jobs j
                LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
                WHERE j.item IN ({marks}) OR m.publication_id IN ({marks})''',batch+batch):
            if r['kind']=='download_media':
                value.media_jobs.setdefault(r['reader_item'],[]).append(r)
            else:
                value.jobs.setdefault(r['reader_item'],{})[r['kind']]=r
    feeds = sorted({r['feed_id'] for r in value.items.values()})
    for start in range(0, len(feeds), 400):
        batch = feeds[start:start+400]
        marks = ','.join('?' for _ in batch)
        value.feeds.update({r['id']: r for r in db.execute(_FEED+f' WHERE f.id IN ({marks})',batch)})
    keys = ['settings-version'] + ['settings-version:'+f for f in feeds]
    value.state_keys=set(keys)
    for start in range(0,len(keys),400):
        batch = keys[start:start+400]
        value.state.update({r['key']:r['value'] for r in db.execute('SELECT key,value FROM state WHERE key IN ('+','.join('?' for _ in batch)+')',batch)})
    token = _current.set(value)
    try:
        yield
    finally:
        _current.reset(token)
