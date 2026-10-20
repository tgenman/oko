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


_clock: ContextVar[tuple[float, float, float] | None] = ContextVar('reader_clock', default=None)


@contextmanager
def stable_reader_time(offset: float, now: float, wall: float) -> Iterator[None]:
    token = _clock.set((offset, now, wall))
    try:
        yield
    finally:
        _clock.reset(token)


def retry_time(deadline: float, now: float, wall: float) -> float | None:
    stable = _clock.get()
    if stable is not None:
        return stable[0] + deadline if deadline > stable[1] else None
    return wall + max(0, deadline - now)


def reader_now(fallback: float) -> float:
    stable = _clock.get()
    return stable[1] if stable is not None else fallback


def reader_wall(fallback: float) -> float:
    stable = _clock.get()
    return stable[2] if stable is not None else fallback
