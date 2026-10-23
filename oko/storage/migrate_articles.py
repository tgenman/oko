"""Split cached inline articles without contacting their providers."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
from typing import Any,TYPE_CHECKING

from oko.collection.adapters import MediaReference,PublicationContent,PublicationMetadata
from oko.collection.contracts import Material
from oko.collection.web_adapter import reference
from oko.storage import publication_links
from oko.storage.reading import saved_document

if TYPE_CHECKING:
    from oko.storage.store import Store


def split(store: 'Store', item: str, material: Material, directory: Path, report: dict[str,Any]) -> Material:
    manifest=material.source_manifest
    if not manifest or not manifest.articles:
        return material
    refs=tuple(reference(article.url) for article in manifest.articles)
    with store.db:
        publication_links.sync(store,item,refs)
    resources={resource.key:resource for resource in manifest.resources}
    article_keys={key for article in manifest.articles for key in article.resource_keys}
    for article,ref in zip(manifest.articles,refs):
        if article.text is None:
            continue
        target=store.db.execute('SELECT * FROM items WHERE platform=? AND external_id=?',
                               (ref.key.platform.value,ref.key.external_id)).fetchone()
        assert target is not None
        previous=json.loads(target['content'])
        old_text='\n\n'.join(block['text'] for block in previous.get('blocks',[]) if block['type']=='text' and block.get('role')=='body')
        if target['complete'] and (old_text!=article.text or target['title']!=(article.title or '')):
            raise ValueError('Conflicting cached article snapshots: '+ref.url)
        selected=[resources[key] for key in article.resource_keys if key in resources]
        media=tuple(MediaReference(resource.key,resource.source_url or '',resource.name,resource.mime) for resource in selected)
        blocks: list[dict[str,Any]]=[dict(type='text',role='body',text=article.text)]
        blocks.extend(dict(type='media',role='attachment' if resource.kind in ('file','unsupported') else resource.kind,
                           ref={'$media':resource.key}) for resource in selected)
        value=PublicationContent(PublicationMetadata(ref,article.title or '',None),dict(blocks=blocks,extra={}),media)
        cached=Material(article.title or '',article.text,False,adapter_content=value)
        version=store.versions.prepare(target['id'],cached)
        prepared=store.publications.prepare(target['id'],cached,store.versions.path(target['id'],version[0]).parent)
        known={row['resource_key']:dict(row) for row in store.db.execute('SELECT * FROM media_files WHERE publication_id=?',(target['id'],))}
        for row,resource in zip(prepared[1],selected):
            source=directory/resource.name
            old=known.get(resource.key)
            valid=source.is_file() and (not resource.sha256 or store.versions.file_digest(source)==resource.sha256)
            if valid:
                if old and old['storage_key'] and (store.root/old['storage_key']).is_file():
                    if store.versions.file_digest(source)!=store.versions.file_digest(store.root/old['storage_key']):
                        raise ValueError('Conflicting cached article media: '+ref.url)
                    new=row['id']
                    row.update(old)
                    for block in prepared[0]['blocks']:
                        if block.get('media_id')==new:
                            block['media_id']=row['id']
                else:
                    key='media/'+target['id']+'/'+row['id']
                    path=store.root/key
                    path.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source,path)
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
                    row.update(storage_key=key,size_bytes=path.stat().st_size,status='ready',reason='')
            else:
                row.update(status='unavailable',reason=resource.reason or 'Обновите описание файла статьи')
        store.media.project_header(prepared[0],prepared[1])
        with store.db:
            store.versions.publish(target['id'],version,True)
            store.publications.publish(target['id'],prepared,version[1])
            store.db.execute("UPDATE items SET complete=1,status='ready' WHERE id=?",(target['id'],))
            store.db.execute("UPDATE jobs SET status='done',generation=generation+1,error=NULL WHERE kind='fetch' AND item=?",(target['id'],))
        report.setdefault('linked_publications',[]).append(dict(source_publication_id=item,publication_id=target['id'],external_id=ref.key.external_id))
    own_resources=tuple(resource for resource in manifest.resources if resource.key not in article_keys)
    own_names={resource.name for resource in own_resources}
    for key in article_keys:
        cached_resource=resources.get(key)
        if cached_resource and cached_resource.name not in own_names:
            (directory/cached_resource.name).unlink(missing_ok=True)
    article_reasons={part for article in manifest.articles for part in article.reason.split('; ') if part}
    own_reasons=[part for part in material.reason.split('; ') if part and part not in article_reasons]
    own_reasons.extend(resource.reason for resource in own_resources if resource.reason)
    reason='; '.join(dict.fromkeys(own_reasons))
    manifest=manifest.model_copy(update={'articles':(),'resources':own_resources})
    text=str(manifest.post.get('text',material.text))
    updated=replace(material,text=text,source_manifest=manifest,attachments=tuple(a for a in material.attachments if a.name in own_names),
                    reason=reason,full=not reason and not manifest.post_web_unavailable)
    if updated.adapter_content:
        updated=replace(updated,adapter_content=replace(updated.adapter_content,linked_publications=refs))
    header,_=saved_document(directory/'original.md')
    header.update(source_manifest=manifest.model_dump(mode='json'),reason=reason,full=updated.full)
    store.versions._write(directory/'original.md',(json.dumps(header,ensure_ascii=False)+'\n\n'+text).encode())
    return updated
