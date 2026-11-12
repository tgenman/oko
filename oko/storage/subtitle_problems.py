"""Event-owned subtitle episodes, including reserved future occurrences."""
from typing import TYPE_CHECKING,Any
from collections.abc import Sequence
import json,uuid
from oko.storage.reader_status import changed as status_changed
from oko.storage.reservation_ids import cleanup_pointers as cleanup_reservation_pointers, protect_rows as protect_reservation_rows

if TYPE_CHECKING:
    from oko.storage.store import Store


def new_reservation_id() -> str:
    return uuid.uuid4().hex


def sync_timer(store: 'Store', now: float, job_ids: list[int] | None = None, *, new_ids: set[str] | None = None, previous: list[dict[str,Any]] | None = None) -> None:
    from oko.storage.reservation_timers import sync_timer as sync_reservations
    sync_reservations(store,now,job_ids,new_ids,previous,subtitle=True)


def matches_active_snapshot(store: 'Store', row: Any, candidate: dict[str,Any]) -> bool:
    try:body=json.loads(row['body'])
    except (TypeError,ValueError):return False
    expected={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome')}
    if row['identity']!=candidate['id']:expected['media_job_identity']=candidate['id']
    return body==expected and store.problems.media_record_matches(row,candidate)


def matches_snapshot(store: 'Store', row: Any, candidate: dict[str,Any], pointer: str | None = None) -> bool:
    body=valid(row)
    if not body:return False
    if pointer is None:
        saved=store.db.execute('SELECT value FROM state WHERE key=%s',(row['identity'],)).fetchone()
        pointer=saved['value'] if saved else None
    if pointer!=json.dumps(['oko-subtitle-v1',body['media_job_id'],body['subtitle_canonical']]):return False
    expected={key:value for key,value in candidate.items() if key not in ('id','created_at','finished_at','outcome')}
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
    from oko.storage.reservation_timers import activate_due as shared_activate_due
    return shared_activate_due(store,now,job_id,cursor,subtitle=True)


def drain_due(store: 'Store', now: float) -> None:
    cursor=None
    while True:
        cursor=activate_due(store,now,cursor=cursor)
        if cursor is None:return


def cancel_reservations(store: 'Store', job_id: int, previous: list[dict[str,Any]]) -> None:
    from oko.storage.reservation_timers import cancel_reservations as shared_cancel_reservations
    shared_cancel_reservations(store,job_id,previous,subtitle=True)


def verified(store: 'Store', rows: list[Any], now: float, *, projected_out: dict[tuple[int,str],dict[str,Any]] | None = None) -> dict[str,dict[str,Any]]:
    from oko.storage.reservation_timers import verified as shared_verified
    return shared_verified(store,rows,now,projected_out=projected_out,subtitle=True)


def transfer_reservation(store: 'Store', job_id: int, previous: list[dict[str,Any]], current: list[dict[str,Any]], new_ids: set[str]) -> set[str]:
    from oko.storage.reservation_timers import transfer_reservation as shared_transfer_reservation
    return shared_transfer_reservation(store,job_id,previous,current,new_ids,subtitle=True)




def protect_isolated(store: 'Store', field: str, canonical: str) -> None:
    """Preserve exact legacy collisions without trusting the stored marker."""
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
        protect_rows(store,rows)
        cursor=(rows[-1]['page_hash'],rows[-1]['id'])
        store.fault('subtitle_isolated_protection_batch')


def cleanup_pointers(store: 'Store', cursor: str | None) -> tuple[str | None,bool]:
    return cleanup_reservation_pointers(store,cursor,'subtitle:reservation:','oko-subtitle-v1')


def protect_rows(store: 'Store', rows: Sequence[Any]) -> None:
    protect_reservation_rows(store,rows,'subtitle')
