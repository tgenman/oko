import asyncio
import os
from pathlib import Path
from oko.sources import DemoSource
from oko.__main__ import run


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
    run(fault=fault, source=ResistantSource() if os.environ.get('OKO_TEST_FAULT') == 'resist_cancel' else None)
