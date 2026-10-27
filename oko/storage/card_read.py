"""Card projection from borrowed saved facts, without constructing an opened material.

Only the card owns these fields. Text/media expansion and mutable headers belong
to the opened-material path; borrowing here never mutates saved content.
"""
import json
from typing import Any, TYPE_CHECKING
from oko.storage import material_read, publication_links, youtube_media
from oko.storage.reading import saved_content, saved_bytes, reader_now
from oko.storage.composition import snippet, telegram_title
from oko.collection.contracts import SourceManifest

if TYPE_CHECKING:
    from oko.storage.store import Store


def _preview_source(store: 'Store', identity: str) -> tuple[dict[str, Any], str]:
    row = material_read.item(store.db, identity)
    assert row is not None
    content = saved_content(row['content'])
    own = content['extra']['_reader']
    # Copy only the two collections extended by linked publications. Large
    # manifests, reply previews and source documents remain borrowed facts.
    header = dict(own, attachments=list(own.get('attachments', [])),
                  _media_urls=dict(own.get('_media_urls', {})))
    for child in publication_links.children(store, identity):
        if child['complete']:
            linked = saved_content(child['content']).get('extra', {}).get('_reader', {})
            header['changed'] = bool(header.get('changed') or linked.get('changed'))
            header['attachments'].extend(linked.get('attachments', []))
            header['_media_urls'].update(linked.get('_media_urls', {}))
    settings = store.subscriptions.effective('feed', row['feed_id'], details=False)
    needs_title = (settings['feed']['kind'] == 'telegram' and settings['feed'].get('transport') != 'rss'
                   and 'reply_preview' not in own.get('source_manifest', {}).get('post', {}))
    if own.get('description') and not needs_title:
        return header, ''
    text = '\n\n'.join(b['text'] for b in content.get('blocks', []) if b['type'] == 'text' and b.get('role') == 'body')
    if 'youtube' in content.get('extra', {}):
        path = youtube_media.subtitle_path(store, youtube_media.subtitle_id(content))
        if path:
            try:
                captions = saved_bytes(path).decode('utf-8')
                text = '\n\n'.join((text, captions)) if text else captions
            except (OSError, UnicodeError):
                pass  # The opened material reports unavailable subtitle bytes.
    linked_text = publication_links.text(store, identity)
    return header, '\n\n'.join(part for part in (text, linked_text) if part)


def _state(status: str | None, error: str | None = None) -> str:
    if error and status != 'running':
        return 'waiting'
    return {'done':'ready','pending':'queued','off':'disabled',None:'disabled'}.get(status, status or 'disabled')


def card(store: 'Store', identity: str) -> dict[str, Any] | None:
    row = material_read.item(store.db, identity)
    if row is None or row['viewed']:
        return None
    result: dict[str, Any] = dict(id=identity,feed_id=row['feed_id'],title='Материал загружается',
        published_at=row['published'] if row['published_known'] else None,
        source_url=row['url'],snippet='',availability='pending',loading=False,changed=False,
        revision_id=None,preview_url=None,hidden=False,filter_state='disabled',theses_state='disabled')
    if not row['complete']:
        job = material_read.jobs(store.db, identity).get('fetch')
        result['loading'] = bool(job and job['status'] in ('pending', 'running'))
        return result
    header,text=_preview_source(store, identity)
    filtered=store.filters.read(identity)
    description=header.get('description','')
    preview=description or text
    if filtered['status']=='done':
        for _ in range(1 if description else 2):
            for fragment in filtered['removed']:
                preview=preview.replace(fragment,'')
    settings=store.subscriptions.effective('feed',row['feed_id'], details=False)
    title=header['title']
    if settings['feed']['kind']=='telegram' and settings['feed'].get('transport')!='rss':
        title=telegram_title(header,text)
    attachment=next((a for a in header.get('attachments',[]) if a['kind']=='image'),None)
    jobs = material_read.jobs(store.db, identity)
    fetch = jobs.get('fetch')
    theses=jobs.get('theses')
    theses_state=_state(theses['status'] if theses else 'pending',theses['error'] if theses else None) if store.processing.theses.signature(identity) else 'disabled'
    result.update(title=title,snippet=snippet(preview),availability='available',hidden=bool(filtered['hidden']),
        filter_state=_state(filtered['status'],filtered.get('error')),theses_state=theses_state,
        changed=bool(header.get('changed')),revision_id=store.content_version(identity),
        loading=bool(fetch and (fetch['status']=='running' or fetch['status']=='pending' and fetch['next']<=reader_now(store.clock()))),
        preview_url=header.get('_media_urls',{}).get(attachment['name'],f"/api/v3/materials/{identity}/media/{attachment.get('media_id',attachment['name'])}") if attachment else None)
    result['loading'] = result['loading'] or row['platform'] is not None and store.media.loading(identity)
    manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None
    resources = {r.key: r for r in manifest.resources} if manifest else {}
    result['post_web_unavailable'] = bool(manifest and manifest.post_web_unavailable and manifest.preview_fragment is None)
    result['external_attachment_kinds'] = list(dict.fromkeys(r.external.kind for r in resources.values()
        if r.status == 'external' and r.external))
    return result
