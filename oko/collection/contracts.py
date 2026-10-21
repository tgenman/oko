from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Protocol, Literal
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
    observed_only: bool = False


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


class ExternalMedia(BaseModel):
    """Source-confirmed media that this access method cannot download."""
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    kind: Literal['audio', 'video', 'image', 'document', 'unsupported']
    source_url: str
    reason: Literal['public_preview_unavailable'] = 'public_preview_unavailable'
    restriction: Literal['size_limit'] | None = None
    title: str | None = None
    author: str | None = None
    duration: str | None = None
    poster_key: str | None = None


class ResourceManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    key: str
    origin: str
    order: int
    name: str = Field(pattern=r'^(?:media|image)-[0-9]+\.[a-z0-9]+$')
    kind: str
    mime: str
    status: Literal['missing', 'loaded', 'unsupported', 'external'] = 'missing'
    reason: str = ''
    sha256: str | None = None
    external: ExternalMedia | None = None


class ArticleManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    url: str
    title: str | None = None
    text: str | None = None
    resource_keys: tuple[str, ...] = ()
    reason: str = ''


class SourceManifest(BaseModel):
    post_web_unavailable: bool = False
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


class ReplyPreview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    author: str | None = None
    text: str
    source_url: str | None = None


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
    reply_preview: ReplyPreview | None = None


@dataclass(frozen=True)
class SourceIdentity:
    """Detached Telegram claims/artwork; missing artwork differs from removal."""
    peer: int | None
    username: str | None
    usernames: tuple[str, ...]
    avatar_present: bool = False
    avatar: str | None = None

    @classmethod
    def from_info(cls, info: dict[str, Any] | None) -> 'SourceIdentity | None':
        if info is None:
            return None
        return cls(info.get('peer'), info.get('username'), tuple(info.get('usernames', ())),
                   'avatar' in info, info.get('avatar'))

    def as_info(self) -> dict[str, Any]:
        value: dict[str, Any] = dict(peer=self.peer, username=self.username, usernames=list(self.usernames))
        if self.avatar_present:
            value['avatar'] = self.avatar
        return value


@dataclass(frozen=True)
class SourceObservation:
    # None means metadata was not provided; empty string explicitly clears it.
    gap: str | None = None
    changes: str | None = None
    identity: SourceIdentity | None = None
    description: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    publications: tuple[Publication, ...]
    cursor: str
    observation: SourceObservation = SourceObservation()
    continuation: float | None = None
    retry_error: 'SourceError | None' = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'publications', deepcopy(self.publications))
        object.__setattr__(self, 'retry_error', deepcopy(self.retry_error))


class CaptionsClient(Protocol):
    async def fetch(self, video: str) -> str | None: ...


@dataclass(frozen=True)
class FetchRequirements:
    staging: bool = False
    captions: bool = False


@dataclass(frozen=True)
class FetchResources:
    staging: DownloadStage | None = None
    captions: CaptionsClient | None = None
    request_captions: bool = True


@dataclass(frozen=True)
class FetchResult:
    material: Material
    observation: SourceObservation = SourceObservation()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'material', deepcopy(self.material))


class Source(Protocol):
    @property
    def fetch_requirements(self) -> FetchRequirements: ...

    @property
    def changes(self) -> str | None: ...

    async def discover(self, cursor: str | None) -> DiscoveryResult: ...
    async def fetch(self, publication: Publication, resources: FetchResources = FetchResources()) -> FetchResult: ...


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


class SourceDeferred(SourceError):
    """Shared external wait; stop further requests in the current attempt."""


class SourceDeleted(SourceError):
    def __init__(self) -> None:
        super().__init__('Публикация удалена или недоступна в источнике')


class SourceSkipped(SourceError):
    """An intentionally ignored publication, with no future collection."""
    diagnostic_skip = True
