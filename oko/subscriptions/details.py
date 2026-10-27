"""Persistent feed information; callers own the transaction of each real change."""
from oko.storage.database import expect_row, Database
from collections.abc import Callable
from typing import Any, Literal


class FeedDetails:
    def __init__(self, db: Database, clock: Callable[[], float]) -> None:
        self.db, self.clock = db, clock
        pass

    def created(self, feed: str) -> None:
        now = self.clock()
        self.db.execute('INSERT INTO feed_details ( feed , created , modified ) VALUES ( %s , %s , %s ) ON CONFLICT DO NOTHING', (feed, now, now))

    def record(self, feed: str, field: Literal['modified', 'updated', 'marked_read']) -> None:
        self.db.execute(f'INSERT INTO feed_details ( feed , {field} ) VALUES ( %s , %s ) ON CONFLICT ( feed ) DO UPDATE SET {field} = excluded . {field}', (feed, self.clock()))

    def description(self, feed: str, value: str | None) -> None:
        if value is not None:
            self.db.execute('INSERT INTO feed_details ( feed , description ) VALUES ( %s , %s ) ON CONFLICT ( feed ) DO UPDATE SET description = excluded . description', (feed, value))

    def snapshot(self, feed: str) -> dict[str, Any]:
        row = self.db.execute('SELECT * FROM feed_details WHERE feed = %s', (feed,)).fetchone()
        result = {field: row[field] if row else None for field in ('created', 'modified', 'updated', 'marked_read', 'description')}
        result['unread'] = expect_row(self.db.execute('SELECT COUNT ( * ) FROM unviewed_items_by_feed WHERE feed_id = %s AND viewed = 0', (feed,)).fetchone())[0]
        return result
