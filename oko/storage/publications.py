"""Latest Publication and results in SQLite; files belong to MediaFile.

All supported sources use the same current storage.
"""
from copy import deepcopy
from datetime import datetime, timezone
from oko.storage import processing_input as publication_input
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from oko.collection.contracts import Material, Publication
from oko.domain.collection import CollectionEffects
from oko.collection.adapters import ExternalReference
from oko.collection.telegram.adapter import reference
from oko.storage import publication_links,material_read
from oko.storage.reading import reading_value

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
    fingerprint: str | None = None
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
        CREATE INDEX IF NOT EXISTS publication_stage ON items(status,id) WHERE platform IS NOT NULL AND viewed=0;
        CREATE TABLE IF NOT EXISTS feed_publications (
          feed_id TEXT REFERENCES feeds(id), publication_id TEXT REFERENCES items(id),
          PRIMARY KEY(feed_id,publication_id));
        CREATE INDEX IF NOT EXISTS feed_publications_publication ON feed_publications(publication_id,feed_id);
        DROP VIEW IF EXISTS reading_memberships;
        CREATE VIEW reading_memberships AS SELECT publication_id,feed_id FROM feed_publications;
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
        row=material_read.item(self.store.db,item)
        return row is not None and row['platform'] is not None

    @reading_value
    def source_deleted(self,item: str) -> bool:
        snapshot=material_read.current(self.store.db)
        if snapshot and item in snapshot.items:
            return bool(snapshot.items[item]['source_deleted'])
        return self.store.db.execute('SELECT 1 FROM source_deleted WHERE item=?',(item,)).fetchone() is not None

    def feed(self, feed: str) -> bool:
        return self.store.db.execute("SELECT 1 FROM feeds WHERE id=? AND adapter IN ('telegram_web_adapter','telegram_client_adapter','rss_adapter','youtube_adapter')", (feed,)).fetchone() is not None

    def sync_feeds(self, feeds: list[str]) -> None:
        if self.store.db.execute("SELECT 1 FROM state WHERE key='publication-pipeline'").fetchone():
            with self.store.db:
                for feed in feeds:
                    self.configure_feed(feed)

    def configure_feed(self, feed: str) -> None:
        store = self.store
        row = store.db.execute('SELECT * FROM feeds WHERE id=?', (feed,)).fetchone()
        binding = store.subscriptions.telegram.binding(feed)
        transport=row['transport'] or row['kind'] if row else None
        if row and transport in ('rss','youtube'):
            from oko.collection.rss import normalized
            url=normalized(json.loads(row['original_urls'])[0])
            platform,external_id,adapter='rss',url,'rss_adapter'
            if transport=='youtube':
                from oko.collection.youtube.adapter import feed_reference
                ref=feed_reference(url)
                platform,external_id,adapter='youtube',ref.key.external_id,'youtube_adapter'
                url=ref.url
            details=store.subscriptions.details.snapshot(feed)
            effective=store.subscriptions.effective('feed',feed)['effective']
            store.db.execute('''UPDATE feeds SET platform=?,external_id=?,url=?,description=?,created_at=?,
                adapter=?,config='{}',interval_seconds=?,filter_config=?,summary_config=?,enabled=? WHERE id=?''',
                (platform,external_id,url,details['description'] or '',details['created'] or row['created_at'] or store.wall_clock(),adapter,
                 store.subscriptions.feed_refresh_minutes()*60,
                 json.dumps({k:v['value'] for k,v in effective.items() if k in ('prompt_filters','processor')}),
                 json.dumps({k:v['value'] for k,v in effective.items() if k in ('theses','prompt_theses','processor')}),int(row['transport'] is not None),feed))
            return
        if row and binding and binding['kind']=='client' and json.loads(row['config']).get('source_kind')=='channel':
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
            (external_id, url, details['description'] or '', details['created'] or row['created_at'] or store.wall_clock(), json.dumps({**json.loads(row['config']),**binding}),
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
        db.execute('''UPDATE media_files SET status='unavailable',reason='Обновляется описание вложения после смены доступа'
            WHERE status!='ready' AND publication_id IN (SELECT id FROM items WHERE feed_id=? AND viewed=0)''',(feed,))
        db.execute('DELETE FROM source_deleted WHERE item IN (SELECT id FROM items WHERE feed_id=?)',(feed,))
        db.execute("UPDATE items SET status='pending_collection' WHERE feed_id=? AND viewed=0",(feed,))
        db.execute('DELETE FROM telegram_read_state WHERE feed=?',(feed,))

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
                plan.content=self.prepare(item,publication.teaser)
                plan.fingerprint=publication_input.digest(plan.content[0])
            plans[publication.id]=plan
        return plans

    def prepare(self, item: str, material: Material) -> tuple[dict[str,Any],list[dict[str,Any]]]:
        """Convert the common adapter description; media bytes remain independent."""
        from oko.storage.publication_document import header as reading_header
        header=reading_header(material)
        if material.adapter_content and 'blocks' in material.adapter_content.content:
            return self._prepare_adapter(item,material,header)
        row=self.store.db.execute('SELECT content FROM items WHERE id=?',(item,)).fetchone()
        if row and row[0]!='{}':
            content=json.loads(row[0])
            content['extra'].update(_reader=header,_collection_reason=material.reason)
            if material.adapter_content is None:
                content['extra']['_links']=[dict(platform=row['platform'],external_id=row['external_id'],url=row['url'])
                    for row in publication_links.children(self.store,item)]
            if material.adapter_content:
                content['extra']['_links']=[dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in material.adapter_content.linked_publications]
            media=[dict(r) for r in self.store.db.execute('SELECT * FROM media_files WHERE publication_id=?',(item,))]
            self.store.media.project_header(content,media)
            return content,media
        raise ValueError('Publication adapter did not return structured content')

    def _prepare_adapter(self, item: str, material: Material, header: dict[str,Any]) -> tuple[dict[str,Any],list[dict[str,Any]]]:
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
                or ref.resource_identity is None and previous['resource_identity'] is None
                and previous['source_locator']==ref.locator
                and (not ref.name or previous['name']==ref.name)
                and (ref.size_bytes is None or previous['size_bytes']==ref.size_bytes)))
            identity=previous['id'] if previous and unchanged else str(uuid4())
            if ref.key in identities:
                raise ValueError('Duplicate adapter media key')
            identities[ref.key]=identity
            ready=bool(unchanged and previous and previous['status']=='ready' and previous['storage_key'] and (self.store.root/previous['storage_key']).is_file())
            media.append(dict(id=identity,publication_id=item,resource_key=ref.key,source_locator=ref.locator,
                name=ref.name or (previous['name'] if ready and previous else ''),mime_type=previous['mime_type'] if ready and previous else ref.mime_type,
                size_bytes=previous['size_bytes'] if ready and previous else ref.size_bytes,
                storage_key=previous['storage_key'] if ready and previous else None,
                status='ready' if ready else 'pending_download',reason='',resource_identity=ref.resource_identity))
        for resource in header.get('source_manifest',{}).get('resources',[]):
            previous=known.get(resource['key'])
            external=resource.get('external')
            if not external or resource['key'] in identities or not previous or not (previous['status']=='ready' or previous['storage_key'] or previous['reason']=='Локальный файл вложения недоступен'):
                continue
            if not previous['storage_key'] or not (self.store.root/previous['storage_key']).is_file():
                previous=dict(previous,status='unavailable',reason='Локальный файл вложения недоступен',storage_key=None)
            identities[resource['key']]=previous['id']
            media.append(previous)
            block=next((block for block in content['blocks'] if block.get('type')=='external_media'
                and block.get('url')==external['source_url']),None)
            if block is not None:
                block.clear()
                block.update(type='media',role=resource['kind'],ref={'$media':resource['key']})
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
        content.setdefault('extra',{}).update(_reader=header,_collection_reason='' if youtube is not None or header.get('reason','').startswith('Вложения ещё не загружены') else header.get('reason',''),
            _links=[dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in value.linked_publications])
        self.store.media.project_header(content,media)
        text='\n\n'.join(block.get('text','') for block in content['blocks'] if block['type']=='text' and block.get('role')=='body')
        return content,media

    def publish(self, item: str, prepared: tuple[dict[str, Any], list[dict[str, Any]]], fingerprint: str, effects: CollectionEffects) -> None:
        """Called in the accepting transaction, after durable media preparation."""
        content, media = prepared
        store = self.store
        store.db.execute('DELETE FROM source_deleted WHERE item=?',(item,))
        from oko.collection.adapters import ExternalKey,Platform
        links=content['extra'].pop('_links',[])
        links_changed=publication_links.sync(store,item,tuple(ExternalReference(ExternalKey(Platform(ref['platform']),ref['external_id']),ref['url']) for ref in links),effects)
        old = store.db.execute('SELECT content,platform FROM items WHERE id=?', (item,)).fetchone()
        previous = json.loads(old['content'])
        same = not links_changed and publication_input.blocks(previous)==publication_input.blocks(content) and previous.get('extra',{}).get('_reader',{}).get('title')==content['extra']['_reader']['title']
        if 'youtube' in content.get('extra',{}):
            before=previous.get('extra',{}).get('youtube',{})
            after=content['extra']['youtube']
            same=not links_changed and all(before.get(key)==after.get(key) for key in ('captions','captions_digest'))
        old_header=previous.get('extra',{}).get('_reader',{})
        def embeds(header: dict[str,Any]) -> list[str]:
            return [r.get('reason','') for r in header.get('source_manifest',{}).get('resources',[])
                if r.get('reason','').startswith('Внешнее встраивание')]
        source_changed=bool(previous.get('blocks')) and (
            embeds(old_header)!=embeds(content['extra']['_reader']) or
            '\n\n'.join(block['text'] for block in publication_input.blocks(previous))!='\n\n'.join(block['text'] for block in publication_input.blocks(content))
            or old_header.get('title')!=content['extra']['_reader']['title']
            or bool(old_header.get('attachments')) and
                [block.get('media_id') for block in previous.get('blocks',[]) if block['type']=='media']!=
                [block.get('media_id') for block in content.get('blocks',[]) if block['type']=='media'])
        if content['extra']['_reader'].get('source_manifest',{}).get('preview_fragment') is not None and old_header.get('source_manifest',{}).get('preview_fragment') is None:
            source_changed=False
        content['extra']['_reader']['changed']=bool(old_header.get('changed')) or source_changed
        content['extra']['_collection_revision']=(previous.get('extra',{}).get('_collection_revision')
            if previous.get('extra',{}).get('_input_hash')==fingerprint else None) or str(uuid4())
        content['extra']['_input_hash'] = fingerprint
        retained={row['id'] for row in media}
        removed=[(row[0],) for row in store.db.execute('SELECT id FROM media_files WHERE publication_id=?',(item,)) if row[0] not in retained]
        store.db.execute('DELETE FROM media_files WHERE publication_id=?', (item,))
        for row in media:
            store.db.execute('''INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,mime_type,size_bytes,storage_key,status,reason,resource_identity)
                VALUES (:id,:publication_id,:resource_key,:source_locator,:name,:mime_type,:size_bytes,:storage_key,:status,:reason,:resource_identity)''', row)
        effects.media.add(item)
        if source_changed and old['platform'] in ('other_web','telegraph'):
            for row in media:
                if row['status']=='ready':
                    effects.revalidate[row['id']]=False
        for row in media:
            if row['resource_key']=='youtube:cover' and row['status']=='ready':
                effects.revalidate[row['id']]=False
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
        store.db.execute('INSERT OR IGNORE INTO processing_dirty VALUES (?)',(item,))
        self.reconcile(item)
        publication_links.notify_parents(store,item)

    def original(self, item: str) -> tuple[dict[str,Any],str]:
        row=material_read.item(self.store.db,item)
        assert row is not None
        content=json.loads(row['content'])
        header=deepcopy(content['extra']['_reader'])
        text='\n\n'.join(b['text'] for b in content.get('blocks',[]) if b['type']=='text' and b['role']=='body')
        return header,text


    def provenance(self, item: str, kind: str) -> dict[str,Any] | None:
        row=material_read.item(self.store.db,item)
        metadata=json.loads(row['processing_metadata']).get('filter' if kind=='filter' else 'summary',{}) if row else {}
        return {key:metadata[key] for key in ('processor','model','reasoning_effort','fast') if key in metadata} or None

    def accept_result(self, item: str, kind: str, signature: str, result: dict[str,Any], origin: dict[str,Any] | None) -> None:
        """Publish a validated result and its provenance in the caller's transaction."""
        row=self.store.db.execute('SELECT processing_metadata FROM items WHERE id=?',(item,)).fetchone()
        content=publication_input.content(self.store,item)
        metadata=json.loads(row[0])
        operation='filter' if kind=='filter' else 'summary'
        input_content=publication_input.selected_content(self.store,item,kind)
        metadata[operation]=dict(input_hash=publication_input.digest(publication_input.blocks(input_content or content)),signature=signature,
            settings_hash=publication_input.digest(publication_input.settings(self.store,item)),
            completed_at=datetime.fromtimestamp(self.store.wall_clock(),timezone.utc).isoformat(),**(origin or {}))
        if kind=='filter':
            filtered=publication_input.filtered(content,item,result)
            self.store.db.execute('UPDATE items SET filtered_content=?,status=? WHERE id=?',
                (json.dumps(filtered,ensure_ascii=False),'filter_out' if result['hidden'] else 'pending_summary',item))
        else:
            summarized=dict(points=[dict(text=p['text'],source_publication_ids=[r['id'] for r in p['sources']]) for p in result['points']],extra={})
            self.store.db.execute("UPDATE items SET summarized_content=?,status='ready' WHERE id=?",(json.dumps(summarized,ensure_ascii=False),item))
        self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),item))

    def complete_empty(self, item: str, kind: str, signature: str) -> None:
        result=dict(hidden=False,removed=[],reason='Нет текста для обработки') if kind=='filter' else dict(points=[])
        self.accept_result(item,kind,signature,result,None)
        row=self.store.db.execute('SELECT processing_metadata FROM items WHERE id=?',(item,)).fetchone()
        metadata=json.loads(row[0])
        metadata['filter' if kind=='filter' else 'summary']['reason']='no_input'
        self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),item))
        if kind=='filter':
            row=self.store.db.execute('SELECT filtered_content FROM items WHERE id=?',(item,)).fetchone()
            filtered=json.loads(row[0])
            filtered['blocks']=[]
            self.store.db.execute('UPDATE items SET filtered_content=? WHERE id=?',(json.dumps(filtered,ensure_ascii=False),item))

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
        row = self.store.db.execute('SELECT processing_metadata,feed_id,viewed FROM items WHERE id=? AND platform IS NOT NULL', (item,)).fetchone()
        if row is None:
            return False
        metadata = json.loads(row['processing_metadata'])
        for kind, operation, column, signature in (
            ('filter', 'filter', 'filtered_content', self.store.filters.signature(item)),
            ('theses', 'summary', 'summarized_content', self.store.processing.theses.signature(item)),
        ):
            if operation in metadata and metadata[operation].get('signature',metadata[operation].get('input_hash')) != signature:
                metadata.pop(operation)
                self.store.db.execute(f"UPDATE items SET {column}='{{}}' WHERE id=?", (item,))
        if row['feed_id'] is not None and not row['viewed']:
            current=publication_input.input_hash(self.store,item)
            if metadata.get('_revision_input')!=current:
                metadata['_revision_input']=current
                metadata['_revision']=str(uuid4())
        else:
            metadata.pop('_revision',None)
            metadata.pop('_revision_input',None)
            metadata.pop('_pass',None)
        self.store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), item))
        self.store.read_versions.sync_filter(item,bool(self.store.filters.read(item)['hidden']))
        self.status(item)
        return True

    def prune(self, item: str) -> None:
        media_directory = self.store.root/'media'/item
        if media_directory.is_dir():
            referenced = {r[0] for r in self.store.db.execute('SELECT storage_key FROM media_files WHERE publication_id=?', (item,))}
            for path in media_directory.iterdir():
                if str(path.relative_to(self.store.root)) not in referenced and path not in self.store.media.active_paths:
                    path.unlink()

    def forget(self, item: str, *, prune: bool = True) -> None:
        db = self.store.db
        linked={row[0] for row in db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id=?',(item,))}
        db.execute('DELETE FROM feed_publications WHERE publication_id=?', (item,))
        db.execute('DELETE FROM publication_links WHERE source_publication_id=? OR target_publication_id=?', (item, item))
        db.execute('DELETE FROM media_files WHERE publication_id=?', (item,))
        db.execute("UPDATE items SET content='{}',media_ids='[]',filtered_content='{}',summarized_content='{}',processing_metadata='{}' WHERE id=?", (item,))
        if prune:
            publication_links.prune(self.store,linked)
