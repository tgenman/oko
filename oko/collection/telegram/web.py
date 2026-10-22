"""Public Telegram HTML adapter, adapted from RSSHub's channel parser.

Upstream: DIYgod/RSSHub d24f5136487a388662ec6656e4f1ba4ff46b0ed8,
lib/routes/telegram/channel.ts (AGPL-3.0). Python adaptation for Oko: plain
text/local media, bounded pagination, no route switches, RSS or account fallback.
See docs/licenses/RSSHub-AGPL-3.0.txt and docs/licenses/RSSHub-NOTICE.md.
"""
from dataclasses import dataclass
import hashlib
import base64
from datetime import datetime
import re
from typing import Any, Literal
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
import httpx
from oko.collection import request_observation as observation

from oko.collection.contracts import Material, Publication, SourceDeleted, SourceError, DownloadStage, Attachment, SourceManifest, ResourceManifest, ExternalMedia, ReplyPreview
from oko.collection.telegram.contracts import TelegramPublication, ExpectedMedia
from oko.collection.telegram.media import Downloader


@dataclass(frozen=True)
class WebPage:
    title: str
    description: str
    posts: tuple[TelegramPublication, ...]
    next_before: int | None
    avatar_url: str | None = None


MEDIA_WIDGETS = '.tgme_widget_message_video_player, .tgme_widget_message_photo_wrap, video, audio, .tgme_widget_message_sticker, .tgme_widget_message_tgsticker, .tgme_widget_message_document_wrap, .link_preview_image, .link_preview_right_image, iframe'

def text(node: Tag | NavigableString) -> str:
    """Render data as text/links; external markup is never executable DOM."""
    if isinstance(node, NavigableString):
        return str(node)
    if node.name == 'img':
        return str(node.get('alt',''))
    if node.name in ('script', 'style', 'iframe', 'video', 'audio'):
        return ''
    if node.name == 'br':
        return '\n'
    content = ''.join(text(child) for child in node.children if isinstance(child, (Tag, NavigableString)))
    if node.name == 'a':
        href = str(node.get('href', ''))
        if urlsplit(href).scheme in ('https', 'http'):
            return '['+content.strip()+']('+href+')'
    if node.name == 'blockquote':
        return '\n'+'\n'.join('> '+line for line in content.strip().splitlines())+'\n'
    return '\n'+content+'\n' if node.name in ('p','div','li','h3','h4','figcaption') else content


def readable(node: Tag) -> str:
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'[\t ]+', ' ', text(node))).strip()


