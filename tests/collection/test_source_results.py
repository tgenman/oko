"""Source outcomes belong to one call, including optional Telegram metadata."""
import asyncio



def test_configuration_detaches_history_and_keeps_youtube_discovery_cache():
    import httpx
    from oko.collection.sources import FeedRuntime, Sources, SourceSettings
    from oko.collection.youtube.adapter import YouTubeAdapter
    from oko.workers.feed_discovery import discover_youtube
    from tests.support.youtube import PNG, VIDEO, feed_xml
    from tests.support.app import NoCaptions

    def reply(request):
        if request.url.host == 'i.ytimg.com':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        return httpx.Response(200, text=feed_xml())

    async def check():
        sources = Sources(None, 'fixture', True, httpx.MockTransport(reply), NoCaptions(), None)
        history = dict(mode='count',count=1,run='fixture')
        settings = SourceSettings('video', 'youtube', 'https://www.youtube.com/feeds/videos.xml?channel_id='+('UC'+'a'*22), history)
        updates = sources.configure([settings])
        assert updates.created == ('video',)
        assert sources.description('video').startswith('YouTube:')
        history['count'] = 500
        runtime=sources.sources['video']
        assert isinstance(runtime,FeedRuntime) and isinstance(runtime.adapter,YouTubeAdapter)
        initial=await discover_youtube(runtime.adapter,runtime.settings.url,runtime.settings.initial_history or {},None)
        result=await discover_youtube(runtime.adapter,runtime.settings.url,runtime.settings.initial_history or {},initial.cursor)
        assert [p.metadata.reference.key.external_id for p in result.publications] == [VIDEO]
        # Applying the unchanged original settings must not discard cached titles.
        original = SourceSettings('video', 'youtube', settings.url, dict(mode='count',count=1,run='fixture'))
        assert sources.configure([original]).created == ()
        adapter=sources.publication_adapter('video')
        assert adapter is not None
        loaded=await adapter.fetch(result.publications[0].metadata.reference)
        assert loaded.metadata.title == result.publications[0].metadata.title
        assert loaded.metadata.title != 'YouTube'
        assert 'captions' not in loaded.content['extra']['youtube']

    asyncio.run(check())
