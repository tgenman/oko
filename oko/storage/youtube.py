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
        from oko.storage.problems import _readable_timestamp
        blocked_known=_readable_timestamp(row['blocked_until'])
        next_known=_readable_timestamp(row['next_at'])
        paused=not blocked_known or row['blocked_until']>wall
        next_value=max(row['next_at'],row['blocked_until']) if blocked_known and next_known else float('inf')
        return dict(mode=config['mode'], address=config['address'], has_username=bool(config['username']),
                    has_password=bool(config['password']), paused=paused,
                    next_at=next_value, strikes=row['strikes'],
                    reason='YouTube ограничил получение субтитров' if paused else '')

    @reading_value
    def _limits(self) -> Row:
        row = self.db.execute('SELECT * FROM youtube_access WHERE id = 1').fetchone()
        assert row is not None
        return row

    def ready_at(self) -> float:
        row = self._limits()
        from oko.storage.problems import _readable_timestamp
        if not _readable_timestamp(row['next_at']) or not _readable_timestamp(row['blocked_until']):return float('inf')
        return float(max(row['next_at'], row['blocked_until']))

    def start(self, wall: float) -> None:
        with self.db:
            from oko.storage import memberships
            memberships.lock_graph(self.db)
            from oko.storage.subtitle_problems import drain_due
            drain_due(self.store,wall)
            from oko.storage.youtube_fetch_problems import drain_due as drain_fetch_due
            drain_fetch_due(self.store,wall)
            self.db.execute('UPDATE youtube_access SET next_at = %s WHERE id = 1', (wall + self.interval,))
            from oko.storage.subtitle_problems import sync_timer
            sync_timer(self.store,wall)
            from oko.storage.youtube_fetch_problems import sync_timer as sync_fetch_timer
            sync_fetch_timer(self.store,wall)
            manager=getattr(self.store,'incomplete_draft',None)
            if manager is not None:manager.mark_youtube_groups(pause=False)

    def blocked(self, wall: float, retry_after: float) -> None:
        with self.db:
            from oko.storage import memberships,source_groups
            memberships.lock_graph(self.db)
            from oko.storage.subtitle_problems import drain_due
            drain_due(self.store,wall)
            from oko.storage.youtube_fetch_problems import drain_due as drain_fetch_due
            drain_fetch_due(self.store,wall)
            if self.store.problems.processor_resume_candidate(wall):
                self.store.problems.finish_processor_change([],None,wall)
            previous=self.store.problems.prepare_processor_pause(wall)
            row = expect_row(self.db.execute('SELECT strikes, blocked_until FROM youtube_access WHERE id = 1').fetchone())
            strikes = None if row['strikes'] is None else min(6, row['strikes'] + 1)
            delay = 21600 if strikes is None else min(900 * 2 ** (strikes - 1), 21600)
            until = wall + max(delay, retry_after)
            if strikes is None:
                from oko.storage.problems import _readable_timestamp
                if _readable_timestamp(row['blocked_until']):
                    until = max(until, row['blocked_until'])
                else:
                    until = row['blocked_until']
            self.db.execute('UPDATE youtube_access SET strikes = %s , blocked_until = %s WHERE id = 1', (strikes, until))
            from oko.storage.subtitle_problems import sync_timer
            sync_timer(self.store,wall)
            from oko.storage.youtube_fetch_problems import sync_timer as sync_fetch_timer
            sync_fetch_timer(self.store,wall)
            source_groups.sync_youtube_timer(self.store,wall)
            manager=getattr(self.store,'incomplete_draft',None)
            if manager is not None:manager.mark_youtube_groups(pause=True)
            self.store.problems.finish_processor_change([],previous,wall)
            old_paused=self.store.read_versions._processor_youtube_paused
            self.db.after_rollback(lambda: setattr(self.store.read_versions,'_processor_youtube_paused',old_paused))
            self.db.after_commit(lambda: setattr(self.store.read_versions,'_processor_youtube_paused',True))

    def completed(self) -> None:
        with self.db:
            from oko.storage import memberships
            memberships.lock_graph(self.db)
            from oko.storage.subtitle_problems import drain_due
            drain_due(self.store,self.store.wall_clock())
            from oko.storage.youtube_fetch_problems import drain_due as drain_fetch_due
            drain_fetch_due(self.store,self.store.wall_clock())
            pointer=self.db.execute("""SELECT processor_reservation_id,blocked_until,
                EXISTS(SELECT 1 FROM state WHERE key LIKE 'source-youtube-timed:%%' LIMIT 1) AS source_timed
                FROM youtube_access WHERE id=1""").fetchone()
            source_timed=bool(pointer and pointer['source_timed'] and pointer['blocked_until'])
            reservation=self.store.problems.processor_reservation(pointer)
            if reservation is not None:
                deadline=min(float(reservation['deadline']),self.store.wall_clock())
                self.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s AND identity=%s',(json.dumps(self.store.problems.processor_reservation_body(deadline)),reservation['id'],'processor:reservation:'+reservation['id']))
            self.db.execute('UPDATE youtube_access SET strikes = 0 , blocked_until = 0 WHERE id = 1')
            from oko.storage.subtitle_problems import sync_timer
            sync_timer(self.store,self.store.wall_clock())
            from oko.storage.youtube_fetch_problems import sync_timer as sync_fetch_timer
            sync_fetch_timer(self.store,self.store.wall_clock())
            if source_timed:
                from oko.storage import source_groups
                source_groups.sync_youtube_timer(self.store,self.store.wall_clock())
            manager=getattr(self.store,'incomplete_draft',None)
            if manager is not None:manager.mark_youtube_groups(pause=True)
            self.store.problems.finish_processor_change([],None,self.store.wall_clock())
            old_paused=self.store.read_versions._processor_youtube_paused
            self.db.after_rollback(lambda: setattr(self.store.read_versions,'_processor_youtube_paused',old_paused))
            self.db.after_commit(lambda: setattr(self.store.read_versions,'_processor_youtube_paused',False))
