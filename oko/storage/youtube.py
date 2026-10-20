"""Durable YouTube caption admission state, owned by Store."""
import json
import sqlite3
from oko.storage.reading import reading_value, reader_wall
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any, cast

from oko.collection.youtube.access import proxy_settings

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


class YouTubeAccess:
    interval = 10.0

    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.db = store.db
        self.path = store.root / 'youtube-proxy.json'
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS youtube_access (
              id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL DEFAULT 0,
              blocked_until REAL DEFAULT 0, strikes INTEGER DEFAULT 0);
            INSERT OR IGNORE INTO youtube_access(id) VALUES (1);
            CREATE TABLE IF NOT EXISTS youtube_attempts (
              item TEXT PRIMARY KEY, attempts INTEGER DEFAULT 0, terminal INTEGER DEFAULT 0);
        ''')

    @reading_value
    def config(self) -> dict[str, str]:
        default = dict(mode='direct', address='', username='', password='')
        if not self.path.exists():
            return default
        return proxy_settings(json.loads(self.path.read_text()), default)

    def configure(self, body: Any) -> None:
        config = proxy_settings(body, self.config())
        descriptor, filename = tempfile.mkstemp(prefix='.youtube-proxy-', dir=self.store.root)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(config, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.path)
        finally:
            Path(filename).unlink(missing_ok=True)

    def status(self, wall: float) -> dict[str, Any]:
        wall = reader_wall(wall)
        config = self.config()
        row = self._limits()
        return dict(mode=config['mode'], address=config['address'], has_username=bool(config['username']),
                    has_password=bool(config['password']), paused=row['blocked_until'] > wall,
                    next_at=max(row['next_at'], row['blocked_until']), strikes=row['strikes'],
                    reason='YouTube ограничил получение субтитров' if row['blocked_until'] > wall else '')

    @reading_value
    def _limits(self) -> sqlite3.Row:
        row = self.db.execute('SELECT * FROM youtube_access WHERE id=1').fetchone()
        assert row is not None
        return cast(sqlite3.Row, row)

    def ready_at(self) -> float:
        row = self._limits()
        return float(max(row['next_at'], row['blocked_until']))

    def terminal(self, item: str) -> bool:
        row = self.db.execute('SELECT terminal,attempts FROM youtube_attempts WHERE item=?', (item,)).fetchone()
        return bool(row and (row['terminal'] or row['attempts'] >= 3))

    def start(self, item: str, wall: float) -> None:
        with self.db:
            self.db.execute('UPDATE youtube_access SET next_at=? WHERE id=1', (wall + self.interval,))
            self.db.execute('INSERT INTO youtube_attempts(item,attempts) VALUES (?,1) ON CONFLICT(item) DO UPDATE SET attempts=attempts+1', (item,))

    def blocked(self, job: 'Job', wall: float, retry_after: float) -> bool:
        """Record the shared pause; return whether the material may retry."""
        with self.db:
            strikes = min(6, self.db.execute('SELECT strikes FROM youtube_access WHERE id=1').fetchone()[0] + 1)
            until = wall + max(min(900 * 2 ** (strikes - 1), 21600), retry_after)
            self.db.execute('UPDATE youtube_access SET strikes=?,blocked_until=? WHERE id=1', (strikes, until))
            return not self.terminal(job.item)

    def completed(self, item: str, success: bool) -> None:
        with self.db:
            self.db.execute('UPDATE youtube_attempts SET terminal=1 WHERE item=?', (item,))
            if success:
                self.db.execute('UPDATE youtube_access SET strikes=0,blocked_until=0 WHERE id=1')

    def reset(self, item: str) -> None:
        self.db.execute('DELETE FROM youtube_attempts WHERE item=?', (item,))

    def defer(self, job: 'Job', now: float, delay: float, reason: str, *, blocked: bool = False) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',next=?,error=?,problem_code=? WHERE id=? AND generation=?",
                            (now + max(.05, delay), reason, 'youtube-admission' if blocked else None, job.id, job.generation))
