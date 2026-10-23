"""Channel-only Telegram client adapter with external keys and individual files."""
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import json
from typing import Any,BinaryIO
from urllib.parse import urlsplit

from oko.collection.adapters import (AdapterErrorKind,AdapterException,AdapterIssue,DiscoveryPage,
    ExternalKey,ExternalReference,FeedMetadata,MediaReference,Platform,PublicationContent,PublicationMetadata)
from oko.collection.contracts import SourceError,SourceDeleted
from oko.collection.telegram.adapter import failure as source_failure
from oko.collection.telegram.source import TelegramClient,LocalTelegram
from oko.collection.telegram.telegraph import direct_articles


def publication_reference(peer: int, message: int, username: str | None = None) -> ExternalReference:
    if peer>=-1000000000000 or message<=0:
        raise ValueError('Marked channel and positive message required')
    base='https://t.me/'+username.lower() if username else 'https://t.me/c/'+str(-peer-1000000000000)
    url=base+'/'+str(message)
    return ExternalReference(ExternalKey(Platform.telegram,url),url)


def failure(error: SourceError, publication: ExternalReference | None = None,
            media_key: str | None = None, scope: str = '') -> AdapterException:
    if error.remote_type=='ChangedMedia':
        return AdapterException(AdapterIssue('media-changed',error.label,AdapterErrorKind.media_changed,publication,media_key))
    if error.remote_type=='FloodWaitError':
        return AdapterException(AdapterIssue('telegram-flood-wait',error.label,AdapterErrorKind.source_wait,
            publication,media_key,scope,datetime.now(timezone.utc)+timedelta(seconds=error.delay)))
    if error.remote_type=='OverflowError':
        error.code,error.retryable='media-too-large',False
    elif error.remote_type in ('ChannelPrivateError','ChatAdminRequiredError','UsernameInvalidError','UsernameNotOccupiedError','PeerIdInvalidError','FileNotFoundError','PermissionError'):
        return AdapterException(AdapterIssue(error.code or 'telegram-unavailable',error.label,AdapterErrorKind.unavailable,publication,media_key))
    return source_failure(error,publication,media_key,scope)


def encode(peer: int, start: int, upper: int, *, done: bool = False) -> str:
    return json.dumps(dict(telegram_client=1,peer=peer,start=start,upper=upper,done=done),separators=(',',':'))


def decode(peer: int, cursor: str) -> dict[str,Any]:
    try:
        value=json.loads(cursor)
        if (value.get('telegram_client')!=1 or value.get('peer')!=peer or type(value['done']) is not bool
                or any(type(value[key]) is not int or value[key]<0 for key in ('start','upper'))):
            raise ValueError('cursor')
        return dict(value)
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        raise AdapterException(AdapterIssue('telegram-cursor-incompatible','Несовместимый курсор Telegram client',AdapterErrorKind.permanent)) from error


