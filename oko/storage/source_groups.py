"""Address source problem groups by the identity visible to the reader.

The legacy key is textual and can represent several (code, owner) pairs; queries
must consider paired alternatives rather than their cartesian product.
Preimage evidence is keyed by the group identity; each set contains ("code", code)
and the ("material"|"feed", object ID) pairs observed under the graph lock.
"""
from typing import Any, TYPE_CHECKING
from collections.abc import Sequence

if TYPE_CHECKING:
    from oko.storage.store import Store


def manageable(identity: str, kind: str) -> bool:
    return len(identity.encode('utf-8'))<=1000 and identity.startswith('job:') and identity.endswith(':'+kind) and identity[4:-len(kind)-1].count(':')<=100


def possible_pairs(identity: str, kind: str) -> list[tuple[str,str]]:
    if not identity.startswith('job:') or not identity.endswith(':'+kind):return []
    raw=identity[4:-len(kind)-1]
    pairs=[]
    for n,ch in enumerate(raw):
        if ch==':' and raw[:n].startswith('source-') and raw[n+1:]:
            pairs.append((raw[:n],raw[n+1:]))
    return pairs


def job_keys(store: 'Store', job_id: int, code: str | None = None) -> set[str]:
    return set(job_evidence_many(store, [(job_id, code)]))





def job_keys_many(store: 'Store', job_ids: list[int]) -> dict[int,tuple[str,set[str]]]:
    if not job_ids:return {}
    rows=store.db.execute(f"""SELECT j.id,j.kind,j.item,j.reason_code,j.feed_id,
        coalesce(items.feed_id,media_owner.feed_id,root.feed_id,j.feed_id) AS owner
        FROM jobs j LEFT JOIN publications items ON items.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id OFFSET 0) root ON true
        WHERE j.id=ANY(%s) AND j.reason_code LIKE 'source-%%' AND j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete')""",(job_ids,)).fetchall()
    groups: dict[int,tuple[str,set[str]]]={}
    for row in rows:
        if row['id'] not in groups:groups[row['id']]=(row['kind'],set())
        groups[row['id']][1].add('job:'+row['reason_code']+':'+(row['owner'] or row['feed_id'] or row['item'] or '')+':'+row['kind'])
    return groups

