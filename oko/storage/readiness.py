"""Bounded PostgreSQL startup; failure exits the CLI so Docker can restart it."""
import time

import psycopg

from oko.storage.postgres_migrations import verify
from oko.storage.postgres_schema import MIGRATIONS


def wait_database(database_url: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            with psycopg.connect(database_url, autocommit=True, connect_timeout=3) as db:
                verify(db, MIGRATIONS)
            return
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise SystemExit('PostgreSQL is unavailable; refusing to start the reader') from None
            time.sleep(1)
