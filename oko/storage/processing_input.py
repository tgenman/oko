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
    linked=[row for row in publication_links.children(store,item) if row['complete']]
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
    row=store.db.execute('SELECT filtered_content FROM items_read WHERE id = %s',(item,)).fetchone()
    if kind=='theses' and row and row[0]!='{}' and 'youtube' not in selected.get('extra',{}) and store.filters.read(item)['status']=='done':
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
        record=expect_row(store.db.execute('SELECT title , url , published FROM items_read WHERE id = %s',(identity,)).fetchone())
        text='\n\n'.join(part['text'] for part in parts if part['type']=='text')
        quotes=[part for part in parts if part['type']=='quote']
        quote=ReplyPreview(text='\n\n'.join(part['text'] for part in quotes),
            author=quotes[0].get('author'),source_url=quotes[0].get('source_url')) if quotes else None
        result.append(InputSource(id=identity,version=digest(parts),url=record['url'],title=record['title'],
                                  text=text,published=record['published'] or 0.,reply_preview=quote))
    return tuple(result)


def awaiting_input(content: dict[str, Any]) -> bool:
    youtube=content.get('extra',{}).get('youtube')
    if youtube and youtube.get('captions') not in ('available','unavailable','not_requested'):
        return True
    manifest=content.get('extra',{}).get('_reader',{}).get('source_manifest',{})
    return manifest.get('preview_fragment') is not None or bool(manifest.get('post_web_unavailable') and not any(block.get('text','').strip() for block in blocks(content)))


def awaiting_linked_input(store: 'Store', item: str, selected: dict[str,Any]) -> bool:
    if any(block.get('text','').strip() for block in blocks(selected)):
        return False
    return any(not row['complete'] for row in publication_links.children(store,item))


@reading_value
def processing_feed(store: 'Store', item: str, kind: str | None = None) -> str:
    row = material_read.item(store.db, item)
    if row is None:
        raise ValueError('Publication not found')
    chosen = json.loads(row['processing_metadata']).get('_pass', {}).get('feed_id')
    if isinstance(chosen, str):
        belongs = chosen == row['feed_id'] or bool(store.db.execute(
            'SELECT 1 FROM feed_publications WHERE feed_id=%s AND publication_id=%s', (chosen,item)).fetchone())
        if belongs:
            return chosen
    if kind in ('filter','theses'):
        owners = store.db.execute('''SELECT feed_id FROM feed_publications WHERE publication_id=%s
            ORDER BY is_primary DESC,feed_id''', (item,)).fetchall()
        if len(owners)>1:
            candidates = []
            for owner in owners:
                feed = owner[0]
                values = store.subscriptions.effective('feed',feed,details=False)['effective']
                filtering = bool(values['filter_enabled']['value'] and values['prompt_filters']['value'].strip())
                if kind=='filter' and filtering or kind=='theses' and values['theses']['value'] and not filtering:
                    candidates.append(str(feed))
            for feed in candidates:
                if can_start(store,item,feed):
                    return feed
            if candidates:
                return candidates[0]
    return store.item_feed(item)


def can_start(store: 'Store', item: str, feed: str) -> bool:
    rules = live_settings(store,item,feed)
    processor = rules['values']['processor']
    if processor == 'codex':
        return bool(rules['public_ai'] and store.codex_config())
    config = store.processor_config()
    return bool(config and processor in ('llamacpp','llamacpp:'+config.model)
                and (config.local_execution or rules['public_ai']))


@reading_value
def live_settings(store: 'Store', item: str, feed: str | None = None) -> dict[str, Any]:
    feed=feed or processing_feed(store,item)
    current=store.subscriptions.effective('feed',feed,details=False)
    allowed=current['feed']['public_ai']
    inputs = material_read.current(store.db)
    owners = inputs.owners[item] if inputs is not None and item in inputs.owners else {row[0] for row in store.db.execute('SELECT DISTINCT m . feed_id FROM reading_memberships m WHERE m . publication_id = %s OR m . publication_id IN ( SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s )',(item,item))}
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
    row=material_read.item(store.db,item)
    selected=json.loads(row['processing_metadata']).get('_pass',{}).get('settings') if row else None
    return selected if selected is not None else live_settings(store,item)


