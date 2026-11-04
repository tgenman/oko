"""Source outcomes belong to one call, including optional Telegram metadata."""
import asyncio



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
        sources = Sources(None, 'fixture', True, httpx.MockTransport(reply), NoCaptions(), None)
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
