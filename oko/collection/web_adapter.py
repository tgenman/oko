"""Feedless web/Telegraph publications. Files are described, never eagerly saved."""
from dataclasses import replace
from html import escape
import re
from typing import Any,BinaryIO
from urllib.parse import urljoin,urlsplit

from bs4 import BeautifulSoup
from bs4.element import Comment,NavigableString,Tag
import httpx

from oko.collection.adapters import AdapterErrorKind,AdapterException,AdapterIssue,ExternalKey,ExternalReference,MediaReference,Platform,PublicationContent,PublicationMetadata
from oko.collection.adapter_errors import failure
from oko.collection.contracts import SourceError,SourceDeleted
from oko.collection.rss import normalized
from oko.collection.telegram.media import Downloader


def reference(url: str) -> ExternalReference:
    try:
        url=normalized(url)
    except SourceError as error:
        raise failure(error) from error
    parts=urlsplit(url)
    platform=Platform.telegraph if parts.hostname in ('telegra.ph','www.telegra.ph') else Platform.other_web
    if platform==Platform.telegraph:
        url='https://telegra.ph'+parts.path+('?' + parts.query if parts.query else '')
    return ExternalReference(ExternalKey(platform,url),url)


def _node_html(node: Any) -> str:
    if isinstance(node,str):
        return escape(node)
    if not isinstance(node,dict) or not isinstance(node.get('tag'),str) or not isinstance(node.get('children',[]),list):
        raise SourceError('Неверное содержимое Telegraph',code='article-unavailable')
    tag=str(node['tag'])
    if not re.fullmatch('[a-zA-Z][a-zA-Z0-9]*',tag):
        raise SourceError('Неверный тег Telegraph',code='article-unavailable')
    attrs=node.get('attrs',{})
    if not isinstance(attrs,dict):
        raise SourceError('Неверные атрибуты Telegraph',code='article-unavailable')
    supplied=''.join(' '+key+'="'+escape(value,quote=True)+'"' for key,value in attrs.items() if key in ('src','href','type','download','poster') and isinstance(value,str))
    body=''.join(_node_html(child) for child in node.get('children',[]))
    return '<'+tag+supplied+'>'+body+('</'+tag+'>' if tag.lower() not in ('img','br','hr','source') else '')


class _Content:
    def __init__(self, base: str) -> None:
        self.base=base
        self.blocks: list[dict[str,Any]]=[]
        self.media: list[MediaReference]=[]
        self.text: list[str]=[]

    def flush(self) -> None:
        text=''.join(self.text).strip()
        if text:
            self.blocks.append(dict(type='text',role='body',text=text))
        self.text=[]

    def address(self, value: Any) -> str | None:
        address=urljoin(self.base,value) if isinstance(value,str) else ''
        return address if urlsplit(address).scheme in ('http','https') else None

    def file(self, url: str, role: str, *, name: str = '', mime: str | None = None, caption: str = '') -> None:
        self.flush()
        key='web:'+str(len(self.media))+':'+role
        self.media.append(MediaReference(key,url,name,mime))
        self.blocks.append(dict(type='media',ref={'$media':key},role=role,caption=caption))

    def walk(self, node: Any) -> None:
        if isinstance(node,Comment):
            return
        if isinstance(node,NavigableString):
            self.text.append(re.sub(r'\s+',' ',str(node)))
            return
        if not isinstance(node,Tag) or node.name in ('script','style','nav','footer','aside','form'):
            return
        tag=node.name
        if tag in ('img','video','audio'):
            source=node.get('src') or node.get('data-src')
            if not source and tag in ('video','audio'):
                source_node=node.find('source')
                source=source_node.get('src') if source_node else None
            url=self.address(source)
            if url:
                mime=node.get('type')
                self.file(url,'image' if tag=='img' else tag,mime=mime if isinstance(mime,str) else None,caption=str(node.get('alt','')))
            return
        if tag=='iframe':
            self.flush()
            url=self.address(node.get('src'))
            if url:
                self.blocks.append(dict(type='external_media',kind='youtube' if urlsplit(url).hostname in ('www.youtube.com','youtube.com','youtu.be') else 'web',url=url))
            return
        if tag=='a':
            url=self.address(node.get('href'))
            if url and node.has_attr('download'):
                name=node.get('download')
                self.file(url,'attachment',name=name if isinstance(name,str) else '',caption=node.get_text(' ',strip=True))
                return
            if node.find(['img','video','audio']):
                for child in node.children:
                    self.walk(child)
                return
            text=node.get_text(' ',strip=True)
            self.text.append('['+text+']('+url+')' if url and text else text)
            return
        if tag=='blockquote':
            self.flush()
            self.blocks.append(dict(type='quote',text=node.get_text(' ',strip=True),author=None,source_url=self.base))
            return
        if tag=='pre':
            self.flush()
            self.blocks.append(dict(type='text',role='body',text='```\n'+node.get_text()+'\n```'))
            return
        if tag in ('p','div','li','h1','h2','h3','h4','h5','h6','br'):
            self.text.append('\n\n')
        marker='**' if tag in ('b','strong') else '*' if tag in ('i','em') else '#' * int(tag[1])+' ' if re.fullmatch('h[1-6]',tag) else ''
        self.text.append(marker)
        for child in node.children:
            self.walk(child)
        if tag in ('b','strong','i','em'):
            self.text.append(marker)
        if tag in ('p','div','li','h1','h2','h3','h4','h5','h6'):
            self.text.append('\n\n')


