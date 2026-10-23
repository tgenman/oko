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
            from oko.storage.migration_preflight import jobs,inspect,results
            report['interrupted_jobs']=jobs(str(work/'storage'/'state.sqlite'))
            previous_results=results(str(work/'storage'/'state.sqlite'))
            store = Store(work/'storage', lambda _: None)
            try:
                inspect(store,report)
                _convert(store, report)
                _remove_viewed(store,report)
                for item,operations in previous_results.items():
                    row=store.db.execute('SELECT processing_metadata FROM items WHERE id=?',(item,)).fetchone()
                    current=set(json.loads(row[0])) if row else set()
                    for operation in operations-current:
                        report['unportable'].append(dict(publication_id=item,reason='Unverified '+operation+' result removed; reprocessing required'))
                if not report['conflicts']:
                    with store.db:
                        store.db.execute("INSERT INTO state VALUES ('publication-pipeline','complete') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
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
    # Already converted state is authoritative; do not round-trip it through legacy files.
    for row in store.db.execute('SELECT * FROM items WHERE platform IS NOT NULL AND viewed=0').fetchall():
        old_metadata=json.loads(row['processing_metadata'])
        with store.db:
            store.publications.reconcile(row['id'])
        metadata=json.loads(store.db.execute('SELECT processing_metadata FROM items WHERE id=?',(row['id'],)).fetchone()[0])
        for name in ('filter','summary'):
            if name in old_metadata and name not in metadata:
                report['unportable'].append(dict(publication_id=row['id'],reason='Unverified '+name+' result removed; reprocessing required'))
        metadata.pop('_legacy',None)
        with store.db:
            store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),row['id']))
        report['publications'].append(dict(old_id=row['id'],new_id=row['id'],old_key=row['source_id'],platform=row['platform'],external_id=row['external_id'],results=[name for name,key in [('filter','filter'),('theses','summary')] if key in metadata],preserved=True))
    for feed in store.db.execute('SELECT * FROM feeds WHERE adapter IS NOT NULL').fetchall():
        if store.publications.feed(feed['id']):
            report['feeds'].append(dict(old_id=feed['id'],new_id=feed['id'],old_key=feed['identity'],platform=feed['platform'],external_id=feed['external_id'],preserved=True))
    from oko.storage.migrate_rss import convert as convert_rss
    convert_rss(store,report)
    from oko.storage.migrate_youtube import convert
    convert(store,report)
    for feed in store.subscriptions.feeds():
        if store.publications.feed(feed['id']):
            continue
        binding = store.subscriptions.telegram.binding(feed['id'])
        row=store.db.execute('SELECT config FROM feeds WHERE id=?',(feed['id'],)).fetchone()
        native=bool(binding and binding['kind']=='client' and json.loads(row[0]).get('source_kind')=='channel')
        if not binding or (binding['kind'] != 'web' and not native) or feed['connection'] == 'deleting':
            continue
        namespace='telegram' if native else 'telegram-web'
        cursor = store.cursor(feed['id'], namespace) or ''
        if native:
            from oko.collection.telegram.client_channel_source import legacy_cursor
            cursor=legacy_cursor(cursor,feed['telegram_peer'],feed['initial_history'] or {})
        else:
            from oko.collection.telegram.adapter import legacy_cursor as web_cursor
            cursor=web_cursor(cursor,feed['initial_history'] or {})
        try:
            with store.db:
                store.publications.configure_feed(feed['id'],migrate_legacy=True)
        except (sqlite3.IntegrityError, ValueError) as error:
            report['conflicts'].append(dict(feed_id=feed['id'], reason=str(error)))
            continue
        current = store.db.execute('SELECT * FROM feeds WHERE id=?', (feed['id'],)).fetchone()
        report['feeds'].append(dict(old_id=feed['id'], new_id=feed['id'], old_key=feed['identity'],
                                    platform=current['platform'], external_id=current['external_id']))
        with store.db:
            store.db.execute('UPDATE feeds SET cursor=? WHERE id=?', (cursor, feed['id']))
            store.db.execute('DELETE FROM state WHERE key=?', (store.cursor_key(feed['id'], namespace),))
        for row in store.db.execute('SELECT * FROM items WHERE feed_id=? AND viewed=0', (feed['id'],)).fetchall():
            item = row['id']
            try:
                with store.db:
                    if native:
                        from oko.collection.telegram.client_adapter import publication_reference as native_reference
                        members=json.loads(row['members'])
                        ref=native_reference(feed['telegram_peer'],min(members),feed.get('telegram_username'))
                    else:
                        ref=publication_reference(row['url'])
                    from oko.storage.migrate_shared import merge
                    if merge(store,item,ref,report):
                        continue
                    store.publications.identify(item,ref)
            except (sqlite3.IntegrityError, ValueError, AdapterException) as error:
                report['conflicts'].append(dict(publication_id=item, reason=str(error)))
                continue
            current = store.db.execute('SELECT * FROM items WHERE id=?', (item,)).fetchone()
            mapping = dict(old_id=item, new_id=item, old_key=row['source_id'],
                           platform=current['platform'], external_id=current['external_id'], results=[])
            publication = store.publication(item)
            if publication and publication.teaser:
                previous_text=store.original(item)[1]
                results: dict[str, Any] = {}
                filtered = store.filters.read(item)
                if filtered['status'] == 'done':
                    results['filter'] = dict(file=(store.path(item).parent/'filter.md').read_text())
                if store.processing.theses.read(item) is not None:
                    accepted = store.processing.theses._accepted(item)
                    assert accepted
                    results['theses'] = dict(file=accepted[1].read_text())
                material=publication.teaser
                from oko.storage.migrate_articles import split
                try:
                    material=split(store,item,material,store.path(item).parent,report)
                except ValueError as error:
                    report['conflicts'].append(dict(publication_id=item,reason=str(error)))
                    continue
                if native and material.source_manifest:
                    from dataclasses import replace
                    from datetime import datetime,timezone
                    from oko.collection.adapters import PublicationContent,PublicationMetadata
                    from oko.collection.telegram.client_adapter import media_reference
                    from oko.collection.web_adapter import reference as article_reference
                    from oko.collection.telegram.telegraph import direct_articles
                    refs=[]
                    for resource in material.source_manifest.resources:
                        if not resource.key.startswith('telegram-client:'+str(feed['telegram_peer'])+':'):
                            continue
                        message=int(resource.key.rsplit(':',1)[-1])
                        refs.append(media_reference(feed['telegram_peer'],message,ref,mime_type=resource.mime))
                    value=PublicationContent(PublicationMetadata(ref,material.title,
                        datetime.fromtimestamp(row['published'],timezone.utc) if row['published_known'] else None),
                        dict(extra=dict(telegram=dict(members=list(material.members)))),tuple(refs),
                        tuple(article_reference(url) for url in direct_articles(str(material.source_manifest.post.get('text',material.text)))))
                    material=replace(material,adapter_content=value)
                from oko.storage.migrate_document import prepare as prepare_legacy
                prepared = prepare_legacy(store,item,material,store.path(item).parent)
                # Capture only results accepted against the current input by the
                # existing processors. File presence alone never establishes this.
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
                        store.publications.accept_result(item,kind,saved['signature'],saved['result'],saved.get('execution',saved.get('provenance')))
                        table='filter_inputs' if kind=='filter' else 'thesis_inputs'
                        store.db.execute('UPDATE '+table+' SET signature=? WHERE item=?',(saved['signature'],item))
                    store.publications.status(item)
                mapping['results'] = list(results)
            else:
                report['unportable'].append(dict(publication_id=item, reason='No readable latest content; fetch scheduled'))
                with store.db:
                    store.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1", (item,))
            report['publications'].append(mapping)


