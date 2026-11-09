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
    excluded=('id','created_at','finished_at','outcome','_legacy_id') + (() if subtitle else ('_fetch_item',))
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
