"""Reconcile affected settings in bounded read phases and batched writes."""
import json
from typing import TYPE_CHECKING

from oko.domain.processing import Kind
from oko.storage import material_read, processing_input
from oko.storage.job_writes import execute as job_write
from oko.storage.processing_state import ProcessingRepository
from oko.storage.reading import reading_snapshot

if TYPE_CHECKING:
    from oko.storage.store import Store


def reconcile_settings(store: 'Store', identities: list[str]) -> None:
    """Update stage Jobs and publication statuses in the settings transaction."""
    if not store.db.in_transaction:
        raise RuntimeError('Settings reconciliation requires an owned transaction')
    ordered = sorted(set(identities))
    affected: list[int]=[]
    for start in range(0,len(ordered),400):
        affected.extend(row['id'] for row in store.db.execute("SELECT id FROM jobs WHERE kind IN ('filter','theses') AND item=ANY(%s) AND (processor_visible OR problem_code='processor-config')",(ordered[start:start+400],)))
    processor_previous=store.problems.prepare_processor_change(affected,store.wall_clock())
    for start in range(0,len(ordered),400):
        batch = ordered[start:start+400]
        for kind in ('filter','theses'):
            _stage(store,batch,kind)
        with material_read.material_inputs(store.db,set(batch)), reading_snapshot():
            changes = [(item,status) for item in batch if (status := store.publications._status_change(item)) is not None]
        if changes:
            store.db.execute("""UPDATE publications i SET status=v.status FROM unnest(%s::text[],%s::text[]) AS v(id,status)
                WHERE i.id=v.id AND i.status IS DISTINCT FROM v.status""", ([r[0] for r in changes],[r[1] for r in changes]))
    if affected:
        store.problems.finish_processor_change(affected,processor_previous,store.wall_clock())


def _stage(store: 'Store', batch: list[str], kind: Kind) -> None:
    repository = ProcessingRepository(store)
    unblock: list[str] = []
    block: list[str] = []
    cancel: list[str] = []
    upsert: list[tuple[str,str,str,str]] = []
    with material_read.material_inputs(store.db,set(batch)), reading_snapshot():
        for item in batch:
            row = material_read.item(store.db,item)
            if not row or row['viewed'] or row['platform'] is None or row['feed_id'] is None or not row['complete']:
                continue
            operation = store.filters if kind=='filter' else store.processing.theses
            signature = operation.signature(item)
            revision = repository.revision(item)
            jobs = material_read.jobs(store.db,item)
            job = jobs.get(kind)
            snapshot = job['input_snapshot'] if job else None
            previous = snapshot.get('signature') if isinstance(snapshot,dict) else None
            enabled = processing_input.stage_enabled(store,item,kind) if signature is not None else False
            if job and job['status']=='done' and previous is not None and previous==signature:
                continue
            if previous is not None and previous==signature and job and (job['status']=='running' or json.loads(job['payload']).get('input_revision')==revision):
                if job['status']=='blocked' and enabled:
                    if json.loads(job['payload']).get('preclaim_blocked') and not processing_input.can_start(
                            store,item,processing_input.processing_feed(store,item,kind)):
                        continue
                    unblock.append(item)
                elif job['status']=='pending' and not enabled:
                    block.append(item)
                continue
            if signature is None and previous is None and (job is None or job['status']=='cancelled'):
                continue
            if signature is None:
                filtering = jobs.get('filter')
                if kind=='theses' and job and job['status']=='blocked' and processing_input.stage_enabled(store,item,'theses') and filtering and filtering['status'] in ('pending','running'):
                    continue
                cancel.append(item)
            else:
                satisfied = repository.satisfied(item,kind)
                status = 'done' if satisfied else 'pending' if enabled else 'blocked'
                upsert.append((item,status,json.dumps(dict(input_revision=revision)),json.dumps(dict(signature=signature))))
    changed=set(unblock)|set(block)|set(cancel)|{row[0] for row in upsert}
    from oko.domain.collection import CollectionEffects
    from oko.storage.publication_links import capture_processing_problems
    effects=CollectionEffects()
    if changed:capture_processing_problems(store,effects,changed,kinds=(kind,))
    if unblock:
        job_write(store.db, """UPDATE jobs SET status='pending',
            error=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE error END,
            problem_code=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE problem_code END,
            payload=(payload::jsonb-'preclaim_blocked')::text WHERE kind=%s AND item=ANY(%s) AND status='blocked'""", (kind,unblock))
    if block:
        job_write(store.db,"UPDATE jobs SET status='blocked' WHERE kind=%s AND item=ANY(%s) AND status='pending'", (kind,block))
    if cancel:
        job_write(store.db,"UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL,problem_code=NULL,input_snapshot=NULL WHERE kind=%s AND item=ANY(%s)", (kind,cancel))
    if upsert:
        wall = store.wall_clock()
        job_write(store.db,"""INSERT INTO jobs(kind,item,status,payload,input_snapshot)
            SELECT %s,v.item,v.status,v.payload,v.snapshot::jsonb
            FROM unnest(%s::text[],%s::text[],%s::text[],%s::text[]) AS v(item,status,payload,snapshot)
            ON CONFLICT(kind,item) DO UPDATE SET status=excluded.status,payload=excluded.payload,input_snapshot=excluded.input_snapshot,
                generation=jobs.generation+1,
                next=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.next ELSE 0 END,
                error=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.error ELSE NULL END,
                problem_code=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.problem_code ELSE NULL END""",
            (kind,*([row[index] for row in upsert] for index in range(4)),wall,wall,wall))

    if effects.processing_problems_before:
        store.problems.sync_processing_problems(effects.processing_problems_before,store.wall_clock())
