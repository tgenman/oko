"""RSS metadata delivery through the common adapter, without persistence."""
from typing import Any
import json

from oko.collection.adapters import AdapterException, PublicationContent, ExternalKey, ExternalReference, Platform
from oko.collection.contracts import DiscoveryResult, FetchResult, Material, Publication, SourceError, SourceObservation
from oko.collection.rss import RSSSource
from oko.collection.rss_adapter import RSSAdapter
from oko.collection.adapter_errors import source_error


def material(value: PublicationContent) -> Material:
    text='\n\n'.join(block.get('text','') for block in value.content.get('blocks',[])
                     if block['type']=='quote' or block['type']=='text' and block.get('role')=='body')
    external=any(block['type']=='external_media' for block in value.content.get('blocks',[]))
    return Material(value.metadata.title,text,not value.needs_fetch and not value.media and not external,
        'External embed not saved locally' if external else 'Вложения ещё не загружены' if value.media else '',adapter_content=value)


def publication(value: PublicationContent) -> Publication:
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
                return DiscoveryResult((),json.dumps(state),SourceObservation(description=self.description),continuation=0)
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
            self.description=api.descriptions.get(self.url,self.description)
            state['description']=self.description
            codes={issue.code for issue in page.issues}
            state['warnings']=[issue.message for issue in page.issues]
            if first_pass and not page.has_more:
                state['stop_reason']=('request_failed' if 'rss-history-request-failed' in codes else
                    'ambiguous_continuation' if 'rss-history-ambiguous' in codes else 'cycle' if 'rss-history-cycle' in codes else 'no_continuation' if 'history-shortfall' in codes else 'count_reached')
            publications=tuple(publication(value) for value in page.publications)
            state['history_enroll']=[p.id for p in publications] if first_pass else []
            warnings=list(dict.fromkeys(issue.message for issue in page.issues))
            self.gap='; '.join(warnings) or None
            return DiscoveryResult(publications,json.dumps(state),
                SourceObservation(self.gap,self.changes,description=self.description),
                continuation=0 if page.has_more else None)
        except AdapterException as error:
            raise source_error(error)

    async def fetch(self, publication: Publication) -> FetchResult:
        if publication.adapter_reference is None:
            raise SourceError('Отсутствует внешний ключ RSS',retryable=False)
        try:
            value=await self.publication_adapter().fetch(publication.adapter_reference)
            return FetchResult(material(value),SourceObservation(description=self.description))
        except AdapterException as error:
            raise source_error(error)
