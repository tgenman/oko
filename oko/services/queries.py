"""Reader query interface: saved facts, batched scopes and version projections.

HTTP knows these facts, never the Store, connection, or physical storage layout.
Callers hold admission across synchronous projections; input batching retains
its existing query plan and request-local caches.
"""
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from oko.domain.reader import WorkSummary
from oko.storage.problems import CommandRecord
from oko.storage.reader_facts import ReaderFacts
from oko.processing.config import ProcessorConfig
from oko.storage.composition import Navigation, ReadingComposition
from oko.storage.subscriptions import SubscriptionRepository
from oko.storage.publications import Publications
from oko.storage.filter_results import FilterResults
from oko.storage.processing_coordination import ProcessingState
from oko.storage.media import MediaFiles
from oko.storage.reader_versions import ReaderVersions

from oko.services.access import repository_access


class VersionQueries:
    def __init__(self, access: Callable[[], AbstractContextManager[Any]],
                 versions: Callable[[], ReaderVersions], subscriptions: Callable[[], SubscriptionRepository]) -> None:
        self._access, self._versions, self._subscriptions = access, versions, subscriptions

    @property
    @repository_access
    def identity(self) -> str:
        return self._versions().identity

    @repository_access
    def version(self, kind: str, identity: str) -> dict[str, Any] | None:
        row = self._versions().version(kind, identity)
        return dict(row) if row else None

    @repository_access
    def source_revision(self) -> int:
        return self._subscriptions().source_revision()

    @repository_access
    def history_versions(self) -> list[Any]:
        return self._versions().history_versions()

    @repository_access
    def time_boundaries(self) -> tuple[Any, ...]:
        return self._versions().time_boundaries()

    @repository_access
    def advance(self) -> None:
        self._versions().advance()


class ReadingQueries:
    def __init__(self, *, access: Callable[[], AbstractContextManager[Any]], facts: ReaderFacts,
                 subscriptions: Callable[[], SubscriptionRepository], reading: Callable[[], ReadingComposition],
                 publications: Callable[[], Publications], filters: Callable[[], FilterResults],
                 processing: Callable[[], ProcessingState], media: Callable[[], MediaFiles],
                 versions: Callable[[], ReaderVersions]) -> None:
        self._access, self._facts, self._subscriptions, self._reading = access, facts, subscriptions, reading
        self._publications, self._filters, self._processing, self._media, self._versions = publications, filters, processing, media, versions

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
    def hidden_problem_objects(self, materials: set[str], feeds: set[str]) -> tuple[set[str],set[str]]:
        return self._facts.hidden_problem_objects(materials,feeds)

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
        repository = self._subscriptions()
        if folder is not None:
            return repository.feeds(identity, folder=folder)
        return repository.feeds() if identity is None else repository.feeds(identity)

    @repository_access
    def feeds_with_history(self, identity: str | None = None) -> list[dict[str, Any]]:
        return self._subscriptions().feeds_with_history(identity)

    @repository_access
    def feed_resources(self, identities: set[str] | None = None) -> dict[str, tuple[dict[str, Any], dict[str, Any] | None]]:
        return self._facts.feed_resources(identities)

    @repository_access
    def folders(self) -> list[dict[str, Any]]:
        return self._subscriptions().folders()

    @repository_access
    def bindings(self, feeds: list[str] | None = None) -> dict[str, dict[str, Any]]:
        return self._subscriptions().telegram.bindings(feeds)

    @repository_access
    def binding(self, feed: str) -> dict[str, Any] | None:
        return self._subscriptions().telegram.binding(feed)

    @repository_access
    def feed_details(self, feed: str) -> dict[str, Any]:
        return self._subscriptions().details.snapshot(feed)

    @repository_access
    def check_owner(self, scope: str, owner: str) -> None:
        return self._subscriptions().check_visible_owner(scope, owner)

    @repository_access
    def effective(self, scope: str, owner: str, *, details: bool = True) -> dict[str, Any]:
        return self._subscriptions().effective(scope, owner, details=details)

    @repository_access
    def telegram_http_settings(self) -> dict[str, Any]:
        return self._subscriptions().telegram_http_settings()

    @repository_access
    def navigation(self) -> Navigation:
        return self._reading().navigation()

    @repository_access
    def page(self, options: dict[str, Any], cursor: str | None, limit: int) -> tuple[list[str], int] | None:
        return self._reading().page(options, cursor, limit)

    @repository_access
    def card_validators(self, identities: list[str]) -> dict[str, tuple[Any, ...]]:
        return self._facts.card_validators(identities)

    @repository_access
    def card(self, identity: str) -> dict[str, Any] | None:
        return self._facts.card(identity)




    @repository_access
    def unified_publication(self, identity: str) -> dict[str, Any]:
        return self._facts.unified_publication(identity)

    @repository_access
    def filter(self, identity: str) -> dict[str, Any]:
        return self._filters().read(identity)

    @repository_access
    def media_loading(self, identity: str) -> bool:
        return self._media().loading(identity)

    @repository_access
    def media_file(self, identity: str, name: str) -> tuple[Path, str] | None:
        return self._media().visible_file(identity, name)

    @repository_access
    def content_version(self, identity: str) -> str:
        return self._facts.content_version(identity)

    @repository_access
    def processor_config(self) -> ProcessorConfig | None:
        return self._facts.processor_config()

    @repository_access
    def command_record(self, identity: str) -> CommandRecord | None:
        return self._facts.command_record(identity)

    @repository_access
    def command_succeeded(self, context: str) -> bool:
        return self._facts.command_succeeded(context)

    @repository_access
    def work_summary(self) -> list[WorkSummary]:
        return self._facts.work_summary()

    @repository_access
    def clock(self) -> float:
        return self._facts.clock()

    @repository_access
    def wall_clock(self) -> float:
        return self._facts.wall_clock()


    @property
    @repository_access
    def versions(self) -> VersionQueries:
        return VersionQueries(self._access, self._versions, self._subscriptions)

    @repository_access
    def retry_settings(self) -> dict[str, int]:
        return self._subscriptions().retry_settings()

    @repository_access
    def validate_retry_settings(self, patch: dict[str, Any]) -> None:
        self._subscriptions().validate_retry_settings(patch)
