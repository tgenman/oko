"""Persistent command outcomes and their references to reader entities."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from oko.domain.errors import ActionError
from oko.services.access import repository_access
from oko.storage.reader_facts import ReaderFacts


class CommandRecords(Protocol):
    def action_started(self, identity: str, context: dict[str, Any], now: float, *, restart: bool = False, expected_identity: str | None = None) -> None: ...
    def command_error(self, identity: str, error_json: str) -> None: ...
    def action_finished(self, identity: str, succeeded: bool, now: float) -> None: ...
    def report_action(self, identity: str, context: dict[str, Any], result: str, now: float) -> str: ...
    def abandon(self, identity: str, now: float, validate_context: Callable[[Any, str, Any], bool] | None = None) -> bool: ...


class CommandJournal:
    def __init__(self, *, access: Callable[[], AbstractContextManager[Any]], records: Callable[[], CommandRecords],
                 facts: ReaderFacts, clock: Callable[[], float]) -> None:
        self._access, self._records, self._facts, self._clock = access, records, facts, clock

    @repository_access
    def started(self, identity: str, context: dict[str, Any], restart: bool, expected_identity: str | None) -> None:
        self._records().action_started(identity, context, self._clock(), restart=restart, expected_identity=expected_identity)

    @repository_access
    def failed(self, identity: str, error_json: str) -> None:
        self._records().command_error(identity, error_json)

    @repository_access
    def finished(self, identity: str, succeeded: bool) -> None:
        self._records().action_finished(identity, succeeded, self._clock())

    @repository_access
    def report(self, identity: str, context: dict[str, Any], result: str) -> str:
        if any(obj['kind'] == 'material' and self._facts.was_viewed(obj['id']) for obj in context['objects']):
            context, result = dict(context, objects=[]), 'abandoned'
        return self._records().report_action(identity, context, result, self._clock())

    @repository_access
    def abandon(self, identity: str, validate_context: Callable[[Any,str,Any],bool] | None = None) -> None:
        if not self._records().abandon(identity, self._clock(), validate_context):
            raise ActionError('invalid', 'Отмена доступна только ошибке соответствующего черновика', expected=True)
