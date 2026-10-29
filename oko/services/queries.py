"""Reader query interface: saved facts, batched scopes and version projections.

HTTP knows these facts, never the Store, connection, or physical storage layout.
Callers hold admission across synchronous projections; input batching retains
its existing query plan and request-local caches.
"""
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from oko.storage.store import Store, WorkSummary
from oko.storage.problems import CommandRecord
from oko.storage.reader_facts import ReaderFacts
from oko.processing.llamacpp import ProcessorConfig
from oko.storage.composition import Navigation

from oko.storage.access import repository_access


class VersionQueries:
    def __init__(self, storage: Callable[[], Store]) -> None:
        self._storage = storage

    @property
    @repository_access
    def identity(self) -> str:
        return self._storage().read_versions.identity

    @property
    @repository_access
    def offset(self) -> float:
        return self._storage().read_versions.offset

    @repository_access
    def batch(self, keys: set[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
        return {key: dict(value) for key, value in self._storage().read_versions.batch(keys).items()}

    @repository_access
    def version(self, kind: str, identity: str) -> dict[str, Any] | None:
        row = self._storage().read_versions.version(kind, identity)
        return dict(row) if row else None

    @repository_access
    def time_boundaries(self) -> tuple[float | None, float | None, float | None]:
        from oko.storage.reading import reader_now, reader_wall
        store = self._storage()
        wall = reader_wall(store.wall_clock())
        row = store.db.execute("""SELECT
            (SELECT next FROM jobs WHERE status='pending' AND kind IN ('fetch','filter','theses') AND next>%s ORDER BY next LIMIT 1),
            (SELECT blocked_until FROM youtube_access WHERE id=1 AND blocked_until>%s),
            (SELECT finished FROM reader_problems WHERE finished>%s ORDER BY finished LIMIT 1)""",
            (reader_now(store.clock()), wall, wall-86400)).fetchone()
        return (row[0], row[1], row[2]) if row else (None, None, None)

    @repository_access
    def advance(self) -> None:
        self._storage().read_versions.advance()


class ReadingQueries:
    def __init__(self, storage: Callable[[], Store]) -> None:
        self._storage = storage
        self._facts = ReaderFacts(storage)

    @repository_access
    def item(self, identity: str) -> dict[str, Any] | None:
        return self._facts.item(identity)

    @repository_access
    def jobs(self, identity: str) -> dict[str, dict[str, Any]]:
        return self._facts.jobs(identity)

    @repository_access
    def feed(self, identity: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return self._facts.feed(identity)

    @repository_access
    def inputs(self, identities: set[str], *, include_subtitles: bool = True) -> AbstractContextManager[None]:
        return self._facts.inputs(identities, include_subtitles=include_subtitles)

    @repository_access
    def avatar(self, feed: str, revision: str) -> tuple[bytes, str] | None:
        return self._facts.avatar(feed, revision)

    @repository_access
    def material_feeds(self, identities: set[str]) -> dict[str, str]:
        return self._facts.material_feeds(identities)

    @repository_access
    def linked_preview(self, identity: str) -> dict[str, Any] | None:
        return self._facts.linked_preview(identity)

    @repository_access
    def was_viewed(self, identity: str) -> bool:
        return self._facts.was_viewed(identity)

    @repository_access
    def viewed_ids(self) -> list[str]:
        return self._facts.viewed_ids()

    @repository_access
    def feed_members(self, identity: str) -> set[str]:
        return self._facts.feed_members(identity)

    @repository_access
    def deletion_feeds(self) -> set[str]:
        return self._facts.deletion_feeds()

    @repository_access
    def feeds(self, identity: str | None = None, *, folder: str | None = None) -> list[dict[str, Any]]:
        repository = self._storage().subscriptions
        if folder is not None:
            return repository.feeds(identity, folder=folder)
        return repository.feeds() if identity is None else repository.feeds(identity)

    @repository_access
    def feed_resources(self, identities: set[str] | None = None) -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
        return self._facts.feed_resources(identities)

    @repository_access
    def folders(self) -> list[dict[str, Any]]:
        return self._storage().subscriptions.folders()

    @repository_access
    def bindings(self, feeds: list[str] | None = None) -> dict[str, dict[str, Any]]:
        return self._storage().subscriptions.telegram.bindings(feeds)

    @repository_access
    def binding(self, feed: str) -> dict[str, Any] | None:
        return self._storage().subscriptions.telegram.binding(feed)

    @repository_access
    def feed_details(self, feed: str) -> dict[str, Any]:
        return self._storage().subscriptions.details.snapshot(feed)

    @repository_access
    def check_owner(self, scope: str, owner: str) -> None:
        return self._storage().subscriptions.check_owner(scope, owner)

    @repository_access
    def effective(self, scope: str, owner: str, *, details: bool = True) -> dict[str, Any]:
        return self._storage().subscriptions.effective(scope, owner, details=details)

    @repository_access
    def telegram_http_settings(self) -> dict[str, Any]:
        return self._storage().subscriptions.telegram_http_settings()

    @repository_access
    def navigation(self) -> Navigation:
        return self._storage().reading.navigation()

    @repository_access
    def page(self, options: dict[str, Any], cursor: str | None, limit: int) -> tuple[list[str], int] | None:
        return self._storage().reading.page(options, cursor, limit)

    @repository_access
    def card_validators(self, identities: list[str]) -> dict[str, tuple[str, int | None, float | None]]:
        return self._facts.card_validators(identities)

    @repository_access
    def card(self, identity: str) -> dict[str, Any] | None:
        from oko.storage.card_read import card
        return card(self._storage(), identity)

    @repository_access
    def material(self, identity: str) -> dict[str, Any] | None:
        return self._storage().reading.material(identity)

    @repository_access
    def provenance(self, identity: str, kind: str) -> dict[str, Any] | None:
        return self._storage().publications.provenance(identity, kind)

    @repository_access
    def original(self, identity: str) -> tuple[dict[str, Any], str]:
        return self._storage().original(identity)

    @repository_access
    def source_deleted(self, identity: str) -> bool:
        return self._storage().publications.source_deleted(identity)

    @repository_access
    def filter(self, identity: str) -> dict[str, Any]:
        return self._storage().filters.read(identity)

    @repository_access
    def theses_signature(self, identity: str) -> str | None:
        return self._storage().processing.theses.signature(identity)

    @repository_access
    def media_loading(self, identity: str) -> bool:
        return self._storage().media.loading(identity)

    @repository_access
    def media_file(self, identity: str, name: str) -> tuple[Path, str] | None:
        return self._storage().media.file(identity, name)

    @repository_access
    def content_version(self, identity: str) -> str:
        return self._storage().content_version(identity)

    @repository_access
    def processor_config(self) -> ProcessorConfig | None:
        return self._storage().processor_config()

    @repository_access
    def command_record(self, identity: str) -> CommandRecord | None:
        return self._storage().problems.command_record(identity)

    @repository_access
    def work_summary(self) -> list[WorkSummary]:
        return self._storage().work_summary()

    @repository_access
    def clock(self) -> float:
        return self._storage().clock()

    @repository_access
    def wall_clock(self) -> float:
        return self._storage().wall_clock()

    @repository_access
    def theses_parts(self, identity: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
        theses = self._storage().processing.theses
        return theses.read(identity), theses.provenance(identity), theses.status(identity)

    @property
    @repository_access
    def versions(self) -> VersionQueries:
        return VersionQueries(self._storage)

    @repository_access
    def retry_settings(self) -> dict[str, int]:
        return self._storage().subscriptions.retry_settings()

    @repository_access
    def validate_retry_settings(self, patch: dict[str, Any]) -> None:
        self._storage().subscriptions.validate_retry_settings(patch)
