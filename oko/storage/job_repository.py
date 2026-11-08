"""Attempt identity, conditional transitions and saved execution parameters."""
from oko.storage.publication_document import stored_content
from oko.storage.reading import saved_content
from oko.storage.job_writes import execute as job_write
from oko.storage.publication_owners import owned_ids, owner_expr
from oko.storage.database import expect_row, Database, Row
import json
from oko.domain.collection import CollectionEffects
from oko.storage.collection_diagnostics import CollectionDiagnostics
from typing import Any
from dataclasses import dataclass
from collections.abc import Callable,Iterator
from contextlib import contextmanager
from oko.storage.transaction import transaction
from oko.storage.reader_status import changed as status_changed
from typing import TYPE_CHECKING
from oko.storage import collection_jobs, material_read, processing_input, memberships
from oko.collection.adapters import AdapterIssue, AdapterErrorKind
from oko.storage import publication_links, youtube_media
from oko.diagnostics import record_caught
if TYPE_CHECKING:
    from oko.storage.store import Store

from oko.domain.job import Job

from oko.domain.processing import Kind, ProcessingInput

_COLLECTION_CAPTURES = object()
_COLLECTION_OUTCOMES = object()
_ACTIVE_COLLECTION = object()


class JobRejected(Exception):
    pass


@dataclass
class CollectionApplicationState:
    effects: CollectionEffects
    diagnostics: CollectionDiagnostics
    capture_state: dict[str,Any]
    processor_jobs: list[int]
    processor_previous: dict[str,Any] | None