def media_blocks(wrapper: Tag, username: str, message: int) -> tuple[ExpectedMedia, ...]:
    result: list[ExpectedMedia] = []
    indices: dict[int, int] = {}
    def add(node: Tag, kind: Literal['photo', 'video', 'audio', 'document', 'unsupported'], url: str | None, extension: str, mime: str, reason: str = '') -> None:
        member = message
        anchor = node if 'grouped_media_wrap' in node.get_attribute_list('class') else node.find_parent(class_='grouped_media_wrap')
        if anchor:
            match = re.search(r'/([0-9]+)$',urlsplit(str(anchor.get('href',''))).path)
            if match:
                member = int(match[1])
        index = indices.get(member,0)
        indices[member] = index+1
        key_kind = 'document' if 'tgme_widget_message_document_wrap' in node.get_attribute_list('class') else kind
        key = 'telegram-web:'+str(member)+':'+str(index)+':'+key_kind
        name = 'media-'+str(int(hashlib.sha256(key.encode()).hexdigest()[:16],16))+'.'+extension
        external = None
        if not url and kind in ('photo','video','audio','document'):
            label = node.select_one('.message_media_not_supported_label')
            duration = node.select_one('.message_video_duration')
            media_title = node.select_one('.tgme_widget_message_document_title')
            author = node.select_one('.tgme_widget_message_document_extra')
            external = ExternalMedia(kind='image' if kind=='photo' else kind,
                source_url=f'https://t.me/{username}/{member}',
                restriction='size_limit' if label and label.get_text(' ',strip=True)=='Media is too big' else None,
                title=media_title.get_text(' ',strip=True) if media_title else None,
                size_label=(' '.join(author.stripped_strings) if author and kind=='document' and re.fullmatch(r'[0-9]+(?:[.,][0-9]+)?\s*(?:B|KB|MB|GB|TB|KiB|MiB|GiB)', ' '.join(author.stripped_strings), re.IGNORECASE) else None),
                author=author.get_text(' ',strip=True) if author and kind!='document' else None,
                duration=duration.get_text(' ',strip=True) if duration else None)
        if kind == 'photo':
            result.append(ExpectedMedia(key,'photo',url,name,mime,reason=reason or None,external=external))
        elif kind == 'video':
            result.append(ExpectedMedia(key,'video',url,name,mime,reason=reason or None,external=external))
        elif kind == 'audio':
            result.append(ExpectedMedia(key,'audio',url,name,mime,reason=reason or None,external=external))
        elif kind == 'document':
            result.append(ExpectedMedia(key,'document',url,name,mime,reason=reason or None,external=external))
        else:
            result.append(ExpectedMedia(key,'unsupported',None,name,mime,reason=reason or 'Неподдерживаемое вложение'))
    def background(node: Tag) -> str | None:
        match = re.search(r"url\(\s*['\"]?([^'\")]+)",str(node.get('style','')))
        return match[1] if match else None
    for node in wrapper.select(MEDIA_WIDGETS):
        classes = node.get_attribute_list('class')
        if node.name == 'video' and node.find_parent(class_='tgme_widget_message_video_player'):
            continue
        if node.name == 'iframe':
            add(node,'unsupported',None,'bin','application/octet-stream','Внешнее встраивание не сохранено локально: '+str(node.get('src','')))
        elif 'tgme_widget_message_video_player' in classes or node.name == 'video':
            video = node if node.name == 'video' else node.select_one('video')
            source = video.get('src') if video else None
            if video and not source:
                child = video.select_one('source[src]')
                source = child.get('src') if child else None
            add(node,'video',str(source) if source else None,'mp4','video/mp4','Видео не предоставлено публичным предпросмотром' if not source else '')
            poster = node.select_one('.tgme_widget_message_video_thumb')
            cover = background(poster) if poster else str(video.get('poster','')) if video else ''
            if cover:
                add(node,'photo',cover,'jpg','image/jpeg')
                if result[-2].external:
                    from dataclasses import replace
                    result[-2] = replace(result[-2], external=result[-2].external.model_copy(update={'poster_key':result[-1].id}))
        elif node.name == 'audio':
            source = node.get('src')
            if not source:
                child = node.select_one('source[src]')
                source = child.get('src') if child else None
            add(node,'audio',str(source) if source else None,'ogg','audio/ogg','Аудио не предоставлено публичным предпросмотром' if not source else '')
        elif 'tgme_widget_message_document_wrap' in classes:
            if node.select_one('audio[src]'):
                continue
            link = node if node.has_attr('download') and node.has_attr('href') else node.select_one('a[download][href]')
            href = str(link.get('href')) if link else None
            add(node,'audio' if node.select_one('.tgme_widget_message_document_icon.audio') else 'document',href,'bin','application/octet-stream','Файл документа не предоставлен публичным предпросмотром' if not href else '')
        elif any(kind in classes for kind in ('tgme_widget_message_photo_wrap','link_preview_image','link_preview_right_image')):
            url = background(node)
            add(node,'photo',url,'jpg','image/jpeg','Фото не предоставлено публичным предпросмотром' if not url else '')
        else:
            child = node.select_one('img[src]')
            picture = node.select_one('source[srcset]')
            picture_url = str(picture.get('srcset','')).split(',')[0].split()[0] if picture else ''
            url = str(node.get('data-webp') or picture_url or (child.get('src') if child else '') or '')
            add(node,'photo',url or None,'webp','image/webp','Стикер не предоставлен публичным предпросмотром' if not url else '')
    # Telegram embeds a CSS-hidden fallback beside supported text/custom emoji.
    # Its presence alone is not an unavailable attachment. An active fallback
    # (media_not_supported ancestor), or a standalone placeholder, still is.
    unsupported = any(node.find_parent(class_='media_not_supported_cont') is None
                      or node.find_parent(class_='media_not_supported') is not None
                      for node in wrapper.select('.message_media_not_supported'))
    if unsupported and not result:
        add(wrapper,'unsupported',None,'bin','application/octet-stream','Пост не поддерживается публичным предпросмотром')
    return tuple(result)


