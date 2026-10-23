"""Relevant publication inputs and feed rules, independent of downloaded bytes."""
from copy import deepcopy
import hashlib
import json
from typing import Any, TYPE_CHECKING
from oko.storage import material_read, youtube_media

if TYPE_CHECKING:
    from oko.storage.store import Store
    from oko.processing.contracts import InputSource


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def blocks(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [block for block in content.get('blocks',[]) if block['type']=='quote' or block['type']=='text' and block['role']=='body']


def content(store: 'Store', item: str) -> dict[str, Any]:
    row=store.db.execute('SELECT content FROM items WHERE id=?',(item,)).fetchone()
    own=youtube_media.add_input(store,json.loads(row[0])) if row else {}
    linked=store.db.execute('''SELECT i.id,i.content FROM publication_links l JOIN items i ON i.id=l.target_publication_id
        WHERE l.source_publication_id=? AND i.complete=1 AND i.viewed=0 ORDER BY i.discovered_at,i.id''',(item,)).fetchall()
    if not linked:
        return own
    for block in own.get('blocks',[]):
        block.setdefault('source_publication_id',item)
    for row in linked:
        for index,block in enumerate(json.loads(row['content']).get('blocks',[])):
            block.update(source_publication_id=row['id'],source_block_index=index)
            own.setdefault('blocks',[]).append(block)
    return own


def sources(store: 'Store', item: str, kind: str) -> tuple['InputSource', ...]:
    from oko.processing.contracts import InputSource
    from oko.collection.contracts import ReplyPreview
    selected=content(store,item)
    row=store.db.execute('SELECT filtered_content FROM items WHERE id=?',(item,)).fetchone()
    if kind=='theses' and row and row[0]!='{}' and 'youtube' not in selected.get('extra',{}):
        selected=json.loads(row[0])
    groups: dict[str,list[dict[str,Any]]]={item:[]}
    for block in blocks(selected):
        groups.setdefault(block.get('source_publication_id',item),[]).append(block)
    result=[]
    for identity,parts in groups.items():
        record=store.db.execute('SELECT title,url,published FROM items WHERE id=?',(identity,)).fetchone()
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
    return store.db.execute('''SELECT 1 FROM publication_links l JOIN items i ON i.id=l.target_publication_id
        WHERE l.source_publication_id=? AND i.viewed=0 AND i.complete=0 LIMIT 1''',(item,)).fetchone() is not None


def live_settings(store: 'Store', item: str) -> dict[str, Any]:
    feed=store.item_feed(item)
    current=store.subscriptions.effective('feed',feed,details=False)
    rules=dict(feed_id=feed,public_ai=current['feed']['public_ai'],importance=current['feed']['importance'],values={key:value['value'] for key,value in current['effective'].items() if key in ('prompt_filters','theses','prompt_theses','processor')})
    return rules


def settings(store: 'Store', item: str) -> dict[str, Any]:
    row=material_read.item(store.db,item)
    selected=json.loads(row['processing_metadata']).get('_pass',{}).get('settings') if row else None
    return selected if selected is not None else live_settings(store,item)


def signature(store: 'Store', item: str, kind: str) -> str | None:
    row=material_read.item(store.db,item)
    if not row or row['viewed'] or row['feed_id'] is None or not row['complete'] or row['content']=='{}':
        return None
    rules=live_settings(store,item)
    values=rules['values']
    youtube=row['platform']=='youtube'
    summary=values['theses'] or youtube and rules['importance']=='high'
    if kind=='filter' and not values['prompt_filters'].strip() or kind=='theses' and not summary:
        return None
    selected_content=json.loads(row['content']) if youtube else content(store,item)
    if youtube and selected_content.get('extra',{}).get('youtube',{}).get('captions')=='available' and not youtube_media.subtitle_path(store,youtube_media.subtitle_id(selected_content)):
        return None
    if awaiting_input(selected_content) or awaiting_linked_input(store,item,selected_content):
        return None
    selected=([dict(captions=selected_content.get('extra',{}).get('youtube',{}).get('captions'),
                    digest=selected_content.get('extra',{}).get('youtube',{}).get('captions_digest'))]
              if youtube else blocks(selected_content))
    if kind=='theses':
        filtered=store.filters.read(item)
        if filtered['status'] not in ('off','done') or filtered['hidden']:
            return None
        if filtered['status']=='done' and row['platform']!='youtube':
            selected=blocks(json.loads(row['filtered_content']))
    config=store.processor_config() if values['processor'].startswith('llamacpp') else None
    processor=dict(endpoint=config.endpoint,local_execution=config.local_execution) if config else None
    relevant=dict(prompt=values['prompt_filters' if kind=='filter' else 'prompt_theses'],processor=values['processor'],public_ai=rules['public_ai'],local=processor)
    return digest([None if youtube else row['title'],selected,relevant])


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
