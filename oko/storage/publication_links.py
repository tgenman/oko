"""Direct article dependencies; external identities come only from adapters."""
import json
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from oko.collection.adapters import ExternalReference

if TYPE_CHECKING:
    from oko.storage.store import Store


def sync(store: 'Store', item: str, references: tuple[ExternalReference,...]) -> None:
    owner=store.db.execute('SELECT * FROM items WHERE id=?',(item,)).fetchone()
    if owner['feed_id'] is None:
        return
    targets=[]
    for ref in references:
        if (ref.key.platform.value,ref.key.external_id)==(owner['platform'],owner['external_id']):
            continue
        row=store.db.execute('SELECT * FROM items WHERE platform=? AND external_id=?',
                             (ref.key.platform.value,ref.key.external_id)).fetchone()
        if row is None:
            identity=str(uuid4())
            store.db.execute('''INSERT INTO items(id,source_id,url,published,feed_id,members,published_known,
                platform,external_id,discovered_at) VALUES (?,?,?,0,NULL,'[]',0,?,?,?)''',
                (identity,'linked:'+identity,ref.url,ref.key.platform.value,ref.key.external_id,store.wall_clock()))
            store.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch',?)",(identity,))
        else:
            identity=row['id']
            if row['url']!=ref.url:
                raise ValueError('Conflicting linked publication identity')
            if row['viewed'] and row['feed_id'] is None:
                store.db.execute('UPDATE items SET viewed=0,complete=0 WHERE id=?',(identity,))
                store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1 WHERE kind='delete' AND item=?",(identity,))
                store.enqueue_fetch(identity)
        targets.append(identity)
    previous={row[0] for row in store.db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id=?',(item,))}
    store.db.execute('DELETE FROM publication_links WHERE source_publication_id=?',(item,))
    store.db.executemany('INSERT OR IGNORE INTO publication_links VALUES (?,?)',[(item,target) for target in targets])
    prune(store,previous-set(targets))


def feedless(store: 'Store', item: str) -> bool:
    return store.db.execute('SELECT 1 FROM items WHERE id=? AND platform IS NOT NULL AND feed_id IS NULL',(item,)).fetchone() is not None


def text(store: 'Store', item: str) -> str:
    sections=[]
    for row in store.db.execute('''SELECT i.* FROM publication_links l JOIN items i ON i.id=l.target_publication_id
            WHERE l.source_publication_id=? AND i.viewed=0 AND i.complete=1 ORDER BY i.discovered_at,i.id''',(item,)):
        content=json.loads(row['content'])
        body='\n\n'.join(block['text'] for block in content.get('blocks',[]) if block['type'] in ('text','quote') and block.get('text'))
        if body:
            sections.append('---\n\n# '+row['title']+'\n\nИсходник: '+row['url']+'\n\n'+body)
    return '\n\n'.join(sections)


def notify_parents(store: 'Store', item: str) -> None:
    for row in store.db.execute('SELECT source_publication_id FROM publication_links WHERE target_publication_id=?',(item,)).fetchall():
        store.db.execute('INSERT OR IGNORE INTO processing_dirty VALUES (?)',(row[0],))
        store.read_versions.media_changed(row[0])


def prune(store: 'Store', candidates: set[str]) -> None:
    for item in candidates:
        if not feedless(store,item) or store.db.execute('SELECT 1 FROM publication_links WHERE target_publication_id=?',(item,)).fetchone():
            continue
        store.publications.forget(item)
        store.versions.forget(item)
        store.db.execute('UPDATE items SET viewed=1,complete=0 WHERE id=?',(item,))
        store.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1 WHERE item=? AND kind!='delete'",(item,))
        store.db.execute("INSERT INTO jobs(kind,item) VALUES ('delete',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0",(item,))


def project_header(store: 'Store', item: str, header: dict[str,Any]) -> dict[str,Any]:
    from copy import deepcopy
    result=deepcopy(header)
    reasons=[result['reason']] if result.get('reason') else []
    for row in store.db.execute('''SELECT i.id,i.url,i.complete,i.content,j.error FROM publication_links l
            JOIN items i ON i.id=l.target_publication_id LEFT JOIN jobs j ON j.kind='fetch' AND j.item=i.id
            WHERE l.source_publication_id=? AND i.viewed=0 ORDER BY i.discovered_at,i.id''',(item,)):
        if not row['complete']:
            reasons.append('Связанная статья недоступна: '+row['error'] if row['error'] else 'Связанная статья ещё не загружена')
            continue
        article=json.loads(row['content']).get('extra',{}).get('_reader',{})
        if article.get('reason'):
            reasons.append(article['reason'])
        result.setdefault('attachments',[]).extend(article.get('attachments',[]))
        result.setdefault('_media_urls',{}).update(article.get('_media_urls',{}))
    result['reason']='; '.join(dict.fromkeys(reasons))
    result['full']=result.get('full',True) and not reasons
    return result


def retry(store: 'Store', item: str) -> None:
    for row in store.db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id=?',(item,)).fetchall():
        store.enqueue_fetch(row[0])
        store.media.retry(row[0])


def reading(store: 'Store', item: str, post: str) -> dict[str,Any] | None:
    articles=[]
    for row in store.db.execute('''SELECT i.*,j.error FROM publication_links l JOIN items i ON i.id=l.target_publication_id
            LEFT JOIN jobs j ON j.kind='fetch' AND j.item=i.id
            WHERE l.source_publication_id=? AND i.viewed=0 AND i.platform='telegraph' ORDER BY i.discovered_at,i.id''',(item,)):
        content=json.loads(row['content'])
        header=content.get('extra',{}).get('_reader',{})
        body='\n\n'.join(block.get('text','') for block in content.get('blocks',[]) if block['type'] in ('text','quote'))
        articles.append(dict(source_url=row['url'],title=row['title'] or 'Telegraph',text=body,
            error=row['error'] or header.get('reason') or None,
            media_urls=list(header.get('_media_urls',{}).values())))
    return dict(post_text=post,articles=articles) if articles else None
