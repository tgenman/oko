"""Offline, explicit SQLite library import into an empty migrated PostgreSQL schema."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
from typing import Any

import psycopg
from psycopg import sql

from oko.storage.credentials import private_database_url
from oko.storage.migration import MigrationRefused, file_hash, offline_owner, quote
from oko.storage.postgres_migrations import schema_lock_key, verify
from oko.storage.postgres_schema import MIGRATIONS


def files(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise MigrationRefused('Source contains a symlink or special file')
        relative = path.relative_to(root)
        if stat.S_ISREG(mode) and str(relative) not in (
            'instance.lock', 'runtime.json', 'runtime.tmp', 'state.sqlite',
            'state.sqlite-wal', 'state.sqlite-shm', 'state.sqlite-journal',
        ):
            result[str(relative)] = file_hash(path)
    return result


def digest(rows: list[tuple[Any, ...]]) -> str:
    def encode(value: Any) -> Any:
        if isinstance(value, (bytes, memoryview)):
            return {'bytes': bytes(value).hex()}
        raise TypeError('Unsupported stored value')
    encoded = sorted(json.dumps(tuple(row), ensure_ascii=False, default=encode,
                                separators=(',', ':')) for row in rows)
    return hashlib.sha256('\n'.join(encoded).encode()).hexdigest()


def empty_target(db: psycopg.Connection[Any], tables: list[str]) -> None:
    for table in tables:
        rows = db.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
        if table == 'state':
            if dict(rows) == {'publication-pipeline': 'complete', 'work-enabled': 'false', 'journal-index-version': '1'}:
                continue
        elif table == 'youtube_access':
            if rows == [(1, 0.0, 0.0, 0)]:
                continue
        elif not rows:
            continue
        raise MigrationRefused('Target PostgreSQL schema is not an empty migrated library')


def import_library(source: Path, target: Path, credential: Path, report: Path) -> dict[str, Any]:
    if source.is_symlink() or target.is_symlink():
        raise MigrationRefused('Use explicit physical source and target directories')
    source, target, report = source.resolve(), target.resolve(), report.absolute()
    if source == target or source in target.parents or target in source.parents or target.exists():
        raise MigrationRefused('Target data must be a new separate directory')
    if report.exists() or report.is_symlink() or source == report or source in report.parents or target in report.parents:
        raise MigrationRefused('Report must be a new file outside source and target')
    if credential.is_symlink():
        raise MigrationRefused('Database credential must not be a symlink')
    dsn = private_database_url(credential)
    stage: Path | None = None
    published = False
    committed = False
    try:
        with offline_owner(source), closing(sqlite3.connect((source/'state.sqlite').as_uri()+'?mode=ro', uri=True)) as original:
            if original.execute('PRAGMA quick_check').fetchone() != ('ok',) or original.execute('PRAGMA foreign_key_check').fetchone():
                raise MigrationRefused('Source integrity check failed')
            marker = original.execute("SELECT value FROM state WHERE key='publication-pipeline'").fetchone()
            if marker != ('complete',):
                raise MigrationRefused('Source is not the current publication library format')
            source_hash, inventory = file_hash(source/'state.sqlite'), files(source)
            for key, in original.execute("SELECT storage_key FROM media_files WHERE status='ready'"):
                if key not in inventory:
                    raise MigrationRefused('Source has missing or unsafe ready media')
            source_tables = {row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            with psycopg.connect(dsn, autocommit=True) as db:
                verify(db, MIGRATIONS)
                locked = db.execute('SELECT pg_try_advisory_lock(%s)', (schema_lock_key(db),)).fetchone()
                if not locked or not locked[0]:
                    raise MigrationRefused('Target schema is busy: stop its reader before import')
                verify(db, MIGRATIONS)
                tables = [r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename!='oko_schema_migrations' ORDER BY tablename")]
                unknown = source_tables - set(tables) - {'migration_retired_rows'}
                missing = set(tables) - source_tables - {'reader_membership_dirty'}
                if unknown or missing:
                    raise MigrationRefused('Source/target table sets differ; no data imported')
                with db.transaction():
                    db.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE NOWAIT').format(sql.SQL(',').join(map(sql.Identifier, tables))))
                    empty_target(db, tables)
                    stage = Path(tempfile.mkdtemp(prefix='.oko-import-', dir=target.parent))
                    stage.chmod(0o700)
                    for relative, expected in inventory.items():
                        destination = stage / relative
                        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        shutil.copyfile(source/relative, destination)
                        destination.chmod(0o600)
                        if file_hash(destination) != expected:
                            raise MigrationRefused('Copied file checksum mismatch')
                    archive = stage/'migration-archive'
                    if archive.exists():
                        raise MigrationRefused('Source already has an import archive; reconcile it explicitly')
                    archive.mkdir(mode=0o700)
                    with closing(sqlite3.connect(archive/'state.sqlite')) as backup:
                        original.backup(backup)
                    (archive/'state.sqlite').chmod(0o600)
                    for table in tables:
                        db.execute(sql.SQL('ALTER TABLE {} DISABLE TRIGGER USER').format(sql.Identifier(table)))
                    db.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY').format(sql.SQL(',').join(map(sql.Identifier, tables))))
                    receipts = {}
                    for table in sorted(source_tables & set(tables), key=lambda t: (t not in ('feeds', 'items'), t)):
                        source_columns = [r[1] for r in original.execute('PRAGMA table_info('+quote(table)+')')]
                        columns = [r[0] for r in db.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position', (table,))]
                        if set(source_columns) != set(columns) - ({'rowid'} if 'rowid' not in source_columns else set()):
                            raise MigrationRefused('Source/target columns differ for table '+table)
                        rows = [tuple(row) for row in original.execute('SELECT '+','.join(quote(c) for c in columns)+' FROM '+quote(table))]
                        if table == 'state':
                            rows = [(key, 'false' if key == 'work-enabled' else value) for key, value in rows]
                            if not any(key == 'work-enabled' for key, _ in rows):
                                rows.append(('work-enabled', 'false'))
                        with db.cursor() as cursor:
                            with cursor.copy(sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)))) as copy:
                                for row in rows:
                                    copy.write_row(row)
                        imported = db.execute(sql.SQL('SELECT {} FROM {}').format(sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table))).fetchall()
                        expected_digest = digest(rows)
                        if digest(imported) != expected_digest:
                            raise MigrationRefused('Imported table checksum mismatch for '+table)
                        receipts[table] = {'rows': len(rows), 'sha256': expected_digest}
                        for column in columns:
                            sequence = db.execute('SELECT pg_get_serial_sequence(%s,%s)', (table, column)).fetchone()
                            if sequence and sequence[0]:
                                maximum = db.execute(sql.SQL('SELECT max({}) FROM {}').format(sql.Identifier(column), sql.Identifier(table))).fetchone()
                                next_value = max(1, int(maximum[0] or 0)+1) if maximum else 1
                                db.execute(sql.SQL('ALTER SEQUENCE {} RESTART WITH {}').format(sql.Identifier(*sequence[0].split('.')), sql.Literal(next_value)))
                    for table in tables:
                        db.execute(sql.SQL('ALTER TABLE {} ENABLE TRIGGER USER').format(sql.Identifier(table)))
                    if file_hash(source/'state.sqlite') != source_hash or files(source) != inventory:
                        raise MigrationRefused('Source changed while importing')
                    receipt = {'version': 1, 'state': 'prepared', 'schema_version': len(MIGRATIONS), 'work_enabled': False,
                               'source_database_sha256': source_hash, 'tables': receipts,
                               'files': inventory, 'archived_tables': {table: original.execute('SELECT count(*) FROM '+quote(table)).fetchone()[0] for table in sorted(source_tables-set(tables))},
                               'archive_database_sha256': file_hash(archive/'state.sqlite')}
                    (stage/'instance.lock').touch(mode=0o600)
                    (stage/'migration-import.json').write_text(json.dumps(receipt, indent=2)+'\n')
                    (stage/'migration-import.json').chmod(0o600)
                    # The offline target is published before commit; an exception
                    # rolls the transaction back and removes only our new directory.
                    stage.rename(target)
                    stage = None
                    published = True
                committed = True
                receipt['state'] = 'committed'
                receipt_path = target/'migration-import.json'
                receipt_path.write_text(json.dumps(receipt, indent=2)+'\n')
                report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with os.fdopen(os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                    stream.write(json.dumps(receipt, indent=2)+'\n')
                return receipt
    except BaseException:
        if stage is not None:
            shutil.rmtree(stage)
        # Once committed, target/migration-import.json is the recovery receipt;
        # a failure writing the optional external report must not remove data.
        if published and not committed:
            shutil.rmtree(target)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target-data', type=Path, required=True)
    parser.add_argument('--database-url-file', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = import_library(args.source, args.target_data, args.database_url_file, args.report)
    except (ValueError, OSError, sqlite3.Error, psycopg.Error, RuntimeError) as error:
        # Database/OS exceptions may contain credentials or stored material text.
        raise SystemExit('Import refused or failed ('+type(error).__name__+'); inspect private state before retrying') from None
    print(json.dumps({'imported': True, 'schema_version': receipt['schema_version'], 'work_enabled': False,
                      'tables': len(receipt['tables']), 'files': len(receipt['files'])}))


if __name__ == '__main__':
    main()
