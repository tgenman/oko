"""Feed details stored with their owner; callers own each write transaction."""
from oko.storage.database import expect_row, Database
from collections.abc import Callable
from typing import Any, Literal


class FeedDetails:
    def __init__(self, db: Database, clock: Callable[[], float]) -> None:
        self.db, self.clock = db, clock

    def created(self, feed: str) -> None:
        now = self.clock()
        self.db.execute('UPDATE feeds SET created_at=%s, modified_at=%s WHERE id=%s AND created_at IS NULL', (now, now, feed))

    def record(self, feed: str, field: Literal['modified', 'updated', 'marked_read']) -> None:
        column = {'modified': 'modified_at', 'updated': 'last_discovery_at', 'marked_read': 'last_viewed_at'}[field]
        self.db.execute(f'UPDATE feeds SET {column}=%s WHERE id=%s', (self.clock(), feed))

    def description(self, feed: str, value: str | None) -> None:
        if value is not None:
            self.db.execute('UPDATE feeds SET description=%s WHERE id=%s', (value, feed))

    def snapshot(self, feed: str) -> dict[str, Any]:
        row = self.db.execute('SELECT created_at AS created, modified_at AS modified, last_discovery_at AS updated, last_viewed_at AS marked_read, description FROM feeds WHERE id=%s', (feed,)).fetchone()
        result = {field: row[field] if row else None for field in ('created', 'modified', 'updated', 'marked_read', 'description')}
        result['unread'] = expect_row(self.db.execute('SELECT COUNT ( * ) FROM feed_publications m JOIN publications p ON p.id=m.publication_id WHERE m.feed_id = %s AND m.is_primary AND m.is_unviewed AND p.viewed = 0', (feed,)).fetchone())[0]
        return result
