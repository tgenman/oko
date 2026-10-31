"""Address source problem groups by the identity visible to the reader.

The legacy key is textual and can represent several (code, owner) pairs; queries
must consider paired alternatives rather than their cartesian product.
Preimage evidence is keyed by the group identity; each set contains ("code", code)
and the ("material"|"feed", object ID) pairs observed under the graph lock.
"""
from typing import Any, TYPE_CHECKING
from collections.abc import Sequence
from oko.storage.publication_owners import owner_expr

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
    rows=store.db.execute(f"""SELECT j.kind,j.item,coalesce(%s,j.problem_code) AS problem_code,j.feed_id,
        coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('root')},j.feed_id) AS owner
        FROM jobs j LEFT JOIN publications items ON items.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id AND viewed=0 OFFSET 0) root ON true
        WHERE j.id=%s AND coalesce(%s,j.problem_code) LIKE 'source-%%' AND j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete')""",(code,job_id,code)).fetchall()
    return {'job:'+r['problem_code']+':'+(r['owner'] or r['feed_id'] or r['item'] or 'demo')+':'+r['kind'] for r in rows}




def job_keys_with_previous_feed(store: 'Store', job_id: int, feed: str | None) -> tuple[str,set[str]] | None:
    rows=store.db.execute(f"""SELECT j.kind,j.item,j.problem_code,
        coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('root')},%s::text) AS owner
        FROM jobs j LEFT JOIN publications items ON items.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id AND viewed=0 OFFSET 0) root ON true
        WHERE j.id=%s AND j.problem_code LIKE 'source-%%' AND j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete')""",(feed,job_id)).fetchall()
    if not rows:return None
    kind=rows[0]['kind']
    return kind,{'job:'+row['problem_code']+':'+(row['owner'] or feed or row['item'] or 'demo')+':'+kind for row in rows}

def job_keys_many(store: 'Store', job_ids: list[int]) -> dict[int,tuple[str,set[str]]]:
    if not job_ids:return {}
    rows=store.db.execute(f"""SELECT j.id,j.kind,j.item,j.problem_code,j.feed_id,
        coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('root')},j.feed_id) AS owner
        FROM jobs j LEFT JOIN publications items ON items.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id AND viewed=0 OFFSET 0) root ON true
        WHERE j.id=ANY(%s) AND j.problem_code LIKE 'source-%%' AND j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete')""",(job_ids,)).fetchall()
    groups: dict[int,tuple[str,set[str]]]={}
    for row in rows:
        if row['id'] not in groups:groups[row['id']]=(row['kind'],set())
        groups[row['id']][1].add('job:'+row['problem_code']+':'+(row['owner'] or row['feed_id'] or row['item'] or 'demo')+':'+row['kind'])
    return groups

