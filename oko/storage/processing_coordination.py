"""Current processing work and reconciliation of dependent reader results."""
from oko.storage import material_read
from oko.storage.reading import retry_time
from oko.storage.transaction import begin_write
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING
from collections.abc import Iterator

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
        self.recovering = False
        self.background = False
        self.theses = SummaryResults(store)
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
        for _ in self.recovery_steps():
            pass
        self.recovering = False

    def recovery_steps(self, *, seed: bool = True) -> Iterator[None]:
        """Commit one publication at a time; callers may yield only between steps."""
        self.recovering = True
        if seed:
            with self.store.db:
                self.store.db.execute('INSERT INTO processing_dirty SELECT id FROM items_read WHERE viewed = 0 ON CONFLICT DO NOTHING')
            yield
        while True:
            self._queue_repairs()
            row = self.store.db.execute('SELECT item FROM processing_dirty ORDER BY item LIMIT 1').fetchone()
            if row is None:
                break
            self.reconcile_item(row[0])
            yield

    def invalidate(self, scope: str = 'oko', owner: str = 'global') -> None:
        query = 'SELECT DISTINCT i . id FROM items_read i JOIN reading_memberships m ON m . publication_id = i . id JOIN feeds f ON f . id = m . feed_id WHERE i . viewed = 0'
        params: tuple[str, ...] = ()
        if scope in ('feed', 'folder'):
            query += ' AND ' + ('f . id = %s' if scope == 'feed' else 'f . folder_id = %s')
            params = (owner,)
        self.store.db.execute(f'INSERT INTO processing_dirty {query} ON CONFLICT DO NOTHING', params)

    def repair(self, item: str) -> None:
        """A read can discover missing bytes without writing inside its snapshot."""
        self._repairs.add(item)

    def filter_changed(self, item: str) -> None:
        """Update the dependent stage in the same transaction as the filter Job."""
        from oko.storage.processing_state import ProcessingRepository
        from oko.storage.reading import reading_snapshot
        # An already queued publication has broader changes to reconcile first.
        if self.store.db.execute('SELECT 1 FROM processing_dirty WHERE item=%s', (item,)).fetchone():
            return
        repository = ProcessingRepository(self.store)
        with reading_snapshot():
            candidates = [(identity, signature, repository.satisfied(identity,'theses'), repository.revision(identity))
                          for identity,signature in repository.candidates('theses',item=item)]
        self.store.jobs.reconcile('theses',candidates)
        self.store.publications.status(item)

    def pending(self) -> bool:
        return bool(self._repairs or self.store.db.execute('SELECT 1 FROM processing_dirty LIMIT 1').fetchone())

    def sync(self) -> None:
        """Reconcile persisted affected publications in dependency order."""
        self._queue_repairs()
        if self.recovering or self.background:
            return
        items = [row[0] for row in self.store.db.execute('SELECT item FROM processing_dirty')]
        for item in items:
            self.reconcile_item(item)

    def _queue_repairs(self) -> None:
        if self._repairs:
            with self.store.db:
                self.store.db.executemany('INSERT INTO processing_dirty VALUES ( %s ) ON CONFLICT ( item ) DO NOTHING', [(item,) for item in self._repairs])
            self._repairs.clear()

    def reconcile_item(self, item: str) -> None:
        # Accepted state and both stage Jobs commit with removal of the marker.
        with self.store.db:
            begin_write(self.store.db)
            self.reconcile_accepted_item(item)

    def reconcile_accepted_item(self, item: str) -> None:
        """Update both processing Jobs inside the owner's acceptance transaction."""
        from oko.storage.processing_state import ProcessingRepository
        from oko.storage.reading import reading_snapshot
        if not self.store.db.in_transaction:
            raise RuntimeError('Accepted publication requires an owned transaction')
        repository = ProcessingRepository(self.store)
        self.store.publications.reconcile(item)
        self.store.db.execute('DELETE FROM processing_dirty WHERE item=%s', (item,))
        for kind in ('filter','theses'):
            with reading_snapshot():
                candidates = [(identity, signature, repository.satisfied(identity,kind), repository.revision(identity))
                              for identity,signature in repository.candidates(kind,item=item)]
            self.store.jobs.reconcile(kind,candidates)
            # The filter transition can invalidate the dependent stage; this
            # transaction evaluates it immediately.
            self.store.db.execute('DELETE FROM processing_dirty WHERE item=%s', (item,))
        self.store.publications.status(item)
