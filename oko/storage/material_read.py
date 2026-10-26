"""Bounded, request-local inputs shared by material parts and their admission rules.

Synchronous reading calculations open this scope. Nothing survives it;
write paths use the same accessors against current storage without a snapshot.
"""
from oko.storage.database import Database, Row
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any

from oko.storage.reading import reading_value, saved_content


@dataclass
class Inputs:
    db: Database
    items: dict[str, Row] = field(default_factory=dict)
    jobs: dict[str, dict[str, Row]] = field(default_factory=dict)
    media_jobs: dict[str,list[Row]] = field(default_factory=dict)
    feeds: dict[str, Row] = field(default_factory=dict)
    state: dict[str, str] = field(default_factory=dict)
    state_keys: set[str] = field(default_factory=set)
    subtitles: dict[str, str | None] = field(default_factory=dict)
    related_items: dict[str, Row] = field(default_factory=dict)
    children: dict[str, list[Row]] = field(default_factory=dict)
    owners: dict[str, set[str]] = field(default_factory=dict)


_current: ContextVar[Inputs | None] = ContextVar('material_inputs', default=None)


def current(db: Database) -> Inputs | None:
    value = _current.get()
    return value if value is not None and value.db is db else None


_ITEM = 'SELECT i . * , d . item AS source_deleted FROM items i LEFT JOIN source_deleted d ON d . item = i . id'
_FEED = 'SELECT f . * , b . binding AS reader_binding FROM feeds f LEFT JOIN telegram_bindings b ON b . feed = f . id'


@reading_value
def item(db: Database, identity: str) -> Row | None:
    value = current(db)
    if value is not None:
        if identity in value.items:
            return value.items[identity]
        if identity in value.related_items:
            return value.related_items[identity]
    return db.execute('SELECT * FROM items WHERE id = %s', (identity,)).fetchone()






@reading_value
def jobs(db: Database, identity: str) -> dict[str, Row]:
    value = current(db)
    return value.jobs.get(identity, {}) if value and identity in value.items else {r['kind']: r for r in db.execute('SELECT * FROM jobs WHERE item = %s', (identity,))}




@reading_value
def feed(db: Database, identity: str) -> dict[str, Any]:
    value = current(db)
    row = value.feeds.get(identity) if value and identity in value.feeds else db.execute(f'{_FEED} WHERE f . id = %s', (identity,)).fetchone()
    if row is None:
        raise ValueError('Feed not found')
    result = dict(row)
    result.pop('rowid', None)
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
    row = db.execute('SELECT value FROM state WHERE key = %s', (key,)).fetchone()
    return str(row[0]) if row else None


@contextmanager
def material_inputs(db: Database, identities: set[str]) -> Iterator[None]:
    """Bounded inputs for requested materials; independent of foreign data."""
    value = Inputs(db)
    ids = sorted(identities)
    for start in range(0, len(ids), 400):
        batch = ids[start:start+400]
        marks = ','.join('%s' for _ in batch)
        value.items.update({r['id']: r for r in db.execute(_ITEM+f' WHERE i.id IN ({marks})',batch)})
        for r in db.execute(f"SELECT j . * , coalesce ( m . publication_id , j . item ) AS reader_item FROM jobs j LEFT JOIN media_files m ON j . kind = 'download_media' AND m . id = j . item WHERE j . item IN ( {marks} ) UNION SELECT j . * , m . publication_id AS reader_item FROM media_files m JOIN jobs j ON j . kind = 'download_media' AND m . id = j . item WHERE m . publication_id IN ( {marks} )",batch+batch):
            if r['kind']=='download_media':
                value.media_jobs.setdefault(r['reader_item'],[]).append(r)
            else:
                value.jobs.setdefault(r['reader_item'],{})[r['kind']]=r
        value.children.update({identity: [] for identity in batch})
        for row in db.execute(f"SELECT l . source_publication_id AS reader_source , i . * , j . error AS fetch_error FROM publication_links l JOIN items i ON i . id = l . target_publication_id LEFT JOIN jobs j ON j . kind = 'fetch' AND j . item = i . id WHERE l . source_publication_id IN ( {marks} ) AND i . viewed = 0 ORDER BY l . rowid", batch):
            value.children[row['reader_source']].append(row)
            value.related_items[row['id']] = row
        value.owners.update({identity: set() for identity in batch})
        for row in db.execute(f'SELECT m . publication_id AS reader_source , m . feed_id FROM reading_memberships m WHERE m . publication_id IN ( {marks} ) UNION SELECT l . source_publication_id AS reader_source , m . feed_id FROM publication_links l JOIN reading_memberships m ON m . publication_id = l . target_publication_id WHERE l . source_publication_id IN ( {marks} )', batch+batch):
            value.owners[row['reader_source']].add(row['feed_id'])
    feeds = sorted({r['feed_id'] for r in value.items.values() if r['feed_id'] is not None})
    for start in range(0, len(feeds), 400):
        batch = feeds[start:start+400]
        marks = ','.join('%s' for _ in batch)
        value.feeds.update({r['id']: r for r in db.execute(_FEED+f' WHERE f.id IN ({marks})',batch)})
    keys = ['settings-version'] + ['settings-version:'+f for f in feeds]
    value.state_keys=set(keys)
    for start in range(0,len(keys),400):
        batch = keys[start:start+400]
        value.state.update({r['key']:r['value'] for r in db.execute(f"SELECT key , value FROM state WHERE key IN ( {','.join(('%s' for _ in batch))} )",batch)})
    from oko.storage.youtube_media import subtitle_id
    for row in value.items.values():
        try:
            content = saved_content(row['content'])
            identity = subtitle_id(content) if 'youtube' in content.get('extra', {}) else None
            if identity is not None:
                value.subtitles[identity] = None
        except (ValueError, KeyError, TypeError, AttributeError):
            # A corrupt resource is reported by its own projection; it must not
            # prevent other resources in the same batch from being read.
            continue
    subtitle_ids = sorted(value.subtitles)
    for start in range(0, len(subtitle_ids), 400):
        batch = subtitle_ids[start:start+400]
        marks = ','.join('%s' for _ in batch)
        value.subtitles.update({row['id']: row['storage_key'] for row in db.execute(
            f"SELECT id , storage_key FROM media_files WHERE status = 'ready' AND id IN ( {marks} )", batch)})
    token = _current.set(value)
    try:
        yield
    finally:
        _current.reset(token)
