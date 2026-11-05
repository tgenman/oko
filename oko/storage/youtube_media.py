"""Read YouTube's original subtitle file as transient processing/reader text."""
from typing import Any,TYPE_CHECKING
from pathlib import Path

from oko.storage.reading import saved_bytes,reading_value
from oko.storage.media_paths import existing_file

if TYPE_CHECKING:
    from oko.storage.store import Store


def subtitle_id(content: dict[str,Any]) -> str | None:
    return next((block['media_id'] for block in content.get('blocks',[])
                 if block['type']=='media' and block.get('role')=='subtitles'),None)


@reading_value
def subtitle_path(store: 'Store', identity: str | None) -> Path | None:
    from oko.storage.material_read import current
    inputs = current(store.db)
    if inputs is not None and identity is not None and identity in inputs.subtitles:
        key = inputs.subtitles[identity]
        owner = inputs.subtitle_owners.get(identity)
    else:
        row=store.db.execute("SELECT storage_key,publication_id FROM media_files WHERE id=%s AND status='ready'",(identity,)).fetchone()
        key = row[0] if row else None
        owner = row[1] if row else None
    path=existing_file(store.root,key)
    if path:
        return path
    if owner:
        store.processing.repair(owner)
    return None


def subtitle_state(store: 'Store', content: dict[str,Any], item: str) -> str:
    identity=subtitle_id(content)
    if identity is None:
        return 'not_requested'
    media=next((value for value in store.media.publication_rows(item) if value['id']==identity),None)
    if media is None:
        return 'not_requested'
    if media['status']=='ready':
        return 'available' if existing_file(store.root,media.get('storage_key')) is not None else 'error'
    if media.get('reader_job_id') is None or media.get('reader_job_status')=='cancelled':
        return 'not_requested'
    if media.get('reader_code')=='subtitles_absent' and media.get('reader_job_status')=='done':
        return 'unavailable'
    if media.get('reader_job_status') in ('running','pending','blocked'):
        return 'running' if media['reader_job_status']=='running' else 'pending'
    return 'error'


def add_input(store: 'Store', content: dict[str,Any]) -> dict[str,Any]:
    if 'youtube' not in content.get('extra',{}):
        return content
    identity=subtitle_id(content)
    if identity is None:
        return content
    from oko.storage.material_read import current
    inputs=current(store.db)
    owner=inputs.media_owners.get(identity) if inputs is not None else None
    if owner is None:
        row=store.db.execute('SELECT publication_id FROM media_files WHERE id=%s',(identity,)).fetchone()
        owner=row[0] if row else None
    state=subtitle_state(store,content,owner) if owner else 'not_requested'
    path=subtitle_path(store,identity) if state in ('available','error') else None
    if path:
        try:
            text=saved_bytes(path).decode('utf-8')
        except (OSError,UnicodeError):
            return content
        index=next(i for i,block in enumerate(content['blocks']) if block.get('media_id')==identity)
        content['blocks'].append(dict(type='text',role='body',text=text,source_media_id=identity,source_block_index=index))
    return content
