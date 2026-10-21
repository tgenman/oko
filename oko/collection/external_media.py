"""Saved external-media facts, shared by status and reading projections."""
from typing import Any
import json

from oko.collection.contracts import SourceManifest


def external_only(manifest: SourceManifest | None) -> bool:
    return bool(manifest and any(r.status=='external' and r.external for r in manifest.resources)
        and all(r.status=='external' and r.external or r.status=='loaded' and not r.reason for r in manifest.resources)
        and all(not a.reason for a in manifest.articles))


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
            poster_url=base+'/media/'+poster.name if poster and poster.status=='loaded' else None))
    return dict(external_attachments=attachments,external_only=external_only(manifest))
