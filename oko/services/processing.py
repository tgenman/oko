"""Preparation and acceptance of domain results; workers own attempts and models."""
from collections.abc import Callable
from typing import Any
from oko.processing.execution import CodexSettings
from oko.processing.llamacpp import ProcessorConfig
from oko.storage.store import Store
from oko.domain.processing import Kind, ProcessingInput, ProcessingPreparation
from oko.storage.processing_state import ProcessingRepository
from oko.storage.access import repository_access
from oko.storage.reading import reading_snapshot
from oko.processing.contracts import FilterResult, Theses


class StaleInput(Exception):
    """The attempted input no longer represents the publication."""


class ProcessingService:
    def __init__(self, storage: Callable[[], Store]) -> None:
        self._storage = storage

    @repository_access
    @reading_snapshot()
    def candidates(self, kind: Kind, *, limit: int | None = None, item: str | None = None) -> list[tuple[str, str | None, bool, str]]:
        repository=ProcessingRepository(self._storage())
        return [(identity,signature,repository.satisfied(identity,kind),repository.revision(identity)) for identity,signature in repository.candidates(kind,limit=limit,item=item)]

    @repository_access
    def prepare(self, item: str, kind: Kind) -> ProcessingPreparation:
        return ProcessingRepository(self._storage()).prepare(item,kind)

    @repository_access
    @reading_snapshot()
    def current(self, value: ProcessingInput) -> bool:
        return ProcessingRepository(self._storage()).current(value)

    @repository_access
    def accept(self, value: ProcessingInput, result: FilterResult | Theses | None, origin: dict[str, Any] | None) -> None:
        if not self.current(value):
            raise StaleInput
        if result is not None:
            result.validate_sources(value.request)
        ProcessingRepository(self._storage()).apply(value,result,origin)

    @repository_access
    def processor_config(self) -> ProcessorConfig | None:
        return ProcessingRepository(self._storage()).processor_config()

    @repository_access
    def codex_config(self) -> CodexSettings | None:
        return ProcessingRepository(self._storage()).codex_config()

    @repository_access
    def refresh(self, item: str) -> None:
        ProcessingRepository(self._storage()).refresh(item)

    @repository_access
    def regenerate(self, item: str, kind: Kind) -> bool:
        return ProcessingRepository(self._storage()).regenerate(item,kind)

    @repository_access
    def reconcile(self) -> None:
        self._storage().processing.sync()

    @repository_access
    def feed(self, item: str) -> str:
        return self._storage().item_feed(item)
