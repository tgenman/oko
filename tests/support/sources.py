
from tests.support.synthetic_material import material as Material
import asyncio
from tests.support.synthetic_source import SyntheticSource


class LateSource(SyntheticSource):
    async def fetch(self, publication):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return Material('Late secret title', 'Late secret body')