def signature(store: 'Store', item: str, kind: str) -> str | None:
    row=material_read.item(store.db,item)
    if not row or row['viewed'] or row['feed_id'] is None or not row['complete'] or row['content']=='{}':
        return None
    feed=processing_feed(store,item,kind)
    current=store.subscriptions.effective('feed',feed,details=False)
    values={key:value['value'] for key,value in current['effective'].items()}
    youtube=row['platform']=='youtube'
    if kind=='filter' and not values['prompt_filters'].strip():
        return None
    rules=live_settings(store,item,feed)
    selected_content=saved_content(row['content']) if youtube else _reading_content(store,item)
    if youtube and selected_content.get('extra',{}).get('youtube',{}).get('captions')=='available' and not youtube_media.subtitle_path(store,youtube_media.subtitle_id(selected_content)):
        return None
    if awaiting_input(selected_content) or awaiting_linked_input(store,item,selected_content):
        return None
    selected=([dict(captions=selected_content.get('extra',{}).get('youtube',{}).get('captions'),
                    digest=selected_content.get('extra',{}).get('youtube',{}).get('captions_digest'))]
              if youtube else blocks(selected_content))
    if kind=='theses':
        metadata=json.loads(row['processing_metadata'])
        unselected=not metadata.get('_pass',{}).get('feed_id')
        first_without_filter=unselected and feed!=row['feed_id'] and not (values['filter_enabled'] and values['prompt_filters'].strip())
        filtered=({'status':'off','hidden':False} if first_without_filter else store.filters.read(item))
        if filtered['status'] not in ('off','done') or filtered['hidden']:
            return None
        if filtered['status']=='done' and row['platform']!='youtube':
            selected=blocks(json.loads(row['filtered_content']))
    config=store.processor_config() if values['processor'].startswith('llamacpp') else None
    processor=dict(endpoint=config.endpoint,local_execution=config.local_execution) if config else None
    relevant=dict(prompt=values['prompt_filters' if kind=='filter' else 'prompt_theses'],processor=values['processor'],public_ai=rules['public_ai'],local=processor)
    return digest([None if youtube else row['title'],selected,relevant])


def snapshot(store: 'Store', item: str) -> None:
    row=expect_row(store.db.execute('SELECT processing_metadata FROM items_read WHERE id = %s',(item,)).fetchone())
    metadata=json.loads(row[0])
    rules=live_settings(store,item)
    current_revision=revision(store,item)
    if metadata.get('_pass',{}).get('settings')!=rules or metadata.get('_pass',{}).get('revision')!=current_revision:
        metadata['_pass']=dict(feed_id=rules['feed_id'],settings=deepcopy(rules),settings_version=store.settings_version(rules['feed_id']),revision=current_revision)
        with store.db:
            store.db.execute('UPDATE items SET processing_metadata = %s WHERE id = %s',(json.dumps(metadata,ensure_ascii=False),item))


def request_settings(store: 'Store', item: str) -> tuple[dict[str, Any], bool, int]:
    chosen=settings(store,item)
    record=material_read.item(store.db,item)
    assert record is not None
    metadata=json.loads(record['processing_metadata'])
    version=metadata.get('_pass',{}).get('settings_version',store.settings_version(chosen['feed_id']))
    return {key:dict(value=value) for key,value in chosen['values'].items()},chosen['public_ai'] and live_settings(store,item)['public_ai'],version


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


def input_hash(store: 'Store', item: str) -> str:
    selected=_reading_content(store,item)
    youtube=selected.get('extra',{}).get('youtube',{})
    row=material_read.item(store.db,item)
    rules = deepcopy(live_settings(store, item))
    prefix = [row['title'] if row and not youtube else None,
              [youtube.get('captions'),youtube.get('captions_digest')] if youtube else blocks(selected)]
    current = digest([*prefix, rules])
    # Old revisions included the theses switch. Preserve their revision when
    # that switch alone changes, without accepting changes to any other input.
    metadata = json.loads(row['processing_metadata']) if row else {}
    previous = metadata.get('_revision_input')
    if isinstance(rules['values'].get('theses'), bool):
        rules['values']['theses'] = not rules['values']['theses']
        if previous == digest([*prefix, rules]):
            return str(previous)
    return current


def revision(store: 'Store', item: str) -> str:
    row=material_read.item(store.db,item)
    metadata=json.loads(row['processing_metadata']) if row else {}
    current=input_hash(store,item)
    if metadata.get('_revision_input')!=current:
        return digest(['unreconciled',metadata.get('_revision'),current])
    return str(metadata.get('_revision',''))