class WebAdapter:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport=transport

    async def fetch(self, publication: ExternalReference) -> PublicationContent:
        if publication!=reference(publication.url):
            raise AdapterException(AdapterIssue('invalid_reference','Внешний ключ не соответствует URL',AdapterErrorKind.permanent,publication))
        try:
            base=publication.url
            if publication.key.platform==Platform.telegraph:
                data=await Downloader(self.transport).metadata('https://api.telegra.ph/getPage'+urlsplit(base).path+'?return_content=true')
                result=data.get('result') if isinstance(data,dict) and data.get('ok') is True else None
                if not isinstance(result,dict) or not isinstance(result.get('title'),str) or not isinstance(result.get('content'),list):
                    raise SourceError('Статья Telegraph недоступна',code='article-unavailable')
                raw=('<title>'+escape(result['title'])+'</title><article>'+''.join(_node_html(n) for n in result['content'])+'</article>').encode()
            else:
                async with Downloader(self.transport).open(base,purpose='publication') as response:
                    data_bytes=bytearray()
                    async for part in response.aiter_bytes():
                        data_bytes.extend(part)
                        if len(data_bytes)>4_000_000:
                            raise SourceError('Статья превышает лимит',code='article-limit',retryable=False)
                    raw=bytes(data_bytes)
                    base=str(response.url)
            soup=BeautifulSoup(raw,'html.parser')
            main=soup.select_one('article,main,.entry-content,.post-content,.article-body') or soup.body or soup
            title=main.find('h1') or soup.title
            content=_Content(base)
            content.walk(main)
            content.flush()
            return PublicationContent(PublicationMetadata(publication,title.get_text(' ',strip=True) if title else '',None),
                dict(blocks=content.blocks,extra={}),tuple(content.media))
        except SourceError as error:
            cause=error.__cause__
            if isinstance(cause,httpx.HTTPStatusError) and cause.response.status_code in (404,410):
                error=SourceDeleted()
            raise failure(error,publication,scope='web:'+urlsplit(publication.url).netloc) from error

    async def download_media(self, publication: ExternalReference, media: MediaReference, output: BinaryIO) -> MediaReference:
        current=await self.fetch(publication)
        actual=next((value for value in current.media if value.key==media.key),None)
        if actual is None or actual.locator!=media.locator:
            raise AdapterException(AdapterIssue('media_changed','Ресурс статьи изменился',AdapterErrorKind.media_changed,publication,media.key))
        attachment=any(block.get('ref')=={'$media':media.key} and block.get('role')=='attachment' for block in current.content['blocks'])
        try:
            size,_=await Downloader(self.transport).write(actual.locator,output,allow_empty=attachment)
            return replace(actual,size_bytes=size)
        except SourceError as error:
            raise failure(error,publication,media.key,'web:media:'+urlsplit(actual.locator).netloc) from error
