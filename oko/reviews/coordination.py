"""Current processing work and reconciliation of dependent reader results."""
from oko.diagnostics import record_caught
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from oko.processing.contracts import FilterResult, ProcessingError, ProcessingRequest, Theses
from oko.reviews.theses import PublicationTheses
from oko.processing.execution import CodexChoice

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


Result = Theses | FilterResult


@dataclass(frozen=True)
class _Operation:
    request: Callable[['Job'], tuple[ProcessingRequest, str, bool] | None]
    signature: Callable[[str], str | None]
    save: Callable[['Job', str, Result | None], None]
    stage: str
    reconcile: bool = False


@dataclass
class PreparedWork:
    """A request and its acceptance rules remain one owned piece of work."""
    _owner: 'ProcessingCoordination'
    _operation: _Operation
    _job: 'Job'
    _signature: str
    request: ProcessingRequest
    public_ai: bool
    _closed: bool = False

    @property
    def stage(self) -> str:
        return self._operation.stage

    def current(self) -> bool:
        return (not self._closed and self._owner.store.valid(self._job)
                and self._signature == self._operation.signature(self._job.item))

    def accept(self, result: Result | None) -> None:
        if not self.current():
            return
        self._operation.save(self._job, self._signature, result)
        if self._operation.reconcile:
            self._owner.sync()

    def close(self) -> None:
        if not self._closed:
            self._closed = True


@dataclass(frozen=True)
class ProcessingWait:
    reason: Literal['processor_not_configured', 'public_ai_not_allowed', 'processor_unavailable', 'retry_scheduled']
    retry_at: float | None


