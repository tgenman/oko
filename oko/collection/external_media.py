"""Saved external-media facts, shared by status and reading projections."""
from typing import Any
import json

from oko.collection.contracts import SourceManifest


def external_only(manifest: SourceManifest | None) -> bool:
    return bool(manifest and any(r.status=='external' and r.external for r in manifest.resources)
        and all(r.status=='external' and r.external or r.status=='loaded' and not r.reason for r in manifest.resources))


def expected_incomplete(manifest: SourceManifest | None) -> bool:
    """A confirmed access restriction alone does not require another download."""
    return bool(manifest and (manifest.post_web_unavailable and all(r.status=='loaded' and not r.reason or r.status=='external' and r.external for r in manifest.resources) or external_only(manifest)))


def saved_external(header: dict[str, Any], base: str) -> dict[str, Any]:
    manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None
    resources = {r.key:r for r in manifest.resources} if manifest else {}
    attachments = []
    for resource in resources.values():
        media = resource.external
        if resource.status!='external' or not media:
            continue
        poster = resources.get(media.poster_key or '')
        attachments.append(dict(media.model_dump(exclude={'poster_key'}),
            poster_url=header.get('_media_urls',{}).get(poster.name,base+'/media/'+poster.name) if poster and poster.status=='loaded' else None))
    return dict(external_attachments=attachments,external_only=external_only(manifest),post_web_unavailable=bool(manifest and manifest.post_web_unavailable and manifest.preview_fragment is None))