def group_snapshot(store: 'Store', identity: str, kind: str, now: float, *, exclude: set[int] | None = None, pause_override: bool | None = None) -> tuple[dict[str,Any] | None,bool,set[int]]:
    pairs=possible_pairs(identity,kind)
    if not pairs:return None,False,set()
    codes=[p[0] for p in pairs];owners=[p[1] for p in pairs]
    rows=store.db.execute(f"""WITH source_groups AS MATERIALIZED (SELECT code,owner FROM unnest(%s::text[],%s::text[]) AS g(code,owner)),
        source_members AS MATERIALIZED (SELECT feed_id,id AS publication_id FROM publications WHERE feed_id=ANY(%s::text[])),
        items AS MATERIALIZED (
            SELECT g.code,g.owner,g.owner AS item FROM source_groups g
            UNION SELECT g.code,g.owner,m.publication_id FROM source_groups g JOIN source_members m ON m.feed_id=g.owner
            UNION SELECT g.code,g.owner,l.target_publication_id FROM source_groups g JOIN source_members m ON m.feed_id=g.owner
                JOIN publications root ON root.id=m.publication_id
                CROSS JOIN LATERAL (SELECT target_publication_id FROM publication_links WHERE source_publication_id=root.id OFFSET 0) l
            UNION SELECT g.code,g.owner,c.item FROM source_groups g CROSS JOIN LATERAL (SELECT item FROM jobs WHERE kind='delete' AND payload::jsonb->>'cleanup_feed'=g.owner OFFSET 0) c
        ), targets AS MATERIALIZED (
            SELECT j.id FROM items i CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind=%s AND item=i.item AND reason_code=i.code AND reason_code LIKE 'source-%%' AND octet_length(reason_code)<=1000 OFFSET 0) j
            UNION SELECT j.id FROM items i CROSS JOIN LATERAL (SELECT id FROM media_files WHERE publication_id=i.item OFFSET 0) m
                CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind=%s AND kind='download_media' AND item=m.id AND reason_code=i.code AND reason_code LIKE 'source-%%' AND octet_length(reason_code)<=1000 OFFSET 0) j
            UNION SELECT j.id FROM source_groups g CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind=%s AND feed_id=g.owner AND reason_code=g.code AND reason_code LIKE 'source-%%' AND octet_length(reason_code)<=1000 AND coalesce(octet_length(feed_id),0)<=1000 OFFSET 0) j
        ) SELECT j.id,j.kind,j.item,j.status,j.reason_message,j.reason_code,j.feed_id,m.resource_key,
            coalesce(items.feed_id,media_owner.feed_id,root.feed_id,j.feed_id) AS owner,
            coalesce(root.id,m.publication_id,j.item) AS affected
        FROM targets t CROSS JOIN LATERAL (SELECT * FROM jobs WHERE id=t.id OFFSET 0) j
        LEFT JOIN publications items ON items.id=j.item LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id OFFSET 0) root ON true
        WHERE (j.status IN ('pending','running','failed','unavailable','blocked') OR (j.status='done' AND j.kind='fetch' AND j.reason_message IS NOT NULL AND j.reason_code IS NOT NULL))
        {'AND NOT (j.id=ANY(%s))' if exclude else ''}
        ORDER BY j.kind,j.item,j.id,root.id""",(codes,owners,owners,kind,kind,kind,*([sorted(exclude)] if exclude else []))).fetchall()
    selected=[]
    members: set[int]=set()
    timed=False
    paused=pause_override if pause_override is not None else bool(kind=='download_media' and any(row['resource_key']=='youtube:subtitles' for row in rows) and store.youtube.status(now)['paused'])
    for row in rows:
        owner=row['owner'] or row['feed_id'] or row['item'] or ''
        if 'job:'+row['reason_code']+':'+owner+':'+kind!=identity:continue
        members.add(row['id'])
        if kind=='download_media' and row['resource_key']=='youtube:subtitles':
            timed=True
            if paused:continue
        selected.append((row,owner))
    if not selected:return None,timed,members
    first,first_owner=selected[0];code=first['reason_code']
    title={'feed-delete':'Удаление фида не завершено','fetch':'Не удалось загрузить материал','download_media':'Не удалось загрузить вложение','delete':'Удаление не завершено'}.get(kind,'Не удалось обработать материал')
    result: dict[str,Any]=dict(id=identity,code=code,section='object',title=title,
        consequence='Око продолжает попытки удаления.' if kind in ('delete','feed-delete') else 'Сохранённое содержимое остаётся доступным.',
        next_step='Включите обработку, чтобы повторить попытку.' if first['status']=='blocked' else 'Повторите загрузку вручную.' if first['status'] in ('done','failed') else 'Проверьте доступность источника.' if first['status']=='unavailable' else 'Око повторит попытку автоматически.',
        objects=[],action=None,operation='producer:'+kind,created_at=now,state='retrying' if first['status']=='running' or not first['reason_message'] else 'current',finished_at=None,outcome=None,next_at=None)
    if first['reason_message'] and kind!='delete':result['reason']=first['reason_message']
    if code=='source-reconnect':result.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.')
    seen_objects: set[tuple[str,str | None]]=set()
    result['_source_codes']={row['reason_code'] for row,_ in selected}
    for row,owner in selected:
        obj=dict(kind='feed' if kind=='feed-delete' else 'material',id=owner if kind=='feed-delete' else row['affected'])
        key=(obj['kind'],obj['id'])
        if kind!='delete' and key not in seen_objects:
            seen_objects.add(key)
            result['objects'].append(obj)
    return result,timed,members


def project(store: 'Store', identity: str, kind: str, now: float) -> dict[str,Any] | None:
    result=group_snapshot(store,identity,kind,now)[0]
    if result is not None:result.pop('_source_codes',None)
    return result


def notify_admission(store: 'Store', ids: list[int]) -> None:
    """Capture changed source ownership before the enclosing commit."""
    if not ids or not getattr(store,'youtube_admission_ready',False):return
    from oko.storage.youtube_admission_jobs import capture_fetch_flag_changes
    capture_fetch_flag_changes(store,ids)


