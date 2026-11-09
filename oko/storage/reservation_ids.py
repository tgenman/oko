"""Allocate identities for future problem occurrences without replacing state."""
import hashlib
import json
import re
from typing import Any, TYPE_CHECKING
from collections.abc import Callable, Sequence

if TYPE_CHECKING:
    from oko.storage.store import Store


def allocate(store: 'Store', count: int, prefix: str, generate: Callable[[], str]) -> list[str]:
    accepted: list[str]=[]
    occupied: set[str]=set()
    for _ in range(3):
        missing=count-len(accepted)
        if missing<=0:break
        proposals=[generate() for _ in range(missing)]
        counts: dict[str,int]={}
        for value in proposals:counts[value]=counts.get(value,0)+1
        used=set(occupied)
        for start in range(0,len(proposals),400):
            chunk=proposals[start:start+400]
            used.update(row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE id=ANY(%s)',(chunk,)))
            keys=[prefix+value for value in chunk]
            used.update(row['key'][len(prefix):] for row in store.db.execute('SELECT key FROM state WHERE key=ANY(%s)',(keys,)))
        for value in proposals:
            if counts[value]==1 and value not in used:
                accepted.append(value);occupied.add(value)
    if len(accepted)!=count:raise RuntimeError('Unable to allocate '+prefix+' identity')
    return accepted


def cleanup_pointers(store: 'Store', cursor: str | None, prefix: str, version: str) -> tuple[str | None,bool]:
    """Delete only well-formed orphaned pointers, in bounded pages."""
    query='SELECT key,value FROM state WHERE key COLLATE "C">=%s AND key COLLATE "C"<%s'
    params: tuple[Any,...]=(prefix,prefix[:-1]+';')
    if cursor is not None:
        query+=' AND key COLLATE "C">%s'
        params=(*params,cursor)
    rows=store.db.execute(query+' ORDER BY key COLLATE "C" LIMIT 100',params).fetchall()
    if not rows:return None,True
    parsed=[]
    for row in rows:
        identity=row['key'][len(prefix):]
        try:body=json.loads(row['value'])
        except (TypeError,ValueError):continue
        if not re.fullmatch('[0-9a-f]{32}',identity):continue
        if not isinstance(body,list) or len(body)!=3 or body[0]!=version or not all(isinstance(value,str) for value in body[1:]):continue
        parsed.append((row['key'],row['value'],identity))
    saved={row['id']:row['identity'] for row in store.db.execute('SELECT id,identity FROM reader_problems WHERE id=ANY(%s)',([row[2] for row in parsed],))} if parsed else {}
    stale=[(key,value) for key,value,identity in parsed if identity not in saved or saved[identity]!=key]
    if stale:store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) v(key,value) WHERE s.key=v.key AND s.value=v.value',([x[0] for x in stale],[x[1] for x in stale]))
    return rows[-1]['key'],False


def protect_rows(store: 'Store', rows: Sequence[Any], label: str) -> None:
    """Protect exact problem identities without overwriting conflicting markers."""
    for start in range(0,len(rows),400):
        batch=rows[start:start+400]
        pairs=[('fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest(),json.dumps(['oko-fetch-null-v2',row['id'],row['identity']])) for row in batch]
        if not pairs:continue
        store.db.execute('INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) v(key,value) ON CONFLICT(key) DO NOTHING',([p[0] for p in pairs],[p[1] for p in pairs]))
        found={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([p[0] for p in pairs],))}
        if any(found.get(key)!=value for key,value in pairs):raise RuntimeError('Conflicting '+label+' protection marker')
