"""Job mutations and status freshness share the caller's transaction."""
from collections.abc import Mapping, Sequence
from typing import Any

from oko.storage.database import Cursor, Database
from oko.storage.reader_status import changed as status_changed


def execute(db: Database, query: str, parameters: Sequence[Any] | Mapping[str, Any] = ()) -> Cursor:
    cursor = db.execute(query, parameters)
    if cursor.rowcount:
        status_changed(db)
    return cursor
