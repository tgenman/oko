"""Offline migration to a new directory. Never switches a running installation.

Usage: python -m oko.storage.migration SOURCE NEW_DIRECTORY
The result contains an untouched verified backup, storage, and report.json.
"""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4
from typing import Any

from oko.storage.store import Store
from oko.reviews import publication_input
from oko.collection.adapters import AdapterException
from oko.collection.telegram.adapter import publication_reference


def manifest(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                result[str(path.relative_to(root))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def _integrity(root: Path) -> None:
    db = sqlite3.connect((root/'state.sqlite').as_uri()+'?mode=ro', uri=True)
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise sqlite3.DatabaseError('Migration source failed integrity_check')
    finally:
        db.close()


def migrate_publications(source: Path, destination: Path) -> dict[str, Any]:
    source, destination = source.resolve(), destination.resolve()
    if destination.exists() or source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Migration requires a new directory outside the source')
    if not (source/'state.sqlite').is_file():
        raise ValueError('Source storage does not contain state.sqlite')
    with ExitStack() as stack:
        # The installed entry point owns this same lock. Opening read-only does
        # not change source bytes. With no lock file, the caller must have stopped
        # embedded writers too; the manifest guard rejects a changing copy.
        if (source/'instance.lock').is_file():
            lock = stack.enter_context((source/'instance.lock').open('rb'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any((source/name).exists() for name in ('state.sqlite-wal', 'state.sqlite-journal')):
            raise ValueError('Stop the storage owner and recover its SQLite journal before migration')
        before = manifest(source)
        _integrity(source)
        work = Path(tempfile.mkdtemp(prefix=destination.name+'-', dir=destination.parent))
        report: dict[str, Any] = dict(success=False, source=str(source), feeds=[], publications=[], conflicts=[], unportable=[])
        try:
            shutil.copytree(source, work/'backup')
            if manifest(source) != before or manifest(work/'backup') != before:
                raise ValueError('Source changed while copying; migration rejected')
            _integrity(work/'backup')
            shutil.copytree(work/'backup', work/'storage')
            store = Store(work/'storage', lambda _: None)
            try:
                _convert(store, report)
                if not report['conflicts']:
                    with store.db:
                        store.db.execute("INSERT INTO state VALUES ('publication-pipeline','telegram_web') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
                        old_identity = store.read_versions.identity
                        new_identity = str(uuid4())
                        store.db.execute('UPDATE reader_identity SET value=? WHERE id=1', (new_identity,))
                        report['storage_id'] = dict(old=old_identity, new=new_identity)
                    from oko.storage.publication_schema import remove_feed_url_key
                    remove_feed_url_key(store.db)
                    store.publications.recover()
                    report['success'] = True
            finally:
                store.db.close()
            if not report['success']:
                shutil.rmtree(work/'storage')
            report['backup_manifest'] = before
            (work/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            if manifest(source) != before:
                raise ValueError('Source changed before migration publication')
            os.replace(work, destination)
            return report
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise


def _convert(store: Store, report: dict[str, Any]) -> None:
    for feed in store.subscriptions.feeds():
        binding = store.subscriptions.telegram.binding(feed['id'])
        if not binding or binding['kind'] != 'web' or feed['connection'] == 'deleting':
            continue
        cursor = store.cursor(feed['id'], 'telegram-web') or ''
        try:
            with store.db:
                store.publications.configure_feed(feed['id'])
        except (sqlite3.IntegrityError, ValueError) as error:
            report['conflicts'].append(dict(feed_id=feed['id'], reason=str(error)))
            continue
        current = store.db.execute('SELECT * FROM feeds WHERE id=?', (feed['id'],)).fetchone()
        report['feeds'].append(dict(old_id=feed['id'], new_id=feed['id'], old_key=feed['identity'],
                                    platform=current['platform'], external_id=current['external_id']))
        with store.db:
            store.db.execute('UPDATE feeds SET cursor=? WHERE id=?', (cursor, feed['id']))
            store.db.execute('DELETE FROM state WHERE key=?', (store.cursor_key(feed['id'], 'telegram-web'),))
        for row in store.db.execute('SELECT * FROM items WHERE feed_id=? AND viewed=0', (feed['id'],)).fetchall():
            item = row['id']
            try:
                with store.db:
                    store.publications.identify(item, publication_reference(row['url']))
            except (sqlite3.IntegrityError, ValueError, AdapterException) as error:
                report['conflicts'].append(dict(publication_id=item, reason=str(error)))
                continue
            current = store.db.execute('SELECT * FROM items WHERE id=?', (item,)).fetchone()
            mapping = dict(old_id=item, new_id=item, old_key=row['source_id'],
                           platform=current['platform'], external_id=current['external_id'], results=[])
            publication = store.publication(item)
            if publication and publication.teaser:
                previous_text=store.original(item)[1]
                prepared = store.publications.prepare(item, publication.teaser, store.path(item).parent)
                # Capture only results accepted against the current input by the
                # existing processors. File presence alone never establishes this.
                results: dict[str, Any] = {}
                filtered = store.filters.read(item)
                if filtered['status'] == 'done':
                    results['filter'] = dict(file=(store.path(item).parent/'filter.md').read_text())
                if store.processing.theses.read(item) is not None:
                    accepted = store.processing.theses._accepted(item)
                    assert accepted
                    results['theses'] = dict(file=accepted[1].read_text())
                current_text='\n\n'.join(b['text'] for b in publication_input.blocks(prepared[0]) if b['type']=='text')
                if results and current_text!=previous_text:
                    report['unportable'].append(dict(publication_id=item,reason='Changed processing text; results require reprocessing'))
                    results={}
                with store.db:
                    store.publications.publish(item, prepared, store.content_version(item))
                    for kind,value in results.items():
                        # Result was validated against the legacy input above;
                        # translate its signature for the same canonical text.
                        header,text=value['file'].split('\n\n',1)
                        saved=json.loads(header)
                        saved['signature']=publication_input.signature(store,item,kind)
                        value['file']=json.dumps(saved,ensure_ascii=False)+'\n\n'+text
                        store.publications.results(item,{kind:value})
                        table='filter_inputs' if kind=='filter' else 'thesis_inputs'
                        store.db.execute('UPDATE '+table+' SET signature=? WHERE item=?',(saved['signature'],item))
                    store.publications.status(item)
                mapping['results'] = list(results)
            else:
                report['unportable'].append(dict(publication_id=item, reason='No readable latest content; fetch scheduled'))
                with store.db:
                    store.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1", (item,))
            report['publications'].append(mapping)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    report = migrate_publications(args.source, args.destination)
    print(json.dumps(dict(success=report['success'], report=str(args.destination/'report.json'))))
    raise SystemExit(0 if report['success'] else 1)


if __name__ == '__main__':
    main()