class TelegramClientAdapter:
    def __init__(self, client: TelegramClient, account: str, peer: int) -> None:
        self.client,self.account,self.peer=client,account,peer
        self.info: dict[str,Any]={}
        self._head: dict[str,Any] | None=None

    async def _resolve(self) -> None:
        info=await self.client.info(self.account,self.peer)
        if info.get('peer')!=self.peer:
            raise SourceError('Telegram вернул другой канал',code='source_identity_conflict',retryable=False)
        if info.get('kind')!='channel':
            raise SourceError('Этот адаптер поддерживает только каналы Telegram',code='telegram-channel-required',retryable=False)
        self.info=info

    def _url(self, message: int | None = None) -> str:
        name=self.info.get('username')
        base='https://t.me/'+str(name).lower() if name else 'https://t.me/c/'+str(-self.peer-1000000000000)
        return base+'/'+str(message) if message is not None else base

    def _reference(self, message: int) -> ExternalReference:
        return publication_reference(self.peer,message,self.info.get('username'))

    def _groups(self, rows: list[dict[str,Any]]) -> list[list[dict[str,Any]]]:
        groups: dict[tuple[str,int],dict[int,dict[str,Any]]]={}
        for row in rows:
            if row.get('service'):
                continue
            identity=('album',row['group']) if row.get('group') else ('post',row['id'])
            groups.setdefault(identity,{})[row['id']]=row
        return sorted((sorted(group.values(),key=lambda row:row['id']) for group in groups.values()),key=lambda group:max(row['id'] for row in group))

    def _content(self, rows: list[dict[str,Any]]) -> PublicationContent:
        reference=self._reference(rows[0]['id'])
        text='\n\n'.join(row['text'] for row in rows if row['text'])
        blocks: list[dict[str,Any]]=[dict(type='text',role='body',text=text)] if text else []
        media=[]
        issues=[]
        resources: list[dict[str,Any]]=[]
        for row in rows:
            item=row.get('media')
            if not item:
                continue
            key='telegram-client:'+str(self.peer)+':'+str(row['id'])
            if item['kind']=='unsupported':
                issues.append(AdapterIssue('media-unsupported','Тип вложения пока не поддерживается',AdapterErrorKind.unavailable,reference,key))
                continue
            locator=json.dumps(dict(peer=self.peer,message=row['id'],publication_key=reference.key.external_id),separators=(',',':'))
            name='media-'+str(row['id'])+'.'+item['extension']
            media.append(MediaReference(key,locator,name,item['mime'],item.get('size'),item.get('identity')))
            blocks.append(dict(type='media',role='attachment' if item['kind']=='file' else item['kind'],ref={'$media':key}))
            resources.append(dict(key=key,origin='telegram:'+str(self.peer)+'/'+str(row['id']),order=len(resources),
                name=name,kind=item['kind'],mime=item['mime'],status='missing'))
        members=[row['id'] for row in rows]
        publishing=any(row.get('group') and datetime.now(timezone.utc).timestamp()-row['date']<5 for row in rows)
        if publishing:
            issues.append(AdapterIssue('telegram-album-pending','Альбом ещё публикуется',AdapterErrorKind.temporary,reference))
        manifest=dict(source_url=reference.url,post=dict(text=text,members=members,
            baseline_text='\n\n'.join(row.get('original_text',row['text']) for row in rows if row.get('original_text',row['text'])),
            edits=[dict(id=row['id'],at=row.get('edited')) for row in rows]),resources=resources)
        from oko.collection.web_adapter import reference as article_reference
        content=dict(blocks=blocks,extra=dict(telegram=dict(members=members,album_id=rows[0].get('group'),description='',manifest=manifest)))
        return PublicationContent(PublicationMetadata(reference,text.split('\n')[0] if text else 'Telegram · медиа',
            datetime.fromtimestamp(rows[0]['date'],timezone.utc)),content,tuple(media),
            tuple(article_reference(url) for url in direct_articles(text)),needs_fetch=publishing,issues=tuple(issues))

    async def resolve_feed(self, url: str) -> FeedMetadata:
        try:
            await self._resolve()
            self._head=await self.client.history_page(self.account,self.peer,0,0,0)
            upper=self._head['upper']
            reference=ExternalReference(ExternalKey(Platform.telegram,str(self.peer)),self._url())
            return FeedMetadata(reference,self.info.get('title',''),self.info.get('description',''),encode(self.peer,upper,upper) if upper else '')
        except SourceError as error:
            raise failure(error,scope='telegram:client:'+self.account) from error

    async def _window(self, start: int, upper: int, count: int | None = None) -> list[list[dict[str,Any]]]:
        before=upper+1
        rows: dict[int,dict[str,Any]]={}
        while True:
            if self._head and self._head['upper']==upper and before==upper+1:
                page=self._head
            else:
                page=await self.client.history_page(self.account,self.peer,upper,before,0)
            rows.update((row['id'],row) for row in page['messages'] if row['id']<=upper)
            groups=[group for group in self._groups(list(rows.values())) if max(row['id'] for row in group)>=start]
            if not page['more'] or count is not None and len(groups)>=count or any(row['id']<=start for row in page['messages']):
                return groups
            following=page['next_before']
            if not following or following>=before:
                raise SourceError('Telegram не продвинул границу истории',code='telegram-history-stalled')
            before=following

    async def cursor_before(self, feed: ExternalReference, cursor: str, offset: int) -> str:
        if offset<0:
            raise ValueError('offset must be non-negative')
        if not cursor:
            return json.dumps(dict(telegram_client=1,peer=self.peer,start=0,upper=0,done=False,shortfall=offset+1))
        if not offset:
            return cursor
        state=decode(self.peer,cursor)
        try:
            groups=await self._window(0,state['start'],offset+1)
            start=max(row['id'] for row in groups[max(0,len(groups)-offset-1)]) if groups else 0
            result=json.loads(encode(self.peer,start,state['upper']))
            result['shortfall']=max(0,offset+1-len(groups))
            return json.dumps(result)
        except SourceError as error:
            raise failure(error,scope='telegram:client:'+self.account) from error

    async def discover(self, feed: ExternalReference, cursor: str, limit: int) -> DiscoveryPage:
        if limit<1:
            raise ValueError('limit must be positive')
        try:
            if feed.key!=ExternalKey(Platform.telegram,str(self.peer)):
                raise SourceError('Ключ фида не соответствует каналу',code='source_identity_conflict',retryable=False)
            await self._resolve()
            state=decode(self.peer,cursor) if cursor else dict(start=0,upper=0,done=True)
            if state['done']:
                metadata=await self.resolve_feed(feed.url)
                upper=decode(self.peer,metadata.latest_cursor)['upper'] if metadata.latest_cursor else state['upper']
                state=dict(start=state['upper']+1,upper=upper,done=False)
            groups=await self._window(state['start'],state['upper']) if state['start']<=state['upper'] else []
            chosen=groups[:limit]
            start=max(row['id'] for row in chosen[-1])+1 if chosen else state['upper']+1
            issues=(AdapterIssue('history-shortfall','Доступно меньше публикаций, чем запрошено',AdapterErrorKind.notice),) if state.get('shortfall') else ()
            return DiscoveryPage(tuple(self._content(group) for group in chosen),encode(self.peer,start,state['upper'],done=len(groups)<=limit),len(groups)>limit,issues)
        except SourceError as error:
            raise failure(error,scope='telegram:client:'+self.account) from error

    async def fetch(self, publication: ExternalReference) -> PublicationContent:
        try:
            await self._resolve()
            message=int(urlsplit(publication.url).path.rstrip('/').rsplit('/',1)[-1])
            private='https://t.me/c/'+str(-self.peer-1000000000000)+'/'+str(message)
            if (publication.key!=ExternalKey(Platform.telegram,publication.url) or message<=0
                    or publication.url not in (self._url(message),private)):
                raise SourceError('Неверная ссылка Telegram',retryable=False)
            groups=self._groups(await self.client.messages(self.account,self.peer,[message]))
            group=next((group for group in groups if any(row['id']==message for row in group)),None)
            if group is None:
                raise SourceDeleted()
            return self._content(group)
        except (ValueError,SourceError) as error:
            source=error if isinstance(error,SourceError) else SourceError('Неверная ссылка Telegram',retryable=False)
            raise failure(source,publication,scope='telegram:client:'+self.account) from error

    async def download_media(self, publication: ExternalReference, media: MediaReference, output: BinaryIO) -> MediaReference:
        try:
            locator=json.loads(media.locator)
            if (locator['peer']!=self.peer or locator['publication_key']!=publication.key.external_id
                    or publication.key!=ExternalKey(Platform.telegram,publication.url)):
                raise ValueError('owner')
            anchor=int(publication.url.rstrip('/').rsplit('/',1)[-1])
        except (ValueError,TypeError,KeyError):
            raise AdapterException(AdapterIssue('media-changed','Нужно обновить описание вложения Telegram',AdapterErrorKind.media_changed,publication,media.key)) from None
        current=await self.fetch(publication_reference(self.peer,anchor))
        if current.metadata.reference.key!=publication.key:
            raise AdapterException(AdapterIssue('media-changed','Адрес публикации Telegram изменился',AdapterErrorKind.media_changed,publication,media.key))
        selected=next((value for value in current.media if value.key==media.key),None)
        if selected is None or selected.resource_identity!=media.resource_identity:
            raise AdapterException(AdapterIssue('media-changed','Вложение Telegram изменилось',AdapterErrorKind.media_changed,publication,media.key))
        try:
            message=json.loads(selected.locator)['message']
            if isinstance(self.client,LocalTelegram):
                size=await self.client.download_stream(self.account,self.peer,message,output,expected_identity=selected.resource_identity)
                return replace(selected,size_bytes=size)
            raw=await self.client.download(self.account,self.peer,message)
            if not raw:
                raise SourceError('Пустое вложение Telegram')
            if len(raw)>200*1024*1024:
                raise SourceError('Вложение превышает лимит 200 МиБ',code='media-too-large',retryable=False)
            output.write(raw)
            return replace(selected,size_bytes=len(raw))
        except SourceError as error:
            raise failure(error,publication,media.key,scope='telegram:client:'+self.account) from error