def parse_page(data: bytes, username: str) -> WebPage:
    document = BeautifulSoup(data, 'html.parser')
    history = document.select_one('.tgme_channel_history')
    if history is None:
        raise SourceError('Публичная история Telegram недоступна', code='source_unavailable')
    title = document.select_one('.tgme_channel_info_header_title')
    description = document.select_one('.tgme_channel_info_description')
    posts = []
    for wrapper in history.select('.tgme_widget_message[data-post]'):
        identity = re.fullmatch(r'([a-zA-Z0-9_]+)/([1-9][0-9]*)', str(wrapper.get('data-post')))
        if not identity or identity[1].lower() != username.lower():
            raise SourceError('Некорректная идентичность публикации Telegram')
        message = int(identity[2])
        anchors = []
        for anchor in wrapper.select('a.grouped_media_wrap[href]'):
            address = urlsplit(str(anchor['href']))
            match = re.fullmatch('/'+re.escape(username)+r'/([1-9][0-9]*)', address.path, re.I)
            if address.hostname not in ('t.me','telegram.me') or not match:
                raise SourceError('Неподтверждённый состав альбома Telegram')
            anchors.append(int(match[1]))
        members = tuple(sorted(set(anchors or [message])))
        reply_preview = None
        reply = wrapper.select_one('.tgme_widget_message_reply')
        if reply is not None:
            def plain(node: Tag | NavigableString) -> str:
                if isinstance(node, NavigableString):
                    return str(node)
                if node.name in ('script', 'style', 'iframe'):
                    return ''
                if node.name == 'img':
                    return str(node.get('alt', ''))
                if node.name == 'br':
                    return ' '
                content = ''.join(plain(child) for child in node.children if isinstance(child, (Tag, NavigableString)))
                return ' '+content+' ' if node.name in ('div', 'p', 'li') else content
            def clean(node: Tag | None) -> str:
                return ' '.join(plain(node).split()) if node is not None else ''
            author = clean(reply.select_one('.tgme_widget_message_author_name'))
            fragment = clean(reply.select_one('.js-message_reply_text, .tgme_widget_message_text'))
            safe = None
            try:
                reply_address = urljoin(f'https://t.me/{username}/{message}', str(reply.get('href', ''))) if reply.get('href') else None
                parsed = urlsplit(reply_address) if reply_address else None
                if parsed and parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password and re.fullmatch(r'https?://[^\s]+', reply_address or ''):
                    safe = reply_address
            except ValueError:
                pass  # Malformed source addresses do not discard the saved excerpt.

            reply_preview = ReplyPreview(author=author or None, text=fragment, source_url=safe)
            reply.decompose()
        own_text = [readable(node) for node in wrapper.select('.tgme_widget_message_text')]
        own_title = next((line.strip() for part in own_text for line in part.splitlines() if line.strip()), '')
        paragraphs = []
        for quote in wrapper.select('.tgme_widget_message_forwarded_from'):
            paragraphs.append('> '+readable(quote))
        forwarded_count = len(paragraphs)
        for paragraph in wrapper.select('.tgme_widget_message_text'):
            if paragraph.find_parent(class_='tgme_widget_message_reply') is None:
                paragraphs.append(readable(paragraph))
        for selector in ('.tgme_widget_message_poll', '.tgme_widget_message_contact'):
            paragraphs.extend(readable(node) for node in wrapper.select(selector))
        service = wrapper.select_one('.service_message')
        if service and not paragraphs:
            paragraphs.append(readable(service))
        body = '\n\n'.join(part for part in paragraphs if part)
        stamp = wrapper.select_one('time[datetime]')
        try:
            published = datetime.fromisoformat(str(stamp['datetime']).replace('Z','+00:00')).timestamp() if stamp else None
        except ValueError:
            published = None
        if published is None:
            raise SourceError('Telegram не предоставил дату публикации')
        post_web_unavailable = bool('text_not_supported_wrap' in wrapper.get_attribute_list('class')
            and wrapper.select_one('.message_media_not_supported .message_media_view_in_telegram')
            and not any(part.strip() for part in paragraphs[forwarded_count:]) and reply_preview is None
            and not wrapper.select_one(MEDIA_WIDGETS))
        media = () if post_web_unavailable else media_blocks(wrapper,username,message)
        if not post_web_unavailable and not media and not any(part.strip() for part in paragraphs[forwarded_count:]) and reply_preview is None:
            media = (ExpectedMedia('telegram-web:'+str(message)+':unknown','unsupported',None,'media-'+str(message)+'.bin',reason='Предпросмотр не предоставил содержимое публикации'),)
        preview = wrapper.select_one('.tgme_widget_message_link_preview')
        preview_text = readable(preview) if preview else ''
        meta = wrapper.select_one('.tgme_widget_message_meta')
        edited = bool(meta and re.match(r'^edited\b',meta.get_text(' ',strip=True)))
        resources = tuple(ResourceManifest(key=m.id,origin='telegram:'+username+'/'+str(message),order=i,
            name=m.name,kind='image' if m.kind=='photo' else 'file' if m.kind in ('document','unsupported') else m.kind,
            mime=m.mime or 'application/octet-stream',status='external' if m.external else 'missing' if m.url else 'unsupported',reason=m.reason or '',external=m.external) for i,m in enumerate(media))
        manifest = SourceManifest(post_web_unavailable=post_web_unavailable,source_url=f'https://t.me/{username}/{message}',post=dict(text=body,description=preview_text,reply_preview=reply_preview.model_dump(mode="json") if reply_preview else None,edited=edited,members=list(members),resources=[dict(key=m.id,kind=m.id.rsplit(':',1)[-1]) for m in media]),resources=resources)
        file_title = next((node.get_text(' ',strip=True) for node in wrapper.select('.tgme_widget_message_document_title') if node.get_text(' ',strip=True)), '')
        file_fallback = file_title or ('Файл доступен в Telegram' if any(m.kind=='document' for m in media) else '')
        teaser = Material(('Пост доступен только в Telegram' if post_web_unavailable else own_title) or (body.split('\n')[0] or file_fallback or ('Ответ на сообщение' if reply_preview else 'Telegram · медиа')),body,False,
                          'Вложения ещё не загружены' if media else '',description=preview_text,source_manifest=manifest,reply_preview=reply_preview)
        posts.append(TelegramPublication(Publication(str(message),f'https://t.me/{username}/{message}',published,teaser,members),body,media))
    more = history.select_one('a.js-messages_more[href*="before="]')
    next_before = None
    if more:
        address = urlsplit(urljoin('https://t.me',str(more['href'])))
        values = parse_qs(address.query).get('before',[])
        if address.hostname not in ('t.me','telegram.me') or address.path.lower() != '/s/'+username.lower() or len(values)!=1 or not values[0].isdigit() or int(values[0])<=0:
            raise SourceError('Неверная позиция веб-истории Telegram')
        next_before = int(values[0])
        if not posts:
            raise SourceError('Пустая страница с неподтверждённым продолжением Telegram')
    return WebPage(title.get_text(' ',strip=True) if title else username,
                   readable(description) if description else '',tuple(posts),next_before,
                   str(avatar.get('src')) if (avatar := document.select_one('.tgme_channel_info_photo img, .tgme_page_photo_image img, img.tgme_page_photo_image')) and avatar.get('src') else None)


