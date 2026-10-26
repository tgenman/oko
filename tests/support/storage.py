"""Synthetic PostgreSQL schemas owned by test data directories, never production."""
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
import atexit
import os
import threading
from uuid import uuid4
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from oko.storage.store import Store as ProductionStore
from oko.storage.postgres_connection import PostgresConnection
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from oko.storage.reader_versions import ReaderVersions

_lock = threading.RLock()
_owned: list[tuple[str, str]] = []
_roots: dict[Path, str] = {}
_retained: set[str] = set()


def database_url(root: Path) -> str:
    """Reuse a scenario's schema across restart; allocate only on explicit test DSN."""
    root = Path(root)
    with _lock:
        path = root / '.test-database-url'
        if path.exists():
            return path.read_text().strip()
        base = os.environ.get('OKO_TEST_POSTGRES_URL')
        if not base:
            raise RuntimeError('Tests require OKO_TEST_POSTGRES_URL pointing to disposable PostgreSQL')
        schema = 'test_' + uuid4().hex
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        _owned.append((base, schema))
        _roots[root.resolve()] = schema
        dsn = make_conninfo(base, options='-csearch_path='+schema)
        with psycopg.connect(dsn, autocommit=True) as connection:
            migrate(connection, MIGRATIONS)
            # General reader scenarios explicitly exercise enabled synthetic
            # work. Production first-start policy is tested with postgres_url.
            connection.execute("UPDATE state SET value='true' WHERE key='work-enabled'")
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(dsn)
        path.chmod(0o600)
        return dsn


class Store(ProductionStore):
    def __init__(self, root: Path, *args: Any, database_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(root, *args, database_url=database_url or globals()['database_url'](root), **kwargs)


@contextmanager
def database(root: Path) -> Iterator[PostgresConnection]:
    db = PostgresConnection(database_url(root))
    db.before_commit = lambda: ReaderVersions.flush_reading_lists(db)
    try:
        with db:
            yield db
    finally:
        db.close()


def owned_schemas() -> set[str]:
    return {schema for _, schema in _owned}


def retain_database(root: Path) -> None:
    schema = _roots.get(root.resolve())
    if schema is not None:
        _retained.add(schema)


def release_database(root: Path) -> None:
    schema = _roots.pop(root.resolve(), None)
    if schema is None:
        return  # Explicitly supplied schemas belong to the supplying fixture.
    _retained.discard(schema)
    cleanup(created_after=owned_schemas() - {schema})


def analyze(db: Any) -> None:
    """Collect statistics only for this scenario, not every schema in the DB."""
    connection = getattr(db, '_db', db)
    schema = connection.execute('SELECT current_schema()').fetchone()[0]
    tables = connection.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s', (schema,)).fetchall()
    for (table,) in tables:
        connection.execute(sql.SQL('ANALYZE {}').format(sql.Identifier(schema, table)))


def cleanup(*, created_after: set[str] | None = None) -> None:
    for base, schema in list(reversed(_owned)):
        if created_after is not None and (schema in created_after or schema in _retained):
            continue
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        _owned.remove((base, schema))
        _retained.discard(schema)


def clone_database(source: Path, destination: Path) -> None:
    """Copy a closed synthetic seed into an independently owned test schema."""
    marker = source / '.test-database-url'
    if not marker.exists():
        return
    # File copying must never let two scenarios reuse the seed's connection.
    (destination / '.test-database-url').unlink(missing_ok=True)
    target_dsn = database_url(destination)
    with psycopg.connect(marker.read_text().strip(), autocommit=True) as original:
        source_row = original.execute('SELECT current_schema()').fetchone()
        assert source_row is not None
        source_schema = source_row[0]
    with psycopg.connect(target_dsn) as target:
        target_row = target.execute('SELECT current_schema()').fetchone()
        assert target_row is not None
        target_schema = target_row[0]
        tables = [r[0] for r in target.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename!='oko_schema_migrations'")]
        target.execute('SET LOCAL session_replication_role=replica')
        target.execute(sql.SQL('TRUNCATE {}').format(sql.SQL(',').join(sql.Identifier(target_schema, name) for name in tables)))
        for name in tables:
            target.execute(sql.SQL('INSERT INTO {} OVERRIDING SYSTEM VALUE SELECT * FROM {}').format(
                sql.Identifier(target_schema, name), sql.Identifier(source_schema, name)))
        for table, column in target.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema() AND is_identity='YES'").fetchall():
            sequence_row = target.execute('SELECT pg_get_serial_sequence(%s,%s)', (f'{target_schema}.{table}', column)).fetchone()
            maximum_row = target.execute(sql.SQL('SELECT max({}) FROM {}').format(sql.Identifier(column), sql.Identifier(target_schema, table))).fetchone()
            assert sequence_row is not None and maximum_row is not None
            sequence, maximum = sequence_row[0], maximum_row[0]
            target.execute('SELECT setval(%s,%s,%s)', (sequence, maximum or 1, maximum is not None))


atexit.register(cleanup)
