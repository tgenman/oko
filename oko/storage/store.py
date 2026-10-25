from oko.storage.database import expect_row, Connection, Row, INTEGRITY_ERRORS
from oko.collection.external_media import saved_external, expected_incomplete
from oko.collection.adapters import ExternalKey, ExternalReference, Platform, MediaReference, PublicationContent, PublicationMetadata
from datetime import datetime, timezone
from oko.collection.telegram.reading import saved_preview
from oko.storage.collection_status import CollectionStatus
from oko.storage import material_read, collection_jobs, publication_links, youtube_media
from oko.storage import processing_input as publication_input
from oko.storage.reading import reading_value, retry_time, reader_now
from oko.diagnostics import record_caught
from dataclasses import dataclass, replace
import json
from pathlib import Path
import shutil
import sqlite3
import time
from collections.abc import Callable, Generator
from typing import Any

from oko.storage.publications import Publications, PUBLICATION_COLUMNS
from oko.storage.media import MediaFiles
from oko.storage.reader_versions import ReaderVersions
from oko.storage.connection import ReadingConnection
from oko.storage.postgres_connection import PostgresConnection
from oko.storage.problems import Problems
from oko.storage.youtube import YouTubeAccess
from oko.storage.composition import FolderContents, ReadingComposition
from oko.storage.read_sync import ReadSync
from oko.collection.telegram.contracts import HistoryEnvelope
from oko.storage.filter_results import FilterResults
from oko.storage.processing_coordination import ProcessingState
from oko.collection.contracts import ReplyPreview, SourceError, Material, Publication, SourceManifest
from oko.storage.subscriptions import SubscriptionRepository
from oko.storage.transaction import begin_write
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

    def change_telegram_access(self, feed: str, binding: dict[str,Any], info: dict[str,Any],
                               auto_read: bool | None) -> dict[str,Any]:
        with self.db:
            begin_write(self.db)
            result=self.subscriptions.telegram.change_access(feed,binding,info,auto_read,self.subscriptions)
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
            if database_url is not None:
                self.db = PostgresConnection(database_url, clock=clock, wall_clock=wall_clock)
                self.db.claim_store()
            else:
                from oko.storage.bootstrap import prepare
                prepare(root)
                self.db = ReadingConnection(root / 'state.sqlite')
                self.db.row_factory = sqlite3.Row
                ReaderVersions.functions(self.db,self.clock,self.wall_clock)
                self.db.execute('PRAGMA secure_delete=ON')
                self.db.execute('PRAGMA journal_mode=DELETE')
                if expect_row(self.db.execute('PRAGMA quick_check').fetchone())[0]!='ok':
                    raise sqlite3.DatabaseError('integrity')
                publication_columns=','.join(name+' '+declaration for name,declaration in PUBLICATION_COLUMNS.items())
                self.db.executescript('''CREATE TABLE IF NOT EXISTS items (
                  id TEXT PRIMARY KEY,source_id TEXT UNIQUE,url TEXT,published REAL,
                  viewed INTEGER DEFAULT 0,complete INTEGER DEFAULT 0,feed_id TEXT,
                  members TEXT DEFAULT '[]',published_known INTEGER DEFAULT 1,source_order INTEGER,'''+publication_columns+''');
                CREATE TABLE IF NOT EXISTS jobs (
                  id INTEGER PRIMARY KEY,kind TEXT,item TEXT DEFAULT '',status TEXT DEFAULT 'pending',
                  attempt INTEGER DEFAULT 0,next REAL DEFAULT 0,generation INTEGER DEFAULT 0,error TEXT,
                  manual_once INTEGER DEFAULT 0,problem_code TEXT,retry_at REAL,retry_delay REAL NOT NULL DEFAULT 0,wait_scope TEXT,
                  UNIQUE(kind,item));
                CREATE TABLE IF NOT EXISTS execution_settings (job INTEGER PRIMARY KEY,generation INTEGER,signature TEXT,parameters TEXT);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS thesis_inputs (item TEXT PRIMARY KEY,signature TEXT);
                CREATE TABLE IF NOT EXISTS source_deleted (item TEXT PRIMARY KEY);
                ''')
                self.db.execute('CREATE INDEX IF NOT EXISTS items_viewed_ids ON items(id) WHERE viewed=1')
                self.db.execute('CREATE INDEX IF NOT EXISTS items_unviewed_order ON items(published DESC,id,feed_id) WHERE viewed=0')
                self.db.execute('CREATE INDEX IF NOT EXISTS jobs_item ON jobs(item)')
                self.db.execute("CREATE INDEX IF NOT EXISTS jobs_running ON jobs(kind,item) WHERE status='running'")
                self.db.execute("CREATE INDEX IF NOT EXISTS jobs_active ON jobs(status,kind,item) WHERE status IN ('pending','running') OR error IS NOT NULL")
                self.db.execute('CREATE INDEX IF NOT EXISTS items_unviewed_feed ON items(feed_id,id) WHERE viewed=0')
                self.db.execute('CREATE INDEX IF NOT EXISTS items_feed_all ON items(feed_id,id)')
                self.db.execute("CREATE INDEX IF NOT EXISTS jobs_pending_next ON jobs(next,kind,item) WHERE status='pending'")
                self.db.execute("CREATE INDEX IF NOT EXISTS jobs_pending_kind_item ON jobs(kind,item,next,id) WHERE status='pending'")
                self.db.execute("CREATE INDEX IF NOT EXISTS jobs_problems ON jobs(kind,item) WHERE problem_code IS NOT NULL OR error IS NOT NULL")
                JobRepository.install(self.db)
            self.jobs = JobRepository(self)
            self.subscriptions = SubscriptionRepository(self.db, self.fault, wall_clock=self.wall_clock)
            self.publications = Publications(self)
            self.media = MediaFiles(self)
            self.collection_status = CollectionStatus(self.db, self.clock, self.wall_clock)
            self.filters = FilterResults(self)
            self.processing = ProcessingState(self)
            self.reading = ReadingComposition(self)
            self.read_sync = ReadSync(self)
            self.youtube = YouTubeAccess(self)
            self.problems = Problems(self)
            for name in ('content', 'staging'):
                (root / name).mkdir(exist_ok=True)
            with self.db:
                collection_jobs.interrupt(self)
                self.db.execute("UPDATE execution_settings SET generation=generation+1 WHERE job IN (SELECT id FROM jobs WHERE status='running')")
                self.db.execute("UPDATE jobs SET status='pending',generation=generation+1 WHERE status='running'")
                self.db.execute("UPDATE jobs SET next=0 WHERE status='pending'")
            collection_jobs.recover(self)
            self.recover_files()
            if self.db.execute("SELECT 1 FROM items WHERE feed_id='demo' LIMIT 1").fetchone():
                self.subscriptions.ensure_demo()
            self.read_versions = ReaderVersions(self)
            self.subscriptions.watch_configuration()
            self.read_sync.install()
            self.processing.background = defer_recovery
            self.processing.recovering = True
            self._recovery: Generator[None,None,None] | None = self._recover_steps(reconcile_stages=defer_recovery)
            if not defer_recovery:
                while self.recover_step():
                    pass
        except BaseException:
            if hasattr(self, 'db'):
                self.db.close()
            raise

    def _recover_steps(self, *, reconcile_stages: bool) -> Generator[None,None,None]:
        initial = True
        while True:
            yield from self.processing.recovery_steps(seed=initial)
            initial = False
            yield from self.jobs.backfill_processing_steps()
            yield from self.jobs.sync_reads_steps()
            if reconcile_stages:
                # The host must not hand a full startup queue to the first
                # synchronous worker tick after the reader is already serving.
                yield from self._prepare_processing_steps()
            # User commands may have changed settings or membership between steps.
            if (not self.processing.pending()
                    and self.read_sync._configuration == self.subscriptions.source_revision()):
                break
        self.processing.recovering = False

    def _prepare_processing_steps(self) -> Generator[None,None,None]:
        from oko.storage.processing_state import ProcessingRepository
        from oko.storage.reading import reading_snapshot
        from oko.domain.processing import Kind
        stages: tuple[Kind, ...] = ('filter','theses')
        processing = ProcessingRepository(self)
        for kind in stages:
            while True:
                with self.db:
                    with reading_snapshot():
                        candidates = [(item, signature, processing.satisfied(item,kind), processing.revision(item))
                                      for item,signature in processing.candidates(kind, limit=1)]
                    if not candidates:
                        break
                    self.jobs.reconcile(kind, candidates)
                yield

    def _reconcile_steps(self) -> Generator[None,None,None]:
        """Runtime changes use the same bounded owner without repeating startup scans."""
        while True:
            yield from self.processing.recovery_steps(seed=False)
            yield from self.jobs.sync_reads_steps()
            yield from self._prepare_processing_steps()
            if not self.processing.pending(stages=True) and not self.read_sync.pending():
                break
        self.processing.recovering = False

    def recover_step(self) -> bool:
        """Advance owned recovery without leaving an SQL transaction open."""
        if self._recovery is None:
            if not self.processing.background or not (self.processing.pending(stages=True) or self.read_sync.pending()):
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
            if path.name not in ('_media','_telegram'):
                shutil.rmtree(path) if path.is_dir() else path.unlink()


    def pause_history(self, feed: str, paused: bool) -> dict[str, Any]:
        self.subscriptions.check_owner('feed', feed)
        with self.db:
            return self.subscriptions.history.pause(feed, paused)

    def change_history(self, feed: str, selection: InitialHistory | None) -> dict[str, Any]:
        self.subscriptions.check_owner('feed', feed)
        configured = next(f for f in self.subscriptions.feeds() if f['id'] == feed)
        if configured['transport'] not in ('rss', 'youtube') and not self.publications.feed(feed):
            raise ValueError('Догрузка доступна подключённому RSS/Atom или YouTube-фиду')
        if self.publications.feed(feed) and selection and selection.mode != 'count':
            raise ValueError('Догрузка YouTube поддерживает последние N видео')
        with self.db:
            return self.subscriptions.history.start(feed, selection) if selection else self.subscriptions.history.cancel(feed)


    def cursor(self, feed: str = 'demo') -> str | None:
        row=self.db.execute('SELECT cursor FROM feeds WHERE id=?',(feed,)).fetchone()
        return str(row[0]) or None if row else None

    def collect_discovery(self,publications: list[Publication],cursor: str,feed: str = 'demo',namespace: str = '',*,description: str | None = None,successful: bool = True,source_order: tuple[ExternalKey,...] = ()) -> CollectionEffects:
        effects=CollectionEffects()
        plans=self.publications.plan(feed,publications)
        with self.db:
            history_items=[]
            envelope=HistoryEnvelope.decode(cursor,adapter='client' if namespace=='telegram' else 'web') if namespace in ('telegram','telegram-web') else None
            selection=json.loads(cursor) if not envelope and self.subscriptions.history.status(feed) else {}
            enrolled=set(envelope.selection.enrolled_ids) if envelope else set(selection.get('history_enroll',[]))
            ordered=source_order
            known={ExternalKey(platform,row['external_id']):row['source_order'] for platform in {key.platform for key in ordered}
                for row in self.db.execute("SELECT external_id,source_order FROM items WHERE platform=? AND source_order IS NOT NULL AND external_id IN (SELECT value FROM json_each(?))",
                    (platform.value,json.dumps([key.external_id for key in ordered if key.platform==platform])))}
            anchor=next((index for index in reversed(range(len(ordered))) if ordered[index] in known),None)
            # Positive ranks leave room for later uploads and earlier history.
            base_order=known[ordered[anchor]]+anchor if anchor is not None else 1_000_000_000
            orders={key:base_order-index for index,key in enumerate(ordered)}
            for publication in publications:
                plan=plans.get(publication.id)
                if plan is None:
                    continue
                item=plan.item
                self.db.execute('INSERT OR IGNORE INTO items(id,source_id,url,published,feed_id,members,published_known) VALUES (?,?,?,?,?,?,?)',
                    (item,plan.source_id,publication.url,publication.published,feed,json.dumps(publication.members),publication.published_known))
                if publication.adapter_reference is None:
                    raise ValueError('Adapter did not return a publication external key')
                rank=orders.get(publication.adapter_reference.key)
                if rank is not None and not publication.published_known:
                    self.db.execute('UPDATE items SET source_order=? WHERE id=? AND source_order IS NOT ?',(rank,item,rank))
                self.publications.identify(item,publication.adapter_reference)
                self.db.execute('INSERT OR IGNORE INTO feed_publications VALUES (?,?)',(feed,item))
                if plan.content is not None:
                    assert plan.fingerprint is not None
                    self.publications.publish(item,plan.content,plan.fingerprint,effects)
                    self.db.execute('UPDATE items SET complete=1 WHERE id=?',(item,))
                row=expect_row(self.db.execute('SELECT * FROM items WHERE id=?',(item,)).fetchone())
                if publication.published_known and not row['published_known']:
                    self.db.execute('UPDATE items SET published=?,published_known=1,published_at=? WHERE id=?',(publication.published,publication.published,item))
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
                        self.db.execute('UPDATE items SET members=? WHERE id=?',(json.dumps(members),item))
                        effects.fetch[item]=True
                    effects.fetch.setdefault(item,False)
            if envelope or 'history_enroll' in selection:
                self.subscriptions.history.enroll(feed,history_items,envelope.selection.scan_complete if envelope else selection['scan_complete'])
                if envelope:
                    self.subscriptions.history.telegram_state(feed,envelope.selection)
                for item in history_items:
                    row=expect_row(self.db.execute('SELECT complete FROM items WHERE id=?',(item,)).fetchone())
                    if self.publications.source_deleted(item):
                        self.subscriptions.history.failed(item,unavailable=True)
                    elif row['complete']:
                        self.subscriptions.history.saved(item,bool(self.original(item)[0].get('full')))
            if selection:
                self.subscriptions.history.discovery_state(feed,selection)
            if envelope and namespace=='telegram':
                self.read_sync.observed(feed,tuple(member for publication in publications for member in publication.members),cursor)
            if successful:
                self.subscriptions.details.record(feed,'updated')
            self.subscriptions.details.description(feed,description)
            self.fault('before_discovery_commit')
            self.db.execute('UPDATE feeds SET cursor=?,description=COALESCE(?,description) WHERE id=?',(cursor,description,feed))

        return effects

    def source_http_until(self, scope: str) -> float:
        row=self.db.execute('SELECT value FROM state WHERE key=?',('source-http:'+scope,)).fetchone()
        return float(row[0]) if row else 0.

    def source_http_wait(self, scope: str, until: float) -> None:
        with self.db:
            self.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            ('source-http:'+scope,str(until)))

    def telegram_http_state(self, group: str) -> dict[str, Any]:
        row = self.db.execute('SELECT value FROM state WHERE key=?', ('telegram-http:'+group,)).fetchone()
        return json.loads(row[0]) if row else {}

    def telegram_http_wait(self, group: str, state: dict[str, Any]) -> None:
        with self.db:
            self.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            ('telegram-http:'+group, json.dumps(state)))

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
        return self.db.execute('SELECT 1 FROM items WHERE id=? AND viewed=0',(item,)).fetchone() is not None


    @reading_value
    def item_feed(self, item: str) -> str:
        row = material_read.item(self.db,item)
        if row is None:
            owner = self.media.owner(item)
            row = material_read.item(self.db,owner) if owner else None
        if row is None:
            raise LookupError('Publication not found')
        if row['feed_id'] is None:
            root=self.db.execute('''SELECT i.feed_id FROM publication_links l JOIN items i ON i.id=l.source_publication_id
                WHERE l.target_publication_id=? AND i.viewed=0 AND i.feed_id IS NOT NULL ORDER BY i.id LIMIT 1''',(item,)).fetchone()
            if root:
                return str(root[0])
            return ''
        return str(row['feed_id'])

    def feed_status(self, feed: str, **state: Any) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('feed-status:' + feed, json.dumps(state)))

    def source_statuses(self) -> list[dict[str, Any]]:
        return [dict(feed_id=row['key'].split(':', 1)[1], **json.loads(row['value'])) for row in self.db.execute("SELECT * FROM state WHERE key >= 'feed-status:' AND key < 'feed-status;'")]

    def ready_sources(self, feeds: list[str]) -> int:
        if not feeds:
            return 0
        return int(expect_row(self.db.execute("""SELECT count(*) FROM json_each(?) f WHERE
            NOT EXISTS (SELECT 1 FROM jobs j WHERE j.item=CASE f.value WHEN 'demo' THEN '' ELSE f.value END
                AND j.status IN ('pending','running') AND j.kind!='read')
            AND NOT EXISTS (SELECT 1 FROM items i JOIN jobs j ON j.item=i.id WHERE i.feed_id=f.value
                AND j.status IN ('pending','running') AND j.kind!='read')""",(json.dumps(feeds),)).fetchone())[0])


    def publication(self, item: str) -> Publication | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        if row:
            header, text = self.publications.original(item) if row['complete'] else ({},'')
            refs = tuple(MediaReference(m['resource_key'],m['source_locator'],m['name'],m['mime_type'],m['size_bytes'],m['resource_identity'])
                for m in self.db.execute('SELECT * FROM media_files WHERE publication_id=?',(item,)) if m['source_locator'])
            last_description = PublicationContent(PublicationMetadata(ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),row['url']),
                header.get('title',''),datetime.fromtimestamp(row['published'],timezone.utc) if row['published_known'] else None),media=refs,
                linked_publications=tuple(ExternalReference(ExternalKey(Platform(target['platform']),target['external_id']),target['url']) for target in self.db.execute('SELECT i.* FROM publication_links l JOIN items i ON i.id=l.target_publication_id WHERE l.source_publication_id=?',(item,))))
            teaser = Material(header.get('title',''),text,header.get('full',False),header.get('reason',''),
                members=tuple(json.loads(row['members'])),description=header.get('description',''),
                source_manifest=SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None,
                reply_preview=ReplyPreview.model_validate(header['reply_preview']) if header.get('reply_preview') else None,adapter_content=last_description) if row['complete'] else None
            return Publication(row['source_id'].split(':',1)[1],row['url'],row['published'],teaser,tuple(json.loads(row['members'])),bool(row['published_known']),adapter_reference=last_description.metadata.reference)
        return None

    def collect_material(self, item: str, material: Material, *, manual: bool = False) -> tuple[SourceError | None,CollectionEffects]:
        effects=CollectionEffects()
        if not self._has_unviewed(item):
            return None,effects
        previous = self.publication(item)
        if material.source_manifest and material.source_manifest.post_web_unavailable:
            media=[dict(row) for row in self.db.execute('SELECT * FROM media_files WHERE publication_id=?',(item,))]
            missing=[row for row in media if row['storage_key'] and not (self.root/row['storage_key']).is_file() or row['status']=='unavailable' and row['reason']=='Локальный файл вложения недоступен']
            if missing:
                error=SourceError('Локальный файл вложения недоступен',code='media-unavailable',retryable=False)
                content=json.loads(expect_row(self.db.execute('SELECT content FROM items WHERE id=?',(item,)).fetchone())[0])
                content['extra']['_reader']['source_manifest']['post_web_unavailable']=True
                for row in missing:
                    row.update(status='unavailable',reason=error.label,storage_key=None)
                self.media.project_header(content,media)
                with self.db:
                    for row in missing:
                        self.db.execute("UPDATE media_files SET status='unavailable',reason=?,storage_key=NULL WHERE id=?",(error.label,row['id']))
                    self.db.execute('UPDATE items SET content=? WHERE id=?',(json.dumps(content,ensure_ascii=False),item))
                    self.collection_history(item,replace(material,collection_error=error))
                    self.read_versions.media_changed(item)
                return error,effects
        if (material.source_manifest and material.source_manifest.post_web_unavailable
                and previous and previous.teaser and previous.teaser.source_manifest):
            retained = previous.teaser
            manifest = retained.source_manifest
            assert manifest is not None
            accept_preview = (material.source_manifest.preview_fragment is not None
                and (manifest.preview_fragment is not None or
                     manifest.post.get('text') == material.source_manifest.post.get('text')
                     and all(r.status == 'unsupported' and r.sha256 is None
                         and r.reason == 'Пост не поддерживается публичным предпросмотром' for r in manifest.resources)))
            if (retained.text.strip() or any(resource.status=='loaded' for resource in manifest.resources)) and not accept_preview:
                material = replace(retained, full=False, members=material.members,
                    source_manifest=manifest.model_copy(update={'post_web_unavailable':True}),
                    adapter_content=None, collection_error=material.collection_error)
        old_media={row['id']:dict(row) for row in self.db.execute('SELECT * FROM media_files WHERE publication_id=?',(item,))} if manual else {}
        prepared=self.publications.prepare(item,material)
        fingerprint=publication_input.digest(prepared[0])
        self.fault('after_file_move')
        with self.db:
            if not self._has_unviewed(item):
                return None,effects
            if material.adapter_content and material.adapter_content.metadata.reference.key.platform==Platform.telegram:
                self.publications.identify(item,material.adapter_content.metadata.reference)
            self.publications.publish(item,prepared,fingerprint,effects)
            self.db.execute('UPDATE items SET complete=1 WHERE id=?',(item,))
            if material.members:
                self.db.execute('UPDATE items SET members=? WHERE id=?',(json.dumps(material.members),item))
            for row in prepared[1]:
                old=old_media.get(row['id'])
                if row['status']=='ready' and old and not (row['resource_identity'] is not None and old['source_locator']!=row['source_locator']):
                    effects.revalidate[row['id']]=True
            self.collection_history(item,material)
        self.processing.sync()
        with self.db:
            self.publications.status(item)

        return material.collection_error,effects

    def collection_history(self, item: str, material: Material) -> None:
        if not material.collection_error or not self.subscriptions.history.member(item):
            self.subscriptions.history.saved(item,material.full)
        if not material.collection_error:
            self.db.execute('DELETE FROM source_deleted WHERE item=?',(item,))


    def folder_contents(self, folder: str) -> FolderContents:
        return self.reading.folder_contents(folder)

    def viewed(self, item: str) -> bool:
        if self.db.execute("SELECT 1 FROM jobs WHERE kind='delete' AND item=?",(item,)).fetchone():
            return True
        return self.viewed_set(self.reading.members(item))

    def viewed_set(self, members: list[str]) -> bool:
        members = sorted(set(members))
        affected = set(members)
        with self.db:
            if any(not self.db.execute('SELECT 1 FROM items WHERE id=?', (member,)).fetchone() for member in members):
                return False
            memberships={member:[r[0] for r in self.db.execute('SELECT m.feed_id FROM reading_memberships m JOIN items i ON i.id=m.publication_id WHERE m.publication_id=? AND i.viewed=0',(member,))] for member in members}
            changed_feeds = {feed for feeds in memberships.values() for feed in feeds}
            for feed in changed_feeds:
                self.subscriptions.details.record(feed, 'marked_read')
            self.problems.forget_materials(affected, self.wall_clock())
            for member in members:
                row = expect_row(self.db.execute('SELECT feed_id,members FROM items WHERE id=?', (member,)).fetchone())
                for feed in memberships[member]:
                    self.read_sync.eligible(feed,json.loads(row['members']),'viewed')
                self.subscriptions.history.viewed(member)
                self.db.execute('DELETE FROM feed_publications WHERE publication_id=?',(member,))
                publication_links.prune(self,{member})
                self.db.execute('UPDATE items SET feed_id=NULL WHERE id=? AND viewed=0',(member,))
        self.processing.sync()
        self.db.after_commit(lambda: self.fault('after_viewed_commit'))
        return True

    def remove_feed(self, feed: str) -> None:
        self.subscriptions.check_owner('feed', feed)
        with self.db:
            self.db.execute("UPDATE feeds SET connection='deleting' WHERE id=?", (feed,))
            shared=[r[0] for r in self.db.execute('SELECT id FROM items WHERE platform IS NOT NULL AND feed_id=?',(feed,))]
            self.db.execute('DELETE FROM feed_publications WHERE feed_id=?',(feed,))
            for item in shared:
                remaining=self.db.execute('SELECT feed_id FROM feed_publications WHERE publication_id=? ORDER BY feed_id LIMIT 1',(item,)).fetchone()
                self.db.execute('UPDATE items SET feed_id=? WHERE id=?',(remaining[0] if remaining else feed,item))
            publication_links.prune(self,set(shared))
            for item in shared:
                self.db.execute('UPDATE items SET feed_id=NULL WHERE id=? AND feed_id=? AND viewed=0',(item,feed))
            items = [r[0] for r in self.db.execute('SELECT id FROM items WHERE feed_id=?', (feed,))]
            self.problems.forget_materials(set(items), self.wall_clock())
            for item in items:
                self.filters.forget(item)
                self.db.execute('DELETE FROM source_deleted WHERE item=?',(item,))
                if self.publications.owns(item):
                    self.publications.forget(item)
                self.db.execute('DELETE FROM thesis_inputs WHERE item=?', (item,))
                # This is local cleanup, not viewed(): never create read eligibility.
                self.db.execute('UPDATE items SET viewed=1,complete=0 WHERE id=?', (item,))
        self.db.after_commit(lambda: self.fault('after_feed_delete_commit'))


    @reading_value
    def original(self, item: str) -> tuple[dict[str, Any], str]:
        own=material_read.item(self.db,item)
        header,text=self.publications.original(item)
        if own:
            if own and 'youtube' in json.loads(own['content']).get('extra',{}):
                projected=youtube_media.add_input(self,json.loads(own['content']))
                text='\n\n'.join(block['text'] for block in projected['blocks'] if block['type']=='text' and block.get('role')=='body')
                if projected['extra']['youtube'].get('captions')=='error' and header.get('captions')=='available':
                    header={**header,'captions':'error','full':False,'reason':'Локальный файл субтитров недоступен; повторите загрузку'}
            header=publication_links.project_header(self,item,header)
            article_text=publication_links.text(self,item)
            if article_text:
                text='\n\n'.join(part for part in (text,article_text) if part)
        return header,text

    def material(self, item: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        material = self.saved_publication(row) if row and row['feed_id'] is not None else None
        if material and material['complete']:
            display = material['text']
            filtered = material['filter']
            if filtered['status'] == 'done':
                for fragment in filtered['removed']:
                    display = display.replace(fragment, '')
            material['display_text'] = display
        return material

    def saved_publication(self, row: Row) -> dict[str, Any] | None:
        """Saved publication facts without reader-specific text projections."""
        item = row['id']
        settings = self.subscriptions.effective('feed', row['feed_id'])
        base = dict(id=item, feed_id=row['feed_id'], url=row['url'], published=row['published'],
                    published_known=bool(row['published_known']), source_order=row['source_order'],
                    members=json.loads(row['members']), complete=bool(row['complete']),
                    originals=settings['effective']['originals']['value'])
        if not row['complete']:
            base.update(title='Материал загружается', loading=bool(self.db.execute(
                "SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status IN ('pending','running')", (item,)).fetchone()))
            return dict(base, text='')
        try:
            header, text = self.original(item)
            filtered = self.filters.read(item)
            base.update(changed=bool(header.get('changed')), source_deleted=self.publications.source_deleted(item),
                filter=filtered, title=header['title'], theses=self.processing.theses.read(item),
                provenance=self.processing.theses.provenance(item), ai=self.processing.theses.status(item),
                version=self.content_version(item), loading=bool(self.db.execute(
                    "SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND (status='running' OR (status='pending' AND next<=?))",
                    (item, reader_now(self.clock()))).fetchone()),
                full=header.get('full', True) and (not header.get('members') or set(header['members']) == set(base['members'])),
                reason=header.get('reason', ''), captions=header.get('captions'),
                attachments=[dict(url=header.get('_media_urls',{}).get(a['name'],f"/api/v3/materials/{item}/media/{a.get('media_id',a['name'])}"), kind=a['kind'], mime=a['mime']) for a in header.get('attachments', [])],
                images=[])
            base.update(saved_external(header, f'/api/v3/materials/{item}'))
            base['loading'] = base['loading'] or row['platform'] is not None and self.media.loading(item)
            linked=saved_preview(header)
            if row['platform']=='telegram':
                linked=publication_links.reading(self,item,self.publications.original(item)[1]) or linked
            base.update(text=text, description=header.get('description', ''), reply_preview=header.get('reply_preview'), linked_content=linked)
            return base
        except (OSError, ValueError, KeyError):
            record_caught('store.material')
            return None

    def cards(self, show_hidden: bool = False) -> list[dict[str, Any]]:
        return self.reading.cards(show_hidden)

    def work_summary(self, *, include_problems: bool = False) -> list[WorkSummary]:
        condition = "jobs.status IN ('pending','running')"
        if include_problems:
            # Match each existing partial index separately; a combined OR scans
            # the completed archive on SQLite even when it has no problems.
            condition = """jobs.id IN (SELECT id FROM jobs WHERE status IN ('pending','running') OR error IS NOT NULL
                UNION SELECT id FROM jobs WHERE problem_code IS NOT NULL OR error IS NOT NULL)"""
        rows = self.db.execute(f"""SELECT jobs.kind,jobs.status,jobs.problem_code,count(*) AS count,
            CASE WHEN jobs.kind IN ('discover','read','feed-delete') THEN NULLIF(jobs.item,'')
                WHEN jobs.kind='download_media' THEN (SELECT i.feed_id FROM media_files m JOIN items i ON i.id=m.publication_id WHERE m.id=jobs.item)
                ELSE items.feed_id END AS feed,
            min(jobs.next) AS next,sum(CASE WHEN jobs.error IS NOT NULL THEN 1 ELSE 0 END) AS errors
            FROM jobs LEFT JOIN items ON items.id=jobs.item
            WHERE {condition}
            GROUP BY jobs.kind,jobs.status,jobs.problem_code,feed ORDER BY min(jobs.id)""").fetchall()
        now, wall = self.clock(), self.wall_clock()
        return [WorkSummary(row['kind'],row['status'],row['feed'],row['count'],
                    row['problem_code'] or (row['kind']+'-failed' if row['errors'] else None),
                    retry_time(row['next'],now,wall) if row['status']=='pending' and row['errors'] and row['next'] else None)
                for row in rows]

    def status(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT kind,status,error,count(*) AS count FROM jobs WHERE status IN ('pending','running') OR error IS NOT NULL GROUP BY kind,status,error").fetchall()
        # The durable feed sweep is deletion work, not a new public job kind.
        jobs = [dict(r, kind='delete' if r['kind'] == 'feed-delete' else r['kind']) for r in rows]
        errors = sorted({r['error'] for r in rows if r['error']})
        pending_deletes = any(r['kind'] in ('delete', 'feed-delete') and r['status'] != 'done' for r in rows)
        return dict(jobs=jobs, errors=errors, deleting=pending_deletes)


    def work_enabled(self) -> bool:
        row = self.db.execute("SELECT value FROM state WHERE key='work-enabled'").fetchone()
        return row is None or row['value'] != 'false'

    def set_work_enabled(self, enabled: bool) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES ('work-enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('true' if enabled else 'false',))

    def codex_config(self) -> CodexSettings | None:
        value = self.subscriptions.settings.state_value('processor:codex')
        return CodexSettings.model_validate_json(value) if value else None

    def configure_codex(self, config: CodexSettings) -> None:
        # Future execution choice does not invalidate inputs or cancel work.
        with self.db:
            self.subscriptions.settings.set_state('processor:codex', config.model_dump_json())

    @reading_value
    def processor_config(self) -> ProcessorConfig | None:
        value = self.subscriptions.settings.state_value('processor:llamacpp')
        return ProcessorConfig.model_validate_json(value) if value else None

    def configure_processor(self, config: ProcessorConfig) -> bool:
        if self.processor_config() == config:
            return False
        with self.db:
            self.subscriptions.settings.set_state('processor:llamacpp', config.model_dump_json())
        self.settings_changed()
        return True

    @reading_value
    def settings_version(self, feed: str | None = None) -> int:
        keys = ['settings-version'] + (['settings-version:' + feed] if feed else [])
        return max((int(value) for key in keys if (value := material_read.state(self.db,key)) is not None), default=0)

    def settings_changed(self, scope: str = 'oko', owner: str = 'global') -> None:
        with self.db:
            row = self.db.execute("SELECT value FROM state WHERE key='settings-sequence'").fetchone()
            sequence = max(int(row[0]) if row else 0, self.settings_version()) + 1
            self.db.execute("INSERT INTO state VALUES ('settings-sequence',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(sequence),))
            if scope == 'oko':
                keys = ['settings-version']
            else:
                query = 'SELECT id FROM feeds WHERE ' + ('id=?' if scope == 'feed' else 'folder_id=?')
                keys = ['settings-version:' + row[0] for row in self.db.execute(query, (owner,))]
            self.db.executemany('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', [(key, str(sequence)) for key in keys])
            self.processing.invalidate(scope, owner)
        self.processing.sync()

    @reading_value
    def content_version(self, item: str) -> str:
        def input_value(row: Any) -> list[Any]:
            content=json.loads(row['content'])
            youtube=content.get('extra',{}).get('youtube',{})
            return [row['title'],publication_input.blocks(content),json.loads(row['media_ids']),youtube.get('captions_digest')]
        own=material_read.item(self.db,item)
        return publication_input.digest([input_value(own) if own else None,
            [(row['id'],input_value(row)) for row in publication_links.children(self,item) if row['complete']]])


    def mark_source_removed(self, item: str) -> None:
        self.db.execute('INSERT OR IGNORE INTO source_deleted VALUES (?)',(item,))
        publication_links.notify_parents(self,item)
