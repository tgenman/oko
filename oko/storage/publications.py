"""Latest Publication and results in the database; files belong to MediaFile.

All supported sources use the same current storage.
"""
from oko.storage.media_paths import existing_file
from oko.storage.publication_owners import owned_ids
from oko.storage.database import expect_row
from oko.storage.reading import reading_snapshot, saved_content
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
from oko.storage import publication_links,material_read,memberships
from oko.storage.reading import reading_value
from oko.storage.reader_status import changed as status_changed

if TYPE_CHECKING:
    from oko.storage.store import Store


@dataclass
class DiscoveryProjection:
    item: str
    source_id: str
    delete_problem_job: int | None = None
    delete_problem_children: tuple[str,...] = ()
    fetch_problem_job: int | None = None
    fetch_problem_children: tuple[str,...] = ()
    media_problem_exists: bool = False
    media_problem_children: tuple[str,...] = ()
    processing_problem_exists: bool = False
    source_problem_exists: bool = False
    new_source_jobs: tuple[int,...] = ()
    processing_problem_children: tuple[str,...] = ()
    fingerprint: str | None = None
    content: tuple[dict[str, Any], list[dict[str, Any]]] | None = None


class Publications:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        (store.root/'media').mkdir(exist_ok=True)

    def owns(self, item: str) -> bool:
        row=material_read.item(self.store.db,item)
        return row is not None and row['platform'] is not None

    @reading_value
    def source_deleted(self,item: str) -> bool:
        snapshot=material_read.current(self.store.db)
        if snapshot:
            row = snapshot.items.get(item) or snapshot.related_items.get(item)
            if row is not None:
                return bool(row['source_deleted'])
        row = self.store.db.execute('SELECT source_deleted FROM publications WHERE id=%s', (item,)).fetchone()
        return bool(row and row[0])

    def set_source_deleted(self, identities: list[str], deleted: bool) -> None:
        """Change source markers and reader freshness in the caller's transaction."""
        ordered = sorted(set(identities))
        literal = 'TRUE' if deleted else 'FALSE'
        for start in range(0, len(ordered), 400):
            batch = ordered[start:start+400]
            changed = [row[0] for row in self.store.db.execute(
                f'UPDATE publications SET source_deleted={literal} WHERE id=ANY(%s) AND source_deleted IS DISTINCT FROM {literal} RETURNING id', (batch,))]
            if not changed:
                continue
            if not deleted:
                from oko.storage.incomplete_index import classify_newly_eligible
                classify_newly_eligible(self.store.db,changed)
            status_changed(self.store.db)

    def feed(self, feed: str) -> bool:
        return self.store.db.execute("SELECT 1 FROM feeds WHERE id = %s AND adapter IN ( 'telegram_web_adapter' , 'telegram_client_adapter' , 'rss_adapter' , 'youtube_adapter' )", (feed,)).fetchone() is not None

    def sync_feeds(self, feeds: list[str]) -> None:
        if self.store.db.execute("SELECT 1 FROM state WHERE key = 'publication-pipeline'").fetchone():
            with self.store.db:
                for feed in feeds:
                    self.configure_feed(feed)

    def configure_feed(self, feed: str) -> None:
        store = self.store
        row = store.db.execute('SELECT transport,kind,original_urls,config,telegram_username,telegram_peer FROM feeds WHERE id = %s', (feed,)).fetchone()
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
            store.db.execute("UPDATE feeds SET platform = %s , external_id = %s , url = %s , adapter = %s , config = '{}' , enabled = %s WHERE id = %s",
                (platform,external_id,url,adapter,int(row['transport'] is not None),feed))
            return
        if row and binding and binding['kind']=='client' and json.loads(row['config']).get('source_kind')=='channel':
            config={**json.loads(row['config']),**binding}
            url='https://t.me/'+row['telegram_username'] if row['telegram_username'] else 'https://t.me/c/'+str(-row['telegram_peer']-1000000000000)
            store.db.execute("UPDATE feeds SET platform = 'telegram' , external_id = %s , url = %s , adapter = 'telegram_client_adapter' , config = %s WHERE id = %s",
                (str(row['telegram_peer']),url,json.dumps(config),feed))
            return
        if not row or not binding or binding['kind'] != 'web':
            return
        url = reference('https://t.me/s/'+binding['username']).url
        external_id = str(row['telegram_peer']) if row['telegram_peer'] is not None else url
        store.db.execute("UPDATE feeds SET platform = 'telegram' , external_id = %s , url = %s , adapter = 'telegram_web_adapter' , config = %s WHERE id = %s",
            (external_id, url, json.dumps({**json.loads(row['config']),**binding}), feed))

    def identify(self, item: str, ref: ExternalReference) -> None:
        row = expect_row(self.store.db.execute('SELECT * FROM publications WHERE id = %s', (item,)).fetchone())
        if row['url'] != ref.url:
            status_changed(self.store.db)
        self.store.db.execute('UPDATE publications SET platform = %s , external_id = %s , url = %s , published_at = %s , discovered_at = COALESCE ( discovered_at , %s ) WHERE id = %s',
            (ref.key.platform.value, ref.key.external_id, ref.url, row['published'] if row['published_known'] else None,
             self.store.wall_clock(), item))

    def changed_adapter(self, feed: str) -> None:
        """Invalidate old opaque descriptions atomically with a binding change."""
        db=self.store.db
        db.execute("UPDATE feeds SET cursor = '' WHERE id = %s",(feed,))
        db.execute("UPDATE media_files SET status = 'unavailable' , reason = 'Обновляется описание вложения после смены доступа' WHERE status != 'ready' AND publication_id IN ( SELECT p.id FROM feed_publications m JOIN publications p ON p.id=m.publication_id WHERE m.feed_id = %s AND m.is_primary AND m.is_unviewed AND p.viewed = 0 )",(feed,))
        marked = [row[0] for row in db.execute(f'SELECT p.id FROM ({owned_ids("= %s")}) owner JOIN publications p ON p.id=owner.id WHERE p.source_deleted', (feed,feed))]
        self.set_source_deleted(marked, False)
        db.execute("UPDATE publications SET status = 'pending_collection' WHERE id IN (SELECT p.id FROM feed_publications m JOIN publications p ON p.id=m.publication_id WHERE m.feed_id = %s AND m.is_primary AND m.is_unviewed AND p.viewed = 0)",(feed,))
        if db.execute('DELETE FROM telegram_read_state WHERE feed = %s',(feed,)).rowcount:
            status_changed(db)

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
            proposed_item=str(uuid4())
            checked = self.store.db.execute("""SELECT id,source_id,ARRAY[]::bigint[] AS source_jobs FROM publications
                WHERE (platform=%s AND external_id=%s) OR source_id=%s
                UNION ALL SELECT NULL::text,NULL::text,ARRAY(SELECT id FROM jobs WHERE item=%s AND problem_code LIKE 'source-%%')""",
                (ref.key.platform.value,ref.key.external_id,source_id,proposed_item)).fetchall()
            new_source_jobs=next(row['source_jobs'] for row in checked if row['id'] is None)
            existing=[row for row in checked if row['id'] is not None]
            if not existing and publication.members:
                placeholders = ','.join('%s' for _ in publication.members)
                existing = list(self.store.db.execute(f'SELECT DISTINCT i . id , i . source_id FROM ({owned_ids("= %s")}) owner JOIN publications i ON i.id=owner.id CROSS JOIN jsonb_array_elements_text(i.members::jsonb) m(value) WHERE CAST ( m . value AS BIGINT ) IN ( {placeholders} )', (feed, feed, *publication.members)).fetchall())
            if len(existing)>1:
                raise ValueError('Conflicting publication identity or Telegram album membership')
            if publication.observed_only and not existing:
                continue
            item=existing[0]['id'] if existing else proposed_item
            plan=DiscoveryProjection(item,existing[0]['source_id'] if existing else source_id)
            if not existing:plan.new_source_jobs=tuple(new_source_jobs)
            current=self.store.db.execute("""SELECT viewed,members,complete,
                EXISTS (SELECT 1 FROM jobs j WHERE j.item=publications.id AND j.problem_code LIKE 'source-%%') OR EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=publications.id AND j.problem_code LIKE 'source-%%') OR EXISTS (SELECT 1 FROM publication_links l JOIN jobs j ON j.item=l.target_publication_id AND j.problem_code LIKE 'source-%%' WHERE l.source_publication_id=publications.id) OR EXISTS (SELECT 1 FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=publications.id AND j.problem_code LIKE 'source-%%') AS source_problem_exists,
                (SELECT j.id FROM jobs j WHERE j.kind='delete' AND j.item=publications.id AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL)) AS delete_problem_job,
                EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=publications.id AND m.resource_key IS DISTINCT FROM 'youtube:subtitles' AND j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%') AS media_problem_exists,
                ARRAY(SELECT DISTINCT m.publication_id FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=publications.id AND m.resource_key IS DISTINCT FROM 'youtube:subtitles' AND j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%') AS media_problem_children,
                EXISTS (SELECT 1 FROM jobs j WHERE j.kind IN ('filter','theses') AND j.item=publications.id AND (((j.problem_code='' OR (j.problem_code IS NULL AND j.error IS NOT NULL))) OR (j.problem_code IS NOT NULL AND j.problem_code<>'' AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission')))) AS processing_problem_exists,
                ARRAY(SELECT DISTINCT j.item FROM publication_links l JOIN jobs j ON j.kind IN ('filter','theses') AND j.item=l.target_publication_id WHERE l.source_publication_id=publications.id AND (((j.problem_code='' OR (j.problem_code IS NULL AND j.error IS NOT NULL))) OR (j.problem_code IS NOT NULL AND j.problem_code<>'' AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission')))) AS processing_problem_children,
                (SELECT j.id FROM jobs j WHERE j.kind='fetch' AND j.item=publications.id AND j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')) AS fetch_problem_job,
                ARRAY(SELECT j.item FROM LATERAL
                      (SELECT target_publication_id FROM publication_links WHERE source_publication_id=publications.id ORDER BY target_publication_id OFFSET 0) l
                      CROSS JOIN LATERAL (SELECT j.item FROM jobs j WHERE j.item=l.target_publication_id AND j.kind='fetch'
                          AND j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%' AND j.problem_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch') OFFSET 0) j) AS fetch_problem_children,
                CASE WHEN viewed=0 AND NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=publications.id AND m.is_primary)
                     THEN ARRAY(SELECT j.item FROM LATERAL
                                (SELECT target_publication_id FROM publication_links WHERE source_publication_id=publications.id
                                 ORDER BY target_publication_id OFFSET 0) l
                                CROSS JOIN LATERAL (SELECT j.item FROM jobs j WHERE j.item=l.target_publication_id AND j.kind='delete'
                                 AND (j.error IS NOT NULL OR j.problem_code IS NOT NULL) OFFSET 0) j)
                     ELSE ARRAY[]::text[] END AS delete_problem_children
                FROM publications WHERE id = %s""",(item,)).fetchone() if existing else None
            if current and current['delete_problem_job'] is not None:
                plan.delete_problem_job = current['delete_problem_job']
            if current:
                plan.delete_problem_children = tuple(current['delete_problem_children'])
                plan.fetch_problem_job = current['fetch_problem_job']
                plan.fetch_problem_children = tuple(current['fetch_problem_children'])
                plan.media_problem_exists = bool(current['media_problem_exists'])
                plan.media_problem_children = tuple(current['media_problem_children'])
                plan.processing_problem_exists = bool(current['processing_problem_exists'])
                plan.source_problem_exists = bool(current['source_problem_exists'])
                plan.processing_problem_children = tuple(current['processing_problem_children'])
            described=publication.teaser.adapter_content if publication.teaser else None
            album_changed=bool(current and described and ref.key.platform.value=='telegram'
                and described.content.get('extra',{}).get('telegram',{}).get('album_id') is not None
                and set(publication.members)!=set(json.loads(current['members']))
                and set(json.loads(current['members'])).issubset(publication.members))
            refresh=bool(current and not current['viewed'] and described and
                (not current['complete'] or ref.key.platform.value in ('rss','youtube') and not described.needs_fetch or album_changed))
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
        row=self.store.db.execute('SELECT content FROM publications WHERE id = %s',(item,)).fetchone()
        if row and row[0]!='{}':
            content=json.loads(row[0])
            content['extra'].update(_reader=header,_collection_reason=material.reason)
            if material.adapter_content is None:
                content['extra']['_links']=[dict(platform=row['platform'],external_id=row['external_id'],url=row['url'])
                    for row in publication_links.children(self.store,item)]
            if material.adapter_content:
                content['extra']['_links']=[dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in material.adapter_content.linked_publications]
            media=[dict(r) for r in self.store.db.execute('SELECT * FROM media_files WHERE publication_id = %s',(item,))]
            self.store.media.project_header(content,media)
            return content,media
        raise ValueError('Publication adapter did not return structured content')

    def _prepare_adapter(self, item: str, material: Material, header: dict[str,Any]) -> tuple[dict[str,Any],list[dict[str,Any]]]:
        value=material.adapter_content
        assert value is not None
        content=deepcopy(value.content)
        media=[]
        identities={}
        known={row['resource_key']:dict(row) for row in self.store.db.execute('SELECT * FROM media_files WHERE publication_id = %s',(item,))}
        youtube=content.get('extra',{}).get('youtube')
        old_content=self.store.db.execute('SELECT content FROM publications WHERE id = %s',(item,)).fetchone() if youtube is not None else None
        old_youtube=json.loads(old_content[0]).get('extra',{}).get('youtube',{}) if old_content else {}
        for ref in value.media:
            previous=known.get(ref.key)
            subtitles=youtube is not None and any(block.get('ref')=={'$media':ref.key} and block.get('role')=='subtitles' for block in content['blocks'])
            keep_caption=bool(subtitles and previous and previous['source_locator']==ref.locator and
                (previous['status']=='ready' and previous['storage_key'] and existing_file(self.store.root,previous['storage_key']) is not None or youtube.get('captions')!='not_requested' and old_youtube.get('captions') in ('unavailable','error')))
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
            ready=bool(unchanged and previous and previous['status']=='ready' and previous['storage_key'] and existing_file(self.store.root,previous['storage_key']) is not None)
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
            if not previous['storage_key'] or existing_file(self.store.root,previous['storage_key']) is None:
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
        old = expect_row(store.db.execute("""SELECT p.content,p.platform,
            EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=p.id AND j.problem_code LIKE 'source-%%') AS source_media_exists,
            ARRAY(SELECT j.id FROM jobs j WHERE j.kind='download_media' AND j.item=ANY(%s) AND j.problem_code LIKE 'source-%%' AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=j.item)) AS source_orphan_jobs,
            EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
            WHERE m.publication_id=p.id AND m.resource_key IS DISTINCT FROM 'youtube:subtitles' AND j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%') AS media_problem_exists,
            ARRAY(SELECT j.id FROM jobs j WHERE j.kind='download_media' AND j.item=ANY(%s) AND j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%' AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=j.item)) AS orphan_media_jobs
            FROM publications p WHERE p.id=%s""",([row['id'] for row in media],[row['id'] for row in media],item)).fetchone())
        if old['source_media_exists']:publication_links.capture_source_groups(store,effects,{item})
        if old['source_orphan_jobs']:
            from oko.storage import source_groups
            ids=[job_id for job_id in old['source_orphan_jobs'] if job_id not in effects.source_groups_before]
            for start in range(0,len(ids),400):
                group=ids[start:start+400]
                effects.source_groups_before.update(source_groups.job_keys_many(store,group))
                evidence=source_groups.job_evidence_many(store,[(job_id,None) for job_id in group])
                for key,objects in evidence.items():effects.source_evidence_before.setdefault(key,set()).update(objects)
        if old['media_problem_exists']:
            publication_links.capture_media_problems(store,effects,{item})
        if old['orphan_media_jobs']:
            from oko.storage.media_job_problems import managed_candidates
            for job_id in old['orphan_media_jobs']:
                if job_id in effects.media_problems_checked:continue
                effects.media_problems_checked.add(job_id)
                previous=managed_candidates(store,job_id,store.wall_clock())
                if previous:effects.media_problems_before[job_id]=previous
        self.set_source_deleted([item], False)
        from oko.collection.adapters import ExternalKey,Platform
        links=content['extra'].pop('_links',[])
        links_changed=publication_links.sync(store,item,tuple(ExternalReference(ExternalKey(Platform(ref['platform']),ref['external_id']),ref['url']) for ref in links),effects)
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
        removed=[(row[0],) for row in store.db.execute('SELECT id FROM media_files WHERE publication_id = %s',(item,)) if row[0] not in retained]
        processor_jobs=[row['id'] for row in store.db.execute("SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND j.processor_visible",(item,))]
        store.problems.capture_processor_histories(processor_jobs,store.wall_clock())
        store.db.execute('DELETE FROM media_files WHERE publication_id = %s', (item,))
        for row in media:
            store.db.execute('INSERT INTO media_files ( id , publication_id , resource_key , source_locator , name , mime_type , size_bytes , storage_key , status , reason , resource_identity ) VALUES ( %(id)s , %(publication_id)s , %(resource_key)s , %(source_locator)s , %(name)s , %(mime_type)s , %(size_bytes)s , %(storage_key)s , %(status)s , %(reason)s , %(resource_identity)s )', row)
        effects.media.add(item)
        if source_changed and old['platform'] in ('other_web','telegraph'):
            for row in media:
                if row['status']=='ready':
                    effects.revalidate[row['id']]=False
        for row in media:
            if row['resource_key']=='youtube:cover' and row['status']=='ready':
                effects.revalidate[row['id']]=False
        store.db.execute('UPDATE publications SET content = %s , media_ids = %s , title = %s , status = %s WHERE id = %s',
            (json.dumps(content, ensure_ascii=False), json.dumps([r['id'] for r in media]),
             content['extra']['_reader']['title'], 'pending_filtering', item))
        from oko.storage.incomplete_index import save_class
        save_class(store.db,item,content)
        if previous and old['platform'] != 'youtube' and old_header.get('title') != content['extra']['_reader']['title']:
            metadata_row = expect_row(store.db.execute('SELECT processing_metadata FROM publications WHERE id = %s', (item,)).fetchone())
            metadata = json.loads(metadata_row[0])
            for operation in ('filter', 'summary'):
                if operation in metadata:
                    metadata[operation].setdefault('source_title', old_header.get('title'))
            store.db.execute('UPDATE publications SET processing_metadata=%s WHERE id=%s',
                             (json.dumps(metadata, ensure_ascii=False), item))
        if same and previous:
            filtered_row=expect_row(store.db.execute('SELECT filtered_content FROM publications WHERE id = %s',(item,)).fetchone())
            filtered=json.loads(filtered_row[0])
            if filtered:
                result=dict(hidden=filtered.get('hidden',False),reason=filtered['reason'],removed=[r['text'] for r in filtered['removed']])
                refreshed=publication_input.filtered(publication_input.content(store,item),item,result)
                store.db.execute('UPDATE publications SET filtered_content = %s WHERE id = %s',(json.dumps(refreshed,ensure_ascii=False),item))
        self.reconcile(item)
        publication_links.notify_parents(store,item)

    def original(self, item: str) -> tuple[dict[str,Any],str]:
        row=material_read.item(self.store.db,item)
        assert row is not None
        content=saved_content(row['content'])
        header=deepcopy(content['extra']['_reader'])
        text='\n\n'.join(b['text'] for b in content.get('blocks',[]) if b['type']=='text' and b['role']=='body')
        return header,text


    def provenance(self, item: str, kind: str) -> dict[str,Any] | None:
        row=material_read.item(self.store.db,item)
        metadata=json.loads(row['processing_metadata']).get('filter' if kind=='filter' else 'summary',{}) if row else {}
        return {key:metadata[key] for key in ('processor','model','reasoning_effort','fast') if key in metadata} or None

    def accept_result(self, item: str, kind: str, signature: str, result: dict[str,Any], origin: dict[str,Any] | None) -> None:
        """Publish a validated result and its provenance in the caller's transaction."""
        row=expect_row(self.store.db.execute('SELECT processing_metadata, title, platform FROM publications WHERE id = %s',(item,)).fetchone())
        content=publication_input.content(self.store,item)
        metadata=json.loads(row[0])
        operation='filter' if kind=='filter' else 'summary'
        input_content=publication_input.selected_content(self.store,item,kind)
        metadata[operation]=dict(input_hash=publication_input.digest(publication_input.blocks(input_content or content)),signature=signature,
            source_title=None if row['platform']=='youtube' else row['title'],
            settings_hash=publication_input.digest(publication_input.settings(self.store,item)),
            completed_at=datetime.fromtimestamp(self.store.wall_clock(),timezone.utc).isoformat(),**(origin or {}))
        if kind=='filter':
            filtered=publication_input.filtered(content,item,result)
            self.store.db.execute('UPDATE publications SET filtered_content = %s , status = %s WHERE id = %s',
                (json.dumps(filtered,ensure_ascii=False),'filter_out' if result['hidden'] else 'pending_summary',item))
        else:
            summarized=dict(points=[dict(text=p['text'],source_publication_ids=[r['id'] for r in p['sources']]) for p in result['points']],extra={})
            self.store.db.execute("UPDATE publications SET summarized_content = %s , status = 'ready' WHERE id = %s",(json.dumps(summarized,ensure_ascii=False),item))
        self.store.db.execute('UPDATE publications SET processing_metadata = %s WHERE id = %s',(json.dumps(metadata,ensure_ascii=False),item))

    def complete_empty(self, item: str, kind: str, signature: str) -> None:
        result=dict(hidden=False,removed=[],reason='Нет текста для обработки') if kind=='filter' else dict(points=[])
        self.accept_result(item,kind,signature,result,None)
        row=expect_row(self.store.db.execute('SELECT processing_metadata FROM publications WHERE id = %s',(item,)).fetchone())
        metadata=json.loads(row[0])
        metadata['filter' if kind=='filter' else 'summary']['reason']='no_input'
        self.store.db.execute('UPDATE publications SET processing_metadata = %s WHERE id = %s',(json.dumps(metadata,ensure_ascii=False),item))
        if kind=='filter':
            row=expect_row(self.store.db.execute('SELECT filtered_content FROM publications WHERE id = %s',(item,)).fetchone())
            filtered=json.loads(row[0])
            filtered['blocks']=[]
            self.store.db.execute('UPDATE publications SET filtered_content = %s WHERE id = %s',(json.dumps(filtered,ensure_ascii=False),item))

    def status(self, item: str) -> None:
        status = self._status_change(item)
        if status is not None:
            self.store.db.execute('UPDATE publications SET status = %s WHERE id = %s', (status, item))

    @reading_snapshot()
    def _status_change(self, item: str) -> str | None:
        row = material_read.item(self.store.db,item)
        if not row or row['platform'] is None or row['content']=='{}':
            return None
        if row['feed_id'] is None:
            return 'ready' if row['status'] != 'ready' else None
        selected=publication_input.content(self.store,item)
        if publication_input.awaiting_input(selected) or publication_input.awaiting_linked_input(self.store,item,selected):
            return 'pending_collection' if row['status'] != 'pending_collection' else None
        filtered = self.store.filters.read(item)
        jobs={kind:row['status'] for kind,row in material_read.jobs(self.store.db,item).items() if kind in ('filter','theses')}
        if filtered['hidden']:
            status = 'filter_out'
        elif filtered['status'] not in ('off', 'done') or self.store.filters.signature(item) and jobs.get('filter') in ('pending','running'):
            status = 'pending_filtering'
        elif self.store.processing.theses.signature(item) and (publication_input.stage_enabled(self.store, item, 'theses') or jobs.get('theses') == 'running') and self.store.processing.theses.read(item) is None and json.loads(row['processing_metadata']).get('summary',{}).get('reason')!='no_input':
            status = 'pending_summary'
        else:
            status = 'ready'
        return status if row['status'] != status else None

    def reconcile(self, item: str) -> bool:
        # Reuse input facts only while computing; writes occur outside snapshots.
        with reading_snapshot():
            row = material_read.item(self.store.db,item)
            if row is None or row['platform'] is None:
                return False
            current = publication_input.input_hash(self.store,item) if row['feed_id'] is not None and not row['viewed'] else None
            content = publication_input._reading_content(self.store,item) if row['complete'] and row['content']!='{}' else None
            available = bool(content is not None and not publication_input.awaiting_input(content)
                             and not publication_input.awaiting_linked_input(self.store,item,content))
            source_hash = publication_input.digest(publication_input.blocks(content)) if available and content is not None else None
        metadata = json.loads(row['processing_metadata'])
        # Results carry the actual source blocks they were derived from. Keep
        # them when only settings change, but discard them on a confirmed input
        # change rather than retaining a hidden stale copy indefinitely.
        saved_filter = metadata.get('filter',{})
        saved_summary = metadata.get('summary',{})
        prior_source = saved_filter.get('input_hash')
        if prior_source is None and row['filtered_content']=='{}':
            prior_source = saved_summary.get('input_hash')
        saved_title = saved_filter.get('source_title', saved_summary.get('source_title'))
        title_changed = row['platform'] != 'youtube' and saved_title is not None and saved_title != row['title']
        if source_hash is not None and ((prior_source is not None and source_hash != prior_source) or title_changed):
            self.store.db.execute("UPDATE publications SET filtered_content='{}',summarized_content='{}' WHERE id=%s", (item,))
            metadata.pop('filter',None)
            metadata.pop('summary',None)
        if row['feed_id'] is not None and not row['viewed']:
            if metadata.get('_revision_input')!=current:
                metadata['_revision_input']=current
                metadata['_revision']=str(uuid4())
        else:
            metadata.pop('_revision',None)
            metadata.pop('_revision_input',None)
            metadata.pop('_pass',None)
        encoded = json.dumps(metadata, ensure_ascii=False)
        if encoded != row['processing_metadata']:
            self.store.db.execute('UPDATE publications SET processing_metadata = %s WHERE id = %s', (encoded, item))
        with reading_snapshot():
            status = self._status_change(item)
        if status is not None:
            self.store.db.execute('UPDATE publications SET status = %s WHERE id = %s', (status, item))
        return True

    def prune(self, item: str) -> None:
        media_directory = self.store.root/'media'/item
        if media_directory.is_dir():
            referenced = {r[0] for r in self.store.db.execute('SELECT storage_key FROM media_files WHERE publication_id = %s', (item,))}
            for path in media_directory.iterdir():
                if str(path.relative_to(self.store.root)) not in referenced and path not in self.store.media.active_paths:
                    path.unlink()

    def forget(self, item: str, *, prune: bool = True, effects: CollectionEffects | None = None) -> None:
        db = self.store.db
        linked={row[0] for row in db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s',(item,))}
        self.forget_many([item],effects=effects)
        if prune:
            publication_links.prune(self.store,linked,effects=effects)

    def forget_many(self, items: list[str], *, effects: CollectionEffects | None = None) -> None:
        """Clear saved copies without re-evaluating graph reachability."""
        db = self.store.db
        memberships.lock_graph(db)
        local_effects=effects if effects is not None else CollectionEffects()
        publication_links.capture_media_problems(self.store,local_effects,set(items))
        publication_links.capture_source_groups(self.store,local_effects,set(items))
        for start in range(0, len(items), 400):
            batch = items[start:start+400]
            marks = ','.join('%s' for _ in batch)
            removed = memberships.remove(db,items=batch)
            self.store.read_versions.memberships_changed(removed)
            db.execute(f'DELETE FROM publication_links WHERE source_publication_id IN ( {marks} ) OR target_publication_id IN ( {marks} )', batch+batch)
            db.execute(f'DELETE FROM media_files WHERE publication_id IN ( {marks} )', batch)
            db.execute(f"UPDATE publications SET content = '{{}}' , incomplete_class = 1 , media_ids = '[]' , filtered_content = '{{}}' , summarized_content = '{{}}' , processing_metadata = '{{}}' WHERE id IN ( {marks} )", batch)
            self.store.processing.memberships_removed(removed)
        if effects is None:
            if local_effects.source_groups_before:self.store.jobs.sync_captured_source_groups(local_effects)
            for job_id,previous in local_effects.media_problems_before.items():
                self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
