"""Bounded, request-local inputs shared by material parts and their admission rules.

Only the synchronous versioned reader opens this scope. Nothing survives it;
write paths use the same accessors against current storage without a snapshot.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import sqlite3
from collections.abc import Iterator
from typing import Any

from oko.storage.reading import reading_value


@dataclass
class Inputs:
    db: sqlite3.Connection
    items: dict[str, sqlite3.Row] = field(default_factory=dict)
    jobs: dict[str, dict[str, sqlite3.Row]] = field(default_factory=dict)
    versions: dict[str, list[sqlite3.Row]] = field(default_factory=dict)
    feeds: dict[str, sqlite3.Row] = field(default_factory=dict)
    settings: dict[tuple[str, str], list[sqlite3.Row]] = field(default_factory=dict)
    state: dict[str, str] = field(default_factory=dict)
    state_keys: set[str] = field(default_factory=set)


_current: ContextVar[Inputs | None] = ContextVar('material_inputs', default=None)


def current(db: sqlite3.Connection) -> Inputs | None:
    value = _current.get()
    return value if value is not None and value.db is db else None


_ITEM = '''SELECT i.*,c.number AS current_number,v.fingerprint AS current_fingerprint,
    d.item AS source_deleted,r.version AS restored_version,t.path AS retained_path,t.digest AS retained_digest
    FROM items i LEFT JOIN content_current c ON c.item=i.id
    LEFT JOIN content_versions v ON v.item=i.id AND v.number=c.number
    LEFT JOIN source_deleted d ON d.item=i.id LEFT JOIN filter_restores r ON r.item=i.id
    LEFT JOIN retained_theses t ON t.item=i.id'''
_FEED = 'SELECT f.*,b.binding AS reader_binding FROM feeds f LEFT JOIN telegram_bindings b ON b.feed=f.id'


@reading_value
def item(db: sqlite3.Connection, identity: str) -> sqlite3.Row | None:
    value = current(db)
    return value.items.get(identity) if value and identity in value.items else db.execute('SELECT * FROM items WHERE id=?', (identity,)).fetchone()


def restored_version(db: sqlite3.Connection, identity: str) -> str | None:
    value=current(db)
    if value and identity in value.items:
        saved=value.items[identity]['restored_version']
        return str(saved) if saved is not None else None
    row=db.execute('SELECT version FROM filter_restores WHERE item=?',(identity,)).fetchone()
    return str(row[0]) if row else None


def retained(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    value=current(db)
    if value and identity in value.items:
        row=value.items[identity]
        return dict(path=row['retained_path'],digest=row['retained_digest']) if row['retained_path'] else None
    row=db.execute('SELECT path,digest FROM retained_theses WHERE item=?',(identity,)).fetchone()
    return dict(row) if row else None


@reading_value
def jobs(db: sqlite3.Connection, identity: str) -> dict[str, sqlite3.Row]:
    value = current(db)
    return value.jobs.get(identity, {}) if value and identity in value.items else {r['kind']: r for r in db.execute('SELECT * FROM jobs WHERE item=?', (identity,))}


@reading_value
def versions(db: sqlite3.Connection, identity: str) -> list[sqlite3.Row]:
    value = current(db)
    return value.versions.get(identity, []) if value and identity in value.items else list(db.execute('SELECT * FROM content_versions WHERE item=? ORDER BY number DESC', (identity,)))


@reading_value
def feed(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    value = current(db)
    row = value.feeds.get(identity) if value and identity in value.feeds else db.execute(_FEED+' WHERE f.id=?', (identity,)).fetchone()
    if row is None:
        raise ValueError('Feed not found')
    result = dict(row)
    for name in ('public_ai', 'read_history', 'auto_read'):
        result[name] = None if result[name] is None else bool(result[name])
    return result


def settings(db: sqlite3.Connection, scope: str, owner: str) -> list[sqlite3.Row]:
    value = current(db)
    return value.settings.get((scope, owner), []) if value and (scope,owner) in value.settings else list(db.execute('SELECT field,value FROM settings WHERE scope=? AND owner=?', (scope,owner)))


def state(db: sqlite3.Connection, key: str) -> str | None:
    value = current(db)
    if value is not None and key in value.state_keys:
        return value.state.get(key)
    row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
    return str(row[0]) if row else None


@contextmanager
def material_inputs(db: sqlite3.Connection, identities: set[str]) -> Iterator[None]:
    """Six indexed reads for up to one API batch; independent of foreign data."""
    value = Inputs(db)
    ids = sorted(identities)
    for start in range(0, len(ids), 400):
        batch = ids[start:start+400]
        marks = ','.join('?' for _ in batch)
        value.items.update({r['id']: r for r in db.execute(_ITEM+f' WHERE i.id IN ({marks})',batch)})
        for r in db.execute(f'SELECT * FROM jobs WHERE item IN ({marks})',batch):
            value.jobs.setdefault(r['item'], {})[r['kind']] = r
        for r in db.execute(f'SELECT * FROM content_versions WHERE item IN ({marks}) ORDER BY number DESC',batch):
            value.versions.setdefault(r['item'], []).append(r)
    feeds = sorted({r['feed_id'] for r in value.items.values()})
    for start in range(0, len(feeds), 400):
        batch = feeds[start:start+400]
        marks = ','.join('?' for _ in batch)
        value.feeds.update({r['id']: r for r in db.execute(_FEED+f' WHERE f.id IN ({marks})',batch)})
    owners = [('oko','global')] + [('feed',f) for f in feeds] + [('folder',f) for f in sorted({r['folder_id'] for r in value.feeds.values()})]
    value.settings={key:[] for key in owners}
    for start in range(0,len(owners),400):
        batch = owners[start:start+400]
        where = ' OR '.join('(scope=? AND owner=?)' for _ in batch)
        for r in db.execute('SELECT scope,owner,field,value FROM settings WHERE '+where,[v for pair in batch for v in pair]):
            value.settings.setdefault((r['scope'],r['owner']),[]).append(r)
    keys = ['processor:llamacpp','settings-version'] + ['settings-version:'+f for f in feeds]
    value.state_keys=set(keys)
    for start in range(0,len(keys),400):
        batch = keys[start:start+400]
        value.state.update({r['key']:r['value'] for r in db.execute('SELECT key,value FROM state WHERE key IN ('+','.join('?' for _ in batch)+')',batch)})
    token = _current.set(value)
    try:
        yield
    finally:
        _current.reset(token)
