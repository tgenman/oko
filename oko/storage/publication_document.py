"""Reader metadata from a collection payload; no content or file persistence."""
from typing import Any


def stored_content(content: dict[str,Any]) -> dict[str,Any]:
    """Remove transient completeness projections before persisting content."""
    from copy import deepcopy
    result=deepcopy(content)
    extra=result.get('extra')
    if isinstance(extra,dict):
        extra.pop('_collection_reason',None)
        extra.pop('_reader',None)
        youtube=extra.get('youtube')
        if isinstance(youtube,dict):
            youtube.pop('captions',None)
            youtube.pop('captions_digest',None)
    return result
