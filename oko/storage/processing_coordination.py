"""Current processing work and reconciliation of dependent reader results."""
from oko.storage import material_read
from oko.storage.reading import retry_time
from oko.storage.transaction import begin_write
from oko.storage.media_paths import existing_file
from oko.storage.job_writes import execute as job_write
from dataclasses import dataclass
import json
from typing import Literal, TYPE_CHECKING
from collections.abc import Iterator

from oko.processing.contracts import FilterResult, Theses
from oko.storage.summary_results import SummaryResults

if TYPE_CHECKING:
    from oko.storage.store import Store


Result = Theses | FilterResult


from oko.domain.processing import ProcessingWait


class ProcessingState:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        self._repairs: set[str] = set()
        self._repairing: set[str] = set()
        self.recovering = False
        self.background = False
        self.theses = SummaryResults(store)
    def waiting(self, item: str, kind: Literal['filter', 'theses'], now: float, wall_now: float) -> ProcessingWait | None:
        row = material_read.jobs(self.store.db,item).get(kind)
        if not row or row['status'] != 'pending' or not row['error']:
            return None
        reason: Literal['processor_not_configured', 'public_ai_not_allowed', 'processor_unavailable', 'retry_scheduled']
        if row['problem_code'] == 'processor-config':
            reason = 'processor_not_configured'
        elif row['problem_code'] == 'ai-permission':
            reason = 'public_ai_not_allowed'
        else:
            reason = 'retry_scheduled'
        return ProcessingWait(reason, retry_time(row['next'], now, wall_now) if row['next'] and reason != 'public_ai_not_allowed' else None)

    def recover(self) -> None:
        """Only startup scans the whole library, including interrupted file moves."""
        for _ in self.recovery_steps():
            pass
        self.recovering = False

    def recovery_steps(self, *, seed: bool = True) -> Iterator[None]:
        """Commit one publication at a time; callers may yield only between steps."""
        self.recovering = True
        if seed:
            # Startup recovery walks the existing library in bounded transactions;
            # it does not materialize another persistent queue for every item.
            after: str | None = None
            while True:
                query = 'SELECT id FROM publications WHERE viewed=0' + (' AND id>%s' if after is not None else '') + ' ORDER BY id LIMIT 1'
                row = self.store.db.execute(query, (after,) if after is not None else ()).fetchone()
                if row is None:
                    break
                after = row[0]
                self.reconcile_item(after)
                yield
        while self._repairs:
            self._repair_one()
            yield

    def invalidate(self, scope: str = 'oko', owner: str = 'global') -> None:
        query = 'SELECT DISTINCT i . id FROM publications i JOIN feed_publications m ON m . publication_id = i . id JOIN feeds f ON f . id = m . feed_id WHERE i . viewed = 0'
        params: tuple[str, ...] = ()
        if scope in ('feed', 'folder'):
            condition = 'f.id=%s' if scope=='feed' else 'f.folder_id=%s'
            query += ' AND ' + condition
            # A linked source's privacy participates in its parent's AI input.
            query += """ UNION SELECT DISTINCT parent.id FROM publications parent
                JOIN publication_links l ON l.source_publication_id=parent.id
                JOIN feed_publications m ON m.publication_id=l.target_publication_id
                JOIN feeds f ON f.id=m.feed_id WHERE parent.viewed=0 AND """ + condition
            params = (owner,owner)
        items = [row[0] for row in self.store.db.execute(query, params).fetchall()]
        from oko.storage.processing_batch import reconcile_settings
        reconcile_settings(self.store,items)

    def memberships_removed(self, identities: list[str]) -> None:
        """Reconcile surviving owners and revoke own AI work when none remain."""
        ordered = sorted(set(identities))
        for start in range(0,len(ordered),400):
            batch = ordered[start:start+400]
            rows = self.store.db.execute("""SELECT i.id,EXISTS(SELECT 1 FROM feed_publications m
                WHERE m.publication_id=i.id) AS has_owner FROM publications i WHERE i.id=ANY(%s)""", (batch,)).fetchall()
            feedless = [row['id'] for row in rows if not row['has_owner']]
            if feedless:
                processor_affected=[row['id'] for row in self.store.db.execute("SELECT id FROM jobs WHERE kind IN ('filter','theses') AND item=ANY(%s) AND (processor_visible OR problem_code='processor-config')",(feedless,))]
                processor_previous=self.store.problems.prepare_processor_change(processor_affected,self.store.wall_clock())
                stage_affected=[row['id'] for row in self.store.db.execute("SELECT id FROM jobs WHERE kind IN ('filter','theses') AND item=ANY(%s) AND problem_code IS NOT NULL AND problem_code<>'' AND problem_code NOT LIKE 'source-%%'",(feedless,))]
                stage_previous=self.store.problems.saved_processing_candidates(stage_affected)
                job_write(self.store.db, """UPDATE jobs SET status='cancelled',generation=generation+1,
                    input_snapshot=NULL,error=NULL,problem_code=NULL WHERE item=ANY(%s) AND kind IN ('filter','theses')
                    AND (status IS DISTINCT FROM 'cancelled' OR input_snapshot IS NOT NULL OR error IS NOT NULL OR problem_code IS NOT NULL)""", (feedless,))
                if processor_affected:
                    self.store.problems.finish_processor_change(processor_affected,processor_previous,self.store.wall_clock())
                self.store.db.execute("""UPDATE publications SET filtered_content='{}',summarized_content='{}',
                    processing_metadata=(processing_metadata::jsonb-'filter'-'summary'-'_pass'-'_revision'-'_revision_input')::text,
                    status=CASE WHEN viewed=0 THEN 'ready' ELSE status END WHERE id=ANY(%s)
                    AND (filtered_content IS DISTINCT FROM '{}' OR summarized_content IS DISTINCT FROM '{}'
                         OR processing_metadata::jsonb ?| ARRAY['filter','summary','_pass','_revision','_revision_input']
                         OR (viewed=0 AND status IS DISTINCT FROM 'ready'))""", (feedless,))
                for job_id,candidates in stage_previous.items():
                    self.store.problems.sync_processing_problem(job_id,candidates,self.store.wall_clock())
            for row in rows:
                if row['has_owner']:
                    self.reconcile_accepted_item(row['id'])

    def repair(self, item: str) -> None:
        """A read can discover missing bytes without writing inside its snapshot."""
        if item not in self._repairing:
            self._repairs.add(item)

    def filter_changed(self, item: str) -> None:
        """Update the dependent stage in the same transaction as the filter Job."""
        from oko.storage.processing_state import ProcessingRepository
        from oko.storage.reading import reading_snapshot
        repository = ProcessingRepository(self.store)
        with reading_snapshot():
            candidates = [(identity, signature, repository.satisfied(identity,'theses'), repository.revision(identity))
                          for identity,signature in repository.candidates('theses',item=item)]
        self.store.jobs.reconcile('theses',candidates)
        self.store.publications.status(item)

    def pending(self) -> bool:
        return bool(self._repairs)

    def sync(self) -> None:
        """Reconcile persisted affected publications in dependency order."""
        if self.recovering or self.background:
            return
        while self._repairs:
            self._repair_one()

    def _repair_one(self) -> None:
        item = min(self._repairs)
        self._repairs.remove(item)
        self._repairing.add(item)
        try:
            from oko.storage import memberships, publication_links, youtube_media
            with self.store.db:
                begin_write(self.store.db)
                self.store.db.after_rollback(lambda: self._repairs.add(item))
                memberships.lock_graph(self.store.db)
                row = self.store.db.execute("SELECT content FROM publications WHERE id=%s AND viewed=0", (item,)).fetchone()
                content = json.loads(row[0]) if row else {}
                identity = youtube_media.subtitle_id(content)
                media = self.store.db.execute("SELECT storage_key FROM media_files WHERE id=%s AND publication_id=%s AND status='ready'", (identity,item)).fetchone() if identity else None
                if media and (not media[0] or existing_file(self.store.root,media[0]) is None):
                    affected=self.store.problems.processor_jobs_for_materials([item])
                    previous=self.store.problems.prepare_processor_change(affected,self.store.wall_clock())
                    self.store.db.execute("UPDATE media_files SET status='pending_download',storage_key=NULL,reason='' WHERE id=%s", (identity,))
                    content['extra']['youtube']['captions'] = 'pending'
                    content['extra']['_reader']['captions'] = 'error'
                    self.store.db.execute('UPDATE publications SET content=%s WHERE id=%s', (json.dumps(content,ensure_ascii=False),item))
                    from oko.storage.incomplete_index import save_class
                    save_class(self.store.db,item,content)
                    job_write(self.store.db, "UPDATE jobs SET status='cancelled' WHERE kind='download_media' AND item=%s AND status IN ('failed','unavailable')", (identity,))
                    self.store.jobs.schedule_media(item,sync_processor=False)
                    if affected:self.store.problems.finish_processor_change(affected,previous,self.store.wall_clock())
                    publication_links.notify_parents(self.store,item)
                self.reconcile_accepted_item(item)
        except BaseException:
            self._repairs.add(item)
            raise
        finally:
            self._repairing.discard(item)

    def reconcile_item(self, item: str) -> None:
        # Accepted state and both stage Jobs commit together.
        with self.store.db:
            begin_write(self.store.db)
            self.reconcile_accepted_item(item)

    def reconcile_accepted_item(self, item: str) -> None:
        """Update both processing Jobs inside the owner's acceptance transaction."""
        from oko.storage.processing_state import ProcessingRepository
        from oko.storage.reading import reading_snapshot
        if not self.store.db.in_transaction:
            raise RuntimeError('Accepted publication requires an owned transaction')
        repository = ProcessingRepository(self.store)
        self.store.publications.reconcile(item)
        for kind in ('filter','theses'):
            with reading_snapshot():
                candidates = [(identity, signature, repository.satisfied(identity,kind), repository.revision(identity))
                              for identity,signature in repository.candidates(kind,item=item)]
            self.store.jobs.reconcile(kind,candidates)
        self.store.publications.status(item)
