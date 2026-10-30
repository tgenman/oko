"""Subscription changes and their domain consequences; no task ownership."""

from oko.storage.access import repository_access
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from oko.storage.store import Store
from oko.subscriptions.history import InitialHistory
from oko.subscriptions.subscriptions import Overrides, GLOBAL_SERVICE_SETTINGS, AddFeed, FeedPatch

@dataclass(frozen=True)
class SettingsChange:
    value: dict[str, Any]
    changed: bool
    interval: float


class SubscriptionService:
    def __init__(self, repository: Callable[[], Store]) -> None:
        self._storage = repository

    @repository_access
    def update_settings(self, scope: str, owner: str, patch: Overrides) -> SettingsChange:
        repository = self._storage()
        previous = repository.subscriptions.effective(scope, owner)
        value = repository.subscriptions.update_settings(scope, owner, patch)
        if patch.model_fields_set & {'filter_enabled', 'prompt_filters'}:
            affected_feeds = repository.subscriptions.feeds(
                owner if scope == 'feed' else None, folder=owner if scope == 'folder' else None)
            candidates = [value] + [repository.subscriptions.effective('feed', feed['id'], details=False)
                                    for feed in affected_feeds]
            if any(settings['effective']['filter_enabled']['value'] and
                   not settings['effective']['prompt_filters']['value'].strip() for settings in candidates):
                raise ValueError('Для включённой фильтрации нужен непустой промпт')
        repository.fault('settings_after_values')
        changed = bool(patch.model_fields_set - GLOBAL_SERVICE_SETTINGS and value != previous)
        processing_fields = {'filter_enabled','prompt_filters','theses','prompt_theses','processor'}
        if changed and patch.model_fields_set & processing_fields:
            repository.settings_changed(scope, owner)
        repository.fault('settings_after_invalidation')
        return SettingsChange(value, changed, repository.subscriptions.feed_refresh_minutes() * 60)

    @repository_access
    def rename_folder(self, identity: str, title: str) -> dict[str, Any]:
        return self._storage().subscriptions.rename_folder(identity, title)

    @repository_access
    def remove_folder(self, identity: str) -> None:
        self._storage().subscriptions.remove_folder(identity)

    @repository_access
    def add_feed(self, value: AddFeed) -> dict[str, Any]:
        return self._storage().subscriptions.add_feed(value)

    @repository_access
    def patch_feed(self, identity: str, value: FeedPatch) -> tuple[dict[str, Any], bool]:
        repository = self._storage()
        known = repository.subscriptions.feeds(identity)
        if not known:
            raise LookupError('Фид не найден')
        previous = known[0]
        result = repository.subscriptions.update_feed(identity, value)
        if result != previous and value.model_fields_set & {'folder_id', 'new_folder', 'importance', 'public_ai', 'transport'}:
            repository.settings_changed('feed', identity)
        source_fields = value.model_fields_set - {'title', 'folder_id', 'new_folder', 'importance', 'public_ai'}
        return result, any(result.get(field) != previous.get(field) for field in source_fields)

    @repository_access
    def remove_feed(self, identity: str) -> None:
        self._storage().remove_feed(identity)

    @repository_access
    def pause_history(self, identity: str, paused: bool) -> dict[str, Any]:
        return self._storage().pause_history(identity, paused)

    @repository_access
    def change_history(self, identity: str, selection: 'InitialHistory | None') -> dict[str, Any]:
        return self._storage().change_history(identity, selection)

    @repository_access
    def existing_feed(self, url: str) -> dict[str, Any] | None:
        return self._storage().subscriptions.existing_feed(url)
