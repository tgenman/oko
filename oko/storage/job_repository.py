"""Attempt identity, conditional transitions and saved execution parameters."""
from oko.storage.job_writes import execute as job_write
from oko.storage.publication_owners import owned_ids, owner_expr
from oko.storage.database import expect_row, Database, Row
import json
import shutil
from oko.domain.collection import CollectionEffects
from pathlib import Path
from typing import Any
from collections.abc import Callable,Iterator
from contextlib import contextmanager,nullcontext
from oko.storage.transaction import transaction
from oko.storage.source_configuration import changed as source_changed
from oko.storage.reader_status import changed as status_changed
from typing import TYPE_CHECKING
from oko.storage import collection_jobs, material_read, processing_input, memberships
from oko.collection.adapters import AdapterIssue, AdapterErrorKind
from oko.collection.contracts import SourceManifest
from oko.collection.external_media import expected_incomplete
from oko.storage import publication_links, youtube_media
from oko.diagnostics import record_caught
if TYPE_CHECKING:
    from oko.storage.store import Store

from oko.storage.job_types import Job

from oko.domain.processing import Kind, ProcessingInput

class JobRejected(Exception):
    pass


class JobRepository:

    def __init__(self, store: 'Store') -> None:
        self.store=store

    def after_commit(self, callback: Callable[[], None]) -> None:
        """Publish an attempt's in-memory completion only after its transaction commits."""
        self.store.db.after_commit(callback)

    def valid(self, job: Job) -> bool:
        row=self.store.db.execute('SELECT status , generation , kind , item FROM jobs WHERE id = %s',(job.id,)).fetchone()
        return bool(row and row['status']=='running' and row['generation']==job.generation and row['kind']==job.kind and row['item']==job.item)

    def finish(self, job: Job) -> None:
        if job.kind not in ('read','discover','feed-delete'):memberships.lock_graph(self.store.db)
        processor_previous=self.store.problems.processor_history_before_finish(job.id,self.store.wall_clock())
        previous = self.store.problems.deletion_candidates(job.id,self.store.wall_clock()) if job.kind=='delete' else []
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        previous_fetch=managed_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='fetch' else []
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
        discover_before = self.store.db.execute("SELECT problem_code,error FROM jobs WHERE id=%s AND kind='discover'",(job.id,)).fetchone() if job.kind=='discover' else None
        changed=job_write(self.store.db, "UPDATE jobs SET status = 'done' , error = NULL , problem_code = NULL , source_event_owned = false , manual_once = 0 , retry_at = NULL , retry_delay = 0 , wait_scope = NULL WHERE id = %s AND generation = %s AND status = 'running' AND kind = %s AND item = %s AND CASE WHEN kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.error AS previous_error, OLD.problem_code AS previous_code",
            (job.id,job.generation,job.kind,job.item)).fetchone()
        if changed is None:
            raise JobRejected('Attempt no longer owns this job')
        self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
        if job.kind in ('fetch','download_media','filter','theses','delete','feed-delete') and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync(self.store,job.kind,source_groups.job_keys(self.store,job.id,changed['previous_code']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(job.id,changed['previous_code'])]))
        if job.kind=='delete' and previous:
            self.store.problems.sync_deletion(job.id,previous,self.store.wall_clock())
        if job.kind=='read':
            self.store.problems.sync_read_feed(job.item,self.store.wall_clock())
        if job.kind=='fetch' and previous_fetch:
            self.store.problems.sync_fetch(job.id,previous_fetch,self.store.wall_clock())
        if job.kind=='download_media' and previous_media:
            self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock(),successful=True)
        if job.kind in ('filter','theses') and (changed['previous_error'] is not None or changed['previous_code'] is not None):
            previous_processing=self.store.problems.saved_processing_candidates([job.id]).get(job.id,[])
            if previous_processing:self.store.problems.sync_processing_problem(job.id,previous_processing,self.store.wall_clock())
        if job.kind=='discover':
            codes=((discover_before['problem_code'] or 'work-discover'),) if discover_before and (discover_before['problem_code'] is not None or discover_before['error'] is not None) else ()
            self.store.problems.sync_discover(job.id,self.store.wall_clock(),previous_codes=codes)
        self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)
        if job.kind=='filter':
            self.store.processing.filter_changed(job.item)

    def reconcile(self, kind: Kind, candidates: list[tuple[str, str | None, bool, str]], *, preserve_cancelled: bool = False) -> None:
        memberships.lock_graph(self.store.db)
        db=self.store.db
        def sync_source_after(row: Any) -> None:
            if row and (row['previous_code'] or '').startswith('source-'):
                from oko.storage import source_groups
                before=source_groups.job_keys(self.store,row['id'],row['previous_code'])
                complete=source_groups.sync(self.store,kind,before | source_groups.job_keys(self.store,row['id']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(row['id'],row['previous_code'])]))
                job_write(db,"UPDATE jobs SET source_event_owned=(%s=1) AND (problem_code LIKE 'source-%%') IS TRUE WHERE id=%s",(complete,row['id']))
        affected: list[int]=[]
        items=[row[0] for row in candidates]
        for start in range(0,len(items),400):
            affected.extend(row['id'] for row in db.execute("SELECT id FROM jobs WHERE kind=%s AND item=ANY(%s) AND (processor_visible OR problem_code='processor-config')",(kind,items[start:start+400])))
        processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        for item,signature,satisfied,revision in candidates:
            job=db.execute("SELECT status,payload,problem_code,input_snapshot->>'signature' AS signature,id,error FROM jobs WHERE kind=%s AND item=%s",(kind,item)).fetchone()
            previous=job['signature'] if job else None
            def capture_problem() -> list[dict[str,Any]]:
                from oko.storage.processing_job_problems import managed_candidates as processing_candidates
                return processing_candidates(self.store,job['id'],self.store.wall_clock()) if job and job['problem_code'] not in (None,'','processor-config','ai-permission','codex-choice','youtube-admission') and not job['problem_code'].startswith('source-') else []
            previous_problem: list[dict[str,Any]]=[]
            enabled = processing_input.stage_enabled(self.store, item, kind) if signature is not None else False
            if preserve_cancelled and job and job['status']=='cancelled' and (previous is None or previous==signature):
                continue
            if job and job['status']=='done' and previous is not None and previous==signature:
                if json.loads(job['payload']).get('input_revision') != revision:
                    job_write(db, "UPDATE jobs SET payload=jsonb_set(payload::jsonb,'{input_revision}',to_jsonb(%s::text))::text WHERE kind=%s AND item=%s", (revision,kind,item))
                continue
            if previous is not None and previous==signature and job and (job['status']=='running' or json.loads(job['payload']).get('input_revision')==revision):
                # Reconsider blocked work only on an input/settings event. Running
                # attempts are allowed to finish when the switch is turned off.
                if job['status'] == 'blocked' and enabled:
                    if json.loads(job['payload']).get('preclaim_blocked') and not processing_input.can_start(
                            self.store,item,processing_input.processing_feed(self.store,item,kind)):
                        continue
                    previous_problem=capture_problem()
                    source_changed=job_write(db, """UPDATE jobs SET status='pending',
                        error=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE error END,
                        problem_code=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE problem_code END,
                        payload=(payload::jsonb-'preclaim_blocked')::text
                        WHERE kind=%s AND item=%s AND status='blocked' AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code""", (kind, item)).fetchone()
                    sync_source_after(source_changed)
                    if kind=='filter':self.store.processing.filter_changed(item)
                    self.store.publications.status(item)
                    if previous_problem:self.store.problems.sync_processing_problem(job['id'],previous_problem,self.store.wall_clock())
                elif job['status'] == 'pending' and not enabled:
                    previous_problem=capture_problem()
                    source_changed=job_write(db, "UPDATE jobs SET status='blocked' WHERE kind=%s AND item=%s AND status='pending' AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code", (kind, item)).fetchone()
                    sync_source_after(source_changed)
                    if kind=='filter':self.store.processing.filter_changed(item)
                    self.store.publications.status(item)
                    if previous_problem:self.store.problems.sync_processing_problem(job['id'],previous_problem,self.store.wall_clock())
                continue
            if signature is None and previous is None and (job is None or job[0]=='cancelled'):
                continue
            if signature is None:
                if kind=='theses' and job and job['status']=='blocked' and processing_input.stage_enabled(self.store,item,'theses'):
                    filtering = db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
                    if filtering and filtering['status'] in ('pending','running'):
                        continue
                previous_problem=capture_problem()
                cancelled=job_write(db, "UPDATE jobs SET status='cancelled', generation=generation+1, error=NULL, problem_code=NULL, source_event_owned=false, input_snapshot=NULL WHERE kind=%s AND item=%s AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",(kind,item)).fetchone()
                sync_source_after(cancelled)
                if kind=='filter' and job:self.store.processing.filter_changed(item)
            else:
                previous_problem=capture_problem()
                source_changed=job_write(db, "INSERT INTO jobs (kind,item,status,payload,input_snapshot) VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (kind,item) DO UPDATE SET status=excluded.status,payload=excluded.payload,input_snapshot=excluded.input_snapshot,generation=jobs.generation+1,next=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.next ELSE 0 END,error=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.error ELSE NULL END,problem_code=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.problem_code ELSE NULL END WHERE CASE WHEN jobs.problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",
                    (kind,item,'done' if satisfied else 'pending' if enabled else 'blocked',json.dumps(dict(input_revision=revision)),json.dumps(dict(signature=signature)),self.store.wall_clock(),self.store.wall_clock(),self.store.wall_clock())).fetchone()
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
        snapshot = dict(signature=work.signature, generation=job.generation, feed_id=selected['feed_id'],
                        rules=selected['values'], public_ai=work.public_ai, no_input=work.no_input,
                        sources=[dict(id=source.id, version=source.version) for source in work.request.sources])
        changed = self.store.db.execute("UPDATE jobs SET input_snapshot=%s::jsonb WHERE id=%s AND generation=%s AND status='running' AND input_snapshot->>'signature'=%s",
            (json.dumps(snapshot, ensure_ascii=False), job.id, job.generation, work.signature)).rowcount
        if changed != 1:
            raise JobRejected('Attempt no longer owns this input')

    def claim_processing(self, kind: Kind, now: float) -> Job | None:
        # The common claim code retains monotonic generations and interrupted choices.
        return self.store.jobs.next_job(now,ai_only=True,processing_kind=kind)

    def _revoke_running_input(self, row: Any) -> None:
        previous=self.store.problems.saved_processing_candidates([row['id']]).get(row['id'],[]) if row['error'] is not None or row['problem_code'] is not None else []
        processor_previous=self.store.problems.prepare_processor_change([row['id']],self.store.wall_clock())
        changed=job_write(self.store.db, """UPDATE jobs SET status='pending',generation=generation+1,input_snapshot=NULL,
            next=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN next ELSE 0 END,
            error=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN error ELSE NULL END,
            problem_code=CASE WHEN wait_scope IS NOT NULL AND retry_at>%s THEN problem_code ELSE NULL END
            WHERE id=%s AND status='running' AND generation=%s RETURNING id,OLD.problem_code AS previous_code""",
            (self.store.wall_clock(),self.store.wall_clock(),self.store.wall_clock(),row['id'],row['generation'])).fetchone()
        if changed:
            if (changed['previous_code'] or '').startswith('source-'):
                from oko.storage import source_groups
                source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
            self.store.problems.finish_processor_change([row['id']],processor_previous,self.store.wall_clock())
            if previous:self.store.problems.sync_processing_problem(row['id'],previous,self.store.wall_clock())
            self.store.incomplete_draft.mark_materials([row['item']])

    def revoke_changed_running_inputs(self) -> None:
        """Settings writes revoke only running attempts whose stage input changed."""
        memberships.lock_graph(self.store.db)
        from oko.storage.processing_input import signature
        rows = self.store.db.execute("""SELECT id,kind,item,generation,error,problem_code,input_snapshot->>'signature' AS signature
            FROM jobs WHERE status='running' AND kind IN ('filter','theses')""").fetchall()
        for row in rows:
            if row['signature'] is None or signature(self.store,row['item'],row['kind']) != row['signature']:
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
            rows=self.store.db.execute("""SELECT id,kind,item,generation,error,problem_code,status,input_snapshot,payload
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
        before=db.execute("SELECT id,error,problem_code,processor_visible FROM jobs WHERE kind=%s AND item=%s AND input_snapshot IS NOT NULL",(kind,item)).fetchone()
        if before is None:
            return False
        stage_previous=self.store.problems.saved_processing_candidates([before['id']]).get(before['id'],[]) if before['error'] is not None or before['problem_code'] is not None else []
        affected=[before['id']] if before['processor_visible'] or before['problem_code']=='processor-config' else []
        previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        self.store.jobs.forget_execution(kind,item)
        changed=job_write(db, "UPDATE jobs SET status = 'pending' , next = CASE WHEN wait_scope IS NOT NULL THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL THEN retry_at ELSE NULL END , error = CASE WHEN wait_scope IS NOT NULL THEN error ELSE NULL END , generation = generation + 1 WHERE kind = %s AND item = %s AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",(kind,item)).fetchone()
        if changed and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
        if kind=='filter':self.store.processing.filter_changed(item)
        if affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())
        if stage_previous:self.store.problems.sync_processing_problem(before['id'],stage_previous,self.store.wall_clock())
        self.store.incomplete_draft.mark_materials([item])
        return True

    def origin(self, job: Job, signature: str) -> dict[str, Any] | None:
        return self.store.jobs.saved_execution(job,signature)

    def save_origin(self, job: Job, signature: str, value: dict[str, Any]) -> None:
        self.store.jobs.save_execution(job,signature,value)

    def fail(self, job: Job, error: str, *, code: str) -> None:
        boundary=nullcontext() if self.store.db.in_transaction else transaction(self.store.db)
        with boundary:
            valid=self.valid(job)
            collection_jobs.fail(self.store,job,AdapterIssue(code,error,AdapterErrorKind.temporary))
            if valid and job.kind=='filter':self.store.processing.filter_changed(job.item)
            if not processing_input.stage_enabled(self.store, job.item, job.kind):
                previous=self.store.problems.saved_processing_candidates([job.id]).get(job.id,[])
                changed=job_write(self.store.db, "UPDATE jobs SET status='blocked' WHERE id=%s AND generation=%s AND status='pending' AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code", (job.id, job.generation)).fetchone()
                if changed and (changed['previous_code'] or '').startswith('source-'):
                    from oko.storage import source_groups
                    source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
                if previous:self.store.problems.sync_processing_problem(job.id,previous,self.store.wall_clock())
                self.store.publications.status(job.item)

    def cancel(self, job: Job) -> None:
        if job.kind not in ('read','discover','feed-delete'):memberships.lock_graph(self.store.db)
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        previous=managed_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='fetch' else []
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
        processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock())
        from oko.storage.processing_job_problems import managed_candidates as processing_candidates
        previous_processing=processing_candidates(self.store,job.id,self.store.wall_clock()) if job.kind in ('filter','theses') else []
        changed=job_write(self.store.db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.problem_code AS previous_code",(job.id,job.generation)).fetchone()
        if changed:self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
        if changed and job.kind in ('fetch','download_media','filter','theses','delete','feed-delete') and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            complete=source_groups.sync(self.store,job.kind,source_groups.job_keys(self.store,job.id,changed['previous_code']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(job.id,changed['previous_code'])]))
            job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND status IN ('pending','running','failed','unavailable','blocked') WHERE id=%s",(complete,job.id))
        if changed and job.kind=='filter':self.store.processing.filter_changed(job.item)
        if changed and previous:self.store.problems.sync_fetch(job.id,previous,self.store.wall_clock())
        if changed and previous_media:self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock())
        if changed and previous_processing:self.store.problems.sync_processing_problem(job.id,previous_processing,self.store.wall_clock())
        if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def block(self, job: Job) -> None:
        if job.kind in ('fetch','download_media','filter','theses','delete','feed-delete'):
            from oko.storage import memberships
            memberships.lock_graph(self.store.db)
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        previous=managed_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='fetch' else []
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
        from oko.storage.processing_job_problems import managed_candidates as processing_candidates
        previous_processing=processing_candidates(self.store,job.id,self.store.wall_clock()) if job.kind in ('filter','theses') else []
        changed=job_write(self.store.db, "UPDATE jobs SET status = 'blocked' WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.problem_code AS previous_code", (job.id, job.generation)).fetchone()
        if changed and job.kind in ('fetch','download_media','filter','theses','delete','feed-delete') and (changed['previous_code'] or '').startswith('source-'):
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


    def enqueue_discovery(self, feed: str = 'demo', next_at: float = 0) -> None:
        # Monotonic deadlines belong to this process; persist their wall-clock
        # equivalent so a continuation keeps its remaining delay after restart.
        retry_at = self.store.wall_clock()+max(0,next_at-self.store.clock()) if next_at else None
        with self.store.db:
            prior=self.store.db.execute("SELECT id,processor_visible,problem_code FROM jobs WHERE kind='discover' AND item=%s",('' if feed=='demo' else feed,)).fetchone()
            affected=[prior['id']] if prior and (prior['processor_visible'] or prior['problem_code']=='processor-config') else []
            previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
            changed=job_write(self.store.db, """INSERT INTO jobs(kind,item,next,retry_at,queued_wall)
                VALUES ('discover',%s,%s,%s,extract(epoch FROM clock_timestamp()))
                ON CONFLICT(kind,item) DO UPDATE SET queued_wall=excluded.queued_wall,status='pending',
                    next=excluded.next,retry_at=excluded.retry_at,error=NULL WHERE jobs.status='done' RETURNING id,error,problem_code""",
                ('' if feed=='demo' else feed,next_at,retry_at)).fetchone()
            if changed and (changed['error'] is not None or changed['problem_code'] is not None):self.store.problems.sync_discover(changed['id'],self.store.wall_clock())
            if changed and affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())

    def refresh_feed(self, feed: str) -> None:
        """Admit one manual refresh without restarting work already in progress."""
        with self.store.db:
            for row in self.store.db.execute('SELECT DISTINCT i . id FROM publications i JOIN feed_publications owner ON owner.publication_id=i.id AND owner.is_primary AND owner.is_unviewed JOIN media_files m ON m . publication_id = i . id WHERE owner.feed_id = %s AND i . viewed = 0', (feed,)).fetchall():
                self.retry_media(row['id'],revalidate_ready=False)
        discovery = '' if feed == 'demo' else feed
        active = self.store.db.execute("SELECT 1 FROM jobs WHERE kind = 'discover' AND item = %s AND ( status = 'running' OR ( status = 'pending' AND error IS NULL AND next <= %s ) )", (discovery,self.store.clock())).fetchone()
        with self.store.db:
            memberships.lock_graph(self.store.db)
            history = self.store.subscriptions.history.status(feed)
            if history and not history.get('cancelled'):
                self.store.subscriptions.history.pause(feed, False)
            if not active:
                self.enqueue_discovery(feed)
                changed_discover=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , next = CASE WHEN problem_code LIKE '%%FloodWaitError' OR problem_code = 'youtube-admission' OR ( wait_scope IS NOT NULL AND retry_at > %s ) THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END , generation = generation + 1 WHERE kind = 'discover' AND item = %s AND status != 'running' RETURNING id,error,problem_code", (self.store.wall_clock(),self.store.wall_clock(),discovery)).fetchone()
                if changed_discover and (changed_discover['error'] is not None or changed_discover['problem_code'] is not None):self.store.problems.sync_discover(changed_discover['id'],self.store.wall_clock())
                if changed_discover and changed_discover['problem_code']=='processor-config':self.store.problems.finish_processor_change([changed_discover['id']],None,self.store.wall_clock())
            source_ids: list[int]=[]
            from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
            for row in self.store.db.execute("SELECT i . id , i . members , i . source_deleted , j . id AS job_id, j . error , j . problem_code, j . status FROM publications i JOIN feed_publications owner ON owner.publication_id=i.id AND owner.is_primary AND owner.is_unviewed JOIN jobs j ON j . item = i . id AND j . kind = 'fetch' WHERE owner.feed_id = %s AND i . viewed = 0 AND j . status != 'running'", (feed,)).fetchall():
                material = self.store.material(row['id'])
                unavailable = row['source_deleted']
                manifest=SourceManifest.model_validate_json(json.dumps(self.store.original(row['id'])[0]['source_manifest'])) if material and material.get('complete') and self.store.original(row['id'])[0].get('source_manifest') else None
                if material and not row['error'] and not unavailable and (material.get('full',False) or expected_incomplete(manifest)) and not self.store._needs_collection(row['id']):
                    continue
                if row['members'] != '[]' and material and material.get('full',False):
                    continue
                if row['error'] or unavailable or not material or not material.get('full',False):
                    prior=managed_candidates(self.store,row['job_id'],self.store.wall_clock()) if row['error'] is not None or row['problem_code'] is not None else []
                    changed_fetch=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , manual_once = CASE WHEN ( status = 'done' AND error IS NOT NULL ) OR EXISTS ( SELECT 1 FROM publications i WHERE i.id=jobs.item AND i.source_deleted ) THEN 1 ELSE manual_once END , status = 'pending' , next = CASE WHEN problem_code LIKE '%%FloodWaitError' OR problem_code = 'youtube-admission' OR ( wait_scope IS NOT NULL AND retry_at > %s ) THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END , generation = generation + 1 WHERE kind = 'fetch' AND item = %s AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,processor_visible,problem_code", (self.store.wall_clock(),self.store.wall_clock(),row['id'])).fetchone()
                    if changed_fetch and (changed_fetch['processor_visible'] or changed_fetch['problem_code']=='processor-config'):
                        self.store.problems.finish_processor_change([changed_fetch['id']],None,self.store.wall_clock())
                    if prior:self.store.problems.sync_fetch(row['job_id'],prior,self.store.wall_clock())
                    self.store.incomplete_draft.mark_materials([row['id']])
                    if changed_fetch and (changed_fetch['problem_code'] or '').startswith('source-'):source_ids.append(changed_fetch['id'])
            if source_ids:
                from oko.storage import source_groups
                source_groups.sync_jobs(self.store,source_ids,self.store.wall_clock())
            for kind in ('filter','theses'):
                for row in self.store.db.execute(f'SELECT j.item FROM jobs j JOIN ({owned_ids("= %s")}) owner ON owner.id=j.item WHERE j.kind = %s AND j.error IS NOT NULL',(feed,feed,kind)).fetchall():
                    self.retry_processing(row[0],kind)


    def retry_material(self, item: str) -> bool:
        if not self.store._has_unviewed(item):
            return False
        with self.store.db:
            self.retry_linked(item)
        with self.store.db:
            self.retry_media(item,revalidate_ready=False)
            self.enqueue_fetch(item)
            self.store.db.execute("UPDATE jobs SET manual_once = 1 WHERE kind = 'fetch' AND item = %s",(item,))
        return True


    def enqueue_fetch(self, item: str, *, sync_problem: bool = True, sync_processor: bool = True, sync_source: bool = True) -> None:
        memberships.lock_graph(self.store.db)
        from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
        before = self.store.db.execute("SELECT id,status,error,next,problem_code,processor_visible FROM jobs WHERE kind='fetch' AND item=%s", (item,)).fetchone()
        previous=managed_candidates(self.store,before['id'],self.store.wall_clock()) if sync_problem and before and (before['error'] is not None or before['problem_code'] is not None) else []
        affected=[before['id']] if sync_processor and before and (before['processor_visible'] or before['problem_code']=='processor-config') else []
        processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        changed_source=self.store.db.execute("INSERT INTO jobs ( kind , item , queued_wall ) VALUES ( 'fetch' , %s , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , manual_once = 0 , next = CASE WHEN jobs . problem_code LIKE '%%FloodWaitError' OR ( jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s ) THEN jobs . next ELSE 0 END , error = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . error ELSE NULL END , retry_at = CASE WHEN jobs . problem_code LIKE '%%FloodWaitError' OR ( jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s ) THEN jobs . retry_at ELSE NULL END , generation = jobs . generation + 1 WHERE CASE WHEN jobs.problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code", (item,self.store.wall_clock(),self.store.wall_clock(),self.store.wall_clock())).fetchone()

        after = self.store.db.execute("SELECT id,status,error,next,problem_code,processor_visible FROM jobs WHERE kind='fetch' AND item=%s", (item,)).fetchone()
        if sync_source and changed_source and (changed_source['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            complete=source_groups.sync(self.store,'fetch',source_groups.job_keys(self.store,changed_source['id'],changed_source['previous_code']) | source_groups.job_keys(self.store,changed_source['id']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(changed_source['id'],changed_source['previous_code'])]))
            job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND (problem_code LIKE 'source-%%') IS TRUE WHERE id=%s",(complete,changed_source['id']))
        if before != after:
            status_changed(self.store.db)
            if previous and after:self.store.problems.sync_fetch(after['id'],previous,self.store.wall_clock())
            if affected:self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock())
            self.store.incomplete_draft.mark_job('fetch',item)


    def next_job(self, now: float, deleting_only: bool = False, source_feed: str = 'demo', feeds: list[str] | None = None, ai_only: bool = False, include_deletions: bool = True, media_only: bool = False, captions_only: bool = False, dependency_feeds: list[str] | None = None, processing_kind: str | None = None) -> Job | None:
        if media_only and not ai_only and not deleting_only:
            if processing_kind is not None:
                return None
            available = feeds if feeds is not None else [source_feed]
            dependencies = available if dependency_feeds is None else dependency_feeds
            return self._claim(self._media_candidate(now, available, dependencies, captions_only))
        query = "SELECT * FROM jobs WHERE status = 'pending' AND next <= %s AND NOT ( coalesce ( problem_code , '' ) = 'ai-permission' AND error IS NOT NULL )"
        if processing_kind is not None:
            query += " AND kind=" + ("'filter'" if processing_kind=='filter' else "'theses'")
        if ai_only:
            query += """ AND kind IN ('filter','theses') AND NOT EXISTS (SELECT 1 FROM publications i WHERE i.id=jobs.item AND i.platform IS NOT NULL
                AND NOT ((jobs.kind='filter' AND i.status IN ('pending_filtering','filter_out'))
                      OR (jobs.kind='theses' AND (i.status IN ('pending_summary','ready')
                          OR (i.status='pending_filtering' AND i.processing_metadata::jsonb->'_pass'->>'feed_id' IS NULL)))))"""
        elif deleting_only:
            query += " AND kind IN ('delete','feed-delete')"
        elif media_only:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('%s' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('%s' for _ in dependencies) or "''"
            # Follow only dependencies of the requested roots.
            query += f""" AND kind='download_media' AND item IN (
                SELECT m.id FROM publications i JOIN feed_publications owner ON owner.publication_id=i.id AND owner.is_primary AND owner.is_unviewed JOIN media_files m ON m.publication_id=i.id
                    WHERE i.viewed=0 AND owner.feed_id IN ({placeholders})
                UNION SELECT m.id FROM publications root JOIN feed_publications owner ON owner.publication_id=root.id AND owner.is_primary AND owner.is_unviewed JOIN publication_links l ON l.source_publication_id=root.id
                    JOIN publications i ON i.id=l.target_publication_id JOIN media_files m ON m.publication_id=i.id
                    WHERE root.viewed=0 AND owner.feed_id IN ({dependency_keys}) AND i.viewed=0 AND NOT EXISTS (SELECT 1 FROM feed_publications own WHERE own.publication_id=i.id AND own.is_primary))"""
        else:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('%s' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('%s' for _ in dependencies) or "''"
            query += f""" AND kind IN ('delete','feed-delete','discover','read','fetch') AND id IN (WITH allowed AS MATERIALIZED (
                SELECT id FROM jobs WHERE status='pending' AND kind IN ('delete','feed-delete')
                UNION SELECT id FROM jobs WHERE status='pending' AND kind IN ('discover','read') AND item IN ({placeholders})
                UNION SELECT id FROM jobs WHERE status='pending' AND kind='fetch' AND item IN (
                    SELECT own.id FROM feed_publications owner JOIN publications own ON own.id=owner.publication_id
                        WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id IN ({placeholders}) AND own.viewed=0
                    UNION SELECT i.id FROM feed_publications owner JOIN publications root ON root.id=owner.publication_id
                        JOIN publication_links l ON l.source_publication_id=root.id JOIN publications i ON i.id=l.target_publication_id
                        WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id IN ({dependency_keys}) AND root.viewed=0 AND i.viewed=0
                        AND NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id AND m.is_primary))) SELECT id FROM allowed)"""
        if media_only:
            subtitles="SELECT 1 FROM media_files m JOIN publications i ON i . id = m . publication_id JOIN jsonb_array_elements(CASE WHEN jsonb_typeof(i.content::jsonb->'blocks')='array' THEN i.content::jsonb->'blocks' ELSE '[]'::jsonb END) b(value) ON b.value->>'media_id' = m.id WHERE m . id = jobs . item AND i . platform = 'youtube' AND b.value->>'role' = 'subtitles'"
            query += ' AND '+('EXISTS' if captions_only else 'NOT EXISTS')+' ('+subtitles+')'
        if not include_deletions:
            query += " AND kind NOT IN ('delete','feed-delete')"
        blocked_history = "SELECT m . item FROM history_members m JOIN initial_history h ON h . feed = m . feed WHERE ( h . paused = 1 OR ( h . scan_complete = 0 AND ( h.selection::jsonb->>'mode' = 'count' OR h . feed IN ( SELECT id FROM feeds WHERE transport = 'rss' ) ) ) )"
        # PostgreSQL primary keys reject NULL history memberships. A malformed
        # fetch without an item is never dispatchable; valid work uses the index.
        query += f""" AND NOT (kind='fetch' AND
            (jobs.item IS NULL OR EXISTS ({blocked_history} AND m.item=jobs.item OFFSET 0)))"""
        query += " AND NOT (kind='feed-delete' AND EXISTS (SELECT 1 FROM jobs d WHERE d.kind='delete' AND d.status!='done' AND d.payload::jsonb->>'cleanup_feed'=jobs.item))"
        query += " ORDER BY CASE kind WHEN 'delete' THEN 0 WHEN 'feed-delete' THEN 0 WHEN 'discover' THEN 1 WHEN 'filter' THEN 2 WHEN 'read' THEN 2 ELSE 3 END,id LIMIT 1"
        params = (now,) if deleting_only or ai_only else (now, *available, *dependencies) if media_only else (now, *('' if f == 'demo' else f for f in available), *available, *dependencies)
        row = self.store.db.execute(query, params).fetchone()
        if ai_only and row is not None and row['kind'] in ('filter','theses'):
            if not self.store.db.in_transaction:
                with self.store.db:
                    self.store.db.begin()
                    return self._claim(self._select_processing_feed(row))
            row = self._select_processing_feed(row)
        return self._claim(row)

    def _select_processing_feed(self, job: Row) -> Row | None:
        """Choose shared-publication rules in the same transaction as its claim."""
        db = self.store.db
        memberships.lock_graph(db)
        item = db.execute('SELECT processing_metadata FROM publications WHERE id=%s FOR UPDATE', (job['item'],)).fetchone()
        if item is None:
            return job
        owners = db.execute('SELECT feed_id FROM feed_publications WHERE publication_id=%s ORDER BY feed_id', (job['item'],)).fetchall()
        if len(owners) < 2:
            return job
        current = db.execute("SELECT * FROM jobs WHERE id=%s AND status='pending'", (job['id'],)).fetchone()
        if current is None:
            return None
        metadata = json.loads(item['processing_metadata'])
        selected = metadata.get('_pass', {}).get('feed_id')
        if selected not in {owner[0] for owner in owners}:
            chosen = processing_input.processing_feed(self.store, job['item'], job['kind'])
            content = processing_input.content(self.store,job['item'])
            has_input = any(block.get('text','').strip() for block in processing_input.blocks(content))
            if has_input and not processing_input.can_start(self.store,job['item'],chosen):
                rules = processing_input.live_settings(self.store,job['item'],chosen)
                config = self.store.processor_config()
                processor = rules['values']['processor']
                denied = not rules['public_ai'] and (processor=='codex' or
                    config is not None and processor in ('llamacpp','llamacpp:'+config.model) and not config.local_execution)
                code = 'ai-permission' if denied else 'processor-config'
                message = ('Запрещена публичная AI-обработка; выберите локальную модель' if denied
                           else 'Ожидание выбранного обработчика; настройте модель')
                previous=self.store.problems.saved_processing_candidates([current['id']]).get(current['id'],[]) if current['error'] is not None or current['problem_code'] is not None else []
                processor_previous=self.store.problems.prepare_processor_change([job['id']],self.store.wall_clock())
                source_changed=job_write(db, """UPDATE jobs SET status='pending',next=%s,error=%s,problem_code=%s,
                    payload=jsonb_set(payload::jsonb,'{preclaim_blocked}','true'::jsonb)::text
                    WHERE id=%s AND status='pending' RETURNING id,OLD.problem_code AS previous_code""", (0 if denied else self.store.clock()+30,message,code,job['id'])).fetchone()
                if source_changed and (source_changed['previous_code'] or '').startswith('source-'):
                    from oko.storage import source_groups
                    source_groups.sync_changed_rows(self.store,[source_changed],self.store.wall_clock())
                self.store.problems.finish_processor_change([job['id']],processor_previous,self.store.wall_clock())
                if previous:self.store.problems.sync_processing_problem(current['id'],previous,self.store.wall_clock())
                self.store.incomplete_draft.mark_materials([job['item']])
                return None
            metadata['_pass'] = dict(feed_id=chosen)
            db.execute('UPDATE publications SET processing_metadata=%s WHERE id=%s', (json.dumps(metadata,ensure_ascii=False),job['item']))
            self.store.processing.reconcile_accepted_item(job['item'])
            changed_sibling=None
            if job['kind']=='filter' and processing_input.stage_enabled(self.store,job['item'],'theses'):
                affected=[row['id'] for row in db.execute("SELECT id FROM jobs WHERE kind='theses' AND item=%s AND (processor_visible OR problem_code='processor-config')",(job['item'],))]
                sibling_processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
                changed_sibling=job_write(db, """INSERT INTO jobs(kind,item,status) VALUES ('theses',%s,'blocked')
                    ON CONFLICT(kind,item) DO UPDATE SET status='blocked'
                    WHERE jobs.status IN ('pending','cancelled') RETURNING id,error,problem_code,OLD.problem_code AS previous_code""", (job['item'],)).fetchone()
                if affected:self.store.problems.finish_processor_change(affected,sibling_processor_previous,self.store.wall_clock())
            elif job['kind']=='theses':
                affected=[row['id'] for row in db.execute("SELECT id FROM jobs WHERE kind='filter' AND item=%s AND (processor_visible OR problem_code='processor-config')",(job['item'],))]
                sibling_processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
                changed_sibling=job_write(db, """UPDATE jobs SET status='cancelled',generation=generation+1
                    WHERE kind='filter' AND item=%s AND status IN ('pending','blocked') RETURNING id,error,problem_code,OLD.problem_code AS previous_code""", (job['item'],)).fetchone()
                if affected:self.store.problems.finish_processor_change(affected,sibling_processor_previous,self.store.wall_clock())
            if changed_sibling and (changed_sibling['previous_code'] or '').startswith('source-'):
                from oko.storage import source_groups
                source_groups.sync_changed_rows(self.store,[changed_sibling],self.store.wall_clock())
            if changed_sibling and (changed_sibling['error'] is not None or changed_sibling['problem_code'] is not None):
                previous=self.store.problems.saved_processing_candidates([changed_sibling['id']]).get(changed_sibling['id'],[])
                if previous:self.store.problems.sync_processing_problem(changed_sibling['id'],previous,self.store.wall_clock())
            self.store.incomplete_draft.mark_materials([job['item']])
            current = db.execute("SELECT * FROM jobs WHERE id=%s AND status='pending'", (job['id'],)).fetchone()
            if current is None:
                raise JobRejected('Selected processing candidate is no longer eligible')
        return current

    def _media_candidate(self, now: float, feeds: list[str], dependencies: list[str], captions: bool) -> Row | None:
        # Avoid walking every publication and media file when no due download
        # exists anywhere. The scoped query below remains authoritative when it does.
        if not self.store.db.execute("SELECT 1 FROM jobs WHERE kind='download_media' AND status='pending' AND next<=%s AND NOT (coalesce(problem_code,'')='ai-permission' AND error IS NOT NULL) LIMIT 1", (now,)).fetchone():
            return None
        # LATERAL keeps the indexed search scoped to each selected publication.
        # Each media identity has one job of this kind. Keep parameterized
        # index probes instead of joining the entire foreign pending archive.
        caption = """EXISTS (SELECT 1 FROM publications own,
            LATERAL jsonb_array_elements(own.content::jsonb->'blocks') block
            WHERE own.id=m.publication_id AND own.platform='youtube'
                AND block->>'media_id'=m.id AND block->>'role'='subtitles')"""
        query = """WITH selected AS MATERIALIZED (
            SELECT i.id FROM feed_publications owner JOIN publications i ON i.id=owner.publication_id
            WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id=ANY(%s) AND i.viewed=0
            UNION SELECT article.id FROM feed_publications owner JOIN publications root ON root.id=owner.publication_id
            JOIN publication_links link ON link.source_publication_id=root.id JOIN publications article ON article.id=link.target_publication_id
            WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id=ANY(%s) AND root.viewed=0 AND article.viewed=0
                AND NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=article.id AND m.is_primary))
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
                    AND NOT (coalesce(j.problem_code,'')='ai-permission' AND j.error IS NOT NULL)
                    AND """ + (caption if captions else 'NOT '+caption) + """
                OFFSET 0
            ) candidate ORDER BY candidate.id LIMIT 1"""
        return self.store.db.execute(query, (feeds, dependencies, now)).fetchone()

    def _claim(self, row: Row | None) -> Job | None:
        if not row:
            return None
        with self.store.db:
            source_before: set[str]=set()
            sequence_value=None
            if row['kind'] in ('fetch','download_media','filter','theses','delete','feed-delete'):
                from oko.storage import memberships,source_groups
                memberships.lock_graph(self.store.db)
                current=self.store.db.execute("SELECT jobs.*,(SELECT value FROM state WHERE key='job-dispatch-generation') AS dispatch_sequence FROM jobs WHERE id=%s",(row['id'],)).fetchone()
                if current is None or current['status']!='pending' or current['generation']!=row['generation']:
                    return None
                row=current
                sequence_value=current['dispatch_sequence']
                if (row['problem_code'] or '').startswith('source-'):
                    source_before=source_groups.job_keys(self.store,row['id'])
            # Keep dispatch generations monotonic even when every old job was removed.
            previous = self.store.problems.deletion_candidates(row['id'],self.store.wall_clock()) if row['kind'] in ('delete','feed-delete') and (row['error'] is not None or row['problem_code'] is not None) else []
            from oko.storage.fetch_job_problems import previous_candidates as managed_candidates
            previous_fetch=managed_candidates(self.store,row['id'],self.store.wall_clock()) if row['kind']=='fetch' and (row['error'] is not None or row['problem_code'] is not None) else []
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous_media=media_candidates(self.store,row['id'],self.store.wall_clock()) if row['kind']=='download_media' and (row['error'] is not None or row['problem_code'] is not None) else []
            from oko.storage.processing_job_problems import managed_candidates as processing_candidates
            previous_processing=processing_candidates(self.store,row['id'],self.store.wall_clock()) if row['kind'] in ('filter','theses') and (row['error'] is not None or row['problem_code'] is not None) else []
            if row['kind'] not in ('fetch','download_media','filter','theses','delete','feed-delete'):
                sequence = self.store.db.execute("SELECT value FROM state WHERE key = 'job-dispatch-generation'").fetchone()
                sequence_value=sequence[0] if sequence else None
            generation = max(row['generation'], int(sequence_value) if sequence_value is not None else 0) + 1
            self.store.db.execute("INSERT INTO state VALUES ( 'job-dispatch-generation' , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value", (str(generation),))
            job_write(self.store.db, "UPDATE jobs SET status='running', attempt=attempt+1, generation=%s WHERE id=%s", (generation, row['id']))
            if source_before:
                complete=source_groups.sync(self.store,row['kind'],source_before | source_groups.job_keys(self.store,row['id']),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(row['id'],row['problem_code'])]))
                job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) WHERE id=%s",(complete,row['id']))
            if row['kind']=='filter':self.store.processing.filter_changed(row['item'])
            if row['kind']=='read':self.store.problems.sync_read_feed(row['item'],self.store.wall_clock())
            if row['kind']=='fetch' and previous_fetch:self.store.problems.sync_fetch(row['id'],previous_fetch,self.store.wall_clock())
            self.store.incomplete_draft.mark_job(row['kind'],row['item'],row['id'])
            if row['kind']=='download_media' and previous_media:self.store.problems.sync_media_problem(row['id'],previous_media,self.store.wall_clock())
            if row['kind'] in ('filter','theses') and previous_processing:self.store.problems.sync_processing_problem(row['id'],previous_processing,self.store.wall_clock())
            if row['kind']=='discover' and (row['error'] is not None or row['problem_code'] is not None):self.store.problems.sync_discover(row['id'],self.store.wall_clock())
            if row['kind'] in ('delete','feed-delete') and previous:
                self.store.problems.sync_deletion(row['id'],previous,self.store.wall_clock())
        current = expect_row(self.store.db.execute('SELECT * FROM jobs WHERE id = %s', (row['id'],)).fetchone())
        return Job(current['id'], current['kind'], current['item'], current['generation'])


    def retry(self, job: Job, now: float, error: str, delay: float = 1, *, code: str | None = None, terminal: bool = False, issue: AdapterIssue | None = None) -> None:
        if job.kind in ('discover','fetch','filter','theses'):
            with self.store.db:
                valid=self.valid(job)
                collection_jobs.fail(self.store,job,issue or AdapterIssue(code or 'collection-failed',error,
                    AdapterErrorKind.permanent if terminal else AdapterErrorKind.temporary))
                if valid and job.kind=='filter':self.store.processing.filter_changed(job.item)
            return
        with self.store.db:
            memberships.lock_graph(self.store.db)
            processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock(),source_lock=job.kind in ('download_media','delete','feed-delete') and (code or '').startswith('source-'))
            previous = self.store.problems.deletion_candidates(job.id,self.store.wall_clock()) if job.kind in ('delete','feed-delete') else []
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
            changed = job_write(self.store.db, "UPDATE jobs SET status = %s , next = %s , error = %s , problem_code = %s WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('download_media','delete','feed-delete') AND (problem_code LIKE 'source-%%' OR %s LIKE 'source-%%') THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING OLD.problem_code AS previous_code",
                ('failed' if terminal else 'pending',0 if code=='ai-permission' else now+delay,error,code or job.kind,job.id,job.generation,code or job.kind)).fetchone()
            if changed and job.kind in ('download_media','delete','feed-delete') and ((changed['previous_code'] or '').startswith('source-') or (code or '').startswith('source-')):
                from oko.storage import source_groups
                source_old_keys=source_groups.job_keys(self.store,job.id,changed['previous_code']) if (changed['previous_code'] or '').startswith('source-') else set()
                complete=source_groups.sync(self.store,job.kind,source_old_keys | source_groups.job_keys(self.store,job.id),self.store.wall_clock(),evidence=source_groups.job_evidence_many(self.store,[(job.id,changed['previous_code'])]))
                job_write(self.store.db,"UPDATE jobs SET source_event_owned=(%s=1) AND (problem_code LIKE 'source-%%') IS TRUE WHERE id=%s",(complete,job.id))
            if changed and job.kind in ('delete','feed-delete'):
                self.store.problems.sync_deletion(job.id,previous,self.store.wall_clock())
            if changed and job.kind=='read':
                self.store.problems.sync_read_feed(job.item,self.store.wall_clock())
            if changed and job.kind=='download_media':
                self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock())
            if changed:
                self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
                self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)


    def restore_telegram_wait(self, feed: str, deadline: float) -> None:
        with self.store.db:
            job_write(self.store.db, f"UPDATE jobs SET next = %s WHERE status = 'pending' AND problem_code LIKE '%%:FloodWaitError' AND ( item = %s OR item IN ( {owned_ids('= %s')} ) )", (deadline, feed, feed, feed))


    def interrupted(self, feeds: set[str] | None = None) -> None:
        selection = "status='running'"
        params: tuple[str, ...] = ()
        if feeds is not None:
            placeholders = ','.join('%s' for _ in feeds) or "''"
            owner_query = owned_ids(f'IN ({placeholders})')
            selection += f" AND (item IN ({placeholders}) OR item IN ({owner_query}) OR item IN (SELECT m.id FROM media_files m JOIN ({owner_query}) owner ON owner.id=m.publication_id))"
            params = (*feeds, *feeds, *feeds, *feeds, *feeds)
        with self.store.db:
            from oko.storage import memberships,source_groups
            memberships.lock_graph(self.store.db)
            # A configuration cancellation is not a failed discovery request.
            changed_discover=job_write(self.store.db, f"UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , generation = generation + 1 , next = CASE WHEN wait_scope IS NULL THEN 0 ELSE next END , retry_at = CASE WHEN wait_scope IS NULL THEN NULL ELSE retry_at END WHERE kind = 'discover' AND {selection} RETURNING id,error,problem_code",params).fetchall()
            for row in changed_discover:
                if row['error'] is not None or row['problem_code'] is not None:self.store.problems.sync_discover(row['id'],self.store.wall_clock())
            collection_jobs.interrupt(self.store,feeds,backoff=False)
            changed=job_write(self.store.db, f"UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status='pending', generation=generation+1 WHERE {selection} RETURNING id,kind,item,error,problem_code", params).fetchall()
            source_ids=[row['id'] for row in changed if row['kind'] in ('fetch','download_media','filter','theses','delete','feed-delete') and (row['problem_code'] or '').startswith('source-')]
            if source_ids:source_groups.sync_jobs(self.store,source_ids,self.store.wall_clock())
            for row in changed:
                self.store.incomplete_draft.mark_job(row['kind'],row['item'],row['id'])
                if row['kind']=='filter':self.store.processing.filter_changed(row['item'])
                if row['kind']=='read':self.store.problems.sync_read_feed(row['item'],self.store.wall_clock())
                if row['kind'] in ('delete','feed-delete') and (row['error'] is not None or row['problem_code'] is not None):
                    self.store.problems.sync_deletion(row['id'],[],self.store.wall_clock())


    def saved_execution(self, job: Job, signature: str) -> dict[str, Any] | None:
        row = self.store.db.execute("SELECT execution_settings->'parameters' FROM jobs WHERE id=%s AND execution_settings->>'generation'=%s::text AND execution_settings->>'signature'=%s",
                                    (job.id, job.generation, signature)).fetchone()
        return dict(row[0]) if row and row[0] is not None else None

    def save_execution(self, job: Job, signature: str, origin: dict[str, Any]) -> None:
        with self.store.db:
            changed = self.store.db.execute("""UPDATE jobs SET execution_settings=jsonb_build_object(
                'generation',%s::bigint,'signature',%s::text,'parameters',%s::jsonb)
                WHERE id=%s AND generation=%s AND status='running'""",
                (job.generation, signature, json.dumps(origin), job.id, job.generation)).rowcount
            if changed != 1:
                raise JobRejected('Attempt no longer owns this job')

    def forget_execution(self, kind: str, item: str) -> None:
        with self.store.db:
            self.store.db.execute('UPDATE jobs SET execution_settings=NULL WHERE kind=%s AND item=%s', (kind, item))

    def execution_provenance(self, job: Job) -> dict[str, Any] | None:
        row = self.store.db.execute("SELECT execution_settings->'parameters' FROM jobs WHERE id=%s AND execution_settings->>'generation'=%s::text", (job.id, job.generation)).fetchone()
        return dict(row[0]) if row and row[0] is not None else None


    def prepare_youtube(self, feed: str, want_captions: bool) -> None:
        if not want_captions:
            rows=self.store.db.execute("SELECT DISTINCT i . id , m . status FROM publications i JOIN feed_publications owner ON owner.publication_id=i.id AND owner.is_primary AND owner.is_unviewed JOIN media_files m ON m . publication_id = i . id WHERE owner.feed_id = %s AND i . viewed = 0 AND m . resource_key = 'youtube:subtitles'",(feed,)).fetchall()
            with self.store.db:
                from oko.storage import memberships,source_groups
                memberships.lock_graph(self.store.db)
                affected=[row['id'] for row in self.store.db.execute(f"SELECT j.id FROM jobs j JOIN media_files m ON j.kind='download_media' AND j.item=m.id JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id WHERE m.resource_key='youtube:subtitles' AND j.status IN ('pending','running') AND (j.processor_visible OR j.problem_code='processor-config')",(feed,feed))]
                previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
                changed_source=job_write(self.store.db, f"UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' ) AND item IN ( SELECT m.id FROM media_files m JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id WHERE m.resource_key = 'youtube:subtitles' ) RETURNING id,item,OLD.problem_code AS previous_code",(feed,feed)).fetchall()
                source_groups.sync_changed_rows(self.store,list(changed_source),self.store.wall_clock())
                self.store.incomplete_draft.mark_materials([row['id'] for row in rows])
                self.store.incomplete_draft.mark_jobs([('download_media',row['item']) for row in changed_source])
                for row in rows:
                    if row['status']=='pending_download':
                        self.enqueue_fetch(row['id'])
                if affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())
            return
        # This runs in the reader loop. Admission needs only the saved caption
        # state, not a reader material with filters, theses and provenance.
        rows = self.store.db.execute("SELECT items.id , items.platform , items.content FROM publications items JOIN feed_publications owner ON owner.publication_id=items.id AND owner.is_primary AND owner.is_unviewed WHERE owner.feed_id = %s AND items.viewed = 0 AND items.complete = 1 AND NOT EXISTS ( SELECT 1 FROM jobs WHERE kind = 'fetch' AND item = items . id AND status IN ( 'pending' , 'running' ) )", (feed,)).fetchall()
        for row in rows:
            try:
                header=json.loads(row['content']).get('extra',{}).get('_reader',{})
            except (OSError, ValueError):
                record_caught('store.prepare_youtube')
                continue
            if header.get('captions') == 'not_requested':
                self.retry_material(row['id'])


    @contextmanager
    def transaction(self, admitted: Callable[[], bool]) -> Iterator[None]:
        with transaction(self.store.db,admitted=admitted):
            yield

    def checkpoint(self, name: str) -> None:
        self.store.fault(name)

    def sync_reads(self) -> None:
        if self.store.processing.background:
            return
        for _ in self.sync_reads_steps():
            pass

    def sync_reads_steps(self) -> Iterator[None]:
        db = self.store.db
        revision = self.store.subscriptions.source_revision()
        if revision != self.store.read_sync._configuration:
            configs = self.store.subscriptions.feeds(transport='telegram')
        else:
            pending = [row[0] for row in db.execute('SELECT feed FROM telegram_read_state WHERE needs_sync OR saved_backfill_after IS NOT NULL')]
            if not pending:
                return
            configs = [config for feed in pending for config in self.store.subscriptions.feeds(feed, transport='telegram')]
        bindings = self.store.subscriptions.telegram.bindings([config['id'] for config in configs]) if configs else {}
        profiles = self.store.subscriptions.telegram.profiles() if any(binding['kind']=='client' for binding in bindings.values()) else {}
        cleared = []
        for config in configs:
            feed = config['id']
            binding = bindings.get(feed)
            if config['transport'] == 'telegram' and config['connection'] != 'deleting' and binding and binding['kind'] == 'client':
                # Claim this marker before yielding. New observations during the
                # pass can insert another marker and must not be erased at its end.
                with db:
                    db.execute('UPDATE telegram_read_state SET needs_sync=false WHERE feed = %s AND needs_sync', (feed,))
                while True:
                    if self.store.subscriptions.source_revision() != revision:
                        return
                    with db:
                        state = db.execute('SELECT saved_backfill_after FROM telegram_read_state WHERE feed=%s', (feed,)).fetchone()
                        if not state or state[0] is None:
                            break
                        batch = [row[0] for row in db.execute('''SELECT m.publication_id FROM feed_publications m
                            WHERE m.feed_id=%s AND m.publication_id>%s
                            ORDER BY m.publication_id LIMIT 8''', (feed,state[0]))]
                        for item in batch:
                            self.store.read_sync.saved_publication(item)
                        if batch:
                            db.execute('UPDATE telegram_read_state SET saved_backfill_after=%s WHERE feed=%s', (batch[-1],feed))
                        else:
                            db.execute('UPDATE telegram_read_state SET saved_backfill_after=NULL,needs_sync=true WHERE feed=%s', (feed,))
                            status_changed(db)
                        self.store.problems.sync_read_feed(feed,self.store.wall_clock())
                    yield
                    if not batch:
                        break
                if self.store.subscriptions.source_revision() != revision:
                    return
                with db:
                    profile = profiles.get(binding['role'])
                    request = self.store.read_sync._request(config, binding, profile)
                    if request is not None:
                        state = expect_row(db.execute('SELECT signature , waiting FROM telegram_read_state WHERE feed = %s', (feed,)).fetchone())
                        if request['eligible'] or request['read_history'] or state['waiting']:
                            signature = self.store.read_sync.signature(request)
                            if state['signature'] != signature:
                                db.execute('UPDATE telegram_read_state SET signature = %s WHERE feed = %s', (signature, feed))
                                changed=job_write(db, "INSERT INTO jobs ( kind , item ) VALUES ( 'read' , %s ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , next = 0 , error = NULL , generation = jobs . generation + 1 RETURNING id,problem_code", (feed,)).fetchone()
                                self.store.problems.sync_read_feed(feed,self.store.wall_clock())
                                if changed and changed['problem_code']=='processor-config':
                                    self.store.problems.finish_processor_change([changed['id']],None,self.store.wall_clock())
                yield
                if self.store.subscriptions.source_revision() != revision:
                    return
            else:
                cleared.append(feed)
        # Deleted and non-Telegram feeds need no read-frontier work.
        with db:
            if cleared:
                db.execute('UPDATE telegram_read_state SET needs_sync=false,saved_backfill_after=NULL WHERE feed = ANY(%s)', (cleared,))
            db.execute("UPDATE telegram_read_state SET needs_sync=false,saved_backfill_after=NULL WHERE (needs_sync OR saved_backfill_after IS NOT NULL) AND NOT EXISTS ( SELECT 1 FROM feeds WHERE id = telegram_read_state.feed AND transport = 'telegram' AND connection IS DISTINCT FROM 'deleting' )")
        self.store.read_sync._configuration = revision


    def finish_read(self, job: Job, waiting: str, now: float) -> None:
        if waiting:
            processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock())
            changed=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , next = %s , error = %s , problem_code = NULL WHERE id = %s AND generation = %s AND status = 'running'",(now+(.1 if waiting=='Продолжение проверки истории Telegram' else 30),waiting,job.id,job.generation)).rowcount
            if changed:
                self.store.problems.sync_read_feed(job.item,self.store.wall_clock())
                self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
        else:self.finish(job)

    def account_wait(self, user: int, until: float, job: Job | None = None) -> None:
        from oko.storage import memberships
        memberships.lock_graph(self.store.db)
        self.store.db.execute('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value',('telegram-retry:'+str(user),str(until)))
        if job:
            processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock())
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous_media=media_candidates(self.store,job.id,self.store.wall_clock()) if job.kind=='download_media' else []
            changed=job_write(self.store.db, "UPDATE jobs SET problem_code = %s WHERE id = %s AND generation = %s AND status = 'pending' AND CASE WHEN kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",(job.kind+':FloodWaitError',job.id,job.generation)).fetchone()
            if changed and (changed['previous_code'] or '').startswith('source-'):
                from oko.storage import source_groups
                source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
            if changed and job.kind=='read':self.store.problems.sync_read_feed(job.item,self.store.wall_clock())
            if changed and job.kind=='discover':self.store.problems.sync_discover(job.id,self.store.wall_clock())
            if changed and previous_media:self.store.problems.sync_media_problem(job.id,previous_media,self.store.wall_clock())
            if changed:self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
            if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def fail_media(self, job: Job, issue: AdapterIssue) -> None:
        collection_jobs.fail(self.store,job,issue,cancelled=issue.kind==AdapterErrorKind.media_changed)

    def defer_captions(self, job: Job, now: float, delay: float, reason: str) -> None:
        memberships.lock_graph(self.store.db)
        processor_previous=self.store.problems.prepare_processor_change([job.id],self.store.wall_clock())
        from oko.storage.media_job_problems import previous_candidates
        subtitle_previous=previous_candidates(self.store,job.id,self.store.wall_clock())
        changed=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , next = %s , error = %s , problem_code = NULL WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",(now+max(.05,delay),reason,job.id,job.generation)).fetchone()
        if changed and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
        if changed:self.store.problems.sync_media_problem(job.id,subtitle_previous,self.store.wall_clock())
        if changed:self.store.problems.finish_processor_change([job.id],processor_previous,self.store.wall_clock())
        if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def source_http_until(self, scope: str) -> float:
        return self.store.source_http_until(scope)

    def telegram_http_state(self, scope: str) -> dict[str,Any]:
        return self.store.telegram_http_state(scope)

    def source_http_wait(self, scope: str, delay: float | None) -> float:
        delay=max(self.store.subscriptions.retry_settings()['initial_seconds'],delay or 0)
        until=max(self.store.source_http_until(scope),self.store.wall_clock()+delay)
        self.store.source_http_wait(scope,until)
        return until

    def telegram_http_wait(self, scope: str, delay: float | None) -> float:
        state=self.store.telegram_http_state(scope)
        strikes=min(int(state.get('strikes',0))+1,7)
        delay=max(float(min(60*2**(strikes-1),3600)),delay or 0)
        until=max(float(state.get('until',0)),self.store.wall_clock()+delay)
        self.store.telegram_http_wait(scope,dict(until=until,strikes=strikes))
        return until

    def deleting(self) -> bool:
        return bool(self.store.db.execute("SELECT 1 FROM jobs WHERE kind IN ( 'delete' , 'feed-delete' ) AND status != 'done' LIMIT 1").fetchone())

    def workspace(self, job: Job) -> Path:
        return self.store.root/'staging'/job.item

    def collection_start(self, job: Job, feed: str) -> dict[str,Any]:
        return self.store.collection_status.start(job,feed)

    def collection_finish(self, job: Job, feed: str, saved: dict[str,Any], elapsed: float, observation: dict[str,Any]) -> None:
        self.store.collection_status.finish(job,feed,saved,elapsed,observation)

    def collection_interrupt(self, job: Job, feed: str, saved: dict[str,Any], elapsed: float) -> None:
        self.store.collection_status.interrupt(job,feed,saved,elapsed)

    def apply_collection(self, effects: CollectionEffects) -> None:
        memberships.lock_graph(self.store.db)
        db=self.store.db
        previous_deletions: dict[str, tuple[int,list[dict[str,Any]]]] = {}
        captured_fetch_items={row['item'] for row in db.execute("SELECT item FROM jobs WHERE kind='fetch' AND id=ANY(%s)",(list(effects.fetch_problems_before),))} if effects.fetch_problems_before else set()
        captured_source_fetch_items={row['item'] for row in db.execute("SELECT item FROM jobs WHERE kind='fetch' AND id=ANY(%s)",(list(effects.source_groups_before),))} if effects.source_groups_before else set()
        affected = sorted(set(effects.fetch) | set(effects.delete_problem_ids_before))
        processor_items=sorted(set(affected) | set(effects.refresh_links) | set(effects.media) | set(effects.revalidate))
        if processor_items:
            from oko.storage.transaction import begin_write
            begin_write(db)
            self.store.incomplete_draft.mark_materials(processor_items)
            for start in range(0,len(effects.revalidate),400):
                ids=sorted(effects.revalidate)[start:start+400]
                owners=[row['publication_id'] for row in db.execute('SELECT publication_id FROM media_files WHERE id=ANY(%s)',(ids,))]
                self.store.incomplete_draft.mark_materials(owners)
        processor_jobs=sorted(set(self.store.problems.processor_jobs_for_materials(processor_items)) | set(self.processor_cleanup_jobs()))
        processor_previous=self.store.problems.processor_history_before_batch(processor_jobs,self.store.wall_clock())
        for start in range(0,len(affected),400):
            for row in db.execute("SELECT id,item FROM jobs WHERE kind='delete' AND item=ANY(%s) AND (error IS NOT NULL OR problem_code IS NOT NULL)",(affected[start:start+400],)):
                identities = effects.delete_problem_ids_before.get(row['item'])
                candidates = ([{'id':identity} for identity in identities] if identities is not None else
                              self.store.problems.deletion_candidates(row['id'],self.store.wall_clock()))
                if candidates:
                    previous_deletions[row['item']] = (row['id'],candidates)
        for item,changed in effects.fetch.items():
            cancelled = job_write(db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , payload = (payload::jsonb - 'cleanup_feed')::text WHERE kind = 'delete' AND item = %s",(item,)).rowcount
            if cancelled and item in previous_deletions:
                deletion_job_id,previous = previous_deletions[item]
                self.store.problems.sync_deletion(deletion_job_id,previous,self.store.wall_clock())
            if changed:
                self.enqueue_fetch(item,sync_problem=item not in captured_fetch_items,sync_processor=False,sync_source=item not in captured_source_fetch_items)
            else:
                job_write(db, "INSERT INTO jobs ( kind , item , queued_wall ) VALUES ( 'fetch' , %s , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , next = 0 , error = NULL , generation = jobs . generation + 1 WHERE jobs . status = 'done' AND ( jobs . error IS NULL OR jobs . problem_code IS NULL )",(item,))
        for item in sorted(set(previous_deletions) - set(effects.fetch)):
            deletion_job_id,previous = previous_deletions[item]
            self.store.problems.sync_deletion(deletion_job_id,previous,self.store.wall_clock())
        for item in effects.refresh_links:
            job_write(db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , next = 0 , generation = generation + 1 WHERE kind = 'fetch' AND item = %s AND status = 'done' AND error IS NULL",(item,))
        for item in effects.media:self.schedule_media(item,skip_problem_jobs=set(effects.media_problems_before),skip_source_jobs=set(effects.source_groups_before),sync_processor=False)
        for identity,manual in effects.revalidate.items():self.revalidate_media(identity,manual=manual,sync_processor=False)
        self.reconcile_cleanup(sync_processor=False,skip_source_jobs=set(effects.source_groups_before))
        for job_id,previous in effects.fetch_problems_before.items():
            self.store.problems.sync_fetch(job_id,previous,self.store.wall_clock())
        if processor_jobs:self.store.problems.finish_processor_change(processor_jobs,processor_previous,self.store.wall_clock())
        for job_id,previous in effects.media_problems_before.items():
            self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        if effects.processing_problems_before:
            self.store.problems.sync_processing_problems(effects.processing_problems_before,self.store.wall_clock())
        if effects.source_groups_before:self.sync_captured_source_groups(effects)

    def processor_cleanup_jobs(self) -> list[int]:
        db=self.store.db
        return [row['id'] for row in db.execute("SELECT j.id FROM jobs j WHERE (j.processor_visible OR j.problem_code='processor-config') AND ((j.kind='download_media' AND j.status IN ('pending','running','failed','unavailable') AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=j.item)) OR (j.status!='cancelled' AND ((j.kind NOT IN ('delete','discover','read','feed-delete','download_media') AND EXISTS (SELECT 1 FROM publications p WHERE p.id=j.item AND p.viewed=1)) OR (j.kind='download_media' AND EXISTS (SELECT 1 FROM media_files m JOIN publications p ON p.id=m.publication_id WHERE m.id=j.item AND p.viewed=1)))) OR (j.kind='delete' AND j.status='cancelled' AND EXISTS (SELECT 1 FROM publications p WHERE p.id=j.item AND p.viewed=1)))")]
    def reconcile_cleanup(self, *, sync_processor: bool = True, skip_source_jobs: set[int] | None = None) -> None:
        db=self.store.db
        skipped_source=skip_source_jobs or set()
        from oko.storage import memberships,source_groups
        memberships.lock_graph(db)
        affected=self.processor_cleanup_jobs() if sync_processor else []
        processor_previous=self.store.problems.processor_history_before_batch(affected,self.store.wall_clock())
        orphaned=job_write(db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' , 'failed' , 'unavailable' ) AND item NOT IN ( SELECT id FROM media_files ) RETURNING id,item,OLD.problem_code AS previous_code").fetchall()
        ids=[row['id'] for row in orphaned]
        source_groups.sync_changed_rows(self.store,[row for row in orphaned if row['id'] not in skipped_source],self.store.wall_clock())
        for start in range(0,len(ids),400):
            for job_id,previous in self.store.problems.saved_media_candidates(ids[start:start+400]).items():
                self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        viewed_cancelled=job_write(db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL WHERE status != 'cancelled' AND ((kind NOT IN ('delete','discover','read','feed-delete','download_media') AND item IN (SELECT id FROM publications WHERE viewed=1)) OR (kind='download_media' AND item IN (SELECT m.id FROM media_files m JOIN publications p ON p.id=m.publication_id WHERE p.viewed=1))) RETURNING id,kind,item,OLD.problem_code AS previous_code").fetchall()
        source_groups.sync_changed_rows(self.store,[row for row in viewed_cancelled if row['id'] not in skipped_source],self.store.wall_clock())
        self.store.incomplete_draft.mark_jobs([('download_media',row['item']) for row in orphaned]+[(row['kind'],row['item']) for row in viewed_cancelled])
        viewed_media=[row['id'] for row in viewed_cancelled if row['kind']=='download_media']
        for start in range(0,len(viewed_media),400):
            for job_id,previous in self.store.problems.saved_media_candidates(viewed_media[start:start+400]).items():
                self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        stage_ids=[row['id'] for row in viewed_cancelled if row['kind'] in ('filter','theses')]
        for start in range(0,len(stage_ids),400):
            previous_processing=self.store.problems.saved_processing_candidates(stage_ids[start:start+400])
            if previous_processing:self.store.problems.sync_processing_problems(previous_processing,self.store.wall_clock())
        job_write(db, "INSERT INTO jobs ( kind , item ) SELECT 'delete' , id FROM publications WHERE viewed = 1 ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , generation = jobs . generation + 1 , next = 0 WHERE jobs . status = 'cancelled'")
        job_write(db, "INSERT INTO jobs ( kind , item ) SELECT 'feed-delete' , id FROM feeds WHERE connection = 'deleting' ON CONFLICT DO NOTHING")
        if affected:self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock())

    def sync_captured_source_groups(self, effects: CollectionEffects) -> None:
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
            if eligible:job_write(self.store.db,"UPDATE jobs SET source_event_owned=(status IN ('pending','running','failed','unavailable','blocked') OR (kind='fetch' AND status='done' AND error IS NOT NULL)) WHERE id=ANY(%s) AND problem_code LIKE 'source-%%'",(eligible,))
            eligible_set=set(eligible)
            ineligible=[job_id for job_id in current if job_id not in eligible_set]
            if ineligible:job_write(self.store.db,"UPDATE jobs SET source_event_owned=false WHERE id=ANY(%s) AND source_event_owned",(ineligible,))
            source_groups.notify_admission(self.store,eligible+ineligible)

    def sync_captured_deletions(self, effects: CollectionEffects) -> None:
        if effects.source_groups_before:
            self.sync_captured_source_groups(effects)
        if effects.processing_problems_before:
            self.store.problems.sync_processing_problems(effects.processing_problems_before,self.store.wall_clock())
        for job_id,previous in effects.media_problems_before.items():
            self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        for job_id,previous in effects.fetch_problems_before.items():
            self.store.problems.sync_fetch(job_id,previous,self.store.wall_clock())
        items = sorted(effects.delete_problem_ids_before)
        for start in range(0,len(items),400):
            for row in self.store.db.execute("SELECT id,item FROM jobs WHERE kind='delete' AND item=ANY(%s) AND (error IS NOT NULL OR problem_code IS NOT NULL)",(items[start:start+400],)):
                previous = [{'id':identity} for identity in effects.delete_problem_ids_before[row['item']]]
                if previous:
                    self.store.problems.sync_deletion(row['id'],previous,self.store.wall_clock())

    def change_adapter(self, feed: str) -> None:
        db=self.store.db
        from oko.storage import memberships
        memberships.lock_graph(db)
        effects=CollectionEffects()
        affected={row['item'] for row in db.execute(f"SELECT j.item FROM jobs j JOIN ({owned_ids('= %s')}) owner ON owner.id=j.item WHERE j.kind='fetch' AND ((j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%'))",(feed,feed))}
        publication_links.capture_fetch_problems(self.store,effects,affected)
        media_affected={row['publication_id'] for row in db.execute(f"SELECT DISTINCT m.publication_id FROM media_files m JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE (((m.resource_key='youtube:subtitles' OR j.media_null_owned) AND j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%'))",(feed,feed))}
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
        job_write(db, f"UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL , retry_at = NULL , retry_delay = 0 , wait_scope = NULL , next = 0 WHERE kind = 'download_media' AND item IN ( SELECT m.id FROM media_files m JOIN ({owned_ids('= %s')}) owner ON owner.id=m.publication_id )",(feed,feed))
        job_write(db, "INSERT INTO jobs ( kind , item , queued_wall ) SELECT 'fetch' , items.id , extract(epoch FROM clock_timestamp()) FROM publications items JOIN feed_publications owner ON owner.publication_id=items.id AND owner.is_primary AND owner.is_unviewed WHERE owner.feed_id = %s AND items.viewed = 0 ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , generation = jobs . generation + 1 , next = 0 , retry_at = NULL , retry_delay = 0 , wait_scope = NULL , error = NULL , problem_code = NULL",(feed,))
        job_write(db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL WHERE kind = 'read' AND item = %s",(feed,))
        self.store.problems.sync_read_feed(feed,self.store.wall_clock())
        self.enqueue_discovery(feed)
        for job_id,previous in effects.fetch_problems_before.items():
            self.store.problems.sync_fetch(job_id,previous,self.store.wall_clock())
        for job_id,previous in effects.media_problems_before.items():
            self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        if effects.source_groups_before:self.sync_captured_source_groups(effects)
        if processor_affected:self.store.problems.finish_processor_change(processor_affected,processor_previous,self.store.wall_clock())

    def remove_feed(self, feed: str, members: set[str]) -> None:
        db=self.store.db
        memberships.lock_graph(db)
        self.store.incomplete_draft.mark_materials(sorted(members | {feed}))
        ordered_members=sorted(members)
        for start in range(0,len(ordered_members),400):
            media_ids=[row['id'] for row in db.execute('SELECT id FROM media_files WHERE publication_id=ANY(%s)',(ordered_members[start:start+400],))]
            self.store.incomplete_draft.mark_jobs([('download_media',identity) for identity in media_ids])
        affected=self.store.problems.processor_jobs_for_materials([feed,*sorted(members)])
        processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        changed_processing: set[int]=set()
        source_rows: list[Any]=[]
        for item in members:
            changed_fetch=job_write(db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status='running' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = CASE WHEN status = 'running' THEN 'pending' ELSE status END , generation = generation + 1 WHERE item = %s AND kind = 'fetch' RETURNING id,OLD.problem_code AS previous_code",(item,)).fetchone()
            if changed_fetch and (changed_fetch['previous_code'] or '').startswith('source-'):source_rows.append(changed_fetch)
            stage_rows=job_write(db, """UPDATE jobs SET status = CASE WHEN status = 'running' THEN 'pending' ELSE status END , generation = generation + 1
                WHERE item = %s AND kind IN ('filter','theses') AND NOT EXISTS (SELECT 1 FROM feed_publications WHERE publication_id=%s) RETURNING id,error,problem_code,OLD.problem_code AS previous_code""",(item,item)).fetchall()
            changed_processing.update(row['id'] for row in stage_rows if row['error'] is not None or row['problem_code'] is not None)
            source_rows.extend(row for row in stage_rows if (row['previous_code'] or '').startswith('source-'))
            changed_media=job_write(db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , generation = generation + 1 WHERE kind = 'download_media' AND status = 'running' AND item IN ( SELECT id FROM media_files WHERE publication_id = %s ) RETURNING id,error,problem_code,OLD.problem_code AS previous_code",(item,)).fetchall()
            source_rows.extend(row for row in changed_media if (row['previous_code'] or '').startswith('source-'))
            changed_ids=[row['id'] for row in changed_media if row['error'] is not None or row['problem_code'] is not None]
            for job_id,previous in self.store.problems.saved_media_candidates(changed_ids).items():
                self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        discover=db.execute("SELECT id FROM jobs WHERE kind='discover' AND item=%s AND (error IS NOT NULL OR problem_code IS NOT NULL)",(feed,)).fetchone()
        final_rows=job_write(db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE item = %s AND kind NOT IN ( 'delete' , 'feed-delete' ) RETURNING id,kind,error,problem_code,OLD.problem_code AS previous_code",(feed,)).fetchall()
        changed_processing.update(row['id'] for row in final_rows if row['kind'] in ('filter','theses') and (row['error'] is not None or row['problem_code'] is not None))
        source_rows.extend(row for row in final_rows if (row['previous_code'] or '').startswith('source-'))
        previous_processing: dict[int,list[dict[str,Any]]]={}
        changed_ids=sorted(changed_processing)
        for start in range(0,len(changed_ids),400):previous_processing.update(self.store.problems.saved_processing_candidates(changed_ids[start:start+400]))
        if previous_processing:self.store.problems.sync_processing_problems(previous_processing,self.store.wall_clock())
        self.store.problems.sync_read_feed(feed,self.store.wall_clock())
        if discover:self.store.problems.sync_discover(discover['id'],self.store.wall_clock())
        self.reconcile_cleanup()
        if source_rows:
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,source_rows,self.store.wall_clock())
        if affected:self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock(),removed_outcome='irrelevant')
        # Retain cleanup ownership independently of reading membership. A
        # publication can lose its last membership before its files are removed.
        # The owner is recorded when the last membership is removed.

    def schedule_media(self, item: str, *, skip_problem_jobs: set[int] | None = None, skip_source_jobs: set[int] | None = None, sync_processor: bool = True) -> None:
        memberships.lock_graph(self.store.db)
        db = self.store.db
        skipped_source=skip_source_jobs or set()
        processor_affected=self.store.problems.processor_jobs_for_materials([item]) if sync_processor else []
        processor_previous=self.store.problems.prepare_processor_change(processor_affected,self.store.wall_clock()) if sync_processor else None
        cancelled=job_write(db, "UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' ) AND item IN ( SELECT id FROM media_files WHERE publication_id = %s AND status = 'unavailable' ) RETURNING id,item,error,problem_code,OLD.problem_code AS previous_code",(item,)).fetchall()
        from oko.storage import source_groups
        source_rows=list(cancelled)
        excluded=skip_problem_jobs or set()
        changed_ids=[row['id'] for row in cancelled if row['id'] not in excluded and (row['error'] is not None or row['problem_code'] is not None)]
        saved=self.store.problems.saved_media_candidates(changed_ids)
        for job_id,previous in saved.items():self.store.problems.sync_media_problem(job_id,previous,self.store.wall_clock())
        media_targets=[]
        for row in db.execute("SELECT m.id,j.id AS job_id,j.error,j.problem_code FROM media_files m LEFT JOIN jobs j ON j.kind='download_media' AND j.item=m.id WHERE m.publication_id=%s AND m.status='pending_download'", (item,)).fetchall():
            media_targets.append(row['id'])
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous=media_candidates(self.store,row['job_id'],self.store.wall_clock()) if row['job_id'] is not None and row['job_id'] not in excluded and (row['error'] is not None or row['problem_code'] is not None) else []
            now,wall=self.store.clock(),self.store.wall_clock()
            changed_source=job_write(db, "INSERT INTO jobs ( kind , item , queued_wall ) VALUES ( 'download_media' , %s , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , generation = jobs . generation + 1 , error = NULL , problem_code = NULL , next = %s + greatest ( jobs . retry_delay , coalesce ( jobs . retry_at , 0 ) - %s , 0 ) , retry_at = greatest ( coalesce ( jobs . retry_at , 0 ) , %s + jobs . retry_delay ) , wait_scope = CASE WHEN jobs . retry_at > %s THEN jobs . wait_scope ELSE NULL END WHERE jobs . status IN ( 'cancelled' , 'done' ) RETURNING id,OLD.problem_code AS previous_code",(row['id'],now,wall,wall,wall)).fetchone()
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
        rows = store.db.execute("SELECT j . * , m . resource_key , m . status AS media_status FROM jobs j JOIN media_files m ON m . id = j . item WHERE m . publication_id = %s AND j . kind = 'download_media' AND ( j . status IN ( 'failed' , 'unavailable' , 'pending' ) OR j . status = 'done' AND m . status = 'unavailable' )", (item,)).fetchall()
        for row in rows:
            if row['media_status']=='ready' and not revalidate_ready or row['resource_key']=='youtube:subtitles' and not captions_allowed:
                continue
            waiting = bool(row['wait_scope'] and row['retry_at'] and row['retry_at']>store.wall_clock())
            from oko.storage.media_job_problems import previous_candidates as media_candidates
            previous_media=media_candidates(store,row['id'],store.wall_clock()) if row['error'] is not None or row['problem_code'] is not None else []
            changed_source=job_write(store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , generation = generation + 1 , next = %s , retry_at = %s , error = %s , problem_code = %s WHERE id = %s AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code", (row['next'] if waiting else 0, row['retry_at'] if waiting else None,
                row['error'] if waiting else None,row['problem_code'] if waiting else None,row['id'])).fetchone()
            if changed_source:source_rows.append(changed_source)
            if not waiting:
                store.db.execute("UPDATE media_files SET status = 'pending_download' , reason = '' WHERE id = %s AND status != 'ready'", (row['item'],))
            if previous_media:store.problems.sync_media_problem(row['id'],previous_media,store.wall_clock())

        youtube_row=store.db.execute("SELECT content FROM publications WHERE id = %s AND platform = 'youtube'",(item,)).fetchone()
        if youtube_row and captions_allowed:
            content=json.loads(youtube_row['content'])
            subtitles=youtube_media.subtitle_id(content)
            if subtitles and content['extra']['youtube'].get('captions')!='not_requested':
                resource=store.db.execute('SELECT status FROM media_files WHERE id = %s',(subtitles,)).fetchone()
                if resource and resource['status']=='unavailable':
                    store.db.execute("UPDATE media_files SET status = 'pending_download' , reason = '' WHERE id = %s",(subtitles,))
                    content['extra']['youtube']['captions']='pending'
                    store.db.execute('UPDATE publications SET content = %s WHERE id = %s',(json.dumps(content,ensure_ascii=False),item))
                    from oko.storage.incomplete_index import save_class
                    save_class(store.db,item,content)
                    changed_source=job_write(store.db, "INSERT INTO jobs ( kind , item , queued_wall ) VALUES ( 'download_media' , %s , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , generation = jobs . generation + 1 , next = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . next ELSE 0 END , retry_at = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . retry_at ELSE NULL END WHERE jobs . status != 'running' AND CASE WHEN jobs.problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",(subtitles,store.wall_clock(),store.wall_clock())).fetchone()
                    if changed_source:source_rows.append(changed_source)
                    store.processing.reconcile_accepted_item(item)

        if revalidate_ready:
            for resource in store.db.execute("SELECT id FROM media_files WHERE publication_id = %s AND status = 'ready'",(item,)).fetchall():
                self.revalidate_media(resource['id'])
        if source_rows:source_groups.sync_changed_rows(store,source_rows,store.wall_clock())
        if affected:store.problems.finish_processor_change(affected,previous,store.wall_clock())
        if store.db.in_transaction:
            store.incomplete_draft.mark_materials([item])
            store.incomplete_draft.mark_jobs([('download_media',row['item']) for row in rows])


    def revalidate_media(self, identity: str, *, manual: bool = True, sync_processor: bool = True) -> None:
        store=self.store
        row=store.db.execute(f"SELECT m.resource_key, i.platform, {owner_expr('i')} AS feed_id FROM media_files m JOIN publications i ON i.id=m.publication_id WHERE m.id = %s AND m.status = 'ready' AND i.viewed = 0",(identity,)).fetchone()
        if row is None or row['resource_key']=='youtube:subtitles' and not store.subscriptions.youtube_theses(row['feed_id']):
            return
        memberships.lock_graph(store.db)
        affected=store.problems.processor_jobs_for_materials([identity]) if sync_processor else []
        previous=store.problems.prepare_processor_change(affected,store.wall_clock()) if sync_processor else None
        changed_source=job_write(store.db, "INSERT INTO jobs ( kind , item , manual_once , queued_wall ) VALUES ( 'download_media' , %s , 1 , extract(epoch FROM clock_timestamp()) ) ON CONFLICT ( kind , item ) DO UPDATE SET queued_wall = CASE WHEN jobs.kind IN ('discover','fetch','download_media') AND jobs.status != 'pending' THEN excluded.queued_wall ELSE jobs.queued_wall END , status = 'pending' , manual_once = 1 , generation = jobs . generation + 1 , next = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . next ELSE 0 END , retry_at = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . retry_at ELSE NULL END WHERE (jobs . status IN ( 'done' , 'cancelled' ) OR ( %s = 1 AND jobs . status IN ( 'failed' , 'unavailable' ) )) AND CASE WHEN jobs.problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",
            (identity,store.wall_clock(),store.wall_clock(),manual)).fetchone()
        if changed_source and (changed_source['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(store,[changed_source],store.wall_clock())
        if affected:store.problems.finish_processor_change(affected,previous,store.wall_clock())
        if store.db.in_transaction:store.incomplete_draft.mark_job('download_media',identity)


    def finish_feed_deletion(self, job: Job) -> None:
        feed = job.item
        if self.store.db.execute("SELECT 1 FROM jobs WHERE kind='delete' AND status!='done' AND payload::jsonb->>'cleanup_feed' = %s", (feed,)).fetchone():
            self.retry(job, 0, 'Ожидание очистки файлов', .1)
            return
        self.store.fault('before_feed_claim_release')
        with self.store.db:
            memberships.lock_graph(self.store.db)
            self.store.incomplete_draft.mark_admission_feed(feed)
            feed_exists = self.store.db.execute('SELECT 1 FROM feeds WHERE id=%s', (feed,)).fetchone() is not None
            affects_codex = feed_exists and self.store.problems.codex_choice(self.store.wall_clock(), feed=feed) is not None
            previous_problem = self.store.problems.deletion_candidates(job.id,self.store.wall_clock())
            owned = "SELECT item FROM jobs WHERE kind='delete' AND payload::jsonb->>'cleanup_feed' = %s"
            source_effects=CollectionEffects()
            source_items={row[0] for row in self.store.db.execute(owned,(feed,))}
            source_items.add(feed)
            publication_links.capture_source_groups(self.store,source_effects,source_items)
            source_ids=[row['id'] for row in self.store.db.execute("SELECT id FROM jobs WHERE feed_id=%s AND collection_attempt IS NOT NULL AND problem_code LIKE 'source-%%'",(feed,))]
            from oko.storage import source_groups
            for start in range(0,len(source_ids),400):
                group=source_ids[start:start+400]
                source_effects.source_groups_before.update(source_groups.job_keys_many(self.store,group))
                evidence=source_groups.job_evidence_many(self.store,[(job_id,None) for job_id in group])
                for key,objects in evidence.items():source_effects.source_evidence_before.setdefault(key,set()).update(objects)
            self.store.incomplete_draft.mark_materials([row['item'] for row in self.store.db.execute(owned,(feed,))])
            affected=[row['id'] for row in self.store.db.execute(f"SELECT id FROM jobs WHERE (processor_visible OR problem_code='processor-config') AND (item=%s OR (kind='delete' AND payload::jsonb->>'cleanup_feed'=%s) OR (kind!='delete' AND item IN ({owned})))",(feed,feed,feed))]
            processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
            job_write(self.store.db, f"DELETE FROM jobs WHERE kind != 'delete' AND item IN ({owned})", (feed,))
            if self.store.db.execute(f'DELETE FROM publications WHERE id IN ({owned})', (feed,)).rowcount:
                status_changed(self.store.db)
            job_write(self.store.db, "DELETE FROM jobs WHERE item = %s OR (kind='delete' AND payload::jsonb->>'cleanup_feed' = %s)", (feed, feed))
            self.store.problems.sync_deletion(job.id,previous_problem,self.store.wall_clock())
            if affected:self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock(),removed_outcome='irrelevant')
            for table in ('history_members', 'initial_history', 'telegram_read_members', 'telegram_read_state'):
                deleted = self.store.db.execute(f'DELETE FROM {table} WHERE feed = %s', (feed,)).rowcount
                if table=='initial_history' and deleted:
                    source_changed(self.store.db)
                if table in ('history_members','telegram_read_members','telegram_read_state') and deleted:
                    status_changed(self.store.db)
            self.store.db.execute('UPDATE jobs SET feed_id=NULL, collection_attempt=NULL WHERE feed_id=%s AND collection_attempt IS NOT NULL', (feed,))
            self.store.subscriptions.clear_settings('feed', feed)
            self.store.db.execute('DELETE FROM state WHERE key = %s', ('feed-status:'+feed,))
            self.store.subscriptions.telegram.release(feed)
            if self.store.db.execute('DELETE FROM feeds WHERE id = %s', (feed,)).rowcount:
                source_changed(self.store.db)
                if affects_codex:
                    self.store.problems.sync_codex_choice(self.store.wall_clock())
            self.store.problems.sync_read_feed(feed,self.store.wall_clock())
            if source_effects.source_groups_before:self.sync_captured_source_groups(source_effects)


    def delete(self, job: Job) -> None:
        self.store.fault('before_delete')
        for path in (self.store.root / 'content' / job.item, self.store.root / 'staging' / job.item, self.store.root / 'media' / job.item):
            if any(path==active or path in active.parents for active in self.store.media.active_paths):
                self.retry(job,self.store.clock(),'Ожидание завершения сохранения файла',.1)
                return
            if path.exists():
                shutil.rmtree(path)
        self.store.fault('after_delete_files')
        with self.store.db:
            memberships.lock_graph(self.store.db)
            source_effects=CollectionEffects()
            publication_links.capture_source_groups(self.store,source_effects,{job.item})
            affected=self.store.problems.processor_jobs_for_materials([job.item])
            previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
            self.store.db.execute('UPDATE jobs SET execution_settings=NULL WHERE item=%s AND execution_settings IS NOT NULL', (job.item,))
            self.finish(job)
            if self.store.db.execute('DELETE FROM publications WHERE id = %s AND viewed = 1 AND platform IS NOT NULL ',(job.item,)).rowcount:
                status_changed(self.store.db)
                if affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())
            if source_effects.source_groups_before:self.sync_captured_source_groups(source_effects)


    def retry_linked(self, item: str) -> None:
        for row in self.store.db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s',(item,)).fetchall():
            self.enqueue_fetch(row[0])
            self.store.db.execute("UPDATE jobs SET manual_once = 1 WHERE kind = 'fetch' AND item = %s",(row[0],))
            self.retry_media(row[0],revalidate_ready=False)

    def cancel_history(self, feed: str) -> None:
        from oko.storage import memberships,source_groups
        memberships.lock_graph(self.store.db)
        processor_affected=[row['id'] for row in self.store.db.execute("SELECT j.id FROM history_members h JOIN jobs j ON j.item=h.item AND j.kind='fetch' WHERE h.feed=%s AND (j.processor_visible OR j.problem_code='processor-config')",(feed,))]
        processor_previous=self.store.problems.prepare_processor_change(processor_affected,self.store.wall_clock())
        effects=CollectionEffects()
        affected={row['item'] for row in self.store.db.execute("SELECT j.item FROM history_members h JOIN jobs j ON j.item=h.item AND j.kind='fetch' WHERE h.feed=%s AND ((j.problem_code IS NULL AND j.error IS NOT NULL) OR (j.problem_code IS NOT NULL AND j.problem_code NOT LIKE 'source-%%'))",(feed,))}
        publication_links.capture_fetch_problems(self.store,effects,affected)
        source_rows=job_write(self.store.db, "UPDATE jobs SET status = 'done' , generation = generation + 1 WHERE kind = 'fetch' AND item IN ( SELECT item FROM history_members WHERE feed = %s ) RETURNING id,item,OLD.problem_code AS previous_code",(feed,)).fetchall()
        source_groups.sync_changed_rows(self.store,list(source_rows),self.store.wall_clock())
        if source_rows:self.store.incomplete_draft.mark_materials([row['item'] for row in source_rows])
        for job_id,previous in effects.fetch_problems_before.items():
            self.store.problems.sync_fetch(job_id,previous,self.store.wall_clock())
        if processor_affected:self.store.problems.finish_processor_change(processor_affected,processor_previous,self.store.wall_clock())

    def backfill_processing_inputs(self) -> None:
        for _ in self.backfill_processing_steps():
            pass

    def backfill_processing_steps(self) -> Iterator[None]:
        for kind in ('filter', 'theses'):
            # The partial index excludes unrelated completed work.
            identities = [row[0] for row in self.store.db.execute("SELECT id FROM jobs WHERE kind=%s AND input_snapshot IS NOT NULL AND (payload::jsonb->>'input_revision') IS NULL", (kind,))]
            for identity in identities:
                with self.store.db:
                    row = self.store.db.execute("SELECT id,item,input_snapshot->>'signature' AS signature FROM jobs WHERE id=%s AND kind=%s AND input_snapshot IS NOT NULL AND (payload::jsonb->>'input_revision') IS NULL", (identity,kind)).fetchone()
                    if row is not None and row['signature']==processing_input.signature(self.store,row['item'],kind):
                        revision = processing_input.revision(self.store,row['item'])
                        self.store.db.execute("UPDATE jobs SET payload=jsonb_set(payload::jsonb,'{input_revision}',to_jsonb(%s::text))::text WHERE id=%s", (revision,row['id']))
                yield

    def replace_media_input(self, job: Job) -> None:
        memberships.lock_graph(self.store.db)
        from oko.storage.media_job_problems import previous_candidates as media_candidates
        previous=media_candidates(self.store,job.id,self.store.wall_clock())
        changed=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status != 'pending' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = 'pending' , generation = generation + 1 , next = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN next ELSE 0 END , error = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN error ELSE NULL END WHERE id = %s AND generation = %s AND status = 'running' AND CASE WHEN problem_code LIKE 'source-%%' THEN pg_advisory_xact_lock(hashtextextended(current_schema(),5713747719990890568))::text IS NOT NULL ELSE TRUE END RETURNING id,OLD.problem_code AS previous_code",(self.store.wall_clock(),self.store.wall_clock(),job.id,job.generation)).fetchone()
        if changed and (changed['previous_code'] or '').startswith('source-'):
            from oko.storage import source_groups
            source_groups.sync_changed_rows(self.store,[changed],self.store.wall_clock())
        if changed and previous:self.store.problems.sync_media_problem(job.id,previous,self.store.wall_clock())
        if changed:self.store.incomplete_draft.mark_job(job.kind,job.item,job.id)

    def invalidate_processing(self) -> None:
        from oko.storage import memberships,source_groups
        memberships.lock_graph(self.store.db)
        affected=[row['id'] for row in self.store.db.execute("SELECT j.id FROM jobs j WHERE (j.processor_visible OR j.problem_code='processor-config') AND j.kind IN ('filter','theses') AND j.payload::jsonb->>'input_revision' IS DISTINCT FROM (SELECT i.processing_metadata::jsonb->>'_revision' FROM publications i WHERE i.id=j.item)")]
        processor_previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
        ids=[row['id'] for row in self.store.db.execute("""SELECT id FROM jobs WHERE kind IN ('filter','theses') AND (((problem_code='' OR (problem_code IS NULL AND error IS NOT NULL))) OR (problem_code IS NOT NULL AND problem_code<>'' AND problem_code NOT LIKE 'source-%'
            AND problem_code NOT IN ('processor-config','ai-permission','codex-choice','youtube-admission','identity-unconfirmed','profile_not_configured','profile_unavailable','account_identity_mismatch')))
            AND payload::jsonb->>'input_revision' IS DISTINCT FROM (SELECT i.processing_metadata::jsonb->>'_revision' FROM publications i WHERE i.id=jobs.item)""")]
        stage_previous: dict[int,list[dict[str,Any]]]={}
        for start in range(0,len(ids),400):stage_previous.update(self.store.problems.saved_processing_candidates(ids[start:start+400]))
        changed=job_write(self.store.db, "UPDATE jobs SET queued_wall = CASE WHEN kind IN ('discover','fetch','download_media') AND status='running' THEN extract(epoch FROM clock_timestamp()) ELSE queued_wall END , status = CASE WHEN status = 'running' THEN 'pending' ELSE status END , generation = generation + 1 , error = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN error ELSE NULL END , problem_code = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN problem_code ELSE NULL END , next = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END WHERE kind IN ( 'filter' , 'theses' ) AND payload::jsonb->>'input_revision' IS DISTINCT FROM ( SELECT i.processing_metadata::jsonb->>'_revision' FROM publications i WHERE i . id = jobs . item ) RETURNING id,kind,item,OLD.problem_code AS previous_code",
            (self.store.wall_clock(),)*4).fetchall()
        source_groups.sync_changed_rows(self.store,list(changed),self.store.wall_clock())
        for row in changed:
            if row['kind']=='filter':self.store.processing.filter_changed(row['item'])
        if changed:self.store.incomplete_draft.mark_materials([row['item'] for row in changed])
        if affected:self.store.problems.finish_processor_change(affected,processor_previous,self.store.wall_clock())
        if stage_previous:self.store.problems.sync_processing_problems(stage_previous,self.store.wall_clock())

    @contextmanager
    def cancellation(self) -> Iterator[None]:
        # Revocation is valid even after new-work admission has closed.
        if self.store.db.transaction_depth:
            yield
        else:
            with transaction(self.store.db):yield
