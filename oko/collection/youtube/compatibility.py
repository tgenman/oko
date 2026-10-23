"""Temporary reader projection of the common YouTube adapter."""
from dataclasses import replace
import json
from typing import Any

from oko.collection.adapters import AdapterException,PublicationContent
from oko.collection.adapter_errors import source_error
from oko.collection.contracts import (DiscoveryResult,FetchResources,FetchResult,FetchRequirements,
    Material,Publication,SourceObservation)
from oko.collection.youtube.adapter import YouTubeAdapter,feed_reference,video_reference
from oko.collection.youtube.source import YouTubeSource


class YouTubeAdapterSource(YouTubeSource):
    fetch_requirements=FetchRequirements()

    def publication_adapter(self) -> YouTubeAdapter:
        if not hasattr(self,'_api'):
            self._api=YouTubeAdapter(self.captions_client,self.transport,self.history_client)
        return self._api

    def _material(self, value: PublicationContent, previous: Material | None = None) -> Material:
        description=value.content['extra']['youtube'].get('description',previous.description if previous else '')
        if not self.want_captions:
            content=json.loads(json.dumps(value.content))
            content['extra']['youtube']['captions']='not_requested'
            value=replace(value,content=content)
        return Material(value.metadata.title or (previous.title if previous else ''),'',False,
            'Субтитры ещё не загружены' if self.want_captions else 'Субтитры не запрошены',
            captions='error' if self.want_captions else 'not_requested',description=description,adapter_content=value)

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        api=self.publication_adapter()
        state: dict[str,Any]=json.loads(cursor) if cursor else {}
        selection=self.initial_history or {}
        run=selection.get('run','initial')
        try:
            if selection.get('cancelled') and not state.get('initial_complete',False):
                state.update(youtube_adapter='',run=run,initial_complete=True,locating=False,scan_complete=True,history_enroll=[])
                return DiscoveryResult((),json.dumps(state))
            if selection.get('paused') and not state.get('initial_complete',False):
                return DiscoveryResult((),cursor or json.dumps(state))
            if 'youtube_adapter' not in state or state.get('run')!=run:
                metadata=await api.resolve_feed(self.url)
                state=dict(youtube_adapter=metadata.latest_cursor,run=run,count=selection.get('count') or 1,locating=True)
                return DiscoveryResult((),json.dumps(state),continuation=.1)
            feed=feed_reference(self.url)
            if state.get('locating'):
                state['youtube_adapter']=await api.cursor_before(feed,state['youtube_adapter'],state['count']-1)
                state['locating']=False
            page=await api.discover(feed,state['youtube_adapter'],50)
            state.update(youtube_adapter=page.next_cursor,scan_complete=not page.has_more,
                         history_enroll=[value.metadata.reference.key.external_id for value in page.publications]
                         if not state.get('initial_complete') else [])
            retry=any(issue.kind.value=='temporary' for issue in page.issues)
            state.update(history_waiting=retry,history_error='; '.join(issue.message for issue in page.issues) if retry else None)
            if not page.has_more:
                state['initial_complete']=True
            publications=tuple(Publication(value.metadata.reference.key.external_id,value.metadata.reference.url,
                value.metadata.published_at.timestamp() if value.metadata.published_at else 0,self._material(value),
                published_known=value.metadata.published_at is not None,adapter_reference=value.metadata.reference) for value in page.publications)
            self.gap='; '.join(dict.fromkeys(issue.message for issue in page.issues)) or None
            return DiscoveryResult(publications,json.dumps(state),SourceObservation(self.gap,self.changes),
                                   continuation=(60 if retry else .1) if page.has_more else None)
        except AdapterException as error:
            raise source_error(error)

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        try:
            value=await self.publication_adapter().fetch(publication.adapter_reference or video_reference(publication.id))
            return FetchResult(self._material(value,publication.teaser),SourceObservation(self.gap,self.changes))
        except AdapterException as error:
            raise source_error(error)
