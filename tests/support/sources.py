from oko.collection.contracts import FetchResult
from tests.support.synthetic_material import material as Material
import asyncio
from oko.collection.demo import DemoSource


class LateSource(DemoSource):
    async def fetch(self, publication):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return FetchResult(Material('Late secret title', 'Late secret body'))
