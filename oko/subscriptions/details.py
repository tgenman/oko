"""Persistent feed information; callers own the transaction of each real change."""
from collections.abc import Callable
import sqlite3
from typing import Any, Literal


class FeedDetails:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float]) -> None:
        self.db, self.clock = db, clock
        db.execute('''CREATE TABLE IF NOT EXISTS feed_details (
            feed TEXT PRIMARY KEY, created REAL, modified REAL, updated REAL,
            marked_read REAL, description TEXT)''')
        db.commit()

    def created(self, feed: str) -> None:
        now = self.clock()
        self.db.execute('INSERT OR IGNORE INTO feed_details(feed,created,modified) VALUES (?,?,?)', (feed, now, now))

    def record(self, feed: str, field: Literal['modified', 'updated', 'marked_read']) -> None:
        self.db.execute(f'INSERT INTO feed_details(feed,{field}) VALUES (?,?) ON CONFLICT(feed) DO UPDATE SET {field}=excluded.{field}', (feed, self.clock()))

    def description(self, feed: str, value: str | None) -> None:
        if value is not None:
            self.db.execute('INSERT INTO feed_details(feed,description) VALUES (?,?) ON CONFLICT(feed) DO UPDATE SET description=excluded.description', (feed, value))

    def snapshot(self, feed: str) -> dict[str, Any]:
        row = self.db.execute('SELECT * FROM feed_details WHERE feed=?', (feed,)).fetchone()
        result = {field: row[field] if row else None for field in ('created', 'modified', 'updated', 'marked_read', 'description')}
        result['unread'] = self.db.execute('SELECT COUNT(*) FROM items WHERE feed_id=? AND viewed=0', (feed,)).fetchone()[0]
        return result
