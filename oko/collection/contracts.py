from dataclasses import dataclass
from typing import Protocol, Literal
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, JsonValue, Field


@dataclass(frozen=True)
class Publication:
    id: str
    url: str
    published: float
    teaser: 'Material | None' = None
    members: tuple[int, ...] = ()
    published_known: bool = True


@dataclass(frozen=True)
class Image:
    name: str
    data: bytes


@dataclass(frozen=True)
class DownloadStage:
    path: Path
    job: int
    generation: int
    admitted: Callable[[], bool] = lambda: True


@dataclass(frozen=True)
class StagedPayload:
    path: Path
    size: int
    sha256: str


class ResourceManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    key: str
    origin: str
    order: int
    name: str = Field(pattern=r'^(?:media|image)-[0-9]+\.[a-z0-9]+$')
    kind: str
    mime: str
    status: Literal['missing', 'loaded', 'unsupported'] = 'missing'
    reason: str = ''
    sha256: str | None = None


class ArticleManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    url: str
    title: str | None = None
    text: str | None = None
    resource_keys: tuple[str, ...] = ()
    reason: str = ''


class SourceManifest(BaseModel):
    source_url: str | None = None
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    post: dict[str, JsonValue]
    resources: tuple[ResourceManifest, ...] = ()
    articles: tuple[ArticleManifest, ...] = ()


@dataclass(frozen=True)
class Attachment:
    name: str
    data: bytes | StagedPayload
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
    collection_error: 'SourceError | None' = None
    description: str = ''
    source_manifest: SourceManifest | None = None


class Source(Protocol):
    async def discover(self, cursor: str | None) -> tuple[list[Publication], str]: ...
    async def fetch(self, publication: Publication) -> Material: ...


class SourceError(Exception):
    def __init__(self, label: str, delay: float = 60, *, remote_traceback: str = '', code: str | None = None, retryable: bool = True) -> None:
        self.label, self.delay = label, delay
        self.code = code
        self.retryable = retryable
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


class SourceSkipped(SourceError):
    """An intentionally ignored publication, with no future collection."""
    diagnostic_skip = True
