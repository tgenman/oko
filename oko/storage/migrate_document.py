"""One-time conversion of old cached documents and their local files."""
from copy import deepcopy
import os
import shutil
from pathlib import Path
from typing import Any,TYPE_CHECKING
from uuid import uuid4
from oko.collection.contracts import Material
from oko.storage.publication_document import header as reading_header

if TYPE_CHECKING:
    from oko.storage.store import Store


def prepare(store: 'Store', item: str, material: Material, directory: Path) -> tuple[dict[str,Any],list[dict[str,Any]]]:
    header=reading_header(material)
    text=material.text
    manifest=material.source_manifest
    if manifest and manifest.articles and manifest.preview_fragment is None:
        text='\n\n'.join(part for part in [str(manifest.post.get('text','')),*[article.text for article in manifest.articles if article.text is not None]] if part)
    blocks: list[dict[str, Any]] = [dict(type='text', role='body', text=text)]
    if material.description:
        blocks.append(dict(type='text', role='description', text=material.description))
    if material.reply_preview:
        reply = material.reply_preview
        blocks.append(dict(type='quote', text=reply.text, author=reply.author, source_url=reply.source_url))
    known = {r['resource_key']: dict(r) for r in store.db.execute('SELECT * FROM media_files WHERE publication_id=?', (item,))}
    resources = {r.name: r for r in material.source_manifest.resources} if material.source_manifest else {}
    references = {r.key: r for r in material.adapter_content.media} if material.adapter_content else {}
    media: list[dict[str, Any]] = []
    names = list(dict.fromkeys([*resources, *[p.name for p in sorted(directory.iterdir()) if p.name.startswith(('media-', 'image-'))]]))
    attachments = {a.name: a for a in material.attachments}
    for name in names:
        resource = resources.get(name)
        if resource and resource.external:
            blocks.append(dict(type='external_media', kind=resource.external.kind, url=resource.external.source_url))
            continue
        key = resource.key if resource else name
        path = directory/name
        old = known.get(key)
        ref = references.get(key)
        same = bool(old and path.is_file() and old['storage_key'] and (store.root/old['storage_key']).is_file()
                    and store.versions.file_digest(path) == store.versions.file_digest(store.root/old['storage_key']))
        if ref and ref.resource_identity and old and old['resource_identity'] == ref.resource_identity:
            same = True
        identity = old['id'] if same and old else str(uuid4())
        storage_key = 'media/'+item+'/'+identity if path.is_file() else None
        if same and old and old['status']=='ready' and old['storage_key'] and (store.root/old['storage_key']).is_file():
            storage_key = old['storage_key']
            if not path.is_file():
                os.link(store.root/storage_key, path)
        if storage_key and not same:
            destination = store.root/storage_key
            destination.parent.mkdir(parents=True, exist_ok=True)
            pending = destination.with_suffix('.tmp')
            with path.open('rb') as source, pending.open('wb') as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, destination)
            store.versions._fsync(destination.parent)
            store.versions._fsync(destination.parent.parent)
        attachment = attachments.get(name)
        kind = resource.kind if resource else attachment.kind if attachment else 'image'
        media.append(dict(id=identity, publication_id=item, resource_key=key,
            source_locator=ref.locator if ref else old['source_locator'] if same and old else resource.source_url or '' if resource else '', name=ref.name if ref else old['name'] if old else name,
            mime_type=ref.mime_type if ref else resource.mime if resource else attachment.mime if attachment else None,
            size_bytes=path.stat().st_size if path.is_file() else ref.size_bytes if ref else None, storage_key=storage_key,
            status='ready' if storage_key else 'pending_download' if ref else 'unavailable',
            reason=old['reason'] if same and old and not storage_key else resource.reason or '' if resource else '', resource_identity=ref.resource_identity if ref else old['resource_identity'] if same and old else None))
        blocks.append(dict(type='media', role='attachment' if kind=='file' else kind, media_id=identity))
    extra = deepcopy(material.adapter_content.content.get('extra', {})) if material.adapter_content else {}
    extra['_links'] = [dict(platform=ref.key.platform.value,external_id=ref.key.external_id,url=ref.url) for ref in material.adapter_content.linked_publications] if material.adapter_content else []
    extra['_reader'] = header
    extra['_collection_reason'] = '' if header.get('reason', '').startswith('Вложения ещё не загружены') else header.get('reason', '')
    content = dict(blocks=blocks, extra=extra)
    store.media.project_header(content, media)
    return content, media

