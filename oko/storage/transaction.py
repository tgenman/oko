"""One synchronous write boundary shared by repositories on one connection."""
from oko.storage.database import Database, Connection
from collections.abc import Iterator, Callable
from contextlib import contextmanager
from oko.storage.postgres_connection import PostgresConnection


class TransactionAborted(RuntimeError):
    pass


def begin_write(db: Database | PostgresConnection) -> None:
    """Existing repositories join a caller's transaction without a nested BEGIN."""
    if not db.in_transaction:
        db.begin()


@contextmanager
def transaction(db: Connection, *, admitted: Callable[[], bool] = lambda: True) -> Iterator[None]:
    """One short SQL lock, independent of longer lifecycle cleanup waits."""
    with db.access_lock:
        if not admitted():
            raise InterruptedError('Storage admission closed')
        with _transaction(db):
            yield


@contextmanager
def _transaction(db: Connection) -> Iterator[None]:
    if db.transaction_depth:
        raise RuntimeError('Pass the existing transaction to nested services')
    if db.in_transaction:
        raise RuntimeError('Unowned transaction at action entry')
    begin_write(db)
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
