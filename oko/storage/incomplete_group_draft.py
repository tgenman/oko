"""Experimental group projection; not wired into runtime."""
import json
from typing import Any
from oko.storage.incomplete_index import content_class
from oko.collection.contracts import SourceManifest
from oko.collection.external_media import expected_incomplete


class UnknownIncompleteGroup(ValueError):
    pass


def group_key(item: str, owner: str | None, content: str) -> str | None:
    classification=content_class(content)
    if classification==1 or owner is None:return None
    if classification!=0:raise UnknownIncompleteGroup('Unrecognized publication completeness')
    try:
        data=json.loads(content)
        header=data['extra']['_reader']
        if header.get('full',True):return None
        manifest=SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None
        if expected_incomplete(manifest):return None
        cause=header.get('captions')
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        raise UnknownIncompleteGroup('Unrecognized publication header') from exc
    return 'incomplete:'+owner+':'+(cause if cause in ('missing','error') else item)


def save_keys(db: Any, items: list[str]) -> None:
    from oko.storage.publication_owners import owner_expr
    for start in range(0,len(items),100):
        rows=db.execute(f'SELECT p.id,p.content,{owner_expr("p")} AS owner FROM publications p WHERE p.id=ANY(%s) ORDER BY p.id FOR UPDATE',(items[start:start+100],)).fetchall()
        if not rows:continue
        keys=[];unknown=[]
        for row in rows:
            try:
                keys.append(group_key(row['id'],row['owner'],row['content']));unknown.append(False)
            except UnknownIncompleteGroup:
                keys.append(None);unknown.append(True)
        db.execute('UPDATE publications p SET incomplete_group_key=s.key,incomplete_group_owner=s.owner,incomplete_group_unknown=s.unknown,incomplete_group_conflict=false,incomplete_group_ready=true FROM unnest(%s::text[],%s::text[],%s::text[],%s::boolean[]) s(id,key,owner,unknown) WHERE p.id=s.id',([row['id'] for row in rows],keys,[row['owner'] for row in rows],unknown))


def recover_group_keys(store: Any) -> None:
    from oko.storage.publication_owners import owner_expr
    from oko.storage.transaction import begin_write
    while True:
        with store.db:
            begin_write(store.db)
            store.db.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(),%s))",(746582092,))
            if store.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-groups-v1'").fetchone():return
            rows=store.db.execute(f"""SELECT p.id,p.content,{owner_expr('p')} AS owner FROM publications p
                WHERE p.viewed=0 AND p.complete=1 AND NOT p.source_deleted AND p.incomplete_class IS DISTINCT FROM 1
                    AND NOT p.incomplete_group_ready ORDER BY p.id LIMIT 100 FOR UPDATE""").fetchall()
            if not rows:
                store.db.execute("INSERT INTO state(key,value) VALUES ('experimental-incomplete-groups-v1','true') ON CONFLICT DO NOTHING")
                return
            keys=[];unknown=[]
            for row in rows:
                try:
                    keys.append(group_key(row['id'],row['owner'],row['content']))
                    unknown.append(False)
                except UnknownIncompleteGroup:
                    keys.append(None);unknown.append(True)
            store.db.execute('''UPDATE publications p SET incomplete_group_key=s.key,incomplete_group_owner=s.owner,incomplete_group_unknown=s.unknown,incomplete_group_ready=true
                FROM unnest(%s::text[],%s::text[],%s::text[],%s::boolean[]) s(id,key,owner,unknown) WHERE p.id=s.id''',([row['id'] for row in rows],keys,[row['owner'] for row in rows],unknown))
            refresh_youtube_flags(store,[row['id'] for row in rows])
        store.fault('experimental_incomplete_group_batch')


def generic_job_targets(db: Any, roots: list[str]) -> list[str]:
    result=set(roots)
    for start in range(0,len(roots),100):
        rows=db.execute('''SELECT target_publication_id FROM publication_links WHERE source_publication_id=ANY(%s)''',(roots[start:start+100],)).fetchall()
        result.update(row['target_publication_id'] for row in rows)
    materials=sorted(result)
    for start in range(0,len(materials),400):
        rows=db.execute('SELECT id FROM media_files WHERE publication_id=ANY(%s)',(materials[start:start+400],)).fetchall()
        result.update(row['id'] for row in rows)
    return sorted(result)


def suppressed_materials(store: Any, roots: list[str], now: float) -> set[str]:
    if not roots:return set()
    targets=generic_job_targets(store.db,roots)
    groups,_,_=store.problems.incomplete_generic_groups(now,targets)
    relevant=set(roots)
    return {obj['id'] for group in groups.values() for obj in group['objects']
            if obj['kind']=='material' and obj['id'] in relevant}



def known_incomplete_materials(store: Any, roots: list[str]) -> set[str]:
    known: set[str]=set()
    for start in range(0,len(roots),100):
        rows=store.db.execute("""SELECT target.id FROM unnest(%s::text[]) target(id) WHERE EXISTS (
            SELECT 1 FROM reader_problems p WHERE p.finished IS NULL AND p.body::jsonb->>'code'='material-incomplete'
              AND (p.body::jsonb->'objects') @> jsonb_build_array(jsonb_build_object('kind','material','id',target.id))
        )""",(roots[start:start+100],)).fetchall()
        known.update(row['id'] for row in rows)
    return known

def group_candidates(store: Any, keys: list[str], now: float, sticky: set[str] | None = None) -> dict[str,dict[str,Any]]:
    rows=store.db.execute('''SELECT p.id,p.content,p.incomplete_group_key,m.feed_id,f.kind AS feed_kind,j.status AS fetch_status,j.problem_code AS fetch_code,
          EXISTS (SELECT 1 FROM media_files file JOIN jobs mj ON mj.kind='download_media' AND mj.item=file.id
                  WHERE file.publication_id=p.id AND mj.status IN ('pending','running') AND mj.error IS NULL AND mj.problem_code IS NULL) AS media_waiting,
          CASE WHEN p.platform='youtube' THEN EXISTS(SELECT 1 FROM media_files file JOIN jobs mj ON mj.kind='download_media' AND mj.item=file.id
                  WHERE file.publication_id=p.id AND file.resource_key='youtube:subtitles' AND mj.status='pending' AND mj.problem_code IS NULL) ELSE FALSE END AS caption_waiting
        FROM publications p JOIN feed_publications m ON m.publication_id=p.id AND m.is_primary JOIN feeds f ON f.id=m.feed_id
        LEFT JOIN jobs j ON j.kind='fetch' AND j.item=p.id WHERE p.incomplete_group_key COLLATE "C"=ANY(%s) AND p.viewed=0 AND p.complete=1 AND NOT p.source_deleted
        ORDER BY p.incomplete_group_key,p.id''',(keys,)).fetchall()
    if not rows:return {}
    roots=[row['id'] for row in rows]
    suppressed=suppressed_materials(store,roots,now)
    known=known_incomplete_materials(store,roots)
    if sticky:known.update(sticky)
    youtube=store.youtube.status(now)
    result: dict[str,dict[str,Any]]={}
    for row in rows:
        key=row['incomplete_group_key']
        if row['id'] in suppressed:continue
        header=store.problems.header(json.dumps(json.loads(row['content'])['extra']['_reader']))
        if header.get('full',True) or (youtube['paused'] and row['feed_kind']=='youtube') or header.get('expected_incomplete'):continue
        if row['caption_waiting'] and youtube['next_at']>now:continue
        active=row['fetch_status'] in ('pending','running')
        waiting=active and row['fetch_code'] is None
        if (waiting or row['media_waiting']) and row['id'] not in known:continue
        if key not in result:
            result[key]=dict(id=key,code='material-incomplete',section='object',title='Материал сохранён не полностью',
                consequence='Доступная часть остаётся пригодной для чтения.',next_step='Повторите загрузку или откройте доступную часть.',
                objects=[],action=None,created_at=now,state='current',finished_at=None,outcome=None,next_at=None)
            if header.get('reason'):result[key]['reason']=header['reason']
        result[key]['objects'].append(dict(kind='material',id=row['id']))
        if active:result[key]['state']='retrying'
    return result


def group_candidate(store: Any, key: str, now: float) -> dict[str,Any] | None:
    return group_candidates(store,[key],now).get(key)



