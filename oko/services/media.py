"""Local resource state and durable files; no Job or adapter invocation."""
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from oko.storage.media import MediaDownload, StagedMedia
from oko.services.access import repository_access
from oko.collection.adapters import MediaReference, AdapterIssue


class MediaRepository(Protocol):
    def owner(self, identity: str, /) -> str | None: ...
    def begin(self, identity: str, attempt: str, revalidate: bool) -> MediaDownload | None: ...
    def stage(self, identity: str, download: MediaDownload, actual: MediaReference) -> StagedMedia: ...
    def accept(self, identity: str, download: MediaDownload, actual: MediaReference, staged: StagedMedia, /) -> bool: ...
    def failed(self, identity: str, issue: AdapterIssue, /) -> tuple[str,str] | None: ...
    def release(self, download: MediaDownload) -> None: ...
    def cleanup(self) -> None: ...
    def retired_file_cleaned(self, key: str | None) -> bool: ...
    def current(self, identity: str, download: MediaDownload) -> bool: ...


class MediaService:
    def __init__(self, *, access: Callable[[], AbstractContextManager[Any]],
                 repository: Callable[[], MediaRepository]) -> None:
        self._access, self._repository = access, repository

    @repository_access
    def owner(self, identity: str) -> str | None:
        return self._repository().owner(identity)

    @repository_access
    def begin(self, identity: str, attempt: str, revalidate: bool) -> MediaDownload | None:
        return self._repository().begin(identity,attempt,revalidate)

    def stage(self, identity: str, download: MediaDownload, actual: MediaReference) -> StagedMedia:
        return self._repository().stage(identity,download,actual)

    @repository_access
    def accept(self, identity: str, download: MediaDownload, actual: MediaReference, staged: StagedMedia) -> None:
        self._repository().accept(identity,download,actual,staged)

    @repository_access
    def failed(self, identity: str, issue: AdapterIssue) -> tuple[str,str] | None:
        return self._repository().failed(identity,issue)

    def release(self, download: MediaDownload) -> None:
        self._repository().release(download)

    def cleanup(self) -> None:
        self._repository().cleanup()

    def retired_file_cleaned(self, key: str | None) -> bool:
        return self._repository().retired_file_cleaned(key)

    @repository_access
    def current(self, identity: str, download: MediaDownload) -> bool:
        return self._repository().current(identity,download)
