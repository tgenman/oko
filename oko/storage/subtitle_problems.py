"""Event-owned subtitle episodes, including reserved future occurrences."""
from typing import TYPE_CHECKING,Any
import json,uuid
from oko.storage.reader_status import changed as status_changed

if TYPE_CHECKING:
    from oko.storage.store import Store


def new_reservation_id() -> str:
    return uuid.uuid4().hex


def allocate_reservation_ids(store: 'Store', count: int) -> list[str]:
    """Allocate only free IDs; retry collisions without replacing foreign state."""
    accepted: list[str]=[]
    occupied: set[str]=set()
    for _ in range(3):
        missing=count-len(accepted)
        if missing<=0:break
        proposals=[new_reservation_id() for _ in range(missing)]
        counts: dict[str,int]={}
        for value in proposals:counts[value]=counts.get(value,0)+1
        used=set(occupied)
        for start in range(0,len(proposals),400):
            chunk=proposals[start:start+400]
            used.update(row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE id=ANY(%s)',(chunk,)))
            keys=['subtitle:reservation:'+value for value in chunk]
            used.update(row['key'][len('subtitle:reservation:'):] for row in store.db.execute('SELECT key FROM state WHERE key=ANY(%s)',(keys,)))
        for value in proposals:
            if counts[value]==1 and value not in used:
                accepted.append(value);occupied.add(value)
    if len(accepted)!=count:raise RuntimeError('Unable to allocate subtitle reservation identity')
    return accepted


def sync_timer(store: 'Store', now: float, job_ids: list[int] | None = None, *, new_ids: set[str] | None = None, previous: list[dict[str,Any]] | None = None) -> None:
    from oko.storage.problems import _readable_timestamp
    from oko.storage.media_job_problems import managed_candidates_many
    limits=store.youtube._limits()
    def safe_limit(value: Any) -> float:
        return float(value) if _readable_timestamp(value) else float('inf')
    blocked=safe_limit(limits['blocked_until']);next_at=safe_limit(limits['next_at'])
    previous_by_id={candidate['id']:candidate for candidate in (previous or [])}
    cursor: int | None=None
    while True:
        query="SELECT id,problem_code FROM jobs WHERE kind='download_media' AND subtitle_event_owned"
        params: list[Any]=[]
        if job_ids is not None:query+=' AND id=ANY(%s)';params.append(job_ids)
        if cursor is not None:query+=' AND id>%s';params.append(cursor)
        jobs=store.db.execute(query+' ORDER BY id LIMIT 100',tuple(params)).fetchall()
        if not jobs:break
        ids=[row['id'] for row in jobs]
        projected=managed_candidates_many(store,ids,now,include_subtitles=True)
        keys=sorted({candidate['id'] for candidates in projected.values() for candidate in candidates})
        by_key={}
        isolated: dict[str,list[Any]]={}
        for start in range(0,len(keys),100):
            chunk=keys[start:start+100]
            by_key.update({row['identity']:row for row in store.db.execute('SELECT * FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(chunk,))})
            for row in store.db.execute("""SELECT r.*,t.canonical FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
                SELECT * FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:download_media'
                AND body::jsonb->>'media_job_identity'=t.canonical LIMIT 101) r""",(chunk,)):
                isolated.setdefault(row['canonical'],[]).append(row)
        reservations: dict[tuple[str,str],list[Any]]={}
        pairs=[(str(job),candidate['id']) for job,parts in projected.items() for candidate in parts]
        for start in range(0,len(pairs),100):
            chunk=pairs[start:start+100]
            for row in store.db.execute("""SELECT r.*,t.marker,t.canonical FROM unnest(%s::text[],%s::text[]) t(marker,canonical) CROSS JOIN LATERAL (
                SELECT * FROM reader_problems WHERE subtitle_due IS NOT NULL AND subtitle_reserved_owned AND identity LIKE 'subtitle:reservation:%%'
                AND md5(body::jsonb->>'media_job_id')=md5(t.marker) AND body::jsonb->>'media_job_id'=t.marker
                AND md5(body::jsonb->>'subtitle_canonical')=md5(t.canonical) AND body::jsonb->>'subtitle_canonical'=t.canonical LIMIT 2) r""",([pair[0] for pair in chunk],[pair[1] for pair in chunk])):
                reservations.setdefault((row['marker'],row['canonical']),[]).append(row)
        reservation_keys=[row['identity'] for group in reservations.values() for row in group]
        pointers={}
        for start in range(0,len(reservation_keys),400):
            pointers.update({row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(reservation_keys[start:start+400],))})
        closes=[];rekeys=[];inserts=[];updates=[];removes=[];ambiguous_jobs:set[int]=set()
        for job in jobs:
            candidates=projected.get(job['id'],[])
            deadline=max(blocked,next_at if job['problem_code'] is None else 0)
            for candidate in candidates:
                canonical=candidate['id']
                saved_rows=reservations.get((str(job['id']),canonical),[])
                if len(saved_rows)>1:
                    ambiguous_jobs.add(job['id']);continue
                matching=[]
                for saved in saved_rows:
                    body=valid(saved)
                    if body and body['subtitle_canonical']==canonical:
                        proof=previous_by_id.get(canonical,candidate)
                        if saved['identity'] in pointers and matches_snapshot(store,saved,proof,pointers[saved['identity']]):matching.append(saved)
                        else:ambiguous_jobs.add(job['id'])
                if len(matching)>1:
                    ambiguous_jobs.add(job['id']);continue
                future={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
                future.update(subtitle_reservation=True,subtitle_canonical=canonical)
                encoded=json.dumps(future)
                if matching:
                    saved=matching[0];new_due=max(now,deadline)
                    if saved['subtitle_due']!=new_due or saved['body']!=encoded:updates.append((job['id'],(saved['id'],new_due,encoded,saved['identity'],saved['body'],saved['subtitle_due'],pointers[saved['identity']])))
                    if new_ids:
                        direct=by_key.get(canonical)
                        if direct and direct['id'] in new_ids:removes.append((job['id'],(direct['id'],direct['identity'],direct['body'])))
                    continue
                if len(isolated.get(canonical,[]))>100:
                    protect_isolated(store,'media_job_identity',canonical)
                    ambiguous_jobs.add(job['id']);continue
                matches=[row for row in isolated.get(canonical,[]) if matches_active_snapshot(store,row,candidate)]
                direct=by_key.get(canonical)
                if direct and matches_active_snapshot(store,direct,candidate):matches.append(direct)
                if len(matches)!=1:
                    unknown={row['id']:row for row in isolated.get(canonical,[]) if not matches_active_snapshot(store,row,candidate)}
                    if direct and not matches_active_snapshot(store,direct,candidate):unknown[direct['id']]=direct
                    if unknown:protect_rows(store,list(unknown.values()))
                    ambiguous_jobs.add(job['id'])
                    continue
                if deadline<=now:continue
                current=matches[0]
                if new_ids and current['id'] in new_ids:
                    identity=current['id']
                    rekeys.append((job['id'],(identity,'subtitle:reservation:'+identity,encoded,deadline,current['identity'],current['body'])))
                else:
                    closes.append((job['id'],(current['id'],current['identity'],current['body'])))
                    identity=''  # Allocated in one checked batch below.
                    inserts.append((job['id'],(identity,'subtitle:reservation:'+identity,encoded,now,-62135596800,deadline)))
        if ambiguous_jobs and store.db.execute('UPDATE jobs SET subtitle_event_owned=false WHERE id=ANY(%s) AND subtitle_event_owned',(sorted(ambiguous_jobs),)).rowcount:status_changed(store.db)
        actual_closes=[value for job,value in closes if job not in ambiguous_jobs]
        actual_rekeys=[value for job,value in rekeys if job not in ambiguous_jobs]
        actual_inserts=[value for job,value in inserts if job not in ambiguous_jobs]
        actual_updates=[value for job,value in updates if job not in ambiguous_jobs]
        actual_removes=[value for job,value in removes if job not in ambiguous_jobs]
        # Newly created active rows have not been observable outside this
        # transaction; replace them with safely allocated reservation IDs.
        reservation_ids=allocate_reservation_ids(store,len(actual_rekeys)+len(actual_inserts))
        for staged,identity in zip(actual_rekeys,reservation_ids):
            actual_removes.append((staged[0],staged[4],staged[5]))
            actual_inserts.append((identity,'subtitle:reservation:'+identity,staged[2],now,-62135596800,staged[3]))
        offset=len(actual_rekeys)
        for index in range(offset,len(reservation_ids)):
            staged=actual_inserts[index-offset]
            identity=reservation_ids[index]
            actual_inserts[index-offset]=(identity,'subtitle:reservation:'+identity,*staged[2:])
        actual_rekeys=[]
        pending_pointers=[]
        for staged in actual_inserts:
            body=json.loads(str(staged[2]));pending_pointers.append((staged[1],json.dumps(['oko-subtitle-v1',body['media_job_id'],body['subtitle_canonical']])))
        for start in range(0,len(pending_pointers),400):
            chunk=pending_pointers[start:start+400]
            inserted=store.db.execute('INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) v(key,value) ON CONFLICT(key) DO NOTHING',([p[0] for p in chunk],[p[1] for p in chunk])).rowcount
            if inserted!=len(chunk):raise RuntimeError('Subtitle reservation pointer is occupied')
        # Lock exact reverse pointers before updating reservations; a concurrent
        # corruption must abort rather than overwrite somebody else's state.
        for start in range(0,len(actual_updates),400):
            chunk_updates=actual_updates[start:start+400]
            locked={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s) FOR UPDATE',([row[3] for row in chunk_updates],))}
            if any(locked.get(row[3])!=row[6] for row in chunk_updates):raise RuntimeError('Subtitle reservation pointer changed')
        changed=0
        for start in range(0,len(actual_closes),400):
            chunk_closes=actual_closes[start:start+400]
            count=store.db.execute("""UPDATE reader_problems p SET finished=%s,outcome='resolved' FROM unnest(%s::text[],%s::text[],%s::text[]) v(id,identity,body)
                WHERE p.id=v.id AND p.identity=v.identity AND p.body=v.body AND p.finished IS NULL""",(now,*tuple([row[i] for row in chunk_closes] for i in range(3)))).rowcount
            if count!=len(chunk_closes):raise RuntimeError('Subtitle occurrence changed during pause')
            changed+=count
        for start in range(0,len(actual_rekeys),400):
            chunk_rekeys=actual_rekeys[start:start+400]
            count=store.db.execute("""UPDATE reader_problems p SET identity=v.identity,body=v.body,finished=-62135596800,outcome='irrelevant',subtitle_due=v.due,subtitle_reserved_owned=true
                FROM unnest(%s::text[],%s::text[],%s::text[],%s::double precision[],%s::text[],%s::text[]) v(id,identity,body,due,old_identity,old_body)
                WHERE p.id=v.id AND p.identity=v.old_identity AND p.body=v.old_body AND p.finished IS NULL""",tuple([row[i] for row in chunk_rekeys] for i in range(6))).rowcount
            if count!=len(chunk_rekeys):raise RuntimeError('Subtitle occurrence changed during reservation')
            changed+=count
        for start in range(0,len(actual_inserts),400):
            chunk_inserts=actual_inserts[start:start+400]
            changed+=store.db.execute("""INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,subtitle_due,subtitle_reserved_owned)
                SELECT id,identity,'background',body,created,finished,'irrelevant',due,true FROM unnest(%s::text[],%s::text[],%s::text[],%s::double precision[],%s::double precision[],%s::double precision[]) v(id,identity,body,created,finished,due)""",tuple([row[i] for row in chunk_inserts] for i in range(6))).rowcount
        for start in range(0,len(actual_updates),400):
            chunk_updates=actual_updates[start:start+400]
            count=store.db.execute("""UPDATE reader_problems p SET subtitle_due=v.due,body=v.body
                FROM unnest(%s::text[],%s::double precision[],%s::text[],%s::text[],%s::text[],%s::double precision[]) v(id,due,body,old_identity,old_body,old_due)
                WHERE p.id=v.id AND p.identity=v.old_identity AND p.body=v.old_body AND p.subtitle_due=v.old_due AND p.subtitle_reserved_owned""",tuple([row[i] for row in chunk_updates] for i in range(6))).rowcount
            if count!=len(chunk_updates):raise RuntimeError('Subtitle reservation changed during retiming')
            changed+=count
        for start in range(0,len(actual_removes),400):
            chunk_removes=actual_removes[start:start+400]
            count=store.db.execute("""DELETE FROM reader_problems p USING unnest(%s::text[],%s::text[],%s::text[]) v(id,identity,body)
                WHERE p.id=v.id AND p.identity=v.identity AND p.body=v.body AND p.finished IS NULL""",tuple([row[i] for row in chunk_removes] for i in range(3))).rowcount
            if count!=len(chunk_removes):raise RuntimeError('Subtitle duplicate changed during reservation')
            changed+=count
        if changed:status_changed(store.db)
        cursor=jobs[-1]['id']
    if job_ids is None:drain_due(store,now)
    else:
        due_cursor=None
        while True:
            due_cursor=activate_due(store,now,job_id=job_ids,cursor=due_cursor)
            if due_cursor is None:break


def matches_active_snapshot(store: 'Store', row: Any, candidate: dict[str,Any]) -> bool:
    try:body=json.loads(row['body'])
    except (TypeError,ValueError):return False
    expected={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
    if row['identity']!=candidate['id']:expected['media_job_identity']=candidate['id']
    return body==expected and store.problems.media_record_matches(row,candidate)


def matches_snapshot(store: 'Store', row: Any, candidate: dict[str,Any], pointer: str | None = None) -> bool:
    body=valid(row)
    if not body:return False
    if pointer is None:
        saved=store.db.execute('SELECT value FROM state WHERE key=%s',(row['identity'],)).fetchone()
        pointer=saved['value'] if saved else None
    if pointer!=json.dumps(['oko-subtitle-v1',body['media_job_id'],body['subtitle_canonical']]):return False
    expected={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
    expected.update(subtitle_reservation=True,subtitle_canonical=candidate['id'])
    return body==expected and store.problems.media_record_matches(row,candidate)


def valid(row: Any) -> dict[str,Any] | None:
    import math,re
    from oko.storage.problems import _readable_timestamp
    try:body=json.loads(row['body'])
    except (TypeError,ValueError):return None
    if not isinstance(body,dict) or body.get('subtitle_reservation') is not True or body.get('subtitle_event') is not True:return None
    if not isinstance(row['id'],str) or not re.fullmatch('[0-9a-f]{32}',row['id']):return None
    if row['identity']!='subtitle:reservation:'+row['id'] or not row['subtitle_reserved_owned']:return None
    if not _readable_timestamp(row['subtitle_due']) and not (isinstance(row['subtitle_due'],float) and math.isinf(row['subtitle_due']) and row['subtitle_due']>0):return None
    if row['finished']!=-62135596800 or row['outcome']!='irrelevant':return None
    if body.get('operation')!='producer:download_media' or not isinstance(body.get('subtitle_canonical'),str) or not isinstance(body.get('media_job_id'),str):return None
    return body


def activate_due(store: 'Store', now: float, job_id: int | list[int] | None = None, cursor: tuple[float,str] | None = None) -> tuple[float,str] | None:
    from oko.storage.media_job_problems import managed_candidates_many
    if job_id is None:
        query="SELECT * FROM reader_problems WHERE subtitle_due IS NOT NULL AND subtitle_reserved_owned AND subtitle_due<=%s"
        params: tuple[Any,...]=(now,)
        if cursor is not None:
            query+=' AND (subtitle_due,id COLLATE "C")>(%s,%s)';params=(*params,*cursor)
        query+=' ORDER BY subtitle_due,id COLLATE "C" LIMIT 100'
    else:
        markers=[str(item) for item in (job_id if isinstance(job_id,list) else [job_id])]
        query="""SELECT r.* FROM unnest(%s::text[]) t(marker) CROSS JOIN LATERAL (
            SELECT * FROM reader_problems WHERE subtitle_due IS NOT NULL AND subtitle_reserved_owned AND subtitle_due<=%s
            AND identity LIKE 'subtitle:reservation:%%' AND md5(body::jsonb->>'media_job_id')=md5(t.marker)
            AND body::jsonb->>'media_job_id'=t.marker"""
        params=(markers,now)
        if cursor is not None:
            query+=' AND (subtitle_due,id COLLATE "C")>(%s,%s)';params=(*params,*cursor)
        query+=' ORDER BY subtitle_due,id COLLATE "C" LIMIT 100) r ORDER BY r.subtitle_due,r.id COLLATE "C" LIMIT 100'
    rows=store.db.execute(query,params).fetchall()
    next_cursor=(float(rows[-1]['subtitle_due']),rows[-1]['id']) if len(rows)==100 else None
    candidates_by_key: dict[tuple[int,str],dict[str,Any]]={}
    proven=verified(store,list(rows),now,projected_out=candidates_by_key)
    selected: list[tuple[Any,dict[str,Any]]]=[]
    for row in rows:
        body=proven.get(row['id'])
        if not body:continue
        candidate=candidates_by_key.get((int(body['media_job_id']),body['subtitle_canonical']))
        if candidate:selected.append((row,candidate))
    keys=[candidate['id'] for _,candidate in selected]
    if not keys:return next_cursor
    occupied_rows={row['identity']:row for row in store.db.execute('SELECT id,identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(keys,))}
    occupied=set(occupied_rows)
    if occupied_rows:protect_rows(store,list(occupied_rows.values()))
    counts={key:keys.count(key) for key in set(keys)}
    import hashlib
    conflicts={row['id']:(row,candidate) for row,candidate in selected if candidate['id'] in occupied and counts[candidate['id']]==1}
    alternatives: dict[str,str]={}
    for _ in range(3):
        unresolved={identity:pair for identity,pair in conflicts.items() if identity not in alternatives}
        if not unresolved:break
        proposals={identity:'job:media-isolated:'+hashlib.sha256(candidate['id'].encode()).hexdigest()+':'+new_reservation_id() for identity,(_,candidate) in unresolved.items()}
        used=set(keys) | {row['identity'] for row in store.db.execute('SELECT identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(proposals.values()),))}
        frequency: dict[str,int]={}
        for value in proposals.values():frequency[value]=frequency.get(value,0)+1
        for identity,value in proposals.items():
            if value not in used and frequency[value]==1:alternatives[identity]=value
    if len(alternatives)!=len(conflicts):raise RuntimeError('Unable to allocate isolated subtitle identity')
    updates=[]
    for row,candidate in selected:
        if counts[candidate['id']]!=1:continue
        actual=alternatives.get(row['id'],candidate['id'])
        future={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
        if actual!=candidate['id']:future['media_job_identity']=candidate['id']
        pointer=json.dumps(['oko-subtitle-v1',candidate['media_job_id'],candidate['id']])
        updates.append((row['id'],actual,json.dumps(future),row['body'],row['subtitle_due'],row['identity'],pointer))
    if updates:
        changed=store.db.execute("""UPDATE reader_problems p SET identity=v.identity,body=v.body,created=p.subtitle_due,finished=NULL,outcome=NULL,subtitle_due=NULL,subtitle_reserved_owned=false
            FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[],%s::text[]) v(id,identity,body,old_body,old_due,old_identity)
            WHERE p.id=v.id AND p.identity=v.old_identity AND p.body=v.old_body AND p.subtitle_due=v.old_due AND p.subtitle_reserved_owned""",tuple([r[i] for r in updates] for i in range(6))).rowcount
        if changed!=len(updates):raise RuntimeError('Subtitle reservation changed during activation')
        deleted=store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) v(key,value) WHERE s.key=v.key AND s.value=v.value',([row[5] for row in updates],[row[6] for row in updates])).rowcount
        if deleted!=len(updates):raise RuntimeError('Subtitle reservation pointer changed during activation')
        # The deadline projection was already visible with this ID and body.
        # Materializing it must not invalidate an otherwise unchanged ETag.
    return next_cursor


def drain_due(store: 'Store', now: float) -> None:
    cursor=None
    while True:
        cursor=activate_due(store,now,cursor=cursor)
        if cursor is None:return


def cancel_reservations(store: 'Store', job_id: int, previous: list[dict[str,Any]]) -> None:
    removed=[]
    for start in range(0,len(previous),100):
        batch=previous[start:start+100]
        canonical=[candidate['id'] for candidate in batch]
        rows=store.db.execute("""SELECT r.*,t.canonical FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
            SELECT * FROM reader_problems WHERE subtitle_due IS NOT NULL AND subtitle_reserved_owned AND identity LIKE 'subtitle:reservation:%%'
            AND md5(body::jsonb->>'media_job_id')=md5(%s) AND body::jsonb->>'media_job_id'=%s
            AND md5(body::jsonb->>'subtitle_canonical')=md5(t.canonical) AND body::jsonb->>'subtitle_canonical'=t.canonical LIMIT 2) r""",(canonical,str(job_id),str(job_id))).fetchall()
        counts: dict[str,int]={}
        for row in rows:counts[row['canonical']]=counts.get(row['canonical'],0)+1
        pointers={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([row['identity'] for row in rows],))} if rows else {}
        for row in rows:
            if counts[row['canonical']]!=1 or row['identity'] not in pointers:continue
            candidate=next((c for c in batch if c['id']==row['canonical']),None)
            if candidate and matches_snapshot(store,row,candidate,pointers[row['identity']]):removed.append((row['id'],row['identity'],row['body'],row['subtitle_due'],pointers[row['identity']]))
    if removed:
        deleted_rows=store.db.execute("""DELETE FROM reader_problems p USING unnest(%s::text[],%s::text[],%s::text[],%s::double precision[]) v(id,identity,body,due)
            WHERE p.id=v.id AND p.identity=v.identity AND p.body=v.body AND p.subtitle_due=v.due AND p.subtitle_reserved_owned""",tuple([row[i] for row in removed] for i in range(4))).rowcount
        if deleted_rows!=len(removed):raise RuntimeError('Subtitle reservation changed during cancellation')
        deleted=store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) v(key,value) WHERE s.key=v.key AND s.value=v.value',([row[1] for row in removed],[row[4] for row in removed])).rowcount
        if deleted!=len(removed):raise RuntimeError('Subtitle reservation pointer changed during cancellation')
        status_changed(store.db)


def verified(store: 'Store', rows: list[Any], now: float, *, projected_out: dict[tuple[int,str],dict[str,Any]] | None = None) -> dict[str,dict[str,Any]]:
    from oko.storage.media_job_problems import managed_candidates_many
    preliminary: dict[str,tuple[Any,dict[str,Any]]]={}
    jobs: set[int]=set()
    for row in rows:
        body=valid(row)
        if body is None:continue
        marker=body['media_job_id']
        if len(marker)>20 or not marker.lstrip('-').isdigit():continue
        job=int(marker)
        if str(job)!=marker or not -(2**63)<=job<2**63:continue
        preliminary[row['id']]=(row,body);jobs.add(job)
    projected: dict[int,list[dict[str,Any]]]={}
    ordered_jobs=sorted(jobs)
    for start in range(0,len(ordered_jobs),100):
        batch_jobs=ordered_jobs[start:start+100]
        owned=[row['id'] for row in store.db.execute("SELECT id FROM jobs WHERE id=ANY(%s) AND kind='download_media' AND subtitle_event_owned",(batch_jobs,))]
        if owned:projected.update(managed_candidates_many(store,owned,now,include_subtitles=True))
    pointer_keys=[row['identity'] for row,_ in preliminary.values()]
    pointers: dict[str,str]={}
    for start in range(0,len(pointer_keys),400):
        pointers.update({row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(pointer_keys[start:start+400],))})
    result: dict[str,dict[str,Any]]={}
    candidates_by_key={(job,candidate['id']):candidate for job,parts in projected.items() for candidate in parts}
    if projected_out is not None:projected_out.update(candidates_by_key)
    for identity,(row,body) in preliminary.items():
        candidate=candidates_by_key.get((int(body['media_job_id']),body['subtitle_canonical']))
        if not candidate:continue
        desired={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
        desired.update(subtitle_reservation=True,subtitle_canonical=candidate['id'])
        if body==desired and row['identity'] in pointers and matches_snapshot(store,row,candidate,pointers[row['identity']]):result[identity]=body
    return result


def transfer_reservation(store: 'Store', job_id: int, previous: list[dict[str,Any]], current: list[dict[str,Any]], new_ids: set[str]) -> set[str]:
    """Move only a uniquely proven old reservation to one new owner."""
    old_by_id={candidate['id']:candidate for candidate in previous}
    current_by_id={candidate['id']:candidate for candidate in current}
    removed=[candidate for identity,candidate in old_by_id.items() if identity not in current_by_id]
    added=[candidate for identity,candidate in current_by_id.items() if identity not in old_by_id]
    if len(removed)!=1 or len(added)!=1:return set()
    old,new=removed[0],added[0]
    same_cause=(old['code']==new['code'] or
        (old['code']=='work-download_media' and old.get('media_implicit_code') is True) or
        (new['code']=='work-download_media' and new.get('media_implicit_code') is True))
    if not same_cause or old.get('media_job_id')!=new.get('media_job_id'):return set()
    rows=store.db.execute("""SELECT r.* FROM unnest(%s::text[]) t(canonical) CROSS JOIN LATERAL (
        SELECT * FROM reader_problems WHERE subtitle_due IS NOT NULL AND subtitle_reserved_owned AND identity LIKE 'subtitle:reservation:%%'
        AND md5(body::jsonb->>'media_job_id')=md5(%s) AND body::jsonb->>'media_job_id'=%s
        AND md5(body::jsonb->>'subtitle_canonical')=md5(t.canonical) AND body::jsonb->>'subtitle_canonical'=t.canonical LIMIT 2) r""",([old['id'],new['id']],str(job_id),str(job_id))).fetchall()
    old_matches=[];new_occupied=False
    for row in rows:
        body=valid(row)
        if not body:continue
        if body['subtitle_canonical']==new['id']:new_occupied=True
        if body['subtitle_canonical']!=old['id']:continue
        expected={key:value for key,value in old.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
        expected.update(subtitle_reservation=True,subtitle_canonical=old['id'])
        if body==expected and matches_snapshot(store,row,old):old_matches.append(row)
    if new_occupied or len(old_matches)!=1:return set()
    row=old_matches[0]
    future={key:value for key,value in new.items() if key not in ('id','created_at','finished_at','outcome','_legacy_id')}
    future.update(subtitle_reservation=True,subtitle_canonical=new['id'])
    changed=store.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s AND identity=%s AND body=%s AND subtitle_due=%s AND subtitle_reserved_owned',(json.dumps(future),row['id'],row['identity'],row['body'],row['subtitle_due'])).rowcount
    if changed!=1:raise RuntimeError('Subtitle reservation changed during transfer')
    old_value=json.dumps(['oko-subtitle-v1',str(job_id),old['id']])
    new_value=json.dumps(['oko-subtitle-v1',str(job_id),new['id']])
    if store.db.execute('UPDATE state SET value=%s WHERE key=%s AND value=%s',(new_value,row['identity'],old_value)).rowcount!=1:
        raise RuntimeError('Subtitle reservation pointer changed')
    if new_ids:store.db.execute('DELETE FROM reader_problems WHERE id=ANY(%s) AND identity=%s AND finished IS NULL',(list(new_ids),new['id']))
    status_changed(store.db)
    return {old['id']}


def protect_legacy(store: 'Store', item: str) -> None:
    """Preserve exact affected legacy occurrences, never an item-wide marker."""
    import hashlib
    cursor: tuple[str,str] | None=None
    while True:
        query="""SELECT id,identity,md5(id) AS page_hash FROM reader_problems WHERE finished IS NULL AND identity LIKE 'job:%%'
            AND body::jsonb->>'code'='work-download_media' AND body::jsonb->>'operation' IN ('producer:download_media','producer:status')
            AND md5(substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$'))=md5(%s)
            AND substring(coalesce(body::jsonb->>'media_job_identity',body::jsonb->>'status_job_identity',identity) FROM ':download_media:(.*)$')=%s"""
        params: tuple[Any,...]=(item,item)
        if cursor is not None:query+=' AND (md5(id),id COLLATE "C")>(%s,%s)';params=(*params,*cursor)
        rows=store.db.execute(query+' ORDER BY md5(id),id COLLATE "C" LIMIT 400',params).fetchall()
        if not rows:return
        pairs=[('fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest(),json.dumps(['oko-fetch-null-v2',row['id'],row['identity']])) for row in rows]
        store.db.execute('INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) v(key,value) ON CONFLICT(key) DO NOTHING',([p[0] for p in pairs],[p[1] for p in pairs]))
        found={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([p[0] for p in pairs],))}
        if any(found.get(key)!=value for key,value in pairs):raise RuntimeError('Conflicting subtitle protection marker')
        cursor=(rows[-1]['page_hash'],rows[-1]['id'])
        store.fault('subtitle_legacy_protection_batch')


def protect_isolated(store: 'Store', field: str, canonical: str) -> None:
    """Preserve exact legacy collisions without trusting the stored marker."""
    import hashlib
    if field not in ('media_job_identity','status_job_identity'):raise ValueError('Invalid subtitle identity field')
    cursor: tuple[str,str] | None=None
    while True:
        query=f"""SELECT id,identity,md5(id) AS page_hash FROM reader_problems WHERE finished IS NULL
            AND body::jsonb->>'operation'='producer:download_media' AND body::jsonb->>'{field}' IS NOT NULL
            AND md5(body::jsonb->>'{field}')=md5(%s) AND body::jsonb->>'{field}'=%s"""
        params: tuple[Any,...]=(canonical,canonical)
        if cursor is not None:query+=' AND (md5(id),id COLLATE "C")>(%s,%s)';params=(*params,*cursor)
        rows=store.db.execute(query+' ORDER BY md5(id),id COLLATE "C" LIMIT 400',params).fetchall()
        if not rows:return
        pairs=[('fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest(),json.dumps(['oko-fetch-null-v2',row['id'],row['identity']])) for row in rows]
        store.db.execute('INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) v(key,value) ON CONFLICT(key) DO NOTHING',([p[0] for p in pairs],[p[1] for p in pairs]))
        found={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([p[0] for p in pairs],))}
        if any(found.get(key)!=value for key,value in pairs):raise RuntimeError('Conflicting subtitle protection marker')
        cursor=(rows[-1]['page_hash'],rows[-1]['id'])
        store.fault('subtitle_isolated_protection_batch')


def cleanup_pointers(store: 'Store', cursor: str | None) -> tuple[str | None,bool]:
    """Remove only well-formed pointers with no exact reservation row."""
    import re
    query='SELECT key,value FROM state WHERE key COLLATE "C">=\'subtitle:reservation:\' AND key COLLATE "C"<\'subtitle:reservation;\''
    params: tuple[Any,...]=()
    if cursor is not None:query+=' AND key COLLATE "C">%s';params=(cursor,)
    rows=store.db.execute(query+' ORDER BY key COLLATE "C" LIMIT 100',params).fetchall()
    if not rows:return None,True
    parsed=[]
    for row in rows:
        identity=row['key'][len('subtitle:reservation:'):]
        try:body=json.loads(row['value'])
        except (TypeError,ValueError):continue
        if not re.fullmatch('[0-9a-f]{32}',identity):continue
        if not isinstance(body,list) or len(body)!=3 or body[0]!='oko-subtitle-v1' or not all(isinstance(value,str) for value in body[1:]):continue
        parsed.append((row['key'],row['value'],identity))
    saved={row['id']:row['identity'] for row in store.db.execute('SELECT id,identity FROM reader_problems WHERE id=ANY(%s)',([row[2] for row in parsed],))} if parsed else {}
    stale=[(key,value) for key,value,identity in parsed if identity not in saved or saved[identity]!=key]
    if stale:store.db.execute('DELETE FROM state s USING unnest(%s::text[],%s::text[]) v(key,value) WHERE s.key=v.key AND s.value=v.value',([x[0] for x in stale],[x[1] for x in stale]))
    return rows[-1]['key'],False


def protect_rows(store: 'Store', rows: list[Any]) -> None:
    """Keep unknown records byte-identical when a candidate identity collides."""
    import hashlib
    for start in range(0,len(rows),400):
        batch=rows[start:start+400]
        pairs=[('fetch-null-protected:'+hashlib.sha256(row['id'].encode()).hexdigest(),json.dumps(['oko-fetch-null-v2',row['id'],row['identity']])) for row in batch]
        if not pairs:continue
        store.db.execute('INSERT INTO state(key,value) SELECT key,value FROM unnest(%s::text[],%s::text[]) v(key,value) ON CONFLICT(key) DO NOTHING',([p[0] for p in pairs],[p[1] for p in pairs]))
        found={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([p[0] for p in pairs],))}
        if any(found.get(key)!=value for key,value in pairs):raise RuntimeError('Conflicting subtitle protection marker')
