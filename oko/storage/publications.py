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
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shutil
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.domain.publication import PublicationIdentityConflict
from oko.collection.adapters import ExternalKey, ExternalReference, Platform, PublicationContent
from oko.collection.telegram.adapter import reference
from oko.storage import publication_links,material_read,memberships
from oko.storage.reading import reading_value
from oko.storage.reader_status import changed as status_changed

if TYPE_CHECKING:
    from oko.storage.store import Store


@dataclass
class PreparedPublication:
    title: str
    content: dict[str,Any]
    media: list[dict[str,Any]]
    links: tuple[ExternalReference,...]


@dataclass
class DiscoveryProjection:
    item: str
    fetch_problem_job: int | None = None
    fetch_problem_children: tuple[str,...] = ()
    media_problem_exists: bool = False
    media_problem_children: tuple[str,...] = ()
    processing_problem_exists: bool = False
    source_problem_exists: bool = False
    new_source_jobs: tuple[int,...] = ()
    processing_problem_children: tuple[str,...] = ()
    content: PreparedPublication | None = None


class Publications:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        (store.root/'media').mkdir(exist_ok=True)

    def notify_parents(self, item: str, *, preserve_cancelled: bool = False) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Publication notification requires an owning transaction')
        publication_links.notify_parents(self.store,item,preserve_cancelled=preserve_cancelled)

    def feed(self, feed: str) -> bool:
        return self.store.db.execute("SELECT 1 FROM feeds WHERE id = %s AND adapter IN ( 'telegram_web_adapter' , 'rss_adapter' , 'youtube_adapter' )", (feed,)).fetchone() is not None

    def sync_feeds(self, feeds: list[str]) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Feed synchronization requires an owning transaction')
        if self.store.db.execute("SELECT 1 FROM state WHERE key = 'publication-pipeline'").fetchone():
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
            JOIN publications p ON p.id=m.publication_id WHERE p.feed_id=%s AND m.status<>'ready'""",(feed,)).fetchall()
        previous={row['id']:previous_candidates(self.store,row['id'],self.store.wall_clock()) for row in jobs}
        changed=job_write(db,"""INSERT INTO jobs(kind,item,status,reason_code,reason_message)
            SELECT 'download_media',m.id,'unavailable','media-metadata-changed','Обновляется описание вложения после смены доступа'
            FROM media_files m JOIN publications p ON p.id=m.publication_id
            WHERE p.feed_id=%s AND m.status<>'ready'
            ON CONFLICT(kind,item) DO UPDATE SET status='unavailable',generation=jobs.generation+1,
                reason_code=excluded.reason_code,
                reason_message=excluded.reason_message
            RETURNING id,OLD.reason_code AS previous_code""",(feed,)).fetchall()
        db.execute("UPDATE media_files SET status='unavailable' WHERE status<>'ready' AND publication_id IN (SELECT id FROM publications WHERE feed_id=%s)",(feed,))
        source_groups.sync_changed_rows(self.store,list(changed),self.store.wall_clock())
        for row in changed:
            self.store.problems.sync_media_problem(row['id'],previous.get(row['id'],[]),self.store.wall_clock())
        db.execute("UPDATE publications SET status = 'pending_collection' WHERE id IN (SELECT p.id FROM publications p WHERE p.feed_id=%s)",(feed,))

    def plan(self, feed: str, publications: list[PublicationContent]) -> dict[str, DiscoveryProjection]:
        """Prepare cache output before the short page acceptance transaction."""
        plans: dict[str, DiscoveryProjection] = {}
        for publication in publications:
            ref=publication.metadata.reference
            identity=ref.key.external_id
            members=tuple(publication.content.get('extra',{}).get('telegram',{}).get('members',()))
            described=publication if 'blocks' in publication.content else None
            if identity in plans:
                continue
            proposed_item=str(uuid4())
            checked = self.store.db.execute("""SELECT id,ARRAY[]::bigint[] AS source_jobs FROM publications
                WHERE platform=%s AND external_id=%s
                UNION ALL SELECT NULL::text,ARRAY(SELECT id FROM jobs WHERE item=%s AND reason_code LIKE 'source-%%')""",
                (ref.key.platform.value,ref.key.external_id,proposed_item)).fetchall()
            new_source_jobs=next(row['source_jobs'] for row in checked if row['id'] is None)
            existing=[row for row in checked if row['id'] is not None]
            if ref.key.platform.value == 'telegram' and members:
                overlap = self.store.db.execute("""SELECT i.id,i.platform,i.external_id FROM publications i
                    CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(i.content #> '{extra,telegram,members}','[]'::jsonb)) AS member(value)
                    WHERE i.feed_id=%s AND i.platform='telegram' AND i.id<>%s AND member.value=ANY(%s)
                    ORDER BY i.id LIMIT 1""", (feed,existing[0]['id'] if existing else '',[str(member) for member in members])).fetchone()
                if overlap:
                    raise PublicationIdentityConflict(publication_id=overlap['id'], old_platform=overlap['platform'],
                        old_external_id=overlap['external_id'], new_platform=ref.key.platform.value,
                        new_external_id=ref.key.external_id)
            if len(existing)>1:
                raise ValueError('Conflicting publication identity or Telegram album membership')
            item=existing[0]['id'] if existing else proposed_item
            plan=DiscoveryProjection(item)
            if not existing:plan.new_source_jobs=tuple(new_source_jobs)
            current=self.store.db.execute("""SELECT content,status,
                EXISTS (SELECT 1 FROM jobs j WHERE j.item=publications.id AND j.reason_code LIKE 'source-%%') OR EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=publications.id AND j.reason_code LIKE 'source-%%') OR EXISTS (SELECT 1 FROM publication_links l JOIN jobs j ON j.item=l.target_publication_id AND j.reason_code LIKE 'source-%%' WHERE l.source_publication_id=publications.id) OR EXISTS (SELECT 1 FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=publications.id AND j.reason_code LIKE 'source-%%') AS source_problem_exists,
                (EXISTS (SELECT 1 FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=publications.id AND (((m.resource_key='youtube:subtitles' OR j.media_null_owned) AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%'))) OR EXISTS (SELECT 1 FROM jobs j WHERE j.kind='download_media' AND j.item=publications.id AND ((j.media_null_owned AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.identity_event_owned AND j.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch'))))) AS media_problem_exists,
                ARRAY(SELECT DISTINCT m.publication_id FROM publication_links l JOIN media_files m ON m.publication_id=l.target_publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE l.source_publication_id=publications.id AND (((m.resource_key='youtube:subtitles' OR j.media_null_owned) AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%')) UNION SELECT j.item FROM publication_links l JOIN jobs j ON j.kind='download_media' AND j.item=l.target_publication_id WHERE l.source_publication_id=publications.id AND ((j.media_null_owned AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.identity_event_owned AND j.reason_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))) AS media_problem_children,
                EXISTS (SELECT 1 FROM jobs j WHERE j.kind IN ('filter','theses') AND j.item=publications.id AND (((j.reason_code='' OR (j.reason_code IS NULL AND j.reason_message IS NOT NULL))) OR (j.reason_code IS NOT NULL AND j.reason_code<>'' AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission')))) AS processing_problem_exists,
                ARRAY(SELECT DISTINCT j.item FROM publication_links l JOIN jobs j ON j.kind IN ('filter','theses') AND j.item=l.target_publication_id WHERE l.source_publication_id=publications.id AND (((j.reason_code='' OR (j.reason_code IS NULL AND j.reason_message IS NOT NULL))) OR (j.reason_code IS NOT NULL AND j.reason_code<>'' AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission')))) AS processing_problem_children,
                (SELECT j.id FROM jobs j WHERE j.kind='fetch' AND j.item=publications.id AND ((j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config')))) AS fetch_problem_job,
                ARRAY(SELECT j.item FROM LATERAL
                      (SELECT target_publication_id FROM publication_links WHERE source_publication_id=publications.id ORDER BY target_publication_id OFFSET 0) l
                      CROSS JOIN LATERAL (SELECT j.item FROM jobs j WHERE j.item=l.target_publication_id AND j.kind='fetch'
                          AND ((j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%' AND j.reason_code NOT IN ('codex-choice','ai-permission','youtube-admission','processor-config'))) OFFSET 0) j) AS fetch_problem_children
                FROM publications WHERE id = %s""",(item,)).fetchone() if existing else None
            if current:
                plan.fetch_problem_job = current['fetch_problem_job']
                plan.fetch_problem_children = tuple(current['fetch_problem_children'])
                plan.media_problem_exists = bool(current['media_problem_exists'])
                plan.media_problem_children = tuple(current['media_problem_children'])
                plan.processing_problem_exists = bool(current['processing_problem_exists'])
                plan.source_problem_exists = bool(current['source_problem_exists'])
                plan.processing_problem_children = tuple(current['processing_problem_children'])
            refresh=bool(current and described and
                ('blocks' not in current['content'] or current['status']=='pending_collection'))
            if described and (not existing or refresh):
                plan.content=self.prepare_content(item,publication)
            plans[identity]=plan
        return plans

    def prepare_content(self, item: str, value: PublicationContent, *, replace_all: bool = False) -> PreparedPublication:
        row=self.store.db.execute('SELECT title,content FROM publications WHERE id=%s',(item,)).fetchone()
        title=value.metadata.title if replace_all else value.metadata.title or (row['title'] if row else '')
        if not replace_all and not any(block.get('type')=='text' and block.get('role')=='description' for block in value.content.get('blocks',[])):
            previous=saved_content(row['content']) if row else {}
            descriptions=[deepcopy(block) for block in previous.get('blocks',[]) if block.get('type')=='text' and block.get('role')=='description']
            if descriptions:
                copied=deepcopy(value.content)
                copied.setdefault('blocks',[]).extend(descriptions)
                value=replace(value,content=copied)
        return self._prepare_adapter(item,value,title)

    def _prepare_adapter(self, item: str, value: PublicationContent, title: str) -> PreparedPublication:
        content=deepcopy(value.content)
        media=[]
        identities={}
        known={row['resource_key']:row for row in self.store.media.publication_rows(item)}
        for ref in value.media:
            previous=known.get(ref.key)
            subtitles=any(block.get('ref')=={'$media':ref.key} and block.get('role')=='subtitles' for block in content['blocks'])
            keep_caption=bool(subtitles and previous and previous['source_locator']==ref.locator and
                (previous['status']=='ready' and previous['storage_key'] and existing_file(self.store.root,previous['storage_key']) is not None or
                 previous.get('reader_job_status') in ('done','failed','unavailable')))
            if keep_caption and previous:
                identities[ref.key]=previous['id']
                media.append(previous)
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
                source_url=ref.source_url if ref.source_url is not None else previous.get('source_url') if unchanged and previous else None,
                name=ref.name or (previous['name'] if ready and previous else ''),mime_type=previous['mime_type'] if ready and previous else ref.mime_type,
                size_bytes=previous['size_bytes'] if ready and previous else ref.size_bytes,
                storage_key=previous['storage_key'] if ready and previous else None,
                status='ready' if ready else 'pending_download',reason='',resource_identity=ref.resource_identity))
        for block in content.get('blocks',[]):
            if block['type']=='media':
                key=block.pop('ref')['$media']
                block['media_id']=identities[key]
                selected=next(row for row in media if row['id']==block['media_id'])
                reference=next((ref for ref in value.media if ref.key==key),None)
                block['source_url']=selected.get('source_url')
                block['publication_url']=reference.publication_url if reference else None
            elif block['type']=='external_media' and 'poster_ref' in block:
                block['poster_media_id']=identities.get(block.pop('poster_ref'))
        return PreparedPublication(title,content,media,value.linked_publications)

    def publish(self, item: str, prepared: PreparedPublication, effects: CollectionDiagnostics, *, preserve_processing: bool = False) -> None:
        """Called in the accepting transaction, after durable media preparation."""
        content,media=prepared.content,prepared.media
        store = self.store
        old = expect_row(store.db.execute("""SELECT p.content,p.platform,p.title,p.status,p.feed_id,
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
        links_changed=False if preserve_processing else publication_links.sync(store,item,prepared.links,effects)
        content['blocks']=[block for block in content.get('blocks',[]) if block.get('type')!='publication']
        content['blocks'].extend(dict(type='publication',publication_id=row['id'],source_url=row['url'])
                                 for row in publication_links.children(store,item))
        previous = saved_content(old['content'])
        previous_title=old['title']
        same = not links_changed and publication_input.blocks(previous)==publication_input.blocks(content) and previous_title==prepared.title
        retained={row['id'] for row in media}
        removed=[row['id'] for row in store.db.execute('SELECT id FROM media_files WHERE publication_id = %s',(item,)) if row['id'] not in retained]
        if removed:
            store.db.execute("""INSERT INTO jobs(kind,item,status,payload)
                SELECT 'download_media',m.id,'cancelled',jsonb_build_object('retired_media_key',m.storage_key)
                FROM media_files m WHERE m.id=ANY(%s)
                ON CONFLICT(kind,item) DO UPDATE SET status='cancelled',generation=jobs.generation+1,
                  payload=jsonb_set(jobs.payload,'{retired_media_key}',excluded.payload->'retired_media_key')""",(removed,))
        processor_jobs=[row['id'] for row in store.db.execute("SELECT j.id FROM media_files m JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND j.processor_visible",(item,))]
        store.problems.capture_processor_histories(processor_jobs,store.wall_clock())
        store.db.execute('DELETE FROM media_files WHERE publication_id = %s', (item,))
        for row in media:
            if row['status']=='unavailable' and row.get('reader_code')!='subtitles_absent' and row.get('reason') and row.get('reader_reason')!=row['reason']:
                effects.unavailable_media[row['id']]=('media-unavailable',row['reason'])
            store.db.execute('INSERT INTO media_files ( id , publication_id , resource_key , source_locator , source_url , name , mime_type , size_bytes , storage_key , status , resource_identity ) VALUES ( %(id)s , %(publication_id)s , %(resource_key)s , %(source_locator)s , %(source_url)s , %(name)s , %(mime_type)s , %(size_bytes)s , %(storage_key)s , %(status)s , %(resource_identity)s )', row)
        effects.media.add(item)
        from oko.storage.publication_document import stored_content
        persisted=stored_content(content)
        store.db.execute('UPDATE publications SET content = %s , media_ids = %s , title = %s , status = %s WHERE id = %s',
            (json.dumps(persisted, ensure_ascii=False), json.dumps([r['id'] for r in media]),
             prepared.title, (old['status'] if old['feed_id'] is not None else 'ready') if preserve_processing else 'pending_filtering', item))
        if old['title']!=prepared.title:
            title_only=not links_changed and publication_input.blocks(previous)==publication_input.blocks(content)
            if title_only:effects.title_only_changes.add(item)
            effects.source_title_changes[item]=title_only
        from oko.storage.incomplete_index import save_class
        save_class(store.db,item,content)
        if not preserve_processing and previous and old['platform'] != 'youtube' and previous_title != prepared.title:
            metadata_row = expect_row(store.db.execute('SELECT processing_metadata FROM publications WHERE id = %s', (item,)).fetchone())
            metadata = json.loads(metadata_row[0])
            for operation in ('filter', 'summary'):
                if operation in metadata:
                    metadata[operation].setdefault('source_title', previous_title)
            store.db.execute('UPDATE publications SET processing_metadata=%s WHERE id=%s',
                             (json.dumps(metadata, ensure_ascii=False), item))
        if not preserve_processing and same and previous:
            filtered_row=expect_row(store.db.execute('SELECT filtered_content FROM publications WHERE id = %s',(item,)).fetchone())
            filtered=json.loads(filtered_row[0])
            if filtered:
                result=dict(hidden=filtered.get('hidden',False),reason=filtered['reason'],removed=[r['text'] for r in filtered['removed']])
                refreshed=publication_input.filtered(publication_input.content(store,item),item,result)
                store.db.execute('UPDATE publications SET filtered_content = %s WHERE id = %s',(json.dumps(refreshed,ensure_ascii=False),item))
        if not preserve_processing:self.reconcile(item)
        effects.parent_notifications[item]=item in effects.title_only_changes



    def provenance(self, item: str, kind: str) -> dict[str,Any] | None:
        row=material_read.item(self.store.db,item)
        metadata=json.loads(row['processing_metadata']).get('filter' if kind=='filter' else 'summary',{}) if row else {}
        if metadata.get('processor') is None:
            return None
        result={key:metadata[key] for key in ('processor','model','reasoning_effort','fast') if key in metadata}
        if metadata['processor']=='codex':
            parameters=metadata.get('parameters') or {}
            for key in ('reasoning_effort','fast'):
                if key not in result and key in parameters:result[key]=parameters[key]
        return result

    def accept_result(self, item: str, kind: str, input_hash: str, result: dict[str,Any], origin: dict[str,Any] | None, *, prompt: str | None = None) -> None:
        """Publish a validated result and its provenance in the caller's transaction."""
        row=expect_row(self.store.db.execute('SELECT processing_metadata, title, platform FROM publications WHERE id = %s',(item,)).fetchone())
        content=publication_input.content(self.store,item)
        metadata=json.loads(row[0])
        operation='filter' if kind=='filter' else 'summary'
        if origin and origin.get('processor')=='codex':
            origin={key:value for key,value in origin.items() if key not in ('reasoning_effort','fast')} | {
                'parameters':{'reasoning_effort':origin.get('reasoning_effort'),'fast':origin.get('fast')}}
        metadata[operation]=dict(input_hash=input_hash,input_hash_format='sources-v1',
            prompt=prompt,source_title=None if row['platform']=='youtube' else row['title'],
            completed_at=datetime.fromtimestamp(self.store.wall_clock(),timezone.utc).isoformat(),
            **({'processor':None,'model':None,'parameters':{}} | (origin or {})))
        if kind=='filter':
            filtered=publication_input.filtered(content,item,result)
            self.store.db.execute('UPDATE publications SET filtered_content = %s , status = %s WHERE id = %s',
                (json.dumps(filtered,ensure_ascii=False),'filter_out' if result['hidden'] else 'pending_summary',item))
        else:
            summarized=dict(points=[dict(text=p['text'],source_publication_ids=[r['id'] for r in p['sources']]) for p in result['points']])
            self.store.db.execute("UPDATE publications SET summarized_content = %s , status = 'ready' WHERE id = %s",(json.dumps(summarized,ensure_ascii=False),item))
        self.store.db.execute('UPDATE publications SET processing_metadata = %s WHERE id = %s',(json.dumps(metadata,ensure_ascii=False),item))

    def complete_empty(self, item: str, kind: str, input_hash: str) -> None:
        result=dict(hidden=False,removed=[],reason='Нет текста для обработки') if kind=='filter' else dict(points=[])
        self.accept_result(item,kind,input_hash,result,None)
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
        if not row or row['platform'] is None or 'blocks' not in row['content']:
            return None
        fetch=material_read.jobs(self.store.db,item).get('fetch')
        if row['status']=='pending_collection' and fetch and fetch['status'] in ('pending','running','failed','unavailable','blocked'):
            return None
        if row['feed_id'] is None:
            return 'ready' if row['status'] != 'ready' else None
        selected=publication_input.content(self.store,item)
        if publication_input.awaiting_input(self.store,item,selected) or publication_input.awaiting_linked_input(self.store,item,selected):
            return 'pending_collection' if row['status'] != 'pending_collection' else None
        filtered = self.store.filters.read(item)
        jobs={kind:row['status'] for kind,row in material_read.jobs(self.store.db,item).items() if kind in ('filter','theses')}
        if filtered['hidden']:
            status = 'filter_out'
        elif filtered['status'] not in ('off', 'done') or self.store.filters.input_hash(item) and jobs.get('filter') in ('pending','running'):
            status = 'pending_filtering'
        elif self.store.processing.theses.input_hash(item) and (publication_input.stage_enabled(self.store, item, 'theses') or jobs.get('theses') == 'running') and self.store.processing.theses.read(item) is None and json.loads(row['processing_metadata']).get('summary',{}).get('reason')!='no_input':
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
            current = publication_input.source_revision_hash(self.store,item) if row['feed_id'] is not None else None
            content = publication_input._reading_content(self.store,item) if 'blocks' in row['content'] else None
            available = bool(row['status']!='pending_collection' and content is not None and not publication_input.awaiting_input(self.store,item,content)
                             and not publication_input.awaiting_linked_input(self.store,item,content))
            source_hash = publication_input.digest(publication_input.blocks(content)) if available and content is not None else None
        metadata = json.loads(row['processing_metadata'])
        preserved = current is not None and self.store.db.execute("""SELECT 1 FROM jobs
            WHERE item=%s AND kind IN ('filter','theses')
            AND payload->>'preserved_source_revision'=%s LIMIT 1""", (item,current)).fetchone() is not None
        # Results carry the actual source blocks they were derived from. Keep
        # them when only settings change, but discard them on a confirmed input
        # change rather than retaining a hidden stale copy indefinitely.
        saved_filter = metadata.get('filter',{})
        saved_summary = metadata.get('summary',{})
        prior_source = saved_filter.get('input_hash')
        source_format=saved_filter.get('input_hash_format')
        if prior_source is None and row['filtered_content']=='{}':
            prior_source = saved_summary.get('input_hash')
            source_format=saved_summary.get('input_hash_format')
        if source_hash is not None and source_format=='sources-v1':
            source_hash=publication_input.stage_input_hash(self.store,item,'filter' if saved_filter else 'theses')
        saved_title = saved_filter.get('source_title', saved_summary.get('source_title'))
        title_changed = row['platform'] != 'youtube' and saved_title is not None and saved_title != row['title']
        stale_source=any(section.get('source_url_stale') or section.get('source_title_stale') for section in (saved_filter,saved_summary))
        if not row['viewed'] and not preserved and not any(section.get('preserve_stale') for section in (saved_filter,saved_summary)) and source_hash is not None and not stale_source and ((prior_source is not None and source_hash != prior_source) or title_changed):
            self.store.db.execute("UPDATE publications SET filtered_content='{}',summarized_content='{}' WHERE id=%s", (item,))
            metadata.pop('filter',None)
            metadata.pop('summary',None)
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
