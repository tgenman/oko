"""Persist one collected publication in the caller's transaction."""
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import json

from oko.collection.adapters import Platform
from oko.collection.contracts import Material, SourceError, SourceManifest
from oko.domain.collection import CollectionEffects
from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.storage.collection_effects import CollectionEffectCapture
from oko.storage.database import Connection, expect_row
from oko.storage import memberships as membership_graph
from oko.storage import processing_input as publication_input
from oko.storage.media_paths import existing_file
from oko.storage.reader_status import changed as status_changed
from oko.storage.publication_document import stored_content
from oko.storage.reading import saved_content
from oko.storage.media import MediaFiles
from oko.storage.publications import Publications


class CollectedPublicationRepository:
    def __init__(self, *, db: Connection, root: Path, publications: Publications, media: MediaFiles,
                 capture: CollectionEffectCapture, collection_history: Callable[[str, Material], None],
                 fault: Callable[[str], None]) -> None:
        self._db, self._root, self._publications, self._media = db, root, publications, media
        self._capture, self._collection_history, self._fault = capture, collection_history, fault

    def _unviewed(self, item: str) -> bool:
        return self._db.execute('SELECT 1 FROM publications WHERE id=%s AND viewed=0', (item,)).fetchone() is not None

    def accept(self, item: str, material: Material, *, manual: bool = False) -> tuple[SourceError | None,CollectionEffects]:
        try:
            return self._accept(item,material,manual=manual)
        except BaseException:
            if self._db.transaction_depth:
                self._db.transaction_failed = True
            raise

    def _accept(self, item: str, material: Material, *, manual: bool = False) -> tuple[SourceError | None,CollectionEffects]:
        if not self._db.in_transaction or not self._db.transaction_depth:
            raise RuntimeError('Collection acceptance requires an owning transaction')
        effects=CollectionDiagnostics()
        if not self._unviewed(item):
            return None,self._capture.finish(effects,operation='fetch',target=item)
        if material.source_manifest and material.source_manifest.post_web_unavailable:
            media=self._media.publication_rows(item)
            missing=[row for row in media if row['storage_key'] and existing_file(self._root,row['storage_key']) is None or row['status']=='unavailable' and row['reason']=='Локальный файл вложения недоступен']
            if missing:
                error=SourceError('Локальный файл вложения недоступен',code='media-unavailable',retryable=False)
                content=saved_content(expect_row(self._db.execute('SELECT content FROM publications WHERE id = %s',(item,)).fetchone())[0],mutable=True)
                content['extra']['_reader']['source_manifest']['post_web_unavailable']=True
                for row in missing:
                    row.update(status='unavailable',reason=error.label,storage_key=None)
                self._media.project_header(content,media)
                membership_graph.lock_graph(self._db)
                for row in missing:
                    self._db.execute("UPDATE media_files SET status = 'unavailable' , storage_key = NULL WHERE id = %s",(row['id'],))
                    effects.unavailable_media[row['id']]=('media-unavailable',error.label)
                self._db.execute('UPDATE publications SET content = %s WHERE id = %s',(json.dumps(stored_content(content),ensure_ascii=False),item))
                from oko.storage.incomplete_index import save_class
                save_class(self._db,item,content)
                self._collection_history(item,replace(material,collection_error=error))
                return error,self._capture.finish(effects,operation='fetch',target=item)
        if material.source_manifest and material.source_manifest.post_web_unavailable:
            saved=self._db.execute('SELECT content FROM publications WHERE id=%s', (item,)).fetchone()
            retained_header, retained_text = self._publications.original(item) if saved and saved['content'] else ({}, '')
            raw_manifest=retained_header.get('source_manifest')
            manifest=SourceManifest.model_validate_json(json.dumps(raw_manifest)) if raw_manifest else None
        else:
            manifest=None
        if manifest is not None:
            observed_manifest=material.source_manifest
            assert observed_manifest is not None
            accept_preview = (observed_manifest.preview_fragment is not None
                and (manifest.preview_fragment is not None or
                     manifest.post.get('text') == observed_manifest.post.get('text')
                     and all(r.status == 'unsupported' and r.sha256 is None
                         and r.reason == 'Пост не поддерживается публичным предпросмотром' for r in manifest.resources)))
            if (retained_text.strip() or any(resource.status=='loaded' for resource in manifest.resources)) and not accept_preview:
                material = Material(retained_header.get('title',''), False,
                    retained_header.get('reason',''), members=material.members,
                    captions=retained_header.get('captions'), description=retained_header.get('description',''),
                    source_manifest=manifest.model_copy(update={'post_web_unavailable':True}),
                    collection_error=material.collection_error)
        old_media={row['id']:row for row in self._media.publication_rows(item)} if manual else {}
        prepared=self._publications.prepare(item,material)
        fingerprint=publication_input.digest(prepared[0])
        self._fault('after_file_move')
        membership_graph.lock_graph(self._db)
        if not self._unviewed(item):
            return None,self._capture.finish(effects,operation='fetch',target=item)
        if material.adapter_content and material.adapter_content.metadata.reference.key.platform==Platform.telegram:
            self._publications.identify(item,material.adapter_content.metadata.reference,effects=effects)
        previous=expect_row(self._db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone())
        item_status_changed=not bool(previous['content'])
        self._publications.publish(item,prepared,fingerprint,effects)
        self._fault('collection_after_publication_write')
        if material.members:
            item_status_changed |= bool(self._db.execute('UPDATE publications SET members = %s WHERE id = %s AND members IS DISTINCT FROM %s',(json.dumps(material.members),item,json.dumps(material.members))).rowcount)
        if item_status_changed:
            status_changed(self._db)
        effects.parent_notifications[item] = item in effects.title_only_changes
        for row in prepared[1]:
            old=old_media.get(row['id'])
            if row['status']=='ready' and old and not (row['resource_identity'] is not None and old['source_locator']!=row['source_locator']):
                effects.revalidate[row['id']]=True
        self._collection_history(item,material)
        effects.processing_items.add(item)
        effects.sync_processing = True
        effects.refresh_status.add(item)

        return material.collection_error,self._capture.finish(effects,operation='fetch',target=item)
