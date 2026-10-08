from dataclasses import dataclass
from typing import Protocol
import asyncio


@dataclass(frozen=True)
class Publication:
    id: str
    url: str
    published: float
    teaser: 'Material | None' = None
    members: tuple[int, ...] = ()


@dataclass(frozen=True)
class Image:
    name: str
    data: bytes


@dataclass(frozen=True)
class Attachment:
    name: str
    data: bytes
    kind: str
    mime: str


@dataclass(frozen=True)
class Material:
    title: str
    text: str
    full: bool = True
    reason: str = ''
    images: tuple[Image, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    members: tuple[int, ...] = ()


class Source(Protocol):
    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]: ...
    async def fetch(self, publication: Publication) -> Material: ...


class DemoSource:
    """Explicit synthetic source; never connects to user accounts."""
    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]:
        await asyncio.sleep(0)
        return ([Publication(str(i), f'https://example.org/oko/{i}', 1800000000-i)
                 for i in range(1, 4)] if cursor is None else []), '3'

    async def fetch(self, publication: Publication) -> Material:
        await asyncio.sleep(0)
        titles = {'1': 'Око: первый материал', '2': 'Чтение без отвлечений', '3': 'Локальное хранение'}
        return Material(titles[publication.id],
                        'Это тестовая публикация. Открытие и копирование ссылки сохраняют её.\n\n'
                        'Кнопка «Просмотрено» удаляет текст из Око. Это демонстрационный источник, '
                        'прошедший настоящую очередь и сохранённый в Markdown.')
