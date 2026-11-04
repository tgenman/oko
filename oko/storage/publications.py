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
from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.domain.publication import PublicationIdentityConflict
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
            manager=getattr(self.store,'incomplete_draft',None)
            if manager is not None:manager.mark_materials(changed)
            if not deleted:
                from oko.storage.incomplete_index import classify_newly_eligible
                classify_newly_eligible(self.store.db,changed)
            status_changed(self.store.db)

    def feed(self, feed: str) -> bool:
        return self.store.db.execute("SELECT 1 FROM feeds WHERE id = %s AND adapter IN ( 'telegram_web_adapter' , 'rss_adapter' , 'youtube_adapter' )", (feed,)).fetchone() is not None

    def sync_feeds(self, feeds: list[str]) -> None:
        if self.store.db.execute("SELECT 1 FROM state WHERE key = 'publication-pipeline'").fetchone():
            with self.store.db:
                for feed in feeds:
                    self.configure_feed(feed)

    def configure_feed(self, feed: str) -> None:
        store = self.store
        row = store.db.execute('SELECT transport,kind,original_urls,config::text AS config,telegram_username,telegram_peer FROM feeds WHERE id = %s', (feed,)).fetchone()
        binding = store.subscriptions.telegram.binding(feed)
        transport=row['transport'] or row['kind'] if row else None
        if row and transport in ('rss','youtube'):
            from oko.collection.rss import normalized
            urls=row['original_urls']
            if isinstance(urls,str):urls=json.loads(urls)
            url=normalized(urls[0])
            platform,external_id,adapter='rss',url,'rss_adapter'
            if transport=='youtube':
                from oko.collection.youtube.adapter import feed_reference
                ref=feed_reference(url)
                platform,external_id,adapter='youtube',ref.key.external_id,'youtube_adapter'
                url=ref.url
            store.db.execute("UPDATE feeds SET platform = %s , external_id = %s , url = %s , adapter = %s , config = '{}' , enabled = %s WHERE id = %s",
                (platform,external_id,url,adapter,int(row['transport'] is not None),feed))
            return
        if not row or not binding or binding['kind'] != 'web':
            return
        url = reference('https://t.me/s/'+binding['username']).url
        external_id = str(row['telegram_peer']) if row['telegram_peer'] is not None else url
        store.db.execute("UPDATE feeds SET platform = 'telegram' , external_id = %s , url = %s , adapter = 'telegram_web_adapter' , config = %s::jsonb WHERE id = %s",
            (external_id, url, json.dumps({**json.loads(row['config']),**binding}), feed))

    def identify(self, item: str, ref: ExternalReference, *, effects: CollectionDiagnostics | None = None) -> None:
        db = self.store.db
        row = expect_row(db.execute('SELECT platform,external_id,url FROM publications WHERE id=%s', (item,)).fetchone())
        if row['url'] != ref.url:
            memberships.lock_graph(db)
            row = expect_row(db.execute('SELECT platform,external_id,url FROM publications WHERE id=%s', (item,)).fetchone())
        if (row['platform'], row['external_id']) != (ref.key.platform.value, ref.key.external_id):
            raise PublicationIdentityConflict(publication_id=item, old_platform=row['platform'],
                old_external_id=row['external_id'], new_platform=ref.key.platform.value, new_external_id=ref.key.external_id)
        if row['url'] != ref.url:
            db.execute('UPDATE publications SET url=%s WHERE id=%s', (ref.url,item))
            status_changed(db)
            if effects is None:
                self.store.jobs.invalidate_source_metadata_inputs(item)
            else:
                effects.source_url_changes.add(item)

    def changed_adapter(self, feed: str) -> None:
        """Invalidate old opaque descriptions atomically with a binding change."""
        db=self.store.db
        db.execute("UPDATE feeds SET cursor = '' WHERE id = %s",(feed,))
        from oko.storage.job_writes import execute as job_write
        from oko.storage.media_job_problems import previous_candidates
        from oko.storage import source_groups
        jobs=db.execute("""SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
            JOIN publications p ON p.id=m.publication_id WHERE p.feed_id=%s AND p.viewed=0 AND m.status<>'ready'""",(feed,)).fetchall()
        previous={row['id']:previous_candidates(self.store,row['id'],self.store.wall_clock()) for row in jobs}
        changed=job_write(db,"""INSERT INTO jobs(kind,item,status,reason_code,reason_message)
            SELECT 'download_media',m.id,'unavailable','media-metadata-changed','Обновляется описание вложения после смены доступа'
            FROM media_files m JOIN publications p ON p.id=m.publication_id
            WHERE p.feed_id=%s AND p.viewed=0 AND m.status<>'ready'
            ON CONFLICT(kind,item) DO UPDATE SET status='unavailable',generation=jobs.generation+1,
                reason_code=excluded.reason_code,
                reason_message=excluded.reason_message
            RETURNING id,OLD.reason_code AS previous_code""",(feed,)).fetchall()
        db.execute("UPDATE media_files SET status='unavailable' WHERE status<>'ready' AND publication_id IN (SELECT id FROM publications WHERE feed_id=%s AND viewed=0)",(feed,))
        source_groups.sync_changed_rows(self.store,list(changed),self.store.wall_clock())
        for row in changed:
            self.store.problems.sync_media_problem(row['id'],previous.get(row['id'],[]),self.store.wall_clock())
        marked = [row[0] for row in db.execute(f'SELECT p.id FROM ({owned_ids("= %s")}) owner JOIN publications p ON p.id=owner.id WHERE p.source_deleted', (feed,feed))]
        self.set_source_deleted(marked, False)
        db.execute("UPDATE publications SET status = 'pending_collection' WHERE id IN (SELECT p.id FROM publications p WHERE p.feed_id=%s AND p.viewed=0)",(feed,))

    def plan(self, feed: str, publications: list[Publication]) -> dict[str, DiscoveryProjection]:
        """Prepare cache output before the short page acceptance transaction."""
        plans: dict[str, DiscoveryProjection] = {}
        for publication in publications:
            ref = publication.adapter_reference
            if ref is None:
                raise ValueError('Adapter did not return a publication external key')
            if publication.id in plans:
                continue
            proposed_item=str(uuid4())
            checked = self.store.db.execute("""SELECT id,ARRAY[]::bigint[] AS source_jobs FROM publications
                WHERE platform=%s AND external_id=%s
                UNION ALL SELECT NULL::text,ARRAY(SELECT id FROM jobs WHERE item=%s AND reason_code LIKE 'source-%%')""",
                (ref.key.platform.value,ref.key.external_id,proposed_item)).fetchall()
            new_source_jobs=next(row['source_jobs'] for row in checked if row['id'] is None)
            existing=[row for row in checked if row['id'] is not None]
            if ref.key.platform.value == 'telegram' and publication.members:
                overlap = self.store.db.execute("""SELECT i.id,i.platform,i.external_id FROM publications i
                    CROSS JOIN LATERAL jsonb_array_elements_text(i.members::jsonb) AS member(value)
                    WHERE i.feed_id=%s AND i.platform='telegram' AND i.viewed=0 AND i.id<>%s AND member.value=ANY(%s)
                    ORDER BY i.id LIMIT 1""", (feed,existing[0]['id'] if existing else '',[str(member) for member in publication.members])).fetchone()
                if overlap:
                    raise PublicationIdentityConflict(publication_id=overlap['id'], old_platform=overlap['platform'],
                        old_external_id=overlap['external_id'], new_platform=ref.key.platform.value,
                        new_external_id=ref.key.external_id)
            if len(existing)>1:
                raise ValueError('Conflicting publication identity or Telegram album membership')
            if publication.observed_only and not existing:
                continue
            item=existing[0]['id'] if existing else proposed_item
            plan=DiscoveryProjection(item)
            if not existing:plan.new_source_jobs=tuple(new_source_jobs)
            current=self.store.db.execute("""SELECT viewed,members,content,status,
                EXISTS (SELECT 1 FROM jobs j WHERE j.item=publications.id AND j.reason_code LIKE 'source-%%') OR EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=publications.id AND j.reason_code LIKE 'source-%%') OR EXISTS (SELECT 1 FROM publication_links l JOIN jobs j ON j.item=l.target_publication_id AND j.reason_code LIKE 'source-%%' WHERE l.source_publication_id=publications.id) OR EXISTS (SELECT 1 FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=publications.id AND j.reason_code LIKE 'source-%%') AS source_problem_exists,
                (SELECT j.id FROM jobs j WHERE j.kind='delete' AND j.item=publications.id AND (j.reason_message IS NOT NULL OR j.reason_code IS NOT NULL)) AS delete_problem_job,
                (EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=publications.id AND (((m.resource_key='youtube:subtitles' OR j.media_null_owned) AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%'))) OR EXISTS (SELECT 1 FROM jobs j WHERE j.kind='download_media' AND j.item=publications.id AND ((j.media_null_owned AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.identity_event_owned AND j.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))))) AS media_problem_exists,
                ARRAY(SELECT DISTINCT m.publication_id FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=publications.id AND (((m.resource_key='youtube:subtitles' OR j.media_null_owned) AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%')) UNION SELECT j.item FROM publication_links l JOIN jobs j ON j.kind='download_media' AND j.item=l.target_publication_id WHERE l.source_publication_id=publications.id AND ((j.media_null_owned AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.identity_event_owned AND j.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))) AS media_problem_children,
                EXISTS (SELECT 1 FROM jobs j WHERE j.kind IN ('filter','theses') AND j.item=publications.id AND (((j.reason_code='' OR (j.reason_code IS NULL AND j.reason_message IS NOT NULL))) OR (j.reason_code IS NOT NULL AND j.reason_code<>'' AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission')))) AS processing_problem_exists,
                ARRAY(SELECT DISTINCT j.item FROM publication_links l JOIN jobs j ON j.kind IN ('filter','theses') AND j.item=l.target_publication_id WHERE l.source_publication_id=publications.id AND (((j.reason_code='' OR (j.reason_code IS NULL AND j.reason_message IS NOT NULL))) OR (j.reason_code IS NOT NULL AND j.reason_code<>'' AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission')))) AS processing_problem_children,
                (SELECT j.id FROM jobs j WHERE j.kind='fetch' AND j.item=publications.id AND ((j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config')))) AS fetch_problem_job,
                ARRAY(SELECT j.item FROM LATERAL
                      (SELECT target_publication_id FROM publication_links WHERE source_publication_id=publications.id ORDER BY target_publication_id OFFSET 0) l
                      CROSS JOIN LATERAL (SELECT j.item FROM jobs j WHERE j.item=l.target_publication_id AND j.kind='fetch'
                          AND ((j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config'))) OFFSET 0) j) AS fetch_problem_children,
                CASE WHEN viewed=0 AND feed_id IS NULL
                     THEN ARRAY(SELECT j.item FROM LATERAL
                                (SELECT target_publication_id FROM publication_links WHERE source_publication_id=publications.id
                                 ORDER BY target_publication_id OFFSET 0) l
                                CROSS JOIN LATERAL (SELECT j.item FROM jobs j WHERE j.item=l.target_publication_id AND j.kind='delete'
                                 AND (j.reason_message IS NOT NULL OR j.reason_code IS NOT NULL) OFFSET 0) j)
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
                (not current['content'] or current['status']=='pending_collection' or ref.key.platform.value in ('rss','youtube') and not described.needs_fetch or album_changed))
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
        if row and bool(row[0]):
            content=saved_content(row[0],mutable=True)
            previous_header=content.get('extra', {}).get('_reader', {})
            previous_quote=previous_header.get('reply_preview')
            if previous_quote:
                header['reply_preview']=previous_quote
            content['extra'].update(_reader=header)
            if material.adapter_content is None:
                content['extra']['_links']=[dict(platform=row['platform'],external_id=row['external_id'],url=row['url'])
                    for row in publication_links.children(self.store,item)]
            if material.adapter_content:
                content['extra']['_links']=[dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in material.adapter_content.linked_publications]
            media=self.store.media.publication_rows(item)
            self.store.media.project_header(content,media,collection_reason=material.reason)
            return content,media
        raise ValueError('Publication adapter did not return structured content')

    def _prepare_adapter(self, item: str, material: Material, header: dict[str,Any]) -> tuple[dict[str,Any],list[dict[str,Any]]]:
        value=material.adapter_content
        assert value is not None
        content=deepcopy(value.content)
        description=header.pop('description', None)
        if description and not any(block.get('type') == 'text' and block.get('role') == 'description'
                                   for block in content.get('blocks', [])):
            content.setdefault('blocks', []).append(dict(type='text', role='description', text=description))
        telegram=content.get('extra', {}).get('telegram')
        if isinstance(telegram, dict) and header.get('source_manifest'):
            # The mutable source manifest is owned by the saved reader facts;
            # the adapter copy is an input, not a second persisted snapshot.
            telegram.pop('manifest', None)
            telegram.pop('members', None)
            telegram.pop('description', None)
        media=[]
        identities={}
        known={row['resource_key']:row for row in self.store.media.publication_rows(item)}
        youtube=content.get('extra',{}).get('youtube')
        old_content=self.store.db.execute('SELECT content FROM publications WHERE id = %s',(item,)).fetchone() if youtube is not None else None
        old_youtube=saved_content(old_content[0]).get('extra',{}).get('youtube',{}) if old_content else {}
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
            if youtube.get('captions')=='not_requested':
                for block in content['blocks']:
                    if block.get('role')=='subtitles':
                        key=block['ref']['$media']
                        for row in media:
                            if row['resource_key']==key:
                                row.update(status='pending_download',reason='')
        for block in content.get('blocks',[]):
            if block['type']=='media':
                block['media_id']=identities[block.pop('ref')['$media']]
        content.setdefault('extra',{}).update(_reader=header,
            _links=[dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in value.linked_publications])
        collection_reason='' if youtube is not None or material.reason.startswith('Вложения ещё не загружены') else material.reason
        self.store.media.project_header(content,media,collection_reason=collection_reason)
        text='\n\n'.join(block.get('text','') for block in content['blocks'] if block['type']=='text' and block.get('role')=='body')
        return content,media

    def publish(self, item: str, prepared: tuple[dict[str, Any], list[dict[str, Any]]], fingerprint: str, effects: CollectionDiagnostics) -> None:
        """Called in the accepting transaction, after durable media preparation."""
        content, media = prepared
        store = self.store
        old = expect_row(store.db.execute("""SELECT p.content,p.platform,p.title,
            EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=p.id AND j.reason_code LIKE 'source-%%') AS source_media_exists,
            ARRAY(SELECT j.id FROM jobs j WHERE j.kind='download_media' AND j.item=ANY(%s) AND j.reason_code LIKE 'source-%%' AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=j.item)) AS source_orphan_jobs,
            (EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id
            WHERE m.publication_id=p.id AND (((m.resource_key='youtube:subtitles' OR j.media_null_owned) AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%'))) OR EXISTS (SELECT 1 FROM jobs j WHERE j.kind='download_media' AND j.item=p.id AND ((j.media_null_owned AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.identity_event_owned AND j.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))))) AS media_problem_exists,
            ARRAY(SELECT j.id FROM jobs j WHERE j.kind='download_media' AND j.item=ANY(%s) AND ((j.media_null_owned AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%')) AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=j.item)) AS orphan_media_jobs
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
                previous_problems=managed_candidates(store,job_id,store.wall_clock())
                if previous_problems:effects.media_problems_before[job_id]=previous_problems
        self.set_source_deleted([item], False)
        from oko.collection.adapters import ExternalKey,Platform
        links=content['extra'].pop('_links',[])
        links_changed=publication_links.sync(store,item,tuple(ExternalReference(ExternalKey(Platform(ref['platform']),ref['external_id']),ref['url']) for ref in links),effects)
        previous = saved_content(old['content'])
        previous_title=previous.get('extra',{}).get('_reader',{}).get('title',old['title'])
        same = not links_changed and publication_input.blocks(previous)==publication_input.blocks(content) and previous_title==content['extra']['_reader']['title']
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
            or previous_title!=content['extra']['_reader']['title']
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
        removed=[row['id'] for row in store.db.execute('SELECT id FROM media_files WHERE publication_id = %s',(item,)) if row['id'] not in retained]
        if removed:
            store.db.execute("""UPDATE jobs j SET payload=jsonb_set(j.payload,'{retired_media_key}',COALESCE(to_jsonb(m.storage_key),'null'::jsonb))
                FROM media_files m WHERE j.kind='download_media' AND j.item=m.id AND m.id=ANY(%s)""",(removed,))
        processor_jobs=[row['id'] for row in store.db.execute("SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND j.processor_visible",(item,))]
        store.problems.capture_processor_histories(processor_jobs,store.wall_clock())
        store.db.execute('DELETE FROM media_files WHERE publication_id = %s', (item,))
        for row in media:
            if row['status']=='unavailable' and row.get('reader_code')!='subtitles_absent' and row.get('reason') and row.get('reader_reason')!=row['reason']:
                effects.unavailable_media[row['id']]=('media-unavailable',row['reason'])
            store.db.execute('INSERT INTO media_files ( id , publication_id , resource_key , source_locator , name , mime_type , size_bytes , storage_key , status , resource_identity ) VALUES ( %(id)s , %(publication_id)s , %(resource_key)s , %(source_locator)s , %(name)s , %(mime_type)s , %(size_bytes)s , %(storage_key)s , %(status)s , %(resource_identity)s )', row)
        effects.media.add(item)
        if source_changed and old['platform'] in ('other_web','telegraph'):
            for row in media:
                if row['status']=='ready':
                    effects.revalidate[row['id']]=False
        for row in media:
            if row['resource_key']=='youtube:cover' and row['status']=='ready':
                effects.revalidate[row['id']]=False
        from oko.storage.publication_document import stored_content
        persisted=stored_content(content)
        persisted['extra']['_reader'].pop('title',None)
        store.db.execute('UPDATE publications SET content = %s , media_ids = %s , title = %s , status = %s WHERE id = %s',
            (json.dumps(persisted, ensure_ascii=False), json.dumps([r['id'] for r in media]),
             content['extra']['_reader']['title'], 'pending_filtering', item))
        if old['title']!=content['extra']['_reader']['title']:
            before_youtube=previous.get('extra',{}).get('youtube',{})
            after_youtube=content.get('extra',{}).get('youtube',{})
            title_only=(not links_changed and publication_input.blocks(previous)==publication_input.blocks(content)
                and all(before_youtube.get(key)==after_youtube.get(key) for key in ('captions','captions_digest')))
            if title_only:effects.title_only_changes.add(item)
            effects.source_title_changes[item]=title_only
        from oko.storage.incomplete_index import save_class
        save_class(store.db,item,content)
        if previous and old['platform'] != 'youtube' and previous_title != content['extra']['_reader']['title']:
            metadata_row = expect_row(store.db.execute('SELECT processing_metadata FROM publications WHERE id = %s', (item,)).fetchone())
            metadata = json.loads(metadata_row[0])
            for operation in ('filter', 'summary'):
                if operation in metadata:
                    metadata[operation].setdefault('source_title', previous_title)
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
        effects.parent_notifications[item]=item in effects.title_only_changes

    def original(self, item: str) -> tuple[dict[str,Any],str]:
        row=material_read.item(self.store.db,item)
        assert row is not None
        content=saved_content(row['content'],mutable=True)
        fetch=material_read.jobs(self.store.db,item).get('fetch')
        collection_reason=''
        if row['status']=='pending_collection':
            if fetch and fetch['status'] in ('pending','running','blocked','failed','unavailable'):
                waiting=bool(fetch['wait_scope'] and fetch['retry_at'] and fetch['retry_at']>self.store.wall_clock())
                fallback=('Публикация недоступна' if fetch['status']=='unavailable' else
                          'Не удалось получить публикацию' if fetch['status']=='failed' else
                          'Ожидание источника' if waiting else '')
                collection_reason=fetch['reason_message'] or fallback
            if not collection_reason and not content.get('extra',{}).get('_reader',{}).get('source_manifest',{}).get('post_web_unavailable'):
                collection_reason='Публикация ожидает сбора'
        media_rows=self.store.media.publication_rows(item)
        self.store.media.project_completeness(content,media_rows,collection_reason=collection_reason)
        header=deepcopy(content['extra']['_reader'])
        header['title']=row['title']
        youtube=content.get('extra', {}).get('youtube')
        if isinstance(youtube, dict):
            from oko.storage.publication_document import caption_state
            subtitle=next((m for m in media_rows if m['resource_key']=='youtube:subtitles'),None)
            if subtitle and subtitle['status']=='pending_download':
                job_status=subtitle.get('reader_job_status')
                header['captions']=('not_requested' if subtitle.get('reader_job_id') is None else
                                    'running' if job_status=='running' else
                                    'pending' if job_status in ('pending','blocked') else 'error')
            elif subtitle and subtitle['status']=='unavailable':
                header['captions']='unavailable' if subtitle.get('reader_job_status')=='done' and subtitle.get('reader_code')=='subtitles_absent' else 'error'
            elif subtitle and subtitle['status']=='ready':
                header['captions']='available'
            else:
                header['captions']=caption_state(youtube, header)
        descriptions=[block['text'] for block in content.get('blocks', []) if block.get('type')=='text' and block.get('role')=='description']
        if descriptions:
            header['description']='\n\n'.join(descriptions)
        quote=next((block for block in content.get('blocks', []) if block.get('type')=='quote'), None)
        if quote is not None:
            header['reply_preview']={key:quote.get(key) for key in ('text','author','source_url')}
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
        if not row or row['platform'] is None or not row['content']:
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
            content = publication_input._reading_content(self.store,item) if row['content'] else None
            available = bool(row['status']!='pending_collection' and content is not None and not publication_input.awaiting_input(content)
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

    def forget(self, item: str, *, prune: bool = True, effects: CollectionDiagnostics | None = None) -> None:
        db = self.store.db
        linked={row[0] for row in db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s',(item,))}
        self.forget_many([item],effects=effects)
        if prune:
            publication_links.prune(self.store,linked,effects=effects)

    def forget_many(self, items: list[str], *, effects: CollectionDiagnostics | None = None) -> None:
        """Clear saved copies without re-evaluating graph reachability."""
        db = self.store.db
        memberships.lock_graph(db)
        local_effects=effects if effects is not None else CollectionDiagnostics()
        publication_links.capture_media_problems(self.store,local_effects,set(items))
        publication_links.capture_source_groups(self.store,local_effects,set(items))
        for start in range(0, len(items), 400):
            batch = items[start:start+400]
            marks = ','.join('%s' for _ in batch)
            children=[row['target_publication_id'] for row in db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id=ANY(%s)',(batch,))]
            self.store.incomplete_draft.mark_materials(batch+children)
            removed = memberships.remove(db,items=batch)
            self.store.read_versions.memberships_changed(removed)
            db.execute(f'DELETE FROM publication_links WHERE source_publication_id IN ( {marks} ) OR target_publication_id IN ( {marks} )', batch+batch)
            # Keep the owner until its disk cleanup completes, after MediaFile rows
            # have been removed and late downloads have been cancelled.
            db.execute("""UPDATE jobs j SET payload=jsonb_set(j.payload,'{cleanup_publication}',to_jsonb(m.publication_id))
                FROM media_files m WHERE j.kind='download_media' AND j.item=m.id AND m.publication_id=ANY(%s)""",(batch,))
            db.execute(f'DELETE FROM media_files WHERE publication_id IN ( {marks} )', batch)
            db.execute(f"UPDATE publications SET content = '{{}}' , media_ids = '[]' , filtered_content = '{{}}' , summarized_content = '{{}}' , processing_metadata = '{{}}' WHERE id IN ( {marks} )", batch)
            self.store.processing.memberships_removed(removed)
        if effects is None:
            if local_effects.source_groups_before:self.store.jobs.sync_captured_source_groups(local_effects)
            for job_id,previous in local_effects.media_problems_before.items():
                self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
