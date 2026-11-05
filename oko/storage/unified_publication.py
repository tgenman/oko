"""Project saved publication, immediate links, media and jobs in one snapshot."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from oko.storage.database import Database, Row
from oko.storage.media_paths import existing_file
from oko.storage import material_read
from oko.storage.reading import retry_time, saved_content


def _json(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value,str) else deepcopy(value)


def _document(row: Row) -> dict[str, Any]:
    content=saved_content(row['content'],mutable=True)
    if not content:
        return {}
    result: dict[str,Any]={'blocks':content.get('blocks',[])}
    telegram=content.get('extra',{}).get('telegram')
    if isinstance(telegram,dict):
        allowed={key:telegram[key] for key in ('members','album_id') if key in telegram}
        if allowed:result['extra']={'telegram':allowed}
    return result


def _timestamp(value: float | None) -> str | None:
    if value is None:return None
    return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z')


def project(db: Database, root: Path, item: str, now: float, wall: float) -> dict[str, Any]:
    inputs=material_read.current(db)
    if inputs is None:
        with material_read.material_inputs(db,{item}):
            return project(db,root,item,now,wall)
    row=inputs.items[item]
    children=inputs.children.get(item,[])
    owners=[row,*children]
    documents={owner['id']:_document(owner) for owner in owners}
    media: list[dict[str,Any]]=[]
    jobs: list[dict[str,Any]]=[]
    for owner in owners:
        identity=owner['id']
        blocks=documents[identity].get('blocks',[])
        roles={block.get('media_id'):block.get('role','attachment') for block in blocks if block.get('type')=='media'}
        circles={block.get('media_id') for block in blocks if block.get('type')=='media' and block.get('presentation')=='circle'}
        for file in inputs.media.get(identity,[]):
            role=roles.get(file['id'],'attachment')
            url=('/api/v3/materials/'+identity+'/media/'+file['id']
                 if file['status']=='ready' and existing_file(root,file['storage_key']) is not None else None)
            mime=file['mime_type'] or ''
            kind={'image':'image','cover':'image','video':'video','audio':'audio','circle':'circle'}.get(role)
            if role=='video' and file['id'] in circles:kind='circle'
            if kind is None:
                kind=next((name for name in ('image','video','audio') if mime.startswith(name+'/')),'file')
            display_role='image' if role=='image' else 'attachment'
            media.append(dict(id=file['id'],name=file['name'],mime=file['mime_type'],size_bytes=file['size_bytes'],
                              source_url=file['source_url'],url=url,status=file['status'],reason_code=file['reader_code'],
                              reason_message=file['reader_reason'],kind=kind,role=display_role))
        for job in [*inputs.jobs.get(identity,{}).values(),*inputs.media_jobs.get(identity,[])]:
            deadline=(job['retry_at'] if job['retry_at'] and job['retry_at']>wall else retry_time(job['next'],now,wall) if job['next'] else None) if job['status']=='pending' else None
            jobs.append(dict(operation=job['kind'],object_id=job['item'],status=job['status'],
                             reason_code=job['reason_code'],reason_message=job['reason_message'],retry_at=_timestamp(deadline)))
    media_ids=json.loads(row['media_ids']) if isinstance(row['media_ids'],str) else row['media_ids']
    filtered=_json(row['filtered_content'])
    original_media={block['media_id']:block for document in documents.values() for block in document.get('blocks',[])
                    if block.get('type')=='media' and 'media_id' in block}
    for block in filtered.get('blocks',[]):
        if block.get('type')=='media' and block.get('media_id') in original_media:
            original=original_media[block['media_id']]
            block.setdefault('source_url',original.get('source_url'))
            block.setdefault('publication_url',original.get('publication_url'))
    return dict(platform=row['platform'],external_id=row['external_id'],status=row['status'],
                discovered_at=_timestamp(row['discovered_at']),media_ids=media_ids,
                content=documents[item],filtered_content=filtered,
                summarized_content=_json(row['summarized_content']),processing_metadata=_json(row['processing_metadata']),
                media=media,linked_publications=[dict(id=child['id'],title=child['title'],source_url=child['url'],
                    content=documents[child['id']],status=child['status']) for child in children],jobs=jobs)
