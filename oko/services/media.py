"""Local resource state and durable files; no Job or adapter invocation."""
from collections.abc import Callable
from oko.storage.store import Store
from oko.storage.media import MediaDownload, StagedMedia
from oko.storage.access import repository_access
from oko.collection.adapters import MediaReference, AdapterIssue


class MediaService:
    def __init__(self, storage: Callable[[],Store]) -> None:
        self._storage=storage

    @repository_access
    def owner(self, identity: str) -> str | None:
        return self._storage().media.owner(identity)

    @repository_access
    def begin(self, identity: str, attempt: str, revalidate: bool) -> MediaDownload | None:
        return self._storage().media.begin(identity,attempt,revalidate)

    def stage(self, identity: str, download: MediaDownload, actual: MediaReference) -> StagedMedia:
        return self._storage().media.stage(identity,download,actual)

    @repository_access
    def accept(self, identity: str, download: MediaDownload, actual: MediaReference, staged: StagedMedia) -> None:
        self._storage().media.accept(identity,download,actual,staged)

    @repository_access
    def failed(self, identity: str, issue: AdapterIssue) -> None:
        self._storage().media.failed(identity,issue)

    def release(self, download: MediaDownload) -> None:
        self._storage().media.release(download)

    def cleanup(self) -> None:
        self._storage().media.cleanup()

    @repository_access
    def current(self, identity: str, download: MediaDownload) -> bool:
        return self._storage().media.current(identity,download)
