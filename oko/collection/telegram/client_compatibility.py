"""Temporary reader projection of channel client metadata and external cursors."""
from dataclasses import replace
from typing import Any

from oko.collection.adapters import AdapterException,AdapterIssue,AdapterErrorKind,ExternalKey,ExternalReference,Platform,PublicationContent
from oko.collection.adapter_errors import source_error
from oko.collection.contracts import DiscoveryResult,FetchResult,FetchResources,FetchRequirements,Publication,SourceObservation,SourceIdentity,Material
from oko.collection.telegram.source import TelegramSource
from oko.collection.telegram.client_adapter import TelegramClientAdapter,encode,publication_reference,finish_window
from oko.collection.telegram.compatibility import material
from oko.collection.telegram.contracts import HistoryEnvelope,HistorySelection
from oko.collection.telegram.web import WebTelegram


def legacy_cursor(cursor: str, peer: int, selection: dict[str,Any]) -> str:
    if not cursor:
        return ''
    previous=HistoryEnvelope.decode(cursor,adapter='client')
    after=previous.position.get('after',0)
    complete=previous.selection.scan_complete
    upper=previous.position.get('upper',after) or after
    if type(after) is not int or type(upper) is not int or after<0 or upper<0:
        raise ValueError('Invalid legacy Telegram cursor boundary')
    token=encode(peer,after+1,after,done=True) if complete else encode(peer,upper,upper)
    return HistoryEnvelope(adapter='client',selection=previous.selection,position=dict(
        telegram_client_adapter=token,run=selection.get('run','initial'),count=selection.get('count') or 1,
        locating=not complete,initial_complete=complete,scan_complete=complete)).encode()


def projected(value: PublicationContent, previous: Material | None = None) -> Material:
    result=material(value,previous)
    reason='; '.join(dict.fromkeys([part for part in [result.reason,*[issue.message for issue in value.issues if issue.kind.value!='notice']] if part]))
    issue=next((issue for issue in value.issues if issue.media_key is None and issue.kind.value!='notice'),None)
    return replace(result,reason=reason,full=not reason and not value.media,
                   collection_error=source_error(AdapterException(issue)) if issue else None)


class TelegramClientSource(TelegramSource):
    fetch_requirements=FetchRequirements()

    def publication_adapter(self) -> TelegramClientAdapter:
        if not hasattr(self,'_api'):
            if isinstance(self.client,WebTelegram):
                raise ValueError('Native Telegram client required')
            self._api=TelegramClientAdapter(self.client,self.account,self.peer)
        return self._api

    async def resolve(self) -> None:
        await self.publication_adapter()._resolve()
        self.identity_info=self.publication_adapter().info

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
                oldest=state.get('oldest'),
                paused=bool(selection.get('paused')))).encode()
        try:
            if selection.get('cancelled') and not state.get('initial_complete',False):
                state.update(telegram_client_adapter=finish_window(self.peer,state.get('telegram_client_adapter','')),
                    run=run,initial_complete=True,locating=False,scan_complete=True)
                return DiscoveryResult((),saved())
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
                    value.metadata.published_at.timestamp() if value.metadata.published_at else 0,projected(value),
                    tuple(extra['members']),published_known=value.metadata.published_at is not None,adapter_reference=value.metadata.reference))
            state.update(telegram_client_adapter=page.next_cursor,scan_complete=not page.has_more,
                history_enroll=[p.id for p in publications] if not state.get('initial_complete') else [],
                warnings=[issue.message for issue in page.issues])
            if not state.get('initial_complete') and publications:
                oldest=min(min(p.members) for p in publications)
                state['oldest']=min(state.get('oldest') or oldest,oldest)
            if not page.has_more:
                state['initial_complete']=True
            self.gap='; '.join(state['warnings']) or None
            return DiscoveryResult(tuple(publications),saved(tuple(state['history_enroll']),tuple(state['warnings'])),self._observation(),continuation=.1 if page.has_more else None)
        except AdapterException as error:
            raise source_error(error)

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        try:
            # This feed's verified native peer and saved message membership are
            # the proof; an old public username may have changed since collection.
            anchor=min(publication.members) if publication.members else int(publication.url.rstrip('/').rsplit('/',1)[-1])
            ref=publication_reference(self.peer,anchor)
            value=await self.publication_adapter().fetch(ref)
            if set(publication.members)-set(value.content['extra']['telegram']['members']):
                raise source_error(AdapterException(AdapterIssue('telegram-album-unavailable',
                    'Часть альбома недоступна в Telegram; показана сохранённая копия',AdapterErrorKind.unavailable,ref)))
            return FetchResult(projected(value,publication.teaser),self._observation())
        except AdapterException as error:
            raise source_error(error)
