"""Current processing work and reconciliation of dependent reader results."""
from oko.diagnostics import record_caught
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from oko.processing.contracts import EventsResult, FilterResult, ProcessingError, ProcessingRequest, Theses
from oko.reviews.theses import PublicationTheses
from oko.processing.execution import CodexChoice

if TYPE_CHECKING:
    from oko.storage.store import Job, Store


Result = Theses | FilterResult | EventsResult


@dataclass(frozen=True)
class _Operation:
    request: Callable[['Job'], tuple[ProcessingRequest, str, bool] | None]
    signature: Callable[[str], str | None]
    save: Callable[['Job', str, Result | None], None]
    stage: str
    release: Callable[['Job'], None] | None = None
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

    def finish_without_model(self) -> bool:
        if self._job.kind != 'digest' or self.request.sources:
            return False
        self.accept(None)
        return True

    def accept(self, result: Result | None) -> None:
        if not self.current():
            return
        self._operation.save(self._job, self._signature, result)
        if self._operation.reconcile:
            self._owner.sync()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._operation.release:
                self._operation.release(self._job)


class ProcessingCoordination:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self.theses = PublicationTheses(store)
        # Fixed domain operations, private to this module; no plugin registry.
        self._operations = {
            'filter': _Operation(store.filters.request, store.filters.signature, self._save_filter, 'Фильтрация', reconcile=True),
            'theses': _Operation(self.theses.request, self.theses.signature, self._save_theses, 'Подготовка тезисов'),
            'events': _Operation(store.events.request, store.events.signature, self._save_events, 'Объединение событий'),
            'digest': _Operation(store.digests.request, store.digests.signature, self._save_digest, 'Подготовка обзора', store.digests.release, True),
        }

    def sync(self) -> None:
        """One owner of dependency order, also used after accepting a result."""
        self.store.filters.sync()
        self.store.digests.sync()
        self.store.events.sync()
        self.theses.sync()

    def prepare(self, job: 'Job') -> PreparedWork:
        operation = self._operations[job.kind]
        if job.kind in ('filter', 'theses'):
            row = self.store.db.execute('SELECT feed_id FROM items WHERE id=? AND viewed=0', (job.item,)).fetchone()
            if row:
                settings = self.store.subscriptions.effective('feed', row['feed_id'])
                if settings['effective']['processor']['value'] == 'codex' and not settings['feed']['public_ai']:
                    raise ProcessingError('Запрещена публичная AI-обработка; выберите локальную модель')
        try:
            prepared = operation.request(job)
            if not prepared:
                raise ProcessingError('Исходник недоступен для обработки')
            request, signature, public_ai = prepared
            if request.processor == 'codex' and not public_ai:
                raise ProcessingError('Запрещена публичная AI-обработка набора; разрешение требуется от всех фидов')
            request = self._execution_choice(job, signature, request)
            return PreparedWork(self, operation, job, signature, request, public_ai)
        except BaseException:
            record_caught('coordination.prepare')
            # Request preparation can allocate a digest plan before failing.
            if operation.release:
                operation.release(job)
            raise

    def _execution_choice(self, job: 'Job', signature: str, request: ProcessingRequest) -> ProcessingRequest:
        origin = self.store.saved_execution(job, signature)
        if origin is None:
            if request.processor == 'codex':
                config = self.store.codex_config()
                if config is None:
                    return request  # Adapter checks auth/quota before missing choice.
                if job.kind == 'digest':
                    self.store.digests.require_known_execution(job.item, signature)
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

    def _save_events(self, job: 'Job', signature: str, result: Result | None) -> None:
        if result is None:
            raise ValueError('Events result required')
        self.store.events.save(job, signature, EventsResult.model_validate(result.model_dump()))

    def _save_digest(self, job: 'Job', signature: str, result: Result | None) -> None:
        self.store.digests.save(job, signature, Theses.model_validate(result.model_dump()) if result is not None else None)