async def load(username: str, before: int, transport: httpx.AsyncBaseTransport | None) -> tuple[WebPage,str]:
    url = 'https://t.me/s/'+username+('?before='+str(before) if before else '')
    original_url = url
    try:
        async with httpx.AsyncClient(transport=transport, timeout=20, follow_redirects=False, trust_env=False) as client:
            for _ in range(5):
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or parsed.hostname not in ('t.me','telegram.me') or parsed.username or parsed.port or parsed.path.lower() != '/s/'+username.lower():
                    raise ValueError('redirect outside public source')
                with observation.request(url, purpose='feed', original_url=original_url):
                    async with client.stream('GET',url) as response:
                        observation.response_received(response)
                        if response.is_redirect:
                            url = urljoin(url,response.headers['location'])
                            continue
                        response.raise_for_status()
                        data = bytearray()
                        async for part in response.aiter_bytes():
                            data.extend(part)
                            if len(data)>2*1024*1024:
                                raise ValueError('preview too large')
                page = parse_page(bytes(data),username)
                if before and any(int(post.publication.id)>=before for post in page.posts):
                    raise SourceError('Telegram вернул страницу вне запрошенной границы')
                if before and page.next_before is not None and page.next_before>=before:
                    raise SourceError('Telegram не продвинул позицию веб-истории')
                return page,url
            raise ValueError('too many redirects')
    except (httpx.HTTPError,ValueError,KeyError) as error:
        delay = 60
        if isinstance(error,httpx.HTTPStatusError) and error.response.status_code == 429:
            header = error.response.headers.get('retry-after','60')
            delay = min(86400,max(1,int(header))) if header.isdecimal() else 60
        raise SourceError('Публичный предпросмотр Telegram недоступен',delay,code='source_unavailable') from error


async def avatar_metadata(page: WebPage, transport: httpx.AsyncBaseTransport | None) -> dict[str, Any]:
    if not page.avatar_url:
        return {'avatar': None}
    try:
        async with Downloader(transport).open(page.avatar_url) as response:
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 128*1024:
                    return {}
        return {'avatar': base64.b64encode(data).decode('ascii')}
    except SourceError:
        return {}  # Optional artwork; text/history remain available.