class JobRepository:

    def __init__(self, store: 'Store') -> None:
        self.store=store

    def begin_collection(self, job: Job) -> None:
        db = self.store.db
        try:
            context = db.transaction_context
            if not db.in_transaction or not db.transaction_depth or context is None:
                raise RuntimeError('Collection Job requires an owning transaction')
            if job.kind not in ('discover','fetch','rebuild') or not self.valid(job):
                raise JobRejected('Collection Job is not a current attempt')
            previous = context.resources.get(_ACTIVE_COLLECTION)
            outcomes: dict[Job,bool] = context.resources.setdefault(_COLLECTION_OUTCOMES,{})
            if previous and not outcomes.get(previous['job'],False):
                raise RuntimeError('Previous Collection Job was not completed')
            state: dict[str,Any] = {'job':job, 'captured':False, 'applied':False}
            context.resources[_ACTIVE_COLLECTION] = state
            self._expect_collection_outcome(job)
            def require_completion() -> None:
                if not state['captured']:
                    raise RuntimeError('Collection Job produced no capture')
                if not state['applied']:
                    raise RuntimeError('Collection effects were not applied')
                if not outcomes.get(job,False):
                    raise RuntimeError('Collection Job was not completed')
            context.before_commit(require_completion)
        except BaseException:
            if db.transaction_depth:
                db.transaction_failed = True
            raise

    def register_collection_capture(self, captured: CollectionDiagnostics, *, operation: str, target: str) -> CollectionEffects:
        context = self.store.db.transaction_context
        if not self.store.db.in_transaction or not self.store.db.transaction_depth or context is None:
            raise RuntimeError('Collection capture requires an owning transaction')
        state: dict[str,Any] | None = context.resources.get(_ACTIVE_COLLECTION)
        if state is None:
            raise JobRejected('Collection Job does not own this result')
        job: Job = state['job']
        if state['captured'] or (job.kind,job.item) != (operation,target) or not self.valid(job):
            raise JobRejected('Collection Job does not own this result')
        effects = captured.domain_effects()
        captures: dict[int,tuple[CollectionEffects,CollectionDiagnostics,Job,dict[str,Any]]] = context.resources.setdefault(_COLLECTION_CAPTURES,{})
        key = id(effects)
        captures[key] = (effects,captured,job,state)
        state['captured'] = True
        return effects

    def _expect_collection_outcome(self, job: Job) -> None:
        context = self.store.db.transaction_context
        if context is None:
            raise RuntimeError('Collection Job requires an owning transaction')
        outcomes: dict[Job,bool] = context.resources.setdefault(_COLLECTION_OUTCOMES,{})
        outcomes[job] = False

    def _complete_collection_outcome(self, job: Job) -> None:
        context = self.store.db.transaction_context
        if context is not None:
            outcomes: dict[Job,bool] = context.resources.get(_COLLECTION_OUTCOMES,{})
            if job in outcomes:
                outcomes[job] = True

    def after_commit(self, callback: Callable[[], None]) -> None:
        """Publish an attempt's in-memory completion only after its transaction commits."""
        self.store.db.after_commit(callback)

    def valid(self, job: Job) -> bool:
        row=self.store.db.execute('SELECT status , generation , kind , item FROM jobs WHERE id = %s',(job.id,)).fetchone()
        return bool(row and row['status']=='running' and row['generation']==job.generation and row['kind']==job.kind and row['item']==job.item)

    def finish(self, job: Job, *, reason_code: str | None = None, reason_message: str | None = None) -> None:
        try:
            self._finish(job, reason_code=reason_code, reason_message=reason_message)
        except BaseException:
            if self.store.db.transaction_depth:
                self.store.db.transaction_failed = True
            raise

    def _finish(self, job: Job, *, reason_code: str | None, reason_message: str | None) -> None:
        if job.kind not in ('read','discover'):memberships.lock_graph(self.store.db)
        processor_previous=self.store.problems.processor_history_before_finish(job.id,self.store.wall_clock())
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        previous_fetch=managed_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='fetch' else []
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
        discover_before = self.store.db.execute("SELECT reason_code,reason_message FROM jobs WHERE id=%s AND kind='discover'",(job.id,)).fetchone() if job.kind=='discover' else None
        changed=job_write(self.store.db, "UPDATE jobs SET status = 'done' , reason_code = NULL , source_event_owned = false , manual_once = false , retry_at = NULL , retry_delay = 0 , wait_scope = NULL WHERE id = %s AND generation = %s AND status = 'running' AND kind = %s AND item = %s AND CASE WHEN kind IN ('fetch','download_media','filter','theses') AND reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.reason_message AS previous_error, OLD.reason_code AS previous_code",
            (job.id,job.generation,job.kind,job.item)).fetchone()
        if changed is None:
            raise JobRejected('Attempt no longer owns this job')
        job_write(self.store.db, 'UPDATE jobs SET reason_code=%s,reason_message=%s WHERE id=%s', (reason_code,reason_message,job.id))
        self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
        if job.kind in ('fetch','download_media','filter','theses') and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync(self.store,job.kind,source_groups.job_keys(self.store,job.id,changed['previous_code']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(job.id,changed['previous_code'])]))
        if job.kind=='fetch' and previous_fetch:
            self.store.problems.sync_fetch(job.id,previous_fetch,self.store.wall_clock())
        if job.kind=='download_media' and previous_media:
            self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock(),successful=True)
        if job.kind in ('filter','theses') and (changed['previous_error'] is not None or changed['previous_code'] is not None):
            previous_processing=self.store.problems.saved_processing_candidates([job.id]).get(job.id,[])
            if previous_processing:self.store.problems.sync_processing_problem(job.id,previous_processing,self.store.wall_clock())
        if job.kind=='discover':
            codes=((discover_before['reason_code'] or 'work-discover'),) if discover_before and (discover_before['reason_code'] is not None or discover_before['reason_message'] is not None) else ()
            self.store.problems.sync_discover(job.id,self.store.wall_clock(),previous_codes=codes)
        self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)
        if job.kind=='filter':
            self.store.processing.filter_changed(job.item)
        self._complete_collection_outcome(job)

    def retired_media_jobs(self) -> list[tuple[Job,str | None]]:
        rows=self.store.db.execute("""SELECT j.id,j.item,j.generation,j.payload->>'retired_media_key' AS storage_key
            FROM jobs j WHERE j.kind='download_media' AND j.status<>'running' AND j.payload ? 'retired_media_key'
              AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=j.item)""").fetchall()
        return [(Job(row['id'],'download_media',row['item'],row['generation']),row['storage_key']) for row in rows]

    def finish_retired_media_cleanup(self, job: Job, key: str | None) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Media cleanup requires an owning transaction')
        job_write(self.store.db,"""DELETE FROM jobs j WHERE j.id=%s AND j.kind='download_media'
            AND j.item=%s AND j.generation=%s AND j.status<>'running' AND j.payload ? 'retired_media_key'
            AND j.payload->>'retired_media_key' IS NOT DISTINCT FROM %s
            AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=j.item)""",(job.id,job.item,job.generation,key))

    def retarget_completed_media(self, job: Job, identity: str, publication: str) -> None:
        """Keep the accepted file and its completed Job together in one transaction."""
        if not self.store.db.in_transaction or not self.store.db.transaction_depth:
            raise RuntimeError('Media retarget requires an owning transaction')
        changed=job_write(self.store.db, """UPDATE jobs SET item=%s
            WHERE id=%s AND kind='download_media' AND item=%s AND status='done' AND generation=%s
              AND EXISTS (SELECT 1 FROM media_files WHERE id=%s AND publication_id=%s)
            RETURNING id""",(identity,job.id,job.item,job.generation,identity,publication)).fetchone()
        if changed is None:
            self.store.db.transaction_failed=True
            raise JobRejected('Completed Job no longer owns its replacement media')
        self.store.incomplete_draft.mark_job('download_media',identity,job.id)

    def reconcile(self, kind: Kind, candidates: list[tuple[str, str | None, bool, str]], *, preserve_cancelled: bool = False) -> None:
        memberships.lock_graph(self.store.db)
        db=self.store.db
        def sync_source_after(row: Any) -> None:
            if row and (row['previous_code'] or '').startswith('source-'):
                from oko.storage import source_groups
                before=source_groups.job_keys(self.store,row['id'],row['previous_code'])
                complete=source_groups.sync(self.store,kind,before | source_groups.job_keys(self.store,row['id']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(row['id'],row['previous_code'])]))
                job_write(db,"UPDATE jobs SET source_event_owned=(%s=1) AND (reason_code LIKE 'source-%%') IS TRUE WHERE id=%s",(complete,row['id']))
        affected: list[int]=[]
        items=[row[0] for row in candidates]
        for start in range(0,len(items),400):
            affected.extend(row['id'] for row in db.execute("SELECT id FROM jobs WHERE kind=%s AND item=ANY(%s) AND (processor_visible OR reason_code='processor-config')",(kind,items[start:start+400])))
        processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        for item,input_hash,satisfied,revision in candidates:
            job=db.execute("SELECT status,payload,reason_code,input_snapshot->>'input_hash' AS input_hash,id,reason_message FROM jobs WHERE kind=%s AND item=%s",(kind,item)).fetchone()
            previous=job['input_hash'] if job else None
            if job and job['status']=='running' and job['payload'].get('input_revision')==revision:
                continue
            if job and job['status']=='done' and satisfied:
                continue
            def capture_problem() -> list[dict[str,Any]]:
                from oko.storage.processing_job_problems import managed_candidates as processing_candidates
                return processing_candidates(self.store,job['id'],self.store.wall_clock()) if job and job['reason_code'] not in (None,'','processor-config','ai-permission','codex-choice','youtube-admission') and not job['reason_code'].startswith('source-') else []
            previous_problem: list[dict[str,Any]]=[]
            enabled = processing_input.stage_enabled(self.store, item, kind) if input_hash is not None else False
            if preserve_cancelled and job and job['status']=='cancelled' and (previous is None or previous==input_hash):
                continue
            if job and job['status']=='done' and previous is not None and previous==input_hash:
                if job['payload'].get('input_revision') != revision:
                    job_write(db, "UPDATE jobs SET payload=jsonb_set(payload,'{input_revision}',to_jsonb(%s::text)) WHERE kind=%s AND item=%s", (revision,kind,item))
                continue
            if previous is not None and previous==input_hash and job and job['payload'].get('input_revision')==revision:
                # Reconsider blocked work only on an input/settings event. Running
                # attempts are allowed to finish when the switch is turned off.
                if job['status'] == 'blocked' and enabled:
                    if job['payload'].get('preclaim_blocked') and not processing_input.can_start(
                            self.store,item,processing_input.processing_feed(self.store,item,kind)):
                        continue
                    previous_problem=capture_problem()
                    source_changed=job_write(db, """UPDATE jobs SET status='pending',
                        reason_code=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE reason_code END,
                        reason_message=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE reason_message END,
                        payload=(payload-'preclaim_blocked')
                        WHERE kind=%s AND item=%s AND status='blocked' AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code""", (kind, item)).fetchone()
                    sync_source_after(source_changed)
                    if kind=='filter':self.store.processing.filter_changed(item)
                    self.store.publications.status(item)
                    if previous_problem:self.store.problems.sync_processing_problem(job['id'],previous_problem,self.store.wall_clock())
                elif job['status'] == 'pending' and not enabled:
                    previous_problem=capture_problem()
                    source_changed=job_write(db, "UPDATE jobs SET status='blocked' WHERE kind=%s AND item=%s AND status='pending' AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code", (kind, item)).fetchone()
                    sync_source_after(source_changed)
                    if kind=='filter':self.store.processing.filter_changed(item)
                    self.store.publications.status(item)
                    if previous_problem:self.store.problems.sync_processing_problem(job['id'],previous_problem,self.store.wall_clock())
                continue
            if input_hash is None and previous is None and (job is None or job[0]=='cancelled'):
                continue
            if input_hash is None:
                if kind=='theses' and job and job['status']=='blocked' and processing_input.stage_enabled(self.store,item,'theses'):
                    filtering = db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
                    if filtering and filtering['status'] in ('pending','running'):
                        continue
                previous_problem=capture_problem()
                cancelled=job_write(db, "UPDATE jobs SET status='cancelled', generation=generation+1, reason_code=NULL, reason_message=NULL, source_event_owned=false, input_snapshot=NULL WHERE kind=%s AND item=%s AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code",(kind,item)).fetchone()
                sync_source_after(cancelled)
                if kind=='filter' and job:self.store.processing.filter_changed(item)
            else:
                previous_problem=capture_problem()
                wall=self.store.wall_clock()
                source_changed=job_write(db, "INSERT INTO jobs (kind,item,status,payload,input_snapshot) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT (kind,item) DO UPDATE SET status=excluded.status,payload=excluded.payload,input_snapshot=excluded.input_snapshot,generation=jobs.generation+1,next=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.next ELSE 0 END,reason_code=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.reason_code ELSE NULL END,reason_message=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.reason_message ELSE NULL END WHERE CASE WHEN jobs.reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code",
                    (kind,item,'done' if satisfied else 'pending' if enabled else 'blocked',json.dumps(dict(input_revision=revision)),json.dumps(dict(input_hash=input_hash)),wall,wall,wall)).fetchone()
                sync_source_after(source_changed)
                if kind=='filter' and job:self.store.processing.filter_changed(item)
            if previous_problem and job:self.store.problems.sync_processing_problem(job['id'],previous_problem,self.store.wall_clock())
        if affected:
            self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock())
        if items and db.in_transaction:self.store.incomplete_draft.mark_materials(items)

    def forget_input(self, kind: Kind, identities: list[str]) -> None:
        ordered = sorted(set(identities))
        for start in range(0, len(ordered), 400):
            self.store.db.execute("UPDATE jobs SET input_snapshot=NULL WHERE kind=%s AND item=ANY(%s) AND input_snapshot IS NOT NULL", (kind, ordered[start:start+400]))

    def save_input(self, job: Job, work: ProcessingInput) -> None:
        selected = processing_input.settings(self.store, job.item)
        snapshot = dict(input_hash=work.input_hash, generation=job.generation, feed_id=selected['feed_id'],
                        rules=selected['values'], public_ai=work.public_ai, public_processor=work.public_processor, no_input=work.no_input,
                        sources=[dict(id=source.id, version=source.version) for source in work.request.sources])
        changed = self.store.db.execute("UPDATE jobs SET input_snapshot=%s::jsonb WHERE id=%s AND generation=%s AND status='running' AND input_snapshot->>'input_hash'=%s",
            (json.dumps(snapshot, ensure_ascii=False), job.id, job.generation, work.input_hash)).rowcount
        if changed != 1:
            raise JobRejected('Attempt no longer owns this input')

    def set_public_processor(self, job: Job, public: bool, permitted: bool) -> None:
        changed=self.store.db.execute("""UPDATE jobs SET input_snapshot=jsonb_set(jsonb_set(input_snapshot,'{public_processor}',to_jsonb(%s::integer<>0),true),'{public_ai}',to_jsonb(%s::integer<>0),true)
            WHERE id=%s AND generation=%s AND status='running' AND input_snapshot IS NOT NULL""",(public,permitted,job.id,job.generation)).rowcount
        if changed!=1:
            raise JobRejected('Attempt no longer owns this input')

    def claim_processing(self, kind: Kind, now: float) -> Job | None:
        # The common claim code retains monotonic generations and interrupted choices.
        return self.store.jobs.next_job(now,ai_only=True,processing_kind=kind)

    def _revoke_running_input(self, row: Any) -> None:
        previous=self.store.problems.saved_processing_candidates([row['id']]).get(row['id'],[]) if row['reason_message'] is not None or row['reason_code'] is not None else []
        processor_previous=self.store.problems.prepare_processor_change([row['id']],self.store.wall_clock())
        changed=job_write(self.store.db, """UPDATE jobs SET status='pending',generation=generation+1,input_snapshot=NULL,
            next=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN next ELSE 0 END,
            reason_message=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN reason_message ELSE NULL END,
            reason_code=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN reason_code ELSE NULL END
            WHERE id=%s AND status='running' AND generation=%s RETURNING id,OLD.reason_code AS previous_code""",
            (self.store.wall_clock(),self.store.wall_clock(),self.store.wall_clock(),row['id'],row['generation'])).fetchone()
        if changed:
            if (changed['previous_code'] or '').startswith('source-'):
                from oko.storage import source_groups
                source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
            self.store.problems.finish_processor_change([row['id']],processor_previous,self.store.wall_clock())
            if previous:self.store.problems.sync_processing_problem(row['id'],previous,self.store.wall_clock())
            self.store.incomplete_draft.mark_materials([row['item']])

    def revoke_changed_running_inputs(self) -> None:
        """Revoke attempts only when their public AI permission is withdrawn."""
        memberships.lock_graph(self.store.db)
        rows=self.store.db.execute("""SELECT id,kind,item,generation,reason_message,reason_code,input_snapshot
            FROM jobs WHERE status='running' AND kind IN ('filter','theses')""").fetchall()
        for row in rows:
            snapshot=row['input_snapshot'] or {}
            if snapshot.get('public_ai') and snapshot.get('public_processor',True) and not processing_input.live_settings(self.store,row['item'])['public_ai']:
                self._revoke_running_input(row)

    def invalidate_source_metadata_inputs(self, item: str, *, title: bool = False, preserve_cancelled: bool = False) -> None:
        """Retain results but invalidate work involving changed source metadata."""
        memberships.lock_graph(self.store.db)
        target_query="""SELECT p.id,p.processing_metadata FROM publication_links l
            JOIN publications p ON p.id=l.source_publication_id WHERE l.target_publication_id=%s"""
        params: tuple[str,...]=(item,)
        if not title:
            target_query="SELECT id,processing_metadata FROM publications WHERE id=%s UNION "+target_query
            params=(item,item)
        stale_key='source_title_stale' if title else 'source_url_stale'
        for targets in self.store.db.incomplete_row_pages(target_query,params):
            ids=[row['id'] for row in targets]
            rows=self.store.db.execute("""SELECT id,kind,item,generation,reason_message,reason_code,status,input_snapshot,payload
                FROM jobs WHERE kind IN ('filter','theses') AND item=ANY(%s)""",(ids,)).fetchall()
            def affected(row: Any) -> bool:
                if row is None or row['item']==item:return True
                snapshot=row['input_snapshot']
                sources=snapshot.get('sources') if isinstance(snapshot,dict) else None
                return (not isinstance(sources,list) or any(not isinstance(source,dict) or not isinstance(source.get('id'),str) for source in sources)
                        or any(source['id']==item for source in sources))
            saved_targets: set[tuple[str,str]]=set()
            metadata_updates: list[tuple[str,str]]=[]
            for target in targets:
                metadata=json.loads(target['processing_metadata'])
                updated=False
                for kind,key in (('filter','filter'),('theses','summary')):
                    saved=metadata.get(key)
                    if isinstance(saved,dict) and saved.get('reason')!='no_input':
                        saved_targets.add((target['id'],kind))
                        if not saved.get(stale_key):
                            saved[stale_key]=True;updated=True
                if updated:metadata_updates.append((target['id'],json.dumps(metadata,ensure_ascii=False)))
            if metadata_updates:
                self.store.db.execute('UPDATE publications p SET processing_metadata=v.body FROM unnest(%s::text[],%s::text[]) AS v(id,body) WHERE p.id=v.id',([row[0] for row in metadata_updates],[row[1] for row in metadata_updates]))
            for row in rows:
                if row['status']=='cancelled':continue
                if row['status']=='running':
                    if affected(row):self._revoke_running_input(row)
                elif affected(row) or (row['item'],row['kind']) in saved_targets:
                    self.store.db.execute('UPDATE jobs SET input_snapshot=NULL WHERE id=%s AND input_snapshot IS NOT NULL',(row['id'],))
            if title:
                from oko.storage.processing_batch import reconcile_settings
                reconcile_settings(self.store,ids,preserve_cancelled=preserve_cancelled)
            else:
                for target in targets:self.store.processing.reconcile_accepted_item(target['id'])

    def retry_processing(self, item: str, kind: Kind) -> bool:
        db=self.store.db
        memberships.lock_graph(db)
        before=db.execute("SELECT id,reason_message,reason_code,processor_visible FROM jobs WHERE kind=%s AND item=%s",(kind,item)).fetchone()
        if before is None:
            return False
        current_input=processing_input.input_hash(self.store,item,kind)
        if current_input is None:
            return False
        stage_previous=self.store.problems.saved_processing_candidates([before['id']]).get(before['id'],[]) if before['reason_message'] is not None or before['reason_code'] is not None else []
        affected=[before['id']] if before['processor_visible'] or before['reason_code']=='processor-config' else []
        previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        self.store.jobs.forget_execution(kind,item)
        changed=job_write(db, "UPDATE jobs SET status = 'pending' , input_snapshot=jsonb_build_object('input_hash',%s::text), next = CASE WHEN wait_scope IS NOT NULL THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL THEN retry_at ELSE NULL END , reason_code = NULL , reason_message = NULL , generation = generation + 1 WHERE kind = %s AND item = %s AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code",(current_input,kind,item)).fetchone()
        if changed and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
        if kind=='filter':self.store.processing.filter_changed(item)
        if affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())
        if stage_previous:self.store.problems.sync_processing_problem(before['id'],stage_previous,self.store.wall_clock())
        self.store.incomplete_draft.mark_materials([item])
        return True

    def origin(self, job: Job, input_hash: str) -> dict[str, Any] | None:
        return self.store.jobs.saved_execution(job,input_hash)

    def save_origin(self, job: Job, input_hash: str, value: dict[str, Any]) -> None:
        self.store.jobs.save_execution(job,input_hash,value)

    def fail(self, job: Job, error: str, *, code: str) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Processing failure requires an owning transaction')
        valid=self.valid(job)
        collection_jobs.fail(self.store,job,AdapterIssue(code,error,AdapterErrorKind.temporary))
        if valid and job.kind=='filter':self.store.processing.filter_changed(job.item)
        if not processing_input.stage_enabled(self.store, job.item, job.kind):
            previous=self.store.problems.saved_processing_candidates([job.id]).get(job.id,[])
            changed=job_write(self.store.db, "UPDATE jobs SET status='blocked' WHERE id=%s AND generation=%s AND status='pending' AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code", (job.id, job.generation)).fetchone()
            if changed and (changed['previous_code'] or '').startswith('source-'):
                from oko.storage import source_groups
                source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
            if previous:self.store.problems.sync_processing_problem(job.id,previous,self.store.wall_clock())
            self.store.publications.status(job.item)

    def cancel(self, job: Job) -> None:
        if job.kind not in ('read','discover'):memberships.lock_graph(self.store.db)
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        previous=managed_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='fetch' else []
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
        processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock())
        from oko.storage.processing_job_problems import managed_candidates as processing_candidates
        previous_processing=processing_candidates(self.store,job.id,self.store.wall_clock()) if job.kind in ('filter','theses') else []
        changed=job_write(self.store.db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('fetch','download_media','filter','theses') AND reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.reason_code AS previous_code",(job.id,job.generation)).fetchone()
        if changed:self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
        if changed and job.kind in ('fetch','download_media','filter','theses') and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            complete=source_groups.sync(self.store,job.kind,source_groups.job_keys(self.store,job.id,changed['previous_code']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(job.id,changed['previous_code'])]))
            job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND status IN ('pending','running','failed','unavailable','blocked') WHERE id=%s",(complete,job.id))
        if changed and job.kind=='filter':self.store.processing.filter_changed(job.item)
        if changed and previous:self.store.problems.sync_fetch(job.id,previous,self.store.wall_clock())
        if changed and previous_media:self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock())
        if changed and previous_processing:self.store.problems.sync_processing_problem(job.id,previous_processing,self.store.wall_clock())
        if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def block(self, job: Job) -> None:
        if job.kind in ('fetch','download_media','filter','theses'):
            from oko.storage import memberships
            memberships.lock_graph(self.store.db)
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        previous=managed_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='fetch' else []
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
        from oko.storage.processing_job_problems import managed_candidates as processing_candidates
        previous_processing=processing_candidates(self.store,job.id,self.store.wall_clock()) if job.kind in ('filter','theses') else []
        changed=job_write(self.store.db, "UPDATE jobs SET status = 'blocked' WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('fetch','download_media','filter','theses') AND reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.reason_code AS previous_code", (job.id, job.generation)).fetchone()
        if changed and job.kind in ('fetch','download_media','filter','theses') and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            complete=source_groups.sync(self.store,job.kind,source_groups.job_keys(self.store,job.id,changed['previous_code']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(job.id,changed['previous_code'])]))
            job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND status IN ('pending','running','failed','unavailable','blocked') WHERE id=%s",(complete,job.id))
        if changed and job.kind=='filter':self.store.processing.filter_changed(job.item)
        if changed and previous:self.store.problems.sync_fetch(job.id,previous,self.store.wall_clock())
        if changed and previous_media:self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock())
        if changed and previous_processing:self.store.problems.sync_processing_problem(job.id,previous_processing,self.store.wall_clock())
        if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def manual(self, job: Job) -> bool:
        row=self.store.db.execute('SELECT manual_once FROM jobs WHERE id = %s AND generation = %s',(job.id,job.generation)).fetchone()
        return bool(row and row[0])

    def retry_at(self, job: Job) -> float | None:
        row=self.store.db.execute('SELECT retry_at FROM jobs WHERE id = %s AND generation = %s',(job.id,job.generation)).fetchone()
        return row[0] if row else None


    def suspend_removed_feed_discovery(self, feed: str) -> None:
        job_write(self.store.db, """UPDATE jobs SET status='cancelled',generation=generation+1
            WHERE kind='discover' AND item=%s AND status='pending' AND reason_message IS NULL
            AND (reason_code IS NULL OR reason_code='') AND (retry_at IS NULL OR retry_at<=%s)""",(feed,self.store.wall_clock()))

    def consume_restored_discovery(self, job: Job) -> bool:
        return bool(job_write(self.store.db,"UPDATE jobs SET payload=payload-'restore_discover' WHERE id=%s AND payload->>'restore_discover'='true' RETURNING id",(job.id,)).fetchone())

    def restore_feed_discovery(self, feed: str) -> None:
        now=self.store.wall_clock()
        job_write(self.store.db,"UPDATE jobs SET payload=jsonb_set(payload,'{restore_discover}','true'::jsonb) WHERE kind='discover' AND item=%s AND status='running'",(feed,))
        job_write(self.store.db,"""UPDATE jobs SET status='pending',next=CASE WHEN retry_at>%s THEN next ELSE 0 END,
            retry_at=CASE WHEN retry_at>%s THEN retry_at ELSE NULL END,
            reason_message=CASE WHEN retry_at>%s THEN reason_message ELSE NULL END,
            reason_code=CASE WHEN retry_at>%s THEN reason_code ELSE NULL END
            WHERE kind='discover' AND item=%s AND status IN ('done','cancelled')""",(now,now,now,now,feed))

    def enqueue_discovery(self, feed: str, next_at: float = 0) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Discovery scheduling requires an owning transaction')
        if self.store.db.execute('SELECT 1 FROM feeds WHERE id=%s AND deleted',(feed,)).fetchone():
            return
        # Monotonic deadlines belong to this process; persist their wall-clock
        # equivalent so a continuation keeps its remaining delay after restart.
        retry_at = self.store.wall_clock()+max(0,next_at-self.store.clock()) if next_at else None
        prior=self.store.db.execute("SELECT id,processor_visible,reason_code FROM jobs WHERE kind='discover' AND item=%s",(feed,)).fetchone()
        affected=[prior['id']] if prior and (prior['processor_visible'] or prior['reason_code']=='processor-config') else []
        previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        changed=job_write(self.store.db, """INSERT INTO jobs(kind,item,next,retry_at,queued_wall)
            VALUES ('discover',%s,%s,%s,extract(epoch FROM clock_timestamp()))
            ON CONFLICT(kind,item) DO UPDATE SET queued_wall=excluded.queued_wall,status='pending',
                next=excluded.next,retry_at=excluded.retry_at,reason_message=NULL WHERE jobs.status='done' RETURNING id,reason_message,reason_code""",
            (feed,next_at,retry_at)).fetchone()
        if changed and (changed['reason_message'] is not None or changed['reason_code'] is not None):self.store.problems.sync_discover(changed['id'],self.store.wall_clock())
        if changed and affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())

    def refresh_discovery(self, feed: str) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Feed refresh requires an owning transaction')
        discovery = feed
        active = self.store.db.execute("SELECT 1 FROM jobs WHERE kind = 'discover' AND item = %s AND ( status = 'running' OR ( status = 'pending' AND reason_message IS NULL AND next <= %s ) )", (discovery,self.store.clock())).fetchone()
        if not active:
            self.enqueue_discovery(feed)
            changed_discover=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , reason_code = NULL , reason_message = NULL , next = CASE WHEN reason_code LIKE '%%FloodWaitError' OR reason_code = 'youtube-admission' OR ( wait_scope IS NOT NULL AND retry_at > %s ) THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END , generation = generation + 1 WHERE kind = 'discover' AND item = %s AND status != 'running' RETURNING id,reason_message,reason_code", (self.store.wall_clock(),self.store.wall_clock(),discovery)).fetchone()
            if changed_discover and (changed_discover['reason_message'] is not None or changed_discover['reason_code'] is not None):self.store.problems.sync_discover(changed_discover['id'],self.store.wall_clock())
            if changed_discover and changed_discover['reason_code']=='processor-config':self.store.problems.finish_processor_change([changed_discover['id']],None,self.store.wall_clock())

    def retry_feed_fetches(self, feed: str) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Feed refresh requires an owning transaction')
        source_rows: list[Any]=[]
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        for row in self.store.db.execute("SELECT i . id , j . id AS job_id, j . reason_message , j . reason_code, j . status FROM publications i JOIN jobs j ON j . item = i . id AND j . kind = 'fetch' WHERE i.feed_id = %s AND j . status != 'running'", (feed,)).fetchall():
            if row['status']=='unavailable' or row['status']=='done' and row['reason_message'] is None and row['reason_code'] is None:
                continue
            prior=managed_candidates(self.store,row['job_id'],self.store.wall_clock()) if row['reason_message'] is not None or row['reason_code'] is not None else []
            changed_fetch=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , manual_once = CASE WHEN ( status = 'done' AND reason_message IS NOT NULL ) THEN true ELSE manual_once END , status = 'pending' , reason_code = NULL , reason_message = NULL , next = CASE WHEN reason_code LIKE '%%FloodWaitError' OR reason_code = 'youtube-admission' OR ( wait_scope IS NOT NULL AND retry_at > %s ) THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END , generation = generation + 1 WHERE kind = 'fetch' AND item = %s AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,processor_visible,OLD.reason_code AS previous_code", (self.store.wall_clock(),self.store.wall_clock(),row['id'])).fetchone()
            if changed_fetch and (changed_fetch['processor_visible'] or changed_fetch['previous_code']=='processor-config'):
                self.store.problems.finish_processor_change([changed_fetch['id']],None,self.store.wall_clock())
            if prior:self.store.problems.sync_fetch(row['job_id'],prior,self.store.wall_clock())
            self.store.incomplete_draft.mark_materials([row['id']])
            if changed_fetch and (changed_fetch['previous_code'] or '').startswith('source-'):source_rows.append(changed_fetch)
        if source_rows:
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,source_rows,self.store.wall_clock())

    def retry_feed_processing(self, feed: str) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Feed refresh requires an owning transaction')
        for kind in ('filter','theses'):
            for row in self.store.db.execute(f'SELECT j.item FROM jobs j JOIN ({owned_ids("= %s")}) owner ON owner.id=j.item WHERE j.kind = %s AND j.reason_message IS NOT NULL',(feed,feed,kind)).fetchall():
                self.retry_processing(row[0],kind)


    def enqueue_rebuild(self, item: str) -> bool:
        memberships.lock_graph(self.store.db)
        row=self.store.db.execute('SELECT id FROM publications WHERE id=%s AND feed_id IS NOT NULL',(item,)).fetchone()
        if row is None:return False
        job_write(self.store.db,"""INSERT INTO jobs(kind,item,status,queued_wall) VALUES ('rebuild',%s,'pending',extract(epoch FROM clock_timestamp()))
            ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=jobs.generation+1,
                reason_code=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.reason_code ELSE NULL END,
                reason_message=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.reason_message ELSE NULL END,
                next=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.next ELSE 0 END,
                retry_at=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.retry_at ELSE NULL END""",
            (item,*([self.store.wall_clock()]*4)))
        return True

    def retry_material(self, item: str) -> bool:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Material retry requires an owning transaction')
        if not self.store._has_unviewed(item):
            return False
        self.retry_linked(item)
        self.retry_media(item,revalidate_ready=False)
        if self.store.db.execute("SELECT 1 FROM publications WHERE id=%s AND status='pending_collection'",(item,)).fetchone():
            self.enqueue_fetch(item)
            self.store.db.execute("UPDATE jobs SET manual_once = true WHERE kind = 'fetch' AND item = %s",(item,))
        return True


    def enqueue_fetch(self, item: str, *, sync_problem: bool = True, sync_processor: bool = True, sync_source: bool = True) -> None:
        memberships.lock_graph(self.store.db)
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        before = self.store.db.execute("SELECT id,status,reason_message,next,reason_code,processor_visible FROM jobs WHERE kind='fetch' AND item=%s", (item,)).fetchone()
        previous=managed_candidates(self.store,before['id'],self.store.wall_clock()) if sync_problem and before and (before['reason_message'] is not None or before['reason_code'] is not None) else []
        affected=[before['id']] if sync_processor and before and (before['processor_visible'] or before['reason_code']=='processor-config') else []
        processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        changed_source=self.store.db.execute("INSERT INTO jobs ( kind , item , queued_wall ) VALUES ( 'fetch' , %s , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , manual_once = false , reason_code = NULL , reason_message = NULL , next = CASE WHEN jobs . reason_code LIKE '%%FloodWaitError' OR ( jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s ) THEN jobs . next ELSE 0 END , retry_at = CASE WHEN jobs . reason_code LIKE '%%FloodWaitError' OR ( jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s ) THEN jobs . retry_at ELSE NULL END , generation = jobs . generation + 1 WHERE CASE WHEN jobs.reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code", (item,self.store.wall_clock(),self.store.wall_clock())).fetchone()

        after = self.store.db.execute("SELECT id,status,reason_message,next,reason_code,processor_visible FROM jobs WHERE kind='fetch' AND item=%s", (item,)).fetchone()
        if sync_source and changed_source and (changed_source['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            complete=source_groups.sync(self.store,'fetch',source_groups.job_keys(self.store,changed_source['id'],changed_source['previous_code']) | source_groups.job_keys(self.store,changed_source['id']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(changed_source['id'],changed_source['previous_code'])]))
            job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND (reason_code LIKE 'source-%%') IS TRUE WHERE id=%s",(complete,changed_source['id']))
        if before != after:
            status_changed(self.store.db)
            if previous and after:self.store.problems.sync_fetch(after['id'],previous,self.store.wall_clock())
            if affected:self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock())
            self.store.incomplete_draft.mark_job('fetch',item)


    def next_job(self, now: float, source_feed: str = '', feeds: list[str] | None = None, ai_only: bool = False, media_only: bool = False, captions_only: bool = False, dependency_feeds: list[str] | None = None, processing_kind: str | None = None, invalid_history_feeds: list[str] | None = None) -> Job | None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Job claim requires an owning transaction')
        return self._claim(self._candidate_job(now, source_feed=source_feed, feeds=feeds, ai_only=ai_only, media_only=media_only, captions_only=captions_only, dependency_feeds=dependency_feeds, processing_kind=processing_kind, invalid_history_feeds=invalid_history_feeds))

    def has_due_job(self, now: float, **options: Any) -> bool:
        return self._candidate_job(now, **options) is not None

    def _candidate_job(self, now: float, source_feed: str = '', feeds: list[str] | None = None, ai_only: bool = False, media_only: bool = False, captions_only: bool = False, dependency_feeds: list[str] | None = None, processing_kind: str | None = None, invalid_history_feeds: list[str] | None = None) -> Row | None:
        invalid_history = invalid_history_feeds or []
        if media_only and not ai_only:
            if processing_kind is not None:
                return None
            available = feeds if feeds is not None else [source_feed]
            dependencies = available if dependency_feeds is None else dependency_feeds
            return self._media_candidate(now, available, dependencies, captions_only)
        query = "SELECT * FROM jobs WHERE status = 'pending' AND next <= %s AND NOT ( coalesce ( reason_code , '' ) = 'ai-permission' AND reason_message IS NOT NULL )"
        if processing_kind is not None:
            query += " AND kind=" + ("'filter'" if processing_kind=='filter' else "'theses'")
        if ai_only:
            query += """ AND kind IN ('filter','theses') AND NOT EXISTS (SELECT 1 FROM publications i WHERE i.id=jobs.item AND i.platform IS NOT NULL
                AND NOT ((jobs.kind='filter' AND i.status IN ('pending_filtering','filter_out'))
                      OR (jobs.kind='theses' AND (i.status IN ('pending_summary','ready')
                          OR i.status='pending_filtering'))))"""
        elif media_only:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('%s' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('%s' for _ in dependencies) or "''"
            # Follow only dependencies of the requested roots.
            query += f""" AND kind='download_media' AND item IN (
                SELECT m.id FROM publications i JOIN media_files m ON m.publication_id=i.id
                    WHERE i.feed_id IN ({placeholders})
                UNION SELECT m.id FROM publications root JOIN (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) owner ON owner.id=root.id JOIN publication_links l ON l.source_publication_id=root.id
                    JOIN publications i ON i.id=l.target_publication_id JOIN media_files m ON m.publication_id=i.id
                    WHERE owner.feed_id IN ({dependency_keys}) AND NOT EXISTS (SELECT 1 FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) own WHERE own.id=i.id))"""
        else:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('%s' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('%s' for _ in dependencies) or "''"
            discover = [feed for feed in available if feed not in invalid_history]
            discover_keys = ','.join('%s' for _ in discover) or "''"
            query += f""" AND kind IN ('discover','fetch','rebuild') AND id IN (WITH allowed AS MATERIALIZED (
                SELECT id FROM jobs WHERE status='pending' AND kind = 'discover' AND item IN ({discover_keys})
                UNION SELECT id FROM jobs WHERE status='pending' AND kind IN ('fetch','rebuild') AND item IN (
                    SELECT own.id FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) owner JOIN publications own ON own.id=owner.id
                        WHERE owner.feed_id IN ({placeholders})
                    UNION SELECT i.id FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) owner JOIN publications root ON root.id=owner.id
                        JOIN publication_links l ON l.source_publication_id=root.id JOIN publications i ON i.id=l.target_publication_id
                        WHERE owner.feed_id IN ({dependency_keys})
                        AND NOT EXISTS (SELECT 1 FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) m WHERE m.id=i.id))) SELECT id FROM allowed)"""
        if media_only:
            subtitles="SELECT 1 FROM media_files m JOIN publications i ON i . id = m . publication_id JOIN jsonb_array_elements(CASE WHEN jsonb_typeof(i.content::jsonb->'blocks')='array' THEN i.content::jsonb->'blocks' ELSE '[]'::jsonb END) b(value) ON b.value->>'media_id' = m.id WHERE m . id = jobs . item AND i . platform = 'youtube' AND b.value->>'role' = 'subtitles'"
            query += ' AND '+('EXISTS' if captions_only else 'NOT EXISTS')+' ('+subtitles+')'
        # The worker's damaged-feed set is only a hint: repair can pause a
        # history before the next poll. Recheck those feeds against current data.
        invalid_for_fetch = sorted(self.store.subscriptions.history.invalid_feeds(
            invalid_history, require_rows=True, allow_deferred=True)) if invalid_history and not ai_only and not media_only else []
        blocked_history = """SELECT m.item FROM history_members m JOIN initial_history h ON h.feed=m.feed
            WHERE NOT (m.feed=ANY(%s)) AND CASE
                WHEN NOT pg_input_is_valid(h.selection, 'jsonb') THEN FALSE
                WHEN jsonb_typeof(h.selection::jsonb) IS DISTINCT FROM 'object'
                    OR h.selection::jsonb->>'mode' IS DISTINCT FROM 'count'
                    OR jsonb_typeof(h.selection::jsonb->'count') IS DISTINCT FROM 'number'
                    OR (h.selection::jsonb->>'count') !~ '^[1-9][0-9]*$' THEN FALSE
                ELSE h.paused=1 OR (h.scan_complete=0 AND (h.selection::jsonb->>'mode'='count'
                    OR h.feed IN (SELECT id FROM feeds WHERE transport='rss'))) END"""
        # PostgreSQL primary keys reject NULL history memberships. A malformed
        # fetch without an item is never dispatchable; valid work uses the index.
        query += f""" AND NOT (kind='fetch' AND
            (jobs.item IS NULL OR EXISTS ({blocked_history} AND m.item=jobs.item OFFSET 0)))"""
        query += " ORDER BY CASE WHEN kind='discover' THEN 1 WHEN kind IN ('filter','read') THEN 2 ELSE 3 END,id LIMIT 1"
        params = ((now,) if ai_only else (now, *available, *dependencies) if media_only else (now, *discover, *available, *dependencies)) + (invalid_for_fetch,)
        row = self.store.db.execute(query, params).fetchone()
        return row

    def _media_candidate(self, now: float, feeds: list[str], dependencies: list[str], captions: bool) -> Row | None:
        # Avoid walking every publication and media file when no due download
        # exists anywhere. The scoped query below remains authoritative when it does.
        if not self.store.db.execute("SELECT 1 FROM jobs WHERE kind='download_media' AND status='pending' AND next<=%s AND NOT (coalesce(reason_code,'')='ai-permission' AND reason_message IS NOT NULL) LIMIT 1", (now,)).fetchone():
            return None
        # LATERAL keeps the indexed search scoped to each selected publication.
        # Each media identity has one job of this kind. Keep parameterized
        # index probes instead of joining the entire foreign pending archive.
        caption = """EXISTS (SELECT 1 FROM publications own,
            LATERAL jsonb_array_elements(own.content::jsonb->'blocks') block
            WHERE own.id=m.publication_id AND own.platform='youtube'
                AND block->>'media_id'=m.id AND block->>'role'='subtitles')"""
        query = """WITH selected AS MATERIALIZED (
            SELECT i.id FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) owner JOIN publications i ON i.id=owner.id
            WHERE owner.feed_id=ANY(%s)
            UNION SELECT article.id FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) owner JOIN publications root ON root.id=owner.id
            JOIN publication_links link ON link.source_publication_id=root.id JOIN publications article ON article.id=link.target_publication_id
            WHERE owner.feed_id=ANY(%s)
                AND NOT EXISTS (SELECT 1 FROM (SELECT id,feed_id FROM publications WHERE feed_id IS NOT NULL) m WHERE m.id=article.id))
            SELECT candidate.* FROM selected
            CROSS JOIN LATERAL (
                SELECT id,publication_id FROM media_files
                WHERE publication_id=selected.id OFFSET 0
            ) m
            CROSS JOIN LATERAL (
                WITH matching AS MATERIALIZED (
                    SELECT * FROM jobs WHERE item=m.id AND kind='download_media'
                ) SELECT j.* FROM matching j
                WHERE j.status='pending' AND j.next<=%s
                    AND NOT (coalesce(j.reason_code,'')='ai-permission' AND j.reason_message IS NOT NULL)
                    AND """ + (caption if captions else 'NOT '+caption) + """
                OFFSET 0
            ) candidate ORDER BY candidate.id LIMIT 1"""
        return self.store.db.execute(query, (feeds, dependencies, now)).fetchone()

    def _claim(self, row: Row | None) -> Job | None:
        if not row:
            return None
        sequence_value=None
        if row['kind'] in ('fetch','download_media','filter','theses'):
            from oko.storage import memberships
            memberships.lock_graph(self.store.db)
            current=self.store.db.execute("SELECT jobs.*,(SELECT value FROM state WHERE key='job-dispatch-generation') AS dispatch_sequence FROM jobs WHERE id=%s",(row['id'],)).fetchone()
            if current is None or current['status']!='pending' or current['generation']!=row['generation']:
                return None
            row=current
            sequence_value=current['dispatch_sequence']
        from oko.storage import individual_job_problems
        with individual_job_problems.claim(self.store, row):
            # Keep dispatch generations monotonic even when every old job was removed.
            if row['kind'] not in ('fetch','download_media','filter','theses'):
                sequence = self.store.db.execute("SELECT value FROM state WHERE key = 'job-dispatch-generation'").fetchone()
                sequence_value=sequence[0] if sequence else None
            generation = max(row['generation'], int(sequence_value) if sequence_value is not None else 0) + 1
            self.store.db.execute("INSERT INTO state VALUES ( 'job-dispatch-generation' , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value", (str(generation),))
            job_write(self.store.db, "UPDATE jobs SET status='running', attempt=attempt+1, generation=%s WHERE id=%s", (generation, row['id']))
        if row['kind']=='discover' and (row['reason_message'] is not None or row['reason_code'] is not None):self.store.problems.sync_discover(row['id'],self.store.wall_clock())
        current = expect_row(self.store.db.execute('SELECT * FROM jobs WHERE id = %s', (row['id'],)).fetchone())
        return Job(current['id'], current['kind'], current['item'], current['generation'])


    def retry(self, job: Job, now: float, error: str, delay: float = 1, *, code: str | None = None, terminal: bool = False, issue: AdapterIssue | None = None) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Job retry requires an owning transaction')
        try:
            self._retry(job,now,error,delay,code=code,terminal=terminal,issue=issue)
        except BaseException:
            if self.store.db.transaction_depth:
                self.store.db.transaction_failed = True
            raise

    def _retry(self, job: Job, now: float, error: str, delay: float = 1, *, code: str | None = None, terminal: bool = False, issue: AdapterIssue | None = None) -> None:
        if job.kind in ('discover','fetch','rebuild','filter','theses'):
            valid=self.valid(job)
            collection_jobs.fail(self.store,job,issue or AdapterIssue(code or 'collection-failed',error,
                AdapterErrorKind.permanent if terminal else AdapterErrorKind.temporary))
            if valid and job.kind=='filter':self.store.processing.filter_changed(job.item)
            if valid and not self.valid(job):self._complete_collection_outcome(job)
            return
        memberships.lock_graph(self.store.db)
        processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock(),source_lock=job.kind == 'download_media' and (code or '').startswith('source-'))
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
        changed = job_write(self.store.db, "UPDATE jobs SET status = %s , next = %s , reason_message = %s , reason_code = %s WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('download_media') AND (reason_code LIKE 'source-%%' OR %s LIKE 'source-%%') THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.reason_code AS previous_code",
            ('failed' if terminal else 'pending',0 if code=='ai-permission' else now+delay,error,code or job.kind,job.id,job.generation,code or job.kind)).fetchone()
        if changed and job.kind == 'download_media' and ((changed['previous_code'] or '').startswith('source-') or (code or '').startswith('source-')):
            from oko.storage import source_groups
            source_old_keys=source_groups.job_keys(self.store,job.id,changed['previous_code']) if (changed['previous_code'] or '').startswith('source-') else set()
            complete=source_groups.sync(self.store,job.kind,source_old_keys | source_groups.job_keys(self.store,job.id),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(job.id,changed['previous_code'])]))
            job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND (reason_code LIKE 'source-%%') IS TRUE WHERE id=%s",(complete,job.id))
        if changed and job.kind=='download_media':
            self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock())
        if changed:
            self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
            self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)


    def interrupted(self, feeds: set[str] | None = None) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Job interruption requires an owning transaction')
        selection = "status='running'"
        params: tuple[str, ...] = ()
        if feeds is not None:
            placeholders = ','.join('%s' for _ in feeds) or "''"
            owner_query = owned_ids(f'IN ({placeholders})')
            selection += f" AND (item IN ({placeholders}) OR item IN ({owner_query}) OR item IN (SELECT m.id FROM media_files m JOIN ({owner_query}) owner ON owner.id=m.publication_id))"
            params = (*feeds, *feeds, *feeds, *feeds, *feeds)
        from oko.storage import memberships,source_groups
        memberships.lock_graph(self.store.db)
        # A configuration cancellation is not a failed discovery request.
        changed_discover=job_write(self.store.db, f"UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , generation = generation + 1 , next = CASE WHEN wait_scope IS NULL THEN 0 ELSE next END , retry_at = CASE WHEN wait_scope IS NULL THEN NULL ELSE retry_at END WHERE kind = 'discover' AND {selection} RETURNING id,reason_message,reason_code",params).fetchall()
        for row in changed_discover:
            if row['reason_message'] is not None or row['reason_code'] is not None:self.store.problems.sync_discover(row['id'],self.store.wall_clock())
        collection_jobs.interrupt(self.store,feeds,backoff=False)
        changed=job_write(self.store.db, f"UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status='pending', generation=generation+1 WHERE {selection} RETURNING id,kind,item,reason_message,reason_code", params).fetchall()
        source_ids=[row['id'] for row in changed if row['kind'] in ('fetch','download_media','filter','theses') and (row['reason_code'] or '').startswith('source-')]
        if source_ids:source_groups.sync_jobs(self.store,source_ids,self.store.wall_clock())
        for row in changed:
            self.store.incomplete_draft.mark_job(row['kind'],row['item'],row['id'])
            if row['kind']=='filter':self.store.processing.filter_changed(row['item'])


    def saved_execution(self, job: Job, input_hash: str) -> dict[str, Any] | None:
        row = self.store.db.execute("SELECT execution_settings->'parameters' FROM jobs WHERE id=%s AND execution_settings->>'generation'=%s::text AND input_snapshot->>'input_hash'=%s",
                                    (job.id, job.generation, input_hash)).fetchone()
        return dict(row[0]) if row and row[0] is not None else None

    def save_execution(self, job: Job, input_hash: str, origin: dict[str, Any]) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Execution settings require an owning transaction')
        changed = self.store.db.execute("""UPDATE jobs SET execution_settings=jsonb_build_object(
            'generation',%s::bigint,'parameters',%s::jsonb)
            WHERE id=%s AND generation=%s AND status='running'""",
            (job.generation, json.dumps(origin), job.id, job.generation)).rowcount
        if changed != 1:
            raise JobRejected('Attempt no longer owns this job')

    def forget_execution(self, kind: str, item: str) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Execution settings require an owning transaction')
        self.store.db.execute('UPDATE jobs SET execution_settings=NULL WHERE kind=%s AND item=%s', (kind, item))

    def execution_provenance(self, job: Job) -> dict[str, Any] | None:
        row = self.store.db.execute("SELECT execution_settings->'parameters' FROM jobs WHERE id=%s AND execution_settings->>'generation'=%s::text", (job.id, job.generation)).fetchone()
        return dict(row[0]) if row and row[0] is not None else None


    def prepare_youtube(self, feed: str, want_captions: bool) -> None:
        if not self.store.db.transaction_depth:
            raise RuntimeError('YouTube preparation requires an owning transaction')
        if not want_captions:
            rows=self.store.db.execute("SELECT DISTINCT i . id , m . status FROM publications i JOIN media_files m ON m . publication_id = i . id WHERE i.feed_id = %s AND m . resource_key = 'youtube:subtitles'",(feed,)).fetchall()
            from oko.storage import memberships,source_groups
            memberships.lock_graph(self.store.db)
            affected=[row['id'] for row in self.store.db.execute(f"SELECT j.id FROM jobs j JOIN media_files m ON j.kind='download_media' AND j.item=m.id JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id WHERE m.resource_key='youtube:subtitles' AND j.status IN ('pending','running') AND (j.processor_visible OR j.reason_code='processor-config')",(feed,feed))]
            previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
            changed_source=job_write(self.store.db, f"UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , reason_message = NULL , reason_code = NULL WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' ) AND item IN ( SELECT m.id FROM media_files m JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id WHERE m.resource_key = 'youtube:subtitles' ) RETURNING id,item,OLD.reason_code AS previous_code",(feed,feed)).fetchall()
            source_groups.sync_changed_rows(self.store,list(changed_source),self.store.wall_clock())
            self.store.incomplete_draft.mark_materials([row['id'] for row in rows])
            self.store.incomplete_draft.mark_jobs([('download_media',row['item']) for row in changed_source])
            if affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())
            return
        # This runs in the reader loop. Admission needs only the saved caption
        # state, not a reader material with filters, theses and provenance.
        rows = self.store.db.execute("SELECT items.id , items.platform , items.content FROM publications items WHERE items.feed_id = %s AND items.content ? 'blocks' AND NOT EXISTS ( SELECT 1 FROM jobs WHERE kind = 'fetch' AND item = items . id AND status IN ( 'pending' , 'running' ) )", (feed,)).fetchall()
        for row in rows:
            content=saved_content(row['content'],mutable=True)
            subtitles=youtube_media.subtitle_id(content)
            if subtitles is None:
                continue
            if youtube_media.subtitle_state(self.store,content,row['id']) == 'not_requested':
                job_write(self.store.db, """INSERT INTO jobs(kind,item,status,queued_wall)
                    VALUES ('download_media',%s,'pending',extract(epoch FROM clock_timestamp()))
                    ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=jobs.generation+1,
                      next=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.next ELSE 0 END
                    WHERE jobs.status='cancelled'""",(subtitles,self.store.wall_clock()))
                self.store.processing.reconcile_accepted_item(row['id'])


    @contextmanager
    def transaction(self, admitted: Callable[[], bool]) -> Iterator[None]:
        with transaction(self.store.db,admitted=admitted):
            yield

    def checkpoint(self, name: str) -> None:
        self.store.fault(name)

    def mark_media_unavailable(self, identity: str, code: str, message: str) -> None:
        """Record a durable media failure inside the collection transaction."""
        if not self.store.db.in_transaction or not self.store.db.transaction_depth:
            raise RuntimeError('Media failure requires an owning transaction')
        from oko.storage.media_job_problems import previous_candidates
        from oko.storage import source_groups
        db=self.store.db
        old=db.execute("SELECT id FROM jobs WHERE kind='download_media' AND item=%s",(identity,)).fetchone()
        previous=previous_candidates(self.store,old['id'],self.store.wall_clock()) if old else []
        changed=job_write(db,"""INSERT INTO jobs(kind,item,status,reason_code,reason_message)
            SELECT 'download_media',id,'unavailable',%s,%s FROM media_files WHERE id=%s
            ON CONFLICT(kind,item) DO UPDATE SET status='unavailable',generation=jobs.generation+1,
                reason_code=excluded.reason_code,
                reason_message=excluded.reason_message
            RETURNING id,OLD.reason_code AS previous_code""",(code,message,identity)).fetchone()
        if changed is None:
            raise JobRejected('Media target no longer exists')
        source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
        self.store.problems.sync_media_problem(changed['id'],previous,self.store.wall_clock())

    def fail_media(self, job: Job, issue: AdapterIssue) -> None:
        if issue.kind==AdapterErrorKind.media_changed:
            issue=AdapterIssue('media-metadata-changed',issue.message,AdapterErrorKind.unavailable,issue.publication,issue.media_key)
        collection_jobs.fail(self.store,job,issue)

    def defer_captions(self, job: Job, now: float, delay: float, reason: str) -> None:
        memberships.lock_graph(self.store.db)
        processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock())
        from oko.storage.media_job_problems import previous_candidates
        subtitle_previous=previous_candidates(self.store,job.id,self.store.wall_clock())
        changed=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , next = %s , reason_code = NULL , reason_message = %s WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('fetch','download_media','filter','theses') AND reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code",(now+max(.05,delay),reason,job.id,job.generation)).fetchone()
        if changed and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
        if changed:self.store.problems.sync_media_problem(job.id,subtitle_previous,self.store.wall_clock())
        if changed:self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
        if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def collection_start(self, job: Job, feed: str) -> dict[str,Any]:
        return self.store.collection_status.start(job,feed)

    def collection_finish(self, job: Job, feed: str, saved: dict[str,Any], elapsed: float, observation: dict[str,Any]) -> None:
        self.store.collection_status.finish(job,feed,saved,elapsed,observation)

    def collection_interrupt(self, job: Job, feed: str, saved: dict[str,Any], elapsed: float) -> None:
        self.store.collection_status.interrupt(job,feed,saved,elapsed)

    def begin_collection_effects(self, effects: CollectionEffects, job: Job) -> CollectionApplicationState:
        db=self.store.db
        if not db.in_transaction or not db.transaction_depth:
            raise RuntimeError('Collection effects require an owning transaction')
        memberships.lock_graph(db)
        context=db.transaction_context
        captures: dict[int,tuple[CollectionEffects,CollectionDiagnostics,Job,dict[str,Any]]] = context.resources.get(_COLLECTION_CAPTURES,{}) if context is not None else {}
        registered=captures.get(id(effects))
        if registered is None or registered[0] is not effects:
            raise RuntimeError('Collection effects have no capture in this transaction')
        if job != registered[2] or not self.valid(job):
            raise JobRejected('Collection Job does not own this result')
        return CollectionApplicationState(effects,registered[1],registered[3],[],None)

    def reject_collection_effects(self) -> None:
        if self.store.db.transaction_depth:
            self.store.db.transaction_failed=True

    def _check_collection_effects(self, effects: CollectionEffects, state: CollectionApplicationState) -> None:
        context=self.store.db.transaction_context
        captures=context.resources.get(_COLLECTION_CAPTURES,{}) if context is not None else {}
        registered=captures.get(id(effects))
        if not self.store.db.transaction_depth or state.effects is not effects or not registered or registered[3] is not state.capture_state:
            raise RuntimeError('Collection effects require their owning transaction')

    def schedule_collection_effects(self, effects: CollectionEffects, state: CollectionApplicationState) -> None:
        self._check_collection_effects(effects,state)
        db=self.store.db
        diagnostics=state.diagnostics
        captured_fetch_items={row['item'] for row in db.execute("SELECT item FROM jobs WHERE kind='fetch' AND id=ANY(%s)",(list(diagnostics.fetch_problems_before),))} if diagnostics.fetch_problems_before else set()
        captured_source_fetch_items={row['item'] for row in db.execute("SELECT item FROM jobs WHERE kind='fetch' AND id=ANY(%s)",(list(diagnostics.source_groups_before),))} if diagnostics.source_groups_before else set()
        affected = sorted(set(effects.fetch))
        processor_items=sorted(set(affected) | set(effects.media))
        if processor_items:
            from oko.storage.transaction import begin_write
            begin_write(db)
            self.store.incomplete_draft.mark_materials(processor_items)
        processor_jobs=sorted(set(self.store.problems.processor_jobs_for_materials(processor_items)))
        processor_previous=self.store.problems.processor_history_before_batch(processor_jobs,self.store.wall_clock())
        for item in effects.invalidated_fetch:
            running=db.execute("SELECT id,generation FROM jobs WHERE kind='fetch' AND item=%s AND status='running'",(item,)).fetchone()
            if running:
                self.cancel(Job(running['id'],'fetch',item,running['generation']))
                if item in effects.fetch:effects.fetch[item]=True
        for item,changed in effects.fetch.items():
            if changed:
                self.enqueue_fetch(item,sync_problem=item not in captured_fetch_items,sync_processor=False,sync_source=item not in captured_source_fetch_items)
            else:
                job_write(db, "INSERT INTO jobs ( kind , item , queued_wall ) VALUES ( 'fetch' , %s , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , next = 0 , reason_message = NULL , generation = jobs . generation + 1 WHERE jobs . status = 'done' AND ( jobs . reason_message IS NULL OR jobs . reason_code IS NULL )",(item,))
        for item in effects.media:self.schedule_media(item,skip_problem_jobs=set(diagnostics.media_problems_before),skip_source_jobs=set(diagnostics.source_groups_before),sync_processor=False)
        state.processor_jobs=processor_jobs
        state.processor_previous=processor_previous

    def complete_collection_effects(self, effects: CollectionEffects, state: CollectionApplicationState) -> None:
        self._check_collection_effects(effects,state)
        diagnostics=state.diagnostics
        processor_jobs=state.processor_jobs
        processor_previous=state.processor_previous
        for job_id,previous in diagnostics.fetch_problems_before.items():
            self.store.problems.sync_fetch(job_id,previous,self.store.wall_clock())
        if processor_jobs:self.store.problems.finish_processor_change(processor_jobs,processor_previous,self.store.wall_clock())
        for job_id,previous in diagnostics.media_problems_before.items():
            self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        if diagnostics.processing_problems_before:
            self.store.problems.sync_processing_problems(diagnostics.processing_problems_before,self.store.wall_clock())
        if diagnostics.source_groups_before:self.sync_captured_source_groups(diagnostics)
        self.store.fault('collection_effects_before_completion')
        state.capture_state['applied']=True
        context=self.store.db.transaction_context
        if context is not None:
            context.resources.get(_COLLECTION_CAPTURES,{}).pop(id(effects),None)

    def sync_captured_source_groups(self, effects: CollectionDiagnostics) -> None:
        from oko.storage import source_groups
        source_ids=list(effects.source_groups_before)
        current: dict[int,tuple[str,set[str]]]={}
        for start in range(0,len(source_ids),400):current.update(source_groups.job_keys_many(self.store,source_ids[start:start+400]))
        grouped: dict[str,set[str]]={}
        transitions: dict[tuple[str,tuple[str,...],tuple[str,...]],list[int]]={}
        failed_jobs: set[int]=set()
        for job_id in sorted(effects.source_groups_before):
            kind,before=effects.source_groups_before[job_id]
            after=current.get(job_id,(kind,set()))[1]
            if before!=after:
                transitions.setdefault((kind,tuple(sorted(before)),tuple(sorted(after))),[]).append(job_id)
            else:grouped.setdefault(kind,set()).update(before)
        for (kind,before_tuple,after_tuple),jobs in transitions.items():
            if not source_groups.sync_transition(self.store,kind,set(before_tuple),set(after_tuple),self.store.wall_clock(),evidence=effects.source_evidence_before):failed_jobs.update(jobs)
        failed={(kind,key) for kind,keys in grouped.items() for key in keys if not source_groups.sync(self.store,kind,{key},self.store.wall_clock(),evidence=effects.source_evidence_before)}
        if current:
            eligible=[job_id for job_id,(kind,keys) in current.items() if job_id not in failed_jobs and all((kind,key) not in failed for key in keys)]
            if eligible:job_write(self.store.db,"UPDATE jobs SET source_event_owned=(status IN ('pending','running','failed','unavailable','blocked') OR (kind='fetch' AND status='done' AND reason_message IS NOT NULL)) WHERE id=ANY(%s) AND reason_code LIKE 'source-%%'",(eligible,))
            eligible_set=set(eligible)
            ineligible=[job_id for job_id in current if job_id not in eligible_set]
            if ineligible:job_write(self.store.db,"UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned",(ineligible,))
            source_groups.notify_admission(self.store,eligible+ineligible)

    def change_adapter(self, feed: str) -> None:
        db=self.store.db
        from oko.storage import memberships
        memberships.lock_graph(db)
        effects=CollectionDiagnostics()
        affected={row['item'] for row in db.execute(f"SELECT j.item FROM jobs j JOIN ({owned_ids('= %s')}) owner ON owner.id=j.item WHERE j.kind='fetch' AND ((j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%'))",(feed,feed))}
        publication_links.capture_fetch_problems(self.store,effects,affected)
        media_affected={row['publication_id'] for row in db.execute(f"SELECT DISTINCT m.publication_id FROM media_files m JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE (((m.resource_key='youtube:subtitles' OR j.media_null_owned) AND j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%'))",(feed,feed))}
        publication_links.capture_media_problems(self.store,effects,media_affected)
        owned=[row[0] for row in db.execute(owned_ids('= %s'),(feed,feed))]
        publication_links.capture_media_problems(self.store,effects,set(owned))
        from oko.storage import memberships
        memberships.lock_graph(db)
        publication_links.capture_source_groups(self.store,effects,set(owned))
        self.store.incomplete_draft.mark_materials(owned)
        for start in range(0,len(owned),400):
            media_ids=[row['id'] for row in db.execute('SELECT id FROM media_files WHERE publication_id=ANY(%s)',(owned[start:start+400],))]
            self.store.incomplete_draft.mark_jobs([('download_media',identity) for identity in media_ids])
        processor_affected=self.store.problems.processor_jobs_for_materials([feed,*owned])
        processor_previous=self.store.problems.prepare_processor_change(processor_affected,self.store.wall_clock())
        job_write(db, f"UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , reason_message = NULL , reason_code = NULL , retry_at = NULL , retry_delay = 0 , wait_scope = NULL , next = 0 WHERE kind = 'download_media' AND item IN ( SELECT m.id FROM media_files m JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id )",(feed,feed))
        job_write(db, "INSERT INTO jobs ( kind , item , queued_wall ) SELECT 'fetch' , items.id , extract(epoch FROM clock_timestamp()) FROM publications items WHERE items.feed_id = %s ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , generation = jobs . generation + 1 , next = 0 , retry_at = NULL , retry_delay = 0 , wait_scope = NULL , reason_message = NULL , reason_code = NULL",(feed,))
        self.enqueue_discovery(feed)
        for job_id,previous in effects.fetch_problems_before.items():
            self.store.problems.sync_fetch(job_id,previous,self.store.wall_clock())
        for job_id,previous in effects.media_problems_before.items():
            self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        if effects.source_groups_before:self.sync_captured_source_groups(effects)
        if processor_affected:self.store.problems.finish_processor_change(processor_affected,processor_previous,self.store.wall_clock())

    def schedule_media(self, item: str, *, skip_problem_jobs: set[int] | None = None, skip_source_jobs: set[int] | None = None, sync_processor: bool = True) -> None:
        memberships.lock_graph(self.store.db)
        db = self.store.db
        skipped_source=skip_source_jobs or set()
        processor_affected=self.store.problems.processor_jobs_for_materials([item]) if sync_processor else []
        processor_previous=self.store.problems.prepare_processor_change(processor_affected,self.store.wall_clock()) if sync_processor else None
        cancelled=job_write(db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' ) AND item IN ( SELECT id FROM media_files WHERE publication_id = %s AND status = 'unavailable' ) RETURNING id,item,reason_message,reason_code,OLD.reason_code AS previous_code",(item,)).fetchall()
        from oko.storage import source_groups
        source_rows=list(cancelled)
        excluded=skip_problem_jobs or set()
        changed_ids=[row['id'] for row in cancelled if row['id'] not in excluded and (row['reason_message'] is not None or row['reason_code'] is not None)]
        saved=self.store.problems.saved_media_candidates(changed_ids)
        for job_id,previous in saved.items():self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        for row in db.execute("SELECT j.id,j.item FROM jobs j JOIN media_files m ON m.id=j.item WHERE j.kind='download_media' AND j.status='unavailable' AND j.reason_code='media-metadata-changed' AND m.publication_id=%s AND m.status='ready'",(item,)).fetchall():
            if self.store.media.file(item,row['item']) is None:
                continue
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous=media_candidates(self.store,row['id'],self.store.wall_clock()) if row['id'] not in excluded else []
            changed=job_write(db,"UPDATE jobs SET status='done',reason_code=NULL,reason_message=NULL WHERE id=%s AND status='unavailable' AND reason_code='media-metadata-changed' RETURNING id,OLD.reason_code AS previous_code",(row['id'],)).fetchone()
            if changed:source_rows.append(changed)
            if previous:self.store.problems.sync_media_problem(row['id'],previous,self.store.wall_clock())
        media_targets=[]
        owner=db.execute(f"SELECT {owner_expr('p')} AS feed_id FROM publications p WHERE p.id=%s",(item,)).fetchone()
        captions_allowed=bool(owner and owner['feed_id'] and self.store.subscriptions.youtube_theses(owner['feed_id']))
        for row in db.execute("SELECT m.id,m.resource_key,j.id AS job_id,j.reason_message,j.reason_code FROM media_files m LEFT JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.status='pending_download'", (item,)).fetchall():
            if row['resource_key']=='youtube:subtitles' and not captions_allowed:
                continue
            media_targets.append(row['id'])
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous=media_candidates(self.store,row['job_id'],self.store.wall_clock()) if row['job_id'] is not None and row['job_id'] not in excluded and (row['reason_message'] is not None or row['reason_code'] is not None) else []
            now,wall=self.store.clock(),self.store.wall_clock()
            changed_source=job_write(db, "INSERT INTO jobs ( kind , item , queued_wall ) VALUES ( 'download_media' , %s , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , generation = jobs . generation + 1 , reason_code = NULL , reason_message = NULL , next = %s + greatest ( jobs . retry_delay , coalesce ( jobs . retry_at , 0 ) - %s , 0 ) , retry_at = greatest ( coalesce ( jobs . retry_at , 0 ) , %s + jobs . retry_delay ) , wait_scope = CASE WHEN jobs . retry_at > %s THEN jobs . wait_scope ELSE NULL END WHERE (jobs . status IN ( 'cancelled' , 'done' ) OR (jobs.status='unavailable' AND jobs.reason_code='media-metadata-changed')) RETURNING id,OLD.reason_code AS previous_code",(row['id'],now,wall,wall,wall)).fetchone()
            if changed_source:source_rows.append(changed_source)
            if previous and row['job_id'] is not None:self.store.problems.sync_media_problem(row['job_id'],previous,self.store.wall_clock())
        if source_rows:source_groups.sync_changed_rows(self.store,[row for row in source_rows if row['id'] not in skipped_source],self.store.wall_clock())
        if processor_affected:self.store.problems.finish_processor_change(processor_affected,processor_previous,self.store.wall_clock())
        if db.in_transaction:
            self.store.incomplete_draft.mark_materials([item])
            self.store.incomplete_draft.mark_jobs([('download_media',row['item']) for row in cancelled]+[('download_media',identity) for identity in media_targets])


    def retry_media(self, item: str, *, revalidate_ready: bool = True) -> None:
        """Manual retry preserves source deadlines and the accumulated backoff."""
        store = self.store
        memberships.lock_graph(store.db)
        from oko.storage import source_groups
        source_rows: list[Any]=[]
        affected=store.problems.processor_jobs_for_materials([item])
        previous=store.problems.prepare_processor_change(affected,store.wall_clock())
        owner=store.db.execute(f"SELECT i.platform, {owner_expr('i')} AS feed_id FROM publications i WHERE i.id = %s",(item,)).fetchone()
        captions_allowed=not owner or owner['platform']!='youtube' or store.subscriptions.youtube_theses(owner['feed_id'])
        from oko.storage.media_paths import existing_file
        for file in store.media.publication_rows(item):
            if file['status']!='ready' or existing_file(store.root,file['storage_key']) is not None:
                continue
            store.db.execute("UPDATE media_files SET status='pending_download',storage_key=NULL WHERE id=%s AND status='ready'",(file['id'],))
            job_write(store.db,"""INSERT INTO jobs(kind,item,status,queued_wall) VALUES ('download_media',%s,'pending',extract(epoch FROM clock_timestamp()))
                ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=jobs.generation+1,
                  queued_wall=excluded.queued_wall,
                  next=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.next ELSE 0 END,
                  retry_at=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.retry_at ELSE NULL END,
                  reason_code=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.reason_code ELSE NULL END,
                  reason_message=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.reason_message ELSE NULL END""",
                (file['id'],store.wall_clock(),store.wall_clock(),store.wall_clock(),store.wall_clock()))
        rows = store.db.execute("SELECT j . * , m . resource_key , m . status AS media_status FROM jobs j JOIN media_files m ON m . id = j . item WHERE m . publication_id = %s AND j . kind = 'download_media' AND ( j . status IN ( 'failed' , 'unavailable' , 'pending' ) OR j . status = 'done' AND m . status = 'unavailable' )", (item,)).fetchall()
        for row in rows:
            if row['media_status']=='ready' and not revalidate_ready or row['resource_key']=='youtube:subtitles' and not captions_allowed:
                continue
            waiting = bool(row['wait_scope'] and row['retry_at'] and row['retry_at']>store.wall_clock())
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous_media=media_candidates(store,row['id'],store.wall_clock()) if row['reason_message'] is not None or row['reason_code'] is not None else []
            changed_source=job_write(store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , generation = generation + 1 , next = %s , retry_at = %s , reason_code = NULL , reason_message = NULL WHERE id = %s AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code", (row['next'] if waiting else 0, row['retry_at'] if waiting else None,
                row['id'])).fetchone()
            if changed_source:source_rows.append(changed_source)
            store.db.execute("UPDATE media_files SET status = 'pending_download' WHERE id = %s AND status != 'ready'", (row['item'],))
            if previous_media:store.problems.sync_media_problem(row['id'],previous_media,store.wall_clock())

        if rows:
            store.processing.reconcile_accepted_item(item)
        if source_rows:source_groups.sync_changed_rows(store,source_rows,store.wall_clock())
        if affected:store.problems.finish_processor_change(affected,previous,store.wall_clock())
        if store.db.in_transaction:
            store.incomplete_draft.mark_materials([item])
            store.incomplete_draft.mark_jobs([('download_media',row['item']) for row in rows])


    def revalidate_media(self, identity: str, *, manual: bool = True, sync_processor: bool = True) -> None:
        store=self.store
        row=store.db.execute(f"SELECT m.resource_key, i.platform, {owner_expr('i')} AS feed_id FROM media_files m JOIN publications i ON i.id=m.publication_id WHERE m.id = %s AND m.status = 'ready'",(identity,)).fetchone()
        if row is None or row['resource_key']=='youtube:subtitles' and not store.subscriptions.youtube_theses(row['feed_id']):
            return
        memberships.lock_graph(store.db)
        affected=store.problems.processor_jobs_for_materials([identity]) if sync_processor else []
        previous=store.problems.prepare_processor_change(affected,store.wall_clock()) if sync_processor else None
        changed_source=job_write(store.db, "INSERT INTO jobs ( kind , item , manual_once , queued_wall ) VALUES ( 'download_media' , %s , true , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , manual_once = true , generation = jobs . generation + 1 , reason_code = NULL , reason_message = NULL , next = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . next ELSE 0 END , retry_at = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . retry_at ELSE NULL END WHERE (jobs . status IN ( 'done' , 'cancelled' ) OR ( %s = 1 AND jobs . status IN ( 'failed' , 'unavailable' ) )) AND CASE WHEN jobs.reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code",
            (identity,store.wall_clock(),store.wall_clock(),manual)).fetchone()
        if changed_source and (changed_source['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(store,[changed_source],store.wall_clock())
        if affected:store.problems.finish_processor_change(affected,previous,store.wall_clock())
        if store.db.in_transaction:store.incomplete_draft.mark_job('download_media',identity)


    def retry_linked(self, item: str) -> None:
        for row in self.store.db.execute('SELECT p.id,p.status FROM publication_links l JOIN publications p ON p.id=l.target_publication_id WHERE l.source_publication_id=%s',(item,)).fetchall():
            if row['status']=='pending_collection':
                self.enqueue_fetch(row['id'])
                self.store.db.execute("UPDATE jobs SET manual_once = true WHERE kind = 'fetch' AND item = %s",(row['id'],))
            self.retry_media(row['id'],revalidate_ready=False)

    def cancel_history(self, feed: str) -> None:
        from oko.storage import memberships,source_groups
        memberships.lock_graph(self.store.db)
        processor_affected=[row['id'] for row in self.store.db.execute("SELECT j.id FROM history_members h JOIN jobs j ON j.item=h.item AND j.kind='fetch' WHERE h.feed=%s AND (j.processor_visible OR j.reason_code='processor-config')",(feed,))]
        processor_previous=self.store.problems.prepare_processor_change(processor_affected,self.store.wall_clock())
        effects=CollectionDiagnostics()
        affected={row['item'] for row in self.store.db.execute("SELECT j.item FROM history_members h JOIN jobs j ON j.item=h.item AND j.kind='fetch' WHERE h.feed=%s AND ((j.reason_code IS NULL AND j.reason_message IS NOT NULL) OR (j.reason_code IS NOT NULL AND j.reason_code NOT LIKE 'source-%%'))",(feed,))}
        publication_links.capture_fetch_problems(self.store,effects,affected)
        source_rows=job_write(self.store.db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , reason_code = NULL , reason_message = NULL WHERE kind = 'fetch' AND status <> 'done' AND item IN ( SELECT item FROM history_members WHERE feed = %s ) RETURNING id,item,OLD.reason_code AS previous_code",(feed,)).fetchall()
        source_groups.sync_changed_rows(self.store,list(source_rows),self.store.wall_clock())
        if source_rows:self.store.incomplete_draft.mark_materials([row['item'] for row in source_rows])
        for job_id,previous in effects.fetch_problems_before.items():
            self.store.problems.sync_fetch(job_id,previous,self.store.wall_clock())
        if processor_affected:self.store.problems.finish_processor_change(processor_affected,processor_previous,self.store.wall_clock())

    def backfill_processing_inputs(self) -> None:
        for _ in self.backfill_processing_steps():
            pass

    def backfill_processing_steps(self) -> Iterator[None]:
        if not self.store.db.transaction_depth:
            raise RuntimeError('Input recovery requires an owning transaction')
        for kind in ('filter', 'theses'):
            # The partial index excludes unrelated completed work.
            identities = [row[0] for row in self.store.db.execute("SELECT id FROM jobs WHERE kind=%s AND input_snapshot IS NOT NULL AND (payload::jsonb->>'input_revision') IS NULL", (kind,))]
            for identity in identities:
                if not self.store.db.transaction_depth:
                    raise RuntimeError('Input recovery requires an owning transaction')
                row = self.store.db.execute("SELECT id,item,input_snapshot->>'input_hash' AS input_hash FROM jobs WHERE id=%s AND kind=%s AND input_snapshot IS NOT NULL AND (payload::jsonb->>'input_revision') IS NULL", (identity,kind)).fetchone()
                if row is not None and row['input_hash']==processing_input.input_hash(self.store,row['item'],kind):
                    revision = processing_input.revision(self.store,row['item'])
                    self.store.db.execute("UPDATE jobs SET payload=jsonb_set(payload,'{input_revision}',to_jsonb(%s::text)) WHERE id=%s", (revision,row['id']))
                yield

    def replace_media_input(self, job: Job) -> None:
        memberships.lock_graph(self.store.db)
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous=media_candidates(self.store,job.id,self.store.wall_clock())
        changed=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , generation = generation + 1 , next = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN next ELSE 0 END , reason_message = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN reason_message ELSE NULL END WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN reason_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.reason_code AS previous_code",(self.store.wall_clock(),self.store.wall_clock(),job.id,job.generation)).fetchone()
        if changed and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
        if changed and previous:self.store.problems.sync_media_problem(job.id,previous,self.store.wall_clock())
        if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def invalidate_processing(self, feeds: set[str] | None = None) -> None:
        """Revoke stale active attempts using current facts and Job generations."""
        from oko.storage import memberships,source_groups
        memberships.lock_graph(self.store.db)
        rows=self.store.db.execute("""SELECT j.id,j.item,j.kind,j.payload,j.reason_code,j.reason_message,j.processor_visible
            FROM jobs j JOIN publications p ON p.id=j.item
            WHERE j.kind IN ('filter','theses')
            AND (j.status IN ('pending','running','blocked','failed','unavailable') OR j.processor_visible)
            AND (%s::text[] IS NULL OR p.feed_id=ANY(%s::text[]) OR EXISTS (
                SELECT 1 FROM publication_links l JOIN publications target ON target.id=l.target_publication_id
                WHERE l.source_publication_id=p.id AND target.feed_id=ANY(%s::text[])))""",
            (list(feeds) if feeds is not None else None,)*3).fetchall()
        stale=[]
        for start in range(0,len(rows),400):
            batch=rows[start:start+400]
            identities={row['item'] for row in batch if row['item']}
            revisions={}
            if identities:
                with material_read.material_inputs(self.store.db,identities):
                    revisions={identity:processing_input.revision(self.store,identity) for identity in identities}
            for row in batch:
                current=revisions.get(row['item'])
                if row['payload'].get('input_revision')!=current and row['payload'].get('preserved_source_revision')!=current:
                    stale.append(row)
        if not stale:return
        ids=[row['id'] for row in stale]
        affected=[row['id'] for row in stale if row['processor_visible'] or row['reason_code']=='processor-config']
        processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        stage_previous: dict[int,list[dict[str,Any]]]={}
        for start in range(0,len(ids),400):
            stage_previous.update(self.store.problems.saved_processing_candidates(ids[start:start+400]))
        changed=job_write(self.store.db,"""UPDATE jobs SET
            status=CASE WHEN status='running' THEN 'pending' ELSE status END,generation=generation+1,
            reason_message=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN reason_message ELSE NULL END,
            reason_code=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN reason_code ELSE NULL END,
            next=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN next ELSE 0 END,
            retry_at=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN retry_at ELSE NULL END
            WHERE id=ANY(%s) RETURNING id,kind,item,OLD.reason_code AS previous_code""",
            (*([self.store.wall_clock()]*4),ids)).fetchall()
        source_groups.sync_changed_rows(self.store,list(changed),self.store.wall_clock())
        for row in changed:
            if row['kind']=='filter':self.store.processing.filter_changed(row['item'])
        if changed:self.store.incomplete_draft.mark_materials([row['item'] for row in changed])
        if affected:self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock())
        if stage_previous:self.store.problems.sync_processing_problems(stage_previous,self.store.wall_clock())