class ProcessingCoordination:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self._repairs: set[str] = set()
        self.theses = PublicationTheses(store)
        store.db.executescript("""
        CREATE TABLE IF NOT EXISTS processing_dirty (item TEXT PRIMARY KEY);
        CREATE TRIGGER IF NOT EXISTS processing_item_added AFTER INSERT ON items
          WHEN NEW.viewed=0 BEGIN INSERT INTO processing_dirty VALUES (NEW.id) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_item_changed AFTER UPDATE OF complete,members,viewed,feed_id ON items
          WHEN NEW.complete IS NOT OLD.complete OR NEW.members IS NOT OLD.members
            OR NEW.viewed IS NOT OLD.viewed OR NEW.feed_id IS NOT OLD.feed_id
          BEGIN INSERT INTO processing_dirty VALUES (NEW.id) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_filter_changed AFTER UPDATE OF status,generation ON jobs
          WHEN NEW.kind='filter' AND (NEW.status IS NOT OLD.status OR NEW.generation IS NOT OLD.generation)
          BEGIN INSERT INTO processing_dirty VALUES (NEW.item) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_filter_restored AFTER INSERT ON filter_restores
          BEGIN INSERT INTO processing_dirty VALUES (NEW.item) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_restore_changed AFTER UPDATE ON filter_restores
          BEGIN INSERT INTO processing_dirty VALUES (NEW.item) ON CONFLICT(item) DO NOTHING; END;
        """)
        # Fixed domain operations, private to this module; no plugin registry.
        self._operations = {
            'filter': _Operation(store.filters.request, store.filters.signature, self._save_filter, 'Фильтрация', reconcile=True),
            'theses': _Operation(self.theses.request, self.theses.signature, self._save_theses, 'Подготовка тезисов'),
        }

    def waiting(self, item: str, kind: Literal['filter', 'theses'], now: float, wall_now: float) -> ProcessingWait | None:
        row = self.store.db.execute("SELECT problem_code,error,next FROM jobs WHERE item=? AND kind=? AND status='pending'", (item, kind)).fetchone()
        if not row or not row['error']:
            return None
        reason: Literal['processor_not_configured', 'public_ai_not_allowed', 'processor_unavailable', 'retry_scheduled']
        if row['problem_code'] == 'processor-config':
            reason = 'processor_not_configured'
        elif row['problem_code'] == 'ai-permission':
            reason = 'public_ai_not_allowed'
        else:
            reason = 'retry_scheduled'
        return ProcessingWait(reason, wall_now + max(0, row['next'] - now) if row['next'] and reason != 'public_ai_not_allowed' else None)

    def recover(self) -> None:
        """Only startup scans the whole library, including interrupted file moves."""
        self.store.db.executescript("""
        CREATE TRIGGER IF NOT EXISTS processing_version_added AFTER INSERT ON content_versions
          BEGIN INSERT INTO processing_dirty VALUES (NEW.item) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_version_changed AFTER UPDATE OF fingerprint ON content_versions
          WHEN NEW.fingerprint IS NOT OLD.fingerprint
          BEGIN INSERT INTO processing_dirty VALUES (NEW.item) ON CONFLICT(item) DO NOTHING; END;
        CREATE TRIGGER IF NOT EXISTS processing_current_changed AFTER UPDATE ON content_current
          WHEN NEW.number IS NOT OLD.number
          BEGIN INSERT INTO processing_dirty VALUES (NEW.item) ON CONFLICT(item) DO NOTHING; END;
        """)
        with self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO processing_dirty SELECT id FROM items WHERE viewed=0')
        self.sync()

    def invalidate(self, scope: str = 'oko', owner: str = 'global') -> None:
        query = 'SELECT i.id FROM items i JOIN feeds f ON f.id=i.feed_id WHERE i.viewed=0'
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
        items = [row[0] for row in self.store.db.execute('SELECT item FROM processing_dirty')]
        for item in items:
            # No suspension: changes made by this reconciliation are included
            # before removing its dirty marker. Interrupted passes retain it.
            self.store.filters.sync([item])
            self.theses.sync([item])
            with self.store.db:
                self.store.db.execute('DELETE FROM processing_dirty WHERE item=?', (item,))

    def retry_failed(self, feed: str) -> None:
        """Retry failed processing only; accepted current results are untouched."""
        self.sync()
        rows = self.store.db.execute("""SELECT j.kind,j.item FROM jobs j JOIN items i ON i.id=j.item
            WHERE i.feed_id=? AND i.viewed=0 AND j.kind IN ('filter','theses')
            AND j.status IN ('pending','done') AND j.error IS NOT NULL""", (feed,)).fetchall()
        for row in rows:
            if row['kind'] == 'filter':
                self.store.filters.retry(row['item'])
            else:
                self.theses.retry(row['item'])

    def prepare(self, job: 'Job') -> PreparedWork:
        operation = self._operations[job.kind]
        if job.kind in ('filter', 'theses'):
            row = self.store.db.execute('SELECT feed_id FROM items WHERE id=? AND viewed=0', (job.item,)).fetchone()
            if row:
                settings = self.store.subscriptions.effective('feed', row['feed_id'])
                if settings['effective']['processor']['value'] == 'codex' and not settings['feed']['public_ai']:
                    raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель', code='ai-permission')
        try:
            prepared = operation.request(job)
            if not prepared:
                raise ProcessingError('Исходник недоступен для обработки')
            request, signature, public_ai = prepared
            if request.processor == 'codex' and not public_ai:
                raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель', code='ai-permission')
            request = self._execution_choice(job, signature, request)
            return PreparedWork(self, operation, job, signature, request, public_ai)
        except BaseException:
            record_caught('coordination.prepare')
            raise

    def _execution_choice(self, job: 'Job', signature: str, request: ProcessingRequest) -> ProcessingRequest:
        origin = self.store.saved_execution(job, signature)
        if origin is None:
            if request.processor == 'codex':
                config = self.store.codex_config()
                if config is None:
                    return request  # Adapter checks auth/quota before missing choice.
                origin = dict(processor='codex', **config.choice(request.operation).model_dump())
            else:
                local = self.store.processor_config()
                origin = dict(processor=request.processor, model=local.model if local else None)
            self.store.save_execution(job, signature, origin)
        choice = CodexChoice.model_validate({k: origin[k] for k in ('model', 'reasoning_effort', 'fast')}) if origin['processor'] == 'codex' else None
        return request.model_copy(update={'codex_choice': choice})

    def retry_theses(self, item: str) -> bool:
        return self.theses.retry(item)

    def _save_filter(self, job: 'Job', signature: str, result: Result | None) -> None:
        if result is None:
            raise ValueError('Filter result required')
        self.store.filters.save(job, signature, FilterResult.model_validate(result.model_dump()))

    def _save_theses(self, job: 'Job', signature: str, result: Result | None) -> None:
        if result is None:
            raise ValueError('Theses result required')
        self.theses.save(job, signature, Theses.model_validate(result.model_dump()))
