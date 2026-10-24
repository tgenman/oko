"""Offline preparation of a separately validated store. Never switches an instance."""
from contextlib import contextmanager
from collections.abc import Iterator
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any


class MigrationRefused(ValueError):
    pass


def quote(identifier: str) -> str:
    return '"'+identifier.replace('"','""')+'"'


def group_guard(db: sqlite3.Connection) -> dict[str, Any]:
    """Fail closed for unknown Telegram identity, including empty bindings."""
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conflicts=[]
    if 'feeds' not in tables or 'items' not in tables or 'state' not in tables:
        raise MigrationRefused('Unsupported source schema; no source data were changed')
    columns={row[1] for row in db.execute('PRAGMA table_info(feeds)')}
    if not {'config','kind','id'}<=columns:
        raise MigrationRefused('Source schema has no proven Telegram channel type')
    for row in db.execute("SELECT id,config FROM feeds WHERE kind='telegram'"):
        try:
            kind=json.loads(row[1]).get('source_kind')
        except (TypeError,ValueError):
            kind=None
        if kind!='channel':
            conflicts.append(dict(kind='telegram_type',feed=row[0],type=kind or 'unknown'))
    if 'telegram_bindings' in tables:
        for row in db.execute('SELECT feed,binding FROM telegram_bindings'):
            try:
                binding=json.loads(row[1])
            except (TypeError,ValueError):
                binding={}
            if binding.get('kind') not in ('web','client'):
                conflicts.append(dict(kind='telegram_binding',feed=row[0]))
            if db.execute("SELECT 1 FROM feeds WHERE id=? AND kind='telegram' AND json_extract(config,'$.source_kind')='channel'",(row[0],)).fetchone() is None:
                conflicts.append(dict(kind='unproven_binding_owner',feed=row[0]))
    # Historical group/topic tables count even without a surviving feed or messages.
    for table in sorted(tables):
        if any(part in table.lower() for part in ('topic','group','chat')) and db.execute('SELECT 1 FROM '+quote(table)+' LIMIT 1').fetchone():
            conflicts.append(dict(kind='legacy_group_data',table=table))
    for table,column in (('jobs','kind'),('settings','scope')):
        if table in tables:
            for row in db.execute('SELECT DISTINCT '+quote(column)+' FROM '+quote(table)):
                if row[0] and any(part in row[0].lower() for part in ('group','topic','chat')):
                    conflicts.append(dict(kind='legacy_group_reference',table=table,value=row[0]))
    if conflicts:
        raise MigrationRefused(json.dumps(dict(reason='group_or_unknown_data',conflicts=conflicts),ensure_ascii=False))
    return dict(telegram_types='channels_only',group_data='absent',checked_tables=sorted(tables))


@contextmanager
def offline_owner(source: Path) -> Iterator[None]:
    # Opening an existing lock read-only does not create or alter source files.
    if (source/'instance.lock').is_symlink():
        raise MigrationRefused('Instance lock must not be a symlink')
    try:
        lock=(source/'instance.lock').open('rb')
    except FileNotFoundError:
        raise MigrationRefused('No instance.lock: offline ownership cannot be established') from None
    with lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise MigrationRefused('Source instance is still running') from None
        try:
            yield
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def file_hash(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str,str]:
    result={}
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if path.is_symlink():
            raise MigrationRefused('Symlink in source: '+str(relative))
        if path.is_file() and relative.parts[0] not in ('staging','instance.lock') and path.name not in ('state.sqlite','state.sqlite-wal','state.sqlite-shm','state.sqlite-journal'):
            result[str(relative)]=file_hash(path)
    return result


def table_digest(db: sqlite3.Connection, table: str) -> str:
    columns=[row[1] for row in db.execute('PRAGMA table_info('+quote(table)+')')]
    if table=='telegram_profiles':columns=[name for name in columns if name not in ('state','message')]
    rows=sorted(json.dumps(list(row),ensure_ascii=False,sort_keys=True,default=repr) for row in db.execute('SELECT '+','.join(quote(name) for name in columns)+' FROM '+quote(table)))
    return hashlib.sha256('\n'.join(rows).encode()).hexdigest()


