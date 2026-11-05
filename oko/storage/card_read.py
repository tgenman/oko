"""Card projection from common Publication, MediaFile and Job facts."""
from typing import Any, TYPE_CHECKING

from oko.storage import material_read, publication_links
from oko.storage.media_paths import existing_file
from oko.storage.reading import reader_now, saved_content
from oko.diagnostics import record_caught

if TYPE_CHECKING:
    from oko.storage.store import Store


def _preview(store: 'Store', identity: str) -> str | None:
    owners=[material_read.item(store.db,identity),*publication_links.children(store,identity)]
    for owner in owners:
        if owner is None:continue
        files={item['id']:item for item in store.media.publication_rows(owner['id'])}
        blocks=saved_content(owner['content']).get('blocks',[])
        for block in blocks:
            if block.get('type')=='media':
                file=files.get(block.get('media_id'))
                image=block.get('role') in ('image','cover') or bool(file and (file['mime_type'] or '').startswith('image/'))
            elif block.get('type')=='external_media':
                file=files.get(block.get('poster_media_id'))
                image=True
            else:
                continue
            if image and file and file['status']=='ready' and existing_file(store.root,file['storage_key']) is not None:
                return store.media.url(owner['id'],file['id'])
    return None


def card(store: 'Store', identity: str) -> dict[str, Any] | None:
    row=material_read.item(store.db,identity)
    if row is None:return None
    hidden=bool(row['reader_hidden']) if 'reader_hidden' in row.keys() else row['status']=='filter_out'
    result: dict[str,Any]=dict(id=identity,feed_id=row['feed_id'],title='Материал загружается',
        published_at=row['published_at'],source_url=row['url'],loading=True,preview_url=None,hidden=hidden,
        external_attachment_kinds=[])
    if 'blocks' not in row['content']:return result
    try:
        content=saved_content(row['content'])
        title=row['title']
        if not isinstance(title,str):raise ValueError('Invalid saved publication title')
        jobs=material_read.jobs(store.db,identity)
        fetch=jobs.get('fetch')
        result.update(title=title,
            loading=bool(fetch and (fetch['status']=='running' or fetch['status']=='pending' and fetch['next']<=reader_now(store.clock()))) or store.media.loading(identity),
            preview_url=_preview(store,identity),
            external_attachment_kinds=list(dict.fromkeys(block['kind'] for block in content['blocks']
                if block.get('type')=='external_media' and block.get('kind') in ('audio','video','image','document','unsupported'))))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        record_caught('store.card')
        result.update(title='Публикация недоступна',loading=False,preview_url=None,
            external_attachment_kinds=[])
    return result
