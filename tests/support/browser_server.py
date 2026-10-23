"""Production HTTP reader with temporary storage and synthetic RSS/model adapters."""
import json
import asyncio
from pathlib import Path
import secrets
import re
import socket
import sys
import time
from datetime import datetime, timezone

import httpx
import uvicorn

from tests.support.app import create_app
from tests.support.models import model_transport
from tests.support.codex import CodexService
from tests.support.youtube import PNG, History, feed_xml
from oko.collection.contracts import SourceError
from tests.support.telegram import Telegram
from tests.support.telegram_web import page, external_page, AUDIO, VIDEO, mark_post_unavailable


class BrowserTelegram(Telegram):
    async def accounts(self):
        return ['one', 'two']

    async def identity(self, account):
        return {'one': 9007199254740993, 'two': 202}[account]

    async def info(self, account, peer):
        if peer in ('@switchfixture',-1001234567899):
            return dict(peer=-1001234567899,username='switchfixture',title='Switch channel',kind='channel',forum=False)
        result = await super().info(account, peer)
        result['peer'] = -100101 if str(peer).lstrip('@') == 'personal' else -100202
        return result

    async def page(self,account,peer,after,since):
        if peer==-1001234567899:
            return dict(messages=[dict(id=11,date=datetime.now(timezone.utc).timestamp()-60,text='Client original',group=None,
                media=dict(kind='image',mime='image/png',extension='png'))],more=False,gap=None)
        return await super().page(account,peer,after,since)

    async def messages(self,account,peer,ids):
        if peer==-1001234567899:
            return (await self.page(account,peer,0,0))['messages']
        return await super().messages(account,peer,ids)

    async def download(self,account,peer,id):
        return PNG if peer==-1001234567899 else await super().download(account,peer,id)

    async def history_page(self, account, peer, upper, before, since):
        result = await self.page(account, peer, 0, since)
        return dict(messages=result['messages'], upper=11, next_before=11, more=False)


