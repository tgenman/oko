"""Subscription changes and their domain consequences; no task ownership."""

from oko.services.access import repository_access
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from contextlib import AbstractContextManager
from oko.storage.subscriptions import SubscriptionRepository
from oko.subscriptions.history import InitialHistory
from oko.subscriptions.subscriptions import Overrides, GLOBAL_SERVICE_SETTINGS, AddFeed, FeedPatch

@dataclass(frozen=True)
class SettingsChange:
    value: dict[str, Any]
    changed: bool
    interval: float


class SubscriptionEffects(Protocol):
    def require_transaction(self) -> None: ...
    def lock_feed_graph(self) -> None: ...
    def feed_added(self, identity: str) -> None: ...
    def settings_changed(self, scope: str, owner: str) -> None: ...
    def remove_feed(self, identity: str) -> None: ...
    def pause_history(self, identity: str, paused: bool) -> dict[str, Any]: ...
    def change_history(self, identity: str, selection: InitialHistory | None) -> dict[str, Any]: ...
    def checkpoint(self, name: str) -> None: ...


class SubscriptionService:
    def __init__(self, *, access: Callable[[], AbstractContextManager[Any]],
                 repository: Callable[[], SubscriptionRepository], effects: Callable[[], SubscriptionEffects]) -> None:
        self._access, self._repository, self._effects = access, repository, effects

    @repository_access
    def update_settings(self, scope: str, owner: str, patch: Overrides) -> SettingsChange:
        self._effects().require_transaction()
        repository = self._repository()
        previous = repository.effective(scope, owner)
        value = repository.update_settings(scope, owner, patch)
        if patch.model_fields_set & {'filter_enabled', 'prompt_filters'}:
            affected_feeds = repository.feeds(
                owner if scope == 'feed' else None, folder=owner if scope == 'folder' else None)
            candidates = [value] + [repository.effective('feed', feed['id'], details=False)
                                    for feed in affected_feeds]
            if any(settings['effective']['filter_enabled']['value'] and
                   not settings['effective']['prompt_filters']['value'].strip() for settings in candidates):
                raise ValueError('Для включённой фильтрации нужен непустой промпт')
        self._effects().checkpoint('settings_after_values')
        changed = bool(patch.model_fields_set - GLOBAL_SERVICE_SETTINGS and value != previous)
        processing_fields = {'filter_enabled','prompt_filters','theses','prompt_theses','processor'}
        if changed and patch.model_fields_set & processing_fields:
            self._effects().settings_changed(scope, owner)
        self._effects().checkpoint('settings_after_invalidation')
        return SettingsChange(value, changed, repository.feed_refresh_minutes() * 60)

    @repository_access
    def rename_folder(self, identity: str, title: str) -> dict[str, Any]:
        self._effects().require_transaction()
        return self._repository().rename_folder(identity, title)

    @repository_access
    def remove_folder(self, identity: str) -> None:
        self._effects().require_transaction()
        self._repository().remove_folder(identity)

    @repository_access
    def add_feed(self, value: AddFeed) -> dict[str, Any]:
        repository = self._repository()
        self._effects().lock_feed_graph()
        feed = repository.add_feed(value)
        self._effects().feed_added(feed['id'])
        return feed

    @repository_access
    def patch_feed(self, identity: str, value: FeedPatch) -> tuple[dict[str, Any], bool]:
        repository = self._repository()
        self._effects().lock_feed_graph()
        known = repository.feeds(identity)
        if not known:
            raise LookupError('Фид не найден')
        previous = known[0]
        result = repository.update_feed(identity, value)
        if result != previous and value.model_fields_set & {'folder_id', 'new_folder', 'importance', 'public_ai', 'transport'}:
            self._effects().settings_changed('feed', identity)
        source_fields = value.model_fields_set - {'title', 'folder_id', 'new_folder', 'importance', 'public_ai'}
        return result, any(result.get(field) != previous.get(field) for field in source_fields)

    @repository_access
    def remove_feed(self, identity: str) -> None:
        self._effects().remove_feed(identity)

    @repository_access
    def pause_history(self, identity: str, paused: bool) -> dict[str, Any]:
        return self._effects().pause_history(identity, paused)

    @repository_access
    def change_history(self, identity: str, selection: 'InitialHistory | None') -> dict[str, Any]:
        return self._effects().change_history(identity, selection)

    @repository_access
    def existing_feed(self, url: str) -> dict[str, Any] | None:
        return self._repository().existing_feed(url)
