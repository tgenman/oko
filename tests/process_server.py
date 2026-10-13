import asyncio
import os
from pathlib import Path
from oko.collection.demo import DemoSource
import oko.__main__ as entrypoint
from tests.support.app import create_app, OwnedServerTransport


def fault(point: str) -> None:
    if point == os.environ.get('OKO_TEST_FAULT'):
        os._exit(79)


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
    origin = os.environ.get('OKO_TEST_MODEL_ORIGIN')
    def fixture_app(root, **options):
        if origin:
            options['ai_transport'] = OwnedServerTransport(origin)
        return create_app(root, **options)
    entrypoint.create_app = fixture_app
    entrypoint.run(fault=fault, source=ResistantSource() if os.environ.get('OKO_TEST_FAULT') == 'resist_cancel' else None)
