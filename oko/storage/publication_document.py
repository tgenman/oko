"""Reader metadata from a collection payload; no content or file persistence."""
from typing import Any
from oko.collection.contracts import Material


def caption_state(youtube: dict[str, Any], legacy: dict[str, Any] | None = None) -> str:
    """Map source caption state to the reader's completeness status."""
    old=legacy.get('captions') if legacy is not None else None
    if isinstance(old, str):
        return old
    state=youtube.get('captions')
    return state if isinstance(state, str) and state in ('available', 'unavailable', 'not_requested') else 'error'


def header(material: Material) -> dict[str,Any]:
    header: dict[str,Any]=dict(title=material.title,full=material.full,reason=material.reason,members=list(material.members),captions=material.captions,
        attachments=[])
    if material.adapter_content and 'youtube' in material.adapter_content.content.get('extra', {}):
        header.pop('captions')
    blocks=material.adapter_content.content.get('blocks', []) if material.adapter_content else []
    if material.description and not any(b.get('type')=='text' and b.get('role')=='description' for b in blocks):
        header['description']=material.description
    if material.source_manifest:
        header['source_manifest']=material.source_manifest.model_dump(mode='json')
    return header