def valid_group_record(row: Any, key: str, allowed: set[str]) -> bool:
    try:body=json.loads(row['body'])
    except (TypeError,ValueError):return False
    if not isinstance(body,dict):return False
    from oko.storage.problems import _readable_timestamp
    if not _readable_timestamp(row['created']):return False
    fields={'code','section','title','consequence','next_step','objects','action','operation','state','next_at','reason','incomplete_group_identity'}
    if set(body)-fields or body.get('code')!='material-incomplete' or body.get('operation') not in (None,'producer:status','producer:incomplete'):
        return False
    if body.get('incomplete_group_identity') not in (None,key) or body.get('section')!='object' or body.get('state') not in ('current','retrying') or body.get('next_at') is not None:
        return False
    if any(not isinstance(body.get(field),str) for field in ('title','consequence','next_step')):return False
    if body.get('action') is not None or ('reason' in body and not isinstance(body['reason'],str)):return False
    objects=body.get('objects')
    return bool(isinstance(objects,list) and objects and all(isinstance(obj,dict) and set(obj)=={'kind','id'}
        and obj['kind']=='material' and isinstance(obj['id'],str) and obj['id'] in allowed for obj in objects))

def sync_group(store: Any, key: str, now: float, *, previous_items: set[str] | None = None) -> None:
    import hashlib
    import uuid
    candidate=group_candidate(store,key,now)
    canonical=store.db.execute('SELECT id,identity,body,created FROM reader_problems WHERE identity=%s AND finished IS NULL',(key,)).fetchone()
    indexed={r['id'] for r in store.db.execute('SELECT id FROM publications WHERE incomplete_group_key COLLATE "C"=%s',(key,))}
    allowed=indexed | (previous_items or set())
    def valid(row: Any) -> bool:
        return valid_group_record(row,key,allowed)
    row=canonical if canonical is not None and valid(canonical) else None
    isolated=store.db.execute("SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:incomplete' AND body::jsonb->>'incomplete_group_identity'=%s ORDER BY created,id COLLATE \"C\" LIMIT 2",(key,)).fetchall()
    if len(isolated)>1:return  # Preserve ambiguous legacy state for recovery review.
    if isolated and valid(isolated[0]):
        if row is not None:return  # Do not merge two existing occurrences without proof.
        row=isolated[0]
    if candidate is None:
        if row is None:return
        body=json.loads(row['body']);ids=[o['id'] for o in body['objects']]
        irrelevant=bool(store.db.execute('SELECT 1 FROM publications WHERE id=ANY(%s) AND source_deleted LIMIT 1',(ids,)).fetchone())
        store.problems.sync_candidate(row['identity'],None,now,removed_outcome='irrelevant' if irrelevant else 'resolved')
        return
    if row is not None:
        identity=row['identity']
    elif canonical is None:
        identity=key
    else:
        digest=hashlib.sha256(key.encode()).hexdigest()
        for _ in range(3):
            identity='incomplete:isolated:'+digest+':'+uuid.uuid4().hex
            if not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone():break
        else:raise RuntimeError('Unable to allocate incomplete occurrence')
    candidate['id']=identity
    candidate['operation']='producer:incomplete'
    if identity!=key:candidate['incomplete_group_identity']=key
    store.problems.sync_candidate(identity,candidate,now)


class TransactionGroupDraft:
    """Transaction-local affected IDs; no work remains after commit/rollback."""
    def __init__(self,store: Any) -> None:
        self.store=store
        self.flushing=False

    def clear(self) -> None:
        # SQL scratch is transaction-local and disappears on commit/rollback.
        pass

    def materialize(self, ids: Any) -> None:
        values=list(ids)
        for start in range(0,len(values),100):
            self.store.db.execute('INSERT INTO pg_temp.oko_incomplete_materialized SELECT unnest(%s::text[]) ON CONFLICT DO NOTHING',(values[start:start+100],))

    def item_pages(self) -> Any:
        cursor: str | None=None
        while True:
            sql='SELECT item FROM pg_temp.oko_incomplete_work'
            if cursor is not None:sql+=' WHERE item>%s COLLATE "C"'
            sql+=' ORDER BY item LIMIT 100'
            rows=self.store.db.execute(sql,(cursor,) if cursor is not None else ()).fetchall()
            if not rows:return
            items=[r['item'] for r in rows]
            yield items
            cursor=items[-1]

    def group_pages(self, *, old_only: bool = False) -> Any:
        cursor: str | None=None
        while True:
            conditions=[]
            if old_only:conditions.append('is_old')
            if cursor is not None:conditions.append('key>%s COLLATE "C"')
            sql='SELECT key,weight FROM pg_temp.oko_incomplete_groups'
            if conditions:sql+=' WHERE '+' AND '.join(conditions)
            sql+=' ORDER BY key LIMIT 100'
            rows=self.store.db.execute(sql,(cursor,) if cursor is not None else ()).fetchall()
            if not rows:return
            keys: list[str]=[];weight=0
            for row in rows:
                if keys and weight+row['weight']>2000:break
                keys.append(row['key']);weight+=row['weight']
            yield keys
            cursor=keys[-1]

    def update_group_weights(self) -> None:
        self.store.db.execute('''UPDATE pg_temp.oko_incomplete_groups g SET weight=
            (SELECT count(*) FROM publications p WHERE p.incomplete_group_key COLLATE "C"=g.key)
            + (SELECT count(*) FROM pg_temp.oko_incomplete_work w WHERE w.old_key=g.key)''')

    def mark_materials(self, items: list[str]) -> None:
        ordered=sorted({item for item in items if isinstance(item,str) and item})
        self.mark(ordered)
        for start in range(0,len(ordered),100):
            batch=ordered[start:start+100]
            if not self.store.db.execute('SELECT 1 FROM publication_links WHERE target_publication_id=ANY(%s) LIMIT 1',(batch,)).fetchone():continue
            for rows in self.store.db.incomplete_row_pages('SELECT source_publication_id FROM publication_links WHERE target_publication_id=ANY(%s)',(batch,)):
                self.mark([row['source_publication_id'] for row in rows])

    def mark_youtube_groups(self, *, pause: bool) -> None:
        db=self.store.db
        if pause:
            for rows in db.incomplete_row_pages("SELECT p.id FROM feeds f JOIN publications p ON p.incomplete_group_owner=f.id WHERE f.kind='youtube' AND p.incomplete_group_key IS NOT NULL"):
                self.mark_materials([r['id'] for r in rows])
        # Caption waits also occur for a YouTube publication owned by another feed.
        for rows in db.incomplete_row_pages("""SELECT p.id FROM jobs j JOIN media_files m ON m.id=j.item
            JOIN publications p ON p.id=m.publication_id WHERE j.kind='download_media' AND j.status='pending'
            AND j.problem_code IS NULL AND m.resource_key='youtube:subtitles' AND p.incomplete_group_key IS NOT NULL"""):
            self.mark_materials([r['id'] for r in rows])
        for rows in db.incomplete_row_pages("""SELECT kind,item FROM jobs WHERE kind='fetch' AND incomplete_youtube_sensitive AND problem_code IS NULL AND error IS NOT NULL
                AND status IN ('pending','running','failed','unavailable','blocked')
            UNION ALL SELECT j.kind,j.item FROM jobs j JOIN media_files m ON m.id=j.item
                WHERE j.kind='download_media' AND j.problem_code IS NULL AND j.error IS NOT NULL
                AND j.status IN ('pending','running','failed','unavailable','blocked') AND m.resource_key='youtube:subtitles'"""):
            self.mark_jobs([(row['kind'],row['item']) for row in rows])

    def mark_profile_feeds(self, feeds: set[str]) -> None:
        ordered=sorted(feeds)
        for start in range(0,len(ordered),100):
            batch=ordered[start:start+100]
            for rows in self.store.db.incomplete_row_pages("SELECT id FROM publications WHERE incomplete_group_owner=ANY(%s) AND incomplete_group_key IS NOT NULL",(batch,)):
                self.mark_materials([row['id'] for row in rows])
            for rows in self.store.db.incomplete_row_pages("SELECT kind,item FROM jobs WHERE feed_id=ANY(%s) AND problem_code IN ('identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')",(batch,)):
                self.mark_jobs([(row['kind'],row['item']) for row in rows])

    def mark_jobs(self, jobs: list[tuple[str,Any]]) -> None:
        # Legacy generic status can resolve a media item's text ID as a
        # publication ID as well as through media_files; preserve both paths.
        roots: set[str]=set()
        media: set[str]=set()
        for kind,item in jobs:
            if not isinstance(item,str) or not item or kind in ('read','discover','feed-delete'):continue
            roots.add(item)
            if kind=='download_media':media.add(item)
        ordered=sorted(media)
        for start in range(0,len(ordered),400):
            roots.update(row['publication_id'] for row in self.store.db.execute('SELECT publication_id FROM media_files WHERE id=ANY(%s)',(ordered[start:start+400],)))
        self.mark_materials(sorted(roots))

    def mark_job(self, kind: str, item: str) -> None:
        self.mark_jobs([(kind,item)])

    def mark(self, items: list[str]) -> None:
        if self.flushing or not items:return
        db=self.store.db
        if not db.in_transaction:raise RuntimeError('Incomplete event requires an open transaction')
        ordered=sorted({item for item in items if isinstance(item,str) and item})
        if not ordered:return
        db.ensure_incomplete_workset()
        for start in range(0,len(ordered),100):
            db.execute("""INSERT INTO pg_temp.oko_incomplete_work(item,old_key)
                SELECT id,incomplete_group_key FROM publications WHERE id=ANY(%s)
                AND (viewed=0 OR incomplete_group_key IS NOT NULL)
                AND (incomplete_class IS DISTINCT FROM 1 OR incomplete_group_key IS NOT NULL OR NOT incomplete_group_ready)
                ON CONFLICT(item) DO NOTHING""",(ordered[start:start+100],))

    def flush(self) -> None:
        if self.flushing:return
        db=self.store.db
        if db.execute("SELECT to_regclass('pg_temp.oko_incomplete_work')").fetchone()[0] is None:return
        self.flushing=True
        try:
            now=self.store.wall_clock()
            db.execute('INSERT INTO pg_temp.oko_incomplete_groups(key,is_old) SELECT DISTINCT old_key,true FROM pg_temp.oko_incomplete_work WHERE old_key IS NOT NULL ON CONFLICT(key) DO UPDATE SET is_old=true')
            due_exists=db.execute('SELECT 1 FROM reader_problems WHERE incomplete_due IS NOT NULL AND incomplete_due<=%s LIMIT 1',(now,)).fetchone() is not None
            if due_exists:self.update_group_weights()
            for keys in self.group_pages(old_only=True) if due_exists else ():
                due=due_snapshot_materials(self.store,keys,now)
                ordered_due=sorted(due)
                for start in range(0,len(ordered_due),100):
                    db.execute('UPDATE pg_temp.oko_incomplete_work SET sticky=true WHERE item=ANY(%s)',(ordered_due[start:start+100],))
                apply_due_current_for_keys(self.store,keys,now)
            for items in self.item_pages():
                known=known_incomplete_materials(self.store,items)
                if known:db.execute('UPDATE pg_temp.oko_incomplete_work SET sticky=true WHERE item=ANY(%s)',(sorted(known),))
                save_keys(db,items)
                refresh_youtube_flags(self.store,items)
                db.execute("""UPDATE pg_temp.oko_incomplete_work w SET new_key=p.incomplete_group_key,unknown=p.incomplete_group_unknown,forgotten=p.viewed<>0
                    FROM publications p WHERE w.item=p.id AND w.item=ANY(%s)""",(items,))
                db.execute('UPDATE pg_temp.oko_incomplete_work w SET forgotten=true WHERE item=ANY(%s) AND NOT EXISTS (SELECT 1 FROM publications p WHERE p.id=w.item)',(items,))
            db.execute('INSERT INTO pg_temp.oko_incomplete_groups(key) SELECT DISTINCT new_key FROM pg_temp.oko_incomplete_work WHERE new_key IS NOT NULL ON CONFLICT(key) DO NOTHING')
            self.update_group_weights()
            for keys in self.group_pages():
                sql='SELECT item,old_key,new_key,sticky,unknown FROM pg_temp.oko_incomplete_work WHERE old_key=ANY(%s) OR new_key=ANY(%s)'
                sample=db.execute(sql+' LIMIT 101',(keys,keys)).fetchall()
                pages=(page for page in [sample]) if len(sample)<=100 else db.incomplete_row_pages(sql,(keys,keys))
                old: dict[str,set[str]]={};sticky=set();blocked=set()
                for page in pages:
                    for row in page:
                        if row['old_key'] in keys:
                            old.setdefault(row['old_key'],set()).add(row['item'])
                            if row['unknown']:blocked.add(row['old_key'])
                        if row['sticky']:sticky.add(row['item'])
                selected=[key for key in keys if key not in blocked]
                sync_groups_time_batch(self.store,selected,now,old,sticky)
            cursor: str | None=None
            while True:
                sql='SELECT id FROM pg_temp.oko_incomplete_materialized'
                if cursor is not None:sql+=' WHERE id>%s COLLATE "C"'
                sql+=' ORDER BY id LIMIT 100'
                rows=db.execute(sql,(cursor,) if cursor is not None else ()).fetchall()
                if not rows:break
                ids=[r['id'] for r in rows]
                forgotten={r['item'] for r in db.execute("""SELECT DISTINCT w.item FROM reader_problems p CROSS JOIN LATERAL jsonb_array_elements(p.body::jsonb->'objects') obj JOIN pg_temp.oko_incomplete_work w ON w.item=obj->>'id' AND w.forgotten WHERE p.id=ANY(%s) AND jsonb_typeof(p.body::jsonb->'objects')='array' AND obj->>'kind'='material'""",(ids,))}
                if forgotten:scrub_materialized(self.store,set(ids),forgotten,now)
                cursor=ids[-1]
            db.execute('DELETE FROM pg_temp.oko_incomplete_groups')
            db.execute('DELETE FROM pg_temp.oko_incomplete_materialized')
            db.execute('DELETE FROM pg_temp.oko_incomplete_work')
        finally:self.flushing=False


