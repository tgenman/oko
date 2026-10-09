from oko.collection.contracts import Material, Publication
import asyncio
from oko.collection.demo import DemoSource


class EventSource:
    async def discover(self, cursor):
        return ([Publication(str(i), f'https://example.org/event/{i}', 1800000000+i*60) for i in range(3)] if cursor is None else []), '3'

    async def fetch(self, publication):
        return Material(['Открытие', 'Обновление', 'Другой сюжет'][int(publication.id)], ['Открылась библиотека на 24 места.', 'Библиотека открылась. Мэрия сообщает 30 мест.', 'Спутник вышел на орбиту.'][int(publication.id)])


class LateSource(DemoSource):
    async def fetch(self, publication):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return Material('Late secret title', 'Late secret body')