def response(request):
    if request.url.host == 't.me' and '/previewfragment' in request.url.path:
        from tests.support.telegram_web import forwarded_unavailable_page, preview_metadata_page
        if request.url.path == '/previewfragment/11':
            return httpx.Response(200, text=preview_metadata_page('Сохранённый фрагмент [это не восстановленная ссылка](https://example.test/fake)…\nC:\\folder\n1. Это обычная строка', 'previewfragment/11'))
        return httpx.Response(200, text=forwarded_unavailable_page().replace('fixture/', 'previewfragment/'))
    if request.url.host == 'broken-telegram.example':
        from tests.support.telegram_web import BrokenMediaStream
        return httpx.Response(200, stream=BrokenMediaStream(), headers={'content-type':'video/mp4'})
    if request.url.host == 't.me' and '/brokenstream' in request.url.path:
        media = '<a class="tgme_widget_message_video_player"><video src="https://broken-telegram.example/video.mp4"></video></a>'
        return httpx.Response(200, text=page([(11, 'Текст сохранён до обрыва' + media)]).replace('fixture/', 'brokenstream/'))
    if request.url.host == 'cdn.example':
        return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
    if request.url.host == 't.me' and any('/'+name in request.url.path for name in ('externalmedia','postunavailable')):
        username = 'postunavailable' if '/postunavailable' in request.url.path else 'externalmedia'
        posts = [(11,'External audio post'+AUDIO),(12,'External video post'+VIDEO)]
        if username == 'externalmedia':
            from tests.support.telegram_web import DOCUMENT
            posts.append((13,'External document post'+DOCUMENT))
        if username == 'postunavailable':
            posts.append((13,''))
        html = external_page(posts,before=int(request.url.params.get('before','0')))
        if username == 'postunavailable':
            html = mark_post_unavailable(html,'fixture/13')
        return httpx.Response(200,text=html.replace('fixture',username))
    if request.url.host == 'broken-image.example':
        return httpx.Response(403)
    if request.url.host == 't.me' and '/telegrapherrors' in request.url.path:
        return httpx.Response(200, text=page([(11, 'Текст поста <a href="https://telegra.ph/FailedImage">Статья</a> <a href="https://telegra.ph/MissingArticle">Недоступная статья</a>')]).replace('fixture/', 'telegrapherrors/'))
    if request.url.host == 't.me' and '/hiddenfallback' in request.url.path:
        from tests.support.telegram_web import hidden_fallback_page
        return httpx.Response(200, text=hidden_fallback_page('Available text <tg-emoji emoji-id="1"><b>🔵</b></tg-emoji> <a href="https://example.test/source">Source</a>').replace('fixture/', 'hiddenfallback/'))
    if request.url.host == 't.me':
        return httpx.Response(200, text=page([(11, 'Веб-публикация без аккаунта <a href="https://telegra.ph/Fixture">Статья</a>')]))
    if request.url.host == 'api.telegra.ph':
        if request.url.path == '/getPage/MissingArticle':
            return httpx.Response(503)
        if request.url.path == '/getPage/FailedImage':
            return httpx.Response(200, json={'ok': True, 'result': {'title': 'Статья с изображением', 'content': [
                {'tag': 'p', 'children': ['Текст до изображения.']},
                {'tag': 'img', 'attrs': {'src': 'https://broken-image.example/unavailable.png'}},
                {'tag': 'p', 'children': ['Текст после изображения.']}]}})
        assert request.url.path == '/getPage/Fixture'
        return httpx.Response(200, json={'ok': True, 'result': {'title': 'Статья Telegraph', 'content': [
            {'tag': 'p', 'children': ['Полный текст статьи сохранён локально.']},
            {'tag': 'img', 'attrs': {'src': '/file/fixture.png'}},
            {'tag': 'iframe', 'attrs': {'src': 'https://example.org/embed'}},
        ]}})
    if request.url.host == 'telegra.ph':
        assert request.url.path == '/file/fixture.png'
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.host == 'i.ytimg.com':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.host == 'www.youtube.com':
        assert request.url.path == '/feeds/videos.xml'
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>Synthetic channel</title></feed>')
    assert request.url.host in ('a.test', 'b.test', 'empty.test', 'source.test'), request.url
    if request.url.path == '/image.png':
        return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
    if request.url.path in ('/feed', '/markdown-feed') or request.url.host == 'source.test':
        date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        article_path = 'markdown-article' if request.url.path == '/markdown-feed' else 'article'
        item = '' if request.url.host == 'empty.test' else f'<item><guid>article</guid><title>Статья {request.url.host}</title><link>https://{request.url.host}/{article_path}</link><pubDate>{date}</pubDate><description>Анонс библиотеки.</description></item>'
        return httpx.Response(200, text=f'<rss version="2.0"><channel><title>Синтетический источник</title>{item}</channel></rss>')
    if request.url.path == '/markdown-article':
        return httpx.Response(200, text='<article><p>[YouTube\n\nChrista Pike 911 Calls](https://youtu.be/\n  BPoce9VGatE)</p><p>[Статья](https://www.upstartsmedia.com/p/tolan-raises-30m-for-ai-companion-you-trust)</p></article>')
    assert request.url.path == '/article'
    return httpx.Response(200, text=f'<article><p>Открылась библиотека на 24 места.</p><img src="https://{request.url.host}/image.png"></article>')


