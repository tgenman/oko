"""Source outcomes belong to one call, including optional Telegram metadata."""
import asyncio
import time

from oko.collection.telegram.client_channel_source import TelegramClientSource
from tests.support.telegram import Telegram


def test_discovery_returns_detached_metadata_and_continuation():
    class Client(Telegram):
        metadata = dict(peer=-1001234567890, username='first', usernames=['first'], avatar='first-image',kind='channel',title='Fixture')

        async def info(self, account, peer):
            return self.metadata

        async def page(self, account, peer, after, since):
            return dict(messages=[dict(id=11, date=time.time()-60, text='Saved post', group=None, media=None)], more=not after, gap=None)

    async def check():
        client = Client()
        source = TelegramClientSource(client, 'fixture', -1001234567890, 1)
        source.initial_history=dict(mode='count',count=1,run='fixture')
        pending=await source.discover(None)
        assert pending.continuation is not None
        first = await source.discover(pending.cursor)
        assert [p.id for p in first.publications] == ['11']
        assert first.continuation is None
        fetched = await source.fetch(first.publications[0])
        client.metadata['username'] = 'second'
        client.metadata['usernames'].append('second')
        client.metadata['avatar'] = None
        second = await source.discover(first.cursor)
        assert second.continuation is None
        assert first.observation.identity.as_info() == dict(peer=-1001234567890, username='first', usernames=['first'], avatar='first-image',kind='channel')
        assert fetched.observation.identity == first.observation.identity
        assert second.observation.identity.as_info()['avatar'] is None
        del client.metadata['avatar']
        third = await source.discover(second.cursor)
        assert 'avatar' not in third.observation.identity.as_info()

    asyncio.run(check())




def test_configuration_detaches_history_and_keeps_youtube_discovery_cache():
    import httpx
    from oko.collection.contracts import Publication
    from oko.collection.sources import Sources, SourceSettings
    from tests.support.youtube import PNG, VIDEO, feed_xml
    from tests.support.app import NoCaptions

    def reply(request):
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        return httpx.Response(200, text=feed_xml())

    async def check():
        sources = Sources(None, 'demo', True, httpx.MockTransport(reply), None, NoCaptions(), None)
        history = dict(mode='count',count=1,run='fixture')
        settings = SourceSettings('video', 'youtube', 'https://www.youtube.com/feeds/videos.xml?channel_id='+('UC'+'a'*22), 1, history)
        updates = sources.configure([settings])
        assert updates.created == ('video',)
        assert sources.description('video').startswith('YouTube:')
        history['count'] = 500
        initial = await sources.sources['video'].discover(None)
        result = await sources.sources['video'].discover(initial.cursor)
        assert [p.id for p in result.publications] == [VIDEO]
        # Applying the unchanged original settings must not discard cached titles.
        original = SourceSettings('video', 'youtube', settings.url, 1, dict(mode='count',count=1,run='fixture'))
        assert sources.configure([original]).created == ()
        plain = Publication(VIDEO, 'https://www.youtube.com/watch?v='+VIDEO, 0)
        loaded = await sources.sources['video'].fetch(plain)
        assert loaded.material.title == result.publications[0].teaser.title
        assert loaded.material.title != 'YouTube'
        assert loaded.material.captions == 'not_requested'

    asyncio.run(check())
