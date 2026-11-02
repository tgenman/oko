"""Compact synthetic source data; text exists only in test fixtures."""
from dataclasses import dataclass
from typing import Any
from oko.collection.contracts import Material
from oko.collection.adapters import ExternalKey, ExternalReference, Platform, PublicationContent, PublicationMetadata


@dataclass(frozen=True, init=False)
class SyntheticMaterial(Material):
    images: tuple[Any, ...] = ()
    attachments: tuple[Any, ...] = ()

    def __init__(self, title: str, text: str = '', full: bool = True, reason: str = '',
                 members: tuple[int, ...] = (), captions: str | None = None,
                 collection_error: Any = None, description: str = '', source_manifest: Any = None,
                 adapter_content: Any = None, images: tuple[Any, ...] = (), attachments: tuple[Any, ...] = ()) -> None:
        if adapter_content is None:
            reference=ExternalReference(ExternalKey(Platform.other_web, 'synthetic'), 'https://fixture.invalid/synthetic')
            adapter_content=PublicationContent(PublicationMetadata(reference,title,None),
                {'blocks':[{'type':'text','role':'body','text':text}], 'extra':{}})
        super().__init__(title, full, reason, members, captions, collection_error, description, source_manifest, adapter_content)
        object.__setattr__(self, 'images', images)
        object.__setattr__(self, 'attachments', attachments)


def material(title: str, text: str, **kwargs: Any) -> Material:
    return SyntheticMaterial(title, text, **kwargs)
