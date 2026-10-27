"""Card projection from borrowed saved facts, without constructing an opened material.

Only the card owns these fields. Text/media expansion and mutable headers belong
to the opened-material path; borrowing here never mutates saved content.
"""
import json
from typing import Any, TYPE_CHECKING
from oko.storage import material_read, publication_links
from oko.storage.reading import saved_content, reader_now
from oko.storage.composition import telegram_title
from oko.collection.contracts import SourceManifest
from oko.diagnostics import record_caught

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
            json.loads(child['media_ids'])
            linked = saved_content(child['content']).get('extra', {}).get('_reader', {})
            header['changed'] = bool(header.get('changed') or linked.get('changed'))
            header['attachments'].extend(linked.get('attachments', []))
            header['_media_urls'].update(linked.get('_media_urls', {}))
    settings = store.subscriptions.effective('feed', row['feed_id'], details=False)
    needs_title = (settings['feed']['kind'] == 'telegram' and settings['feed'].get('transport') != 'rss'
                   and 'reply_preview' not in own.get('source_manifest', {}).get('post', {}))
    if not needs_title:
        return header, ''
    text = '\n\n'.join(b['text'] for b in content.get('blocks', []) if b['type'] == 'text' and b.get('role') == 'body')
    linked_text = publication_links.text(store, identity)
    return header, '\n\n'.join(part for part in (text, linked_text) if part)


def card(store: 'Store', identity: str) -> dict[str, Any] | None:
    row = material_read.item(store.db, identity)
    if row is None or row['viewed']:
        return None
    if 'reader_hidden' in row.keys():
        hidden=bool(row['reader_hidden'])
    else:
        visibility=store.db.execute('SELECT hidden FROM reader_visibility WHERE item = %s',(identity,)).fetchone()
        hidden=bool(visibility and visibility[0])
    result: dict[str, Any] = dict(id=identity,feed_id=row['feed_id'],title='Материал загружается',
        published_at=row['published'] if row['published_known'] else None,
        source_url=row['url'],loading=True,changed=False,preview_url=None,hidden=hidden,
        post_web_unavailable=False,external_attachment_kinds=[])

    if not row['complete']:
        return result
    try:
        media_ids=json.loads(row['media_ids'])
        metadata=json.loads(row['processing_metadata'])
        if not isinstance(media_ids,list) or not isinstance(metadata,dict):
            raise ValueError('Invalid saved publication metadata')
        header,text=_preview_source(store, identity)
        for saved in header.get('attachments',[]):
            saved['name'],saved['kind'],saved['mime']
        settings=store.subscriptions.effective('feed',row['feed_id'], details=False)
        title=header['title']
        if not isinstance(title,str):raise ValueError('Invalid saved publication title')
        if settings['feed']['kind']=='telegram' and settings['feed'].get('transport')!='rss':
            title=telegram_title(header,text)
        attachment=next((a for a in header.get('attachments',[]) if a['kind']=='image'),None)
        jobs = material_read.jobs(store.db, identity)
        fetch = jobs.get('fetch')
        result.update(title=title,
            changed=bool(header.get('changed')),
            loading=bool(fetch and (fetch['status']=='running' or fetch['status']=='pending' and fetch['next']<=reader_now(store.clock()))),
            preview_url=header.get('_media_urls',{}).get(attachment['name'],f"/api/v3/materials/{identity}/media/{attachment.get('media_id',attachment['name'])}") if attachment else None)
        result['loading'] = result['loading'] or row['platform'] is not None and store.media.loading(identity)
        manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None
        resources = {r.key: r for r in manifest.resources} if manifest else {}
        result['post_web_unavailable'] = bool(manifest and manifest.post_web_unavailable and manifest.preview_fragment is None)
        result['external_attachment_kinds'] = list(dict.fromkeys(r.external.kind for r in resources.values()
            if r.status == 'external' and r.external))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        record_caught('store.card')
        result.update(title='Публикация недоступна',loading=False,changed=False,preview_url=None,
            post_web_unavailable=False,external_attachment_kinds=[])

    return result
