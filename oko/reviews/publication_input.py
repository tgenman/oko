"""Relevant publication inputs and feed rules, independent of downloaded bytes."""
from copy import deepcopy
import hashlib
import json
from typing import Any, TYPE_CHECKING
from oko.storage import material_read

if TYPE_CHECKING:
    from oko.storage.store import Store


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def blocks(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [block for block in content.get('blocks',[]) if block['type']=='quote' or block['type']=='text' and block['role']=='body']


def awaiting_input(content: dict[str, Any]) -> bool:
    manifest=content.get('extra',{}).get('_reader',{}).get('source_manifest',{})
    return manifest.get('preview_fragment') is not None or bool(manifest.get('post_web_unavailable') and not any(block.get('text','').strip() for block in blocks(content)))


def live_settings(store: 'Store', item: str) -> dict[str, Any]:
    feed=store.item_feed(item)
    current=store.subscriptions.effective('feed',feed,details=False)
    rules=dict(feed_id=feed,public_ai=current['feed']['public_ai'],values={key:value['value'] for key,value in current['effective'].items() if key in ('prompt_filters','theses','prompt_theses','processor')})
    return rules


def settings(store: 'Store', item: str) -> dict[str, Any]:
    row=material_read.item(store.db,item)
    selected=json.loads(row['processing_metadata']).get('_pass',{}).get('settings') if row else None
    return selected if selected is not None else live_settings(store,item)


def signature(store: 'Store', item: str, kind: str) -> str | None:
    row=material_read.item(store.db,item)
    if not row or row['viewed'] or not row['complete'] or row['content']=='{}':
        return None
    rules=live_settings(store,item)
    values=rules['values']
    if kind=='filter' and not values['prompt_filters'].strip() or kind=='theses' and not values['theses']:
        return None
    content=json.loads(row['content'])
    if awaiting_input(content):
        return None
    selected=blocks(content)
    if kind=='theses':
        filtered=store.filters.read(item)
        if filtered['status'] not in ('off','done') or filtered['hidden']:
            return None
        if filtered['status']=='done':
            selected=blocks(json.loads(row['filtered_content']))
    config=store.processor_config() if values['processor'].startswith('llamacpp') else None
    processor=dict(endpoint=config.endpoint,local_execution=config.local_execution) if config else None
    relevant=dict(prompt=values['prompt_filters' if kind=='filter' else 'prompt_theses'],processor=values['processor'],public_ai=rules['public_ai'],local=processor)
    return digest([row['title'],selected,relevant])


def snapshot(store: 'Store', item: str, generation: int) -> None:
    row=store.db.execute('SELECT processing_metadata FROM items WHERE id=?',(item,)).fetchone()
    metadata=json.loads(row[0])
    rules=live_settings(store,item)
    if metadata.get('_pass',{}).get('settings')!=rules:
        metadata['_pass']=dict(feed_id=rules['feed_id'],settings=deepcopy(rules),settings_version=store.settings_version(rules['feed_id']),generation=generation)
        with store.db:
            store.db.execute('UPDATE items SET processing_metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),item))


def request_settings(store: 'Store', item: str) -> tuple[dict[str, Any], bool, int]:
    chosen=settings(store,item)
    record=material_read.item(store.db,item)
    assert record is not None
    metadata=json.loads(record['processing_metadata'])
    version=metadata.get('_pass',{}).get('settings_version',store.settings_version(chosen['feed_id']))
    return {key:dict(value=value) for key,value in chosen['values'].items()},chosen['public_ai'],version


def filtered(content: dict[str, Any], item: str, result: dict[str, Any]) -> dict[str, Any]:
    from oko.processing.contracts import ProcessingError
    output=deepcopy(content)
    for block in output['blocks']:
        block['source_publication_id']=item
    removed=[]
    for fragment in result['removed']:
        for index,block in enumerate(output['blocks']):
            if block['type']=='text' and block['role']=='body' and fragment in block['text']:
                block['text']=block['text'].replace(fragment,'')
                removed.append(dict(publication_id=item,block_index=index,text=fragment))
                break
        else:
            raise ProcessingError('Фрагмент фильтра отсутствует в собственном блоке публикации')
    output.update(reason=result['reason'],removed=removed,hidden=result['hidden'])
    if result['hidden']:
        output['blocks']=[]
    return output
