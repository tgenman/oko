"""Validate the persisted, platform-neutral Publication document."""
from copy import deepcopy
from typing import Any


def stored_content(content: dict[str,Any]) -> dict[str,Any]:
    """Reject obsolete projections instead of silently discarding their data."""
    result=deepcopy(content)
    if unexpected:=result.keys()-{'blocks','extra'}:
        raise ValueError('Unexpected publication fields: '+', '.join(sorted(unexpected)))
    extra=result.get('extra',{})
    if not isinstance(extra,dict):
        raise ValueError('Publication extra must be an object')
    forbidden={'_reader','_collection_reason','_collection_revision','_links','_media_urls','_input_hash','source_manifest','completeness','title','description','reply_preview','captions','members','changed','full','reason','edited'} & extra.keys()
    if forbidden:
        raise ValueError('Obsolete publication fields: '+', '.join(sorted(forbidden)))
    for platform,keys in (('telegram',{'manifest','description','title','reply_preview','edited'}),('youtube',{'captions','captions_digest','description','title','reply_preview','edited'})):
        value=extra.get(platform)
        if platform in extra:
            if not isinstance(value,dict):
                raise ValueError('Publication '+platform+' extra must be an object')
            if unexpected:=keys & value.keys():
                raise ValueError('Obsolete '+platform+' fields: '+', '.join(sorted(unexpected)))
    return result
