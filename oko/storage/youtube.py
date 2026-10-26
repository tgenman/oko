"""Durable YouTube caption admission state, owned by Store."""
from oko.storage.database import expect_row, Row
import json
from oko.storage.reading import reading_value, reader_wall
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any

from oko.collection.youtube.access import proxy_settings

if TYPE_CHECKING:
    from oko.storage.store import Store
from oko.storage.job_types import Job


class YouTubeAccess:
    interval = 10.0

    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.db = store.db
        self.path = store.root / 'youtube-proxy.json'

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
    def _limits(self) -> Row:
        row = self.db.execute('SELECT * FROM youtube_access WHERE id = 1').fetchone()
        assert row is not None
        return row

    def ready_at(self) -> float:
        row = self._limits()
        return float(max(row['next_at'], row['blocked_until']))

    def start(self, wall: float) -> None:
        with self.db:
            self.db.execute('UPDATE youtube_access SET next_at = %s WHERE id = 1', (wall + self.interval,))

    def blocked(self, wall: float, retry_after: float) -> None:
        with self.db:
            strikes = min(6, expect_row(self.db.execute('SELECT strikes FROM youtube_access WHERE id = 1').fetchone())[0] + 1)
            until = wall + max(min(900 * 2 ** (strikes - 1), 21600), retry_after)
            self.db.execute('UPDATE youtube_access SET strikes = %s , blocked_until = %s WHERE id = 1', (strikes, until))

    def completed(self) -> None:
        with self.db:
            self.db.execute('UPDATE youtube_access SET strikes = 0 , blocked_until = 0 WHERE id = 1')