def reservation_pointer(key: str) -> str:
    import hashlib
    return 'incomplete:reservation-group:'+hashlib.sha256(key.encode('utf-8')).hexdigest()


def reservation_pointers(key: str) -> list[str]:
    base=reservation_pointer(key)
    return [base,base+':1',base+':2']


def reservation(store: Any, key: str) -> dict[str,Any] | None:
    pointers=store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(reservation_pointers(key),)).fetchall()
    recognized=[]
    for pointer in pointers:
        try:metadata=json.loads(pointer['value'])
        except (TypeError,ValueError):continue
        if isinstance(metadata,dict) and metadata.get('group')==key and isinstance(metadata.get('id'),str):recognized.append(metadata)
    if len(recognized)!=1:return None
    metadata=recognized[0]
    identity='incomplete:reservation:'+metadata['id']
    reverse=store.db.execute('SELECT value FROM state WHERE key=%s',(identity,)).fetchone()
    if not reverse or reverse['value']!=key:return None
    row=store.db.execute('SELECT id,identity,body,finished,incomplete_due FROM reader_problems WHERE id=%s AND identity=%s AND finished IS NOT NULL',(metadata['id'],identity)).fetchone()
    if not row:return None
    try:body=json.loads(row['body'])
    except (TypeError,ValueError):return None
    from oko.storage.problems import _readable_timestamp
    if (not isinstance(body,dict) or body.get('reservation') is not True or body.get('code')!='material-incomplete'
        or body.get('operation')!='producer:incomplete-reservation' or body.get('incomplete_group_identity')!=key
        or not _readable_timestamp(body.get('deadline')) or body['deadline']!=row['incomplete_due'] or row['finished']!=-62135596800
        or not valid_incomplete_projection(body.get('future')) or not body['future']['objects']):return None
    return {'id':row['id'],'deadline':body['deadline'],'future':body['future']}


def clear_reservation(store: Any, key: str, saved: dict[str,Any], *, delete_row: bool) -> None:
    identity='incomplete:reservation:'+saved['id']
    if delete_row:store.db.execute('DELETE FROM reader_problems WHERE id=%s AND identity=%s',(saved['id'],identity))
    store.db.execute('DELETE FROM state WHERE key=%s AND value=%s',(identity,key))
    for existing in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(reservation_pointers(key),)):
        try:metadata=json.loads(existing['value'])
        except (ValueError,TypeError):metadata=None
        if metadata=={'group':key,'id':saved['id']}:store.db.execute('DELETE FROM state WHERE key=%s',(existing['key'],))


def reservation_body(key: str, deadline: float, candidate: dict[str,Any] | None = None) -> dict[str,Any]:
    future=None
    if candidate is not None:
        future={k:v for k,v in candidate.items() if k not in ('id','created_at','finished_at','outcome')}
        future['operation']='producer:incomplete'
    return dict(code='material-incomplete',section='object',title='Резерв идентификатора',consequence='Служебная запись.',
        next_step='Ожидание.',objects=[],action=None,operation='producer:incomplete-reservation',state='current',next_at=None,
        reservation=True,incomplete_group_identity=key,deadline=deadline,future=future)


