"""Current processing work and reconciliation of dependent reader results."""
from oko.storage import material_read
from oko.storage.reading import retry_time
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from oko.processing.contracts import FilterResult, Theses
from oko.storage.summary_results import SummaryResults

if TYPE_CHECKING:
    from oko.storage.store import Store


Result = Theses | FilterResult


from oko.domain.processing import ProcessingWait


class ProcessingState:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self._repairs: set[str] = set()
        self.theses = SummaryResults(store)
        store.db.executescript("""
        CREATE TABLE IF NOT EXISTS processing_dirty (item TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS processing_stage_dirty (kind TEXT,item TEXT,PRIMARY KEY(kind,item));
        CREATE TRIGGER IF NOT EXISTS processing_item_added AFTER INSERT ON items
          WHEN NEW.viewed=0 BEGIN INSERT INTO processing_dirty VALUES (NEW.id) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_item_changed AFTER UPDATE OF complete,members,viewed,feed_id ON items
          WHEN NEW.complete IS NOT OLD.complete OR NEW.members IS NOT OLD.members
            OR NEW.viewed IS NOT OLD.viewed OR NEW.feed_id IS NOT OLD.feed_id
          BEGIN INSERT INTO processing_dirty VALUES (NEW.id) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_filter_changed AFTER UPDATE OF status,generation ON jobs
          WHEN NEW.kind='filter' AND (NEW.status IS NOT OLD.status OR NEW.generation IS NOT OLD.generation)
          BEGIN INSERT INTO processing_dirty VALUES (NEW.item) ON CONFLICT(item) DO NOTHING; END;
        """)
    def waiting(self, item: str, kind: Literal['filter', 'theses'], now: float, wall_now: float) -> ProcessingWait | None:
        row = material_read.jobs(self.store.db,item).get(kind)
        if not row or row['status'] != 'pending' or not row['error']:
            return None
        reason: Literal['processor_not_configured', 'public_ai_not_allowed', 'processor_unavailable', 'retry_scheduled']
        if row['problem_code'] == 'processor-config':
            reason = 'processor_not_configured'
        elif row['problem_code'] == 'ai-permission':
            reason = 'public_ai_not_allowed'
        else:
            reason = 'retry_scheduled'
        return ProcessingWait(reason, retry_time(row['next'], now, wall_now) if row['next'] and reason != 'public_ai_not_allowed' else None)

    def recover(self) -> None:
        """Only startup scans the whole library, including interrupted file moves."""
        with self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO processing_dirty SELECT id FROM items WHERE viewed=0')
        self.sync()

    def invalidate(self, scope: str = 'oko', owner: str = 'global') -> None:
        query = 'SELECT DISTINCT i.id FROM items i JOIN reading_memberships m ON m.publication_id=i.id JOIN feeds f ON f.id=m.feed_id WHERE i.viewed=0'
        params: tuple[str, ...] = ()
        if scope in ('feed', 'folder'):
            query += ' AND ' + ('f.id=?' if scope == 'feed' else 'f.folder_id=?')
            params = (owner,)
        self.store.db.execute('INSERT OR IGNORE INTO processing_dirty ' + query, params)

    def repair(self, item: str) -> None:
        """A read can discover missing bytes without writing inside its snapshot."""
        self._repairs.add(item)

    def sync(self) -> None:
        """Reconcile persisted affected publications in dependency order."""
        if self._repairs:
            with self.store.db:
                self.store.db.executemany('INSERT INTO processing_dirty VALUES (?) ON CONFLICT(item) DO NOTHING', [(item,) for item in self._repairs])
            self._repairs.clear()
        with self.store.db:
            self.store.db.execute("DELETE FROM processing_stage_dirty WHERE NOT EXISTS (SELECT 1 FROM items WHERE id=processing_stage_dirty.item AND viewed=0)")
        items = [row[0] for row in self.store.db.execute('SELECT item FROM processing_dirty')]
        for item in items:
            # No suspension: changes made by this reconciliation are included
            # before removing its dirty marker. Interrupted passes retain it.
            with self.store.db:
                self.store.publications.reconcile(item)
                self.store.db.executemany("INSERT OR IGNORE INTO processing_stage_dirty VALUES (?,?)",[(kind,item) for kind in ('filter','theses')])
            with self.store.db:
                self.store.db.execute('DELETE FROM processing_dirty WHERE item=?', (item,))
