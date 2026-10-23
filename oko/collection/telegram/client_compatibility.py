"""Temporary reader projection of channel client metadata and external cursors."""
import json
from typing import Any

from oko.collection.adapters import AdapterException,ExternalKey,ExternalReference,Platform
from oko.collection.adapter_errors import source_error
from oko.collection.contracts import DiscoveryResult,FetchResult,FetchResources,FetchRequirements,Publication,SourceObservation,SourceIdentity
from oko.collection.telegram.source import TelegramSource
from oko.collection.telegram.client_adapter import TelegramClientAdapter
from oko.collection.telegram.compatibility import material
from oko.collection.telegram.contracts import HistoryEnvelope,HistorySelection
from oko.collection.telegram.web import WebTelegram


class TelegramClientSource(TelegramSource):
    fetch_requirements=FetchRequirements()

    def publication_adapter(self) -> TelegramClientAdapter:
        if not hasattr(self,'_api'):
            if isinstance(self.client,WebTelegram):
                raise ValueError('Native Telegram client required')
            self._api=TelegramClientAdapter(self.client,self.account,self.peer)
        return self._api

    def _observation(self) -> SourceObservation:
        self.identity_info=self.publication_adapter().info
        return SourceObservation(self.gap,self.changes,SourceIdentity.from_info(self.identity_info),self.identity_info.get('description'))

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        api=self.publication_adapter()
        previous=HistoryEnvelope.decode(cursor,adapter='client') if cursor else None
        state: dict[str,Any]=dict(previous.position) if previous else {}
        selection=self.initial_history or {}
        run=selection.get('run','initial')
        def saved(enrolled: tuple[str,...] = (), warnings: tuple[str,...] = ()) -> str:
            return HistoryEnvelope(adapter='client',position=state,selection=HistorySelection(since=0,
                enrolled_ids=enrolled,scan_complete=state.get('scan_complete',False),warnings=warnings,
                paused=bool(selection.get('paused')))).encode()
        try:
            if selection.get('paused') and not state.get('initial_complete',False):
                return DiscoveryResult((),saved())
            if 'telegram_client_adapter' not in state or state.get('run')!=run:
                metadata=await api.resolve_feed('https://t.me/'+(self.username or str(self.peer)))
                state=dict(telegram_client_adapter=metadata.latest_cursor,run=run,count=selection.get('count') or 1,locating=True)
                return DiscoveryResult((),saved(),self._observation(),continuation=.1)
            feed=ExternalReference(ExternalKey(Platform.telegram,str(self.peer)),api._url())
            if state.get('locating'):
                state['telegram_client_adapter']=await api.cursor_before(feed,state['telegram_client_adapter'],state['count']-1)
                state['locating']=False
            page=await api.discover(feed,state['telegram_client_adapter'],50)
            publications=[]
            for value in page.publications:
                extra=value.content['extra']['telegram']
                identity='album:'+str(extra['album_id']) if extra.get('album_id') else str(extra['members'][0])
                publications.append(Publication(identity,value.metadata.reference.url,
                    value.metadata.published_at.timestamp() if value.metadata.published_at else 0,material(value),
                    tuple(extra['members']),published_known=value.metadata.published_at is not None,adapter_reference=value.metadata.reference))
            state.update(telegram_client_adapter=page.next_cursor,scan_complete=not page.has_more,
                history_enroll=[p.id for p in publications] if not state.get('initial_complete') else [],
                warnings=[issue.message for issue in page.issues])
            if not page.has_more:
                state['initial_complete']=True
            self.gap='; '.join(state['warnings']) or None
            return DiscoveryResult(tuple(publications),saved(tuple(state['history_enroll']),tuple(state['warnings'])),self._observation(),continuation=.1 if page.has_more else None)
        except AdapterException as error:
            raise source_error(error)

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        try:
            ref=publication.adapter_reference or ExternalReference(ExternalKey(Platform.telegram,publication.url),publication.url)
            value=await self.publication_adapter().fetch(ref)
            return FetchResult(material(value,publication.teaser),self._observation())
        except AdapterException as error:
            raise source_error(error)
