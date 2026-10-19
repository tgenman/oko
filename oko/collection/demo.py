import asyncio
from .contracts import Publication, Material, DiscoveryResult, FetchResult, FetchResources, FetchRequirements, SourceObservation


class DemoSource:
    """Explicit synthetic source; never connects to user accounts."""
    fetch_requirements = FetchRequirements()
    changes: str | None = None

    async def discover(self, cursor: str | None) -> DiscoveryResult:
        await asyncio.sleep(0)
        return DiscoveryResult(tuple([Publication(str(i), f'https://example.org/oko/{i}', 1800000000-i)
                 for i in range(1, 4)] if cursor is None else []), '3', SourceObservation())

    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult:
        await asyncio.sleep(0)
        titles = {'1': 'Око: первый материал', '2': 'Чтение без отвлечений', '3': 'Локальное хранение'}
        return FetchResult(Material(titles[publication.id],
                        'Это тестовая публикация. Открытие и копирование ссылки сохраняют её.\n\n'
                        'Кнопка «Просмотрено» удаляет текст из Око. Это демонстрационный источник, '
                        'прошедший настоящую очередь и сохранённый в Markdown.'), SourceObservation())