def group_snapshot(store: 'Store', identity: str, kind: str, now: float, *, exclude: set[int] | None = None, pause_override: bool | None = None) -> tuple[dict[str,Any] | None,bool,set[int]]:
    pairs=possible_pairs(identity,kind)
    if not pairs:return None,False,set()
    codes=[p[0] for p in pairs];owners=[p[1] for p in pairs]
    rows=store.db.execute(f"""WITH source_groups AS MATERIALIZED (SELECT code,owner FROM unnest(%s::text[],%s::text[]) AS g(code,owner)),
        source_members AS MATERIALIZED (SELECT feed_id,publication_id FROM feed_publications WHERE is_primary AND feed_id=ANY(%s::text[])),
        items AS MATERIALIZED (
            SELECT g.code,g.owner,g.owner AS item FROM source_groups g
            UNION SELECT g.code,g.owner,m.publication_id FROM source_groups g JOIN source_members m ON m.feed_id=g.owner
            UNION SELECT g.code,g.owner,l.target_publication_id FROM source_groups g JOIN source_members m ON m.feed_id=g.owner
                JOIN publications root ON root.id=m.publication_id AND root.viewed=0
                CROSS JOIN LATERAL (SELECT target_publication_id FROM publication_links WHERE source_publication_id=root.id OFFSET 0) l
            UNION SELECT g.code,g.owner,c.item FROM source_groups g CROSS JOIN LATERAL (SELECT item FROM jobs WHERE kind='delete' AND payload::jsonb->>'cleanup_feed'=g.owner OFFSET 0) c
                JOIN publications p ON p.id=c.item AND p.viewed=1
        ), targets AS MATERIALIZED (
            SELECT j.id FROM items i CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind=%s AND (item=i.item OR (i.owner='demo' AND (item='' OR (item IS NULL AND feed_id IS NULL)))) AND problem_code=i.code AND problem_code LIKE 'source-%%' AND octet_length(problem_code)<=1000 OFFSET 0) j
            UNION SELECT j.id FROM items i CROSS JOIN LATERAL (SELECT id FROM media_files WHERE publication_id=i.item OFFSET 0) m
                CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind=%s AND kind='download_media' AND item=m.id AND problem_code=i.code AND problem_code LIKE 'source-%%' AND octet_length(problem_code)<=1000 OFFSET 0) j
            UNION SELECT j.id FROM source_groups g CROSS JOIN LATERAL (SELECT id FROM jobs WHERE kind=%s AND feed_id=g.owner AND problem_code=g.code AND problem_code LIKE 'source-%%' AND octet_length(problem_code)<=1000 AND coalesce(octet_length(feed_id),0)<=1000 OFFSET 0) j
        ) SELECT j.id,j.kind,j.item,j.status,j.error,j.problem_code,j.feed_id,m.resource_key,
            coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('root')},j.feed_id) AS owner,
            coalesce(items.viewed,media_owner.viewed) AS viewed,coalesce(root.id,m.publication_id,j.item) AS affected
        FROM targets t CROSS JOIN LATERAL (SELECT * FROM jobs WHERE id=t.id OFFSET 0) j
        LEFT JOIN publications items ON items.id=j.item LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id AND viewed=0 OFFSET 0) root ON true
        WHERE (j.status IN ('pending','running','failed','unavailable','blocked') OR (j.status='done' AND j.kind='fetch' AND j.error IS NOT NULL AND j.problem_code IS NOT NULL))
        {'AND NOT (j.id=ANY(%s))' if exclude else ''}
        ORDER BY j.kind,j.item,j.id,root.id""",(codes,owners,owners,kind,kind,kind,*([sorted(exclude)] if exclude else []))).fetchall()
    selected=[]
    members: set[int]=set()
    timed=False
    paused=pause_override if pause_override is not None else bool(kind=='download_media' and any(row['resource_key']=='youtube:subtitles' for row in rows) and store.youtube.status(now)['paused'])
    for row in rows:
        owner=row['owner'] or row['feed_id'] or row['item'] or 'demo'
        if 'job:'+row['problem_code']+':'+owner+':'+kind!=identity:continue
        members.add(row['id'])
        if row['viewed'] and kind not in ('delete','feed-delete'):continue
        if kind=='download_media' and row['resource_key']=='youtube:subtitles':
            timed=True
            if paused:continue
        selected.append((row,owner))
    if not selected:return None,timed,members
    first,first_owner=selected[0];code=first['problem_code']
    title={'feed-delete':'Удаление фида не завершено','fetch':'Не удалось загрузить материал','download_media':'Не удалось загрузить вложение','delete':'Удаление не завершено'}.get(kind,'Не удалось обработать материал')
    result: dict[str,Any]=dict(id=identity,code=code,section='object',title=title,
        consequence='Око продолжает попытки удаления.' if kind in ('delete','feed-delete') else 'Сохранённое содержимое остаётся доступным.',
        next_step='Включите обработку, чтобы повторить попытку.' if first['status']=='blocked' else 'Повторите загрузку вручную.' if first['status'] in ('done','failed') else 'Проверьте доступность источника.' if first['status']=='unavailable' else 'Око повторит попытку автоматически.',
        objects=[],action=None,operation='producer:'+kind,created_at=now,state='retrying' if first['status']=='running' or not first['error'] else 'current',finished_at=None,outcome=None,next_at=None)
    if first['error'] and kind!='delete':result['reason']=first['error']
    if code=='source-reconnect':result.update(section='action',next_step='Проверьте привязку источника; доступ не изменяется автоматически.')
    seen_objects: set[tuple[str,str | None]]=set()
    result['_source_codes']={row['problem_code'] for row,_ in selected}
    for row,owner in selected:
        legacy_feed=row['feed_id'] or row['item'] or 'demo'
        legacy='job:'+row['problem_code']+':'+legacy_feed+':'+kind
        if legacy!=identity:result.setdefault('_legacy_ids',set()).add(legacy)
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
            if occupied is not None:
                try:body=json.loads(occupied['body'])
                except (TypeError,ValueError):body={}
                compatible=isinstance(body,dict) and body.get('operation') in (None,'producer:status','producer:'+kind) and (current is None or body.get('code')==current['code']) and all(isinstance(body.get(field),str) for field in ('title','section','consequence','next_step')) and body.get('state') in ('current','retrying') and (body.get('next_at') is None or isinstance(body.get('next_at'),(int,float)))
                if compatible:
                    protected_key='source-legacy-protected:'+hashlib.sha256(canonical.encode()).hexdigest()
                    protected=store.db.execute('SELECT value FROM state WHERE key=%s',(protected_key,)).fetchone()
                    if protected:
                        try:protected_value=json.loads(protected['value'])
                        except (TypeError,ValueError):protected_value=None
                        if not isinstance(protected_value,list) or len(protected_value)!=3 or protected_value[:2]!=['oko-source-legacy-v1',canonical] or not isinstance(protected_value[2],str) or protected_value[2]==occupied['id']:
                            compatible=False
                if compatible and current is not None:
                    allowed={(o.get('kind'),o.get('id')) for o in current['objects']} | set((evidence or {}).get(canonical,set()))
                    old=body.get('objects')
                    compatible=isinstance(old,list) and ((bool(old) and all(isinstance(o,dict) and (o.get('kind'),o.get('id')) in allowed for o in old)) or (not old and kind=='delete' and body.get('operation')=='producer:delete' and body.get('code')==current['code']))
                if not compatible:
                    for _ in range(3):
                        actual='job:source-isolated:'+hashlib.sha256(canonical.encode()).hexdigest()+':'+uuid4().hex
                        if not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(actual,)).fetchone():break
                    else:raise RuntimeError('Unable to allocate source group identity')
        if current is not None:
            current.pop('_source_codes',None)
            legacy_ids=current.pop('_legacy_ids',set())
            moved_legacy: str | None=None
            active_legacy: set[str]=set()
            if row is None and len(legacy_ids)==1 and (actual!=canonical or occupied is None):
                legacy=next(iter(legacy_ids))
                previous=store.db.execute('SELECT id,body FROM reader_problems WHERE identity=%s AND finished IS NULL',(legacy,)).fetchone()
                legacy_current=project(store,legacy,kind,now) if previous and manageable(legacy,kind) else None
                if legacy_current is not None:active_legacy.add(legacy)
                if previous and manageable(legacy,kind) and legacy_current is None:
                    try:previous_body=json.loads(previous['body'])
                    except (TypeError,ValueError):previous_body={}
                    allowed={(o.get('kind'),o.get('id')) for o in current['objects']}
                    objects=previous_body.get('objects') if isinstance(previous_body,dict) else None
                    valid=isinstance(previous_body,dict) and previous_body.get('code')==current['code'] and previous_body.get('operation') in (None,'producer:status','producer:'+kind) and all(isinstance(previous_body.get(field),str) for field in ('title','section','consequence','next_step')) and previous_body.get('state') in ('current','retrying')
                    if valid and isinstance(objects,list) and bool(objects) and all(isinstance(obj,dict) and (obj.get('kind'),obj.get('id')) in allowed for obj in objects):
                        store.db.execute('UPDATE reader_problems SET identity=%s WHERE id=%s AND finished IS NULL',(actual,previous['id']))
                        moved_legacy=legacy
            remaining=sorted(legacy_ids-({moved_legacy} if moved_legacy else set())-active_legacy)
            for start in range(0,len(remaining),400):
                legacy_rows=store.db.execute('SELECT id,identity,body FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(remaining[start:start+400],)).fetchall()
                protected_rows: list[tuple[str,str]]=[]
                for legacy_row in legacy_rows:
                    try:legacy_body=json.loads(legacy_row['body'])
                    except (TypeError,ValueError):continue
                    if not isinstance(legacy_body,dict) or isinstance(legacy_body.get('source_group_identity'),str) or not isinstance(legacy_body.get('code'),str) or not legacy_body['code'].startswith('source-') or legacy_body.get('operation') not in (None,'producer:status','producer:'+kind):continue
                    identity=legacy_row['identity']
                    protected_rows.append(('source-legacy-protected:'+hashlib.sha256(identity.encode()).hexdigest(),json.dumps(['oko-source-legacy-v1',identity,legacy_row['id']])))
                if protected_rows:
                    if len(dict(protected_rows))!=len(protected_rows):raise RuntimeError('Source legacy identity collision')
                    store.db.execute("INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) batch(key,value) ON CONFLICT DO NOTHING",([p[0] for p in protected_rows],[p[1] for p in protected_rows]))
                    saved=store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([p[0] for p in protected_rows],)).fetchall()
                    expected=dict(protected_rows)
                    for marker in saved:
                        wanted=expected[marker['key']]
                        if marker['value']==wanted:continue
                        try:old_value=json.loads(marker['value']);new_value=json.loads(wanted)
                        except (TypeError,ValueError):raise RuntimeError('Source legacy identity collision')
                        if not isinstance(old_value,list) or len(old_value)!=3 or not all(isinstance(v,str) for v in old_value) or old_value[:2]!=new_value[:2]:
                            raise RuntimeError('Source legacy identity collision')
                        changed=store.db.execute("""UPDATE state SET value=%s WHERE key=%s AND value=%s AND NOT EXISTS
                            (SELECT 1 FROM reader_problems WHERE id=%s AND identity=%s AND finished IS NULL) RETURNING key""",(wanted,marker['key'],marker['value'],old_value[2],old_value[1])).fetchone()
                        if changed is None:raise RuntimeError('Source legacy identity collision')
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
            if still is not None:allowed.update((obj['kind'],obj['id']) for obj in still['objects'])
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



