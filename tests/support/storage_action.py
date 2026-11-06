"""Execute a storage fixture operation within an application transaction."""
from typing import Any

from oko.storage.store import Store
from oko.storage.transaction import transaction


def storage_action(store: Store, method: str, *args: Any, **kwargs: Any) -> Any:
    if store.db.transaction_depth:
        return getattr(store, method)(*args, **kwargs)
    with transaction(store.db):
        return getattr(store, method)(*args, **kwargs)
