"""Admission shared by application commands and background execution."""
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar
from typing import ParamSpec
from functools import wraps
from oko.storage.store import Store
from oko.diagnostics import Journal
from oko.domain.errors import ActionError as ActionError

T = TypeVar('T')
P = ParamSpec('P')


def read_locked(execution: 'Execution') -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Protect a non-suspending HTTP projection with the connection's SQL lock."""
    def decorate(call: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(call)
        async def read(*args: P.args, **kwargs: P.kwargs) -> T:
            store = execution._store
            if store is None:
                return await call(*args, **kwargs)
            # The decorated projection has no await. Lifecycle cleanup may be
            # waiting with the action gate held, but no SQL transaction open.
            with store.db.access_lock:
                return await call(*args, **kwargs)
        return read
    return decorate


class Execution:
    def __init__(self, admitted: Callable[[], bool]) -> None:
        self._admitted = admitted
        self._actions = asyncio.Lock()
        self._stop_requested = asyncio.Event()
        self._store: Store | None = None
        self._error: str | None = None
        self._epoch = 0
        self._requests: set[asyncio.Future[Any]] = set()
        self.enabled = True
        self.diagnostics: Journal | None = None
        self.reader_finish: Callable[[], Awaitable[None]] | None = None
        self.reader_advance: Callable[[], None] | None = None

    @property
    def stopped(self) -> bool:
        return self._stop_requested.is_set()


    def _live(self) -> bool:
        return not self.stopped and self.enabled and self._admitted()


    def require_store(self) -> Store:
        """Existing read interface; callers do not own its lifetime."""
        if self.stopped:
            raise ActionError('conflict', 'Око остановлено')
        if self._error or self._store is None:
            raise ActionError('unavailable', self._error or 'Хранилище не готово')
        return self._store

    def retained_store(self) -> Store:
        """Only shutdown projection may read after admission has closed."""
        if self._store is None:
            raise ActionError('unavailable', 'Хранилище закрыто')
        return self._store


    def require_work(self) -> None:
        self.require_store()
        if not self.enabled:
            raise ActionError('conflict', 'Око выключено; сначала включите Око', expected=True, code='runtime_disabled')


class _ExternalCheck:
    """One owned external wait and its acceptance through shared admission.

    Start is called under the action lock. Waiting releases it; accept reacquires
    it at the scenario's existing commit points, including failures. Snapshots
    preserve each scenario's synchronous preparation point.
    """
    def __init__(self, runtime: 'Execution', store: Store, epoch: int | None = None) -> None:
        self._runtime = runtime
        self._store = store
        self._epoch = runtime._epoch if epoch is None else epoch

    def start(self, call: Awaitable[T]) -> Awaitable[T]:
        task = asyncio.ensure_future(call)
        self._runtime._requests.add(task)

        async def wait() -> T:
            try:
                return await task
            finally:
                self._runtime._requests.discard(task)

        return wait()

    def follow(self, later: '_ExternalCheck') -> '_ExternalCheck':
        # Telegram's multi-step connection keeps its original Store, but the
        # final info call supplies the acceptance generation. Do not strengthen
        # this to a single generation spanning the whole connection.
        return _ExternalCheck(self._runtime, self._store, later._epoch)

    @asynccontextmanager
    async def accept(self, *, require_work: bool = False, failure: ActionError | None = None) -> AsyncIterator[Store]:
        runtime = self._runtime
        async with runtime._actions:
            if require_work:
                runtime.require_work()
            if failure is not None:
                # A failed identity check may mark a profile unavailable only
                # in its original context; interruption rethrows the same error.
                if failure.diagnostic_skip or runtime._store is not self._store or not runtime._live() or self._epoch != runtime._epoch:
                    raise failure
            else:
                if runtime.stopped or runtime._store is not self._store or not runtime._live():
                    raise ActionError('conflict', 'Око остановлено', expected=True)
                if self._epoch != runtime._epoch:
                    raise ActionError('conflict', 'Проверка подключения прервана; повторите действие', expected=True)
            yield self._store