def recover(store: 'Store') -> None:
    """One-time resumable recovery, deduplicating groups in bounded batches."""
    import hashlib
    import json
    from uuid import UUID,uuid4
    from oko.storage import memberships
    marker=store.db.execute("SELECT value FROM state WHERE key='source-problems-recovered-v1'").fetchone()
    if marker and marker['value']=='true':return
    if marker:
        phase,separator,token=marker['value'].partition(':')
        if separator!=':' or phase not in ('running','done'):
            raise RuntimeError('Invalid source recovery marker')
        try:UUID(token)
        except ValueError as error:raise RuntimeError('Invalid source recovery token') from error
    else:
        phase='running';token=uuid4().hex
        with store.db:store.db.execute("INSERT INTO state(key,value) VALUES ('source-problems-recovered-v1',%s)",('running:'+token,))
    prefix='source-recovery-seen:'+token+':'
    def cleanup() -> None:
        while True:
            with store.db:
                keys=[r[0] for r in store.db.execute("SELECT key FROM state WHERE key LIKE 'source-recovery-seen:%%' AND key LIKE %s ORDER BY key LIMIT 400",(prefix+'%',))]
                if not keys:break
                store.db.execute('DELETE FROM state WHERE key=ANY(%s)',(keys,))
        with store.db:store.db.execute("UPDATE state SET value='true' WHERE key='source-problems-recovered-v1' AND value=%s",('done:'+token,))
    if phase=='done':
        cleanup()
        return
    cursor: int | None=None
    while True:
        with store.db:
            memberships.lock_graph(store.db)
            query="""SELECT id FROM jobs WHERE kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code LIKE 'source-%%' AND source_event_owned=false"""
            if cursor is not None:query+=' AND id>%s'
            jobs=store.db.execute(query+' ORDER BY id LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
            if not jobs:break
            ids=[row['id'] for row in jobs]
            groups=job_keys_many(store,ids)
            pairs=sorted({(kind,key) for kind,keys in groups.values() for key in keys})
            status: dict[tuple[str,str],str]={}
            if pairs:
                markers=[(prefix+hashlib.sha256((kind+'\0'+key).encode()).hexdigest(),json.dumps([kind,key,'pending'])) for kind,key in pairs]
                store.db.execute("""INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) AS batch(key,value)
                    ON CONFLICT DO NOTHING""",([p[0] for p in markers],[p[1] for p in markers]))
                existing=store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([p[0] for p in markers],)).fetchall()
                expected={marker:(kind,key) for marker,(kind,key) in zip((p[0] for p in markers),pairs)}
                updates: list[tuple[str,str]]=[]
                for row in existing:
                    try:value=json.loads(row['value'])
                    except (TypeError,ValueError):raise RuntimeError('Invalid source recovery marker')
                    pair=expected[row['key']]
                    if not isinstance(value,list) or len(value)!=3 or value[:2]!=list(pair) or value[2] not in ('pending','done','blocked'):
                        raise RuntimeError('Source recovery marker collision')
                    state=value[2]
                    if state=='pending':
                        state='done' if sync(store,pair[0],{pair[1]},store.wall_clock()) else 'blocked'
                        updates.append((row['key'],json.dumps([*pair,state])))
                    status[pair]=state
                if updates:
                    store.db.execute('UPDATE state s SET value=batch.value FROM unnest(%s::text[],%s::text[]) AS batch(key,value) WHERE s.key=batch.key',([r[0] for r in updates],[r[1] for r in updates]))
            eligible=[job_id for job_id,(kind,keys) in groups.items() if all(status.get((kind,key))=='done' for key in keys)]
            if eligible:store.db.execute("UPDATE jobs SET source_event_owned=(status IN ('pending','running','failed','unavailable','blocked') OR (kind='fetch' AND status='done' AND error IS NOT NULL)) WHERE id=ANY(%s) AND problem_code LIKE 'source-%%'",(eligible,))
            notify_admission(store,eligible)
            cursor=ids[-1]
        store.fault('source_problem_recovery_batch')
    with store.db:
        store.db.execute("UPDATE state SET value=%s WHERE key='source-problems-recovered-v1' AND value=%s",('done:'+token,'running:'+token))
    cleanup()


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
    failed={(kind,key) for kind,keys in grouped.items() for key in keys if not sync(store,kind,{key},now,evidence=evidence)}
    eligible=[job_id for job_id,(kind,keys) in all_groups.items() if all((kind,key) not in failed for key in keys)]
    eligible_set=set(eligible)
    ineligible=[job_id for job_id in all_groups if job_id not in eligible_set]
    for start in range(0,len(eligible),400):
        store.db.execute("UPDATE jobs SET source_event_owned=(status IN ('pending','running','failed','unavailable','blocked') OR (kind='fetch' AND status='done' AND error IS NOT NULL)) WHERE id=ANY(%s) AND problem_code LIKE 'source-%%'",(eligible[start:start+400],))
    for start in range(0,len(ineligible),400):
        store.db.execute("UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned",(ineligible[start:start+400],))
    notify_admission(store,eligible+ineligible)


