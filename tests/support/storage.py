"""Offline fixture connections retain Store's transactional reader functions."""
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
import time
from oko.storage.reader_versions import ReaderVersions,ReadingConnection


@contextmanager
def database(root:Path) -> Iterator[ReadingConnection]:
    db=ReadingConnection(root/'state.sqlite')
    ReaderVersions.functions(db,time.monotonic,time.time)
    try:
        with db:
            yield db
    finally:
        db.close()
