"""Persistent command outcomes and their references to reader entities."""

from oko.storage.access import repository_access
from collections.abc import Callable
from typing import Any
from oko.domain.errors import ActionError
from oko.storage.store import Store
from oko.storage.reader_facts import ReaderFacts

class CommandJournal:
    def __init__(self, repository: Callable[[], Store], clock: Callable[[], float]) -> None:
        self._storage, self._clock = repository, clock

    @repository_access
    def started(self, identity: str, context: dict[str, Any], restart: bool, expected_identity: str | None) -> None:
        self._storage().problems.action_started(identity, context, self._clock(), restart=restart, expected_identity=expected_identity)

    @repository_access
    def failed(self, identity: str, error_json: str) -> None:
        self._storage().problems.command_error(identity, error_json)

    @repository_access
    def finished(self, identity: str, succeeded: bool) -> None:
        self._storage().problems.action_finished(identity, succeeded, self._clock())

    @repository_access
    def report(self, identity: str, context: dict[str, Any], result: str) -> str:
        facts = ReaderFacts(self._storage)
        if any(obj['kind'] == 'material' and facts.was_viewed(obj['id']) for obj in context['objects']):
            context, result = dict(context, objects=[]), 'abandoned'
        return self._storage().problems.report_action(identity, context, result, self._clock())

    @repository_access
    def abandon(self, identity: str, validate_context: Callable[[Any,str,Any],bool] | None = None) -> None:
        if not self._storage().problems.abandon(identity, self._clock(), validate_context):
            raise ActionError('invalid', 'Отмена доступна только ошибке соответствующего черновика', expected=True)
