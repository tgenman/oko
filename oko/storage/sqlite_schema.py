"""Legacy SQLite installation is retained only for the local SQLite backend."""
import sqlite3
from typing import TypeGuard

from oko.storage.database import Database


def is_sqlite(db: Database) -> TypeGuard[sqlite3.Connection]:
    return isinstance(db, sqlite3.Connection)
