from oko.storage.publication_owners import owned_ids, owner_expr
from oko.storage.database import expect_row, Connection, Row, INTEGRITY_ERRORS
from oko.collection.external_media import saved_external, expected_incomplete
from oko.collection.adapters import ExternalKey, ExternalReference, Platform
from oko.collection.telegram.reading import saved_preview
from oko.storage.collection_status import CollectionStatus
from oko.storage import material_read, collection_jobs, publication_links, youtube_media
from oko.storage import memberships as membership_graph
from oko.storage import processing_input as publication_input
from oko.storage.reading import reading_snapshot, reading_value, retry_time, reader_now, saved_content
from oko.diagnostics import record_caught
from dataclasses import dataclass, replace
import json
from pathlib import Path
import shutil
import time
from collections.abc import Callable, Generator
from contextlib import nullcontext
from typing import Any

from oko.storage.publications import Publications
from oko.storage.media import MediaFiles
from oko.storage.reader_versions import ReaderVersions
from oko.storage.source_configuration import changed as source_changed
from oko.storage.reader_status import changed as status_changed
from oko.storage.postgres_connection import PostgresConnection
from oko.storage.problems import Problems
from oko.storage.youtube import YouTubeAccess
from oko.storage.composition import FolderContents, ReadingComposition
from oko.collection.telegram.contracts import HistoryEnvelope
from oko.storage.filter_results import FilterResults
from oko.storage.processing_coordination import ProcessingState
from oko.collection.contracts import SourceError, Material, Publication, SourceManifest
from oko.storage.subscriptions import SubscriptionRepository
from oko.storage.job_writes import execute as job_write
from oko.storage.transaction import begin_write, transaction
from oko.storage.media_paths import existing_file
from oko.storage.job_repository import JobRepository
from oko.domain.collection import CollectionEffects
from oko.subscriptions.history import InitialHistory
from oko.processing.llamacpp import ProcessorConfig
from oko.processing.execution import CodexSettings


@dataclass(frozen=True)
class WorkSummary:
    kind: str
    status: str
    feed: str | None
    count: int
    error_code: str | None
    retry_at: float | None


