"""Reuse saved facts only during one synchronous read-only calculation.

No values survive the calculation. The caller must not mutate storage
or suspend while a snapshot is active; it is not a multi-request transaction.
"""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from copy import deepcopy
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
def _decoded_content(serialized: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(serialized))


def saved_content(serialized: str, *, mutable: bool = False) -> dict[str, Any]:
    """Borrow decoded publication facts, or request an owned mutable copy.

    Facts live only in the current synchronous snapshot. Without a snapshot the
    decoder already returns a fresh owned value: copying it would penalize writes
    and background processing. Read projections must not mutate borrowed values.
    """
    value = _decoded_content(serialized)
    return deepcopy(value) if mutable and _values.get() is not None else value


@reading_value
def saved_bytes(path: 'Path') -> bytes:
    return path.read_bytes()


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
