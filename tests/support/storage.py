"""Offline fixture connections retain Store's transactional reader functions."""
from contextlib import contextmanager
import sqlite3
import time
from oko.storage.reader_versions import ReaderVersions


@contextmanager
def database(root):
    db=sqlite3.connect(root/'state.sqlite')
    ReaderVersions.functions(db,time.monotonic,time.time)
    try:
        with db:
            yield db
    finally:
        db.close()