async def preview(username: str, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    page,url = await load(username,0,transport)
    return dict(username=username,title=page.title,description=page.description,preview_url=url,**await avatar_metadata(page,transport))


class WebTelegram:
    """One bounded page per call; pagination state is returned for durable commit."""
    def __init__(self, username: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.username,self.transport = username,transport
        self.identity_info: dict[str, Any] | None = None
        self._avatar_url: str | None = None

    async def metadata(self, page: WebPage) -> None:
        avatar = await avatar_metadata(page,self.transport) if self.identity_info is None or page.avatar_url != self._avatar_url else {key:value for key,value in self.identity_info.items() if key == 'avatar'}
        self.identity_info = dict(username=self.username,title=page.title,description=page.description,**avatar)
        self._avatar_url = page.avatar_url

    @staticmethod
    def rows(posts: list[TelegramPublication]) -> list[dict[str, Any]]:
        return [dict(id=max(p.publication.members),date=p.publication.published,post=p) for p in posts]

    async def history_page(self, account: str, peer: int, upper: int, before: int, since: float) -> dict[str, Any]:
        page,_ = await load(self.username,before or (upper+1 if upper else 0),self.transport)
        await self.metadata(page)
        upper = upper or max((max(p.publication.members) for p in page.posts),default=0)
        posts = [p for p in page.posts if max(p.publication.members)<=upper]
        return dict(messages=self.rows(posts),upper=upper,next_before=page.next_before or min((min(p.publication.members) for p in posts),default=before),more=page.next_before is not None)

    async def page(self, account: str, peer: int, after: int, since: float, *, position: dict[str, Any] | None = None) -> dict[str, Any]:
        progress = dict(position or {})
        before = progress.get('catchup_before',0)
        page,_ = await load(self.username,before,self.transport)
        await self.metadata(page)
        upper = progress.get('catchup_upper') or max((max(p.publication.members) for p in page.posts),default=after)
        self.observed = tuple(p.publication for p in page.posts if max(p.publication.members)<=after)
        posts = [p for p in page.posts if after<max(p.publication.members)<=upper]
        reached = not page.next_before or any(min(p.publication.members)<=after for p in page.posts)
        if reached:
            progress = {}
        else:
            progress = dict(catchup_upper=upper,catchup_before=page.next_before)
        return dict(messages=self.rows(posts),more=not reached,next_after=max(after,upper) if reached else after,position=progress)

    async def fetch(self, publication: Publication, staging: DownloadStage | None = None) -> Material:
        page,_ = await load(self.username,max(publication.members)+1,self.transport)
        post = next((p for p in page.posts if p.publication.id==publication.id or set(p.publication.members).intersection(publication.members)),None)
        if post is None:
            raise SourceDeleted()
        teaser = post.publication.teaser
        assert teaser
        assert teaser.source_manifest
        if teaser.source_manifest.post_web_unavailable and post.text.strip():
            from oko.collection.telegram.preview import fragment
            preview, error = await fragment(self.username, publication.id, self.transport, staging)
            previous = publication.teaser.source_manifest if publication.teaser else None
            if error and previous and previous.preview_fragment:
                preview = previous.preview_fragment
            manifest = teaser.source_manifest.model_copy(update={'preview_fragment':preview, 'preview_error':error})
            text = '\n\n'.join(part for part in (post.text, preview) if part)
            return Material(preview.split('\n')[0] if preview else 'Фрагмент предпросмотра Telegram недоступен',
                text, False, 'Сохранён фрагмент предпросмотра; полная публикация недоступна для веб-просмотра' if preview else 'Фрагмент предпросмотра недоступен; откройте пост в Telegram',
                members=post.publication.members, source_manifest=manifest)
        collected = await Downloader(self.transport).collect(post.media,teaser.source_manifest.resources,publication.teaser,staging)
        partial = bool(collected.error and collected.error.code == 'media-unavailable'
                       and (post.text.strip() or collected.attachments))
        manifest = teaser.source_manifest.model_copy(update={'resources':collected.resources, 'download_partial':partial})
        warnings = [resource.reason for resource in collected.resources if resource.reason]
        reasons = list(collected.reasons)
        previous_manifest = publication.teaser.source_manifest if publication.teaser else None
        if previous_manifest and previous_manifest.post!=manifest.post:
            reasons.extend(warnings)
        return Material(teaser.title,post.text,not partial and not manifest.post_web_unavailable and not reasons and not any(r.status=='external' for r in collected.resources),'; '.join(dict.fromkeys([*reasons,*warnings])),
                        members=post.publication.members,attachments=collected.attachments,
                        description=teaser.description,source_manifest=manifest,collection_error=None if partial else collected.error,reply_preview=teaser.reply_preview)
