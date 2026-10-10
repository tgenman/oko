from dataclasses import dataclass
from typing import Protocol


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
    captions: str | None = None


class Source(Protocol):
    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]: ...
    async def fetch(self, publication: Publication) -> Material: ...


class SourceError(Exception):
    def __init__(self, label: str, delay: float = 60) -> None:
        self.label, self.delay = label, delay


class SourceDeleted(SourceError):
    def __init__(self) -> None:
        super().__init__('Публикация удалена или недоступна в источнике')
