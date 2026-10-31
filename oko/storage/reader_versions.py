"""Transitional status revision, library identity and membership notifications.

The card list and individual materials are read directly.
"""
from oko.storage.database import expect_row, Row
from oko.storage.transaction import begin_write
import json
import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from oko.storage.store import Store


class ReaderVersions:
    def __init__(self, store: 'Store') -> None:
        self.store, self.db = store, store.db
        self._next_action_cleanup = float('-inf')
        self._source_legacy_gc_cursor: str | None = None
        self._next_source_legacy_gc = float('-inf')
        self._processor_youtube_paused = store.problems.processor_youtube_paused(store.wall_clock())
        with self.db:
            self.db.execute("INSERT INTO state(key,value) VALUES ('storage-id',%s) ON CONFLICT DO NOTHING", (json.dumps(str(uuid.uuid4())),))
        identity = json.loads(expect_row(self.db.execute("SELECT value FROM state WHERE key='storage-id'").fetchone())[0])
        if not isinstance(identity, str):
            raise ValueError('Invalid storage identity')
        uuid.UUID(identity)
        self.identity = identity
    def memberships_changed(self, identities: list[str]) -> None:
        ordered = sorted(set(identities))
        if not ordered:
            return
        from oko.storage.reader_status import changed as status_changed
        status_changed(self.db)

    def version(self, kind: str, identity: str) -> Row | None:
        if (kind,identity) != ('status','runtime'):
            return None
        return self.db.execute("SELECT (value::jsonb->>'version')::bigint AS version, (value::jsonb->>'changed')::double precision AS changed FROM state WHERE key='reader-status-revision'").fetchone()

    def history_versions(self) -> list[Any]:
        row = self.db.execute("SELECT coalesce(jsonb_agg(jsonb_build_array(feed,xmin::text) ORDER BY feed),'[]'::jsonb) FROM initial_history").fetchone()
        return row[0] if row else []

    def time_boundaries(self) -> tuple[Any, ...]:
        from oko.storage.reading import reader_now, reader_wall
        wall = reader_wall(self.store.wall_clock())
        reservation=self.store.problems.processor_reservation()
        reserved_id=reservation['id'] if reservation else None
        row = self.db.execute("""SELECT
            (SELECT next FROM jobs WHERE status='pending' AND kind IN ('fetch','filter','theses') AND next>%s ORDER BY next LIMIT 1),
            (SELECT blocked_until FROM youtube_access WHERE id=1 AND blocked_until>%s),
            (SELECT greatest(next_at,blocked_until) FROM youtube_access WHERE id=1 AND greatest(next_at,blocked_until)>%s),
            (SELECT finished FROM reader_problems WHERE finished>%s AND id IS DISTINCT FROM %s ORDER BY finished LIMIT 1),
            (SELECT jsonb_build_array(next_at,blocked_until,strikes) FROM youtube_access WHERE id=1),
            (SELECT coalesce(jsonb_agg(jsonb_build_array(key,value) ORDER BY key),'[]'::jsonb)
             FROM state WHERE key='work-enabled' OR key='processor:codex' OR key LIKE 'feed-status:%%')""",
            (reader_now(self.store.clock()),wall,wall,wall-86400,reserved_id)).fetchone()
        return (row[0],row[1],row[2],row[3],row[4],row[5]) if row else (None,None,None,None,None,None)

    def advance(self, now: float | None = None, wall: float | None = None) -> None:
        now=self.store.clock() if now is None else now
        wall=self.store.wall_clock() if wall is None else wall
        with self.db:
            begin_write(self.db)
            paused=self.store.problems.processor_youtube_paused(wall)
            if paused!=self._processor_youtube_paused:
                from oko.storage import memberships,source_groups
                memberships.lock_graph(self.db)
                source_groups.sync_youtube_timer(self.store,wall)
                self.store.problems.finish_processor_change([],None,wall)
                old_paused=self._processor_youtube_paused
                self.db.after_rollback(lambda: setattr(self,'_processor_youtube_paused',old_paused))
                self.db.after_commit(lambda: setattr(self,'_processor_youtube_paused',paused))
            if now>=self._next_source_legacy_gc:
                from oko.storage.source_groups import cleanup_legacy_protected
                cursor,done=cleanup_legacy_protected(self.store,self._source_legacy_gc_cursor)
                old_cursor=self._source_legacy_gc_cursor
                old_next=self._next_source_legacy_gc
                def restore_source_legacy_gc() -> None:
                    self._source_legacy_gc_cursor=old_cursor
                    self._next_source_legacy_gc=old_next
                self.db.after_rollback(restore_source_legacy_gc)
                self.db.after_commit(lambda: setattr(self,'_source_legacy_gc_cursor',cursor))
                if done:self.db.after_commit(lambda: setattr(self,'_next_source_legacy_gc',now+60))
            cutoff = wall-86400
            reservation=self.store.problems.processor_reservation()
            reserved_id=reservation['id'] if reservation else None
            expired = [row['id'] for row in self.db.execute('SELECT id FROM reader_problems WHERE finished <= %s AND id IS DISTINCT FROM %s ORDER BY finished,id LIMIT 200',(cutoff,reserved_id))]
            removed = self.db.execute('DELETE FROM reader_problems WHERE id=ANY(%s) AND finished <= %s',(expired,cutoff)).rowcount if expired else 0
            if removed:
                from oko.storage.reader_status import changed as status_changed
                status_changed(self.db)
            if now >= self._next_action_cleanup:
                self.store.problems.cleanup_receipts(wall - 86400)
                self._next_action_cleanup=now+60
