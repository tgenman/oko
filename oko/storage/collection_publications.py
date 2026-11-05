"""Publication facts and actions needed by the collection worker."""
from typing import TYPE_CHECKING

from oko.collection.adapters import ExternalKey, ExternalReference, Platform
from oko.storage import publication_links

if TYPE_CHECKING:
    from oko.storage.store import Store


class CollectionPublications:
    def __init__(self, store: 'Store') -> None:
        self._store = store

    def unviewed(self, item: str) -> bool:
        return self._store._has_unviewed(item)

    def feedless(self, item: str) -> bool:
        return publication_links.feedless(self._store, item)

    def reference(self, item: str) -> ExternalReference | None:
        row=self._store.db.execute('SELECT platform,external_id,url FROM publications WHERE id=%s AND viewed=0',(item,)).fetchone()
        return ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),row['url']) if row else None

    def feed(self, item: str) -> str:
        return self._store.item_feed(item)

    def viewed(self, item: str) -> None:
        if not self._store.db.in_transaction:
            raise RuntimeError('Collection changes require an owning transaction')
        self._store.viewed(item)
