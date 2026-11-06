"""Run synthetic YouTube admission changes inside an owned transaction."""
from typing import Any

from oko.storage.store import Store
from oko.storage.transaction import transaction


def youtube_action(store: Store, method: str, *args: Any) -> None:
    if store.db.transaction_depth:
        getattr(store.youtube, method)(*args)
    else:
        with transaction(store.db):
            getattr(store.youtube, method)(*args)
