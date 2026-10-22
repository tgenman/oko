"""The public adapter boundary: no Store or internal publication identifiers."""
import asyncio

import httpx

from tests.support.telegram_web import page


def test_telegram_window_is_inclusive_chronological_and_fixed():
    from oko.collection.telegram.adapter import TelegramWebAdapter

    maximum = [6]
    calls = []

    def response(request):
        before = int(request.url.params.get('before', '0'))
        calls.append(before)
        ids = [i for i in (1, 3, 5, 6, 8) if i <= maximum[0] and (not before or i < before)][-2:]
        return httpx.Response(200, text=page([(i, f'Post {i}') for i in ids], min(ids) if ids and min(ids) > 1 else None))

    async def scenario():
        adapter = TelegramWebAdapter(httpx.MockTransport(response))
        feed = await adapter.resolve_feed('https://telegram.me/s/fixture')
        assert feed.reference.key.external_id == 'https://t.me/s/fixture'
        assert await adapter.cursor_before(feed.reference, feed.latest_cursor, 0) == feed.latest_cursor
        cursor = await adapter.cursor_before(feed.reference, feed.latest_cursor, 2)
        maximum[0] = 8
        first = await adapter.discover(feed.reference, cursor, 2)
        assert [p.metadata.reference.key.external_id for p in first.publications] == ['https://t.me/fixture/3', 'https://t.me/fixture/5']
        assert first.has_more
        second = await adapter.discover(feed.reference, first.next_cursor, 2)
        assert [p.metadata.reference.key.external_id for p in second.publications] == ['https://t.me/fixture/6']
        assert not second.has_more
        replay = await adapter.discover(feed.reference, cursor, 2)
        assert replay == first
        following = await adapter.discover(feed.reference, second.next_cursor, 2)
        assert [p.metadata.reference.key.external_id for p in following.publications] == ['https://t.me/fixture/8']
        assert calls

    asyncio.run(scenario())


def test_album_counts_once_and_media_streams_to_caller_owned_output():
    from io import BytesIO
    from oko.collection.telegram.adapter import TelegramWebAdapter

    anchors = ''.join(f'<a class="grouped_media_wrap" href="https://t.me/fixture/{i}?single"></a>' for i in (5, 6))
    photo = '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo\')"></a>'
    def response(request):
        if request.url.host == 'cdn.example':
            return httpx.Response(200, content=b'photo')
        before = int(request.url.params.get('before', '0'))
        rows = [(3, 'Old'), (5, 'Album'+anchors+photo), (8, 'New')]
        return httpx.Response(200, text=page([(i, text) for i, text in rows if not before or i < before]))

    async def scenario():
        adapter = TelegramWebAdapter(httpx.MockTransport(response))
        feed = await adapter.resolve_feed('https://t.me/s/fixture')
        cursor = await adapter.cursor_before(feed.reference, feed.latest_cursor, 1)
        found = await adapter.discover(feed.reference, cursor, 10)
        assert len(found.publications) == 2
        album = found.publications[0]
        assert album.content['extra']['telegram']['members'] == [5, 6]
        assert album.metadata.reference.key.external_id == 'https://t.me/fixture/5'
        output = BytesIO()
        media = await adapter.download_media(album.metadata.reference, album.media[0], output)
        assert output.getvalue() == b'photo'
        assert media.size_bytes == 5
        assert not output.closed
    asyncio.run(scenario())


def test_failed_page_can_retry_same_window_without_admitting_new_posts():
    from oko.collection.adapters import AdapterException, AdapterErrorKind
    from oko.collection.telegram.adapter import TelegramWebAdapter

    failing = [False]
    maximum = [6]
    def response(request):
        before = int(request.url.params.get('before', '0'))
        if failing[0] and before == 5:
            return httpx.Response(503)
        ids = [i for i in (1, 3, 5, 6, 8) if i <= maximum[0] and (not before or i < before)][-2:]
        return httpx.Response(200, text=page([(i, f'Post {i}') for i in ids], min(ids) if ids and min(ids)>1 else None))

    async def scenario():
        adapter = TelegramWebAdapter(httpx.MockTransport(response))
        feed = await adapter.resolve_feed('https://t.me/s/fixture')
        cursor = await adapter.cursor_before(feed.reference, feed.latest_cursor, 2)
        failing[0] = True
        try:
            await adapter.discover(feed.reference, cursor, 10)
        except AdapterException as error:
            assert error.issue.kind == AdapterErrorKind.temporary
        else:
            raise AssertionError('A failed page must not look like end of history')
        failing[0] = False
        maximum[0] = 8
        retried = await adapter.discover(feed.reference, cursor, 10)
        assert [p.metadata.reference.key.external_id for p in retried.publications] == [
            'https://t.me/fixture/3', 'https://t.me/fixture/5', 'https://t.me/fixture/6']
    asyncio.run(scenario())