def virtual_group(store: Any, key: str, now: float) -> dict[str,Any] | None:
    saved=reservation(store,key)
    if not saved or now<saved['deadline']:return None
    candidate=dict(saved['future'])
    candidate.update(id=saved['id'],created_at=saved['deadline'],finished_at=None,outcome=None)
    return candidate


def sync_group_with_time(store: Any, key: str, now: float, *, previous_items: set[str] | None = None) -> None:
    previous={key:previous_items} if previous_items is not None else None
    sync_groups_time_batch(store,[key],now,previous)


def sync_groups_batch(store: Any, keys: list[str], now: float, previous_items: dict[str,set[str]] | None = None, sticky: set[str] | None = None, projected: dict[str,dict[str,Any]] | None = None) -> None:
    """Batch ordinary, non-reserved group transitions for one transaction."""
    import uuid
    if not keys:return
    previous_items=previous_items or {}
    candidates={key:dict(value) for key,value in (projected if projected is not None else group_candidates(store,keys,now,sticky)).items()}
    canonical={r['identity']:r for r in store.db.execute('SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(keys,))}
    isolated: dict[str,list[Any]]={}
    for r in store.db.execute("SELECT r.*,k.group_key FROM unnest(%s::text[]) AS k(group_key) CROSS JOIN LATERAL (SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:incomplete' AND body::jsonb->>'incomplete_group_identity'=k.group_key LIMIT 2) AS r",(keys,)):
        isolated.setdefault(r['group_key'],[]).append(r)
    members: dict[str,set[str]]={}
    for row in store.db.execute('SELECT id,incomplete_group_key FROM publications WHERE incomplete_group_key COLLATE "C"=ANY(%s)',(keys,)):
        members.setdefault(row['incomplete_group_key'],set()).add(row['id'])
    selected: dict[str,Any]={}
    candidate_keys: dict[str,str]={}
    foreign_keys=[]
    for key in keys:
        allowed=members.get(key,set()) | previous_items.get(key,set())
        def valid(row: Any) -> bool:
            return valid_group_record(row,key,allowed)
        row=canonical.get(key)
        current=row if row is not None and valid(row) else None
        alternates=isolated.get(key,[])
        if len(alternates)>1:continue
        if alternates and valid(alternates[0]):
            if current is not None:continue
            current=alternates[0]
        if current is not None:selected[key]=current
        if key not in candidates:continue
        if current is not None:candidate_keys[key]=current['identity']
        elif row is None:candidate_keys[key]=key
        else:foreign_keys.append(key)
    unresolved=set(foreign_keys)
    for _ in range(3):
        if not unresolved:break
        proposals={key:'incomplete:isolated:'+uuid.uuid4().hex for key in sorted(unresolved)}
        occupied={row['identity'] for row in store.db.execute('SELECT identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(proposals.values()),))}
        counts: dict[str,int]={}
        for identity in proposals.values():counts[identity]=counts.get(identity,0)+1
        for key,identity in proposals.items():
            if identity not in occupied and counts[identity]==1:
                candidate_keys[key]=identity;unresolved.remove(key)
    if unresolved:raise RuntimeError('Unable to allocate incomplete occurrence')
    closing={key:row for key,row in selected.items() if key not in candidates}
    refs={obj['id'] for row in closing.values() for obj in json.loads(row['body'])['objects']}
    deleted={r['id'] for r in store.db.execute('SELECT id FROM publications WHERE id=ANY(%s) AND source_deleted',(sorted(refs),))} if refs else set()
    finished: dict[str,list[str]]={'irrelevant':[],'resolved':[]}
    for row in closing.values():
        body=json.loads(row['body'])
        outcome='irrelevant' if any(o['id'] in deleted for o in body['objects']) else 'resolved'
        finished[outcome].append(row['id'])
    changed=False
    for outcome,ids in finished.items():
        if ids:
            store.db.execute('UPDATE reader_problems SET finished=%s,outcome=%s,incomplete_due=NULL,incomplete_future=NULL,incomplete_closes=false WHERE id=ANY(%s) AND finished IS NULL',(now,outcome,ids));changed=True
    updates=[];added=[]
    for key,identity in candidate_keys.items():
        candidate=candidates[key]
        candidate['operation']='producer:incomplete'
        if identity!=key:candidate['incomplete_group_identity']=key
        body={k:v for k,v in candidate.items() if k not in ('id','created_at','finished_at','outcome')}
        encoded=json.dumps(body)
        if key in selected:
            if selected[key]['body']!=encoded:updates.append((selected[key]['id'],encoded))
        else:added.append(('',identity,'background',encoded,now))
    unresolved_ids=set(range(len(added)))
    for _ in range(3):
        if not unresolved_ids:break
        id_proposals={index:uuid.uuid4().hex for index in sorted(unresolved_ids)}
        used={row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE id=ANY(%s)',(list(id_proposals.values()),))}
        id_counts: dict[str,int]={}
        for identity in id_proposals.values():id_counts[identity]=id_counts.get(identity,0)+1
        for index,identity in id_proposals.items():
            if identity not in used and id_counts[identity]==1:
                _,group_identity,origin,encoded,created=added[index]
                added[index]=(identity,group_identity,origin,encoded,created)
                unresolved_ids.remove(index)
    if unresolved_ids:raise RuntimeError('Unable to allocate incomplete problem ID')
    if updates:
        store.db.execute('UPDATE reader_problems p SET body=s.body FROM unnest(%s::text[],%s::text[]) s(id,body) WHERE p.id=s.id AND p.finished IS NULL',([v[0] for v in updates],[v[1] for v in updates]));changed=True
    if added:
        store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) SELECT * FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[])",tuple([v[i] for v in added] for i in range(5)));changed=True
    if changed:
        from oko.storage.reader_status import changed as status_changed
        status_changed(store.db)



def flag_reservation_conflicts(store: Any, keys: list[str]) -> None:
    if not keys:return
    rows=store.db.execute('UPDATE publications SET incomplete_group_conflict=true WHERE incomplete_group_key COLLATE "C"=ANY(%s) AND NOT incomplete_group_conflict RETURNING id',(keys,)).fetchall()
    if rows:
        from oko.storage.reader_status import changed as status_changed
        status_changed(store.db)

def create_reservations_batch(store: Any, future: dict[str,dict[str,Any]], now: float, deadline: float) -> set[str]:
    """Reserve first occurrences for independent, unoccupied groups."""
    import uuid
    keys=list(future)
    if not keys:return set()
    possible={key:reservation_pointers(key) for key in keys}
    occupied={row['key'] for row in store.db.execute('SELECT key FROM state WHERE key=ANY(%s)',([pointer for values in possible.values() for pointer in values],))}
    pointers={}
    claimed=set()
    for key in keys:
        pointer=next((value for value in possible[key] if value not in occupied and value not in claimed),None)
        if pointer is not None:pointers[key]=pointer;claimed.add(pointer)
    unresolved=set(pointers)
    flag_reservation_conflicts(store,[key for key in keys if key not in unresolved])
    chosen: dict[str,str]={}
    for _ in range(3):
        if not unresolved:break
        generated={key:'incomplete-reserved-'+uuid.uuid4().hex for key in sorted(unresolved)}
        counts: dict[str,int]={}
        for identity in generated.values():counts[identity]=counts.get(identity,0)+1
        used_ids={row['id'] for row in store.db.execute('SELECT id FROM reader_problems WHERE id=ANY(%s)',(list(generated.values()),))}
        reverse_keys=['incomplete:reservation:'+identity for identity in generated.values()]
        used_state={row['key'] for row in store.db.execute('SELECT key FROM state WHERE key=ANY(%s)',(reverse_keys,))}
        for key,identity in generated.items():
            if counts[identity]==1 and identity not in used_ids and 'incomplete:reservation:'+identity not in used_state:
                chosen[key]=identity;unresolved.remove(key)
    if unresolved:raise RuntimeError('Unable to reserve incomplete occurrence')
    state_rows=[];problem_rows=[]
    for key,identity in chosen.items():
        reverse='incomplete:reservation:'+identity
        state_rows.append((pointers[key],json.dumps({'group':key,'id':identity})))
        state_rows.append((reverse,key))
        problem_rows.append((identity,reverse,'background',json.dumps(reservation_body(key,deadline,future[key])),now,-62135596800,'irrelevant',deadline))
    if state_rows:
        store.db.execute('INSERT INTO state(key,value) SELECT * FROM unnest(%s::text[],%s::text[])',([r[0] for r in state_rows],[r[1] for r in state_rows]))
        store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome,incomplete_due) SELECT * FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[],%s::double precision[],%s::double precision[],%s::text[],%s::double precision[])",tuple([r[i] for r in problem_rows] for i in range(8)))
    return set(chosen)


def sync_groups_time_batch(store: Any, keys: list[str], now: float, previous_items: dict[str,set[str]] | None = None, sticky: set[str] | None = None) -> None:
    if not keys:return
    possible={key:reservation_pointers(key) for key in keys}
    rows={row['key']:row['value'] for row in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([pointer for values in possible.values() for pointer in values],))}
    existing=[];ordinary=[];conflicts=[]
    for key in keys:
        recognized=False
        for pointer in possible[key]:
            try:metadata=json.loads(rows.get(pointer,''))
            except (TypeError,ValueError):continue
            if isinstance(metadata,dict) and metadata.get('group')==key and isinstance(metadata.get('id'),str):recognized=True
        if recognized:existing.append(key)
        elif any(pointer not in rows for pointer in possible[key]):ordinary.append(key)
        else:conflicts.append(key)
    flag_reservation_conflicts(store,conflicts)
    now_candidates: dict[str,dict[str,Any]]={}
    if ordinary:
        now_candidates=group_candidates(store,ordinary,now,sticky)
        sync_groups_batch(store,ordinary,now,previous_items,sticky,now_candidates)
        limits=store.youtube._limits()
        deadline=max(limits['next_at'],limits['blocked_until'])
        if deadline>now:
            future=group_candidates(store,[key for key in ordinary if key not in now_candidates],deadline)
            ambiguous=[]
            if future:
                for row in store.db.execute('SELECT identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(future),)):
                    try:body=json.loads(row['body'])
                    except (ValueError,TypeError):continue
                    if isinstance(body,dict) and body.get('code')=='material-incomplete':ambiguous.append(row['identity'])
            flag_reservation_conflicts(store,ambiguous)
            ambiguous_keys=set(ambiguous)
            create_reservations_batch(store,{key:value for key,value in future.items() if key not in ambiguous_keys},now,deadline)
    existing_current=sync_existing_reservations_batch(store,existing,now,previous_items,sticky,pointer_rows=rows) if existing else set()
    limits=store.youtube._limits();deadline=max(limits['next_at'],limits['blocked_until'])
    if deadline>now:schedule_current_groups(store,keys,now,deadline,(set(now_candidates) | existing_current) if existing_current is not None else None)
    else:
        scheduled_rows=scheduled_group_rows(store,keys)
        valid_ids=[row['id'] for row in scheduled_rows if current_schedule(row) is not None]
        if valid_ids:
            store.db.execute("UPDATE reader_problems SET incomplete_due=NULL,incomplete_future=NULL,incomplete_closes=false WHERE id=ANY(%s) AND finished IS NULL",(valid_ids,))



def activate_saved_reservations(store: Any, saved: dict[str,dict[str,Any]], pointers: dict[str,str], pointer_rows: dict[str,str]) -> set[str]:
    """Materialize the future snapshots that were already visible at their deadline."""
    import uuid
    if not saved:return set()
    occupied={r['identity']:r for r in store.db.execute('SELECT id,identity,body,created FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(saved),))}
    choices={};foreign=[];ambiguous=[]
    for key in saved:
        row=occupied.get(key)
        if not row:choices[key]=key;continue
        try:body=json.loads(row['body'])
        except (TypeError,ValueError):body=None
        if isinstance(body,dict) and body.get('code')=='material-incomplete':ambiguous.append(key)
        else:foreign.append(key)
    flag_reservation_conflicts(store,ambiguous)
    unresolved=set(foreign)
    for _ in range(3):
        if not unresolved:break
        proposals={key:'incomplete:isolated:'+uuid.uuid4().hex for key in sorted(unresolved)}
        used={r['identity'] for r in store.db.execute('SELECT identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(proposals.values()),))}
        counts: dict[str,int]={}
        for identity in proposals.values():counts[identity]=counts.get(identity,0)+1
        for key,identity in proposals.items():
            if identity not in used and counts[identity]==1:choices[key]=identity;unresolved.remove(key)
    if unresolved:raise RuntimeError('Unable to allocate incomplete occurrence')
    updates=[];state_delete=[]
    for key,identity in choices.items():
        prior=saved[key];body=dict(prior['future'])
        if identity!=key:body['incomplete_group_identity']=key
        updates.append((prior['id'],identity,json.dumps(body),prior['deadline']))
        state_delete.extend([(pointers[key],pointer_rows[pointers[key]]),('incomplete:reservation:'+prior['id'],key)])
    if updates:
        changed=store.db.execute("""UPDATE reader_problems p SET identity=s.identity,body=s.body,created=s.created,finished=NULL,outcome=NULL,incomplete_due=NULL
            FROM unnest(%s::text[],%s::text[],%s::text[],%s::double precision[]) s(id,identity,body,created)
            WHERE p.id=s.id AND p.identity='incomplete:reservation:'||p.id AND p.finished IS NOT NULL AND p.incomplete_due IS NOT NULL""",tuple([r[i] for r in updates] for i in range(4))).rowcount
        if changed!=len(updates):raise RuntimeError('Incomplete reservation changed during activation')
        manager=getattr(store,'incomplete_draft',None)
        if manager is not None and manager.flushing:manager.materialize(row[0] for row in updates)
    if state_delete:
        removed=store.db.execute('DELETE FROM state p USING unnest(%s::text[],%s::text[]) s(key,value) WHERE p.key=s.key AND p.value=s.value',([r[0] for r in state_delete],[r[1] for r in state_delete])).rowcount
        if removed!=len(state_delete):raise RuntimeError('Incomplete reservation pointer changed during activation')
    return set(choices)

def sync_existing_reservations_batch(store: Any, keys: list[str], now: float, previous_items: dict[str,set[str]] | None = None, sticky: set[str] | None = None, pointer_rows: dict[str,str] | None = None) -> set[str] | None:
    """Synchronize already-reserved groups without per-group SQL calls."""
    import uuid
    if not keys:return set()
    possible={key:reservation_pointers(key) for key in keys}
    if pointer_rows is None:
        pointer_rows={r['key']:r['value'] for r in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',([pointer for values in possible.values() for pointer in values],))}
    metadata={};pointers={}
    for key in keys:
        matches=[]
        for pointer in possible[key]:
            try:value=json.loads(pointer_rows.get(pointer,''))
            except (ValueError,TypeError):continue
            if isinstance(value,dict) and value.get('group')==key and isinstance(value.get('id'),str):matches.append((pointer,value['id']))
        if len(matches)==1:pointers[key],metadata[key]=matches[0]
    reverses={r['key']:r['value'] for r in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(['incomplete:reservation:'+identity for identity in metadata.values()],))} if metadata else {}
    records={r['id']:r for r in store.db.execute('SELECT id,identity,body,finished,incomplete_due FROM reader_problems WHERE id=ANY(%s)',(list(metadata.values()),))} if metadata else {}
    valid={}
    from oko.storage.problems import _readable_timestamp
    for key,identity in metadata.items():
        row=records.get(identity)
        if not row or row['identity']!='incomplete:reservation:'+identity or row['finished'] is None or reverses.get(row['identity'])!=key:continue
        try:body=json.loads(row['body'])
        except (ValueError,TypeError):continue
        if (isinstance(body,dict) and body.get('reservation') is True and body.get('code')=='material-incomplete'
            and body.get('operation')=='producer:incomplete-reservation' and body.get('incomplete_group_identity')==key
            and _readable_timestamp(body.get('deadline')) and body['deadline']==row['incomplete_due'] and row['finished']==-62135596800
            and valid_incomplete_projection(body.get('future')) and body['future']['objects']):
            valid[key]={'id':identity,'deadline':body['deadline'],'future':body['future']}
    flag_reservation_conflicts(store,sorted(set(keys)-set(valid)))
    if not valid:return set()
    expired={key:value for key,value in valid.items() if value['deadline']<=now}
    if expired:
        activated=activate_saved_reservations(store,expired,pointers,pointer_rows)
        if activated:sync_groups_time_batch(store,sorted(activated),now,previous_items,sticky)
        valid={key:value for key,value in valid.items() if key not in expired}
    if not valid:return None
    current=group_candidates(store,list(valid),now,sticky)
    # Close stale current groups before computing future sticky visibility.
    inactive=[key for key in valid if key not in current]
    if inactive:sync_groups_batch(store,inactive,now,previous_items,sticky)
    canonical={r['identity']:r for r in store.db.execute('SELECT id,identity,body FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(current),))} if current else {}
    activate={};foreign=[]
    for key,candidate in current.items():
        row=canonical.get(key)
        if row:
            try:body=json.loads(row['body'])
            except (ValueError,TypeError):body=None
            if isinstance(body,dict) and body.get('code')=='material-incomplete':
                flag_reservation_conflicts(store,[key])
                continue  # Ambiguous existing occurrence: preserve both.
            foreign.append(key)
        else:activate[key]=key
    unresolved=set(foreign)
    for _ in range(3):
        if not unresolved:break
        proposals={key:'incomplete:isolated:'+uuid.uuid4().hex for key in sorted(unresolved)}
        occupied={r['identity'] for r in store.db.execute('SELECT identity FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(proposals.values()),))}
        counts: dict[str,int]={}
        for identity in proposals.values():counts[identity]=counts.get(identity,0)+1
        for key,identity in proposals.items():
            if identity not in occupied and counts[identity]==1:activate[key]=identity;unresolved.remove(key)
    if unresolved:raise RuntimeError('Unable to allocate incomplete occurrence')
    activations=[];state_delete=[]
    for key,identity in activate.items():
        candidate=current[key];candidate['operation']='producer:incomplete'
        if identity!=key:candidate['incomplete_group_identity']=key
        body={k:v for k,v in candidate.items() if k not in ('id','created_at','finished_at','outcome')}
        saved=valid[key];activations.append((saved['id'],identity,json.dumps(body),min(saved['deadline'],now)))
        state_delete.extend([(pointers[key],pointer_rows[pointers[key]]),('incomplete:reservation:'+saved['id'],key)])
    if activations:
        changed=store.db.execute('''UPDATE reader_problems p SET identity=s.identity,body=s.body,created=s.created,finished=NULL,outcome=NULL,incomplete_due=NULL
            FROM unnest(%s::text[],%s::text[],%s::text[],%s::double precision[]) s(id,identity,body,created)
            WHERE p.id=s.id AND p.identity='incomplete:reservation:'||p.id AND p.finished IS NOT NULL AND p.incomplete_due IS NOT NULL''',tuple([r[i] for r in activations] for i in range(4))).rowcount
        if changed!=len(activations):raise RuntimeError('Incomplete reservation changed during activation')
        manager=getattr(store,'incomplete_draft',None)
        if manager is not None and manager.flushing:manager.materialize(row[0] for row in activations)
    limits=store.youtube._limits();deadline=max(limits['next_at'],limits['blocked_until'])
    future=group_candidates(store,inactive,deadline) if inactive and deadline>now else {}
    updates=[];delete_ids=[]
    for key in inactive:
        saved=valid[key]
        if key in future:
            if saved['deadline']!=deadline or saved['future']!=reservation_body(key,deadline,future[key])['future']:
                updates.append((saved['id'],json.dumps(reservation_body(key,deadline,future[key])),deadline))
        else:
            delete_ids.append(saved['id']);state_delete.extend([(pointers[key],pointer_rows[pointers[key]]),('incomplete:reservation:'+saved['id'],key)])
    if updates:store.db.execute('''UPDATE reader_problems p SET body=s.body,incomplete_due=s.deadline FROM unnest(%s::text[],%s::text[],%s::double precision[]) s(id,body,deadline) WHERE p.id=s.id AND p.finished IS NOT NULL''',tuple([r[i] for r in updates] for i in range(3)))
    if delete_ids:store.db.execute("DELETE FROM reader_problems WHERE id=ANY(%s) AND identity='incomplete:reservation:'||id AND finished IS NOT NULL",(delete_ids,))
    if state_delete:
        removed=store.db.execute('DELETE FROM state p USING unnest(%s::text[],%s::text[]) s(key,value) WHERE p.key=s.key AND p.value=s.value',([r[0] for r in state_delete],[r[1] for r in state_delete])).rowcount
        if removed!=len(state_delete):raise RuntimeError('Incomplete reservation pointer changed during activation')
    return None if expired else set(current)


def recover_group_problems(store: Any) -> None:
    """Two phases: populate all group members first, then reconcile occurrences."""
    from oko.storage.transaction import begin_write
    if store.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-problems-v1'").fetchone():return
    cursor: str | None=None
    while True:
        query='SELECT incomplete_group_key COLLATE "C" AS incomplete_group_key FROM publications WHERE incomplete_group_key IS NOT NULL'
        if cursor is not None:query+=' AND incomplete_group_key COLLATE "C">%s'
        query+=' GROUP BY incomplete_group_key COLLATE "C" ORDER BY incomplete_group_key COLLATE "C" LIMIT 100'
        rows=store.db.execute(query,(cursor,) if cursor is not None else ()).fetchall()
        if not rows:break
        keys=[row['incomplete_group_key'] for row in rows]
        with store.db:
            begin_write(store.db)
            now=store.wall_clock()
            apply_due_current_for_keys(store,keys,now)
            sync_groups_time_batch(store,keys,now)
        cursor=keys[-1]
        store.fault('experimental_incomplete_group_problem_batch')
    cursor=None
    while True:
        query="""SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND left(identity,11)='incomplete:'
            AND body::jsonb->>'code'='material-incomplete' AND coalesce(body::jsonb->>'operation','producer:status')='producer:status'"""
        if cursor is not None:query+=' AND id COLLATE "C">%s'
        query+=' ORDER BY id COLLATE "C" LIMIT 100'
        rows=store.db.execute(query,(cursor,) if cursor is not None else ()).fetchall()
        if not rows:break
        possible={}
        for row in rows:
            try:body=json.loads(row['body'])
            except (ValueError,TypeError):continue
            objects=body.get('objects') if isinstance(body,dict) else None
            if not isinstance(objects,list) or len(objects)!=1 or not isinstance(objects[0],dict):continue
            item=objects[0].get('id');identity=row['identity']
            if (objects[0].get('kind')!='material' or not isinstance(item,str) or not item or ':' in item or item in ('missing','error')
                or not identity.startswith('incomplete:') or identity.count(':')!=2 or not identity.endswith(':'+item)):continue
            if not valid_group_record(row,identity,{item}):continue
            possible[identity]={item}
        if possible:
            item_ids=sorted({item for values in possible.values() for item in values})
            saved={row['id']:row for row in store.db.execute('SELECT id,incomplete_group_unknown,incomplete_group_conflict FROM publications WHERE id=ANY(%s)',(item_ids,))}
            possible={identity:items for identity,items in possible.items() if all(item in saved and not saved[item]['incomplete_group_unknown'] and not saved[item]['incomplete_group_conflict'] for item in items)}
        if possible:
            with store.db:
                begin_write(store.db)
                sync_groups_time_batch(store,list(possible),store.wall_clock(),possible)
        cursor=rows[-1]['id']
        store.fault('experimental_incomplete_legacy_problem_batch')
    with store.db:
        store.db.execute("INSERT INTO state(key,value) VALUES ('experimental-incomplete-problems-v1','true') ON CONFLICT DO NOTHING")


def verified_reservation_rows(store: Any, rows: list[Any]) -> dict[str,dict[str,Any]]:
    """Validate both ownership pointers; preserve all unrecognised legacy rows."""
    candidates={}
    for row in rows:
        try:body=json.loads(row['body'])
        except (ValueError,TypeError):continue
        if (not isinstance(body,dict) or body.get('reservation') is not True or body.get('code')!='material-incomplete'
            or body.get('operation')!='producer:incomplete-reservation' or not isinstance(body.get('incomplete_group_identity'),str)
            or row['identity']!='incomplete:reservation:'+row['id']):continue
        from oko.storage.problems import _readable_timestamp
        if not _readable_timestamp(body.get('deadline')) or body['deadline']!=row['incomplete_due'] or row['finished']!=-62135596800 or not valid_incomplete_projection(body.get('future')) or not body['future']['objects']:continue
        key=body['incomplete_group_identity']
        candidates[row['id']]={'id':row['id'],'key':key,'deadline':body['deadline'],'future':body['future'],'pointers':reservation_pointers(key),'reverse':row['identity']}
    state_keys=sorted({value for record in candidates.values() for value in [*record['pointers'],record['reverse']]})
    states={}
    for start in range(0,len(state_keys),400):
        states.update({r['key']:r['value'] for r in store.db.execute('SELECT key,value FROM state WHERE key=ANY(%s)',(state_keys[start:start+400],))})
    valid={}
    for identity,record in candidates.items():
        if states.get(record['reverse'])!=record['key']:continue
        matches=0
        for pointer in record['pointers']:
            try:metadata=json.loads(states.get(pointer,''))
            except (ValueError,TypeError):continue
            if metadata=={'group':record['key'],'id':identity}:matches+=1
        if matches==1:valid[identity]=record
    return valid


def virtual_due_groups(store: Any, now: float) -> list[dict[str,Any]]:
    rows=store.db.execute("SELECT id,identity,body,created,finished,incomplete_due FROM reader_problems WHERE finished IS NOT NULL AND incomplete_due IS NOT NULL AND incomplete_due<=%s ORDER BY incomplete_due,id COLLATE \"C\"",(now,)).fetchall()
    valid=verified_reservation_rows(store,list(rows))
    groups: dict[str,list[dict[str,Any]]]={}
    for saved in valid.values():groups.setdefault(saved['key'],[]).append(saved)
    # Duplicate pointers for a group are ambiguous and must not be claimed.
    singles={key:values[0] for key,values in groups.items() if len(values)==1}
    keys=sorted(singles)
    result: list[dict[str,Any]]=[]
    from oko.storage.problems import _readable_timestamp,_legacy_problem_id
    for row in rows:
        if row['id'] in valid:continue
        result.append(dict(id=row['id'] or _legacy_problem_id(row['identity']),code='material-incomplete',section='object',
            title='Сведения о неполном материале недоступны',consequence='Исходная запись сохранена.',
            next_step='Проверьте журнал диагностики.',objects=[],action=None,operation='producer:incomplete-legacy',
            created_at=row['created'] if _readable_timestamp(row['created']) else now,state='current',finished_at=None,outcome=None,next_at=None))
    for start in range(0,len(keys),100):
        batch=keys[start:start+100]
        projected={key:dict(singles[key]['future']) for key in batch}
        existing={r['identity']:r for r in store.db.execute('SELECT id,identity,body,created FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(batch,))}
        indexed: dict[str,set[str]]={}
        for row in store.db.execute('SELECT id,incomplete_group_key FROM publications WHERE incomplete_group_key COLLATE "C"=ANY(%s)',(batch,)):
            indexed.setdefault(row['incomplete_group_key'],set()).add(row['id'])
        for key in batch:
            candidate=projected.get(key)
            if not candidate:continue
            if key in existing and valid_group_record(existing[key],key,indexed.get(key,set())):continue
            candidate.update(id=singles[key]['id'],created_at=singles[key]['deadline'],finished_at=None,outcome=None)
            result.append(candidate)
    return result


def valid_incomplete_projection(value: Any) -> bool:
    if not isinstance(value,dict) or value.get('code')!='material-incomplete':return False
    fields={'code','section','title','consequence','next_step','objects','action','operation','state','next_at','reason','incomplete_group_identity'}
    if set(value)-fields:return False
    if value.get('section')!='object' or value.get('state') not in ('current','retrying') or value.get('next_at') is not None or value.get('action') is not None:return False
    if any(not isinstance(value.get(field),str) for field in ('title','consequence','next_step')):return False
    if value.get('operation') not in (None,'producer:status','producer:incomplete'):return False
    if 'reason' in value and not isinstance(value['reason'],str):return False
    if 'request_id' in value:return False
    objects=value.get('objects')
    return isinstance(objects,list) and all(isinstance(obj,dict) and obj.get('kind')=='material' and isinstance(obj.get('id'),str) and bool(obj['id']) for obj in objects)


def due_snapshot_materials(store: Any, keys: list[str], now: float) -> set[str]:
    """Capture already-visible virtual materials before any group batch closes."""
    result: set[str]=set()
    for start in range(0,len(keys),100):
        batch=keys[start:start+100]
        pointers=[pointer for key in batch for pointer in reservation_pointers(key)]
        identities=set()
        for row in store.db.execute('SELECT value FROM state WHERE key=ANY(%s)',(pointers,)):
            try:metadata=json.loads(row['value'])
            except (TypeError,ValueError):continue
            if isinstance(metadata,dict) and metadata.get('group') in batch and isinstance(metadata.get('id'),str):identities.add(metadata['id'])
        if not identities:continue
        records=store.db.execute('SELECT id,identity,body,finished,incomplete_due FROM reader_problems WHERE id=ANY(%s) AND finished IS NOT NULL AND incomplete_due IS NOT NULL AND incomplete_due<=%s',(sorted(identities),now)).fetchall()
        verified=verified_reservation_rows(store,list(records))
        for saved in verified.values():
            result.update(obj['id'] for obj in saved['future']['objects'] if isinstance(obj,dict) and obj.get('kind')=='material' and isinstance(obj.get('id'),str))
    return result



def scrub_materialized(store: Any, identities: set[str], forgotten: set[str], now: float) -> None:
    """Scrub only newly materialized incomplete rows, preserving unrelated history."""
    from oko.storage.reader_status import changed as status_changed
    ordered=sorted(identities)
    for start in range(0,len(ordered),400):
        rows=store.db.execute('SELECT id,body,finished,outcome FROM reader_problems WHERE id=ANY(%s)',(ordered[start:start+400],)).fetchall()
        updates=[]
        for row in rows:
            try:body=json.loads(row['body'])
            except (TypeError,ValueError):continue
            if not isinstance(body,dict) or body.get('code')!='material-incomplete' or not isinstance(body.get('objects'),list):continue
            before=body['objects']
            after=[obj for obj in before if not (isinstance(obj,dict) and obj.get('kind')=='material' and obj.get('id') in forgotten)]
            if len(before)==len(after):continue
            body['objects']=after
            if not after:body['action']=None
            finished=(row['finished'] if row['finished'] is not None else now) if not after else row['finished']
            outcome='irrelevant' if not after else row['outcome']
            updates.append((row['id'],json.dumps(body),finished,outcome))
        if updates:
            store.db.execute("""UPDATE reader_problems p SET body=s.body,finished=s.finished,outcome=s.outcome,
                incomplete_due=CASE WHEN s.finished IS NOT NULL THEN NULL ELSE p.incomplete_due END,
                incomplete_future=CASE WHEN s.finished IS NOT NULL THEN NULL ELSE p.incomplete_future END,
                incomplete_closes=CASE WHEN s.finished IS NOT NULL THEN false ELSE p.incomplete_closes END
                FROM unnest(%s::text[],%s::text[],%s::double precision[],%s::text[]) s(id,body,finished,outcome) WHERE p.id=s.id""",tuple([r[i] for r in updates] for i in range(4)))
            status_changed(store.db)

def candidate_body(candidate: dict[str,Any], key: str, identity: str) -> dict[str,Any]:
    body={k:v for k,v in candidate.items() if k not in ('id','created_at','finished_at','outcome')}
    body['operation']='producer:incomplete'
    if identity!=key:body['incomplete_group_identity']=key
    return body


def current_schedule(row: Any) -> dict[str,Any] | None:
    from oko.storage.problems import _readable_timestamp
    if row['finished'] is not None or not _readable_timestamp(row['incomplete_due']) or not isinstance(row['incomplete_future'],str):return None
    try:future=json.loads(row['incomplete_future'])
    except (ValueError,TypeError):return None
    if (not isinstance(future,dict) or set(future)!={'mode','group','id','identity','deadline','before','future'}
        or future['mode'] not in ('update','close') or not isinstance(future['group'],str)
        or future['id']!=row['id'] or future['identity']!=row['identity'] or future['deadline']!=row['incomplete_due']
        or not isinstance(future['before'],str) or row['incomplete_closes']!=(future['mode']=='close')):return None
    try:before=json.loads(future['before'])
    except (ValueError,TypeError):return None
    if not valid_incomplete_projection(before):return None
    if before.get('incomplete_group_identity',row['identity'])!=future['group']:return None
    if future['mode']=='close' and future['future'] is not None:return None
    if future['mode']=='update' and not valid_incomplete_projection(future['future']):return None
    if future['mode']=='update' and future['future'].get('incomplete_group_identity',row['identity'])!=future['group']:return None
    return future


def schedule_current_groups(store: Any, keys: list[str], now: float, deadline: float, present_hint: set[str] | None = None) -> None:
    if not keys or deadline<=now:return
    present=present_hint if present_hint is not None else set(group_candidates(store,keys,now))
    if not present:return
    future=group_candidates(store,sorted(present),deadline)
    canonical={r['identity']:r for r in store.db.execute('SELECT id,identity,body,created,finished,incomplete_due,incomplete_future,incomplete_closes FROM reader_problems WHERE identity=ANY(%s) AND finished IS NULL',(list(present),))}
    isolated: dict[str,list[Any]]={}
    for row in store.db.execute("SELECT r.*,k.group_key FROM unnest(%s::text[]) AS k(group_key) CROSS JOIN LATERAL (SELECT id,identity,body,created,finished,incomplete_due,incomplete_future,incomplete_closes FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:incomplete' AND body::jsonb->>'incomplete_group_identity'=k.group_key LIMIT 2) AS r",(list(present),)):
        isolated.setdefault(row['group_key'],[]).append(row)
    members: dict[str,set[str]]={}
    for row in store.db.execute('SELECT id,incomplete_group_key FROM publications WHERE incomplete_group_key COLLATE "C"=ANY(%s)',(list(present),)):
        members.setdefault(row['incomplete_group_key'],set()).add(row['id'])
    updates: list[tuple[str,float | None,str | None,bool]]=[]
    for key in present:
        row=canonical.get(key)
        row=row if row is not None and valid_group_record(row,key,members.get(key,set())) else None
        alternates=isolated.get(key,[])
        if len(alternates)>1:continue
        if alternates and valid_group_record(alternates[0],key,members.get(key,set())):
            if row is not None:continue
            row=alternates[0]
        if row is None:continue
        next_body=candidate_body(future[key],key,row['identity']) if key in future else None
        current_body=json.loads(row['body'])
        if next_body==current_body:
            if row['incomplete_due'] is not None:updates.append((row['id'],None,None,False))
            continue
        schedule=dict(mode='update' if next_body is not None else 'close',group=key,id=row['id'],identity=row['identity'],deadline=deadline,before=row['body'],future=next_body)
        encoded=json.dumps(schedule)
        if row['incomplete_due']!=deadline or row['incomplete_future']!=encoded or row['incomplete_closes']!=(next_body is None):updates.append((row['id'],deadline,encoded,next_body is None))
    if updates:
        store.db.execute('''UPDATE reader_problems p SET incomplete_due=s.deadline,incomplete_future=s.future,incomplete_closes=s.closes FROM
            unnest(%s::text[],%s::double precision[],%s::text[],%s::boolean[]) s(id,deadline,future,closes) WHERE p.id=s.id AND p.finished IS NULL''',tuple([r[i] for r in updates] for i in range(4)))


def apply_current_schedules(store: Any, rows: list[Any], now: float) -> set[str]:
    updates=[];closes=[];conflicts=[];redacted: set[str]=set()
    for row in rows:
        future=current_schedule(row)
        if future is None or future['deadline']>now:continue
        if row['body']!=future['before']:
            try:before=json.loads(future['before']);after=json.loads(row['body'])
            except (ValueError,TypeError):
                conflicts.append(future['group']);continue
            original=before.get('objects') if isinstance(before,dict) else None
            surviving=after.get('objects') if isinstance(after,dict) else None
            if not isinstance(original,list) or not isinstance(surviving,list):
                conflicts.append(future['group']);continue
            prior_rest={k:v for k,v in before.items() if k not in ('objects','action')}
            new_rest={k:v for k,v in after.items() if k not in ('objects','action')}
            if prior_rest!=new_rest or after.get('action') is not None or any(obj not in original for obj in surviving):
                conflicts.append(future['group']);continue
            redacted.update(obj['id'] for obj in original if obj not in surviving and isinstance(obj,dict) and obj.get('kind')=='material' and isinstance(obj.get('id'),str))
        if future['mode']=='close':closes.append((row['id'],future['deadline'],row['incomplete_future']))
        else:updates.append((row['id'],json.dumps(future['future']),row['incomplete_future']))
    flag_reservation_conflicts(store,conflicts)
    if updates:
        changed=store.db.execute('''UPDATE reader_problems p SET body=s.body,incomplete_due=NULL,incomplete_future=NULL,incomplete_closes=false FROM
            unnest(%s::text[],%s::text[],%s::text[]) s(id,body,expected) WHERE p.id=s.id AND p.finished IS NULL AND p.incomplete_future=s.expected''',tuple([r[i] for r in updates] for i in range(3))).rowcount
        if changed!=len(updates):raise RuntimeError('Incomplete scheduled update changed')
    if closes:
        changed=store.db.execute('''UPDATE reader_problems p SET finished=s.deadline,outcome='resolved',incomplete_due=NULL,incomplete_future=NULL,incomplete_closes=false FROM
            unnest(%s::text[],%s::double precision[],%s::text[]) s(id,deadline,expected) WHERE p.id=s.id AND p.finished IS NULL AND p.incomplete_future=s.expected''',tuple([r[i] for r in closes] for i in range(3))).rowcount
        if changed!=len(closes):raise RuntimeError('Incomplete scheduled closure changed')
        invisible=[row[0] for row in closes if row[1]<=now-86400]
        if invisible:store.db.execute('DELETE FROM reader_problems WHERE id=ANY(%s) AND finished<=%s AND incomplete_due IS NULL',(invisible,now-86400))
    ids={r[0] for r in updates+closes}
    manager=getattr(store,'incomplete_draft',None)
    if manager is not None and manager.flushing:manager.materialize(ids)
    if redacted:scrub_materialized(store,ids,redacted,now)
    return ids


def scheduled_group_rows(store: Any, keys: list[str]) -> list[Any]:
    """Inspect at most two isolated candidates per key; retain ambiguous state."""
    result=[]
    for start in range(0,len(keys),100):
        batch=keys[start:start+100]
        columns='id,identity,body,finished,incomplete_due,incomplete_future,incomplete_closes'
        canonical={row['identity']:row for row in store.db.execute(f'SELECT {columns} FROM reader_problems WHERE finished IS NULL AND identity=ANY(%s)',(batch,))}
        isolated: dict[str,list[Any]]={}
        for row in store.db.execute(f"SELECT r.*,k.group_key FROM unnest(%s::text[]) k(group_key) CROSS JOIN LATERAL (SELECT {columns} FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'='producer:incomplete' AND body::jsonb->>'incomplete_group_identity'=k.group_key LIMIT 2) r",(batch,)):
            isolated.setdefault(row['group_key'],[]).append(row)
        for key in batch:
            choices=isolated.get(key,[])
            row=canonical.get(key)
            if len(choices)>1 or (row is not None and choices):continue
            row=row if row is not None else (choices[0] if choices else None)
            if row is not None:
                schedule=current_schedule(row)
                if schedule is not None and schedule['group']==key:result.append(row)
    return result


def apply_due_current_for_keys(store: Any, keys: list[str], now: float) -> None:
    if not store.db.execute("SELECT 1 FROM reader_problems WHERE finished IS NULL AND incomplete_due IS NOT NULL AND incomplete_due<=%s LIMIT 1",(now,)).fetchone():return
    rows=scheduled_group_rows(store,keys)
    if rows:apply_current_schedules(store,rows,now)


def refresh_youtube_flags(store: Any, roots: list[str]) -> None:
    """Refresh the derived time-sensitive fetch flag only for affected roots."""
    from oko.storage.publication_owners import owner_expr
    targets=generic_job_targets(store.db,roots)
    for start in range(0,len(targets),400):
        rows=store.db.execute(f"""SELECT j.id,coalesce(bool_or(j.problem_code IS NULL AND j.error IS NOT NULL
                AND j.status IN ('pending','running','failed','unavailable','blocked') AND f.kind='youtube'
                AND CASE WHEN root.id IS NOT NULL THEN root.incomplete_group_key IS NOT NULL ELSE p.incomplete_group_key IS NOT NULL END),false) AS sensitive
            FROM (SELECT * FROM jobs WHERE kind='fetch' AND item=ANY(%s) OFFSET 0) j
            LEFT JOIN publications p ON p.id=j.item
            LEFT JOIN publication_links link ON link.target_publication_id=p.id
            LEFT JOIN publications root ON root.id=link.source_publication_id AND root.viewed=0
            LEFT JOIN feeds f ON f.id=coalesce({owner_expr('p')},{owner_expr('root')},j.feed_id)
            GROUP BY j.id""",(targets[start:start+400],)).fetchall()
        if rows:
            store.db.execute('UPDATE jobs j SET incomplete_youtube_sensitive=s.sensitive FROM unnest(%s::bigint[],%s::boolean[]) s(id,sensitive) WHERE j.id=s.id AND j.incomplete_youtube_sensitive IS DISTINCT FROM s.sensitive',([row['id'] for row in rows],[row['sensitive'] for row in rows]))
