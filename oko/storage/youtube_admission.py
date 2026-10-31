"""Isolated occurrence prototype; does not yet own status or event projection."""
import json
import uuid
from typing import Any
from oko.storage.reader_status import changed as status_changed

IDENTITY='youtube:admission'


def valid(row: Any) -> bool:
    if row['origin']!='background':return False
    try:body=json.loads(row['body'])
    except (TypeError,ValueError):return False
    if not isinstance(body,dict) or body.get('code')!='youtube-admission':return False
    fields={'code','section','title','consequence','next_step','objects','action','operation','state','next_at','youtube_admission_owner','youtube_admission_time_unknown'}
    if set(body)-fields:return False
    from oko.storage.problems import _readable_timestamp
    if not _readable_timestamp(row['created']):return False
    if row['finished'] is None and row['outcome'] is not None:return False
    if row['finished'] is not None and (not _readable_timestamp(row['finished']) or row['outcome'] not in ('resolved','irrelevant')):return False
    unknown_time=body.get('youtube_admission_time_unknown',False)
    if not isinstance(unknown_time,bool):return False
    if unknown_time:
        if body.get('next_at') is not None:return False
    elif not _readable_timestamp(body.get('next_at')):return False
    due=row['youtube_admission_due']
    if due is not None and (not _readable_timestamp(due) or (not unknown_time and due>body['next_at'])):return False
    if body.get('operation') not in (None,'producer:status','producer:youtube-admission'):return False
    if body.get('youtube_admission_owner') not in (None,IDENTITY):return False
    if body.get('section')!='wait' or body.get('state')!='retrying' or body.get('action') is not None:return False
    if any(not isinstance(body.get(k),str) for k in ('title','consequence','next_step')):return False
    objects=body.get('objects')
    if not isinstance(objects,list) or any(not isinstance(obj,dict) or set(obj)!={'kind','id'} or obj['kind']!='material' or not isinstance(obj['id'],str) or not obj['id'] for obj in objects):return False
    return len({obj['id'] for obj in objects})==len(objects)


def current(store: Any) -> Any:
    pointer=store.db.execute('SELECT youtube_admission_problem_id FROM youtube_access WHERE id=1').fetchone()
    identity=pointer['youtube_admission_problem_id'] if pointer else None
    if identity is not None:
        row=store.db.execute('SELECT * FROM reader_problems WHERE id=%s',(identity,)).fetchone()
        if row is None or not valid(row):raise RuntimeError('Unrecognized YouTube admission pointer')
        body=json.loads(row['body'])
        if row['identity']!=IDENTITY and (not row['identity'].startswith('youtube:admission:isolated:') or body.get('youtube_admission_owner')!=IDENTITY or body.get('operation')!='producer:youtube-admission'):
            raise RuntimeError('Unrecognized YouTube admission pointer')
        return row if row['finished'] is None else None
    row=store.db.execute('SELECT * FROM reader_problems WHERE identity=%s AND finished IS NULL',(IDENTITY,)).fetchone()
    if row is not None and valid(row):
        body=json.loads(row['body'])
        if body.get('operation') in (None,'producer:status') and 'youtube_admission_owner' not in body and 'youtube_admission_time_unknown' not in body and row['youtube_admission_due'] is None:return row
    return None


def release_finished_pointer(store: Any) -> None:
    """Clear only a verified pointer to an already-finished occurrence."""
    pointer=store.db.execute('SELECT youtube_admission_problem_id FROM youtube_access WHERE id=1').fetchone()
    identity=pointer['youtube_admission_problem_id'] if pointer else None
    if identity is None:return
    row=store.db.execute('SELECT * FROM reader_problems WHERE id=%s',(identity,)).fetchone()
    if row is None or not valid(row):raise RuntimeError('Unrecognized YouTube admission pointer')
    body=json.loads(row['body'])
    if row['identity']!=IDENTITY and (not row['identity'].startswith('youtube:admission:isolated:') or body.get('youtube_admission_owner')!=IDENTITY or body.get('operation')!='producer:youtube-admission'):
        raise RuntimeError('Unrecognized YouTube admission pointer')
    if row['finished'] is not None:
        store.db.execute('UPDATE youtube_access SET youtube_admission_problem_id=NULL WHERE id=1 AND youtube_admission_problem_id=%s',(identity,))


def candidate(objects: list[str],next_at: float | None,unknown: bool = False,unknown_time: bool = False) -> dict[str,Any]:
    unique=sorted(set(objects))
    body=dict(code='youtube-admission',section='wait',title='YouTube ограничил запросы',
        consequence=f'Ожидают субтитров: {len(unique)}.' + (' Для части заданий материал не определён.' if unknown else '') + (' Время следующей попытки недоступно.' if unknown_time else ''),next_step='Око повторит получение после паузы.',
        objects=[dict(kind='material',id=item) for item in unique],action=None,
        operation='producer:youtube-admission',state='retrying',next_at=next_at)
    if unknown_time:body['youtube_admission_time_unknown']=True
    return body


