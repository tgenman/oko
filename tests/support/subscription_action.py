"""Run subscription fixture writes at an application-owned boundary."""
from typing import Any

from oko.storage.store import Store
from oko.storage.transaction import transaction


def subscription_action(store: Store, method: str, *args: Any, **kwargs: Any) -> Any:
    if store.db.transaction_depth:
        return getattr(store.subscriptions, method)(*args, **kwargs)
    with transaction(store.db):
        return getattr(store.subscriptions, method)(*args, **kwargs)