def previous_keys_many(store: 'Store', rows: list[tuple[int,str]]) -> dict[int,tuple[str,set[str]]]:
    if not rows:return {}
    result: dict[int,tuple[str,set[str]]]={}
    for start in range(0,len(rows),400):
        batch=rows[start:start+400]
        found=store.db.execute(f"""SELECT j.id,j.kind,j.item,v.code AS problem_code,j.feed_id,
            coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('root')},j.feed_id) AS owner
            FROM unnest(%s::bigint[],%s::text[]) AS v(id,code) JOIN jobs j ON j.id=v.id
            LEFT JOIN publications items ON items.id=j.item LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
            LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
            LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
            LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id AND viewed=0 OFFSET 0) root ON true""",([r[0] for r in batch],[r[1] for r in batch])).fetchall()
        for row in found:
            if row['id'] not in result:result[row['id']]=(row['kind'],set())
            result[row['id']][1].add('job:'+row['problem_code']+':'+(row['owner'] or row['feed_id'] or row['item'] or 'demo')+':'+row['kind'])
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
    failed={(kind,key) for kind,keys in grouped.items() for key in keys if not sync(store,kind,{key},now,evidence=evidence)}
    eligible=[job_id for job_id,(kind,keys) in current.items() if all((kind,key) not in failed for key in keys)]
    eligible_set=set(eligible)
    ineligible=[job_id for job_id in ids if job_id not in eligible_set]
    for start in range(0,len(eligible),400):
        store.db.execute("UPDATE jobs SET source_event_owned=(status IN ('pending','running','failed','unavailable','blocked') OR (kind='fetch' AND status='done' AND error IS NOT NULL)) WHERE id=ANY(%s) AND problem_code LIKE 'source-%%'",(eligible[start:start+400],))
    for start in range(0,len(ineligible),400):
        store.db.execute("UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned",(ineligible[start:start+400],))
    notify_admission(store,eligible+ineligible)


