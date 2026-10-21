"""Full direct Telegraph articles shared by Telegram web/client collection.

Link discovery/API behavior adapted from DIYgod/RSSHub
lib/routes/telegram/telegraph.ts at d24f5136487a388662ec6656e4f1ba4ff46b0ed8
(AGPL-3.0). Oko saves plain text and local media, preserves incomplete copies,
and does not execute iframe/scripts or crawl articles' ordinary links.
"""
from dataclasses import replace
import asyncio
import hashlib
import re
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

from oko.collection.contracts import Material, DownloadStage, SourceError, SourceDeferred, SourceManifest, ArticleManifest, ResourceManifest
from oko.collection.telegram.contracts import ExpectedMedia
from oko.collection.telegram.media import Downloader


def direct_articles(text: str) -> tuple[str, ...]:
    result = []
    for match in re.finditer(r'https?://(?:www\.)?telegra\.ph/[^\s<>\])]+',text,re.I):
        address = urlsplit(match[0].rstrip('.,;!'))
        if address.username or address.password or address.port or not address.path.strip('/') or address.path.startswith('/file/'):
            continue
        url = 'https://telegra.ph/'+quote(unquote(address.path.lstrip('/')),safe='/-_')
        if url not in result:
            result.append(url)
    return tuple(result)


def article_content(url: str, content: Any) -> tuple[str, tuple[ExpectedMedia, ...], tuple[ResourceManifest, ...]]:
    if not isinstance(content,list):
        raise SourceError('Telegraph не предоставил содержание статьи',code='article-unavailable')
    expected: list[ExpectedMedia] = []
    resources: list[ResourceManifest] = []
    count = 0
    def walk(node: Any, path: tuple[int, ...]) -> str:
        nonlocal count
        count += 1
        if count>10000 or len(path)>50:
            raise SourceError('Слишком сложное содержимое Telegraph',code='metadata-limit',retryable=False)
        if isinstance(node,str):
            return node
        if not isinstance(node,dict) or not isinstance(node.get('tag'),str):
            raise SourceError('Неверное содержимое Telegraph',code='article-unavailable')
        tag = node['tag'].lower()
        attrs = node.get('attrs',{})
        if not isinstance(attrs,dict):
            raise SourceError('Неверные атрибуты Telegraph',code='article-unavailable')
        children = node.get('children',[])
        if not isinstance(children,list):
            raise SourceError('Неверное содержимое Telegraph',code='article-unavailable')
        if tag in ('script','style'):
            return ''
        source = attrs.get('src') if isinstance(attrs.get('src'),str) else None
        if tag in ('img','video','audio','iframe'):
            source = urljoin('https://telegra.ph',source) if source else None
            kind = 'photo' if tag=='img' else tag if tag in ('video','audio') else 'unsupported'
            key = 'telegraph:'+url+':'+'.'.join(map(str,path))+':'+kind
            extension,mime,display = {'photo':('jpg','image/jpeg','image'),'video':('mp4','video/mp4','video'),
                'audio':('ogg','audio/ogg','audio'),'unsupported':('bin','application/octet-stream','file')}[kind]
            if source and kind=='photo' and urlsplit(source).path.lower().endswith(('.png','.webp','.gif')):
                extension = urlsplit(source).path.rsplit('.',1)[1].lower()
                mime = 'image/'+extension
            name = 'media-'+str(int(hashlib.sha256(key.encode()).hexdigest()[:16],16))+'.'+extension
            reason = 'Внешнее встраивание не сохранено локально' if kind=='unsupported' else 'Файл статьи не предоставлен' if not source else ''
            if kind=='photo':
                media = ExpectedMedia(key,'photo',source,name,mime,reason=reason or None)
            elif kind=='video':
                media = ExpectedMedia(key,'video',source,name,mime,reason=reason or None)
            elif kind=='audio':
                media = ExpectedMedia(key,'audio',source,name,mime,reason=reason or None)
            else:
                media = ExpectedMedia(key,'unsupported',None,name,mime,reason=reason)
            expected.append(media)
            resources.append(ResourceManifest(key=key,origin=url,order=len(resources),name=name,kind=display,
                mime=mime,status='missing' if media.url else 'unsupported',reason=reason))
            if kind=='unsupported':
                return '\n'+reason+(': '+source if source else '')+'\n'
            return '\n['+{'photo':'Изображение статьи','video':'Видео статьи','audio':'Аудио статьи'}[kind]+']\n'
        body = ''.join(walk(child,(*path,index)) for index,child in enumerate(children))
        if tag=='a':
            href = attrs.get('href')
            if isinstance(href,str):
                href = urljoin('https://telegra.ph',href)
                if urlsplit(href).scheme in ('http','https'):
                    return '['+body.strip()+']('+href+')'
        if tag=='br':
            return '\n'
        if tag=='blockquote':
            return '\n'+'\n'.join('> '+line for line in body.strip().splitlines())+'\n'
        return '\n'+body+'\n' if tag in ('p','aside','figure','figcaption','li','h3','h4','pre') else body
    body = ''.join(walk(node,(index,)) for index,node in enumerate(content))
    return re.sub(r'\n{3,}','\n\n',body).strip(),tuple(expected),tuple(resources)


