from oko.storage.publication_owners import owned_ids
from oko.storage.database import expect_row, Connection, INTEGRITY_ERRORS
from oko.storage.collection_status import CollectionStatus
from oko.storage import material_read, collection_jobs, publication_links
from oko.storage import memberships as membership_graph
from oko.storage.reading import reading_snapshot, reading_value, retry_time
import json
from pathlib import Path
import shutil
import time
from collections.abc import Callable, Generator
from typing import Any, Literal

from oko.storage.publications import Publications
from oko.storage.media import MediaFiles
from oko.storage.publication_files import PublicationFiles
from oko.storage.reader_versions import ReaderVersions
from oko.storage.source_configuration import changed as source_changed
from oko.storage.reader_status import changed as status_changed
from oko.storage.postgres_connection import PostgresConnection
from oko.storage.problems import Problems
from oko.storage.youtube import YouTubeAccess
from oko.storage.composition import FolderContents, ReadingComposition
from oko.storage.filter_results import FilterResults
from oko.storage.processing_coordination import ProcessingState
from oko.collection.contracts import SourceError
from oko.storage.subscriptions import SubscriptionRepository
from oko.storage.job_writes import execute as job_write
from oko.storage.transaction import begin_write, transaction
from oko.storage.job_repository import JobRepository
from oko.storage.source_waits import SourceWaits
from oko.storage.collection_diagnostics import CollectionDiagnostics
from oko.subscriptions.history import InitialHistory
from oko.processing.config import ProcessorConfig
from oko.processing.execution import CodexSettings


from oko.domain.reader import WorkSummary


