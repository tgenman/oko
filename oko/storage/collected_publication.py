"""Persist one collected publication in the caller's transaction."""
from collections.abc import Callable
from pathlib import Path

from oko.collection.adapters import PublicationContent
from oko.collection.contracts import SourceError
from oko.domain.collection import CollectionEffects
from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.storage.collection_effects import CollectionEffectCapture
from oko.storage.database import Connection, expect_row
from oko.storage import memberships as membership_graph
from oko.storage.reader_status import changed as status_changed
from oko.storage.media import MediaFiles
from oko.storage.publications import Publications


class CollectedPublicationRepository:
    def __init__(self, *, db: Connection, root: Path, publications: Publications, media: MediaFiles,
                 capture: CollectionEffectCapture, collection_history: Callable[[str, bool, bool], None],
                 fault: Callable[[str], None]) -> None:
        self._db, self._root, self._publications, self._media = db, root, publications, media
        self._capture, self._collection_history, self._fault = capture, collection_history, fault

    def _unviewed(self, item: str) -> bool:
        return self._db.execute('SELECT 1 FROM publications WHERE id=%s AND viewed=0', (item,)).fetchone() is not None

    def accept_content(self, item: str, value: PublicationContent) -> tuple[SourceError | None,CollectionEffects]:
        try:
            return self._accept_content(item,value)
        except BaseException:
            if self._db.transaction_depth:self._db.transaction_failed=True
            raise

    def _accept_content(self, item: str, value: PublicationContent) -> tuple[SourceError | None,CollectionEffects]:
        from oko.collection.adapters import AdapterErrorKind,AdapterException,AdapterIssue
        from oko.collection.adapter_errors import source_error
        if not self._db.in_transaction or not self._db.transaction_depth:
            raise RuntimeError('Collection acceptance requires an owning transaction')
        effects=CollectionDiagnostics()
        if not self._unviewed(item):return None,self._capture.finish(effects,operation='fetch',target=item)
        issues=[issue for issue in value.issues if issue.kind!=AdapterErrorKind.notice]
        priority={AdapterErrorKind.source_wait:3,AdapterErrorKind.temporary:2}
        issue=max(issues,key=lambda candidate:(priority.get(candidate.kind,1),candidate.retry_at.timestamp() if candidate.retry_at else 0),default=None)
        if value.needs_fetch and issue is None:
            issue=AdapterIssue('publication-incomplete','Публикация ожидает сбора',AdapterErrorKind.temporary,value.metadata.reference)
        error=source_error(AdapterException(issue)) if issue else None
        prepared=self._publications.prepare_content(item,value)
        self._fault('after_file_move')
        membership_graph.lock_graph(self._db)
        if not self._unviewed(item):return None,self._capture.finish(effects,operation='fetch',target=item)
        self._publications.identify(item,value.metadata.reference,effects=effects)
        previous=expect_row(self._db.execute('SELECT content FROM publications WHERE id=%s',(item,)).fetchone())
        first='blocks' not in previous['content']
        old_text=[block.get('text','') for block in previous['content'].get('blocks',[]) if block.get('type')=='text' and block.get('role')=='body' and block.get('text')]
        new_text='\n'.join(block.get('text','') for block in prepared.content.get('blocks',[]) if block.get('type')=='text' and block.get('role')=='body')
        preserve=bool(error and old_text and any(text not in new_text for text in old_text))
        if not preserve:self._publications.publish(item,prepared,effects)
        if error or value.needs_fetch:
            self._db.execute("UPDATE publications SET status='pending_collection' WHERE id=%s",(item,))
        self._fault('collection_after_publication_write')
        if first:status_changed(self._db)
        effects.parent_notifications[item]=item in effects.title_only_changes
        external=any(block.get('type')=='external_media' for block in value.content.get('blocks',[]))
        self._collection_history(item,not value.needs_fetch and not value.media and not external,bool(error))
        effects.processing_items.add(item)
        effects.sync_processing=True
        effects.refresh_status.add(item)
        return error,self._capture.finish(effects,operation='fetch',target=item)
