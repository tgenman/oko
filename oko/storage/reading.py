"""Reuse saved facts only during one synchronous reader projection.

No values survive a projection or an action. The caller must not mutate storage
or suspend while a snapshot is active; it is not a multi-request transaction.
"""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
from pathlib import Path
from typing import Any, ParamSpec, TypeVar, cast


P = ParamSpec('P')
R = TypeVar('R')
_values: ContextVar[dict[tuple[Any, ...], Any] | None] = ContextVar('reading_values', default=None)


@contextmanager
def reading_snapshot() -> Iterator[None]:
    if _values.get() is not None:
        yield
        return
    token = _values.set({})
    try:
        yield
    finally:
        _values.reset(token)


def reading_value(read: Callable[P, R]) -> Callable[P, R]:
    """Memoize an unmodified fact inside an explicitly owned reading snapshot."""
    @wraps(read)
    def value(*args: P.args, **kwargs: P.kwargs) -> R:
        saved = _values.get()
        if saved is None:
            return read(*args, **kwargs)
        key = (read, *args, *sorted(kwargs.items()))
        if key not in saved:
            saved[key] = read(*args, **kwargs)
        return cast(R, saved[key])
    return value


@reading_value
def saved_bytes(path: 'Path') -> bytes:
    return path.read_bytes()


@reading_value
def saved_document(path: 'Path') -> tuple[dict[str, Any], str]:
    header, text = saved_bytes(path).decode('utf-8').split('\n\n', 1)
    return json.loads(header), text
