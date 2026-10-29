"""Saved processing inputs and results, without execution or task ownership."""
import json
from typing import Any, Literal, TYPE_CHECKING
from dataclasses import dataclass
from oko.storage import processing_input as publication_input
from oko.processing.contracts import ProcessingRequest, FilterResult, Theses
from oko.processing.llamacpp import ProcessorConfig
from oko.processing.execution import CodexSettings
if TYPE_CHECKING:
    from oko.storage.store import Store

from oko.domain.processing import Kind,ProcessingInput,ProcessingPreparation


class ProcessingRepository:
    def __init__(self, store: 'Store') -> None:
        self.store=store

    def candidates(self, kind: Kind, *, limit: int | None = None, item: str | None = None) -> list[tuple[str,str | None]]:
        query='SELECT i . id FROM processing_stage_dirty d JOIN items_read i ON i . id = d . item WHERE d . kind = %s AND i . viewed = 0 AND i . platform IS NOT NULL AND i . feed_id IS NOT NULL AND i . complete = 1 AND NOT EXISTS ( SELECT 1 FROM processing_dirty p WHERE p . item = i . id )'
        parameters: tuple[str | int, ...] = (kind,)
        if item is not None:
            query += ' AND i . id = %s'
            parameters += (item,)
        if limit is not None:
            query += ' ORDER BY i . id LIMIT %s'
            parameters += (limit,)
        rows=self.store.db.execute(query,parameters).fetchall()
        operation=self.store.filters if kind=='filter' else self.store.processing.theses
        return [(row[0],operation.signature(row[0])) for row in rows]

    def prepare(self, item: str, kind: Kind) -> ProcessingPreparation:
        row=self.store.db.execute('SELECT * FROM items_read WHERE id = %s AND viewed = 0',(item,)).fetchone()
        if row is None or row['feed_id'] is None:return ProcessingPreparation('disabled',reason='not_eligible')
        enabled = publication_input.stage_enabled(self.store, item, kind)
        if not enabled:return ProcessingPreparation('disabled',reason='stage_disabled')
        selected=publication_input.content(self.store,item)
        if not row['complete'] or row['content']=='{}' or publication_input.awaiting_input(selected) or publication_input.awaiting_linked_input(self.store,item,selected):
            return ProcessingPreparation('waiting',reason='input_pending')
        if kind=='theses':
            filtered=self.store.filters.read(item)
            if filtered['hidden']:return ProcessingPreparation('disabled',reason='filter_out')
            if filtered['status'] not in ('off','done'):return ProcessingPreparation('waiting',reason='filter_pending')
        publication_input.snapshot(self.store,item)
        operation=self.store.filters if kind=='filter' else self.store.processing.theses
        prepared=operation.request(item)
        if prepared is None:return ProcessingPreparation('waiting',reason='input_not_reconciled')
        request,signature,public_ai=prepared
        value=ProcessingInput(item,kind,signature,publication_input.revision(self.store,item),request,public_ai,
            not any(source.text.strip() or source.reply_preview and source.reply_preview.text.strip() for source in request.sources))
        return ProcessingPreparation('no_input' if value.no_input else 'model',value)

    def current(self, value: ProcessingInput) -> bool:
        operation=self.store.filters if value.kind=='filter' else self.store.processing.theses
        return value.signature==operation.signature(value.item) and value.revision==publication_input.revision(self.store,value.item)

    def apply(self, value: ProcessingInput, result: FilterResult | Theses | None, origin: dict[str,Any] | None) -> None:
        if value.no_input:
            self.store.publications.complete_empty(value.item,value.kind,value.signature)
        else:
            assert result is not None
            self.store.publications.accept_result(value.item,value.kind,value.signature,result.model_dump(),origin)
        if value.kind=='filter':self.store.read_versions.sync_filter(value.item,bool(self.store.filters.read(value.item)['hidden']))

    def satisfied(self, item: str, kind: Kind) -> bool:
        return self.store.filters.read(item)['status']=='done' if kind=='filter' else self.store.processing.theses.read(item) is not None

    def processor_config(self) -> ProcessorConfig | None:
        return self.store.processor_config()

    def codex_config(self) -> CodexSettings | None:
        return self.store.codex_config()

    def refresh(self, item: str) -> None:
        self.store.publications.status(item)

    def regenerate(self, item: str, kind: Kind) -> bool:
        row=self.store.db.execute('SELECT processing_metadata FROM items_read WHERE id = %s',(item,)).fetchone()
        if row and json.loads(row[0]).get('filter' if kind=='filter' else 'summary',{}).get('reason')=='no_input':return False
        self.store.db.execute('UPDATE items SET status = %s WHERE id = %s',('pending_filtering' if kind=='filter' else 'pending_summary',item))
        return True

    def revision(self, item: str) -> str:
        return publication_input.revision(self.store,item)
