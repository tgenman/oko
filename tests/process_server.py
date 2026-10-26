import asyncio
import os
from pathlib import Path
from oko.collection.demo import DemoSource
import oko.__main__ as entrypoint
from tests.support.app import create_app, OwnedServerTransport


def fault(point: str) -> None:
    if point == os.environ.get('OKO_TEST_FAULT'):
        os._exit(79)


class HeldPresenceBody:
    """Pin the crash-before-registration-body interleaving in process tests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def held(message):
            if message['type'] == 'http.response.body':
                await asyncio.Event().wait()
            await send(message)
        target = scope.get('path') == '/api/v3/client-sessions' and scope.get('method') == 'POST'
        await self.app(scope, receive, held if target else send)


class ResistantSource(DemoSource):
    async def fetch(self, publication):
        import sys
        root = Path(sys.argv[sys.argv.index('--data') + 1])
        (root / 'fetch-started').touch()
        while True:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                continue


if __name__ == '__main__':
    import sys
    if '--database-url-file' not in sys.argv and '--data' in sys.argv:
        root = Path(sys.argv[sys.argv.index('--data')+1])
        # Parent-owned scenario state persists across child process restarts.
        credential = root / '.test-database-url'
        if credential.is_file():
            sys.argv.extend(['--database-url-file', str(credential)])
    origin = os.environ.get('OKO_TEST_MODEL_ORIGIN')
    def fixture_app(root, **options):
        if os.environ.get('OKO_TEST_WEB_MEDIA'):
            import httpx
            from tests.support.telegram_web import page
            def response(request):
                if request.url.host=='cdn.example':
                    return httpx.Response(200,content=b'process-media',headers={'content-type':'image/jpeg'})
                photo='<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn.example/photo.jpg\')"></a>'
                return httpx.Response(200,text=page([(11,'Process text'+photo)]))
            options['rss_transport']=httpx.MockTransport(response)
        if clock_file := os.environ.get('OKO_TEST_CLOCK'):
            def tick():
                return float(Path(clock_file).read_text())
            options.update(clock=tick, wall_clock=tick)
        if origin:
            options['ai_transport'] = OwnedServerTransport(origin)
        app = create_app(root, **options)
        if os.environ.get('OKO_TEST_HOLD_PRESENCE_BODY') == '1':
            app.add_middleware(HeldPresenceBody)
        return app
    entrypoint.create_app = fixture_app
    entrypoint.run(fault=fault, source=ResistantSource() if os.environ.get('OKO_TEST_FAULT') == 'resist_cancel' else None)
