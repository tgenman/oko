"""Accept a discovered page through its dedicated publication repository."""
from collections.abc import Callable, Sequence
from typing import Protocol

from oko.collection.adapters import PublicationContent
from oko.domain.collection import CollectionEffects


class DiscoveryPublications(Protocol):
    def accept(self, publications: Sequence[PublicationContent], cursor: str, feed: str,
               namespace: str = '', *, description: str | None = None,
               successful: bool = True) -> CollectionEffects: ...


class DiscoveryService:
    def __init__(self, publications: Callable[[], DiscoveryPublications]) -> None:
        self._publications = publications

    def accept(self, publications: Sequence[PublicationContent], cursor: str, feed: str,
               namespace: str, *, description: str | None, successful: bool) -> CollectionEffects:
        for publication in publications:
            ref=publication.metadata.reference if isinstance(publication,PublicationContent) else publication.adapter_reference
            if ref is None or not ref.key.external_id:
                raise ValueError('Adapter did not return a publication external key')
        return self._publications().accept(publications, cursor, feed, namespace,
                                           description=description, successful=successful)
