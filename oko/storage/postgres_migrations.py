"""Atomic PostgreSQL schema releases, executed by a separate migration identity.

The reader only verifies the ledger. Migration callers supply an autocommit
connection; one session advisory lock excludes concurrent release migrations.
Migration SQL is trusted release code and must not contain transaction control.
"""
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus


_LOCK = 0x4F4B4F534348454D


class SchemaMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode('utf-8')).hexdigest()


def _validate(migrations: Sequence[Migration]) -> None:
    if [migration.version for migration in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError('Schema migrations must be consecutive, starting at 1')
    if any(not migration.name or not migration.sql.strip() for migration in migrations):
        raise ValueError('Schema migrations need a name and SQL')


def _installed(db: psycopg.Connection[Any], migrations: Sequence[Migration]) -> int:
    rows = db.execute('SELECT version,name,checksum FROM oko_schema_migrations ORDER BY version').fetchall()
    if len(rows) > len(migrations):
        raise SchemaMismatch('Database schema is newer than this release')
    for row, expected in zip(rows, migrations):
        if tuple(row) != (expected.version, expected.name, expected.checksum):
            raise SchemaMismatch('Database migration history does not match this release')
    return len(rows)


def schema_lock_key(db: psycopg.Connection[Any]) -> int:
    row = db.execute('SELECT hashtextextended(current_schema(),%s)', (_LOCK,)).fetchone()
    if row is None or row[0] is None:
        raise SchemaMismatch('Database search_path must name a schema')
    return int(row[0])


def migrate(db: psycopg.Connection[Any], migrations: Sequence[Migration]) -> None:
    """Apply the entire pending release or roll it back, including its ledger."""
    _validate(migrations)
    if not db.autocommit or db.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Migrations require an idle autocommit connection')
    key = schema_lock_key(db)
    locked = db.execute('SELECT pg_try_advisory_lock(%s)', (key,)).fetchone()
    if not locked or not locked[0]:
        raise RuntimeError('Schema is busy: another reader or migration is running')
    try:
        with db.transaction():
            db.execute('''CREATE TABLE IF NOT EXISTS oko_schema_migrations (
                version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT clock_timestamp())''')
            installed = _installed(db, migrations)
            for migration in migrations[installed:]:
                db.execute(migration.sql, prepare=False)
                db.execute('INSERT INTO oko_schema_migrations(version,name,checksum) VALUES (%s,%s,%s)',
                           (migration.version, migration.name, migration.checksum))
    finally:
        db.execute('SELECT pg_advisory_unlock(%s)', (key,))


def verify(db: psycopg.Connection[Any], migrations: Sequence[Migration]) -> None:
    """Check compatibility without creating or changing any schema object."""
    _validate(migrations)
    schema = db.execute("SELECT to_regclass('oko_schema_migrations')").fetchone()
    if schema is None or schema[0] is None:
        raise SchemaMismatch('Database has not been migrated')
    if _installed(db, migrations) != len(migrations):
        raise SchemaMismatch('Database needs migration before starting this release')


def main() -> None:
    import argparse
    from pathlib import Path
    from oko.storage.credentials import private_database_url
    from oko.storage.postgres_schema import MIGRATIONS

    parser = argparse.ArgumentParser(description='Oko PostgreSQL release schema')
    parser.add_argument('--database-url-file', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Verify only; never execute DDL')
    args = parser.parse_args()
    dsn = private_database_url(args.database_url_file)
    with psycopg.connect(dsn, autocommit=True) as db:
        verify(db, MIGRATIONS) if args.check else migrate(db, MIGRATIONS)
    print('PostgreSQL schema verified: version ' + str(len(MIGRATIONS)))


if __name__ == '__main__':
    main()
