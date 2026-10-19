"""Source outcomes belong to one call, including optional Telegram metadata."""
import asyncio
import time

from oko.collection.telegram.source import TelegramSource
from tests.support.telegram import Telegram


def test_discovery_returns_detached_metadata_and_continuation():
    class Client(Telegram):
        metadata = dict(peer=-1001234567890, username='first', usernames=['first'], avatar='first-image')

        async def info(self, account, peer):
            return self.metadata

        async def page(self, account, peer, after, since):
            return dict(messages=[dict(id=11, date=time.time()-60, text='Saved post', group=None, media=None)], more=not after, gap=None)

    async def check():
        client = Client()
        source = TelegramSource(client, 'fixture', -1001234567890, 1)
        first = await source.discover(None)
        assert [p.id for p in first.publications] == ['11']
        assert first.continuation == 0
        fetched = await source.fetch(first.publications[0])
        client.metadata['username'] = 'second'
        client.metadata['usernames'].append('second')
        client.metadata['avatar'] = None
        second = await source.discover(first.cursor)
        assert second.continuation is None
        assert first.observation.identity.as_info() == dict(peer=-1001234567890, username='first', usernames=['first'], avatar='first-image')
        assert fetched.observation.identity == first.observation.identity
        assert second.observation.identity.as_info()['avatar'] is None
        del client.metadata['avatar']
        third = await source.discover(second.cursor)
        assert 'avatar' not in third.observation.identity.as_info()

    asyncio.run(check())


def test_history_returns_progress_and_detached_partial_error_together():
    from oko.collection.contracts import SourceError
    from oko.collection.telegram.contracts import HistoryEnvelope
    from tests.support.telegram_history import Archive

    class Client(Archive):
        error = SourceError('Account limited', 0)
        failed = False

        async def history_page(self, account, peer, upper, before, since, **scope):
            if before:
                self.failed = True
                self.error.remote_type = 'FloodWaitError'
                raise self.error
            return await super().history_page(account, peer, upper, before, since)

        async def page(self, account, peer, after, since, **scope):
            if self.failed:
                self.error.label = 'A later attempt'
            return await super().page(account, peer, after, since)

    async def check():
        source = TelegramSource(Client(), 'fixture', -1001234567890, 1)
        source.initial_history = dict(mode='count', count=10, since=None, paused=False)
        initial = await source.discover(None)
        live = await source.discover(initial.cursor)
        limited = await source.discover(live.cursor)
        assert limited.publications == ()
        assert limited.continuation == .1
        assert limited.retry_error.remote_type == 'FloodWaitError'
        assert HistoryEnvelope.decode(limited.cursor, adapter='client').selection.error == 'Account limited'
        await source.discover(limited.cursor)
        assert limited.retry_error.label == 'Account limited'

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
        history = dict(mode='date', since=0)
        settings = SourceSettings('video', 'youtube', 'https://www.youtube.com/feeds/videos.xml', 1, history)
        updates = sources.configure([settings])
        assert updates.created == ('video',)
        assert sources.description('video').startswith('YouTube:')
        history['since'] = 4_000_000_000
        result = await sources.sources['video'].discover(None)
        assert [p.id for p in result.publications] == [VIDEO]
        # Applying the unchanged original settings must not discard cached titles.
        original = SourceSettings('video', 'youtube', settings.url, 1, dict(mode='date', since=0))
        assert sources.configure([original]).created == ()
        plain = Publication(VIDEO, 'https://www.youtube.com/watch?v='+VIDEO, 0)
        loaded = await sources.sources['video'].fetch(plain)
        assert loaded.material.title == result.publications[0].teaser.title
        assert loaded.material.title != 'YouTube'
        assert loaded.material.captions == 'not_requested'

    asyncio.run(check())