async def enrich(material: Material, previous: Material | None, staging: DownloadStage | None,
                 transport: httpx.AsyncBaseTransport | None) -> Material:
    source = material.source_manifest
    post_text = str(source.post.get('text',material.text)) if source else material.text
    urls = direct_articles(post_text)
    if not urls:
        return material
    source = source or SourceManifest(post=dict(text=post_text,members=list(material.members)))
    old_manifest = previous.source_manifest if previous else None
    old_articles = {article.url:article for article in old_manifest.articles} if old_manifest else {}
    old_resources = {resource.key:resource for resource in old_manifest.resources} if old_manifest else {}
    old_attachments = {attachment.name:attachment for attachment in previous.attachments} if previous else {}
    articles: list[ArticleManifest] = []
    resources = list(source.resources)
    attachments = list(material.attachments)
    reasons = [material.reason] if material.reason and not material.full else []
    retry = material.collection_error
    sections = [material.text]
    downloader = Downloader(transport)
    for url in urls:
        if staging and not staging.admitted():
            raise asyncio.CancelledError
        article = ArticleManifest(url=url)
        try:
            path = urlsplit(url).path
            data = await downloader.metadata('https://api.telegra.ph/getPage'+path+'?return_content=true')
            if staging and not staging.admitted():
                raise asyncio.CancelledError
            if not isinstance(data,dict) or data.get('ok') is not True or not isinstance(data.get('result'),dict):
                raise SourceError('Статья Telegraph недоступна',code='article-unavailable')
            result = data['result']
            if not isinstance(result.get('title'),str):
                raise SourceError('Telegraph не предоставил заголовок статьи',code='article-unavailable')
            body,expected,templates = article_content(url,result.get('content'))
            collected = await downloader.collect(expected,templates,previous,staging)
            offset = len(resources)
            resources.extend(resource.model_copy(update={'order':offset+index}) for index,resource in enumerate(collected.resources))
            attachments.extend(collected.attachments)
            reasons.extend(collected.reasons)
            if collected.error and (retry is None or collected.error.retryable):
                retry = collected.error
            article = ArticleManifest(url=url,title=result['title'],text=body,resource_keys=tuple(resource.key for resource in collected.resources))
            known = old_articles.get(url)
            if known and known.text is not None and (known.title,known.text,known.resource_keys)!=(article.title,article.text,article.resource_keys):
                reasons.extend(resource.reason for resource in collected.resources if resource.reason)
        except SourceError as error:
            if isinstance(error, SourceDeferred):
                raise
            old = old_articles.get(url)
            if old and old.text is not None:
                article = old.model_copy(update={'reason':error.label})
                for key in old.resource_keys:
                    resource = old_resources.get(key)
                    if resource:
                        resources.append(resource.model_copy(update={'order':len(resources),'reason':error.label}))
                        attachment = old_attachments.get(resource.name)
                        if attachment:
                            attachments.append(attachment)
                        elif resource.status!='loaded':
                            reasons.append(resource.reason or error.label)
            else:
                article = article.model_copy(update={'reason':error.label})
                reasons.append(error.label+' · '+url)
            if retry is None or not error.retryable:
                retry = error
        articles.append(article)
        if article.text is not None:
            sections.append('---\n\n# '+(article.title or 'Telegraph')+'\n\nИсходник: '+url+'\n\n'+article.text)
        else:
            sections.append('Telegraph: '+url+'\nСтатья ещё не сохранена.')
    manifest = source.model_copy(update={'resources':tuple(resources),'articles':tuple(articles)})
    return replace(material,text='\n\n'.join(sections),full=material.full and not reasons,
                   reason='; '.join(value for value in dict.fromkeys([material.reason,*reasons,*[resource.reason for resource in resources if resource.reason],*[article.reason for article in articles if article.reason]]) if value),attachments=tuple(attachments),
                   source_manifest=manifest,collection_error=retry)
