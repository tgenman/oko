"""One connection mutex for non-suspending service operations."""
from collections.abc import Callable
from oko.storage.store import Store
from typing import Concatenate, ParamSpec, Protocol, TypeVar
from functools import wraps

class _StorageOwner(Protocol):
    _storage: Callable[[], Store]

_Q = TypeVar('_Q', bound=_StorageOwner)
_P = ParamSpec('_P')
_T = TypeVar('_T')


def repository_access(call: Callable[Concatenate[_Q, _P], _T]) -> Callable[Concatenate[_Q, _P], _T]:
    @wraps(call)
    def read(owner: _Q, /, *args: _P.args, **kwargs: _P.kwargs) -> _T:
        with owner._storage().db.access_lock:
            return call(owner, *args, **kwargs)
    return read
