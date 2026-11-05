"""Relevant publication inputs and feed rules, independent of downloaded bytes."""
from oko.storage.database import expect_row
from copy import deepcopy
import hashlib
import json
from typing import Any, TYPE_CHECKING
from oko.storage import material_read, youtube_media, publication_links
from oko.storage.reading import reading_value, saved_content

if TYPE_CHECKING:
    from oko.storage.store import Store
    from oko.processing.contracts import InputSource


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def blocks(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [block for block in content.get('blocks',[]) if block['type']=='quote' or block['type']=='text' and block['role']=='body']


def content(store: 'Store', item: str) -> dict[str, Any]:
    row=material_read.item(store.db,item)
    own=youtube_media.add_input(store,saved_content(row['content'],mutable=True)) if row else {}
    linked=[row for row in publication_links.children(store,item) if 'blocks' in row['content'] and row['status']!='pending_collection']
    if not linked:
        return own
    for block in own.get('blocks',[]):
        block.setdefault('source_publication_id',item)
    for row in linked:
        for index,block in enumerate(saved_content(row['content'],mutable=True).get('blocks',[])):
            block.update(source_publication_id=row['id'],source_block_index=index)
            own.setdefault('blocks',[]).append(block)
    return own


@reading_value
def _reading_content(store: 'Store', item: str) -> dict[str, Any]:
    """Borrow an assembled input only within the synchronous read snapshot."""
    return content(store, item)


def selected_content(store: 'Store', item: str, kind: str) -> dict[str, Any]:
    selected=content(store,item)
    row=store.db.execute('SELECT filtered_content FROM publications WHERE id = %s',(item,)).fetchone()
    if kind=='theses' and row and row[0]!='{}' and store.filters.read(item)['status']=='done':
        selected=json.loads(row[0])
    return selected


def sources(store: 'Store', item: str, kind: str) -> tuple['InputSource', ...]:
    from oko.processing.contracts import InputSource
    from oko.collection.contracts import ReplyPreview
    selected=selected_content(store,item,kind)
    groups: dict[str,list[dict[str,Any]]]={item:[]}
    for block in blocks(selected):
        groups.setdefault(block.get('source_publication_id',item),[]).append(block)
    result=[]
    for identity,parts in groups.items():
        record=expect_row(store.db.execute('SELECT title , url , published_at FROM publications WHERE id = %s',(identity,)).fetchone())
        text='\n\n'.join(part['text'] for part in parts if part['type']=='text')
        quotes=[part for part in parts if part['type']=='quote']
        quote=ReplyPreview(text='\n\n'.join(part['text'] for part in quotes),
            author=quotes[0].get('author'),source_url=quotes[0].get('source_url')) if quotes else None
        result.append(InputSource(id=identity,version=digest(parts),url=record['url'],title=record['title'],
                                  text=text,published=record['published_at'] or 0.,reply_preview=quote))
    return tuple(result)


def awaiting_input(store: 'Store', item: str, content: dict[str, Any]) -> bool:
    if 'youtube' not in content.get('extra',{}):
        return False
    state=youtube_media.subtitle_state(store,content,item)
    if state=='not_requested':
        row=material_read.item(store.db,item)
        return bool(row and row['feed_id'] and store.subscriptions.youtube_theses(row['feed_id']))
    return state not in ('available','unavailable')


def awaiting_linked_input(store: 'Store', item: str, selected: dict[str,Any]) -> bool:
    return any('blocks' not in row['content'] or row['status']=='pending_collection' for row in publication_links.children(store,item))


@reading_value
def processing_feed(store: 'Store', item: str, kind: str | None = None) -> str:
    row = material_read.item(store.db, item)
    if row is None:
        raise ValueError('Publication not found')
    return str(row['feed_id']) if row['feed_id'] is not None else store.item_feed(item)


def can_start(store: 'Store', item: str, feed: str) -> bool:
    rules = live_settings(store,item,feed)
    processor = rules['values']['processor']
    if processor == 'codex':
        return bool(rules['public_ai'] and store.codex_config())
    from oko.processing.config import loopback
    config = store.processor_config()
    return bool(config and processor in ('llamacpp','llamacpp:'+config.model)
                and (config.local_execution and loopback(config.endpoint) or rules['public_ai']))


@reading_value
def live_settings(store: 'Store', item: str, feed: str | None = None) -> dict[str, Any]:
    feed=feed or processing_feed(store,item)
    current=store.subscriptions.effective('feed',feed,details=False)
    allowed=current['feed']['public_ai']
    inputs = material_read.current(store.db)
    owners = inputs.owners[item] if inputs is not None and item in inputs.owners else {row[0] for row in store.db.execute('SELECT DISTINCT m . feed_id FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) m WHERE m.id = %s OR m.id IN ( SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s )',(item,item))}
    for owner in owners:
        allowed=allowed and store.subscriptions.effective('feed',owner,details=False)['feed']['public_ai']
    rules=dict(feed_id=feed,public_ai=allowed,importance=current['feed']['importance'],values={key:value['value'] for key,value in current['effective'].items() if key in ('prompt_filters','theses','prompt_theses','processor')})
    return rules


@reading_value
def stage_enabled(store: 'Store', item: str, kind: str) -> bool:
    row = material_read.item(store.db, item)
    if row is None or row['feed_id'] is None:
        return False
    values = store.subscriptions.effective('feed', processing_feed(store,item,kind), details=False)['effective']
    if kind == 'filter':
        return bool(values['filter_enabled']['value'] and values['prompt_filters']['value'].strip())
    if kind == 'theses':
        return bool(values['theses']['value'])
    raise ValueError('Unknown processing stage')


def settings(store: 'Store', item: str) -> dict[str, Any]:
    return live_settings(store,item)


def input_hash(store: 'Store', item: str, kind: str) -> str | None:
    row=material_read.item(store.db,item)
    if not row or row['feed_id'] is None or row['status']=='pending_collection' or 'blocks' not in row['content']:
        return None
    feed=processing_feed(store,item,kind)
    current=store.subscriptions.effective('feed',feed,details=False)
    values={key:value['value'] for key,value in current['effective'].items()}
    youtube=row['platform']=='youtube'
    if kind=='filter' and not values['prompt_filters'].strip():
        return None
    rules=live_settings(store,item,feed)
    selected_content=youtube_media.add_input(store,saved_content(row['content'],mutable=True)) if youtube else _reading_content(store,item)
    if youtube and youtube_media.subtitle_state(store,selected_content,item)=='available' and not youtube_media.subtitle_path(store,youtube_media.subtitle_id(selected_content)):
        return None
    if awaiting_input(store,item,selected_content) or awaiting_linked_input(store,item,selected_content):
        return None
    if kind=='theses':
        filtered=store.filters.read(item)
        if filtered['status'] not in ('off','done') or filtered['hidden']:
            return None
    return stage_input_hash(store,item,kind)


def stage_input_hash(store: 'Store', item: str, kind: str) -> str:
    """SHA-256 of canonical UTF-8 JSON: ordered sources with their model-visible facts."""
    return digest([source.model_dump(mode='json') for source in sources(store,item,kind)])


def request_settings(store: 'Store', item: str) -> tuple[dict[str, Any], bool]:
    chosen=settings(store,item)
    return {key:dict(value=value) for key,value in chosen['values'].items()},chosen['public_ai'] and live_settings(store,item)['public_ai']


def filtered(content: dict[str, Any], item: str, result: dict[str, Any]) -> dict[str, Any]:
    from oko.processing.contracts import ProcessingError
    output=deepcopy(content)
    for block in output['blocks']:
        block.setdefault('source_publication_id',item)
    removed=[]
    for fragment in result['removed']:
        for index,block in enumerate(output['blocks']):
            if block['type']=='text' and block['role']=='body' and fragment in block['text']:
                block['text']=block['text'].replace(fragment,'')
                removed.append(dict(publication_id=block['source_publication_id'],block_index=block.get('source_block_index',index),text=fragment))
                break
        else:
            raise ProcessingError('Фрагмент фильтра отсутствует в собственном блоке публикации')
    output.update(reason=result['reason'],removed=removed,hidden=result['hidden'])
    if result['hidden']:
        output['blocks']=[]
    return output


def source_revision_hash(store: 'Store', item: str) -> str:
    """Fingerprint the source facts, independently of processing settings."""
    root=material_read.item(store.db,item)
    if root is None:
        return digest(None)
    selected=_reading_content(store,item)
    sources=[root,*publication_links.children(store,item)]
    identities=[dict(id=row['id'],url=row['url'],title=row['title'],published_at=row['published_at']) for row in sources]
    return digest(dict(sources=identities,blocks=blocks(selected)))


def revision(store: 'Store', item: str) -> str:
    row=material_read.item(store.db,item)
    metadata=json.loads(row['processing_metadata']) if row else {}
    current=source_revision_hash(store,item)
    if metadata.get('_revision_input')!=current:
        return digest(['unreconciled',metadata.get('_revision'),current])
    return str(metadata.get('_revision',''))