class Store:
    """The database owns durable work and publications; files hold local media."""
    @staticmethod
    def is_integrity_failure(error: BaseException) -> bool:
        return isinstance(error, INTEGRITY_ERRORS)

    def connect_telegram(self, binding: dict[str,Any], info: dict[str,Any], source: str, folder: str, history: InitialHistory | None) -> dict[str,Any]:
        if not self.db.transaction_depth:
            raise RuntimeError('Storage action requires an owning transaction')
        membership_graph.lock_graph(self.db)
        result = self.subscriptions.telegram.connect(binding,info,source,folder,history,self.subscriptions)
        self.problems.sync_codex_feed_added(result['id'], self.wall_clock())
        return result

    def change_telegram_access(self, feed: str, binding: dict[str,Any], info: dict[str,Any]) -> dict[str,Any]:
        if not self.db.transaction_depth:
            raise RuntimeError('Storage action requires an owning transaction')
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
            self.source_waits = SourceWaits(self.db)
            self.subscriptions = SubscriptionRepository(self.db, self.fault, wall_clock=self.wall_clock, restore_discovery=self.jobs.restore_feed_discovery)
            self.publications = Publications(self)
            self.media = MediaFiles(self)
            self.publication_files = PublicationFiles(self.root,self.media.active_paths,self.fault)
            self.collection_status = CollectionStatus(self.db, self.clock, self.wall_clock,self)
            self.filters = FilterResults(self)
            self.processing = ProcessingState(self)
            self.reading = ReadingComposition(self)
            self.youtube = YouTubeAccess(self)
            self.problems = Problems(self)
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
                running_source=[row['id'] for row in self.db.execute("SELECT id FROM jobs WHERE status='running' AND kind IN ('fetch','download_media','filter','theses','delete','feed-delete') AND reason_code LIKE 'source-%%'")]
                job_write(self.db, "UPDATE jobs SET status='pending', generation=generation+1 WHERE status='running'")
                if running_source:
                    from oko.storage import source_groups
                    source_groups.sync_jobs(self,running_source,self.wall_clock())
                job_write(self.db, "UPDATE jobs SET next = 0 WHERE status = 'pending' AND next IS DISTINCT FROM 0")
            with transaction(self.db):
                collection_jobs.recover(self)
            self.problems.recover_discover_problems()
            self.problems.recover_fetch_problems()
            self.problems.recover_finished_foreign_fetch_deletions()
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
            self.problems.recover_processing_profile_problems('filter')
            self.problems.recover_processing_profile_problems('theses')
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
            from oko.storage.collection_effects import CollectionEffectCapture
            from oko.storage.discovery import DiscoveryRepository
            from oko.storage.collected_publication import CollectedPublicationRepository
            effects = CollectionEffectCapture(self)
            self.discovery = DiscoveryRepository(db=self.db, publications=self.publications, subscriptions=self.subscriptions,
                versions=self.read_versions, effects=effects,
                root=self.root, unviewed=self.unviewed_set, clock=self.wall_clock, fault=self.fault)
            self.collected_publication = CollectedPublicationRepository(db=self.db, root=self.root,
                publications=self.publications, media=self.media, capture=effects,
                collection_history=self.collection_history, fault=self.fault)
            self.processing.background = defer_recovery
            self.processing.recovering = True
            self._recovery: Generator[None,None,None] | None = self._recover_steps()
            if not defer_recovery:
                while True:
                    with transaction(self.db):
                        recovering = self.recover_step()
                    if not recovering:
                        break
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
        if not self.db.transaction_depth:
            raise RuntimeError('Storage action requires an owning transaction')
        self.subscriptions.check_owner('feed', feed)
        result=self.subscriptions.history.pause(feed, paused)
        return result

    def change_history(self, feed: str, selection: InitialHistory | None) -> dict[str, Any]:
        if not self.db.transaction_depth:
            raise RuntimeError('Storage action requires an owning transaction')
        self.subscriptions.check_owner('feed', feed)
        configured = next(f for f in self.subscriptions.feeds() if f['id'] == feed)
        if configured['transport'] not in ('rss', 'youtube') and not self.publications.feed(feed):
            raise ValueError('Догрузка доступна подключённому RSS/Atom или YouTube-фиду')
        if self.publications.feed(feed) and selection and selection.mode != 'count':
            raise ValueError('Догрузка YouTube поддерживает последние N видео')
        result=self.subscriptions.history.start(feed, selection) if selection else self.subscriptions.history.cancel(feed)
        return result


    def cursor(self, feed: str) -> str | None:
        row=self.db.execute('SELECT cursor FROM feeds WHERE id = %s',(feed,)).fetchone()
        return str(row[0]) or None if row else None

    def _has_unviewed(self, item: str) -> bool:
        return self.db.execute('SELECT 1 FROM publications WHERE id = %s',(item,)).fetchone() is not None


    @reading_value
    def item_feed(self, item: str) -> str:
        row = material_read.item(self.db,item)
        if row is None:
            owner = self.media.owner(item)
            row = material_read.item(self.db,owner) if owner else None
        if row is None:
            raise LookupError('Publication not found')
        if row['reader_feed_id'] is None:
            root=self.db.execute(f"SELECT i.feed_id AS feed_id FROM publication_links l JOIN publications i ON i.id=l.source_publication_id WHERE l.target_publication_id = %s AND i.feed_id IS NOT NULL ORDER BY i.id LIMIT 1",(item,)).fetchone()
            if root:
                return str(root[0])
            return ''
        return str(row['reader_feed_id'])

    def feed_status(self, feed: str, **state: Any) -> None:
        if not self.db.in_transaction:
            raise RuntimeError('Feed status requires an owning transaction')
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
                    AND j.status IN ('pending','running') AND j.kind IN ('discover','feed-delete') OFFSET 0)
            AND NOT EXISTS (SELECT 1 FROM ({owned_ids('= f.id')}) owner WHERE EXISTS (
                SELECT 1 FROM jobs j WHERE j.item=owner.id AND j.status IN ('pending','running')
                    AND j.kind IN ('fetch','filter','theses','delete') OFFSET 0) OFFSET 0)""",
            (feeds,)).fetchone())[0])


    def collection_history(self, item: str, full: bool, error: bool) -> None:
        if not error or not self.subscriptions.history.member(item):
            self.subscriptions.history.saved(item,full)


    def folder_contents(self, folder: str) -> FolderContents:
        return self.reading.folder_contents(folder)

    def viewed(self, item: str) -> bool:
        return self.viewed_set(self.reading.members(item))

    def viewed_set(self, members: list[str]) -> bool:
        if not self.db.transaction_depth:
            raise RuntimeError('Marking publications viewed requires an owning transaction')
        members = sorted(set(members))
        membership_graph.lock_graph(self.db)
        rows = self.db.execute('SELECT p.id,p.feed_id,p.viewed FROM publications p JOIN feeds f ON f.id=p.feed_id WHERE NOT f.deleted AND p.id=ANY(%s)', (members,)).fetchall()
        if len(rows) != len(members):
            return False
        changed = [row['id'] for row in rows if not row['viewed']]
        if changed:
            self.db.execute('UPDATE publications SET viewed=true WHERE id=ANY(%s)', (changed,))
            self.subscriptions.history.viewed_many(changed)
            for feed in {row['feed_id'] for row in rows if not row['viewed'] and row['feed_id'] is not None}:
                self.subscriptions.details.record(feed, 'marked_read')
            self.incomplete_draft.mark_materials(changed)
            self.read_versions.memberships_changed(changed)
        return True

    def unviewed_set(self, identities: list[str]) -> None:
        if not self.db.transaction_depth:
            raise RuntimeError('Restoring reading state requires an owning transaction')
        from oko.storage import processing_input
        membership_graph.lock_graph(self.db)
        selected=self.db.execute("SELECT p.id,p.feed_id,p.processing_metadata FROM publications p JOIN feeds f ON f.id=p.feed_id WHERE NOT f.deleted AND p.viewed AND p.id=ANY(%s)",(sorted(set(identities)),)).fetchall()
        stale: dict[str,list[Literal['filter','theses']]]={}
        for row in selected:
            metadata=json.loads(row['processing_metadata'])
            projected=json.loads(row['processing_metadata'])
            processing_input.mark_stale(self,row['id'],row['feed_id'],projected)
            processing_input.repair_missing_subtitles(self,row['id'])
            stages: tuple[tuple[str,Literal['filter','theses']],...]=(('filter','filter'),('summary','theses'))
            for section,kind in stages:
                if projected.get(section,{}).get('stale'):
                    metadata[section]['preserve_stale']=True
                    stale.setdefault(row['id'],[]).append(kind)
            self.db.execute('UPDATE publications SET viewed=false,processing_metadata=%s WHERE id=%s',(json.dumps(metadata,ensure_ascii=False),row['id']))
        self.read_versions.memberships_changed([row['id'] for row in selected])
        for row in selected:
            item=row['id']
            self.processing.reconcile_accepted_item(item)
            for kind in stale.get(item,[]):
                if processing_input.stage_enabled(self,item,kind):
                    self.jobs.retry_processing(item,kind)

    def remove_feed(self, feed: str) -> None:
        if not self.db.transaction_depth:
            raise RuntimeError('Storage action requires an owning transaction')
        self.subscriptions.check_owner('feed',feed)
        membership_graph.lock_graph(self.db)
        if self.db.execute('UPDATE feeds SET deleted=true WHERE id=%s AND NOT deleted',(feed,)).rowcount:
            self.jobs.suspend_removed_feed_discovery(feed)
            source_changed(self.db)

    def work_summary(self, *, include_problems: bool = False) -> list[WorkSummary]:
        condition = "jobs.status IN ('pending','running')"
        if include_problems:
            # Match each existing partial index separately; a combined OR scans
            # the completed archive even when it has no problems.
            condition = """jobs.id IN (SELECT id FROM jobs WHERE status IN ('pending','running')
                UNION SELECT id FROM jobs WHERE status IN ('failed','unavailable') OR (status<>'done' AND (reason_code IS NOT NULL OR reason_message IS NOT NULL)))"""
        now, wall = self.clock(), self.wall_clock()
        rows = self.db.execute(f"SELECT jobs . kind , jobs . status , jobs . reason_code AS reason_code , (jobs.status='pending' AND jobs.wait_scope IS NOT NULL AND jobs.retry_at > %s) AS source_wait , count ( * ) AS count , CASE WHEN jobs . kind IN ( 'discover' , 'feed-delete' ) THEN NULLIF ( jobs . item , '' ) WHEN jobs . kind = 'download_media' THEN ( SELECT i.feed_id FROM media_files m JOIN publications i ON i . id = m . publication_id WHERE m . id = jobs . item ) ELSE items.feed_id END AS feed , min ( jobs . next ) AS next , sum ( CASE WHEN jobs . status IN ('failed','unavailable') OR jobs . reason_message IS NOT NULL OR jobs . reason_code IS NOT NULL THEN 1 ELSE 0 END ) AS errors FROM jobs LEFT JOIN publications items ON items . id = jobs . item WHERE {condition} GROUP BY jobs . kind , jobs . status , jobs . reason_code , source_wait , feed ORDER BY min ( jobs . id )",(wall,)).fetchall()
        feed_ids=sorted({row['feed'] for row in rows if row['feed']})
        deleted={row['id'] for row in self.db.execute('SELECT id FROM feeds WHERE deleted AND id=ANY(%s)',(feed_ids,))} if feed_ids else set()
        return [WorkSummary(row['kind'],row['status'],row['feed'],row['count'],
                    row['reason_code'] or ('source-wait' if row['source_wait'] else row['kind']+'-failed' if row['errors'] else None),
                    retry_time(row['next'],now,wall) if row['status']=='pending' and (row['errors'] or row['source_wait']) and row['next'] else None)
                for row in rows if row['feed'] not in deleted]

    def status(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT kind , status , reason_message AS error , count ( * ) AS count FROM jobs WHERE status IN ( 'pending' , 'running' ) OR (status <> 'done' AND reason_message IS NOT NULL) GROUP BY kind , status , reason_message").fetchall()
        # The durable feed sweep is deletion work, not a new public job kind.
        jobs = [dict(r, kind='delete' if r['kind'] == 'feed-delete' else r['kind']) for r in rows]
        errors = sorted({r['error'] for r in rows if r['error']})
        pending_deletes = any(r['kind'] in ('delete', 'feed-delete') and r['status'] != 'done' for r in rows)
        return dict(jobs=jobs, errors=errors, deleting=pending_deletes)


    def work_enabled(self) -> bool:
        row = self.db.execute("SELECT value FROM state WHERE key = 'work-enabled'").fetchone()
        return row is None or row['value'] != 'false'

    def set_work_enabled(self, enabled: bool) -> None:
        if not self.db.transaction_depth:
            raise RuntimeError('Configuration changes require an owning transaction')
        self.db.execute("INSERT INTO state VALUES ( 'work-enabled' , %s ) ON CONFLICT ( key ) DO UPDATE SET value = excluded . value", ('true' if enabled else 'false',))

    def codex_config(self) -> CodexSettings | None:
        value = self.subscriptions.settings.state_value('processor:codex')
        return CodexSettings.model_validate_json(value) if value else None

    def configure_codex(self, config: CodexSettings) -> None:
        if not self.db.transaction_depth:
            raise RuntimeError('Configuration changes require an owning transaction')
        # Future execution choice does not invalidate inputs or cancel work.
        membership_graph.lock_graph(self.db)
        self.subscriptions.settings.set_state('processor:codex', config.model_dump_json())
        ids=[row['id'] for row in self.db.execute("SELECT id FROM jobs WHERE status='pending' AND reason_code='processor-config' AND payload::jsonb->>'preclaim_blocked'='true'")]
        processor_previous=self.problems.prepare_processor_change(ids,self.wall_clock())
        job_write(self.db, """UPDATE jobs SET status='pending',next=0,reason_message=NULL,reason_code=NULL,
            payload=(payload-'preclaim_blocked')
            WHERE status='pending' AND reason_code='processor-config' AND payload::jsonb->>'preclaim_blocked'='true'""")
        self.problems.finish_processor_change(ids,processor_previous,self.wall_clock())
        self.problems.sync_codex_choice(self.wall_clock())

    @reading_value
    def processor_config(self) -> ProcessorConfig | None:
        value = self.subscriptions.settings.state_value('processor:llamacpp')
        return ProcessorConfig.model_validate_json(value) if value else None

    def configure_processor(self, config: ProcessorConfig) -> bool:
        if not self.db.transaction_depth:
            raise RuntimeError('Configuration changes require an owning transaction')
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
        if not self.db.transaction_depth:
            raise RuntimeError('Configuration changes require an owning transaction')
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