def sync(store: 'Store', kind: str, keys: set[str], now: float, *, exclude: set[int] | None = None, evidence: dict[str,set[tuple[str,str]]] | None = None) -> bool:
    """Synchronize the exact groups captured around a Job/graph mutation.

    Caller holds the graph lock and owns the write transaction.
    """
    import hashlib
    import json
    from uuid import uuid4
    complete=True
    for canonical in sorted(keys):
        if not manageable(canonical,kind):
            complete=False
            continue
        matches=store.db.execute("SELECT identity,body FROM reader_problems WHERE finished IS NULL AND octet_length(body::jsonb->>'source_group_identity')<=1000 AND body::jsonb->>'source_group_identity'=%s LIMIT 2",(canonical,)).fetchall()
        if len(matches)>1:
            _,_,members=group_snapshot(store,canonical,kind,now,exclude=exclude)
            ordered_members=sorted(members)
            for offset in range(0,len(ordered_members),400):store.db.execute('UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned',(ordered_members[offset:offset+400],))
            notify_admission(store,ordered_members)
            complete=False
            continue
        row=matches[0] if matches else None
        current,timed,members=group_snapshot(store,canonical,kind,now,exclude=exclude)
        actual_codes={value for category,value in (evidence or {}).get(canonical,set()) if category=='code'}
        if current is not None:actual_codes.update(current.get('_source_codes',set()))
        if len(actual_codes)>1:
            ordered_members=sorted(members)
            for offset in range(0,len(ordered_members),400):store.db.execute('UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned',(ordered_members[offset:offset+400],))
            notify_admission(store,ordered_members)
            complete=False
            continue
        if row is not None:
            try:marked_body=json.loads(row['body'])
            except (TypeError,ValueError):marked_body={}
            prior=(evidence or {}).get(canonical,set())
            allowed={pair for pair in prior if pair[0] in ('material','feed')}
            allowed_codes={value for category,value in prior if category=='code'}
            if current is not None:
                allowed.update((obj['kind'],obj['id']) for obj in current['objects'])
                allowed_codes.update(current.get('_source_codes',set()))
            valid=isinstance(marked_body,dict) and marked_body.get('operation')=='producer:'+kind and marked_body.get('code') in allowed_codes and marked_body.get('code') in {code for code,_ in possible_pairs(canonical,kind)} and all(isinstance(marked_body.get(field),str) for field in ('title','section','consequence','next_step')) and marked_body.get('state') in ('current','retrying') and isinstance(marked_body.get('objects'),list)
            if valid:
                objects=marked_body['objects']
                valid=bool(objects or kind=='delete') and marked_body.get('action') is None and (current is not None or canonical in (evidence or {})) and all(isinstance(obj,dict) and set(obj)=={'kind','id'} and (obj.get('kind'),obj.get('id')) in allowed for obj in objects)
                if valid:valid=len({(obj['kind'],obj['id']) for obj in objects})==len(objects)
            if not valid:
                ordered_members=sorted(members)
                for offset in range(0,len(ordered_members),400):store.db.execute('UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned',(ordered_members[offset:offset+400],))
                notify_admission(store,ordered_members)
                complete=False
                continue
        timer_key='source-youtube-timed:'+hashlib.sha256((kind+'\0'+canonical).encode()).hexdigest()
        timer_value=json.dumps([kind,canonical])
        timer=store.db.execute('SELECT value FROM state WHERE key=%s',(timer_key,)).fetchone()
        if timer and timer['value']!=timer_value:raise RuntimeError('Source timer identity collision')
        if timed and not timer:store.db.execute('INSERT INTO state(key,value) VALUES (%s,%s)',(timer_key,timer_value))
        if not timed and timer:store.db.execute('DELETE FROM state WHERE key=%s AND value=%s',(timer_key,timer_value))
        if row is not None:
            actual=row['identity']
        else:
            occupied=store.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(canonical,)).fetchone()
            actual=canonical
            fallback=False
            if occupied is not None:
                body=json.loads(occupied['body'])
                allowed={(category,value) for category,value in (evidence or {}).get(canonical,set()) if category in ('material','feed')}
                if current is not None:allowed.update((obj['kind'],obj['id']) for obj in current['objects'])
                # Current fallback records use the exact producer operation.
                # No adoption of old producer:status records or former owner keys.
                fallback=(isinstance(body,dict) and body.get('operation')=='producer:'+kind
                    and body.get('code') in actual_codes and isinstance(body.get('objects'),list)
                    and bool(body['objects'] or kind=='delete')
                    and all(isinstance(obj,dict) and set(obj)=={'kind','id'} and (obj.get('kind'),obj.get('id')) in allowed for obj in body['objects']))
            if fallback:row=occupied
            if occupied is not None and not fallback:
                # A current group may occupy the same textual key. Keep its
                # occurrence separate; old diagnostic records are removed at release.
                for _ in range(3):
                    actual='job:source-isolated:'+hashlib.sha256(canonical.encode()).hexdigest()+':'+uuid4().hex
                    if not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(actual,)).fetchone():break
                else:raise RuntimeError('Unable to allocate source group identity')
        if current is not None:
            current.pop('_source_codes',None)
            current['source_group_identity']=canonical
        if row is not None or current is not None:
            store.problems.sync_candidate(actual,current,now)
    return complete


