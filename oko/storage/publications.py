"""Latest Publication in SQLite; old reader files are a disposable projection.

Only feeds selected by the offline migration use this path. Other sources keep
their current persistence until their own migration step.
"""
from copy import deepcopy
from datetime import datetime, timezone
from oko.reviews import publication_input
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
from oko.storage import publication_links

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
        self.active=db.execute("SELECT 1 FROM state WHERE key='publication-pipeline'").fetchone() is not None
        if self.active:
            from oko.storage.publication_schema import remove_feed_url_key
            remove_feed_url_key(db)
        with db:
            for table, additions in (('items', PUBLICATION_COLUMNS), ('feeds', FEED_COLUMNS)):
                columns = {r['name'] for r in db.execute('PRAGMA table_info('+table+')')}
                for name, declaration in additions.items():
                    if name not in columns:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        db.executescript('''
        CREATE UNIQUE INDEX IF NOT EXISTS publication_external_key ON items(platform,external_id) WHERE platform IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS feed_external_key ON feeds(platform,external_id) WHERE platform IS NOT NULL;
        CREATE INDEX IF NOT EXISTS publication_stage ON items(status,id) WHERE platform IS NOT NULL AND viewed=0;
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
        return self.store.db.execute("SELECT 1 FROM feeds WHERE id=? AND adapter IN ('telegram_web_adapter','telegram_client_adapter','rss_adapter','youtube_adapter')", (feed,)).fetchone() is not None

    def sync_feeds(self, feeds: list[str]) -> None:
        if self.store.db.execute("SELECT 1 FROM state WHERE key='publication-pipeline'").fetchone():
            with self.store.db:
                for feed in feeds:
                    self.configure_feed(feed)

    def configure_feed(self, feed: str, *, migrate_legacy: bool = False) -> None:
        store = self.store
        row = store.db.execute('SELECT * FROM feeds WHERE id=?', (feed,)).fetchone()
        binding = store.subscriptions.telegram.binding(feed)
        if row and row['transport'] in ('rss','youtube'):
            # Existing legacy RSS rows are converted by the final offline migration.
            if not migrate_legacy and store.db.execute('SELECT 1 FROM items WHERE feed_id=? AND platform IS NULL',(feed,)).fetchone():
                return
            from oko.collection.rss import normalized
            url=normalized(json.loads(row['original_urls'])[0])
            platform,external_id,adapter='rss',url,'rss_adapter'
            if row['transport']=='youtube':
                from oko.collection.youtube.adapter import feed_reference
                ref=feed_reference(url)
                platform,external_id,adapter='youtube',ref.key.external_id,'youtube_adapter'
                url=ref.url
            details=store.subscriptions.details.snapshot(feed)
            effective=store.subscriptions.effective('feed',feed)['effective']
            store.db.execute('''UPDATE feeds SET platform=?,external_id=?,url=?,description=?,created_at=?,
                adapter=?,config='{}',interval_seconds=?,filter_config=?,summary_config=? WHERE id=?''',
                (platform,external_id,url,details['description'] or '',details['created'] or row['created_at'] or store.wall_clock(),adapter,
                 store.subscriptions.feed_refresh_minutes()*60,
                 json.dumps({k:v['value'] for k,v in effective.items() if k in ('prompt_filters','processor')}),
                 json.dumps({k:v['value'] for k,v in effective.items() if k in ('theses','prompt_theses','processor')}),feed))
            return
        if row and binding and binding['kind']=='client' and json.loads(row['config']).get('source_kind')=='channel':
            if not migrate_legacy and store.db.execute('SELECT 1 FROM items WHERE feed_id=? AND platform IS NULL',(feed,)).fetchone():
                return
            config={**json.loads(row['config']),**binding}
            url='https://t.me/'+row['telegram_username'] if row['telegram_username'] else 'https://t.me/c/'+str(-row['telegram_peer']-1000000000000)
            details=store.subscriptions.details.snapshot(feed)
            effective=store.subscriptions.effective('feed',feed)['effective']
            store.db.execute('''UPDATE feeds SET platform='telegram',external_id=?,url=?,description=?,created_at=?,
                adapter='telegram_client_adapter',config=?,interval_seconds=?,filter_config=?,summary_config=? WHERE id=?''',
                (str(row['telegram_peer']),url,details['description'] or '',details['created'] or row['created_at'] or store.wall_clock(),json.dumps(config),
                 store.subscriptions.feed_refresh_minutes()*60,
                 json.dumps({k:v['value'] for k,v in effective.items() if k in ('prompt_filters','processor')}),
                 json.dumps({k:v['value'] for k,v in effective.items() if k in ('theses','prompt_theses','processor')}),feed))
            return
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
        self.store.db.execute('''UPDATE items SET platform=?,external_id=?,url=?,published_at=?,discovered_at=COALESCE(discovered_at,?) WHERE id=?''',
            (ref.key.platform.value, ref.key.external_id, ref.url, row['published'] if row['published_known'] else None,
             self.store.wall_clock(), item))
        self.store.db.execute('INSERT OR IGNORE INTO feed_publications VALUES (?,?)', (row['feed_id'], item))

    def changed_adapter(self, feed: str) -> None:
        """Invalidate old opaque descriptions atomically with a binding change."""
        db=self.store.db
        db.execute("UPDATE feeds SET cursor='' WHERE id=?",(feed,))
        for namespace in ('telegram','telegram-web'):
            db.execute('DELETE FROM state WHERE key=?',(self.store.cursor_key(feed,namespace),))
        db.execute('''UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL,problem_code=NULL,
            retry_at=NULL,retry_delay=0,wait_scope=NULL,next=0
            WHERE kind='download_media' AND item IN (SELECT m.id FROM media_files m JOIN items i ON i.id=m.publication_id WHERE i.feed_id=?)''',(feed,))
        db.execute('''UPDATE media_files SET status='unavailable',reason='Обновляется описание вложения после смены доступа'
            WHERE status!='ready' AND publication_id IN (SELECT id FROM items WHERE feed_id=? AND viewed=0)''',(feed,))
        db.execute('DELETE FROM source_deleted WHERE item IN (SELECT id FROM items WHERE feed_id=?)',(feed,))
        db.execute('''INSERT INTO jobs(kind,item) SELECT 'fetch',id FROM items WHERE feed_id=? AND viewed=0
            ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,
            retry_at=NULL,retry_delay=0,wait_scope=NULL,error=NULL,problem_code=NULL''',(feed,))
        db.execute("UPDATE items SET status='pending_collection' WHERE feed_id=? AND viewed=0",(feed,))
        db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='read' AND item=?",(feed,))
        db.execute('DELETE FROM telegram_read_state WHERE feed=?',(feed,))
        db.execute('DELETE FROM telegram_read_topics WHERE feed=?',(feed,))
        self.store.enqueue_discovery(feed)

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
            item=existing[0]['id'] if existing else str(uuid4())
            plan=DiscoveryProjection(item,existing[0]['source_id'] if existing else source_id)
            current=self.store.db.execute('SELECT viewed,members FROM items WHERE id=?',(item,)).fetchone() if existing else None
            described=publication.teaser.adapter_content if publication.teaser else None
            album_changed=bool(current and described and ref.key.platform.value=='telegram'
                and described.content.get('extra',{}).get('telegram',{}).get('album_id') is not None
                and set(publication.members)!=set(json.loads(current['members']))
                and set(json.loads(current['members'])).issubset(publication.members))
            refresh=bool(current and not current['viewed'] and described and
                (ref.key.platform.value in ('rss','youtube') and not described.needs_fetch or album_changed))
            if publication.teaser and (not existing or refresh):
                plan.version=self.store.versions.prepare(item,publication.teaser)
                plan.content=self.prepare(item,publication.teaser,self.store.versions.path(item,plan.version[0]).parent)
            plans[publication.id]=plan
        return plans

    def prepare(self, item: str, material: Material, directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Prepare durable files outside the accepting transaction."""
        header, text = saved_document(directory/'original.md')
        if material.adapter_content and material.adapter_content.metadata.reference.key.platform.value != 'telegram':
            return self._prepare_adapter(item, material, header, directory)
        manifest=material.source_manifest
        if manifest and manifest.articles and manifest.preview_fragment is None:
            text='\n\n'.join(part for part in [str(manifest.post.get('text','')),*[article.text for article in manifest.articles if article.text is not None]] if part)
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
        extra['_links'] = [dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in material.adapter_content.linked_publications] if material.adapter_content else []
        extra['_reader'] = header
        extra['_collection_reason'] = '' if header.get('reason', '').startswith('Вложения ещё не загружены') else header.get('reason', '')
        content = dict(blocks=blocks, extra=extra)
        self.store.media.project_header(content, media)
        self.store.versions._write(directory/'original.md', (json.dumps(header,ensure_ascii=False)+'\n\n'+text).encode())
        return content, media

    def _prepare_adapter(self, item: str, material: Material, header: dict[str,Any], directory: Path) -> tuple[dict[str,Any],list[dict[str,Any]]]:
        value=material.adapter_content
        assert value is not None
        content=deepcopy(value.content)
        media=[]
        identities={}
        known={row['resource_key']:dict(row) for row in self.store.db.execute('SELECT * FROM media_files WHERE publication_id=?',(item,))}
        youtube=content.get('extra',{}).get('youtube')
        old_content=self.store.db.execute('SELECT content FROM items WHERE id=?',(item,)).fetchone() if youtube is not None else None
        old_youtube=json.loads(old_content[0]).get('extra',{}).get('youtube',{}) if old_content else {}
        for ref in value.media:
            previous=known.get(ref.key)
            subtitles=youtube is not None and any(block.get('ref')=={'$media':ref.key} and block.get('role')=='subtitles' for block in content['blocks'])
            keep_caption=bool(subtitles and previous and previous['source_locator']==ref.locator and
                (previous['status']=='ready' and previous['storage_key'] and (self.store.root/previous['storage_key']).is_file() or youtube.get('captions')!='not_requested' and old_youtube.get('captions') in ('unavailable','error')))
            if keep_caption and previous:
                identities[ref.key]=previous['id']
                media.append(previous)
                youtube['captions']=old_youtube.get('captions','available')
                if 'captions_digest' in old_youtube:
                    youtube['captions_digest']=old_youtube['captions_digest']
                continue
            unchanged=bool(previous and (
                ref.resource_identity is not None and ref.resource_identity==previous['resource_identity']
                or previous['status']=='pending_download' and previous['source_locator']==ref.locator
                and previous['name']==ref.name and previous['mime_type']==ref.mime_type and previous['size_bytes']==ref.size_bytes))
            identity=previous['id'] if previous and unchanged else str(uuid4())
            if ref.key in identities:
                raise ValueError('Duplicate adapter media key')
            identities[ref.key]=identity
            ready=bool(unchanged and previous and previous['status']=='ready' and previous['storage_key'] and (self.store.root/previous['storage_key']).is_file())
            media.append(dict(id=identity,publication_id=item,resource_key=ref.key,source_locator=ref.locator,
                name=ref.name,mime_type=ref.mime_type or (previous['mime_type'] if ready and previous else None),
                size_bytes=previous['size_bytes'] if ready and previous else ref.size_bytes,
                storage_key=previous['storage_key'] if ready and previous else None,
                status='ready' if ready else 'pending_download',reason='',resource_identity=ref.resource_identity))
        if youtube is not None:
            header['captions']={'available':'available','unavailable':'unavailable','not_requested':'not_requested'}.get(youtube.get('captions'),'error')
            if youtube.get('captions')=='not_requested':
                for block in content['blocks']:
                    if block.get('role')=='subtitles':
                        key=block['ref']['$media']
                        for row in media:
                            if row['resource_key']==key:
                                row.update(status='unavailable',reason='Субтитры не запрошены')
        for block in content.get('blocks',[]):
            if block['type']=='media':
                block['media_id']=identities[block.pop('ref')['$media']]
        content.setdefault('extra',{}).update(_reader=header,_collection_reason='',
            _links=[dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in value.linked_publications])
        self.store.media.project_header(content,media)
        text='\n\n'.join(block.get('text','') for block in content['blocks'] if block['type']=='text' and block.get('role')=='body')
        self.store.versions._write(directory/'original.md',(json.dumps(header,ensure_ascii=False)+'\n\n'+text).encode())
        return content,media

    def publish(self, item: str, prepared: tuple[dict[str, Any], list[dict[str, Any]]], fingerprint: str) -> None:
        """Called in the accepting transaction, after durable media preparation."""
        content, media = prepared
        store = self.store
        from oko.collection.adapters import ExternalKey,Platform
        links=content['extra'].pop('_links',[])
        links_changed=publication_links.sync(store,item,tuple(ExternalReference(ExternalKey(Platform(ref['platform']),ref['external_id']),ref['url']) for ref in links))
        old = store.db.execute('SELECT content FROM items WHERE id=?', (item,)).fetchone()
        previous = json.loads(old['content'])
        same = not links_changed and publication_input.blocks(previous)==publication_input.blocks(content) and previous.get('extra',{}).get('_reader',{}).get('title')==content['extra']['_reader']['title']
        if 'youtube' in content.get('extra',{}):
            before=previous.get('extra',{}).get('youtube',{})
            after=content['extra']['youtube']
            same=not links_changed and all(before.get(key)==after.get(key) for key in ('captions','captions_digest'))
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
        elif previous:
            row=store.db.execute('SELECT filtered_content FROM items WHERE id=?',(item,)).fetchone()
            filtered=json.loads(row[0])
            if filtered:
                result=dict(hidden=filtered.get('hidden',False),reason=filtered['reason'],removed=[r['text'] for r in filtered['removed']])
                refreshed=publication_input.filtered(publication_input.content(store,item),item,result)
                store.db.execute('UPDATE items SET filtered_content=? WHERE id=?',(json.dumps(refreshed,ensure_ascii=False),item))
        publication_links.notify_parents(store,item)
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
        content = publication_input.content(self.store,item)
        metadata = json.loads(row['processing_metadata'])
        metadata.setdefault('_legacy', {}).update(results)
        for kind, value in results.items():
            header, _ = value['file'].split('\n\n', 1)
            saved = json.loads(header)
            result = saved['result']
            operation = 'filter' if kind == 'filter' else 'summary'
            origin=saved.get('execution',saved.get('provenance')) or {}
            input_content=json.loads(self.store.db.execute('SELECT filtered_content FROM items WHERE id=?',(item,)).fetchone()[0]) if operation=='summary' else content
            metadata[operation] = dict(input_hash=publication_input.digest(publication_input.blocks(input_content or content)),signature=saved['signature'],
                settings_hash=publication_input.digest(publication_input.settings(self.store,item)),
                completed_at=datetime.fromtimestamp(self.store.wall_clock(),timezone.utc).isoformat(),**origin)
            if kind == 'filter':
                filtered=publication_input.filtered(content,item,result)
                self.store.db.execute('UPDATE items SET filtered_content=?,status=? WHERE id=?',
                    (json.dumps(filtered, ensure_ascii=False), 'filter_out' if result['hidden'] else 'pending_summary', item))
            else:
                summarized = dict(points=[dict(text=p['text'], source_publication_ids=[r['id'] for r in p['sources']]) for p in result['points']], extra={})
                self.store.db.execute("UPDATE items SET summarized_content=?,status='ready' WHERE id=?", (json.dumps(summarized, ensure_ascii=False), item))
        self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), item))

    def complete_empty(self, item: str, kind: str) -> None:
        row=self.store.db.execute('SELECT * FROM items WHERE id=? AND platform IS NOT NULL AND viewed=0 AND complete=1',(item,)).fetchone()
        if not row or row['content']=='{}':
            return
        content=publication_input.content(self.store,item)
        if publication_input.awaiting_linked_input(self.store,item,content):
            return
        if any(block.get('text','').strip() for block in publication_input.blocks(content)):
            return
        header=content['extra'].get('_reader',{})
        if header.get('source_manifest',{}).get('post_web_unavailable'):
            return
        signature=publication_input.signature(self.store,item,kind)
        if signature is None:
            return
        operation='filter' if kind=='filter' else 'summary'
        metadata=json.loads(row['processing_metadata'])
        if metadata.get(operation,{}).get('signature')==signature:
            with self.store.db:
                self.store.db.execute("UPDATE jobs SET status='done',generation=generation+1,error=NULL,problem_code=NULL WHERE kind=? AND item=? AND status='pending'",(kind,item))
            return
        publication_input.snapshot(self.store,item,0)
        result=dict(hidden=False,removed=[],reason='Нет текста для обработки') if kind=='filter' else dict(points=[])
        saved=dict(signature=signature,result=result,provenance=None)
        with self.store.db:
            self.results(item,{kind:dict(file=json.dumps(saved,ensure_ascii=False)+'\n\n')})
            metadata=json.loads(self.store.db.execute('SELECT processing_metadata FROM items WHERE id=?',(item,)).fetchone()[0])
            metadata[operation]['reason']='no_input'
            self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),item))
            if kind=='filter':
                filtered=json.loads(self.store.db.execute('SELECT filtered_content FROM items WHERE id=?',(item,)).fetchone()[0])
                filtered['blocks']=[]
                self.store.db.execute('UPDATE items SET filtered_content=? WHERE id=?',(json.dumps(filtered,ensure_ascii=False),item))
            self.store.db.execute("UPDATE jobs SET status='done',generation=generation+1,error=NULL,problem_code=NULL WHERE kind=? AND item=?",(kind,item))
        self.recover(item)

    def status(self, item: str) -> None:
        row = self.store.db.execute('SELECT * FROM items WHERE id=? AND platform IS NOT NULL', (item,)).fetchone()
        if not row or row['content']=='{}':
            return
        if row['feed_id'] is None:
            self.store.db.execute("UPDATE items SET status='ready' WHERE id=?",(item,))
            return
        selected=publication_input.content(self.store,item)
        if publication_input.awaiting_input(selected) or publication_input.awaiting_linked_input(self.store,item,selected):
            self.store.db.execute("UPDATE items SET status='pending_collection' WHERE id=?",(item,))
            return
        filtered = self.store.filters.read(item)
        jobs={r['kind']:r['status'] for r in self.store.db.execute("SELECT kind,status FROM jobs WHERE item=? AND kind IN ('filter','theses')",(item,))}
        if filtered['status'] not in ('off', 'done') or self.store.filters.signature(item) and jobs.get('filter') in ('pending','running'):
            status = 'pending_filtering'
        elif filtered['hidden']:
            status = 'filter_out'
        elif self.store.processing.theses.signature(item) and (self.store.processing.theses.read(item) is None or jobs.get('theses') in ('pending','running')) and json.loads(row['processing_metadata']).get('summary',{}).get('reason')!='no_input':
            status = 'pending_summary'
        else:
            status = 'ready'
        self.store.db.execute('UPDATE items SET status=? WHERE id=?', (status, item))

    def reconcile(self, item: str) -> bool:
        row = self.store.db.execute('SELECT processing_metadata FROM items WHERE id=? AND platform IS NOT NULL', (item,)).fetchone()
        if row is None:
            return False
        metadata = json.loads(row['processing_metadata'])
        for kind, operation, column, signature in (
            ('filter', 'filter', 'filtered_content', self.store.filters.signature(item)),
            ('theses', 'summary', 'summarized_content', self.store.processing.theses.signature(item)),
        ):
            table='filter_inputs' if kind=='filter' else 'thesis_inputs'
            previous=self.store.db.execute('SELECT signature FROM '+table+' WHERE item=?',(item,)).fetchone()
            if previous and previous[0]!=signature:
                self.store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL,problem_code=NULL WHERE kind=? AND item=? AND status!='cancelled'",(kind,item))
            if operation in metadata and metadata[operation].get('signature',metadata[operation].get('input_hash')) != signature:
                metadata.pop(operation)
                metadata.get('_legacy', {}).pop(kind, None)
                self.store.db.execute(f"UPDATE items SET {column}='{{}}' WHERE id=?", (item,))
        self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), item))
        self.store.read_versions.sync_filter(item,bool(self.store.filters.read(item)['hidden']))
        self.status(item)
        return True

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
        linked={row[0] for row in db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id=?',(item,))}
        db.execute("""UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL,problem_code=NULL
            WHERE kind='download_media' AND item IN (SELECT id FROM media_files WHERE publication_id=?)""", (item,))
        db.execute('DELETE FROM feed_publications WHERE publication_id=?', (item,))
        db.execute('DELETE FROM publication_links WHERE source_publication_id=? OR target_publication_id=?', (item, item))
        db.execute('DELETE FROM media_files WHERE publication_id=?', (item,))
        db.execute("UPDATE items SET content='{}',media_ids='[]',filtered_content='{}',summarized_content='{}',processing_metadata='{}' WHERE id=?", (item,))
        publication_links.prune(self.store,linked)