def prepare(source: Path, destination: Path) -> dict[str, Any]:
    source,destination=source.resolve(),destination.resolve()
    if source==destination or source in destination.parents or destination in source.parents or destination.exists():
        raise MigrationRefused('Destination must be a new separate directory')
    with offline_owner(source):
        with sqlite3.connect((source/'state.sqlite').as_uri()+'?mode=ro',uri=True) as original:
            original.row_factory=sqlite3.Row
            if original.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise MigrationRefused('Source integrity check failed')
            guard=group_guard(original)
            marker=original.execute("SELECT value FROM state WHERE key='publication-pipeline'").fetchone()
            if marker is None or marker[0]!='complete':
                raise MigrationRefused('Pre-Publication schema requires the previous verified format converter')
            files=inventory(source)
            for row in original.execute("SELECT id,storage_key FROM media_files WHERE status='ready'"):
                if row['storage_key'] not in files:
                    raise MigrationRefused('Ready media file missing or unsafe: '+row['id'])
            source_digest=file_hash(source/'state.sqlite')
            tables=guard['checked_tables']
            counts={table:original.execute('SELECT count(*) FROM '+quote(table)).fetchone()[0] for table in tables}
            setting_tables=[table for table in ('settings','folders','telegram_profiles','telegram_bindings','initial_history') if table in tables]
            settings={table:table_digest(original,table) for table in setting_tables}
            identities={table:[row[0] for row in original.execute('SELECT id FROM '+quote(table)+' ORDER BY id')] for table in ('feeds','items','media_files')}
            # Repeat after exclusive offline ownership and immediately before copying.
            group_guard(original)
            destination.mkdir(mode=0o700,parents=True)
            try:
                with sqlite3.connect(destination/'state.sqlite') as target:
                    original.backup(target)
                for relative in files:
                    path=destination/relative
                    path.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source/relative,path)
                (destination/'instance.lock').touch(mode=0o600)
                from oko.storage.store import Store
                target_store=Store(destination,lambda _:None)
                try:
                    integrity=target_store.db.execute('PRAGMA quick_check').fetchone()[0]
                    foreign_keys=[list(row) for row in target_store.db.execute('PRAGMA foreign_key_check')]
                    if integrity!='ok' or foreign_keys:
                        raise MigrationRefused('Destination integrity/relations check failed')
                    target_ids={table:[row[0] for row in target_store.db.execute('SELECT id FROM '+quote(table)+' ORDER BY id')] for table in identities}
                    if target_ids!=identities:
                        raise MigrationRefused('Identity mapping changed during preparation')
                    preserved_settings={table:table_digest(target_store.db,table) for table in setting_tables}
                    if preserved_settings!=settings:
                        raise MigrationRefused('Settings changed during preparation')
                    results=[dict(row) for row in target_store.db.execute("SELECT id,status,filtered_content,summarized_content,processing_metadata FROM items WHERE viewed=0")]
                    recalculate=[row['id'] for row in results if row['status'] in ('pending_filtering','pending_summary')]
                finally:
                    target_store.db.close()
                if inventory(source)!=files or file_hash(source/'state.sqlite')!=source_digest:
                    raise MigrationRefused('Source changed during copy')
                if any(file_hash(destination/name)!=digest for name,digest in files.items()):
                    raise MigrationRefused('Copied file verification failed')
                report=dict(source=str(source),destination=str(destination),verified=True,switched=False,group_guard=guard,
                    source_counts=counts,ids={table:{identity:identity for identity in values} for table,values in identities.items()},
                    files=files,recalculate=recalculate,conflicts=[],source_unchanged=True,
                    settings=dict(tables=settings,verified=preserved_settings==settings,profile_lifecycle='Profiles require verification after startup; durable fields preserved'),results={row['id']:dict(filter=bool(json.loads(row['filtered_content'])),summary=bool(json.loads(row['summarized_content']))) for row in results},history='Source retained; no cleanup performed')
                (destination/'migration-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                return report
            except BaseException:
                # Only the directory created by this attempt is removed.
                shutil.rmtree(destination)
                raise


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    try:
        report=prepare(args.source,args.destination)
    except (ValueError,OSError,sqlite3.Error) as error:
        print(json.dumps(dict(verified=False,error=str(error)),ensure_ascii=False))
        return 1
    print(json.dumps(dict(verified=report['verified'],switched=False,report=str(args.destination/'migration-report.json')),ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