def sync_transition(store: 'Store', kind: str, before: set[str], after: set[str], now: float, *, evidence: dict[str,set[tuple[str,str]]] | None = None) -> bool:
    """Move a uniquely attributable occurrence when its old group vanished."""
    import hashlib
    import json
    from uuid import uuid4
    old=before-after;new=after-before
    evidence=dict(evidence or {})
    if len(old)==1 and len(new)==1 and all(manageable(key,kind) for key in before | after):
        previous=next(iter(old));target=next(iter(new))
        candidate=project(store,target,kind,now)
        still=project(store,previous,kind,now)
        rows=store.db.execute("SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND octet_length(body::jsonb->>'source_group_identity')<=1000 AND body::jsonb->>'source_group_identity'=%s LIMIT 2",(previous,)).fetchall()
        row=rows[0] if len(rows)==1 else None
        if row is not None:
            try:previous_body=json.loads(row['body'])
            except (TypeError,ValueError):previous_body={}
            if not isinstance(previous_body,dict) or previous_body.get('operation')!='producer:'+kind:row=None
        owned=store.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND octet_length(body::jsonb->>'source_group_identity')<=1000 AND body::jsonb->>'source_group_identity'=%s LIMIT 1",(target,)).fetchone() is not None
        occupied=store.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND identity=%s LIMIT 1",(target,)).fetchone() is not None
        if candidate is not None and still is None and row is not None and not owned:
            try:body=json.loads(row['body'])
            except (TypeError,ValueError):body={}
            allowed={pair for pair in evidence.get(previous,set()) if pair[0] in ('material','feed')}
            objects=body.get('objects') if isinstance(body,dict) else None
            valid=isinstance(body,dict) and body.get('code')==candidate['code'] and ('code',body.get('code')) in evidence.get(previous,set()) and body.get('code') in {code for code,_ in possible_pairs(previous,kind)} and all(isinstance(body.get(field),str) for field in ('title','section','consequence','next_step')) and body.get('state') in ('current','retrying') and body.get('action') is None and isinstance(objects,list) and (bool(objects) or kind=='delete') and all(isinstance(obj,dict) and set(obj)=={'kind','id'} and (obj.get('kind'),obj.get('id')) in allowed for obj in objects)
            if valid and isinstance(objects,list):valid=len({(obj['kind'],obj['id']) for obj in objects})==len(objects)
            if valid:
                actual=target
                if occupied:
                    for _ in range(3):
                        actual='job:source-isolated:'+hashlib.sha256(target.encode()).hexdigest()+':'+uuid4().hex
                        if not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(actual,)).fetchone():break
                    else:raise RuntimeError('Unable to allocate source group identity')
                evidence.setdefault(target,set()).update(evidence.get(previous,set()))
                body['source_group_identity']=target
                store.db.execute('UPDATE reader_problems SET identity=%s,body=%s WHERE id=%s AND finished IS NULL',(actual,json.dumps(body),row['id']))
                from oko.storage.reader_status import changed as status_changed
                status_changed(store.db)
    return sync(store,kind,before | after,now,evidence=evidence)





