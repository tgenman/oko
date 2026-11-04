"""Accept a discovered page through its dedicated publication repository."""
from collections.abc import Callable
from typing import Protocol

from oko.collection.contracts import Publication
from oko.domain.collection import CollectionEffects


class DiscoveryPublications(Protocol):
    def accept(self, publications: list[Publication], cursor: str, feed: str,
               namespace: str = '', *, description: str | None = None,
               successful: bool = True) -> CollectionEffects: ...


class DiscoveryService:
    def __init__(self, publications: Callable[[], DiscoveryPublications]) -> None:
        self._publications = publications

    def accept(self, publications: list[Publication], cursor: str, feed: str,
               namespace: str, *, description: str | None, successful: bool) -> CollectionEffects:
        for publication in publications:
            if publication.adapter_reference is None or not publication.adapter_reference.key.external_id:
                raise ValueError('Adapter did not return a publication external key')
        return self._publications().accept(publications, cursor, feed, namespace,
                                           description=description, successful=successful)
