"""Atomic acceptance of a complete, separately prepared replacement set."""
from collections.abc import Callable
from typing import Any
from oko.collection.rebuild_types import PreparedReplacement
from oko.domain.collection import CollectionEffects
from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.storage.collection_effects import CollectionEffectCapture
from oko.storage.database import Connection
from oko.storage.publications import Publications
from oko.storage import memberships
from oko.storage.job_writes import execute as job_write
from oko.storage.processing_input import source_revision_hash
from oko.storage.reading import reading_snapshot


class RebuildRepository:
    def __init__(self, *, db: Connection, publications: Publications,
                 capture: CollectionEffectCapture, clock: Callable[[],float], fault: Callable[[str],None]) -> None:
        self._db,self._publications,self._capture,self._clock,self._fault=db,publications,capture,clock,fault

    def accept(self, item: str, replacements: tuple[PreparedReplacement,...]) -> CollectionEffects:
        if not self._db.in_transaction or not self._db.transaction_depth:
            raise RuntimeError('Rebuild acceptance requires an owning transaction')
        if not replacements or replacements[0].identity!=item:
            raise ValueError('Rebuild root does not match the publication')
        memberships.lock_graph(self._db)
        effects=CollectionDiagnostics()
        for replacement in replacements:
            ref=replacement.value.metadata.reference
            row=self._db.execute('SELECT id,url FROM publications WHERE platform=%s AND external_id=%s FOR UPDATE',
                (ref.key.platform.value,ref.key.external_id)).fetchone()
            if row is None:
                if replacement.identity==item:raise ValueError('Rebuild root disappeared')
                self._db.execute('INSERT INTO publications(id,url,platform,external_id,discovered_at) VALUES (%s,%s,%s,%s,%s)',
                    (replacement.identity,ref.url,ref.key.platform.value,ref.key.external_id,self._clock()))
            elif row['id']!=replacement.identity:
                raise ValueError('Linked publication identity changed during rebuild')
            self._publications.identify(replacement.identity,ref,effects=effects)
        # Publish children first: the root is reconciled only after their new
        # content exists. Shared parents are not invalidated or reprocessed.
        for replacement in (*replacements[1:],replacements[0]):
            prepared=self._publications.prepare_content(replacement.identity,replacement.value,replace_all=True)
            files={value.key:value for value in replacement.files}
            if {row['resource_key'] for row in prepared.media}!=set(files):
                raise ValueError('Rebuild media set changed')
            mapping={}
            for media_row in prepared.media:
                value=files[media_row['resource_key']]
                mapping[media_row['id']]=value.identity
                media_row.update(id=value.identity,storage_key=value.storage_key,size_bytes=value.size,
                    status='unavailable' if value.issue else 'ready',source_locator=value.media.locator,
                    source_url=value.media.source_url,mime_type=value.media.mime_type or media_row['mime_type'],
                    name=value.media.name or media_row['name'],reason=value.issue.message if value.issue else '',
                    reader_code='subtitles_absent' if value.issue else None,
                    reader_reason=value.issue.message if value.issue else None,
                    reader_job_status='done')
            for block in prepared.content.get('blocks',[]):
                if block.get('media_id') in mapping:block['media_id']=mapping[block['media_id']]
                if block.get('poster_media_id') in mapping:block['poster_media_id']=mapping[block['poster_media_id']]
                if block.get('type')=='media':
                    source=next((f for f in replacement.files if f.identity==block.get('media_id')),None)
                    if source:
                        block['source_url']=source.media.source_url
                        block['publication_url']=source.media.publication_url
            self._publications.publish(replacement.identity,prepared,effects,preserve_processing=replacement.identity!=item)
            for value in replacement.files:
                job_write(self._db,"""INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('download_media',%s,'done',%s,%s)
                    ON CONFLICT(kind,item) DO UPDATE SET status='done',generation=jobs.generation+1,
                      reason_code=excluded.reason_code,reason_message=excluded.reason_message,next=0,retry_at=NULL""",
                    (value.identity,'subtitles_absent' if value.issue else None,value.issue.message if value.issue else None))
            if replacement.identity!=item:
                effects.parent_notifications.pop(replacement.identity,None)
                effects.source_title_changes.pop(replacement.identity,None)
                effects.source_url_changes.discard(replacement.identity)
                effects.processing_items.discard(replacement.identity)
                effects.invalidated_fetch.add(replacement.identity)
                effects.fetch.pop(replacement.identity,None)
        # A shared article may change without invalidating another owner's
        # accepted result. Persist the exact observed input in that owner's Job
        # so startup reconciliation makes the same decision.
        children=[value.identity for value in replacements[1:]]
        if children:
            parents=self._db.execute('''SELECT DISTINCT source_publication_id FROM publication_links
                WHERE target_publication_id=ANY(%s) AND source_publication_id<>%s''',(children,item)).fetchall()
            for parent in parents:
                identity=parent['source_publication_id']
                with reading_snapshot():
                    revision=source_revision_hash(self._publications.store,identity)
                job_write(self._db,"""UPDATE jobs SET payload=jsonb_set(payload,
                    '{preserved_source_revision}',to_jsonb(%s::text)),
                    generation=generation+CASE WHEN status IN ('pending','running') THEN 1 ELSE 0 END,
                    status=CASE WHEN status IN ('pending','running') THEN 'cancelled' ELSE status END
                    WHERE item=%s AND kind IN ('filter','theses') AND EXISTS (
                        SELECT 1 FROM publications p WHERE p.id=jobs.item
                        AND (p.filtered_content<>'{}' OR p.summarized_content<>'{}'))""",(revision,identity))
        effects.processing_items.add(item)
        effects.refresh_status.add(item)
        self._fault('rebuild_before_commit')
        return self._capture.finish(effects,operation='rebuild',target=item)
