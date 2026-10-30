"""Transitional status revision, library identity and membership notifications.

The card list and individual materials are read directly.
"""
from oko.storage.database import expect_row, Row
import json
import uuid
from typing import TYPE_CHECKING

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
    def memberships_changed(self, identities: list[str], *, enqueue: bool = True) -> None:
        ordered = sorted(set(identities))
        if not ordered:
            return
        if enqueue:
            for start in range(0,len(ordered),400):
                batch = ordered[start:start+400]
                self.db.execute('INSERT INTO processing_dirty(item) SELECT unnest(%s::text[]) ON CONFLICT DO NOTHING', (batch,))
        from oko.storage.reader_status import changed as status_changed
        status_changed(self.db)

    def version(self, kind: str, identity: str) -> Row | None:
        if (kind,identity) != ('status','runtime'):
            return None
        return self.db.execute("SELECT (value::jsonb->>'version')::bigint AS version, (value::jsonb->>'changed')::double precision AS changed FROM state WHERE key='reader-status-revision'").fetchone()

    def advance(self, now: float | None = None, wall: float | None = None) -> None:
        now=self.store.clock() if now is None else now
        wall=self.store.wall_clock() if wall is None else wall
        with self.db:
            removed = self.db.execute('DELETE FROM reader_problems WHERE rowid IN ( SELECT rowid FROM reader_problems WHERE finished <= %s ORDER BY finished LIMIT 200 )',(wall-86400,)).rowcount
            if removed:
                from oko.storage.reader_status import changed as status_changed
                status_changed(self.db)
            if now >= self._next_action_cleanup:
                self.store.problems.cleanup_receipts(wall - 86400)
                self._next_action_cleanup=now+60
