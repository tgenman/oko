"""Shared timer coordination for future YouTube problem occurrences."""
from typing import TYPE_CHECKING,Any
import json
from oko.storage.reader_status import changed as status_changed
from oko.storage.reservation_ids import allocate

if TYPE_CHECKING:
    from oko.storage.store import Store


def sync_timer(store: 'Store', now: float, job_ids: list[int] | None, new_ids: set[str] | None, previous: list[dict[str,Any]] | None, *, subtitle: bool) -> None:
    from oko.storage.problems import _readable_timestamp
    from oko.storage import subtitle_problems, youtube_fetch_problems
    from oko.storage.media_job_problems import managed_candidates_many as media_candidates
    from oko.storage.fetch_job_problems import managed_candidates_many as fetch_candidates
    ops = subtitle_problems if subtitle else youtube_fetch_problems
    if subtitle:
        kind,owned,due,reserved='download_media','subtitle_event_owned','subtitle_due','subtitle_reserved_owned'
        prefix,version='subtitle:reservation:','oko-subtitle-v1'
        job_field,canonical_field,flag,identity_field='media_job_id','subtitle_canonical','subtitle_reservation','media_job_identity'
        label='Subtitle'
    else:
        kind,owned,due,reserved='fetch','fetch_youtube_sensitive','youtube_fetch_due','youtube_fetch_reserved_owned'
        prefix,version='youtube-fetch:reservation:','oko-youtube-fetch-v1'
        job_field,canonical_field,flag,identity_field='fetch_job_id','fetch_youtube_canonical','fetch_youtube_reservation','fetch_job_identity'
        label='YouTube fetch'
    operation='producer:'+kind
    excluded=('id','created_at','finished_at','outcome') + (() if subtitle else ('_fetch_item',))
    limits=store.youtube._limits()
    def safe_limit(value: Any) -> float:
        return float(value) if _readable_timestamp(value) else float('inf')
    blocked=safe_limit(limits['blocked_until']);next_at=safe_limit(limits['next_at'])
    previous_by_id={candidate['id']:candidate for candidate in (previous or [])}
    cursor: int | None=None
    while True:
        query=f"SELECT id,reason_code FROM jobs WHERE kind='{kind}' AND {owned}"
        params: list[Any]=[]
        if job_ids is not None:query+=' AND id=ANY(%s)';params.append(job_ids)
        if cursor is not None:query+=' AND id>%s';params.append(cursor)
        jobs=store.db.execute(query+' ORDER BY id LIMIT 100',tuple(params)).fetchall()
        if not jobs:break
        ids=[row['id'] for row in jobs]
        projected=media_candidates(store,ids,now,include_subtitles=True) if subtitle else fetch_candidates(store,ids,now)
        if not subtitle:projected={job:[candidate for candidate in candidates if candidate.get('fetch_youtube_event')] for job,candidates in projected.items()}
        keys=sorted({candidate['id'] for candidates in projected.values() for candidate in candidates})
        by_key={}
        isolated: dict[str,list[Any]]={}
        for start in range(0,len(keys),100):
            chunk=keys[start:start+100]
            by_key.update({row['identity']:row for row in store.db.execute('SELECT * FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(chunk,))})
            for row in store.db.execute(f"""SELECT r.*,t.canonical FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
                SELECT * FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='{operation}'
                AND body::jsonb->>'{identity_field}'=t.canonical LIMIT 101) r""",(chunk,)):
                isolated.setdefault(row['canonical'],[]).append(row)
        reservations: dict[tuple[str,str],list[Any]]={}
        pairs=[(str(job),candidate['id']) for job,parts in projected.items() for candidate in parts]
        for start in range(0,len(pairs),100):
            chunk=pairs[start:start+100]
            for row in store.db.execute(f"""SELECT r.*,t.marker,t.canonical FROM unnest(%s::text[],%s::text[]) t(marker,canonical) CROSS JOIN LATERAL (
                SELECT * FROM reader_problems WHERE {due} IS NOT NULL AND {reserved} AND identity LIKE '{prefix}%%'
                AND md5(body::jsonb->>'{job_field}')=md5(t.marker) AND body::jsonb->>'{job_field}'=t.marker
                AND md5(body::jsonb->>'{canonical_field}')=md5(t.canonical) AND body::jsonb->>'{canonical_field}'=t.canonical LIMIT 2) r""",([pair[0] for pair in chunk],[pair[1] for pair in chunk])):
                reservations.setdefault((row['marker'],row['canonical']),[]).append(row)
        reservation_keys=[row['identity'] for group in reservations.values() for row in group]
        pointers={}
        for start in range(0,len(reservation_keys),400):
            pointers.update({row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(reservation_keys[start:start+400],))})
        closes=[];rekeys=[];inserts=[];updates=[];removes=[];ambiguous_jobs:set[int]=set()
        for job in jobs:
            candidates=projected.get(job['id'],[])
            deadline=max(blocked,next_at if job['reason_code'] is None else 0)
            for candidate in candidates:
                canonical=candidate['id']
                saved_rows=reservations.get((str(job['id']),canonical),[])
                if len(saved_rows)>1:
                    ambiguous_jobs.add(job['id']);continue
                matching=[]
                for saved in saved_rows:
                    body=ops.valid(saved)
                    if body and body[canonical_field]==canonical:
                        proof=previous_by_id.get(canonical,candidate)
                        if saved['identity'] in pointers and ops.matches_snapshot(store,saved,proof,pointers[saved['identity']]):matching.append(saved)
                        else:ambiguous_jobs.add(job['id'])
                if len(matching)>1:
                    ambiguous_jobs.add(job['id']);continue
                future={key:value for key,value in candidate.items() if key not in excluded}
                future.update({flag:True,canonical_field:canonical})
                encoded=json.dumps(future)
                if matching:
                    saved=matching[0];new_due=max(now,deadline)
                    if saved[due]!=new_due or saved['body']!=encoded:updates.append((job['id'],(saved['id'],new_due,encoded,saved['identity'],saved['body'],saved[due],pointers[saved['identity']])))
                    if new_ids:
                        direct=by_key.get(canonical)
                        if direct and direct['id'] in new_ids:removes.append((job['id'],(direct['id'],direct['identity'],direct['body'])))
                    continue
                if len(isolated.get(canonical,[]))>100:
                    ops.protect_isolated(store,identity_field,canonical)
                    ambiguous_jobs.add(job['id']);continue
                matches=[row for row in isolated.get(canonical,[]) if ops.matches_active_snapshot(store,row,candidate)]
                direct=by_key.get(canonical)
                if direct and ops.matches_active_snapshot(store,direct,candidate):matches.append(direct)
                if len(matches)!=1:
                    unknown={row['id']:row for row in isolated.get(canonical,[]) if not ops.matches_active_snapshot(store,row,candidate)}
                    if direct and not ops.matches_active_snapshot(store,direct,candidate):unknown[direct['id']]=direct
                    if unknown:ops.protect_rows(store,list(unknown.values()))
                    ambiguous_jobs.add(job['id'])
                    continue
                if deadline<=now:continue
                current=matches[0]
                if new_ids and current['id'] in new_ids:
                    identity=current['id']
                    rekeys.append((job['id'],(identity,prefix+identity,encoded,deadline,current['identity'],current['body'])))
                else:
                    closes.append((job['id'],(current['id'],current['identity'],current['body'])))
                    identity=''  # Allocated in one checked batch below.
                    inserts.append((job['id'],(identity,prefix+identity,encoded,now,-62135596800,deadline)))
        if ambiguous_jobs and store.db.execute("UPDATE jobs SET subtitle_event_owned=false WHERE id=ANY(%s) AND subtitle_event_owned" if subtitle else "UPDATE jobs SET fetch_youtube_sensitive=false,fetch_null_owned=false,fetch_null_ambiguous=true WHERE id=ANY(%s) AND fetch_youtube_sensitive",(sorted(ambiguous_jobs),)).rowcount:status_changed(store.db)
        actual_closes=[value for job,value in closes if job not in ambiguous_jobs]
        actual_rekeys=[value for job,value in rekeys if job not in ambiguous_jobs]
        actual_inserts=[value for job,value in inserts if job not in ambiguous_jobs]
        actual_updates=[value for job,value in updates if job not in ambiguous_jobs]
        actual_removes=[value for job,value in removes if job not in ambiguous_jobs]
        # Newly created active rows have not been observable outside this
        # transaction; replace them with safely allocated reservation IDs.
        reservation_ids=allocate(store,len(actual_rekeys)+len(actual_inserts),prefix,ops.new_reservation_id)
        for staged,identity in zip(actual_rekeys,reservation_ids):
            actual_removes.append((staged[0],staged[4],staged[5]))
            actual_inserts.append((identity,prefix+identity,staged[2],now,-62135596800,staged[3]))
        offset=len(actual_rekeys)
        for index in range(offset,len(reservation_ids)):
            staged=actual_inserts[index-offset]
            identity=reservation_ids[index]
            actual_inserts[index-offset]=(identity,prefix+identity,*staged[2:])
        pending_pointers=[]
        for staged in actual_inserts:
            body=json.loads(str(staged[2]));pending_pointers.append((staged[1],json.dumps([version,body[job_field],body[canonical_field]])))
        for start in range(0,len(pending_pointers),400):
            chunk=pending_pointers[start:start+400]
            inserted=store.db.execute('INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) v(key,value) ON CONFLICT(key) DO NOTHING',([p[0] for p in chunk],[p[1] for p in chunk])).rowcount
            if inserted!=len(chunk):raise RuntimeError(f'{label} reservation pointer is occupied')
        # Lock exact reverse pointers before updating reservations; a concurrent
        # corruption must abort rather than overwrite somebody else's state.
        for start in range(0,len(actual_updates),400):
            chunk_updates=actual_updates[start:start+400]
            locked={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s) FOR UPDATE',([row[3] for row in chunk_updates],))}
            if any(locked.get(row[3])!=row[6] for row in chunk_updates):raise RuntimeError(f'{label} reservation pointer changed')
        changed=0
        for start in range(0,len(actual_closes),400):
            chunk_closes=actual_closes[start:start+400]
            count=store.db.execute("""UPDATE reader_problems p SET finished=%s,outcome='resolved' FROM unnest(%s::text[],%s::text[],%s::text[]) v(id,identity,body)
                WHERE p.id=v.id AND p.identity=v.identity AND p.body=v.body AND p.finished IS NULL""",(now,*tuple([row[i] for row in chunk_closes] for i in range(3)))).rowcount
            if count!=len(chunk_closes):raise RuntimeError(f'{label} occurrence changed during pause')
            changed+=count
        for start in range(0,len(actual_inserts),400):
            chunk_inserts=actual_inserts[start:start+400]
            changed+=store.db.execute(f"""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,{due},{reserved})
                SELECT id,identity,'background',body,created,finished,'irrelevant',due,true FROM unnest(%s::text[],%s::text[],%s::text[],%s::double precision[],%s::double precision[],%s::double precision[]) v(id,identity,body,created,finished,due)""",tuple([row[i] for row in chunk_inserts] for i in range(6))).rowcount
        for start in range(0,len(actual_updates),400):
            chunk_updates=actual_updates[start:start+400]
            count=store.db.execute(f"""UPDATE reader_problems p SET {due}=v.due,body=v.body
                FROM unnest(%s::text[],%s::double precision[],%s::text[],%s::text[],%s::text[],%s::double precision[]) v(id,due,body,old_identity,old_body,old_due)
                WHERE p.id=v.id AND p.identity=v.old_identity AND p.body=v.old_body AND p.{due}=v.old_due AND p.{reserved}""",tuple([row[i] for row in chunk_updates] for i in range(6))).rowcount
            if count!=len(chunk_updates):raise RuntimeError(f'{label} reservation changed during retiming')
            changed+=count
        for start in range(0,len(actual_removes),400):
            chunk_removes=actual_removes[start:start+400]
            count=store.db.execute("""DELETE FROM reader_problems p USING unnest(%s::text[],%s::text[],%s::text[]) v(id,identity,body)
                WHERE p.id=v.id AND p.identity=v.identity AND p.body=v.body AND p.finished IS NULL""",tuple([row[i] for row in chunk_removes] for i in range(3))).rowcount
            if count!=len(chunk_removes):raise RuntimeError(f'{label} duplicate changed during reservation')
            changed+=count
        if changed:status_changed(store.db)
        cursor=jobs[-1]['id']
    if job_ids is None:ops.drain_due(store,now)
    else:
        due_cursor=None
        while True:
            due_cursor=ops.activate_due(store,now,job_id=job_ids,cursor=due_cursor)
            if due_cursor is None:break


def activate_due(store: 'Store', now: float, job_id: int | list[int] | None = None, cursor: tuple[float,str] | None = None, *, subtitle: bool) -> tuple[float,str] | None:
    from oko.storage import subtitle_problems, youtube_fetch_problems
    ops=subtitle_problems if subtitle else youtube_fetch_problems
    due,reserved=('subtitle_due','subtitle_reserved_owned') if subtitle else ('youtube_fetch_due','youtube_fetch_reserved_owned')
    prefix,version=('subtitle:reservation:','oko-subtitle-v1') if subtitle else ('youtube-fetch:reservation:','oko-youtube-fetch-v1')
    job_field,canonical_field,identity_field=('media_job_id','subtitle_canonical','media_job_identity') if subtitle else ('fetch_job_id','fetch_youtube_canonical','fetch_job_identity')
    label='Subtitle' if subtitle else 'YouTube fetch'
    import hashlib
    isolated_prefix='job:media-isolated:' if subtitle else 'job:fetch-isolated:'
    excluded=('id','created_at','finished_at','outcome') + (() if subtitle else ('_fetch_item',))
    if job_id is None:
        query=f"SELECT * FROM reader_problems WHERE {due} IS NOT NULL AND {reserved} AND {due}<=%s"
        params: tuple[Any,...]=(now,)
        if cursor is not None:
            query+=f' AND ({due},id COLLATE "C")>(%s,%s)';params=(*params,*cursor)
        query+=f' ORDER BY {due},id COLLATE "C" LIMIT 100'
    else:
        markers=[str(item) for item in (job_id if isinstance(job_id,list) else [job_id])]
        query=f"""SELECT r.* FROM unnest(%s::text[]) t(marker) CROSS JOIN LATERAL (
            SELECT * FROM reader_problems WHERE {due} IS NOT NULL AND {reserved} AND {due}<=%s
            AND identity LIKE '{prefix}%%' AND md5(body::jsonb->>'{job_field}')=md5(t.marker)
            AND body::jsonb->>'{job_field}'=t.marker"""
        params=(markers,now)
        if cursor is not None:
            query+=f' AND ({due},id COLLATE "C")>(%s,%s)';params=(*params,*cursor)
        query+=f' ORDER BY {due},id COLLATE "C" LIMIT 100) r ORDER BY r.{due},r.id COLLATE "C" LIMIT 100'
    rows=store.db.execute(query,params).fetchall()
    next_cursor=(float(rows[-1][due]),rows[-1]['id']) if len(rows)==100 else None
    candidates_by_key: dict[tuple[int,str],dict[str,Any]]={}
    proven=ops.verified(store,list(rows),now,projected_out=candidates_by_key)
    selected: list[tuple[Any,dict[str,Any]]]=[]
    for row in rows:
        body=proven.get(row['id'])
        if not body:continue
        candidate=candidates_by_key.get((int(body[job_field]),body[canonical_field]))
        if candidate:selected.append((row,candidate))
    keys=[candidate['id'] for _,candidate in selected]
    if not keys:return next_cursor
    occupied_rows={row['identity']:row for row in store.db.execute('SELECT id,identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(keys,))}
    occupied=set(occupied_rows)
    if occupied_rows:ops.protect_rows(store,list(occupied_rows.values()))
    counts={key:keys.count(key) for key in set(keys)}
    conflicts={row['id']:(row,candidate) for row,candidate in selected if candidate['id'] in occupied and counts[candidate['id']]==1}
    alternatives: dict[str,str]={}
    for _ in range(3):
        unresolved={identity:pair for identity,pair in conflicts.items() if identity not in alternatives}
        if not unresolved:break
        proposals={identity:isolated_prefix+hashlib.sha256(candidate['id'].encode()).hexdigest()+':'+ops.new_reservation_id() for identity,(_,candidate) in unresolved.items()}
        used=set(keys) | {row['identity'] for row in store.db.execute('SELECT identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(proposals.values()),))}
        frequency: dict[str,int]={}
        for value in proposals.values():frequency[value]=frequency.get(value,0)+1
        for identity,value in proposals.items():
            if value not in used and frequency[value]==1:alternatives[identity]=value
    if len(alternatives)!=len(conflicts):raise RuntimeError('Unable to allocate isolated '+('subtitle' if subtitle else 'YouTube fetch')+' identity')
    updates=[]
    for row,candidate in selected:
        if counts[candidate['id']]!=1:continue
        actual=alternatives.get(row['id'],candidate['id'])
        future={key:value for key,value in candidate.items() if key not in excluded}
        if actual!=candidate['id']:future[identity_field]=candidate['id']
        pointer=json.dumps([version,candidate[job_field],candidate['id']])
        updates.append((row['id'],actual,json.dumps(future),row['body'],row[due],row['identity'],pointer))
    if updates:
        changed=store.db.execute(f"""UPDATE reader_problems p SET identity=v.identity,body=v.body,created=p.{due},finished=NULL,outcome=NULL,{due}=NULL,{reserved}=false
            FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[],%s::text[]) v(id,identity,body,old_body,old_due,old_identity)
            WHERE p.id=v.id AND p.identity=v.old_identity AND p.body=v.old_body AND p.{due}=v.old_due AND p.{reserved}""",tuple([r[i] for r in updates] for i in range(6))).rowcount
        if changed!=len(updates):raise RuntimeError(f'{label} reservation changed during activation')
        deleted=store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) v(key,value) WHERE s.key=v.key AND s.value=v.value',([row[5] for row in updates],[row[6] for row in updates])).rowcount
        if deleted!=len(updates):raise RuntimeError(f'{label} reservation pointer changed during activation')
        # The deadline projection was already visible with this ID and body.
        # Materializing it must not invalidate an otherwise unchanged ETag.
    return next_cursor


def cancel_reservations(store: 'Store', job_id: int, previous: list[dict[str,Any]], *, subtitle: bool) -> None:
    from oko.storage import subtitle_problems, youtube_fetch_problems
    ops=subtitle_problems if subtitle else youtube_fetch_problems
    due,reserved=('subtitle_due','subtitle_reserved_owned') if subtitle else ('youtube_fetch_due','youtube_fetch_reserved_owned')
    prefix='subtitle:reservation:' if subtitle else 'youtube-fetch:reservation:'
    job_field,canonical_field=('media_job_id','subtitle_canonical') if subtitle else ('fetch_job_id','fetch_youtube_canonical')
    label='Subtitle' if subtitle else 'YouTube fetch'
    removed=[]
    for start in range(0,len(previous),100):
        batch=previous[start:start+100]
        canonical=[candidate['id'] for candidate in batch]
        rows=store.db.execute(f"""SELECT r.*,t.canonical FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
            SELECT * FROM reader_problems WHERE {due} IS NOT NULL AND {reserved} AND identity LIKE '{prefix}%%'
            AND md5(body::jsonb->>'{job_field}')=md5(%s) AND body::jsonb->>'{job_field}'=%s
            AND md5(body::jsonb->>'{canonical_field}')=md5(t.canonical) AND body::jsonb->>'{canonical_field}'=t.canonical LIMIT 2) r""",(canonical,str(job_id),str(job_id))).fetchall()
        counts: dict[str,int]={}
        for row in rows:counts[row['canonical']]=counts.get(row['canonical'],0)+1
        pointers={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([row['identity'] for row in rows],))} if rows else {}
        for row in rows:
            if counts[row['canonical']]!=1 or row['identity'] not in pointers:continue
            candidate=next((c for c in batch if c['id']==row['canonical']),None)
            if candidate and ops.matches_snapshot(store,row,candidate,pointers[row['identity']]):removed.append((row['id'],row['identity'],row['body'],row[due],pointers[row['identity']]))
    if removed:
        deleted_rows=store.db.execute(f"""DELETE FROM reader_problems p USING unnest(%s::text[],%s::text[],%s::text[],%s::double precision[]) v(id,identity,body,due)
            WHERE p.id=v.id AND p.identity=v.identity AND p.body=v.body AND p.{due}=v.due AND p.{reserved}""",tuple([row[i] for row in removed] for i in range(4))).rowcount
        if deleted_rows!=len(removed):raise RuntimeError(f'{label} reservation changed during cancellation')
        deleted=store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) v(key,value) WHERE s.key=v.key AND s.value=v.value',([row[1] for row in removed],[row[4] for row in removed])).rowcount
        if deleted!=len(removed):raise RuntimeError(f'{label} reservation pointer changed during cancellation')
        status_changed(store.db)


def verified(store: 'Store', rows: list[Any], now: float, *, projected_out: dict[tuple[int,str],dict[str,Any]] | None = None, subtitle: bool) -> dict[str,dict[str,Any]]:
    from oko.storage import subtitle_problems, youtube_fetch_problems
    from oko.storage.media_job_problems import managed_candidates_many as media_candidates
    from oko.storage.fetch_job_problems import managed_candidates_many as fetch_candidates
    ops=subtitle_problems if subtitle else youtube_fetch_problems
    kind,owned_field=('download_media','subtitle_event_owned') if subtitle else ('fetch','fetch_youtube_sensitive')
    job_field,canonical_field,flag=('media_job_id','subtitle_canonical','subtitle_reservation') if subtitle else ('fetch_job_id','fetch_youtube_canonical','fetch_youtube_reservation')
    excluded=('id','created_at','finished_at','outcome') + (() if subtitle else ('_fetch_item',))
    preliminary: dict[str,tuple[Any,dict[str,Any]]]={}
    jobs: set[int]=set()
    for row in rows:
        body=ops.valid(row)
        if body is None:continue
        marker=body[job_field]
        if len(marker)>20 or not marker.lstrip('-').isdigit():continue
        job=int(marker)
        if str(job)!=marker or not -(2**63)<=job<2**63:continue
        preliminary[row['id']]=(row,body);jobs.add(job)
    projected: dict[int,list[dict[str,Any]]]={}
    ordered_jobs=sorted(jobs)
    for start in range(0,len(ordered_jobs),100):
        batch_jobs=ordered_jobs[start:start+100]
        owned=[row['id'] for row in store.db.execute(f"SELECT id FROM jobs WHERE id=ANY(%s) AND kind='{kind}' AND {owned_field}",(batch_jobs,))]
        if owned:
            batch=media_candidates(store,owned,now,include_subtitles=True) if subtitle else fetch_candidates(store,owned,now)
            projected.update(batch if subtitle else {job:[candidate for candidate in parts if candidate.get('fetch_youtube_event')] for job,parts in batch.items()})
    pointer_keys=[row['identity'] for row,_ in preliminary.values()]
    pointers: dict[str,str]={}
    for start in range(0,len(pointer_keys),400):
        pointers.update({row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(pointer_keys[start:start+400],))})
    result: dict[str,dict[str,Any]]={}
    candidates_by_key={(job,candidate['id']):candidate for job,parts in projected.items() for candidate in parts}
    if projected_out is not None:projected_out.update(candidates_by_key)
    for identity,(row,body) in preliminary.items():
        candidate=candidates_by_key.get((int(body[job_field]),body[canonical_field]))
        if not candidate:continue
        desired={key:value for key,value in candidate.items() if key not in excluded}
        desired.update({flag:True,canonical_field:candidate['id']})
        if body==desired and row['identity'] in pointers and ops.matches_snapshot(store,row,candidate,pointers[row['identity']]):result[identity]=body
    return result


def transfer_reservation(store: 'Store', job_id: int, previous: list[dict[str,Any]], current: list[dict[str,Any]], new_ids: set[str], *, subtitle: bool) -> set[str]:
    """Move only a uniquely proven old reservation to one new owner."""
    from oko.storage import subtitle_problems, youtube_fetch_problems
    ops=subtitle_problems if subtitle else youtube_fetch_problems
    due,reserved=('subtitle_due','subtitle_reserved_owned') if subtitle else ('youtube_fetch_due','youtube_fetch_reserved_owned')
    prefix,version=('subtitle:reservation:','oko-subtitle-v1') if subtitle else ('youtube-fetch:reservation:','oko-youtube-fetch-v1')
    job_field,canonical_field,flag=('media_job_id','subtitle_canonical','subtitle_reservation') if subtitle else ('fetch_job_id','fetch_youtube_canonical','fetch_youtube_reservation')
    code,implicit=('work-download_media','media_implicit_code') if subtitle else ('work-fetch','fetch_implicit_code')
    label='Subtitle' if subtitle else 'YouTube fetch'
    excluded=('id','created_at','finished_at','outcome') + (() if subtitle else ('_fetch_item',))
    old_by_id={candidate['id']:candidate for candidate in previous}
    current_by_id={candidate['id']:candidate for candidate in current}
    removed=[candidate for identity,candidate in old_by_id.items() if identity not in current_by_id]
    added=[candidate for identity,candidate in current_by_id.items() if identity not in old_by_id]
    if len(removed)!=1 or len(added)!=1:return set()
    old,new=removed[0],added[0]
    same_cause=(old['code']==new['code'] or
        (old['code']==code and old.get(implicit) is True) or
        (new['code']==code and new.get(implicit) is True))
    if not same_cause or old.get(job_field)!=new.get(job_field):return set()
    rows=store.db.execute(f"""SELECT r.* FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
        SELECT * FROM reader_problems WHERE {due} IS NOT NULL AND {reserved} AND identity LIKE '{prefix}%%'
        AND md5(body::jsonb->>'{job_field}')=md5(%s) AND body::jsonb->>'{job_field}'=%s
        AND md5(body::jsonb->>'{canonical_field}')=md5(t.canonical) AND body::jsonb->>'{canonical_field}'=t.canonical LIMIT 2) r""",([old['id'],new['id']],str(job_id),str(job_id))).fetchall()
    old_matches=[];new_occupied=False
    for row in rows:
        body=ops.valid(row)
        if not body:continue
        if body[canonical_field]==new['id']:new_occupied=True
        if body[canonical_field]!=old['id']:continue
        expected={key:value for key,value in old.items() if key not in excluded}
        expected.update({flag:True,canonical_field:old['id']})
        if body==expected and ops.matches_snapshot(store,row,old):old_matches.append(row)
    if new_occupied or len(old_matches)!=1:return set()
    row=old_matches[0]
    future={key:value for key,value in new.items() if key not in excluded}
    future.update({flag:True,canonical_field:new['id']})
    changed=store.db.execute(f'UPDATE reader_problems SET body=%s WHERE id=%s AND identity=%s AND body=%s AND {due}=%s AND {reserved}',(json.dumps(future),row['id'],row['identity'],row['body'],row[due])).rowcount
    if changed!=1:raise RuntimeError(f'{label} reservation changed during transfer')
    old_value=json.dumps([version,str(job_id),old['id']])
    new_value=json.dumps([version,str(job_id),new['id']])
    if store.db.execute('UPDATE state SET value=%s WHERE key=%s AND value=%s',(new_value,row['identity'],old_value)).rowcount!=1:
        raise RuntimeError(f'{label} reservation pointer changed')
    if new_ids:store.db.execute('DELETE FROM reader_problems WHERE id=ANY(%s) AND identity=%s AND finished IS NULL',(list(new_ids),new['id']))
    status_changed(store.db)
    return {old['id']}
