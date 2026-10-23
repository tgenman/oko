"""Read YouTube's original subtitle file as transient processing/reader text."""
from typing import Any,TYPE_CHECKING

from oko.storage.reading import saved_bytes

if TYPE_CHECKING:
    from oko.storage.store import Store


def subtitle_id(content: dict[str,Any]) -> str | None:
    return next((block['media_id'] for block in content.get('blocks',[])
                 if block['type']=='media' and block.get('role')=='subtitles'),None)


def add_input(store: 'Store', content: dict[str,Any]) -> dict[str,Any]:
    if 'youtube' not in content.get('extra',{}):
        return content
    identity=subtitle_id(content)
    if identity is None:
        return content
    media=store.db.execute('SELECT * FROM media_files WHERE id=?',(identity,)).fetchone()
    if media and media['status']=='ready' and media['storage_key']:
        try:
            text=saved_bytes(store.root/media['storage_key']).decode('utf-8')
        except (OSError,UnicodeError):
            content['extra']['youtube']['captions']='error'
            return content
        index=next(i for i,block in enumerate(content['blocks']) if block.get('media_id')==identity)
        content['blocks'].append(dict(type='text',role='body',text=text,source_media_id=identity,source_block_index=index))
    return content