def sync(store: Any, objects: list[str],deadline: float | None,now: float, *, next_at: float | None = None, unknown: bool = False, time_unknown: bool = False) -> str:
    """Persist a provided projection atomically; caller must own event transaction."""
    if not store.db.in_transaction:raise RuntimeError('Admission event requires a transaction')
    from oko.storage import memberships
    memberships.lock_graph(store.db)
    release_finished_pointer(store)
    from oko.storage.problems import _readable_timestamp
    safe_due=deadline if _readable_timestamp(deadline) else None
    display_next=max(safe_due,next_at if next_at is not None else safe_due) if safe_due is not None else None
    unknown_time=time_unknown or not _readable_timestamp(display_next)
    safe_display=display_next if not unknown_time else None
    row=current(store)
    if row is not None and row['youtube_admission_due'] is not None and row['youtube_admission_due']<=now:
        store.db.execute("UPDATE reader_problems SET finished=youtube_admission_due,outcome='resolved',youtube_admission_due=NULL WHERE id=%s AND finished IS NULL",(row['id'],))
        store.db.execute('UPDATE youtube_access SET youtube_admission_problem_id=NULL WHERE id=1 AND youtube_admission_problem_id=%s',(row['id'],))
        row=None
    if row is not None:
        body=candidate(objects,safe_display,unknown,unknown_time)
        if row['identity']!=IDENTITY:body['youtube_admission_owner']=IDENTITY
        encoded=json.dumps(body)
        store.db.execute('UPDATE youtube_access SET youtube_admission_problem_id=%s WHERE id=1 AND youtube_admission_problem_id IS NULL',(row['id'],))
        if row['body']!=encoded or row['youtube_admission_due']!=safe_due:
            store.db.execute('UPDATE reader_problems SET body=%s,youtube_admission_due=%s WHERE id=%s',(encoded,safe_due,row['id']))
            status_changed(store.db)
        return str(row['id'])
    occupied=store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(IDENTITY,)).fetchone()
    identity=IDENTITY if occupied is None else None
    if identity is None:
        for _ in range(3):
            trial='youtube:admission:isolated:'+uuid.uuid4().hex
            if not store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(trial,)).fetchone():
                identity=trial;break
        if identity is None:raise RuntimeError('Unable to allocate YouTube admission identity')
    body=candidate(objects,safe_display,unknown,unknown_time)
    if identity!=IDENTITY:body['youtube_admission_owner']=IDENTITY
    for _ in range(3):
        occurrence=uuid.uuid4().hex
        inserted=store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,youtube_admission_due) VALUES (%s,%s,'background',%s,%s,%s) ON CONFLICT (id) DO NOTHING RETURNING id",(occurrence,identity,json.dumps(body),now,safe_due)).fetchone()
        if inserted:
            attached=store.db.execute('UPDATE youtube_access SET youtube_admission_problem_id=%s WHERE id=1 AND youtube_admission_problem_id IS NULL',(occurrence,)).rowcount
            if attached!=1:raise RuntimeError('YouTube admission pointer changed during allocation')
            status_changed(store.db)
            return occurrence
    raise RuntimeError('Unable to allocate YouTube admission occurrence')


def project(row: Any, now: float) -> dict[str,Any]:
    """Read an occurrence at time now without updating persistent state."""
    if not valid(row):raise ValueError('Unrecognized YouTube admission occurrence')
    body: dict[str,Any]=json.loads(row['body'])
    due=row['youtube_admission_due']
    if row['finished'] is None and (due is None or not isinstance(due,(int,float)) or due!=due):
        raise ValueError('Unrecognized YouTube admission deadline')
    body.pop('youtube_admission_owner',None)
    body.pop('youtube_admission_time_unknown',None)
    finished=due if row['finished'] is None and due is not None and now>=due else row['finished']
    body.update(id=row['id'],created_at=row['created'],finished_at=finished,
                outcome=('resolved' if row['finished'] is None and finished is not None else row['outcome']),
                state='finished' if finished is not None else 'retrying')
    return body


def finish_due(store: Any,now: float) -> bool:
    """Persist an already-visible time transition without advancing revision."""
    if not store.db.in_transaction:raise RuntimeError('Admission tick requires a transaction')
    from oko.storage import memberships
    memberships.lock_graph(store.db)
    release_finished_pointer(store)
    row=current(store)
    if row is None or row['youtube_admission_due'] is None or row['youtube_admission_due']>now:return False
    if not valid(row):return False
    if row['youtube_admission_due']<=now-86400:
        changed=store.db.execute('DELETE FROM reader_problems WHERE id=%s AND finished IS NULL AND youtube_admission_due=%s',(row['id'],row['youtube_admission_due'])).rowcount
        if changed:store.db.execute('UPDATE youtube_access SET youtube_admission_problem_id=NULL WHERE id=1 AND youtube_admission_problem_id=%s',(row['id'],))
        return bool(changed)
    changed=store.db.execute("UPDATE reader_problems SET finished=youtube_admission_due,outcome='resolved',youtube_admission_due=NULL WHERE id=%s AND finished IS NULL AND youtube_admission_due=%s",(row['id'],row['youtube_admission_due'])).rowcount
    if changed!=1:raise RuntimeError('YouTube admission transition changed')
    store.db.execute('UPDATE youtube_access SET youtube_admission_problem_id=NULL WHERE id=1 AND youtube_admission_problem_id=%s',(row['id'],))
    return True


def close_current(store: Any,now: float) -> bool:
    """Close early completion at its event time; due closures are time-owned."""
    if not store.db.in_transaction:raise RuntimeError('Admission close requires a transaction')
    from oko.storage import memberships
    memberships.lock_graph(store.db)
    release_finished_pointer(store)
    row=current(store)
    if row is None:return False
    due=row['youtube_admission_due']
    finished=due if due is not None and due<=now else now
    changed=store.db.execute("UPDATE reader_problems SET finished=%s,outcome='resolved',youtube_admission_due=NULL WHERE id=%s AND finished IS NULL",(finished,row['id'])).rowcount
    if changed!=1:raise RuntimeError('YouTube admission occurrence changed')
    store.db.execute('UPDATE youtube_access SET youtube_admission_problem_id=NULL WHERE id=1 AND youtube_admission_problem_id=%s',(row['id'],))
    if due is None or due>now:status_changed(store.db)
    return True
