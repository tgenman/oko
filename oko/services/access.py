"""Synchronize non-suspending access to repositories without owning a transaction."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from functools import wraps
from typing import Any, Concatenate, ParamSpec, Protocol, TypeVar


class _AccessOwner(Protocol):
    _access: Callable[[], AbstractContextManager[Any]]


Owner = TypeVar('Owner', bound=_AccessOwner)
P = ParamSpec('P')
T = TypeVar('T')


def repository_access(call: Callable[Concatenate[Owner, P], T]) -> Callable[Concatenate[Owner, P], T]:
    @wraps(call)
    def protected(owner: Owner, /, *args: P.args, **kwargs: P.kwargs) -> T:
        with owner._access():
            return call(owner, *args, **kwargs)
    return protected
