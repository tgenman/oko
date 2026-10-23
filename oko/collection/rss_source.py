"""RSS metadata delivery through the common adapter, without persistence."""
from typing import Any
import json

from oko.collection.adapters import AdapterException, PublicationContent, ExternalKey, ExternalReference, Platform
from oko.collection.contracts import (DiscoveryResult, FetchResources, FetchResult, Material,
    Publication, SourceError, SourceObservation)
from oko.collection.rss import RSSSource
from oko.collection.rss_adapter import RSSAdapter
from oko.collection.adapter_errors import source_error


def material(value: PublicationContent) -> Material:
    text='\n\n'.join(block.get('text','') for block in value.content.get('blocks',[])
                     if block['type']=='quote' or block['type']=='text' and block.get('role')=='body')
    return Material(value.metadata.title,text,not value.needs_fetch and not value.media,
        'Вложения ещё не загружены' if value.media else '',adapter_content=value)


def legacy(value: PublicationContent) -> Publication:
    metadata=value.metadata
    return Publication(metadata.reference.key.external_id,metadata.reference.url,
        metadata.published_at.timestamp() if metadata.published_at else 0,
        material(value) if value.content else None,published_known=metadata.published_at is not None,
        adapter_reference=metadata.reference)


class RSSAdapterSource(RSSSource):
    def publication_adapter(self) -> RSSAdapter:
        if not hasattr(self,'_api'):
            self._api=RSSAdapter(self.transport)
        return self._api

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        api=self.publication_adapter()
        state: dict[str,Any]=json.loads(cursor) if cursor else {}
        selection=self.initial_history or {}
        run=selection.get('run','initial')
        try:
            if selection.get('cancelled') and not state.get('initial_complete',False):
                state.update(rss_adapter='',run=run,initial_complete=True,locating=False,scan_complete=True,history_enroll=[])
                return DiscoveryResult((),json.dumps(state),SourceObservation(description=self.description))
            if selection.get('paused') and not state.get('initial_complete',False):
                return DiscoveryResult((),cursor or json.dumps(state),SourceObservation(description=self.description))
            if 'rss_adapter' not in state or state.get('run')!=run:
                metadata=await api.resolve_feed(self.url)
                self.description=metadata.description
                state=dict(rss_adapter=metadata.latest_cursor,locating=True,description=self.description,
                           run=run,count=selection.get('count') or 1)
                return DiscoveryResult((),json.dumps(state),SourceObservation(description=self.description),continuation=.1)
            self.description=state.get('description','')
            metadata_ref=ExternalReference(ExternalKey(Platform.rss,self.url),self.url)
            if state.get('locating'):
                count=state['count']
                state['rss_adapter']=await api.cursor_before(metadata_ref,state['rss_adapter'],count-1)
                state['locating']=False
            page=await api.discover(metadata_ref,state['rss_adapter'],50)
            state['rss_adapter']=page.next_cursor
            first_pass=not state.get('initial_complete',False)
            state['initial_complete']=not page.has_more or not first_pass
            state['scan_complete']=not page.has_more
            publications=tuple(legacy(value) for value in page.publications)
            state['history_enroll']=[p.id for p in publications] if first_pass else []
            warnings=list(dict.fromkeys(issue.message for issue in page.issues))
            self.gap='; '.join(warnings) or None
            return DiscoveryResult(publications,json.dumps(state),
                SourceObservation(self.gap,self.changes,description=self.description),
                continuation=.1 if page.has_more else None)
        except AdapterException as error:
            raise source_error(error)

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        if publication.adapter_reference is None:
            raise SourceError('Отсутствует внешний ключ RSS',retryable=False)
        try:
            value=await self.publication_adapter().fetch(publication.adapter_reference)
            return FetchResult(material(value),SourceObservation(description=self.description))
        except AdapterException as error:
            raise source_error(error)
