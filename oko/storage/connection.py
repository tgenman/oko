"""SQLite connection lifecycle: ownership, commit hooks and rollback callbacks.

Reader projections register their maintenance hook; the connection owns no
reader SQL. PostgreSQL can supply the same lifecycle without importing projections.
"""
import sqlite3
from _thread import RLock
from collections.abc import Callable
from typing import Any, Literal


class ReadingConnection(sqlite3.Connection):
    """Flush derived list versions before the transaction becomes visible.

    Store uses implicit transactions, commit and connection contexts. SQL COMMIT
    and autocommitting DML scripts are not write interfaces of this connection.
    close retains SQLite's rollback semantics.
    """
    membership_feeds: set[str]
    membership_folders: set[str]
    transaction_depth: int = 0
    transaction_failed: bool = False
    commit_callbacks: list[Callable[[], None]] | None = None
    _access_lock: RLock | None = None

    @property
    def access_lock(self) -> RLock:
        if self._access_lock is None:
            self._access_lock = RLock()
        return self._access_lock

    def __enter__(self) -> 'ReadingConnection':
        self.access_lock.acquire()
        return self

    def after_commit(self, callback: Callable[[], None]) -> None:
        if self.transaction_depth:
            if self.commit_callbacks is None:
                self.commit_callbacks = []
            self.commit_callbacks.append(callback)
        else:
            callback()

    before_commit: Callable[[], None] | None = None

    def flush(self) -> None:
        if self.in_transaction and self.before_commit is not None:
            self.before_commit()

    def commit(self) -> None:
        if self.transaction_depth:
            return
        try:
            self.flush()
            super().commit()
        except BaseException:
            self.rollback()
            raise
        callbacks, self.commit_callbacks = self.commit_callbacks or [], None
        for callback in callbacks:
            callback()

    def rollback(self) -> None:
        if self.transaction_depth:
            self.transaction_failed = True
            return
        super().rollback()
        self.commit_callbacks = None
        self.membership_feeds = set()
        self.membership_folders = set()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Literal[False]:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.access_lock.release()
        return False

    def executescript(self, sql_script: str, /) -> sqlite3.Cursor:
        if self.transaction_depth:
            raise RuntimeError('Schema scripts cannot join an action transaction')
        # CPython's executescript commits through C, bypassing commit overrides.
        # Production scripts are schema/trigger setup, not data-write batches.
        self.commit()
        return super().executescript(sql_script)
