"""Card projection from borrowed saved facts, without constructing an opened material.

Only the card owns these fields. Text/media expansion and mutable headers belong
to the opened-material path; borrowing here never mutates saved content.
"""
import json
from typing import Any, TYPE_CHECKING
from oko.storage import material_read, publication_links
from oko.storage.reading import saved_content, reader_now
from oko.collection.contracts import SourceManifest
from oko.diagnostics import record_caught

if TYPE_CHECKING:
    from oko.storage.store import Store


def _preview_source(store: 'Store', identity: str) -> dict[str, Any]:
    row = material_read.item(store.db, identity)
    assert row is not None
    content = saved_content(row['content'])
    own = content.get('extra',{}).get('_reader')
    if not isinstance(own,dict):
        own = store.publications.original(identity)[0]
    # Copy only the two collections extended by linked publications. Large
    # manifests, reply previews and source documents remain borrowed facts.
    header = dict(own, title=row['title'], attachments=list(own.get('attachments', [])),
                  _media_urls=dict(own.get('_media_urls', {})))
    for child in publication_links.children(store, identity):
        if 'blocks' in child['content']:
            json.loads(child['media_ids'])
            linked = saved_content(child['content']).get('extra', {}).get('_reader')
            if not isinstance(linked,dict):linked=store.publications.original(child['id'])[0]
            header['attachments'].extend(linked.get('attachments', []))
            header['_media_urls'].update(linked.get('_media_urls', {}))
    return header


def card(store: 'Store', identity: str) -> dict[str, Any] | None:
    row = material_read.item(store.db, identity)
    if row is None:
        return None
    if 'reader_hidden' in row.keys():
        hidden=bool(row['reader_hidden'])
    else:
        hidden=row['status']=='filter_out'
    result: dict[str, Any] = dict(id=identity,feed_id=row['feed_id'],title='Материал загружается',
        published_at=row['published_at'],
        source_url=row['url'],loading=True,preview_url=None,hidden=hidden,
        post_web_unavailable=False,external_attachment_kinds=[])

    if 'blocks' not in row['content']:
        return result
    try:
        media_ids=json.loads(row['media_ids'])
        metadata=json.loads(row['processing_metadata'])
        if not isinstance(media_ids,list) or not isinstance(metadata,dict):
            raise ValueError('Invalid saved publication metadata')
        header=_preview_source(store, identity)
        for saved in header.get('attachments',[]):
            saved['name'],saved['kind'],saved['mime']
        title=header['title']
        if not isinstance(title,str):raise ValueError('Invalid saved publication title')
        attachment=next((a for a in header.get('attachments',[]) if a['kind']=='image'),None)
        jobs = material_read.jobs(store.db, identity)
        fetch = jobs.get('fetch')
        result.update(title=title,
            loading=bool(fetch and (fetch['status']=='running' or fetch['status']=='pending' and fetch['next']<=reader_now(store.clock()))),
            preview_url=header.get('_media_urls',{}).get(attachment['name'],f"/api/v3/materials/{identity}/media/{attachment.get('media_id',attachment['name'])}") if attachment else None)
        result['loading'] = result['loading'] or row['platform'] is not None and store.media.loading(identity)
        manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None
        resources = {r.key: r for r in manifest.resources} if manifest else {}
        result['post_web_unavailable'] = bool(manifest and manifest.post_web_unavailable and manifest.preview_fragment is None)
        result['external_attachment_kinds'] = list(dict.fromkeys(block['kind'] for block in saved_content(row['content']).get('blocks',[])
            if block.get('type')=='external_media' and block.get('kind') in ('audio','video','image','document','unsupported')))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        record_caught('store.card')
        result.update(title='Публикация недоступна',loading=False,preview_url=None,
            post_web_unavailable=False,external_attachment_kinds=[])

    return result
