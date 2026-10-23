"""Latest Publication in SQLite; old reader files are a disposable projection.

Only feeds selected by the offline migration use this path. Other sources keep
their current persistence until their own migration step.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from oko.collection.contracts import Material, Publication
from oko.collection.adapters import ExternalReference
from oko.collection.telegram.adapter import reference
from oko.storage.reading import saved_document

if TYPE_CHECKING:
    from oko.storage.store import Store


PUBLICATION_COLUMNS = {
    'platform': 'TEXT', 'external_id': 'TEXT', 'title': "TEXT NOT NULL DEFAULT ''",
    'published_at': 'REAL', 'discovered_at': 'REAL',
    'content': "TEXT NOT NULL DEFAULT '{}'", 'media_ids': "TEXT NOT NULL DEFAULT '[]'",
    'filtered_content': "TEXT NOT NULL DEFAULT '{}'", 'summarized_content': "TEXT NOT NULL DEFAULT '{}'",
    'processing_metadata': "TEXT NOT NULL DEFAULT '{}'",
    'status': "TEXT NOT NULL DEFAULT 'pending_collection'",
}
FEED_COLUMNS = {
    'platform': 'TEXT', 'external_id': 'TEXT', 'url': "TEXT NOT NULL DEFAULT ''",
    'description': "TEXT NOT NULL DEFAULT ''", 'created_at': 'REAL',
    'enabled': 'INTEGER NOT NULL DEFAULT 1', 'adapter': "TEXT NOT NULL DEFAULT ''",
    'config': "TEXT NOT NULL DEFAULT '{}'", 'cursor': "TEXT NOT NULL DEFAULT ''",
    'interval_seconds': 'INTEGER NOT NULL DEFAULT 1200',
    'filter_config': "TEXT NOT NULL DEFAULT '{}'", 'summary_config': "TEXT NOT NULL DEFAULT '{}'",
}


@dataclass
class DiscoveryProjection:
    item: str
    source_id: str
    version: tuple[int, str, bool] | None = None
    content: tuple[dict[str, Any], list[dict[str, Any]]] | None = None


class Publications:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        db = store.db
        with db:
            for table, additions in (('items', PUBLICATION_COLUMNS), ('feeds', FEED_COLUMNS)):
                columns = {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
                for name, declaration in additions.items():
                    if name not in columns:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        db.executescript('''
        CREATE UNIQUE INDEX IF NOT EXISTS publication_external_key ON items(platform,external_id) WHERE platform IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS feed_external_key ON feeds(platform,external_id) WHERE platform IS NOT NULL;
        CREATE TABLE IF NOT EXISTS feed_publications (
          feed_id TEXT REFERENCES feeds(id), publication_id TEXT REFERENCES items(id),
          PRIMARY KEY(feed_id,publication_id));
        CREATE TABLE IF NOT EXISTS publication_links (
          source_publication_id TEXT REFERENCES items(id), target_publication_id TEXT REFERENCES items(id),
          PRIMARY KEY(source_publication_id,target_publication_id));
        CREATE TABLE IF NOT EXISTS media_files (
          id TEXT PRIMARY KEY, publication_id TEXT NOT NULL REFERENCES items(id), resource_key TEXT NOT NULL,
          source_locator TEXT NOT NULL, name TEXT NOT NULL, mime_type TEXT, size_bytes INTEGER,
          storage_key TEXT, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
          UNIQUE(publication_id,resource_key));
        ''')
        if 'resource_identity' not in {r['name'] for r in db.execute('PRAGMA table_info(media_files)')}:
            db.execute('ALTER TABLE media_files ADD COLUMN resource_identity TEXT')
            db.commit()
        (store.root/'media').mkdir(exist_ok=True)

    def owns(self, item: str) -> bool:
        return self.store.db.execute('SELECT 1 FROM items WHERE id=? AND platform IS NOT NULL', (item,)).fetchone() is not None

    def feed(self, feed: str) -> bool:
        return self.store.db.execute("SELECT 1 FROM feeds WHERE id=? AND adapter='telegram_web_adapter'", (feed,)).fetchone() is not None

    def sync_feeds(self, feeds: list[str]) -> None:
        if self.store.db.execute("SELECT 1 FROM state WHERE key='publication-pipeline'").fetchone():
            with self.store.db:
                for feed in feeds:
                    self.configure_feed(feed)

    def configure_feed(self, feed: str) -> None:
        store = self.store
        row = store.db.execute('SELECT * FROM feeds WHERE id=?', (feed,)).fetchone()
        binding = store.subscriptions.telegram.binding(feed)
        if not row or not binding or binding['kind'] != 'web':
            return
        url = reference('https://t.me/s/'+binding['username']).url
        details = store.subscriptions.details.snapshot(feed)
        effective = store.subscriptions.effective('feed', feed)['effective']
        external_id = str(row['telegram_peer']) if row['telegram_peer'] is not None else url
        store.db.execute('''UPDATE feeds SET platform='telegram',external_id=?,url=?,description=?,created_at=?,
            adapter='telegram_web_adapter',config=?,interval_seconds=?,filter_config=?,summary_config=? WHERE id=?''',
            (external_id, url, details['description'] or '', details['created'] or row['created_at'] or store.wall_clock(), json.dumps(binding),
             store.subscriptions.feed_refresh_minutes()*60,
             json.dumps({k: v['value'] for k, v in effective.items() if k in ('prompt_filters', 'processor')}),
             json.dumps({k: v['value'] for k, v in effective.items() if k in ('theses', 'prompt_theses', 'processor')}), feed))

    def identify(self, item: str, ref: ExternalReference) -> None:
        row = self.store.db.execute('SELECT * FROM items WHERE id=?', (item,)).fetchone()
        self.store.db.execute('''UPDATE items SET platform=?,external_id=?,published_at=?,discovered_at=COALESCE(discovered_at,?) WHERE id=?''',
            (ref.key.platform.value, ref.key.external_id, row['published'] if row['published_known'] else None,
             self.store.wall_clock(), item))
        self.store.db.execute('INSERT OR IGNORE INTO feed_publications VALUES (?,?)', (row['feed_id'], item))

    def plan(self, feed: str, publications: list[Publication]) -> dict[str, DiscoveryProjection]:
        """Prepare cache output before the short page acceptance transaction."""
        plans: dict[str, DiscoveryProjection] = {}
        for publication in publications:
            ref = publication.adapter_reference
            if ref is None:
                raise ValueError('Adapter did not return a publication external key')
            if publication.id in plans:
                continue
            source_id = feed+':'+publication.id
            existing = self.store.db.execute('SELECT id,source_id FROM items WHERE (platform=? AND external_id=?) OR source_id=?',
                (ref.key.platform.value, ref.key.external_id, source_id)).fetchall()
            if not existing and publication.members:
                placeholders = ','.join('?' for _ in publication.members)
                existing = self.store.db.execute(f'''SELECT DISTINCT i.id,i.source_id FROM items i JOIN json_each(i.members) m
                    WHERE i.feed_id=? AND m.value IN ({placeholders})''', (feed, *publication.members)).fetchall()
            if len(existing)>1:
                raise ValueError('Conflicting publication identity or Telegram album membership')
            if publication.observed_only and not existing:
                continue
            if existing:
                plans[publication.id] = DiscoveryProjection(existing[0]['id'], existing[0]['source_id'])
            else:
                item = str(uuid4())
                plan = DiscoveryProjection(item, source_id)
                if publication.teaser:
                    plan.version = self.store.versions.prepare(item, publication.teaser)
                    plan.content = self.prepare(item, publication.teaser, self.store.versions.path(item, plan.version[0]).parent)
                plans[publication.id] = plan
        return plans

    def prepare(self, item: str, material: Material, directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Prepare durable files outside the accepting transaction."""
        header, text = saved_document(directory/'original.md')
        blocks: list[dict[str, Any]] = [dict(type='text', role='body', text=text)]
        if material.description:
            blocks.append(dict(type='text', role='description', text=material.description))
        if material.reply_preview:
            reply = material.reply_preview
            blocks.append(dict(type='quote', text=reply.text, author=reply.author, source_url=reply.source_url))
        known = {r['resource_key']: dict(r) for r in self.store.db.execute('SELECT * FROM media_files WHERE publication_id=?', (item,))}
        resources = {r.name: r for r in material.source_manifest.resources} if material.source_manifest else {}
        references = {r.key: r for r in material.adapter_content.media} if material.adapter_content else {}
        media: list[dict[str, Any]] = []
        names = list(dict.fromkeys([*resources, *[p.name for p in sorted(directory.iterdir()) if p.name.startswith(('media-', 'image-'))]]))
        attachments = {a.name: a for a in material.attachments}
        for name in names:
            resource = resources.get(name)
            if resource and resource.external:
                blocks.append(dict(type='external_media', kind=resource.external.kind, url=resource.external.source_url))
                continue
            key = resource.key if resource else name
            path = directory/name
            old = known.get(key)
            ref = references.get(key)
            same = bool(old and path.is_file() and old['storage_key'] and (self.store.root/old['storage_key']).is_file()
                        and self.store.versions.file_digest(path) == self.store.versions.file_digest(self.store.root/old['storage_key']))
            if ref and ref.resource_identity and old and old['resource_identity'] == ref.resource_identity:
                same = True
            identity = old['id'] if same and old else str(uuid4())
            storage_key = 'media/'+item+'/'+identity if path.is_file() else None
            if same and old and old['status']=='ready' and old['storage_key'] and (self.store.root/old['storage_key']).is_file():
                storage_key = old['storage_key']
                if not path.is_file():
                    os.link(self.store.root/storage_key, path)
            if storage_key and not same:
                destination = self.store.root/storage_key
                destination.parent.mkdir(parents=True, exist_ok=True)
                pending = destination.with_suffix('.tmp')
                with path.open('rb') as source, pending.open('wb') as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(pending, destination)
                self.store.versions._fsync(destination.parent)
                self.store.versions._fsync(destination.parent.parent)
            attachment = attachments.get(name)
            kind = resource.kind if resource else attachment.kind if attachment else 'image'
            media.append(dict(id=identity, publication_id=item, resource_key=key,
                source_locator=ref.locator if ref else old['source_locator'] if same and old else resource.source_url or '' if resource else '', name=ref.name if ref else old['name'] if old else name,
                mime_type=ref.mime_type if ref else resource.mime if resource else attachment.mime if attachment else None,
                size_bytes=path.stat().st_size if path.is_file() else ref.size_bytes if ref else None, storage_key=storage_key,
                status='ready' if storage_key else 'pending_download' if ref else 'unavailable',
                reason=old['reason'] if same and old and not storage_key else resource.reason or '' if resource else '', resource_identity=ref.resource_identity if ref else old['resource_identity'] if same and old else None))
            blocks.append(dict(type='media', role='attachment' if kind=='file' else kind, media_id=identity))
        extra = deepcopy(material.adapter_content.content.get('extra', {})) if material.adapter_content else {}
        # Temporary header for the old HTTP/processor projection, removed when
        # those consumers read blocks directly. It never owns body text/files.
        extra['_reader'] = header
        extra['_collection_reason'] = '' if header.get('reason', '').startswith('Вложения ещё не загружены') else header.get('reason', '')
        content = dict(blocks=blocks, extra=extra)
        self.store.media.project_header(content, media)
        self.store.versions._write(directory/'original.md', (json.dumps(header,ensure_ascii=False)+'\n\n'+text).encode())
        return content, media

    def publish(self, item: str, prepared: tuple[dict[str, Any], list[dict[str, Any]]], fingerprint: str) -> None:
        """Called in the accepting transaction, after durable media preparation."""
        content, media = prepared
        store = self.store
        old = store.db.execute('SELECT content FROM items WHERE id=?', (item,)).fetchone()
        previous = json.loads(old['content'])
        same = previous.get('extra', {}).get('_input_hash') == fingerprint
        content['extra']['_input_hash'] = fingerprint
        store.db.execute('DELETE FROM media_files WHERE publication_id=?', (item,))
        for row in media:
            store.db.execute('''INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,mime_type,size_bytes,storage_key,status,reason,resource_identity)
                VALUES (:id,:publication_id,:resource_key,:source_locator,:name,:mime_type,:size_bytes,:storage_key,:status,:reason,:resource_identity)''', row)
        self.store.media.schedule(item)
        store.db.execute('UPDATE items SET content=?,media_ids=?,title=?,status=? WHERE id=?',
            (json.dumps(content, ensure_ascii=False), json.dumps([r['id'] for r in media]),
             content['extra']['_reader']['title'], 'pending_filtering', item))
        if not same:
            store.db.execute("UPDATE items SET filtered_content='{}',summarized_content='{}',processing_metadata='{}' WHERE id=?", (item,))
        # Compatibility index points only to the current projection, never history.
        store.db.execute('DELETE FROM content_versions WHERE item=? AND number!=(SELECT number FROM content_current WHERE item=?)', (item, item))

    def recover(self, item: str | None = None) -> None:
        store = self.store
        query = "SELECT * FROM items WHERE platform IS NOT NULL AND viewed=0 AND content!='{}'"
        for row in store.db.execute(query+(' AND id=?' if item else ''), (item,) if item else ()).fetchall():
            content = json.loads(row['content'])
            directory = store.path(row['id']).parent
            directory.mkdir(parents=True, exist_ok=True)
            header = content['extra']['_reader']
            text = '\n\n'.join(b['text'] for b in content['blocks'] if b['type']=='text' and b['role']=='body')
            store.versions._write(directory/'original.md', (json.dumps(header, ensure_ascii=False)+'\n\n'+text).encode())
            wanted = set()
            for media in store.db.execute("SELECT * FROM media_files WHERE publication_id=? AND status='ready'", (row['id'],)):
                saved = store.root/media['storage_key']
                if saved.is_file():
                    target = directory/store.media.projection_name(media)
                    wanted.add(target.name)
                    if target.exists() and os.path.samefile(saved,target):
                        continue
                    target.unlink(missing_ok=True)
                    os.link(saved,target)
            for path in directory.iterdir():
                if path.name.startswith(('media-','image-')) and path.name not in wanted:
                    path.unlink()
            metadata = json.loads(row['processing_metadata'])
            for kind, result in metadata.get('_legacy', {}).items():
                store.versions._write(directory/(kind+'.md'), result['file'].encode())
            self.prune(row['id'])

    def results(self, item: str, results: dict[str, Any]) -> None:
        """Bridge already validated legacy results until processing moves to blocks."""
        row = self.store.db.execute('SELECT content,processing_metadata FROM items WHERE id=?', (item,)).fetchone()
        content = json.loads(row['content'])
        metadata = json.loads(row['processing_metadata'])
        metadata.setdefault('_legacy', {}).update(results)
        for kind, value in results.items():
            header, _ = value['file'].split('\n\n', 1)
            saved = json.loads(header)
            result = saved['result']
            operation = 'filter' if kind == 'filter' else 'summary'
            metadata[operation] = dict(input_hash=saved['signature'], provenance=saved.get('provenance'))
            if kind == 'filter':
                filtered = deepcopy(content)
                removed = []
                for fragment in result['removed']:
                    for index, block in enumerate(filtered['blocks']):
                        if block['type']=='text' and block['role']=='body' and fragment in block['text']:
                            block['text'] = block['text'].replace(fragment, '')
                            removed.append(dict(publication_id=item, block_index=index, text=fragment))
                            break
                filtered.update(reason=result['reason'], removed=removed)
                self.store.db.execute('UPDATE items SET filtered_content=?,status=? WHERE id=?',
                    (json.dumps(filtered, ensure_ascii=False), 'filter_out' if result['hidden'] else 'pending_summary', item))
            else:
                summarized = dict(points=[dict(text=p['text'], source_publication_ids=[r['id'] for r in p['sources']]) for p in result['points']], extra={})
                self.store.db.execute("UPDATE items SET summarized_content=?,status='ready' WHERE id=?", (json.dumps(summarized, ensure_ascii=False), item))
        self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), item))

    def status(self, item: str) -> None:
        row = self.store.db.execute('SELECT * FROM items WHERE id=? AND platform IS NOT NULL', (item,)).fetchone()
        if not row or row['content']=='{}':
            return
        filtered = self.store.filters.read(item)
        if filtered['status'] not in ('off', 'done'):
            status = 'pending_filtering'
        elif filtered['hidden']:
            status = 'filter_out'
        elif self.store.processing.theses.signature(item) and self.store.processing.theses.read(item) is None:
            status = 'pending_summary'
        else:
            status = 'ready'
        self.store.db.execute('UPDATE items SET status=? WHERE id=?', (status, item))

    def reconcile(self, item: str) -> None:
        row = self.store.db.execute('SELECT processing_metadata FROM items WHERE id=? AND platform IS NOT NULL', (item,)).fetchone()
        if row is None:
            return
        metadata = json.loads(row['processing_metadata'])
        for kind, operation, column, signature in (
            ('filter', 'filter', 'filtered_content', self.store.filters.signature(item)),
            ('theses', 'summary', 'summarized_content', self.store.processing.theses.signature(item)),
        ):
            if operation in metadata and metadata[operation]['input_hash'] != signature:
                metadata.pop(operation)
                metadata.get('_legacy', {}).pop(kind, None)
                self.store.db.execute(f"UPDATE items SET {column}='{{}}' WHERE id=?", (item,))
        self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), item))
        self.status(item)

    def prune(self, item: str) -> None:
        directory = self.store.path(item).parent
        if directory.parent.is_dir():
            for old in directory.parent.iterdir():
                if old.is_dir() and old != directory:
                    shutil.rmtree(old)
        media_directory = self.store.root/'media'/item
        if media_directory.is_dir():
            referenced = {r[0] for r in self.store.db.execute('SELECT storage_key FROM media_files WHERE publication_id=?', (item,))}
            for path in media_directory.iterdir():
                if str(path.relative_to(self.store.root)) not in referenced and path not in self.store.media.active_paths:
                    path.unlink()

    def forget(self, item: str) -> None:
        db = self.store.db
        db.execute("""UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL,problem_code=NULL
            WHERE kind='download_media' AND item IN (SELECT id FROM media_files WHERE publication_id=?)""", (item,))
        db.execute('DELETE FROM feed_publications WHERE publication_id=?', (item,))
        db.execute('DELETE FROM publication_links WHERE source_publication_id=? OR target_publication_id=?', (item, item))
        db.execute('DELETE FROM media_files WHERE publication_id=?', (item,))
        db.execute("UPDATE items SET content='{}',media_ids='[]',filtered_content='{}',summarized_content='{}',processing_metadata='{}' WHERE id=?", (item,))
