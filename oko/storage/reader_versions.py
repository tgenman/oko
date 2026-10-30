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
        row = self.db.execute("""SELECT
            (SELECT next FROM jobs WHERE status='pending' AND kind IN ('fetch','filter','theses') AND next>%s ORDER BY next LIMIT 1),
            (SELECT blocked_until FROM youtube_access WHERE id=1 AND blocked_until>%s),
            (SELECT finished FROM reader_problems WHERE finished>%s ORDER BY finished LIMIT 1),
            (SELECT jsonb_build_array(next_at,blocked_until,strikes) FROM youtube_access WHERE id=1),
            (SELECT coalesce(jsonb_agg(jsonb_build_array(key,value) ORDER BY key),'[]'::jsonb)
             FROM state WHERE key='work-enabled' OR key='processor:codex' OR key LIKE 'feed-status:%%')""",
            (reader_now(self.store.clock()),wall,wall-86400)).fetchone()
        return (row[0],row[1],row[2],row[3],row[4]) if row else (None,None,None,None,None)

    def advance(self, now: float | None = None, wall: float | None = None) -> None:
        now=self.store.clock() if now is None else now
        wall=self.store.wall_clock() if wall is None else wall
        with self.db:
            begin_write(self.db)
            cutoff = wall-86400
            expired = [row['id'] for row in self.db.execute('SELECT id FROM reader_problems WHERE finished <= %s ORDER BY finished,id LIMIT 200',(cutoff,))]
            removed = self.db.execute('DELETE FROM reader_problems WHERE id=ANY(%s) AND finished <= %s',(expired,cutoff)).rowcount if expired else 0
            if removed:
                from oko.storage.reader_status import changed as status_changed
                status_changed(self.db)
            if now >= self._next_action_cleanup:
                self.store.problems.cleanup_receipts(wall - 86400)
                self._next_action_cleanup=now+60