def _remove_viewed(store: Store, report: dict[str,Any]) -> None:
    """No tombstones in converted feeds; the backup retains the old history."""
    report['removed_viewed']=[]
    rows=store.db.execute("""SELECT i.* FROM items i LEFT JOIN feeds f ON f.id=i.feed_id
        WHERE i.viewed=1 AND (i.platform IS NOT NULL OR f.adapter IS NOT NULL)""").fetchall()
    for row in rows:
        item=row['id']
        if store.db.execute('SELECT 1 FROM publication_links l JOIN items i ON i.id=l.source_publication_id WHERE l.target_publication_id=? AND i.viewed=0',(item,)).fetchone():
            report['conflicts'].append(dict(publication_id=item,reason='Viewed publication is still required by a live dependency'))
            continue
        media=[r[0] for r in store.db.execute('SELECT id FROM media_files WHERE publication_id=?',(item,))]
        with store.db:
            store.publications.forget(item,prune=False)
            store.versions.forget(item)
            store.filters.forget(item)
            for table in ('thesis_inputs','retained_theses','processing_dirty'):
                store.db.execute('DELETE FROM '+table+' WHERE item=?',(item,))
            for identity in [item,*media]:
                store.db.execute('DELETE FROM execution_settings WHERE job IN (SELECT id FROM jobs WHERE item=?)',(identity,))
                store.db.execute('DELETE FROM jobs WHERE item=?',(identity,))
            store.db.execute('DELETE FROM items WHERE id=?',(item,))
        for parent in ('content','staging','media'):
            shutil.rmtree(store.root/parent/item,ignore_errors=True)
        report['removed_viewed'].append(item)


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
