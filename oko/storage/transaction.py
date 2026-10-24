"""One synchronous write boundary shared by repositories on one connection."""
from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oko.storage.reader_versions import ReadingConnection


class TransactionAborted(RuntimeError):
    pass


def begin_write(db: sqlite3.Connection) -> None:
    """Existing repositories join a caller's transaction without a nested BEGIN."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')


@contextmanager
def transaction(db: 'ReadingConnection') -> Iterator[None]:
    """One short SQL lock, independent of longer lifecycle cleanup waits."""
    with db.access_lock:
        with _transaction(db):
            yield


@contextmanager
def _transaction(db: 'ReadingConnection') -> Iterator[None]:
    if db.transaction_depth:
        raise RuntimeError('Pass the existing transaction to nested services')
    if db.in_transaction:
        raise RuntimeError('Unowned transaction at action entry')
    db.execute('BEGIN IMMEDIATE')
    db.transaction_depth = 1
    db.transaction_failed = False
    try:
        yield
        if db.transaction_failed:
            raise TransactionAborted('A repository aborted this transaction')
    except BaseException:
        db.transaction_depth = 0
        db.rollback()
        raise
    else:
        db.transaction_depth = 0
        db.commit()
    finally:
        db.transaction_depth = 0
        db.transaction_failed = False