class Store:
    """The database owns durable work and publications; files hold local media."""
    @staticmethod
    def is_integrity_failure(error: BaseException) -> bool:
        return isinstance(error, INTEGRITY_ERRORS)

    def connect_telegram(self, binding: dict[str,Any], info: dict[str,Any], source: str, folder: str, history: InitialHistory | None) -> dict[str,Any]:
        boundary = nullcontext() if self.db.transaction_depth else transaction(self.db)
        with boundary:
            membership_graph.lock_graph(self.db)
            result = self.subscriptions.telegram.connect(binding,info,source,folder,history,self.subscriptions)
            self.problems.sync_codex_feed_added(result['id'], self.wall_clock())
            return result

    def change_telegram_access(self, feed: str, binding: dict[str,Any], info: dict[str,Any]) -> dict[str,Any]:
        boundary = nullcontext() if self.db.transaction_depth else transaction(self.db)
        with boundary:
            begin_write(self.db)
            membership_graph.lock_graph(self.db)
            result=self.subscriptions.telegram.change_access(feed,binding,info,self.subscriptions)
            self.publications.configure_feed(feed)
            self.publications.changed_adapter(feed)
        return result

    def __init__(self, root: Path, fault: Callable[[str], None], *, wall_clock: Callable[[], float] = time.time, clock: Callable[[], float] = time.monotonic, database_url: str | None = None, defer_recovery: bool = False) -> None:
        self.root, self.fault = root, fault
        self.wall_clock = wall_clock
        self.clock = clock
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db: Connection
        try:
            if database_url is None:
                raise ValueError('PostgreSQL connection is required')
            self.db = PostgresConnection(database_url, clock=clock, wall_clock=wall_clock)
            self.db.claim_store()
            self.jobs = JobRepository(self)
            self.subscriptions = SubscriptionRepository(self.db, self.fault, restore_discovery=self.jobs.restore_feed_discovery, wall_clock=self.wall_clock)
            self.publications = Publications(self)
            self.media = MediaFiles(self)
            self.collection_status = CollectionStatus(self.db, self.clock, self.wall_clock,self)
            self.filters = FilterResults(self)
            self.processing = ProcessingState(self)
            self.reading = ReadingComposition(self)
            self.youtube = YouTubeAccess(self)
            self.problems = Problems(self)
            from oko.storage.incomplete_index import recover as recover_incomplete_class
            recover_incomplete_class(self)
            from oko.storage.incomplete_group_draft import recover_group_keys,recover_group_problems,TransactionGroupDraft
            if self.db.execute("SELECT 1 FROM state WHERE key='experimental-incomplete-problems-v1'").fetchone():
                self.incomplete_draft=TransactionGroupDraft(self)
                self.db.before_commit=self.incomplete_draft.flush
                setattr(self.db,'incomplete_group_mark',self.incomplete_draft.mark_materials)
                setattr(self.db,'incomplete_job_mark',self.incomplete_draft.mark_job)
                setattr(self.db,'youtube_admission_feed_mark',self.incomplete_draft.mark_admission_feed)
                if expect_row(self.db.execute('SELECT youtube_admission_recovered FROM youtube_access WHERE id=1').fetchone())[0]:self.youtube_admission_ready=True
            with self.db:
                self.problems.sync_codex_choice(self.wall_clock())
            for name in ('content', 'staging'):
                (root / name).mkdir(exist_ok=True)
            with self.db:
                collection_jobs.interrupt(self)
                running_source=[row['id'] for row in self.db.execute("SELECT id FROM jobs WHERE status='running' AND kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND problem_code LIKE 'source-%%'")]
                job_write(self.db, "UPDATE jobs SET status='pending', generation=generation+1 WHERE status='running'")
                if running_source:
                    from oko.storage import source_groups
                    source_groups.sync_jobs(self,running_source,self.wall_clock())
                job_write(self.db, "UPDATE jobs SET next = 0 WHERE status = 'pending' AND next IS DISTINCT FROM 0")
            collection_jobs.recover(self)
            self.problems.recover_discover_problems()
            self.problems.recover_fetch_problems()
            self.problems.recover_fetch_null()
            self.problems.recover_youtube_null_fetch()
            self.problems.recover_fetch_identity_unconfirmed()
            self.problems.recover_fetch_identity_unconfirmed(additional=True)
            self.problems.recover_media_problems()
            self.problems.recover_media_null_problems()
            self.problems.recover_media_profile_problems()
            self.problems.recover_subtitles()
            self.problems.recover_subtitles(profile_only=True)
            with self.db:
                self.problems.recover_processor_duplicates(self.wall_clock())
                self.problems.finish_processor_change([],None,self.wall_clock())
            self.problems.recover_processing_problems()
            self.problems.recover_filter_profile_problems()
            self.problems.recover_theses_profile_problems()
            from oko.storage.source_groups import recover as recover_source_groups
            recover_source_groups(self)
            if self.db.execute("SELECT 1 FROM state WHERE key LIKE 'source-youtube-timed:%%' LIMIT 1").fetchone():
                from oko.storage.source_groups import sync_youtube_timer
                with self.db:sync_youtube_timer(self,self.wall_clock())
            recover_group_keys(self)
            recover_group_problems(self)
            if not hasattr(self,'incomplete_draft'):
                self.incomplete_draft=TransactionGroupDraft(self)
                self.db.before_commit=self.incomplete_draft.flush
                setattr(self.db,'incomplete_group_mark',self.incomplete_draft.mark_materials)
                setattr(self.db,'incomplete_job_mark',self.incomplete_draft.mark_job)
                setattr(self.db,'youtube_admission_feed_mark',self.incomplete_draft.mark_admission_feed)
            from oko.storage.youtube_admission_jobs import recover_flags,group_objects,has_unknown_objects
            from oko.storage.youtube_admission import sync as sync_admission,close_current as close_admission
            recover_flags(self)
            self.youtube_admission_ready=True
            with self.db:
                from oko.storage.transaction import begin_write
                begin_write(self.db)
                now=self.wall_clock()
                limits=self.youtube._limits()
                status=self.youtube.status(now)
                from oko.storage.problems import _readable_timestamp
                if status['paused']:
                    blocked=limits['blocked_until'] if _readable_timestamp(limits['blocked_until']) else None
                    next_at=status['next_at'] if _readable_timestamp(status['next_at']) else None
                    sync_admission(self,group_objects(self),blocked,now,next_at=next_at,unknown=has_unknown_objects(self),time_unknown=next_at is None or blocked is None)
                else:close_admission(self,now)
            from oko.storage.problems import _readable_timestamp
            if not _readable_timestamp(self.youtube.ready_at()):
                with self.db:
                    membership_graph.lock_graph(self.db)
                    self.incomplete_draft.mark_youtube_groups(pause=True)
            self.recover_files()
            self.read_versions = ReaderVersions(self)
            self.processing.background = defer_recovery
            self.processing.recovering = True
            self._recovery: Generator[None,None,None] | None = self._recover_steps()
            if not defer_recovery:
                while self.recover_step():
                    pass
        except BaseException:
            if hasattr(self, 'db'):
                self.db.close()
            raise

    def _recover_steps(self) -> Generator[None,None,None]:
        initial = True
        while True:
            # Legacy attempts need their revision before direct reconciliation,
            # otherwise a saved terminal outcome could be mistaken for new input.
            yield from self.jobs.backfill_processing_steps()
            yield from self.processing.recovery_steps(seed=initial)
            initial = False
            # User commands may have changed settings or membership between steps.
            if not self.processing.pending():
                break
        self.processing.recovering = False

    def _reconcile_steps(self) -> Generator[None,None,None]:
        """Runtime changes use the same bounded owner without repeating startup scans."""
        while True:
            yield from self.processing.recovery_steps(seed=False)
            if not self.processing.pending():
                break
        self.processing.recovering = False

    def recover_step(self) -> bool:
        """Advance owned recovery without leaving an SQL transaction open."""
        if self._recovery is None:
            if not self.processing.background or not self.processing.pending():
                return False
            self._recovery = self._reconcile_steps()
        try:
            next(self._recovery)
            return True
        except StopIteration:
            self._recovery = None
            return False

    def close_recovery(self) -> None:
        """Stop iteration without consuming or discarding durable dirty markers."""
        if self._recovery is not None:
            self._recovery.close()
            self._recovery = None

    def recover_files(self) -> None:
        # No uncommitted publication/file output is promoted on restart.
        staging=self.root/'staging'
        for path in staging.iterdir():
            if path.name != '_media':
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()


    def pause_history(self, feed: str, paused: bool) -> dict[str, Any]:
        self.subscriptions.check_owner('feed', feed)
        with self.db:
            result=self.subscriptions.history.pause(feed, paused)
            return result

    def change_history(self, feed: str, selection: InitialHistory | None) -> dict[str, Any]:
        self.subscriptions.check_owner('feed', feed)
        configured = next(f for f in self.subscriptions.feeds() if f['id'] == feed)
        if configured['transport'] not in ('rss', 'youtube') and not self.publications.feed(feed):
            raise ValueError('Догрузка доступна подключённому RSS/Atom или YouTube-фиду')
        if self.publications.feed(feed) and selection and selection.mode != 'count':
            raise ValueError('Догрузка YouTube поддерживает последние N видео')
        with self.db:
            result=self.subscriptions.history.start(feed, selection) if selection else self.subscriptions.history.cancel(feed)
            return result


    def cursor(self, feed: str) -> str | None:
        row=self.db.execute('SELECT cursor FROM feeds WHERE id = %s',(feed,)).fetchone()
        return str(row[0]) or None if row else None

    def collect_discovery(self,publications: list[Publication],cursor: str,feed: str,namespace: str = '',*,description: str | None = None,successful: bool = True,source_order: tuple[ExternalKey,...] = ()) -> CollectionEffects:
        effects=CollectionEffects()
        with self.db:
            membership_graph.lock_graph(self.db)
            history_items=[]
            item_status_changed = False
            saved_items: set[str] = set()
            added_memberships: list[str] = []
            completed_links: set[str] = set()
            envelope=HistoryEnvelope.decode(cursor,adapter='web') if namespace=='telegram-web' else None
            selection=json.loads(cursor) if not envelope and self.subscriptions.history.status(feed) else {}
            history_row=self.db.execute('SELECT selection FROM initial_history WHERE feed=%s', (feed,)).fetchone()
            history_selection=json.loads(history_row['selection']) if history_row else {}
            result_run=envelope.position.get('run') if envelope else selection.get('run')
            stale_history=bool(history_selection.get('restore_unread') and result_run and result_run != history_selection.get('run'))
            enrolled=set(envelope.selection.enrolled_ids) if envelope else set(selection.get('history_enroll',[]))
            if stale_history:
                enrolled.clear()
            ordered=source_order
            known={ExternalKey(platform,row['external_id']):row['source_order'] for platform in {key.platform for key in ordered}
                for row in self.db.execute('SELECT external_id , source_order FROM publications WHERE platform = %s AND source_order IS NOT NULL AND external_id = ANY(%s)',
                    (platform.value,[key.external_id for key in ordered if key.platform==platform]))}
            anchor=next((index for index in reversed(range(len(ordered))) if ordered[index] in known),None)
            # Positive ranks leave room for later uploads and earlier history.
            base_order=known[ordered[anchor]]+anchor if anchor is not None else 1_000_000_000
            orders={key:base_order-index for index,key in enumerate(ordered)}
            for publication in publications:
                # Earlier entries can create linked identities in this page.
                plan=self.publications.plan(feed,[publication]).get(publication.id)
                if plan is None:
                    continue
                item=plan.item
                if plan.new_source_jobs:
                    from oko.storage import source_groups
                    source_ids=[job_id for job_id in plan.new_source_jobs if job_id not in effects.source_groups_before]
                    for start in range(0,len(source_ids),400):
                        group=source_ids[start:start+400]
                        effects.source_groups_before.update(source_groups.job_keys_many(self,group))
                        evidence=source_groups.job_evidence_many(self,[(job_id,None) for job_id in group])
                        for key,objects in evidence.items():effects.source_evidence_before.setdefault(key,set()).update(objects)
                if plan.fetch_problem_job is not None and plan.fetch_problem_job not in effects.fetch_problems_checked:
                    from oko.storage.fetch_job_problems import managed_candidates,previous_candidates
                    effects.fetch_problems_checked.add(plan.fetch_problem_job)
                    previous_fetch=managed_candidates(self,plan.fetch_problem_job,self.wall_clock())
                    if any(candidate.get('fetch_youtube_event') for candidate in previous_fetch):
                        previous_fetch=previous_candidates(self,plan.fetch_problem_job,self.wall_clock())
                    if previous_fetch:effects.fetch_problems_before[plan.fetch_problem_job]=previous_fetch
                if plan.fetch_problem_children:
                    publication_links.capture_fetch_problems(self,effects,set(plan.fetch_problem_children))
                if plan.media_problem_exists:
                    publication_links.capture_media_problems(self,effects,{item})
                if plan.media_problem_children:
                    publication_links.capture_media_problems(self,effects,set(plan.media_problem_children))
                if plan.source_problem_exists:
                    publication_links.capture_source_groups(self,effects,{item})
                if plan.processing_problem_exists:
                    publication_links.capture_processing_problems(self,effects,{item})
                if plan.processing_problem_children:
                    publication_links.capture_processing_problems(self,effects,set(plan.processing_problem_children))
                if publication.adapter_reference is None:
                    raise ValueError('Adapter did not return a publication external key')
                ref = publication.adapter_reference
                created = bool(self.db.execute('INSERT INTO publications (id, url, members, platform, external_id, published_at, discovered_at) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                    (item, publication.url, json.dumps(publication.members), ref.key.platform.value, ref.key.external_id, publication.published if publication.published_known else None, self.wall_clock())).rowcount)
                item_status_changed |= created
                membership=membership_graph.add(self.db,feed,item,created=created)
                if membership:
                    added_memberships.append(item)
                    saved_items.add(item)
                if publication.adapter_reference is None:
                    raise ValueError('Adapter did not return a publication external key')
                rank=orders.get(publication.adapter_reference.key)
                if rank is not None and not publication.published_known:
                    item_status_changed |= bool(self.db.execute('UPDATE publications SET source_order = %s WHERE id = %s AND source_order IS DISTINCT FROM %s',(rank,item,rank)).rowcount)
                self.publications.identify(item,publication.adapter_reference)
                if plan.content is not None:
                    assert plan.fingerprint is not None
                    self.publications.publish(item,plan.content,plan.fingerprint,effects)
                    completed = bool(self.db.execute('UPDATE publications SET complete = 1 WHERE id = %s AND complete IS DISTINCT FROM 1',(item,)).rowcount)
                    item_status_changed |= completed
                    if completed:
                        completed_links.add(item)
                    saved_items.add(item)
                row=expect_row(self.db.execute('SELECT * FROM publications WHERE id = %s',(item,)).fetchone())
                if publication.published_known and row['published_at'] is None:
                    self.db.execute('UPDATE publications SET published_at = %s WHERE id = %s',(publication.published,item))
                    item_status_changed = True
                if publication.id in enrolled:
                    history_items.append(item)
                needs_fetch=publication.teaser is None or publication.teaser.adapter_content is None or publication.teaser.adapter_content.needs_fetch
                if publication.members and set(publication.members)!=set(json.loads(row['members'])):
                    needs_fetch=True
                if publication.observed_only:
                    needs_fetch=self._needs_collection(item,publication.teaser)
                if needs_fetch and not self.publications.source_deleted(item):
                    members=sorted(set(json.loads(row['members']))|set(publication.members))
                    if members!=json.loads(row['members']):
                        self.db.execute('UPDATE publications SET members = %s WHERE id = %s',(json.dumps(members),item))
                        item_status_changed = True
                        saved_items.add(item)
                        effects.fetch[item]=True
                    effects.fetch.setdefault(item,False)
            if item_status_changed:
                status_changed(self.db)
            self.read_versions.memberships_changed(added_memberships)
            if not stale_history and (envelope or 'history_enroll' in selection):
                restoring = self.db.execute("SELECT selection::jsonb->>'restore_unread' = 'true' FROM initial_history WHERE feed=%s", (feed,)).fetchone()
                if restoring and restoring[0] and history_items:
                    previously_enrolled = {row[0] for row in self.db.execute('SELECT item FROM history_members WHERE feed=%s AND item=ANY(%s)', (feed, history_items))}
                    membership_graph.set_viewed(self.db, [item for item in history_items if item not in previously_enrolled], False)
                self.subscriptions.history.enroll(feed,history_items,envelope.selection.scan_complete if envelope else selection['scan_complete'])
                if envelope:
                    self.subscriptions.history.telegram_state(feed,envelope.selection)
                for item in history_items:
                    row=expect_row(self.db.execute('SELECT complete FROM publications WHERE id = %s',(item,)).fetchone())
                    if self.publications.source_deleted(item):
                        self.subscriptions.history.failed(item,unavailable=True)
                    elif row['complete']:
                        self.subscriptions.history.saved(item,bool(self.original(item)[0].get('full')))
            if selection and not stale_history:
                self.subscriptions.history.discovery_state(feed,selection)
            for item in saved_items:
                self.processing.reconcile_accepted_item(item)
            publication_links.notify_parents_many(self, set(added_memberships) | completed_links)
            if successful:
                self.subscriptions.details.record(feed,'updated')
            self.subscriptions.details.description(feed,description)
            self.fault('before_discovery_commit')
            self.db.execute('UPDATE feeds SET cursor = %s WHERE id = %s',(cursor,feed))

        return effects

    def source_http_until(self, scope: str) -> float:
        row=self.db.execute('SELECT value FROM state WHERE key = %s',('source-http:'+scope,)).fetchone()
        return float(row[0]) if row else 0.

    def source_http_wait(self, scope: str, until: float) -> None:
        with self.db:
            self.db.execute('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value',
                            ('source-http:'+scope,str(until)))

    def telegram_http_state(self, group: str) -> dict[str, Any]:
        row = self.db.execute('SELECT value FROM state WHERE key = %s', ('telegram-http:'+group,)).fetchone()
        return json.loads(row[0]) if row else {}

    def telegram_http_wait(self, group: str, state: dict[str, Any]) -> None:
        with self.db:
            self.db.execute('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value',
                            ('telegram-http:'+group, json.dumps(state)))

    @reading_snapshot()
    def _needs_collection(self, item: str, observed: Material | None = None) -> bool:
        material = self.material(item)
        if not material or not material.get('complete',True):
            return True
        header, _ = self.original(item) if material and not material.get('full',False) else ({}, '')
        linked_partial = expected_incomplete(SourceManifest.model_validate_json(json.dumps(header['source_manifest']))) if header.get('source_manifest') else False
        if material.get('full',False):
            return bool(observed and observed.source_manifest and observed.source_manifest.post_web_unavailable and not material.get('post_web_unavailable'))
        if not observed or not observed.source_manifest:
            return False
        header, _ = self.original(item)
        if not header.get('source_manifest'):
            return True
        previous = SourceManifest.model_validate_json(json.dumps(header['source_manifest']))
        current = observed.source_manifest
        return previous.post_web_unavailable!=current.post_web_unavailable or (not current.post_web_unavailable and previous.post!=current.post) or {r.key:r.external for r in previous.resources if r.status=='external'}!={r.key:r.external for r in current.resources if r.status=='external'}

    def _has_unviewed(self, item: str) -> bool:
        return self.db.execute('SELECT 1 FROM publications WHERE id = %s AND viewed = 0',(item,)).fetchone() is not None

    def _has_publication(self, item: str) -> bool:
        return self.db.execute('SELECT 1 FROM publications WHERE id = %s',(item,)).fetchone() is not None


    @reading_value
    def item_feed(self, item: str) -> str:
        row = material_read.item(self.db,item)
        if row is None:
            owner = self.media.owner(item)
            row = material_read.item(self.db,owner) if owner else None
        if row is None:
            raise LookupError('Publication not found')
        if row['feed_id'] is None:
            root=self.db.execute(f"SELECT {owner_expr('i')} AS feed_id FROM publication_links l JOIN publications i ON i.id=l.source_publication_id WHERE l.target_publication_id = %s AND {owner_expr('i')} IS NOT NULL ORDER BY i.id LIMIT 1",(item,)).fetchone()
            if root:
                return str(root[0])
            return ''
        return str(row['feed_id'])

    def feed_status(self, feed: str, **state: Any) -> None:
        with self.db:
            self.db.execute('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value', ('feed-status:' + feed, json.dumps(state)))

    def source_statuses(self) -> list[dict[str, Any]]:
        return [dict(feed_id=row['key'].split(':', 1)[1], **json.loads(row['value']))
                for row in self.db.execute('SELECT * FROM state WHERE key LIKE %s', ('feed-status:%',))]

    def ready_sources(self, feeds: list[str]) -> int:
        if not feeds:
            return 0
        return int(expect_row(self.db.execute(f"""SELECT count(*) FROM unnest(%s::text[]) f(id)
            WHERE NOT EXISTS (SELECT 1 FROM jobs j
                WHERE j.item=f.id
                    AND j.status IN ('pending','running') AND j.kind='discover' OFFSET 0)
            AND NOT EXISTS (SELECT 1 FROM ({owned_ids('= f.id')}) owner WHERE EXISTS (
                SELECT 1 FROM jobs j WHERE j.item=owner.id AND j.status IN ('pending','running')
                    AND j.kind IN ('fetch','filter','theses') OFFSET 0) OFFSET 0)""",
            (feeds,)).fetchone())[0])


    def publication(self, item: str) -> Publication | None:
        row = self.db.execute('SELECT * FROM publications WHERE id = %s', (item,)).fetchone()
        if row:
            header, text = self.publications.original(item) if row['complete'] else ({},'')
            reference=ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),row['url'])
            teaser = Material(header.get('title',''),header.get('full',False),header.get('reason',''),
                members=tuple(json.loads(row['members'])),description=header.get('description',''),
                source_manifest=SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None) if row['complete'] else None
            return Publication(row['external_id'],row['url'],row['published_at'] or 0.,teaser,tuple(json.loads(row['members'])),row['published_at'] is not None,adapter_reference=reference)
        return None

    def collect_material(self, item: str, material: Material, *, manual: bool = False) -> tuple[SourceError | None,CollectionEffects]:
        effects=CollectionEffects()
        if not self._has_publication(item):
            return None,effects
        if material.source_manifest and material.source_manifest.post_web_unavailable:
            media=[dict(row) for row in self.db.execute('SELECT * FROM media_files WHERE publication_id = %s',(item,))]
            missing=[row for row in media if row['storage_key'] and existing_file(self.root,row['storage_key']) is None or row['status']=='unavailable' and row['reason']=='Локальный файл вложения недоступен']
            if missing:
                error=SourceError('Локальный файл вложения недоступен',code='media-unavailable',retryable=False)
                content=json.loads(expect_row(self.db.execute('SELECT content FROM publications WHERE id = %s',(item,)).fetchone())[0])
                content['extra']['_reader']['source_manifest']['post_web_unavailable']=True
                for row in missing:
                    row.update(status='unavailable',reason=error.label,storage_key=None)
                self.media.project_header(content,media)
                with self.db:
                    membership_graph.lock_graph(self.db)
                    for row in missing:
                        self.db.execute("UPDATE media_files SET status = 'unavailable' , reason = %s , storage_key = NULL WHERE id = %s",(error.label,row['id']))
                    self.db.execute('UPDATE publications SET content = %s WHERE id = %s',(json.dumps(content,ensure_ascii=False),item))
                    from oko.storage.incomplete_index import save_class
                    save_class(self.db,item,content)
                    self.collection_history(item,replace(material,collection_error=error))
                return error,effects
        if material.source_manifest and material.source_manifest.post_web_unavailable:
            saved=self.db.execute('SELECT complete FROM publications WHERE id=%s', (item,)).fetchone()
            retained_header, retained_text = self.publications.original(item) if saved and saved['complete'] else ({}, '')
            raw_manifest=retained_header.get('source_manifest')
            manifest=SourceManifest.model_validate_json(json.dumps(raw_manifest)) if raw_manifest else None
        else:
            manifest=None
        if manifest is not None:
            observed_manifest=material.source_manifest
            assert observed_manifest is not None
            accept_preview = (observed_manifest.preview_fragment is not None
                and (manifest.preview_fragment is not None or
                     manifest.post.get('text') == observed_manifest.post.get('text')
                     and all(r.status == 'unsupported' and r.sha256 is None
                         and r.reason == 'Пост не поддерживается публичным предпросмотром' for r in manifest.resources)))
            if (retained_text.strip() or any(resource.status=='loaded' for resource in manifest.resources)) and not accept_preview:
                material = Material(retained_header.get('title',''), False,
                    retained_header.get('reason',''), members=material.members,
                    captions=retained_header.get('captions'), description=retained_header.get('description',''),
                    source_manifest=manifest.model_copy(update={'post_web_unavailable':True}),
                    collection_error=material.collection_error)
        old_media={row['id']:dict(row) for row in self.db.execute('SELECT * FROM media_files WHERE publication_id = %s',(item,))} if manual else {}
        prepared=self.publications.prepare(item,material)
        fingerprint=publication_input.digest(prepared[0])
        self.fault('after_file_move')
        with self.db:
            membership_graph.lock_graph(self.db)
            if not self._has_publication(item):
                return None,effects
            if material.adapter_content and material.adapter_content.metadata.reference.key.platform==Platform.telegram:
                self.publications.identify(item,material.adapter_content.metadata.reference)
            self.publications.publish(item,prepared,fingerprint,effects)
            item_status_changed = bool(self.db.execute('UPDATE publications SET complete = 1 WHERE id = %s AND complete IS DISTINCT FROM 1',(item,)).rowcount)
            if material.members:
                item_status_changed |= bool(self.db.execute('UPDATE publications SET members = %s WHERE id = %s AND members IS DISTINCT FROM %s',(json.dumps(material.members),item,json.dumps(material.members))).rowcount)
            if item_status_changed:
                status_changed(self.db)
            publication_links.notify_parents(self,item,preserve_cancelled=item in effects.title_only_changes)
            for row in prepared[1]:
                old=old_media.get(row['id'])
                if row['status']=='ready' and old and not (row['resource_identity'] is not None and old['source_locator']!=row['source_locator']):
                    effects.revalidate[row['id']]=True
            self.collection_history(item,material)
            self.processing.reconcile_accepted_item(item)
        self.processing.sync()
        with self.db:
            self.publications.status(item)

        return material.collection_error,effects

    def collection_history(self, item: str, material: Material) -> None:
        if not material.collection_error or not self.subscriptions.history.member(item):
            self.subscriptions.history.saved(item,material.full)
        if not material.collection_error:
            self.publications.set_source_deleted([item], False)


    def folder_contents(self, folder: str) -> FolderContents:
        return self.reading.folder_contents(folder)

    def set_processor_viewed(self, items: list[str], viewed: bool) -> None:
        """Keep processor visibility in the same graph transaction."""
        membership_graph.lock_graph(self.db)
        source_effects=CollectionEffects()
        publication_links.capture_source_groups(self,source_effects,set(items))
        affected=self.problems.processor_jobs_for_materials(items)
        previous=self.problems.prepare_processor_change(affected,self.wall_clock())
        if previous is not None and viewed:
            previous['objects']=[obj for obj in previous['objects'] if obj['kind']!='material' or obj['id'] not in items]
        membership_graph.set_viewed(self.db,items,viewed)
        if source_effects.source_groups_before:self.jobs.sync_captured_source_groups(source_effects)
        if affected:
            self.problems.finish_processor_change(affected,previous,self.wall_clock(),removed_outcome='irrelevant' if viewed else 'resolved')

    def viewed(self, item: str) -> bool:
        return self.viewed_set(self.reading.members(item))

    def viewed_set(self, members: list[str]) -> bool:
        members = sorted(set(members))
        with self.db:
            membership_graph.lock_graph(self.db)
            rows = self.db.execute('SELECT id FROM publications WHERE id=ANY(%s)', (members,)).fetchall()
            if len(rows) != len(members):
                return False
            feeds = {row['feed_id'] for row in self.db.execute('SELECT DISTINCT feed_id FROM publications WHERE id=ANY(%s) AND viewed=0 AND feed_id IS NOT NULL', (members,))}
            membership_graph.set_viewed(self.db, members, True)
            for feed in feeds:
                self.subscriptions.details.record(feed, 'marked_read')
        return True

    def unviewed_set(self, identities: list[str]) -> None:
        from oko.storage.reader_facts import ReaderFacts
        facts = ReaderFacts(lambda: self)
        selected = [row['id'] for row in self.db.execute('SELECT p.id FROM publications p JOIN feeds f ON f.id=p.feed_id WHERE NOT f.deleted AND p.id=ANY(%s)', (sorted(set(identities)),))]
        for identity in selected:
            for kind, section in (('filter', 'filter'), ('theses', 'summary')):
                if facts.result_stale(identity, kind):
                    self.db.execute("UPDATE publications SET processing_metadata=jsonb_set(processing_metadata::jsonb, %s::text[], 'true'::jsonb)::text WHERE id=%s", ([section, 'preserve_stale'],identity))
        self.set_processor_viewed(selected, False)
        for identity in selected:
            self.processing.reconcile_accepted_item(identity)

    def remove_feed(self, feed: str) -> None:
        self.subscriptions.check_owner('feed', feed)
        with self.db:
            membership_graph.lock_graph(self.db)
            self.db.execute('UPDATE feeds SET deleted = true WHERE id = %s', (feed,))
            self.jobs.suspend_removed_feed_discovery(feed)
            source_changed(self.db)
    @reading_value
    def original(self, item: str) -> tuple[dict[str, Any], str]:
        own=material_read.item(self.db,item)
        header,text=self.publications.original(item)
        if own:
            if 'youtube' in saved_content(own['content']).get('extra',{}):
                projected=youtube_media.add_input(self,saved_content(own['content'],mutable=True))
                text='\n\n'.join(block['text'] for block in projected['blocks'] if block['type']=='text' and block.get('role')=='body')
                if projected['extra']['youtube'].get('captions')=='error' and header.get('captions')=='available':
                    header={**header,'captions':'error','full':False,'reason':'Локальный файл субтитров недоступен; повторите загрузку'}
            header=publication_links.project_header(self,item,header)
            article_text=publication_links.text(self,item)
            if article_text:
                text='\n\n'.join(part for part in (text,article_text) if part)
        return header,text

    def material(self, item: str) -> dict[str, Any] | None:
        from oko.storage import material_read
        row = material_read.item(self.db,item)
        active = row and row['feed_id'] is not None and self.db.execute('SELECT 1 FROM feeds WHERE id=%s AND NOT deleted', (row['feed_id'],)).fetchone()
        material = self.saved_publication(row) if row and active else None
        if material and material['complete']:
            display = material['text']
            filtered = material['filter']
            if filtered['status'] == 'done':
                for fragment in filtered['removed']:
                    display = display.replace(fragment, '')
            material['display_text'] = display
        return material

    def reading_content(self, row: Row) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None] | None:
        """Check the saved inputs shared by cards and completion counts.

        This does not build a card, its snippet, media URLs or work status.
        Broken stored content or active processing output stays in the captured
        publication set, but cannot contribute a readable card.
        """
        item = row['id']
        try:
            header, text = self.original(item)
            header['title']
            for attachment in header.get('attachments', []):
                attachment['name'], attachment['kind'], attachment['mime']
            json.loads(row['media_ids'])
            for child in publication_links.children(self, item):
                if child['complete']:
                    json.loads(child['media_ids'])
            filtered = self.filters.read(item)
            theses = self.processing.theses.read(item)
            linked = saved_preview(header)
            if row['platform'] == 'telegram':
                linked = publication_links.reading(self, item, self.publications.original(item)[1]) or linked
            return header, text, filtered, theses, linked
        except (OSError, ValueError, KeyError):
            record_caught('store.material')
            return None

    def saved_publication(self, row: Row) -> dict[str, Any] | None:
        """Saved publication facts without reader-specific text projections."""
        item = row['id']
        settings = self.subscriptions.effective('feed', row['feed_id'], details=False)
        base = dict(id=item, feed_id=row['feed_id'], url=row['url'], published=row['published_at'] or 0.,
                    published_known=row['published_at'] is not None, source_order=row['source_order'],
                    members=json.loads(row['members']), complete=bool(row['complete']),
                    originals=settings['effective']['originals']['value'])
        fetch = material_read.jobs(self.db, item).get('fetch')
        if not row['complete']:
            base.update(title='Материал загружается', loading=bool(fetch and fetch['status'] in ('pending', 'running')))
            return dict(base, text='')
        try:
            content = self.reading_content(row)
            if content is None:
                return None
            header, text, filtered, theses, linked = content
            base.update(changed=bool(header.get('changed')), source_deleted=self.publications.source_deleted(item),
                filter=filtered, title=header['title'], theses=theses,
                provenance=self.processing.theses.provenance(item), ai=self.processing.theses.status(item),
                version=self.content_version(item), loading=bool(fetch and (fetch['status']=='running' or fetch['status']=='pending' and fetch['next']<=reader_now(self.clock()))),
                full=header.get('full', True) and (not header.get('members') or set(header['members']) == set(base['members'])),
                reason=header.get('reason', ''), captions=header.get('captions'),
                attachments=[dict(url=header.get('_media_urls',{}).get(a['name'],f"/api/v3/materials/{item}/media/{a.get('media_id',a['name'])}"), kind=a['kind'], mime=a['mime']) for a in header.get('attachments', [])],
                images=[])
            base.update(saved_external(header, f'/api/v3/materials/{item}'))
            base['loading'] = base['loading'] or row['platform'] is not None and self.media.loading(item)
            base.update(text=text, description=header.get('description', ''), reply_preview=header.get('reply_preview'), linked_content=linked)
            return base
        except (OSError, ValueError, KeyError):
            record_caught('store.material')
            return None

    def work_summary(self, *, include_problems: bool = False) -> list[WorkSummary]:
        condition = "jobs.status IN ('pending','running')"
        if include_problems:
            # Match each existing partial index separately; a combined OR scans
            # the completed archive even when it has no problems.
            condition = """jobs.id IN (SELECT id FROM jobs WHERE status IN ('pending','running') OR error IS NOT NULL
                UNION SELECT id FROM jobs WHERE problem_code IS NOT NULL OR error IS NOT NULL)"""
        rows = self.db.execute(f"SELECT jobs . kind , jobs . status , jobs . problem_code , count ( * ) AS count , CASE WHEN jobs . kind = 'discover' THEN NULLIF ( jobs . item , '' ) WHEN jobs . kind = 'download_media' THEN ( SELECT {owner_expr('i')} FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = jobs . item ) ELSE {owner_expr('items')} END AS feed , min ( jobs . next ) AS next , sum ( CASE WHEN jobs . error IS NOT NULL THEN 1 ELSE 0 END ) AS errors FROM jobs LEFT JOIN publications items ON items . id = jobs . item WHERE {condition} AND jobs.kind NOT IN ('delete','feed-delete') GROUP BY jobs . kind , jobs . status , jobs . problem_code , feed ORDER BY min ( jobs . id )").fetchall()
        now, wall = self.clock(), self.wall_clock()
        feed_ids = sorted({row['feed'] for row in rows if row['feed']})
        deleted = {row['id'] for row in self.db.execute('SELECT id FROM feeds WHERE deleted AND id=ANY(%s)', (feed_ids,))} if feed_ids else set()
        return [WorkSummary(row['kind'],row['status'],row['feed'],row['count'],
                    row['problem_code'] or (row['kind']+'-failed' if row['errors'] else None),
                    retry_time(row['next'],now,wall) if row['status']=='pending' and row['errors'] and row['next'] else None)
                for row in rows if row['feed'] not in deleted]

    def status(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT kind , status , error , count ( * ) AS count FROM jobs WHERE kind NOT IN ('delete','feed-delete') AND (status IN ('pending','running') OR error IS NOT NULL) GROUP BY kind , status , error").fetchall()
        jobs = [dict(r) for r in rows]
        errors = sorted({r['error'] for r in rows if r['error']})
        return dict(jobs=jobs, errors=errors, deleting=False)


    def work_enabled(self) -> bool:
        row = self.db.execute("SELECT value FROM state WHERE key = 'work-enabled'").fetchone()
        return row is None or row['value'] != 'false'

    def set_work_enabled(self, enabled: bool) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES ( 'work-enabled' , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value", ('true' if enabled else 'false',))

    def codex_config(self) -> CodexSettings | None:
        value = self.subscriptions.settings.state_value('processor:codex')
        return CodexSettings.model_validate_json(value) if value else None

    def configure_codex(self, config: CodexSettings) -> None:
        # Future execution choice does not invalidate inputs or cancel work.
        boundary = nullcontext() if self.db.transaction_depth else transaction(self.db)
        with boundary:
            membership_graph.lock_graph(self.db)
            self.subscriptions.settings.set_state('processor:codex', config.model_dump_json())
            ids=[row['id'] for row in self.db.execute("SELECT id FROM jobs WHERE status='pending' AND problem_code='processor-config' AND payload::jsonb->>'preclaim_blocked'='true'")]
            processor_previous=self.problems.prepare_processor_change(ids,self.wall_clock())
            job_write(self.db, """UPDATE jobs SET status='pending',next=0,error=NULL,problem_code=NULL,
                payload=(payload-'preclaim_blocked')
                WHERE status='pending' AND problem_code='processor-config' AND payload::jsonb->>'preclaim_blocked'='true'""")
            self.problems.finish_processor_change(ids,processor_previous,self.wall_clock())
            self.problems.sync_codex_choice(self.wall_clock())

    @reading_value
    def processor_config(self) -> ProcessorConfig | None:
        value = self.subscriptions.settings.state_value('processor:llamacpp')
        return ProcessorConfig.model_validate_json(value) if value else None

    def configure_processor(self, config: ProcessorConfig) -> bool:
        boundary = nullcontext() if self.db.transaction_depth else transaction(self.db)
        with boundary:
            membership_graph.lock_graph(self.db)
            if self.processor_config() == config:
                return False
            self.subscriptions.settings.set_state('processor:llamacpp', config.model_dump_json())
            self.settings_changed()
        return True

    @reading_value
    def settings_version(self, feed: str | None = None) -> int:
        keys = ['settings-version'] + (['settings-version:' + feed] if feed else [])
        return max((int(value) for key in keys if (value := material_read.state(self.db,key)) is not None), default=0)

    def settings_changed(self, scope: str = 'oko', owner: str = 'global') -> None:
        with self.db:
            membership_graph.lock_graph(self.db)
            row = self.db.execute("SELECT value FROM state WHERE key = 'settings-sequence'").fetchone()
            sequence = max(int(row[0]) if row else 0, self.settings_version()) + 1
            self.db.execute("INSERT INTO state VALUES ( 'settings-sequence' , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value", (str(sequence),))
            if scope == 'oko':
                keys = ['settings-version']
            else:
                query = 'SELECT id FROM feeds WHERE ' + ('id = %s' if scope == 'feed' else 'folder_id = %s')
                keys = ['settings-version:' + row[0] for row in self.db.execute(query, (owner,))]
            self.db.executemany('INSERT INTO state VALUES ( %s , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value', [(key, str(sequence)) for key in keys])
            self.jobs.revoke_changed_running_inputs()
            self.processing.invalidate(scope, owner)
            self.problems.sync_codex_choice(self.wall_clock())
        self.processing.sync()

    @reading_value
    def content_version(self, item: str) -> str:
        def input_value(row: Any) -> list[Any]:
            content=saved_content(row['content'])
            youtube=content.get('extra',{}).get('youtube',{})
            return [row['title'],publication_input.blocks(content),json.loads(row['media_ids']),youtube.get('captions_digest')]
        own=material_read.item(self.db,item)
        return publication_input.digest([input_value(own) if own else None,
            [(row['id'],input_value(row)) for row in publication_links.children(self,item) if row['complete']]])


    def mark_source_removed(self, item: str) -> None:
        membership_graph.lock_graph(self.db)
        self.publications.set_source_deleted([item], True)
        publication_links.notify_parents(self,item)
