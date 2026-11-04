"""Resources and commit checks owned by one synchronous transaction."""
from collections.abc import Callable
from typing import Any


class TransactionContext:
    def __init__(self) -> None:
        self.resources: dict[object, Any] = {}
        self._checks: list[Callable[[], None]] = []

    def before_commit(self, check: Callable[[], None]) -> None:
        self._checks.append(check)

    def validate(self) -> None:
        for check in self._checks:
            check()

    def clear(self) -> None:
        self._checks.clear()
        self.resources.clear()
