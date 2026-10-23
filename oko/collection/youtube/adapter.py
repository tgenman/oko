"""YouTube metadata and file streams through the common external adapter API."""
from dataclasses import replace
from datetime import datetime,timezone
import json
import re
from typing import Any,BinaryIO
from urllib.parse import parse_qs,urlsplit

import httpx

from oko.collection.adapter_errors import failure
from oko.collection.adapters import (AdapterErrorKind,AdapterException,AdapterIssue,DiscoveryPage,
    ExternalKey,ExternalReference,FeedMetadata,MediaReference,Platform,PublicationContent,PublicationMetadata)
from oko.collection.contracts import CaptionsClient,SourceError,Publication
from oko.collection.rss import Entry,RSSSource,entries
from oko.collection.youtube.history import HistoryClient,LocalHistory


def video_reference(video: str) -> ExternalReference:
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video):
        raise AdapterException(AdapterIssue('youtube-invalid-reference','Неверный ID YouTube',AdapterErrorKind.permanent))
    return ExternalReference(ExternalKey(Platform.youtube,video),'https://www.youtube.com/watch?v='+video)


def feed_reference(url: str) -> ExternalReference:
    try:
        parsed=urlsplit(url)
        channels=parse_qs(parsed.query).get('channel_id',[])
        if (parsed.scheme not in ('https','http') or parsed.hostname not in ('www.youtube.com','youtube.com')
                or parsed.username or parsed.password or parsed.port or parsed.path!='/feeds/videos.xml'
                or len(channels)!=1 or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',channels[0])):
            raise ValueError('feed')
        channel=channels[0]
        return ExternalReference(ExternalKey(Platform.youtube,channel),'https://www.youtube.com/feeds/videos.xml?channel_id='+channel)
    except ValueError as error:
        raise AdapterException(AdapterIssue('youtube-invalid-feed','Нужен Atom-фид канала YouTube',AdapterErrorKind.permanent)) from error


def _encode(state: dict[str,Any]) -> str:
    return json.dumps(state,separators=(',',':'),ensure_ascii=False)


class YouTubeAdapter:
    def __init__(self, captions: CaptionsClient, transport: httpx.AsyncBaseTransport | None = None,
                 history: HistoryClient | None = None) -> None:
        self.captions,self.transport=captions,transport
        self.history=history or LocalHistory()
        self.latest: dict[str,Entry]={}
        self.anchors: dict[str,str]={}
        self.issues: tuple[AdapterIssue,...]=()

    async def _window(self, feed: ExternalReference) -> list[str]:
        if feed!=feed_reference(feed.url):
            raise AdapterException(AdapterIssue('youtube-invalid-feed','Ключ не соответствует каналу',AdapterErrorKind.permanent))
        channel=feed.key.external_id
        source=RSSSource(feed.url,0,self.transport)
        found: dict[str,Entry]={}
        errors=[]
        self.anchors={}
        for category in ('UULF','UULV'):
            url='https://www.youtube.com/feeds/videos.xml?playlist_id='+category+channel[2:]
            try:
                raw,base,_=await source.download(url)
                for entry in entries(raw,base,strict=True):
                    match=re.fullmatch(r'yt:video:([A-Za-z0-9_-]{11})',entry.publication.id)
                    if match:
                        found.setdefault(match[1],entry)
                        self.anchors.setdefault(category,match[1])
            except SourceError as error:
                errors.append(error)
        if not found and errors:
            raise failure(errors[0],scope='youtube:feed:'+channel) from errors[0]
        self.latest=found
        self.issues=tuple(AdapterIssue('youtube-feed-partial',error.label,AdapterErrorKind.notice) for error in errors)
        ordered=sorted(found,key=lambda key:found[key].publication.published)
        return ordered

    def _value(self, video: str) -> PublicationContent:
        ref=video_reference(video)
        entry=self.latest.get(video)
        stamp=entry.publication.published if entry and entry.publication.published_known else None
        cover=MediaReference('youtube:cover','https://i.ytimg.com/vi/'+video+'/hqdefault.jpg','',mime_type='image/jpeg')
        subtitles=MediaReference('youtube:subtitles','youtube:'+video+':subtitles','',mime_type='text/plain')
        extra: dict[str,Any]=dict(captions='pending')
        if entry:
            extra['description']=RSSSource.excerpt(entry.excerpt)
        return PublicationContent(PublicationMetadata(ref,entry.title if entry else '',
            datetime.fromtimestamp(stamp,timezone.utc) if stamp is not None else None),
            dict(blocks=[dict(type='external_media',kind='youtube',url=ref.url),
                         dict(type='media',role='cover',ref={'$media':cover.key}),
                         dict(type='media',role='subtitles',ref={'$media':subtitles.key})],
                 extra=dict(youtube=extra)),(cover,subtitles))

    def _metadata(self, keys: list[str]) -> dict[str,Any]:
        return {key:dict(title=self.latest[key].title,
            published=self.latest[key].publication.published if self.latest[key].publication.published_known else None)
            for key in keys if key in self.latest}

    def _restore(self, state: dict[str,Any]) -> None:
        for key,value in state.get('metadata',{}).items():
            if key not in self.latest:
                stamp=value.get('published')
                self.latest[key]=Entry(Publication(key,video_reference(key).url,stamp or 0,
                    published_known=stamp is not None),value.get('title',''),'')

    async def _history(self, feed: ExternalReference, state: dict[str,Any]) -> None:
        provider=state.setdefault('history',dict(anchors=state.get('anchors',{})))
        count=state['history_count']
        # The metadata worker may have finished a smaller prefix. Its anchors
        # remain fixed, but lists shorter than the new request must be revisited.
        if count>state.get('provider_count',0):
            provider['done']=[kind for kind in provider.get('done',[])
                              if len(provider.get('lists',{}).get(kind,[]))>=count]
            state['provider_count']=count
        try:
            result=await self.history.fetch(feed.key.external_id,count,provider)
        except SourceError as error:
            raise failure(error,scope='youtube:history:'+feed.key.external_id) from error
        state['history']=result['state']
        state['history_error']=result.get('error')
        state['locating']=not result['complete']
        if state['locating']:
            return
        keys=[]
        for entry in result['entries']:
            key=entry['id']
            video_reference(key)
            if key not in keys:
                keys.append(key)
            if key not in self.latest:
                self.latest[key]=Entry(Publication(key,video_reference(key).url,0,published_known=False),entry.get('title',''),'')
        keys.reverse()
        anchor=state['history_position']
        if anchor is not None and anchor not in keys:
            raise AdapterException(AdapterIssue('youtube-history-boundary','Граница истории исчезла; начните новый подбор',AdapterErrorKind.permanent))
        position=keys.index(anchor) if anchor is not None else len(keys)
        state.update(keys=keys,position=max(0,position-state['history_offset']),
            shortfall=max(0,state['history_offset']-position),metadata=self._metadata(keys))

    async def resolve_feed(self, url: str) -> FeedMetadata:
        feed=feed_reference(url)
        keys=await self._window(feed)
        return FeedMetadata(feed,'','',_encode(dict(channel=feed.key.external_id,keys=keys,
            position=max(0,len(keys)-1),done=False,metadata=self._metadata(keys),
            anchors={**self.anchors,**({'UU':keys[-1]} if keys else {})})))

    async def cursor_before(self, feed: ExternalReference, cursor: str, offset: int) -> str:
        state=self._state(feed,cursor)
        if offset<0:
            raise ValueError('offset must be non-negative')
        if offset>state['position'] and state['keys']:
            state.update(history_count=offset+len(state['keys'])-state['position'],
                history_position=state['keys'][state['position']] if state['position']<len(state['keys']) else None,
                history_offset=offset,locating=True)
            await self._history(feed,state)
            return _encode(state)
        state['shortfall']=max(0,offset-state['position'])
        state['position']=max(0,state['position']-offset)
        return _encode(state)

    @staticmethod
    def _state(feed: ExternalReference, cursor: str) -> dict[str,Any]:
        try:
            state=json.loads(cursor) if cursor else dict(channel=feed.key.external_id,keys=[],position=0,done=True)
            if (feed!=feed_reference(feed.url) or not isinstance(state,dict) or state.get('channel')!=feed.key.external_id
                    or not isinstance(state.get('keys'),list) or type(state.get('position')) is not int
                    or not 0<=state['position']<=len(state['keys']) or type(state.get('done')) is not bool
                    or not all(isinstance(key,str) and re.fullmatch(r'[A-Za-z0-9_-]{11}',key) for key in state['keys'])):
                raise ValueError('cursor')
            return state
        except (ValueError,TypeError) as error:
            raise AdapterException(AdapterIssue('youtube-invalid-cursor','Недопустимый курсор YouTube',AdapterErrorKind.permanent)) from error

    async def discover(self, feed: ExternalReference, cursor: str, limit: int) -> DiscoveryPage:
        state=self._state(feed,cursor)
        if limit<=0:
            raise ValueError('limit must be positive')
        current=await self._window(feed)
        self._restore(state)
        if state.get('locating'):
            await self._history(feed,state)
            if state['locating']:
                issues: tuple[AdapterIssue,...]=(AdapterIssue('youtube-history-error',str(state['history_error']),AdapterErrorKind.temporary),) if state.get('history_error') else ()
                return DiscoveryPage((),_encode(state),True,issues)
        if state['done']:
            state=dict(channel=feed.key.external_id,keys=current,position=0,done=False)
        keys=state['keys'][state['position']:state['position']+limit]
        values=tuple(self._value(key) for key in keys)
        state['position']+=len(keys)
        state['done']=state['position']>=len(state['keys'])
        issues=self.issues
        if state.pop('shortfall',0):
            issues+=(AdapterIssue('history-shortfall','Доступно меньше видео, чем запрошено',AdapterErrorKind.notice),)
        return DiscoveryPage(values,_encode(state),not state['done'],issues)

    async def fetch(self, publication: ExternalReference) -> PublicationContent:
        expected=video_reference(publication.key.external_id)
        if publication!=expected:
            raise AdapterException(AdapterIssue('youtube-invalid-reference','Ключ не соответствует видео',AdapterErrorKind.permanent,publication))
        return self._value(expected.key.external_id)

    async def download_media(self, publication: ExternalReference, media: MediaReference, output: BinaryIO) -> MediaReference:
        value=await self.fetch(publication)
        current=next((ref for ref in value.media if ref.key==media.key),None)
        if current is None or current.locator!=media.locator:
            raise AdapterException(AdapterIssue('media-changed','Описание файла YouTube изменилось',AdapterErrorKind.media_changed,publication,media.key))
        try:
            if current.key=='youtube:cover':
                raw,_,mime=await RSSSource(publication.url,0,self.transport).download(current.locator,2_000_000)
                if not (mime=='image/jpeg' and raw.startswith(b'\xff\xd8\xff')
                        or mime=='image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n')):
                    raise SourceError('Превью недоступно',retryable=False)
                output.write(raw)
                return replace(current,mime_type=mime,size_bytes=len(raw))
            text=await self.captions.fetch(publication.key.external_id)
            if text is None:
                raise AdapterException(AdapterIssue('subtitles-unavailable','Субтитры недоступны',AdapterErrorKind.notice,publication,media.key))
            raw=text.encode('utf-8')
            if len(raw)>2_000_000:
                raise SourceError('Субтитры превышают лимит; текст не обрезан',retryable=False)
            output.write(raw)
            return replace(current,size_bytes=len(raw))
        except SourceError as error:
            raise failure(error,publication,media.key,scope='youtube:captions' if current.key=='youtube:subtitles' else 'youtube:cover') from error
