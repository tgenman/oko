"""Bind subscription operations to the current reader storage."""
from collections.abc import Callable

from oko.services.subscriptions import SubscriptionService
from oko.storage.store import Store
from oko.storage.subscription_effects import SubscriptionEffects


def subscription_service(storage: Callable[[], Store]) -> SubscriptionService:
    return SubscriptionService(access=lambda: storage().db.access_lock,
        repository=lambda: storage().subscriptions, effects=lambda: SubscriptionEffects(storage()))
