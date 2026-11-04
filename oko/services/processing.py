"""Preparation and acceptance of domain results; workers own attempts and models."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol
from oko.processing.execution import CodexSettings
from oko.processing.llamacpp import ProcessorConfig
from oko.domain.processing import Kind, ProcessingInput, ProcessingPreparation
from oko.services.access import repository_access
from oko.storage.reading import reading_snapshot
from oko.processing.contracts import FilterResult, Theses


class StaleInput(Exception):
    """The attempted input no longer represents the publication."""


class ProcessingOperations(Protocol):
    def candidates(self, kind: Kind, *, item: str) -> list[tuple[str, str | None]]: ...
    def satisfied(self, item: str, kind: Kind) -> bool: ...
    def revision(self, item: str) -> str: ...
    def prepare(self, item: str, kind: Kind) -> ProcessingPreparation: ...
    def current(self, value: ProcessingInput) -> bool: ...
    def apply(self, value: ProcessingInput, result: FilterResult | Theses | None, origin: dict[str, Any] | None) -> None: ...
    def processor_config(self) -> ProcessorConfig | None: ...
    def codex_config(self) -> CodexSettings | None: ...
    def refresh(self, item: str) -> None: ...
    def regenerate(self, item: str, kind: Kind) -> bool: ...
    def reconcile(self) -> None: ...
    def feed(self, item: str) -> str: ...


class ProcessingService:
    def __init__(self, *, access: Callable[[], AbstractContextManager[Any]],
                 repository: Callable[[], ProcessingOperations]) -> None:
        self._access, self._repository = access, repository

    @repository_access
    @reading_snapshot()
    def candidates(self, kind: Kind, *, item: str) -> list[tuple[str, str | None, bool, str]]:
        repository=self._repository()
        return [(identity,signature,repository.satisfied(identity,kind),repository.revision(identity)) for identity,signature in repository.candidates(kind,item=item)]

    @repository_access
    def prepare(self, item: str, kind: Kind) -> ProcessingPreparation:
        return self._repository().prepare(item,kind)

    @repository_access
    @reading_snapshot()
    def current(self, value: ProcessingInput) -> bool:
        return self._repository().current(value)

    @repository_access
    def accept(self, value: ProcessingInput, result: FilterResult | Theses | None, origin: dict[str, Any] | None) -> None:
        if not self.current(value):
            raise StaleInput
        if result is not None:
            result.validate_sources(value.request)
        self._repository().apply(value,result,origin)

    @repository_access
    def processor_config(self) -> ProcessorConfig | None:
        return self._repository().processor_config()

    @repository_access
    def codex_config(self) -> CodexSettings | None:
        return self._repository().codex_config()

    @repository_access
    def refresh(self, item: str) -> None:
        self._repository().refresh(item)

    @repository_access
    def regenerate(self, item: str, kind: Kind) -> bool:
        return self._repository().regenerate(item,kind)

    @repository_access
    def reconcile(self) -> None:
        self._repository().reconcile()

    @repository_access
    def feed(self, item: str) -> str:
        return self._repository().feed(item)