def cleanup_legacy_protected(store: 'Store', cursor: str | None) -> tuple[str | None,bool]:
    """Remove at most one page of stale markers, leaving foreign values untouched."""
    import hashlib
    import json
    query="SELECT key,value FROM state WHERE key LIKE 'source-legacy-protected:%%'"
    if cursor is not None:query+=' AND key COLLATE "C">%s'
    markers=store.db.execute(query+' ORDER BY key COLLATE "C" LIMIT 100',(cursor,) if cursor is not None else ()).fetchall()
    if not markers:return None,True
    parsed=[]
    for marker in markers:
        try:value=json.loads(marker['value'])
        except (TypeError,ValueError):continue
        if not isinstance(value,list) or len(value)!=3 or value[0]!='oko-source-legacy-v1' or not all(isinstance(v,str) for v in value):continue
        if marker['key']!='source-legacy-protected:'+hashlib.sha256(value[1].encode()).hexdigest():continue
        parsed.append((marker['key'],marker['value'],value[1],value[2]))
    if parsed:
        rows=store.db.execute('SELECT id,identity,finished FROM reader_problems WHERE id=ANY(%s)',([row[3] for row in parsed],)).fetchall()
        records={row['id']:row for row in rows}
        stale=[(key,value) for key,value,identity,record_id in parsed if record_id not in records or records[record_id]['finished'] is not None or records[record_id]['identity']!=identity]
        if stale:
            store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) AS v(key,value) WHERE s.key=v.key AND s.value=v.value',([r[0] for r in stale],[r[1] for r in stale]))
    return markers[-1]['key'],False