def sync_youtube_timer(store: 'Store', now: float) -> None:
    """Reconcile only indexed source groups with timed subtitle members."""
    import hashlib
    import json
    from oko.storage import memberships
    memberships.lock_graph(store.db)
    cursor: str | None=None
    while True:
        query="SELECT key,value FROM state WHERE key LIKE 'source-youtube-timed:%%'"
        if cursor is not None:query+=' AND key COLLATE "C">%s'
        rows=store.db.execute(query+' ORDER BY key COLLATE "C" LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
        if not rows:break
        grouped: dict[str,set[str]]={}
        for row in rows:
            try:value=json.loads(row['value'])
            except (TypeError,ValueError):continue
            if isinstance(value,list) and len(value)==2 and value[0]=='download_media' and isinstance(value[1],str) and row['key']=='source-youtube-timed:'+hashlib.sha256((value[0]+'\0'+value[1]).encode()).hexdigest():grouped.setdefault(value[0],set()).add(value[1])
        for kind,keys in grouped.items():
            for key in sorted(keys):
                unpaused,_,_=group_snapshot(store,key,kind,now,pause_override=False)
                objects={(obj['kind'],obj['id']) for obj in unpaused['objects']} if unpaused is not None else set()
                if unpaused is not None:objects.update(('code',code) for code in unpaused.get('_source_codes',set()))
                sync(store,kind,{key},now,evidence={key:objects})
        cursor=rows[-1]['key']


def sync_jobs(store: 'Store', job_ids: list[int], now: float) -> None:
    """Reconcile changed Jobs in batches, projecting each affected group once."""
    ordered=sorted(set(job_ids))
    grouped: dict[str,set[str]]={}
    all_groups: dict[int,tuple[str,set[str]]]={}
    for start in range(0,len(ordered),400):
        groups=job_keys_many(store,ordered[start:start+400]);all_groups.update(groups)
        for kind,keys in groups.values():grouped.setdefault(kind,set()).update(keys)
    evidence=job_evidence_many(store,[(job_id,None) for job_id in ordered])
    _sync_batch(store, all_groups, grouped, evidence, list(all_groups), now)


def previous_keys_many(store: 'Store', rows: list[tuple[int,str]]) -> dict[int,tuple[str,set[str]]]:
    if not rows:return {}
    result: dict[int,tuple[str,set[str]]]={}
    for start in range(0,len(rows),400):
        batch=rows[start:start+400]
        found=store.db.execute(f"""SELECT j.id,j.kind,j.item,v.code AS reason_code,j.feed_id,
            coalesce(items.feed_id,media_owner.feed_id,root.feed_id,j.feed_id) AS owner
            FROM unnest(%s::bigint[],%s::text[]) AS v(id,code) JOIN jobs j ON j.id=v.id
            LEFT JOIN publications items ON items.id=j.item LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
            LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
            LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
            LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id OFFSET 0) root ON true""",([r[0] for r in batch],[r[1] for r in batch])).fetchall()
        for row in found:
            if row['id'] not in result:result[row['id']]=(row['kind'],set())
            result[row['id']][1].add('job:'+row['reason_code']+':'+(row['owner'] or row['feed_id'] or row['item'] or '')+':'+row['kind'])
    return result


def sync_changed_rows(store: 'Store', rows: list[Any], now: float) -> None:
    """Reconcile groups for a batch whose source code was captured via OLD."""
    previous=[(row['id'],row['previous_code']) for row in rows if isinstance(row['previous_code'],str) and row['previous_code'].startswith('source-')]
    if not previous:return
    before=previous_keys_many(store,previous)
    evidence=job_evidence_many(store,previous)
    current: dict[int,tuple[str,set[str]]]={}
    ids=[row[0] for row in previous]
    for start in range(0,len(ids),400):current.update(job_keys_many(store,ids[start:start+400]))
    grouped: dict[str,set[str]]={}
    for kind,keys in [*before.values(),*current.values()]:grouped.setdefault(kind,set()).update(keys)
    _sync_batch(store, current, grouped, evidence, ids, now)


def _sync_batch(store: 'Store', current: dict[int,tuple[str,set[str]]], grouped: dict[str,set[str]],
                evidence: dict[str,set[tuple[str,str]]], affected_ids: list[int], now: float) -> None:
    failed={(kind,key) for kind,keys in grouped.items() for key in keys if not sync(store,kind,{key},now,evidence=evidence)}
    eligible=[job_id for job_id,(kind,keys) in current.items() if all((kind,key) not in failed for key in keys)]
    eligible_set=set(eligible)
    ineligible=[job_id for job_id in affected_ids if job_id not in eligible_set]
    for start in range(0,len(eligible),400):
        store.db.execute("UPDATE jobs SET source_event_owned=(status IN ('pending','running','failed','unavailable','blocked') OR (kind='fetch' AND status='done' AND reason_message IS NOT NULL)) WHERE id=ANY(%s) AND reason_code LIKE 'source-%%'",(eligible[start:start+400],))
    for start in range(0,len(ineligible),400):
        store.db.execute("UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned",(ineligible[start:start+400],))
    notify_admission(store,eligible+ineligible)


def job_evidence_many(store: 'Store', rows: Sequence[tuple[int,str | None]]) -> dict[str,set[tuple[str,str]]]:
    """Capture the actual objects attributable to Jobs under the graph lock."""
    evidence: dict[str,set[tuple[str,str]]]={}
    for start in range(0,len(rows),400):
        batch=rows[start:start+400]
        found=store.db.execute(f"""SELECT j.id,j.kind,j.item,coalesce(v.code,j.reason_code) AS code,j.feed_id,
            coalesce(items.feed_id,media_owner.feed_id,root.feed_id,j.feed_id) AS owner,
            coalesce(root.id,m.publication_id,j.item) AS affected
            FROM unnest(%s::bigint[],%s::text[]) AS v(id,code) JOIN jobs j ON j.id=v.id
            LEFT JOIN publications items ON items.id=j.item LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
            LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
            LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
            LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id OFFSET 0) root ON true
            WHERE j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND coalesce(v.code,j.reason_code) LIKE 'source-%%'""",([r[0] for r in batch],[r[1] for r in batch])).fetchall()
        for row in found:
            owner=row['owner'] or row['feed_id'] or row['item'] or ''
            key='job:'+row['code']+':'+owner+':'+row['kind']
            objects=evidence.setdefault(key,set())
            objects.add(('code',row['code']))
            value=owner if row['kind']=='feed-delete' else row['affected']
            if row['kind']!='delete' and isinstance(value,str):objects.add(('feed' if row['kind']=='feed-delete' else 'material',value))
    return evidence


def previous_feed_groups(store: 'Store', job_id: int, feed: str | None) -> tuple[str,dict[str,set[tuple[str,str]]]] | None:
    rows=store.db.execute(f"""SELECT j.kind,j.item,j.reason_code,
        coalesce(items.feed_id,media_owner.feed_id,root.feed_id,%s::text) AS owner,
        coalesce(root.id,m.publication_id,j.item) AS affected
        FROM jobs j LEFT JOIN publications items ON items.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id OFFSET 0) root ON true
        WHERE j.id=%s AND j.reason_code LIKE 'source-%%' AND j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete')""",(feed,job_id)).fetchall()
    if not rows:return None
    evidence: dict[str,set[tuple[str,str]]]={}
    for row in rows:
        owner=row['owner'] or feed or row['item'] or ''
        key='job:'+row['reason_code']+':'+owner+':'+row['kind']
        objects=evidence.setdefault(key,set())
        objects.add(('code',row['reason_code']))
        value=owner if row['kind']=='feed-delete' else row['affected']
        if row['kind']!='delete' and isinstance(value,str):objects.add(('feed' if row['kind']=='feed-delete' else 'material',value))
    return rows[0]['kind'],evidence
