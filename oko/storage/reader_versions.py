"""Persisted versions of reader entities and their derived membership.

Versions are updated by the same database transaction as their inputs.
The current card list is read directly and does not maintain list digests.
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
            for kind, identity in [('status', 'runtime'), ('navigation', 'navigation')]:
                self.db.execute('INSERT INTO reader_entities VALUES ( %s , %s , 1 , %s ) ON CONFLICT DO NOTHING', (kind, identity, store.wall_clock()))
        identity = json.loads(expect_row(self.db.execute("SELECT value FROM state WHERE key='storage-id'").fetchone())[0])
        if not isinstance(identity, str):
            raise ValueError('Invalid storage identity')
        uuid.UUID(identity)
        self.identity = identity
    @staticmethod
    def _bump(kind: str, identity: str) -> str:
        return f"INSERT INTO reader_entities VALUES ( '{kind}' , {identity} , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed"

    def media_changed(self, item: str) -> None:
        for kind in ('material','media','card'):
            self.db.execute(self._bump(kind, '%s'), (item,))

    def linked_changed(self, item: str) -> None:
        self.media_changed(item)
        for kind in ('filter','theses'):
            self.db.execute(self._bump(kind,'%s'),(item,))

    def memberships_changed(self, identities: list[str]) -> None:
        ordered = sorted(set(identities))
        if not ordered:
            return
        now = self.store.wall_clock()
        for start in range(0,len(ordered),400):
            batch = ordered[start:start+400]
            self.db.execute("""INSERT INTO reader_entities(kind,identity,version,changed)
                SELECT kind,item,1,%s FROM unnest(ARRAY['material','card','filter','theses']) kind
                CROSS JOIN unnest(%s::text[]) item
                ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed""", (now,batch))
            self.db.execute('INSERT INTO processing_dirty(item) SELECT unnest(%s::text[]) ON CONFLICT DO NOTHING', (batch,))
        for kind in ('navigation','status'):
            self.db.execute(self._bump(kind, "'navigation'" if kind=='navigation' else "'runtime'"))




    def version(self, kind: str, identity: str) -> Row | None:
        return self.db.execute('SELECT version , changed FROM reader_entities WHERE kind = %s AND identity = %s', (kind,identity)).fetchone()

    def advance(self, now: float | None = None, wall: float | None = None) -> None:
        now=self.store.clock() if now is None else now
        wall=self.store.wall_clock() if wall is None else wall
        with self.db:
            self.db.execute('DELETE FROM reader_problems WHERE rowid IN ( SELECT rowid FROM reader_problems WHERE finished <= %s ORDER BY finished LIMIT 200 )',(wall-86400,))
            if now >= self._next_action_cleanup:
                self.store.problems.cleanup_receipts(wall - 86400)
                self._next_action_cleanup=now+60
