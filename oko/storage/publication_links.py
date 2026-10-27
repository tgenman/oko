"""Direct article dependencies; external identities come only from adapters."""
from oko.storage.database import expect_row, Row
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from oko.collection.adapters import ExternalReference
from oko.domain.collection import CollectionEffects
from oko.storage.reading import reading_value, saved_content

if TYPE_CHECKING:
    from oko.storage.store import Store


@reading_value
def children(store: 'Store',item: str) -> list[Row]:
    from oko.storage.material_read import current
    inputs = current(store.db)
    if inputs is not None and item in inputs.children:
        return inputs.children[item]
    return list(store.db.execute("SELECT i . * , j . error AS fetch_error FROM publication_links l JOIN items_read i ON i . id = l . target_publication_id LEFT JOIN jobs j ON j . kind = 'fetch' AND j . item = i . id WHERE l . source_publication_id = %s AND i . viewed = 0 ORDER BY l . rowid",(item,)).fetchall())


def sync(store: 'Store', item: str, references: tuple[ExternalReference,...], effects: CollectionEffects) -> bool:
    owner=expect_row(store.db.execute('SELECT * FROM items_read WHERE id = %s',(item,)).fetchone())
    if owner['feed_id'] is None:
        return False
    targets=[]
    for ref in references:
        if (ref.key.platform.value,ref.key.external_id)==(owner['platform'],owner['external_id']):
            continue
        row=store.db.execute('SELECT * FROM items_read WHERE platform = %s AND external_id = %s',
                             (ref.key.platform.value,ref.key.external_id)).fetchone()
        if row is None:
            identity=str(uuid4())
            store.db.execute("INSERT INTO items ( id , source_id , url , published , members , published_known , platform , external_id , discovered_at ) VALUES ( %s , %s , %s , 0 , '[]' , 0 , %s , %s , %s )",
                (identity,'linked:'+identity,ref.url,ref.key.platform.value,ref.key.external_id,store.wall_clock()))
            effects.fetch[identity]=False
        else:
            identity=row['id']
            if row['url']!=ref.url:
                raise ValueError('Conflicting linked publication identity')
            if row['viewed'] and row['feed_id'] is None:
                store.db.execute('UPDATE items SET viewed = 0 , complete = 0 WHERE id = %s',(identity,))
                effects.fetch[identity]=True
            elif owner['platform']=='rss':
                effects.refresh_links.add(identity)
        targets.append(identity)
    previous={row[0] for row in store.db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s',(item,))}
    store.db.execute('DELETE FROM publication_links WHERE source_publication_id = %s',(item,))
    store.db.executemany('INSERT INTO publication_links VALUES ( %s , %s ) ON CONFLICT DO NOTHING',[(item,target) for target in targets])
    prune(store,previous-set(targets))
    changed=previous!=set(targets)
    if changed:
        store.db.execute('INSERT INTO processing_dirty VALUES ( %s ) ON CONFLICT DO NOTHING',(item,))
        store.read_versions.linked_changed(item)
    return changed


def feedless(store: 'Store', item: str) -> bool:
    return store.db.execute('SELECT 1 FROM items_read WHERE id = %s AND platform IS NOT NULL AND feed_id IS NULL',(item,)).fetchone() is not None


def text(store: 'Store', item: str) -> str:
    sections=[]
    for row in children(store,item):
        if not row['complete']:
            continue
        content=saved_content(row['content'])
        body='\n\n'.join(block['text'] for block in content.get('blocks',[]) if block['type'] in ('text','quote') and block.get('text'))
        if body:
            sections.append('---\n\n# '+row['title']+'\n\nИсходник: '+row['url']+'\n\n'+body)
    return '\n\n'.join(sections)


def notify_parents(store: 'Store', item: str) -> None:
    for row in store.db.execute('SELECT source_publication_id FROM publication_links WHERE target_publication_id = %s',(item,)).fetchall():
        store.db.execute('INSERT INTO processing_dirty VALUES ( %s ) ON CONFLICT DO NOTHING',(row[0],))
        store.db.execute('INSERT INTO telegram_read_dirty SELECT feed_id FROM items_read WHERE id = %s AND feed_id IS NOT NULL ON CONFLICT DO NOTHING',(row[0],))
        store.publications.reconcile(row[0])
        store.read_versions.linked_changed(row[0])


def prune(store: 'Store', candidates: set[str]) -> None:
    if not candidates:
        return
    rows=store.db.execute('WITH RECURSIVE needed ( id ) AS ( SELECT i . id FROM items_read i JOIN feed_publications f ON f . publication_id = i . id WHERE i . viewed = 0 UNION SELECT l . target_publication_id FROM publication_links l JOIN needed n ON n . id = l . source_publication_id ) SELECT id FROM items_read WHERE platform IS NOT NULL AND viewed = 0 AND id NOT IN ( SELECT id FROM needed )').fetchall()
    items = [row['id'] for row in rows]
    store.publications.forget_many(items)
    for start in range(0, len(items), 400):
        batch = items[start:start+400]
        marks = ','.join('%s' for _ in batch)
        store.db.execute(f'DELETE FROM source_deleted WHERE item IN ( {marks} )', batch)
        store.db.execute(f'DELETE FROM filter_inputs WHERE item IN ( {marks} )', batch)
        store.db.execute(f'DELETE FROM thesis_inputs WHERE item IN ( {marks} )', batch)
        store.db.execute(f'UPDATE items SET viewed = 1 , complete = 0 WHERE id IN ( {marks} )', batch)


def project_header(store: 'Store', item: str, header: dict[str,Any]) -> dict[str,Any]:
    from copy import deepcopy
    result=deepcopy(header)
    reasons=[result['reason']] if result.get('reason') else []
    for row in children(store,item):
        if not row['complete']:
            reasons.append('Связанная статья недоступна: '+row['fetch_error'] if row['fetch_error'] else 'Связанная статья ещё не загружена')
            continue
        article=deepcopy(saved_content(row['content']).get('extra',{}).get('_reader',{}))
        result['changed']=bool(result.get('changed') or article.get('changed'))
        if article.get('reason'):
            reasons.append(article['reason'])
        result.setdefault('attachments',[]).extend(article.get('attachments',[]))
        result.setdefault('_media_urls',{}).update(article.get('_media_urls',{}))
    result['reason']='; '.join(dict.fromkeys(reasons))
    result['full']=result.get('full',True) and not reasons
    return result


def reading(store: 'Store', item: str, post: str) -> dict[str,Any] | None:
    articles=[]
    for row in children(store,item):
        if row['platform']!='telegraph':
            continue
        content=saved_content(row['content'])
        header=content.get('extra',{}).get('_reader',{})
        body='\n\n'.join(block.get('text','') for block in content.get('blocks',[]) if block['type'] in ('text','quote'))
        articles.append(dict(source_url=row['url'],title=row['title'] or 'Telegraph',text=body,
            error=row['fetch_error'] or header.get('reason') or None,
            media_urls=list(header.get('_media_urls',{}).values())))
    return dict(post_text=post,articles=articles) if articles else None
