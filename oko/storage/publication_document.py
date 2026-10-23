"""Reader metadata from a collection payload; no content or file persistence."""
from typing import Any
from oko.collection.contracts import Material


def header(material: Material) -> dict[str,Any]:
    header: dict[str,Any]=dict(title=material.title,full=material.full,reason=material.reason,members=list(material.members),captions=material.captions,
        attachments=[dict(name=a.name,kind=a.kind,mime=a.mime) for a in material.attachments])
    if material.description:
        header['description']=material.description
    if material.source_manifest:
        header['source_manifest']=material.source_manifest.model_dump(mode='json')
    if material.reply_preview:
        header['reply_preview']=material.reply_preview.model_dump()
    return header
