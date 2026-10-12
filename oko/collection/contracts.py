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
    def __init__(self, label: str, delay: float = 60, *, remote_traceback: str = '') -> None:
        self.label, self.delay = label, delay
        self.remote_traceback = remote_traceback
        self.remote_type = self.remote_message = self.remote_location = ''

    @classmethod
    def from_worker(cls, label: str, details: dict[str, str], delay: float = 60) -> 'SourceError':
        error = cls(label, delay, remote_traceback=details.get('traceback', ''))
        error.remote_type = details.get('error_type', '')
        error.remote_message = details.get('message', '')
        error.remote_location = details.get('location', '')
        return error


class SourceDeleted(SourceError):
    def __init__(self) -> None:
        super().__init__('Публикация удалена или недоступна в источнике')
