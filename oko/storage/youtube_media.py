"""Read YouTube's original subtitle file as transient processing/reader text."""
from typing import Any,TYPE_CHECKING
from pathlib import Path

from oko.storage.reading import saved_bytes,reading_value

if TYPE_CHECKING:
    from oko.storage.store import Store


def subtitle_id(content: dict[str,Any]) -> str | None:
    return next((block['media_id'] for block in content.get('blocks',[])
                 if block['type']=='media' and block.get('role')=='subtitles'),None)


@reading_value
def subtitle_path(store: 'Store', identity: str | None) -> Path | None:
    row=store.db.execute("SELECT storage_key FROM media_files WHERE id=? AND status='ready'",(identity,)).fetchone()
    path=store.root/row[0] if row and row[0] else None
    return path if path and path.is_file() else None


def add_input(store: 'Store', content: dict[str,Any]) -> dict[str,Any]:
    if 'youtube' not in content.get('extra',{}):
        return content
    identity=subtitle_id(content)
    if identity is None:
        return content
    path=subtitle_path(store,identity)
    if path:
        try:
            text=saved_bytes(path).decode('utf-8')
        except (OSError,UnicodeError):
            content['extra']['youtube']['captions']='error'
            return content
        index=next(i for i,block in enumerate(content['blocks']) if block.get('media_id')==identity)
        content['blocks'].append(dict(type='text',role='body',text=text,source_media_id=identity,source_block_index=index))
    elif content['extra']['youtube'].get('captions')=='available':
        content['extra']['youtube']['captions']='error'
    return content