def job_evidence_many(store: 'Store', rows: Sequence[tuple[int,str | None]]) -> dict[str,set[tuple[str,str]]]:
    """Capture the actual objects attributable to Jobs under the graph lock."""
    evidence: dict[str,set[tuple[str,str]]]={}
    for start in range(0,len(rows),400):
        batch=rows[start:start+400]
        found=store.db.execute(f"""SELECT j.id,j.kind,j.item,coalesce(v.code,j.problem_code) AS code,j.feed_id,
            coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('root')},j.feed_id) AS owner,
            coalesce(root.id,m.publication_id,j.item) AS affected
            FROM unnest(%s::bigint[],%s::text[]) AS v(id,code) JOIN jobs j ON j.id=v.id
            LEFT JOIN publications items ON items.id=j.item LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
            LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
            LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
            LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id AND viewed=0 OFFSET 0) root ON true
            WHERE j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND coalesce(v.code,j.problem_code) LIKE 'source-%%'""",([r[0] for r in batch],[r[1] for r in batch])).fetchall()
        for row in found:
            owner=row['owner'] or row['feed_id'] or row['item'] or 'demo'
            key='job:'+row['code']+':'+owner+':'+row['kind']
            objects=evidence.setdefault(key,set())
            objects.add(('code',row['code']))
            value=owner if row['kind']=='feed-delete' else row['affected']
            if row['kind']!='delete' and isinstance(value,str):objects.add(('feed' if row['kind']=='feed-delete' else 'material',value))
    return evidence


def job_evidence_with_previous_feed(store: 'Store', job_id: int, feed: str | None) -> dict[str,set[tuple[str,str]]]:
    rows=store.db.execute(f"""SELECT j.kind,j.item,j.problem_code,
        coalesce({owner_expr('items')},{owner_expr('media_owner')},{owner_expr('root')},%s::text) AS owner,
        coalesce(root.id,m.publication_id,j.item) AS affected
        FROM jobs j LEFT JOIN publications items ON items.id=j.item
        LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
        LEFT JOIN publications media_owner ON media_owner.id=m.publication_id
        LEFT JOIN publication_links l ON l.target_publication_id=coalesce(items.id,media_owner.id)
        LEFT JOIN LATERAL (SELECT * FROM publications WHERE id=l.source_publication_id AND viewed=0 OFFSET 0) root ON true
        WHERE j.id=%s AND j.problem_code LIKE 'source-%%' AND j.kind IN ('fetch','download_media','filter','theses','delete','feed-delete')""",(feed,job_id)).fetchall()
    evidence: dict[str,set[tuple[str,str]]]={}
    for row in rows:
        owner=row['owner'] or feed or row['item'] or 'demo'
        key='job:'+row['problem_code']+':'+owner+':'+row['kind']
        objects=evidence.setdefault(key,set())
        objects.add(('code',row['problem_code']))
        value=owner if row['kind']=='feed-delete' else row['affected']
        if row['kind']!='delete' and isinstance(value,str):objects.add(('feed' if row['kind']=='feed-delete' else 'material',value))
    return evidence