def build_app(root, token, *, persistent=False, public_url=None,collection=True,processing_delay=0):
    def youtube_state():
        path=root/'fixture-youtube-state'
        return path.read_text() if path.is_file() else ''
    class Captions:
        async def fetch(self,video):
            state=youtube_state()
            if state=='error':
                raise SourceError('Synthetic caption error')
            return 'Raw YouTube captions.' if state=='ready' else None
    async def external(request):
        if request.url.host=='t.me' and '/switchfixture' in request.url.path:
            return httpx.Response(200,text=page([(11,'Web original')]).replace('fixture/','switchfixture/'))
        if youtube_state() and request.url.host=='www.youtube.com':
            return httpx.Response(200,text=feed_xml())
        if youtube_state() and request.url.host=='i.ytimg.com':
            path=root/'fixture-media-state'
            while path.is_file() and path.read_text()=='held':
                await asyncio.sleep(.01)
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        if request.url.host=='a.test' and request.url.path=='/linked-feed':
            return httpx.Response(200,text='<rss><channel><title>RSS pipeline</title><item><guid>announcement</guid><title>RSS announcement</title><link>https://linked.example/article</link><description>Own announcement.</description></item></channel></rss>')
        if request.url.host=='t.me' and '/linkedposts' in request.url.path:
            return httpx.Response(200,text=page([(11,'Own Telegram post. <a href="https://telegra.ph/ControlledArticle">Article</a>')]).replace('fixture/','linkedposts/'))
        if request.url.host=='linked.example' or request.url.host=='api.telegra.ph' and request.url.path=='/getPage/ControlledArticle':
            state=root/'fixture-article-state'
            while state.is_file() and state.read_text()=='held':
                await asyncio.sleep(.01)
            if state.is_file() and state.read_text()=='error':
                return httpx.Response(503)
            if request.url.host=='api.telegra.ph':
                return httpx.Response(200,json={'ok':True,'result':{'title':'Linked article','content':[{'tag':'p','children':['Article text arrived.']},{'tag':'img','attrs':{'src':'https://controlled.example/linked-image.png'}}]}})
            return httpx.Response(200,text='<title>Linked article</title><article>Article text arrived.<img src="https://controlled.example/linked-image.png"></article>')
        if request.url.host=='controlled.example':
            path=root/'fixture-media-state'
            while path.is_file() and path.read_text()=='held':
                await asyncio.sleep(.01)
            if path.is_file() and path.read_text()=='error':
                return httpx.Response(503)
            return httpx.Response(200,content=PNG,headers={'content-type':'image/png'})
        if request.url.host=='t.me' and '/mediastate' in request.url.path:
            photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://controlled.example/photo.jpg\')"></a>'
            return httpx.Response(200,text=page([(11,'Text before media'+photo)]).replace('fixture/','mediastate/'))
        if request.url.host=='t.me' and '/lateststate' in request.url.path:
            path = root/'fixture-publication.txt'
            text = path.read_text() if path.is_file() else 'Initial publication'
            photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
            return httpx.Response(200, text=page([(11, text+photo)]).replace('fixture/', 'lateststate/'))
        return response(request)
    models=model_transport([])
    from tests.support.filters import transport as filter_transport
    publication_models=filter_transport([],theses=True)
    async def delayed_model(request):
        if request.url.path=='/v1/chat/completions':
            await asyncio.sleep(processing_delay)
            linked_state=root/'fixture-article-state'
            if linked_state.is_file():
                task=json.loads(json.loads(request.content)['messages'][1]['content'])
                result=(dict(hidden=False,removed=[],reason='') if task['operation']=='filter' else
                    dict(points=[dict(text=re.sub(r'\[([^]]+)\]\(https?://[^)]+\)',r'\1',source['text']),sources=[dict(id=source['id'],url=source['url'])])
                                 for source in task['sources'] if source['text'].strip()]))
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(result)}}]})
            state=root/'fixture-model-state'
            if state.is_file():
                if state.read_text()=='error':
                    return httpx.Response(503)
                return await publication_models.handle_async_request(request)
        return await models.handle_async_request(request)
    ai=httpx.MockTransport(delayed_model)
    return create_app(root, token=token, rss_enabled=collection, persistent=persistent, public_url=public_url,
                     rss_transport=httpx.MockTransport(external), telegram_client=BrowserTelegram(), ai_transport=ai, codex_client=CodexService(),
                     youtube_history=History([f'v{n:010d}' for n in range(60, 0, -1)]), youtube_captions=Captions(),
                     wall_clock=lambda:time.time()+{'error':11,'missing':22,'ready':33}.get(youtube_state(),0),
                     lease=60, grace=60)


if __name__ == '__main__':
    token = secrets.token_urlsafe(24)
    app = build_app(Path(sys.argv[1]), token)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    print(json.dumps({'origin': f'http://127.0.0.1:{sock.getsockname()[1]}', 'token': token}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[sock])
