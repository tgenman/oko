"""Construct current publication content for synthetic source scenarios."""
import base64
import hashlib
from typing import Any
from oko.collection.adapters import ExternalKey, ExternalReference, Platform, PublicationContent, PublicationMetadata, MediaReference


def material(title: str, text: str = '', *, full: bool = True, images: tuple[Any, ...] = (), attachments: tuple[Any, ...] = ()) -> PublicationContent:
    reference = ExternalReference(ExternalKey(Platform.other_web, 'synthetic'), 'https://fixture.invalid/synthetic')
    blocks: list[dict[str, Any]] = [dict(type='text', role='body', text=text)]
    media = []
    for attachment in (*images, *attachments):
        digest = hashlib.sha256(attachment.data).hexdigest()
        locator = 'data:application/octet-stream;base64,' + base64.b64encode(attachment.data).decode()
        media.append(MediaReference(attachment.name, locator, attachment.name, getattr(attachment, 'mime', 'image/png'), len(attachment.data), digest))
        blocks.append(dict(type='media', role=getattr(attachment, 'kind', 'image'), ref={'$media': attachment.name}))
    return PublicationContent(PublicationMetadata(reference, title, None), dict(blocks=blocks, extra={}), tuple(media), needs_fetch=not full)


SyntheticMaterial = material
