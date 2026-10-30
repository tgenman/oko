"""Attempt identity, conditional transitions and saved execution parameters."""
from oko.storage.database import expect_row, Database, Row
import json
import shutil
from oko.domain.collection import CollectionEffects
from pathlib import Path
from typing import Any
from collections.abc import Callable,Iterator
from contextlib import contextmanager
from oko.storage.transaction import transaction
from oko.storage.source_configuration import changed as source_changed
from oko.storage.reader_status import changed as status_changed
from typing import TYPE_CHECKING
from oko.storage import collection_jobs, material_read, processing_input
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

    def valid(self, job: Job) -> bool:
        row=self.store.db.execute('SELECT status , generation , kind , item FROM jobs WHERE id = %s',(job.id,)).fetchone()
        return bool(row and row['status']=='running' and row['generation']==job.generation and row['kind']==job.kind and row['item']==job.item)

    def finish(self, job: Job) -> None:
        changed=self.store.db.execute("UPDATE jobs SET status = 'done' , error = NULL , problem_code = NULL , manual_once = 0 , retry_at = NULL , retry_delay = 0 , wait_scope = NULL WHERE id = %s AND generation = %s AND status = 'running' AND kind = %s AND item = %s",
            (job.id,job.generation,job.kind,job.item)).rowcount
        if changed!=1:
            raise JobRejected('Attempt no longer owns this job')
        if job.kind=='filter':
            self.store.processing.filter_changed(job.item)

    def reconcile(self, kind: Kind, candidates: list[tuple[str, str | None, bool, str]]) -> None:
        db=self.store.db
        for item,signature,satisfied,revision in candidates:
            db.execute('DELETE FROM processing_stage_dirty WHERE kind = %s AND item = %s',(kind,item))
            job=db.execute("SELECT status,payload,problem_code,input_snapshot->>'signature' AS signature FROM jobs WHERE kind=%s AND item=%s",(kind,item)).fetchone()
            previous=job['signature'] if job else None
            enabled = processing_input.stage_enabled(self.store, item, kind) if signature is not None else False
            if previous is not None and previous==signature and job and json.loads(job['payload']).get('input_revision')==revision:
                # Reconsider blocked work only on an input/settings event. Running
                # attempts are allowed to finish when the switch is turned off.
                if job['status'] == 'blocked' and enabled:
                    if json.loads(job['payload']).get('preclaim_blocked') and not processing_input.can_start(
                            self.store,item,processing_input.processing_feed(self.store,item,kind)):
                        continue
                    db.execute("""UPDATE jobs SET status='pending',
                        error=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE error END,
                        problem_code=CASE WHEN payload::jsonb->>'preclaim_blocked'='true' THEN NULL ELSE problem_code END,
                        payload=(payload::jsonb-'preclaim_blocked')::text
                        WHERE kind=%s AND item=%s AND status='blocked'""", (kind, item))
                    if kind=='filter':self.store.processing.filter_changed(item)
                    self.store.publications.status(item)
                elif job['status'] == 'pending' and not enabled:
                    db.execute("UPDATE jobs SET status='blocked' WHERE kind=%s AND item=%s AND status='pending'", (kind, item))
                    if kind=='filter':self.store.processing.filter_changed(item)
                    self.store.publications.status(item)
                continue
            if signature is None and previous is None and (job is None or job[0]=='cancelled'):
                continue
            if signature is None:
                if kind=='theses' and job and job['status']=='blocked' and processing_input.stage_enabled(self.store,item,'theses'):
                    filtering = db.execute("SELECT status FROM jobs WHERE kind='filter' AND item=%s", (item,)).fetchone()
                    if filtering and filtering['status'] in ('pending','running'):
                        continue
                db.execute("UPDATE jobs SET status='cancelled', generation=generation+1, error=NULL, problem_code=NULL, input_snapshot=NULL WHERE kind=%s AND item=%s",(kind,item))
                if kind=='filter' and job:self.store.processing.filter_changed(item)
                if kind == 'filter' and previous is not None:
                    self._processing_input_changed([item])
            else:
                db.execute("INSERT INTO jobs (kind,item,status,payload,input_snapshot) VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (kind,item) DO UPDATE SET status=excluded.status,payload=excluded.payload,input_snapshot=excluded.input_snapshot,generation=jobs.generation+1,next=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.next ELSE 0 END,error=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.error ELSE NULL END,problem_code=CASE WHEN excluded.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at>%s THEN jobs.problem_code ELSE NULL END",
                    (kind,item,'done' if satisfied else 'pending' if enabled else 'blocked',json.dumps(dict(input_revision=revision)),json.dumps(dict(signature=signature)),self.store.wall_clock(),self.store.wall_clock(),self.store.wall_clock()))
                if kind=='filter' and job:self.store.processing.filter_changed(item)
                if kind == 'filter' and previous != signature:
                    self._processing_input_changed([item])

    def _processing_input_changed(self, identities: list[str]) -> None:
        if not identities:
            return
        now = self.store.wall_clock()
        for kind in ('filter', 'theses', 'card'):
            self.store.db.execute("INSERT INTO reader_entities SELECT %s,unnest(%s::text[]),1,%s ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed", (kind, identities, now))

    def forget_input(self, kind: Kind, identities: list[str]) -> None:
        ordered = sorted(set(identities))
        for start in range(0, len(ordered), 400):
            rows = self.store.db.execute("UPDATE jobs SET input_snapshot=NULL WHERE kind=%s AND item=ANY(%s) AND input_snapshot IS NOT NULL RETURNING item", (kind, ordered[start:start+400])).fetchall()
            if kind == 'filter':
                self._processing_input_changed([row[0] for row in rows])

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

    def retry_processing(self, item: str, kind: Kind) -> bool:
        db=self.store.db
        if db.execute("SELECT 1 FROM jobs WHERE kind=%s AND item=%s AND input_snapshot IS NOT NULL",(kind,item)).fetchone() is None:
            return False
        self.store.jobs.forget_execution(kind,item)
        db.execute("UPDATE jobs SET status = 'pending' , next = CASE WHEN wait_scope IS NOT NULL THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL THEN retry_at ELSE NULL END , error = CASE WHEN wait_scope IS NOT NULL THEN error ELSE NULL END , generation = generation + 1 WHERE kind = %s AND item = %s",(kind,item))
        if kind=='filter':self.store.processing.filter_changed(item)
        return True

    def origin(self, job: Job, signature: str) -> dict[str, Any] | None:
        return self.store.jobs.saved_execution(job,signature)

    def save_origin(self, job: Job, signature: str, value: dict[str, Any]) -> None:
        self.store.jobs.save_execution(job,signature,value)

    def fail(self, job: Job, error: str, *, code: str) -> None:
        self.store.jobs.retry(job,self.store.clock(),error,30,code=code)
        if not processing_input.stage_enabled(self.store, job.item, job.kind):
            self.store.db.execute("UPDATE jobs SET status='blocked' WHERE id=%s AND generation=%s AND status='pending'", (job.id, job.generation))
            self.store.publications.status(job.item)

    def cancel(self, job: Job) -> None:
        changed=self.store.db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE id = %s AND generation = %s AND status = 'running'",(job.id,job.generation)).rowcount
        if changed and job.kind=='filter':self.store.processing.filter_changed(job.item)

    def block(self, job: Job) -> None:
        changed=self.store.db.execute("UPDATE jobs SET status = 'blocked' WHERE id = %s AND generation = %s AND status = 'running'", (job.id, job.generation)).rowcount
        if changed and job.kind=='filter':self.store.processing.filter_changed(job.item)

    def manual(self, job: Job) -> bool:
        row=self.store.db.execute('SELECT manual_once FROM jobs WHERE id = %s AND generation = %s',(job.id,job.generation)).fetchone()
        return bool(row and row[0])

    def retry_at(self, job: Job) -> float | None:
        row=self.store.db.execute('SELECT retry_at FROM jobs WHERE id = %s AND generation = %s',(job.id,job.generation)).fetchone()
        return row[0] if row else None


    def enqueue_discovery(self, feed: str = 'demo', next_at: float = 0) -> None:
        with self.store.db:
            self.store.db.execute("INSERT INTO jobs ( kind , item , next ) VALUES ( 'discover' , %s , %s ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , next = excluded . next , error = NULL WHERE jobs . status = 'done'", ('' if feed == 'demo' else feed, next_at))


    def refresh_feed(self, feed: str) -> None:
        """Admit one manual refresh without restarting work already in progress."""
        with self.store.db:
            for row in self.store.db.execute('SELECT DISTINCT i . id FROM unviewed_items_by_feed i JOIN media_files m ON m . publication_id = i . id WHERE i . feed_id = %s AND i . viewed = 0', (feed,)).fetchall():
                self.retry_media(row['id'],revalidate_ready=False)
        discovery = '' if feed == 'demo' else feed
        active = self.store.db.execute("SELECT 1 FROM jobs WHERE kind = 'discover' AND item = %s AND ( status = 'running' OR ( status = 'pending' AND error IS NULL AND next <= %s ) )", (discovery,self.store.clock())).fetchone()
        with self.store.db:
            history = self.store.subscriptions.history.status(feed)
            if history and not history.get('cancelled'):
                self.store.subscriptions.history.pause(feed, False)
            if not active:
                self.enqueue_discovery(feed)
                self.store.db.execute("UPDATE jobs SET status = 'pending' , next = CASE WHEN problem_code LIKE '%%FloodWaitError' OR problem_code = 'youtube-admission' OR ( wait_scope IS NOT NULL AND retry_at > %s ) THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END , generation = generation + 1 WHERE kind = 'discover' AND item = %s AND status != 'running'", (self.store.wall_clock(),self.store.wall_clock(),discovery))
            for row in self.store.db.execute("SELECT i . id , i . members , i . source_deleted , j . error , j . status FROM unviewed_items_by_feed i JOIN jobs j ON j . item = i . id AND j . kind = 'fetch' WHERE i . feed_id = %s AND i . viewed = 0 AND j . status != 'running'", (feed,)).fetchall():
                material = self.store.material(row['id'])
                unavailable = row['source_deleted']
                manifest=SourceManifest.model_validate_json(json.dumps(self.store.original(row['id'])[0]['source_manifest'])) if material and material.get('complete') and self.store.original(row['id'])[0].get('source_manifest') else None
                if material and not row['error'] and not unavailable and (material.get('full',False) or expected_incomplete(manifest)) and not self.store._needs_collection(row['id']):
                    continue
                if row['members'] != '[]' and material and material.get('full',False):
                    continue
                if row['error'] or unavailable or not material or not material.get('full',False):
                    self.store.db.execute("UPDATE jobs SET manual_once = CASE WHEN ( status = 'done' AND error IS NOT NULL ) OR EXISTS ( SELECT 1 FROM items i WHERE i.id=jobs.item AND i.source_deleted ) THEN 1 ELSE manual_once END , status = 'pending' , next = CASE WHEN problem_code LIKE '%%FloodWaitError' OR problem_code = 'youtube-admission' OR ( wait_scope IS NOT NULL AND retry_at > %s ) THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END , generation = generation + 1 WHERE kind = 'fetch' AND item = %s", (self.store.wall_clock(),self.store.wall_clock(),row['id']))
            for kind in ('filter','theses'):
                for row in self.store.db.execute('SELECT j . item FROM jobs j JOIN items_by_feed i ON i . id = j . item WHERE i . feed_id = %s AND j . kind = %s AND j . error IS NOT NULL',(feed,kind)).fetchall():
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


    def enqueue_fetch(self, item: str) -> None:
        self.store.db.execute("INSERT INTO jobs ( kind , item ) VALUES ( 'fetch' , %s ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , manual_once = 0 , next = CASE WHEN jobs . problem_code LIKE '%%FloodWaitError' OR ( jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s ) THEN jobs . next ELSE 0 END , error = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . error ELSE NULL END , retry_at = CASE WHEN jobs . problem_code LIKE '%%FloodWaitError' OR ( jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s ) THEN jobs . retry_at ELSE NULL END , generation = jobs . generation + 1", (item,self.store.wall_clock(),self.store.wall_clock(),self.store.wall_clock()))


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
            query += """ AND kind IN ('filter','theses') AND NOT EXISTS (SELECT 1 FROM items_read i WHERE i.id=jobs.item AND i.platform IS NOT NULL
                AND NOT ((jobs.kind='filter' AND i.status IN ('pending_filtering','filter_out'))
                      OR (jobs.kind='theses' AND (i.status IN ('pending_summary','ready')
                          OR (i.status='pending_filtering' AND i.processing_metadata::jsonb->'_pass'->>'feed_id' IS NULL)))))"""
            if self.store.processing.background:
                query += """ AND NOT EXISTS (SELECT 1 FROM processing_dirty p WHERE p.item=jobs.item)
                    AND NOT EXISTS (SELECT 1 FROM processing_stage_dirty d WHERE d.item=jobs.item AND d.kind=jobs.kind)"""
        elif deleting_only:
            query += " AND kind IN ('delete','feed-delete')"
        elif media_only:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('%s' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('%s' for _ in dependencies) or "''"
            # Follow only dependencies of the requested roots.
            query += f""" AND kind='download_media' AND item IN (
                SELECT m.id FROM unviewed_items_by_feed i JOIN media_files m ON m.publication_id=i.id
                    WHERE i.viewed=0 AND i.feed_id IN ({placeholders})
                UNION SELECT m.id FROM unviewed_items_by_feed root JOIN publication_links l ON l.source_publication_id=root.id
                    JOIN items_read i ON i.id=l.target_publication_id JOIN media_files m ON m.publication_id=i.id
                    WHERE root.viewed=0 AND root.feed_id IN ({dependency_keys}) AND i.viewed=0 AND i.feed_id IS NULL)"""
        else:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('%s' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('%s' for _ in dependencies) or "''"
            query += f""" AND id IN (
                SELECT id FROM jobs WHERE status='pending' AND kind IN ('delete','feed-delete')
                UNION SELECT id FROM jobs WHERE status='pending' AND kind IN ('discover','read') AND item IN ({placeholders})
                UNION SELECT id FROM jobs WHERE status='pending' AND kind='fetch' AND item IN (
                    SELECT own.id FROM feed_publications owner JOIN items own ON own.id=owner.publication_id
                        WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id IN ({placeholders}) AND own.viewed=0
                    UNION SELECT i.id FROM feed_publications owner JOIN items root ON root.id=owner.publication_id
                        JOIN publication_links l ON l.source_publication_id=root.id JOIN items i ON i.id=l.target_publication_id
                        WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id IN ({dependency_keys}) AND root.viewed=0 AND i.viewed=0
                        AND NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id AND m.is_primary)))"""
        if media_only:
            subtitles="SELECT 1 FROM media_files m JOIN items_read i ON i . id = m . publication_id JOIN oko_json_each ( i . content , '$.blocks' ) b ON oko_json_extract ( b . value , '$.media_id' ) = m . id WHERE m . id = jobs . item AND i . platform = 'youtube' AND oko_json_extract ( b . value , '$.role' ) = 'subtitles'"
            query += ' AND '+('EXISTS' if captions_only else 'NOT EXISTS')+' ('+subtitles+')'
        if not include_deletions:
            query += " AND kind NOT IN ('delete','feed-delete')"
        blocked_history = "SELECT m . item FROM history_members m JOIN initial_history h ON h . feed = m . feed WHERE ( h . paused = 1 OR ( h . scan_complete = 0 AND ( oko_json_extract ( h . selection , '$.mode' ) = 'count' OR h . feed IN ( SELECT id FROM feeds WHERE transport = 'rss' ) ) ) )"
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
        item = db.execute('SELECT processing_metadata FROM items WHERE id=%s FOR UPDATE', (job['item'],)).fetchone()
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
                db.execute("""UPDATE jobs SET status='pending',next=%s,error=%s,problem_code=%s,
                    payload=jsonb_set(payload::jsonb,'{preclaim_blocked}','true'::jsonb)::text
                    WHERE id=%s AND status='pending'""", (0 if denied else self.store.clock()+30,message,code,job['id']))
                return None
            metadata['_pass'] = dict(feed_id=chosen)
            db.execute('UPDATE items SET processing_metadata=%s WHERE id=%s', (json.dumps(metadata,ensure_ascii=False),job['item']))
            self.store.processing.reconcile_accepted_item(job['item'])
            if job['kind']=='filter' and processing_input.stage_enabled(self.store,job['item'],'theses'):
                db.execute("""INSERT INTO jobs(kind,item,status) VALUES ('theses',%s,'blocked')
                    ON CONFLICT(kind,item) DO UPDATE SET status='blocked'
                    WHERE jobs.status IN ('pending','cancelled')""", (job['item'],))
            elif job['kind']=='theses':
                db.execute("""UPDATE jobs SET status='cancelled',generation=generation+1
                    WHERE kind='filter' AND item=%s AND status IN ('pending','blocked')""", (job['item'],))
                db.execute('DELETE FROM processing_dirty WHERE item=%s', (job['item'],))
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
        caption = """EXISTS (SELECT 1 FROM items own,
            LATERAL jsonb_array_elements(own.content::jsonb->'blocks') block
            WHERE own.id=m.publication_id AND own.platform='youtube'
                AND block->>'media_id'=m.id AND block->>'role'='subtitles')"""
        query = """WITH selected AS MATERIALIZED (
            SELECT i.id FROM feed_publications owner JOIN items i ON i.id=owner.publication_id
            WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id=ANY(%s) AND i.viewed=0
            UNION SELECT article.id FROM feed_publications owner JOIN items root ON root.id=owner.publication_id
            JOIN publication_links link ON link.source_publication_id=root.id JOIN items article ON article.id=link.target_publication_id
            WHERE owner.is_primary AND owner.is_unviewed AND owner.feed_id=ANY(%s) AND root.viewed=0 AND article.viewed=0
                AND NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=article.id AND m.is_primary))
            SELECT candidate.* FROM selected
            CROSS JOIN LATERAL (
                SELECT id,publication_id FROM media_files
                WHERE publication_id=selected.id OFFSET 0
            ) m
            CROSS JOIN LATERAL (
                SELECT j.* FROM jobs j
                WHERE j.item=m.id AND j.kind='download_media'
                    AND j.status='pending' AND j.next<=%s
                    AND NOT (coalesce(j.problem_code,'')='ai-permission' AND j.error IS NOT NULL)
                    AND """ + (caption if captions else 'NOT '+caption) + """
                ORDER BY j.id LIMIT 1
            ) candidate ORDER BY candidate.id LIMIT 1"""
        return self.store.db.execute(query, (feeds, dependencies, now)).fetchone()

    def _claim(self, row: Row | None) -> Job | None:
        if not row:
            return None
        with self.store.db:
            # Keep dispatch generations monotonic even when every old job was removed.
            sequence = self.store.db.execute("SELECT value FROM state WHERE key = 'job-dispatch-generation'").fetchone()
            generation = max(row['generation'], int(sequence[0]) if sequence else 0) + 1
            self.store.db.execute("INSERT INTO state VALUES ( 'job-dispatch-generation' , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value", (str(generation),))
            self.store.db.execute("UPDATE jobs SET status='running', attempt=attempt+1, generation=%s WHERE id=%s", (generation, row['id']))
            if row['kind']=='filter':self.store.processing.filter_changed(row['item'])
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
            self.store.db.execute("UPDATE jobs SET status = %s , next = %s , error = %s , problem_code = %s WHERE id = %s AND generation = %s AND status = 'running'",
                ('failed' if terminal else 'pending',0 if code=='ai-permission' else now+delay,error,code or job.kind,job.id,job.generation))


    def restore_telegram_wait(self, feed: str, deadline: float) -> None:
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET next = %s WHERE status = 'pending' AND problem_code LIKE '%%:FloodWaitError' AND ( item = %s OR item IN ( SELECT id FROM items_by_feed WHERE feed_id = %s ) )", (deadline, feed, feed))


    def interrupted(self, feeds: set[str] | None = None) -> None:
        selection = "status='running'"
        params: tuple[str, ...] = ()
        if feeds is not None:
            placeholders = ','.join('%s' for _ in feeds) or "''"
            selection += f" AND (item IN ({placeholders}) OR item IN (SELECT id FROM items_by_feed WHERE feed_id IN ({placeholders})) OR item IN (SELECT m.id FROM media_files m JOIN items_by_feed i ON i.id=m.publication_id WHERE i.feed_id IN ({placeholders})))"
            params = (*feeds, *feeds, *feeds)
        with self.store.db:
            # A configuration cancellation is not a failed discovery request.
            self.store.db.execute(f"UPDATE jobs SET status = 'pending' , generation = generation + 1 , next = CASE WHEN wait_scope IS NULL THEN 0 ELSE next END , retry_at = CASE WHEN wait_scope IS NULL THEN NULL ELSE retry_at END WHERE kind = 'discover' AND {selection}",params)
            collection_jobs.interrupt(self.store,feeds,backoff=False)
            changed=self.store.db.execute(f"UPDATE jobs SET status='pending', generation=generation+1 WHERE {selection} RETURNING kind,item", params).fetchall()
            for row in changed:
                if row['kind']=='filter':self.store.processing.filter_changed(row['item'])


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
            rows=self.store.db.execute("SELECT DISTINCT i . id , m . status FROM unviewed_items_by_feed i JOIN media_files m ON m . publication_id = i . id WHERE i . feed_id = %s AND i . viewed = 0 AND m . resource_key = 'youtube:subtitles'",(feed,)).fetchall()
            with self.store.db:
                self.store.db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' ) AND item IN ( SELECT m . id FROM media_files m JOIN items_by_feed i ON i . id = m . publication_id WHERE i . feed_id = %s AND m . resource_key = 'youtube:subtitles' )",(feed,))
                for row in rows:
                    if row['status']=='pending_download':
                        self.enqueue_fetch(row['id'])
            return
        # This runs in the reader loop. Admission needs only the saved caption
        # state, not a reader material with filters, theses and provenance.
        rows = self.store.db.execute("SELECT id , platform , content FROM unviewed_items_by_feed items WHERE feed_id = %s AND viewed = 0 AND complete = 1 AND NOT EXISTS ( SELECT 1 FROM jobs WHERE kind = 'fetch' AND item = items . id AND status IN ( 'pending' , 'running' ) )", (feed,)).fetchall()
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
                            db.execute(self.store.read_versions._bump('status', "'runtime'"))
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
                                db.execute("INSERT INTO jobs ( kind , item ) VALUES ( 'read' , %s ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , next = 0 , error = NULL , generation = jobs . generation + 1", (feed,))
                yield
                if self.store.subscriptions.source_revision() != revision:
                    return
            else:
                cleared.append(feed)
        # Deleted and non-Telegram feeds need no read-frontier work.
        with db:
            if cleared:
                db.execute('UPDATE telegram_read_state SET needs_sync=false,saved_backfill_after=NULL WHERE feed IN ( SELECT value FROM oko_json_each ( %s ) )', (json.dumps(cleared),))
            db.execute("UPDATE telegram_read_state SET needs_sync=false,saved_backfill_after=NULL WHERE (needs_sync OR saved_backfill_after IS NOT NULL) AND NOT EXISTS ( SELECT 1 FROM feeds WHERE id = telegram_read_state.feed AND transport = 'telegram' AND connection IS DISTINCT FROM 'deleting' )")
        self.store.read_sync._configuration = revision


    def finish_read(self, job: Job, waiting: str, now: float) -> None:
        if waiting:
            self.store.db.execute("UPDATE jobs SET status = 'pending' , next = %s , error = %s , problem_code = NULL WHERE id = %s AND generation = %s AND status = 'running'",(now+(.1 if waiting=='Продолжение проверки истории Telegram' else 30),waiting,job.id,job.generation))
        else:self.finish(job)

    def account_wait(self, user: int, until: float, job: Job | None = None) -> None:
        self.store.db.execute('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value',('telegram-retry:'+str(user),str(until)))
        if job:self.store.db.execute("UPDATE jobs SET problem_code = %s WHERE id = %s AND generation = %s AND status = 'pending'",(job.kind+':FloodWaitError',job.id,job.generation))

    def fail_media(self, job: Job, issue: AdapterIssue) -> None:
        collection_jobs.fail(self.store,job,issue,cancelled=issue.kind==AdapterErrorKind.media_changed)

    def defer_captions(self, job: Job, now: float, delay: float, reason: str) -> None:
        self.store.db.execute("UPDATE jobs SET status = 'pending' , next = %s , error = %s , problem_code = NULL WHERE id = %s AND generation = %s AND status = 'running'",(now+max(.05,delay),reason,job.id,job.generation))

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
        db=self.store.db
        for item,changed in effects.fetch.items():
            db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , payload = (payload::jsonb - 'cleanup_feed')::text WHERE kind = 'delete' AND item = %s",(item,))
            if changed:
                self.enqueue_fetch(item)
            else:
                db.execute("INSERT INTO jobs ( kind , item ) VALUES ( 'fetch' , %s ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , next = 0 , error = NULL , generation = jobs . generation + 1 WHERE jobs . status = 'done' AND ( jobs . error IS NULL OR jobs . problem_code IS NULL )",(item,))
        for item in effects.refresh_links:
            db.execute("UPDATE jobs SET status = 'pending' , next = 0 , generation = generation + 1 WHERE kind = 'fetch' AND item = %s AND status = 'done' AND error IS NULL",(item,))
        for item in effects.media:self.schedule_media(item)
        for identity,manual in effects.revalidate.items():self.revalidate_media(identity,manual=manual)
        self.reconcile_cleanup()

    def reconcile_cleanup(self) -> None:
        db=self.store.db
        db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' , 'failed' , 'unavailable' ) AND item NOT IN ( SELECT id FROM media_files )")
        db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL WHERE kind != 'delete' AND status != 'cancelled' AND item IN ( SELECT id FROM items_read WHERE viewed = 1 )")
        db.execute("INSERT INTO jobs ( kind , item ) SELECT 'delete' , id FROM items_read WHERE viewed = 1 ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , generation = jobs . generation + 1 , next = 0 WHERE jobs . status = 'cancelled'")
        db.execute("INSERT INTO jobs ( kind , item ) SELECT 'feed-delete' , id FROM feeds WHERE connection = 'deleting' ON CONFLICT DO NOTHING")

    def change_adapter(self, feed: str) -> None:
        db=self.store.db
        db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL , problem_code = NULL , retry_at = NULL , retry_delay = 0 , wait_scope = NULL , next = 0 WHERE kind = 'download_media' AND item IN ( SELECT m . id FROM media_files m JOIN items_by_feed i ON i . id = m . publication_id WHERE i . feed_id = %s )",(feed,))
        db.execute("INSERT INTO jobs ( kind , item ) SELECT 'fetch' , id FROM unviewed_items_by_feed WHERE feed_id = %s AND viewed = 0 ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , generation = jobs . generation + 1 , next = 0 , retry_at = NULL , retry_delay = 0 , wait_scope = NULL , error = NULL , problem_code = NULL",(feed,))
        db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 , error = NULL WHERE kind = 'read' AND item = %s",(feed,))
        self.enqueue_discovery(feed)

    def remove_feed(self, feed: str, members: set[str]) -> None:
        db=self.store.db
        for item in members:
            db.execute("UPDATE jobs SET status = CASE WHEN status = 'running' THEN 'pending' ELSE status END , generation = generation + 1 WHERE item = %s AND kind = 'fetch'",(item,))
            db.execute("""UPDATE jobs SET status = CASE WHEN status = 'running' THEN 'pending' ELSE status END , generation = generation + 1
                WHERE item = %s AND kind IN ('filter','theses') AND NOT EXISTS (SELECT 1 FROM feed_publications WHERE publication_id=%s)""",(item,item))
            db.execute("UPDATE jobs SET status = 'pending' , generation = generation + 1 WHERE kind = 'download_media' AND status = 'running' AND item IN ( SELECT id FROM media_files WHERE publication_id = %s )",(item,))
        db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE item = %s AND kind NOT IN ( 'delete' , 'feed-delete' )",(feed,))
        self.reconcile_cleanup()
        # Retain cleanup ownership independently of reading membership. A
        # publication can lose its last membership before its files are removed.
        # The owner is recorded when the last membership is removed.

    def schedule_media(self, item: str) -> None:
        db = self.store.db
        db.execute("UPDATE jobs SET status = 'cancelled' , generation = generation + 1 WHERE kind = 'download_media' AND status IN ( 'pending' , 'running' ) AND item IN ( SELECT id FROM media_files WHERE publication_id = %s AND status = 'unavailable' )",(item,))
        for row in db.execute("SELECT id FROM media_files WHERE publication_id = %s AND status = 'pending_download'", (item,)).fetchall():
            now,wall=self.store.clock(),self.store.wall_clock()
            db.execute("INSERT INTO jobs ( kind , item ) VALUES ( 'download_media' , %s ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , generation = jobs . generation + 1 , error = NULL , problem_code = NULL , next = %s + oko_max ( jobs . retry_delay , coalesce ( jobs . retry_at , 0 ) - %s , 0 ) , retry_at = oko_max ( coalesce ( jobs . retry_at , 0 ) , %s + jobs . retry_delay ) , wait_scope = CASE WHEN jobs . retry_at > %s THEN jobs . wait_scope ELSE NULL END WHERE jobs . status IN ( 'cancelled' , 'done' )",(row['id'],now,wall,wall,wall))


    def retry_media(self, item: str, *, revalidate_ready: bool = True) -> None:
        """Manual retry preserves source deadlines and the accumulated backoff."""
        store = self.store
        owner=store.db.execute('SELECT platform , feed_id FROM items_read WHERE id = %s',(item,)).fetchone()
        captions_allowed=not owner or owner['platform']!='youtube' or store.subscriptions.youtube_theses(owner['feed_id'])
        rows = store.db.execute("SELECT j . * , m . resource_key , m . status AS media_status FROM jobs j JOIN media_files m ON m . id = j . item WHERE m . publication_id = %s AND j . kind = 'download_media' AND ( j . status IN ( 'failed' , 'unavailable' , 'pending' ) OR j . status = 'done' AND m . status = 'unavailable' )", (item,)).fetchall()
        for row in rows:
            if row['media_status']=='ready' and not revalidate_ready or row['resource_key']=='youtube:subtitles' and not captions_allowed:
                continue
            waiting = bool(row['wait_scope'] and row['retry_at'] and row['retry_at']>store.wall_clock())
            store.db.execute("UPDATE jobs SET status = 'pending' , generation = generation + 1 , next = %s , retry_at = %s , error = %s , problem_code = %s WHERE id = %s", (row['next'] if waiting else 0, row['retry_at'] if waiting else None,
                row['error'] if waiting else None,row['problem_code'] if waiting else None,row['id']))
            if not waiting:
                store.db.execute("UPDATE media_files SET status = 'pending_download' , reason = '' WHERE id = %s AND status != 'ready'", (row['item'],))

        youtube_row=store.db.execute("SELECT content FROM items_read WHERE id = %s AND platform = 'youtube'",(item,)).fetchone()
        if youtube_row and captions_allowed:
            content=json.loads(youtube_row['content'])
            subtitles=youtube_media.subtitle_id(content)
            if subtitles and content['extra']['youtube'].get('captions')!='not_requested':
                resource=store.db.execute('SELECT status FROM media_files WHERE id = %s',(subtitles,)).fetchone()
                if resource and resource['status']=='unavailable':
                    store.db.execute("UPDATE media_files SET status = 'pending_download' , reason = '' WHERE id = %s",(subtitles,))
                    content['extra']['youtube']['captions']='pending'
                    store.db.execute('UPDATE items SET content = %s WHERE id = %s',(json.dumps(content,ensure_ascii=False),item))
                    store.db.execute('INSERT INTO processing_dirty VALUES ( %s ) ON CONFLICT DO NOTHING',(item,))
                    store.db.execute("INSERT INTO jobs ( kind , item ) VALUES ( 'download_media' , %s ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , generation = jobs . generation + 1 , next = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . next ELSE 0 END , retry_at = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . retry_at ELSE NULL END WHERE jobs . status != 'running'",(subtitles,store.wall_clock(),store.wall_clock()))

        if revalidate_ready:
            for resource in store.db.execute("SELECT id FROM media_files WHERE publication_id = %s AND status = 'ready'",(item,)).fetchall():
                self.revalidate_media(resource['id'])


    def revalidate_media(self, identity: str, *, manual: bool = True) -> None:
        store=self.store
        row=store.db.execute("SELECT m . resource_key , i . platform , i . feed_id FROM media_files m JOIN items_read i ON i . id = m . publication_id WHERE m . id = %s AND m . status = 'ready' AND i . viewed = 0",(identity,)).fetchone()
        if row is None or row['resource_key']=='youtube:subtitles' and not store.subscriptions.youtube_theses(row['feed_id']):
            return
        store.db.execute("INSERT INTO jobs ( kind , item , manual_once ) VALUES ( 'download_media' , %s , 1 ) ON CONFLICT ( kind , item ) DO UPDATE SET status = 'pending' , manual_once = 1 , generation = jobs . generation + 1 , next = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . next ELSE 0 END , retry_at = CASE WHEN jobs . wait_scope IS NOT NULL AND jobs . retry_at > %s THEN jobs . retry_at ELSE NULL END WHERE jobs . status IN ( 'done' , 'cancelled' ) OR ( %s = 1 AND jobs . status IN ( 'failed' , 'unavailable' ) )",
            (identity,store.wall_clock(),store.wall_clock(),manual))


    def finish_feed_deletion(self, job: Job) -> None:
        feed = job.item
        if self.store.db.execute("SELECT 1 FROM jobs WHERE kind='delete' AND status!='done' AND payload::jsonb->>'cleanup_feed' = %s", (feed,)).fetchone():
            self.retry(job, 0, 'Ожидание очистки файлов', .1)
            return
        self.store.fault('before_feed_claim_release')
        with self.store.db:
            owned = "SELECT item FROM jobs WHERE kind='delete' AND payload::jsonb->>'cleanup_feed' = %s"
            self.store.db.execute(f"DELETE FROM jobs WHERE kind != 'delete' AND item IN ({owned})", (feed,))
            self.store.db.execute(f'DELETE FROM items WHERE id IN ({owned})', (feed,))
            self.store.db.execute("DELETE FROM jobs WHERE item = %s OR (kind='delete' AND payload::jsonb->>'cleanup_feed' = %s)", (feed, feed))
            for table in ('history_members', 'initial_history', 'telegram_read_members', 'telegram_read_state'):
                deleted = self.store.db.execute(f'DELETE FROM {table} WHERE feed = %s', (feed,)).rowcount
                if table=='initial_history' and deleted:
                    source_changed(self.store.db)
                if table=='history_members' and deleted:
                    status_changed(self.store.db)
            self.store.db.execute('UPDATE jobs SET feed_id=NULL, collection_attempt=NULL WHERE feed_id=%s AND collection_attempt IS NOT NULL', (feed,))
            self.store.subscriptions.clear_settings('feed', feed)
            self.store.db.execute('DELETE FROM state WHERE key = %s', ('feed-status:'+feed,))
            self.store.subscriptions.telegram.release(feed)
            if self.store.db.execute('DELETE FROM feeds WHERE id = %s', (feed,)).rowcount:
                source_changed(self.store.db)


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
            self.store.db.execute('UPDATE jobs SET execution_settings=NULL WHERE item=%s AND execution_settings IS NOT NULL', (job.item,))
            self.finish(job)
            self.store.db.execute('DELETE FROM items WHERE id = %s AND viewed = 1 AND platform IS NOT NULL ',(job.item,))


    def retry_linked(self, item: str) -> None:
        for row in self.store.db.execute('SELECT target_publication_id FROM publication_links WHERE source_publication_id = %s',(item,)).fetchall():
            self.enqueue_fetch(row[0])
            self.store.db.execute("UPDATE jobs SET manual_once = 1 WHERE kind = 'fetch' AND item = %s",(row[0],))
            self.retry_media(row[0],revalidate_ready=False)

    def cancel_history(self, feed: str) -> None:
        self.store.db.execute("UPDATE jobs SET status = 'done' , generation = generation + 1 WHERE kind = 'fetch' AND item IN ( SELECT item FROM history_members WHERE feed = %s )",(feed,))

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
        self.store.db.execute("UPDATE jobs SET status = 'pending' , generation = generation + 1 , next = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN next ELSE 0 END , error = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN error ELSE NULL END WHERE id = %s AND generation = %s AND status = 'running'",(self.store.wall_clock(),self.store.wall_clock(),job.id,job.generation))

    def invalidate_processing(self) -> None:
        changed=self.store.db.execute("UPDATE jobs SET status = CASE WHEN status = 'running' THEN 'pending' ELSE status END , generation = generation + 1 , error = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN error ELSE NULL END , problem_code = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN problem_code ELSE NULL END , next = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN next ELSE 0 END , retry_at = CASE WHEN wait_scope IS NOT NULL AND retry_at > %s THEN retry_at ELSE NULL END WHERE kind IN ( 'filter' , 'theses' ) AND oko_json_extract ( payload , '$.input_revision' ) IS DISTINCT FROM ( SELECT oko_json_extract ( i . processing_metadata , '$._revision' ) FROM items_read i WHERE i . id = jobs . item ) RETURNING kind,item",
            (self.store.wall_clock(),)*4).fetchall()
        for row in changed:
            if row['kind']=='filter':self.store.processing.filter_changed(row['item'])

    @contextmanager
    def cancellation(self) -> Iterator[None]:
        # Revocation is valid even after new-work admission has closed.
        if self.store.db.transaction_depth:
            yield
        else:
            with transaction(self.store.db):yield
