from oko.collection.external_media import saved_external, expected_incomplete, telegraph_partial
from oko.collection.adapters import AdapterIssue, AdapterErrorKind, ExternalKey, ExternalReference, Platform, MediaReference, PublicationContent, PublicationMetadata
from datetime import datetime, timezone
from oko.collection.telegram.reading import saved_telegraph
from oko.storage.collection_status import CollectionStatus
from oko.storage import material_read, collection_jobs, publication_links, youtube_media
from oko.storage.reading import reading_value, saved_document, retry_time, reader_now
from oko.diagnostics import record_caught
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import secrets
import sqlite3
import time
from collections.abc import Callable
from typing import Any, Literal

from oko.storage.aggregate_upgrade import remove_aggregates
from oko.storage.versions import Versions
from oko.storage.publications import Publications
from oko.storage.media import MediaFiles
from oko.storage.reader_versions import ReaderVersions, ReadingConnection
from oko.storage.problems import Problems
from oko.storage.youtube import YouTubeAccess
from oko.reader.composition import FolderContents, ReadingComposition
from oko.collection.telegram.read_sync import ReadSync
from oko.collection.telegram.contracts import HistoryEnvelope
from oko.reviews.filtering import Filters
from oko.reviews.coordination import ProcessingCoordination
from oko.collection.contracts import ReplyPreview, SourceError, SourceDeleted, Attachment, Image, Material, Publication, DownloadStage, StagedPayload, SourceManifest
from oko.subscriptions.subscriptions import Subscriptions
from oko.subscriptions.history import InitialHistory
from oko.processing.llamacpp import ProcessorConfig
from oko.processing.execution import CodexSettings


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    item: str
    generation: int


@dataclass(frozen=True)
class WorkSummary:
    kind: str
    status: str
    feed: str | None
    count: int
    error_code: str | None
    retry_at: float | None


class Store:
    """SQLite owns lifecycle and migrated publications; files hold local media."""
    def change_telegram_access(self, feed: str, binding: dict[str,Any], info: dict[str,Any],
                               auto_read: bool | None) -> dict[str,Any]:
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            result=self.subscriptions.telegram.change_access(feed,binding,info,auto_read,self.subscriptions)
            self.publications.configure_feed(feed)
            self.publications.changed_adapter(feed)
        return result

    def __init__(self, root: Path, fault: Callable[[str], None], *, wall_clock: Callable[[], float] = time.time, clock: Callable[[], float] = time.monotonic) -> None:
        self.root, self.fault = root, fault
        self.wall_clock = wall_clock
        self.clock = clock
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = ReadingConnection(root / 'state.sqlite')
        self.db.row_factory = sqlite3.Row
        ReaderVersions.functions(self.db,self.clock,self.wall_clock)
        # Reject unsupported topic migration before any PRAGMA/schema/data write.
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feeds'").fetchone():
            columns = {r['name'] for r in self.db.execute('PRAGMA table_info(feeds)')}
            if 'telegram_topic' in columns and self.db.execute('SELECT 1 FROM feeds WHERE telegram_topic IS NOT NULL').fetchone():
                self.db.close()
                raise sqlite3.DatabaseError('Telegram: требуется ручная миграция фидов отдельных тем; данные не изменены')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('PRAGMA journal_mode=DELETE')
        if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise sqlite3.DatabaseError('integrity')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS items (
          id TEXT PRIMARY KEY, source_id TEXT UNIQUE, url TEXT,
          published REAL, viewed INTEGER DEFAULT 0, complete INTEGER DEFAULT 0,
          feed_id TEXT DEFAULT 'demo', UNIQUE(feed_id,url));
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, kind TEXT, item TEXT DEFAULT '',
          status TEXT DEFAULT 'pending', attempt INTEGER DEFAULT 0,
          next REAL DEFAULT 0, generation INTEGER DEFAULT 0, error TEXT,
          UNIQUE(kind,item));
        CREATE TABLE IF NOT EXISTS execution_settings (
          job INTEGER PRIMARY KEY, generation INTEGER, signature TEXT, parameters TEXT);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS thesis_inputs (item TEXT PRIMARY KEY, signature TEXT);
        ''')
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(items)')}
        if 'manual_once' not in {row['name'] for row in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN manual_once INTEGER DEFAULT 0')
            self.db.commit()
        if 'problem_code' not in {row['name'] for row in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN problem_code TEXT')
            self.db.commit()
        if 'members' not in columns:
            self.db.execute("ALTER TABLE items ADD COLUMN members TEXT DEFAULT '[]'")
            self.db.commit()
        if 'feed_id' not in columns:
            self.db.execute("ALTER TABLE items ADD COLUMN feed_id TEXT DEFAULT 'demo'")
            self.db.commit()
        global_url_index = any(
            index['unique'] and [column['name'] for column in self.db.execute(
                'SELECT name FROM pragma_index_info(?)', (index['name'],))] == ['url']
            for index in self.db.execute('PRAGMA index_list(items)').fetchall())
        if global_url_index:
            # Rebuild in one transaction. IDs, tombstones, paths and jobs retain
            # their identities; a crash exposes either the old or new schema.
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('''CREATE TABLE items_scoped (
                  id TEXT PRIMARY KEY, source_id TEXT UNIQUE, url TEXT,
                  published REAL, viewed INTEGER DEFAULT 0, complete INTEGER DEFAULT 0,
                  feed_id TEXT DEFAULT 'demo', members TEXT DEFAULT '[]', UNIQUE(feed_id,url))''')
                self.db.execute('INSERT INTO items_scoped SELECT id,source_id,url,published,viewed,complete,feed_id,members FROM items')
                self.db.execute('DROP TABLE items')
                self.db.execute('ALTER TABLE items_scoped RENAME TO items')
        if 'published_known' not in {row['name'] for row in self.db.execute('PRAGMA table_info(items)')}:
            self.db.execute('ALTER TABLE items ADD COLUMN published_known INTEGER DEFAULT 1')
            self.db.commit()
        if 'source_order' not in {row['name'] for row in self.db.execute('PRAGMA table_info(items)')}:
            self.db.execute('ALTER TABLE items ADD COLUMN source_order INTEGER')
            self.db.commit()
        self.db.execute('CREATE INDEX IF NOT EXISTS items_unviewed_order ON items(published DESC,id,feed_id) WHERE viewed=0')
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_item ON jobs(item)')
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_active ON jobs(status,kind,item) WHERE status IN ('pending','running') OR error IS NOT NULL")
        self.db.execute('CREATE INDEX IF NOT EXISTS items_unviewed_feed ON items(feed_id,id) WHERE viewed=0')
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_pending_next ON jobs(next,kind,item) WHERE status='pending'")
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_problems ON jobs(kind,item) WHERE problem_code IS NOT NULL OR error IS NOT NULL")
        self.subscriptions = Subscriptions(self.db, self.fault, wall_clock=self.wall_clock)
        self.publications = Publications(self)
        self.media = MediaFiles(self)
        self.collection_status = CollectionStatus(self.db, self.clock, self.wall_clock)
        self.filters = Filters(self)
        self.processing = ProcessingCoordination(self)
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
        self.versions = Versions(self)
        self.publications.recover()
        self.versions.recover()
        remove_aggregates(self)
        self.recover_files()
        if self.db.execute("SELECT 1 FROM items WHERE feed_id='demo' LIMIT 1").fetchone():
            self.subscriptions.ensure_demo()
        self.read_versions = ReaderVersions(self)
        self.subscriptions.watch_configuration()
        self.processing.recover()
        self.read_sync.recover()

    def recover_files(self) -> None:
        # Never promote orphaned output: the pending fetch must produce a fresh result.
        for p in (self.root / 'staging').iterdir():
            row = self.db.execute('SELECT viewed FROM items WHERE id=?', (p.name,)).fetchone()
            if row and row['viewed']:
                continue  # The durable deletion job owns retries and error reporting.
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        for p in (self.root / 'content').iterdir():
            row = self.db.execute('SELECT * FROM items WHERE id=?', (p.name,)).fetchone()
            if row and row['viewed']:
                continue
            if row is None or not row['complete']:
                shutil.rmtree(p)
        with self.db:
            for row in self.db.execute('SELECT * FROM items WHERE complete=1 AND viewed=0').fetchall():
                if not self.path(row['id']).is_file():
                    self.db.execute('UPDATE items SET complete=0 WHERE id=?', (row['id'],))
                    self.db.execute("UPDATE jobs SET status='pending' WHERE kind='fetch' AND item=?", (row['id'],))

    def path(self, item: str) -> Path:
        return self.versions.path(item)

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

    def enqueue_discovery(self, feed: str = 'demo', next_at: float = 0) -> None:
        with self.db:
            self.db.execute("INSERT INTO jobs(kind,item,next) VALUES ('discover',?,?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=excluded.next,error=NULL WHERE jobs.status='done'", ('' if feed == 'demo' else feed, next_at))

    def refresh_feed(self, feed: str) -> None:
        """Admit one manual refresh without restarting work already in progress."""
        with self.db:
            for row in self.db.execute('''SELECT DISTINCT i.id FROM items i JOIN media_files m ON m.publication_id=i.id
                WHERE i.feed_id=? AND i.viewed=0''', (feed,)).fetchall():
                self.media.retry(row['id'])
        discovery = '' if feed == 'demo' else feed
        active = self.db.execute("""SELECT 1 FROM jobs WHERE kind='discover' AND item=?
            AND (status='running' OR (status='pending' AND error IS NULL))""", (discovery,)).fetchone()
        if active:
            return
        with self.db:
            history = self.subscriptions.history.status(feed)
            if history and not history.get('cancelled'):
                self.subscriptions.history.pause(feed, False)
            self.enqueue_discovery(feed)
            self.db.execute("""UPDATE jobs SET status='pending',next=CASE
                WHEN problem_code LIKE '%FloodWaitError' OR problem_code='youtube-admission' THEN next ELSE 0 END,
                generation=generation+1 WHERE kind='discover' AND item=? AND status!='running'""", (discovery,))
            for row in self.db.execute("""SELECT i.id,i.members,j.error,j.status FROM items i JOIN jobs j ON j.item=i.id AND j.kind='fetch'
                    WHERE i.feed_id=? AND i.viewed=0 AND j.status!='running'""", (feed,)).fetchall():
                material = self.material(row['id'])
                unavailable = self.db.execute('SELECT 1 FROM source_deleted WHERE item=?', (row['id'],)).fetchone()
                if material and not row['error'] and not unavailable and not self._needs_collection(row['id']):
                    continue
                if row['members'] != '[]' and material and material['full']:
                    continue
                if row['error'] or unavailable or not material or not material['full']:
                    self.youtube.reset(row['id'])
                    self.db.execute("""UPDATE jobs SET manual_once=CASE WHEN (status='done' AND error IS NOT NULL)
                        OR item IN (SELECT item FROM source_deleted) THEN 1 ELSE manual_once END, status='pending',next=CASE
                        WHEN problem_code LIKE '%FloodWaitError' OR problem_code='youtube-admission' THEN next ELSE 0 END,
                        generation=generation+1 WHERE kind='fetch' AND item=?""", (row['id'],))
            self.processing.retry_failed(feed)

    def cursor_key(self, feed: str, namespace: str) -> str:
        return 'cursor:' + namespace + ':' + feed if namespace else 'cursor' if feed == 'demo' else 'cursor:' + feed

    def cursor(self, feed: str = 'demo', namespace: str = '') -> str | None:
        if self.publications.feed(feed):
            return str(self.db.execute('SELECT cursor FROM feeds WHERE id=?', (feed,)).fetchone()[0]) or None
        row = self.db.execute("SELECT value FROM state WHERE key=?", (self.cursor_key(feed, namespace),)).fetchone()
        return row[0] if row else None

    def discovered(self, publications: list[Publication], cursor: str, job: Job, feed: str = 'demo', namespace: str = '', *, description: str | None = None, successful: bool = True) -> None:
        if not self.valid(job):
            return
        target = self.publications.feed(feed)
        plans = self.publications.plan(feed, publications) if target else {}
        with self.db:
            history_items = []
            envelope = HistoryEnvelope.decode(cursor, adapter='client' if namespace == 'telegram' else 'web') if namespace in ('telegram', 'telegram-web') else None
            selection = json.loads(cursor) if not envelope and self.subscriptions.history.status(feed) else {}
            history_ids = set(envelope.selection.enrolled_ids) if envelope else set(selection.get('history_ids', selection.get('history_enroll', [])))
            for p in publications:
                item = hashlib.sha256((feed + ':' + p.id).encode()).hexdigest()[:32]
                if not target and p.observed_only and not self.db.execute('SELECT 1 FROM items WHERE id=?', (item,)).fetchone():
                    continue
                source_id = p.id if feed == 'demo' else feed + ':' + p.id
                existing = self.db.execute('SELECT id,source_id,url FROM items WHERE source_id=?', (source_id,)).fetchone()
                if target:
                    plan = plans.get(p.id)
                    if plan is None:
                        continue
                    item, source_id = plan.item, plan.source_id
                    existing = self.db.execute('SELECT id,source_id,url FROM items WHERE id=?', (item,)).fetchone()
                if envelope and p.members and not existing:
                    placeholders = ','.join('?' for _ in p.members)
                    matches = self.db.execute(f"SELECT DISTINCT i.id,i.source_id,i.url FROM items i JOIN json_each(i.members) m WHERE i.feed_id=? AND m.value IN ({placeholders})", (feed,*p.members)).fetchall()
                    if len(matches)>1:
                        raise ValueError('Conflicting Telegram album membership')
                    if matches:
                        existing = matches[0]
                        item,source_id = existing['id'],existing['source_id']
                if existing and existing['url'] != p.url:
                    if envelope or target:
                        self.db.execute('UPDATE items SET url=? WHERE id=?', (p.url,existing['id']))
                    elif not (p.members and p.id.startswith('album:')):
                        source_id = feed + ':' + p.url
                        item = hashlib.sha256(source_id.encode()).hexdigest()[:32]
                inserted = self.db.execute('INSERT OR IGNORE INTO items(id,source_id,url,published,feed_id,members,published_known) VALUES (?,?,?,?,?,?,?)',
                                (item, source_id, p.url, p.published, feed, json.dumps(p.members), p.published_known))
                if target:
                    if p.adapter_reference is None:
                        raise ValueError('Adapter did not return a publication external key')
                    self.publications.identify(item, p.adapter_reference)
                    self.db.execute('INSERT OR IGNORE INTO feed_publications VALUES (?,?)', (feed, item))
                if p.teaser and (inserted.rowcount or target and plans.get(p.id) and plans[p.id].content is not None):
                    planned = plans.get(p.id)
                    prepared = planned.version if planned else None
                    prepared = prepared or self.versions.prepare(item, p.teaser)
                    self.versions.publish(item, prepared, False)
                    if target:
                        assert planned and planned.content
                        canonical = planned.content
                        self.publications.publish(item, canonical, prepared[1])
                    self.db.execute('UPDATE items SET complete=1 WHERE id=?', (item,))
                row = self.db.execute('SELECT * FROM items WHERE source_id=?', (source_id,)).fetchone()
                if row and p.published_known and not row['published_known']:
                    self.db.execute('UPDATE items SET published=?,published_known=1 WHERE id=?', (p.published, row['id']))
                    if target:
                        self.db.execute('UPDATE items SET published_at=? WHERE id=?', (p.published, row['id']))
                if row and p.id in history_ids:
                    history_items.append(row['id'])
                if row and row['viewed'] and namespace=='telegram-web':
                    self.db.execute('UPDATE items SET members=? WHERE id=?', (json.dumps(sorted(set(json.loads(row['members'])) | set(p.members))),row['id']))
                needs_fetch = (p.teaser is None or p.teaser.adapter_content is None or p.teaser.adapter_content.needs_fetch) if target else not envelope or bool(row and self._needs_collection(row['id'],p.teaser))
                if target and row and p.members and set(p.members)!=set(json.loads(row['members'])):
                    needs_fetch=True
                if target and p.observed_only:
                    needs_fetch = bool(row and self._needs_collection(row['id'],p.teaser))
                if row and not row['viewed'] and needs_fetch and not self.db.execute("SELECT 1 FROM source_deleted WHERE item=?", (row['id'],)).fetchone():
                    members = sorted(set(json.loads(row['members'])) | set(p.members))
                    if members != json.loads(row['members']):
                        self.db.execute('UPDATE items SET members=? WHERE id=?', (json.dumps(members), row['id']))
                        self.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='fetch' AND item=?", (row['id'],))
                    self.db.execute("INSERT INTO jobs(kind,item) VALUES ('fetch',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,error=NULL,generation=generation+1 WHERE jobs.status='done' AND (jobs.error IS NULL OR jobs.problem_code IS NULL)", (row['id'],))
            if 'youtube_history' in selection:
                # A new prefix can overlap an older, longer selection. Reorder
                # retained history after that prefix instead of keeping colliding ranks.
                positions = {feed + ':' + entry['id']: rank for rank, entry in enumerate(selection['youtube_history']['entries'])}
                rows = self.db.execute('SELECT id,source_id,source_order FROM items WHERE feed_id=? ORDER BY source_order,id', (feed,)).fetchall()
                prefix = sorted((row for row in rows if row['source_id'] in positions), key=lambda row: positions[row['source_id']])
                retained = [row for row in rows if row['source_order'] is not None and row['source_id'] not in positions]
                self.db.executemany('UPDATE items SET source_order=? WHERE id=?',
                                    [(rank, row['id']) for rank, row in enumerate(prefix + retained) if row['source_order'] != rank])
            if 'history_ids' in selection:
                self.subscriptions.history.freeze(feed, history_items)
                if 'warnings' in selection:
                    self.subscriptions.history.warnings(feed, selection['warnings'])
            if envelope and self.subscriptions.history.status(feed):
                self.subscriptions.history.enroll(feed, history_items, envelope.selection.scan_complete)
                self.subscriptions.history.telegram_state(feed, envelope.selection)
            if 'history_enroll' in selection or envelope:
                if not envelope:
                    self.subscriptions.history.enroll(feed, history_items, selection['scan_complete'])
                for item in history_items:
                    row = self.db.execute('SELECT viewed,complete FROM items WHERE id=?', (item,)).fetchone()
                    if row['viewed']:
                        self.subscriptions.history.viewed(item)
                    elif self.db.execute('SELECT 1 FROM source_deleted WHERE item=?', (item,)).fetchone():
                        self.subscriptions.history.failed(item, unavailable=True)
                    elif self.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status='done'", (item,)).fetchone():
                        material = self.material(item)
                        if material:
                            self.subscriptions.history.saved(item, material.get('full', False))
                    elif target and row['complete']:
                        self.subscriptions.history.saved(item, bool(self.original(item)[0].get('full')))
            if selection:
                self.subscriptions.history.discovery_state(feed, selection)
            if envelope:
                candidates = self.db.execute("SELECT item FROM jobs WHERE kind='fetch' AND status='done' AND (error IS NULL OR problem_code IS NULL) AND item IN (SELECT id FROM items WHERE feed_id=? AND viewed=0 AND members!='[]' AND id NOT IN (SELECT item FROM source_deleted))", (feed,)).fetchall()
                for candidate in ([] if target else candidates):
                    if self._needs_collection(candidate['item']):
                        self.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='fetch' AND item=?", (candidate['item'],))
                if namespace == 'telegram':
                    self.read_sync.observed(feed, tuple(m for p in publications for m in p.members), cursor)
            if successful:
                self.subscriptions.details.record(feed, 'updated')
            self.subscriptions.details.description(feed, description)
            self.fault('before_discovery_commit')
            if target:
                self.db.execute('UPDATE feeds SET cursor=?,description=COALESCE(?,description) WHERE id=?', (cursor, description, feed))
            else:
                self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.cursor_key(feed, namespace), cursor))
            self.finish(job)

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
        canonical = self.publications.owns(item)
        header, _ = self.original(item) if material and not material['full'] else ({}, '')
        linked_partial = expected_incomplete(SourceManifest.model_validate_json(json.dumps(header['source_manifest']))) if header.get('source_manifest') else False
        if not canonical and not material['full'] and not (linked_partial or material.get('external_only') or material.get('post_web_unavailable') and not material.get('reason')):
            return True
        if material['full']:
            return bool(observed and observed.source_manifest and observed.source_manifest.post_web_unavailable and not material.get('post_web_unavailable'))
        if not canonical and not self.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status='done' AND error IS NULL", (item,)).fetchone():
            return True
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

    def source_removed(self, job: Job) -> None:
        if not self.valid(job) or not self._has_unviewed(job.item):
            return
        with self.db:
            self._record_fetch_removal(job, 'Публикация удалена или недоступна в источнике')
        self.processing.sync()

    def retry_material(self, item: str) -> bool:
        if not self._has_unviewed(item):
            return False
        if self.publications.active:
            with self.db:
                publication_links.retry(self,item)
        with self.db:
            self.youtube.reset(item)
            self.media.retry(item)
            self.enqueue_fetch(item)
        return True

    def enqueue_fetch(self, item: str) -> None:
        self.db.execute("""INSERT INTO jobs(kind,item) VALUES ('fetch',?) ON CONFLICT(kind,item) DO UPDATE SET
            status='pending',manual_once=0,next=CASE WHEN jobs.problem_code LIKE '%FloodWaitError'
                OR (jobs.wait_scope IS NOT NULL AND jobs.retry_at>?) THEN jobs.next ELSE 0 END,
            error=CASE WHEN jobs.wait_scope IS NOT NULL AND jobs.retry_at>? THEN jobs.error ELSE NULL END,
            retry_at=CASE WHEN jobs.problem_code LIKE '%FloodWaitError' OR (jobs.wait_scope IS NOT NULL AND jobs.retry_at>?) THEN jobs.retry_at ELSE NULL END,
            generation=jobs.generation+1""", (item,self.wall_clock(),self.wall_clock(),self.wall_clock()))

    @reading_value
    def item_feed(self, item: str) -> str:
        row = self.db.execute('SELECT feed_id FROM items WHERE id=?', (item,)).fetchone()
        if row is None:
            owner = self.media.owner(item)
            row = self.db.execute('SELECT feed_id FROM items WHERE id=?', (owner,)).fetchone()
        if row is None:
            raise LookupError('Publication not found')
        if row[0] is None:
            root=self.db.execute('''SELECT i.feed_id FROM publication_links l JOIN items i ON i.id=l.source_publication_id
                WHERE l.target_publication_id=? AND i.viewed=0 AND i.feed_id IS NOT NULL ORDER BY i.id LIMIT 1''',(item,)).fetchone()
            if root:
                return str(root[0])
            return ''
        return str(row[0])

    def feed_status(self, feed: str, **state: Any) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('feed-status:' + feed, json.dumps(state)))

    def source_statuses(self) -> list[dict[str, Any]]:
        return [dict(feed_id=row['key'].split(':', 1)[1], **json.loads(row['value'])) for row in self.db.execute("SELECT * FROM state WHERE key LIKE 'feed-status:%'")]

    def ready_sources(self, feeds: list[str]) -> int:
        return sum(1 for feed in feeds if not self.db.execute(
            "SELECT 1 FROM jobs WHERE status IN ('pending','running') AND kind!='read' AND (item=? OR item IN (SELECT id FROM items WHERE feed_id=?))",
            ('' if feed == 'demo' else feed, feed)).fetchone())

    def next_job(self, now: float, deleting_only: bool = False, source_feed: str = 'demo', feeds: list[str] | None = None, ai_only: bool = False, include_deletions: bool = True, media_only: bool = False, captions_only: bool = False, dependency_feeds: list[str] | None = None) -> Job | None:
        query = "SELECT * FROM jobs WHERE status='pending' AND next<=? AND NOT (coalesce(problem_code,'')='ai-permission' AND error IS NOT NULL)"
        if ai_only:
            query += """ AND kind IN ('filter','theses') AND NOT EXISTS (SELECT 1 FROM items i WHERE i.id=jobs.item AND i.platform IS NOT NULL
                AND i.status!=CASE jobs.kind WHEN 'filter' THEN 'pending_filtering' ELSE 'pending_summary' END)"""
        elif deleting_only:
            query += " AND kind IN ('delete','feed-delete')"
        elif media_only:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('?' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('?' for _ in dependencies) or "''"
            query += f" AND kind='download_media' AND item IN (SELECT m.id FROM media_files m JOIN items i ON i.id=m.publication_id WHERE i.viewed=0 AND (i.feed_id IN ({placeholders}) OR i.feed_id IS NULL AND EXISTS (SELECT 1 FROM publication_links l JOIN items root ON root.id=l.source_publication_id WHERE l.target_publication_id=i.id AND root.viewed=0 AND root.feed_id IN ({dependency_keys}))))"
        else:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('?' for _ in available) or "''"
            dependencies=available if dependency_feeds is None else dependency_feeds
            dependency_keys=','.join('?' for _ in dependencies) or "''"
            query += f" AND (kind IN ('delete','feed-delete') OR (kind IN ('discover','read') AND item IN ({placeholders})) OR (kind='fetch' AND item IN (SELECT id FROM items WHERE feed_id IN ({placeholders}) OR feed_id IS NULL AND EXISTS (SELECT 1 FROM publication_links l JOIN items root ON root.id=l.source_publication_id WHERE l.target_publication_id=items.id AND root.viewed=0 AND root.feed_id IN ({dependency_keys})))))"
        if media_only:
            subtitles="""SELECT m.id FROM media_files m JOIN items i ON i.id=m.publication_id
                JOIN json_each(i.content,'$.blocks') b ON json_extract(b.value,'$.media_id')=m.id
                WHERE json_extract(b.value,'$.role')='subtitles'"""
            query += ' AND item '+('IN' if captions_only else 'NOT IN')+' ('+subtitles+')'
        if not include_deletions:
            query += " AND kind NOT IN ('delete','feed-delete')"
        blocked_history = """SELECT m.item FROM history_members m JOIN initial_history h ON h.feed=m.feed
            WHERE (h.paused=1 OR (h.scan_complete=0 AND (json_extract(h.selection,'$.mode')='count'
                OR h.feed IN (SELECT id FROM feeds WHERE transport='rss'))))"""
        # The legacy schema permits NULL IDs: retain IN's three-valued semantics
        # for those rows. Ordinary publications use an indexed membership lookup.
        query += f""" AND NOT (kind='fetch' AND CASE
            WHEN jobs.item IS NULL OR EXISTS (SELECT 1 FROM history_members WHERE item IS NULL)
                THEN jobs.item IN ({blocked_history})
            ELSE EXISTS ({blocked_history} AND m.item=jobs.item) END)"""
        query += " AND NOT (kind='feed-delete' AND EXISTS (SELECT 1 FROM jobs d WHERE d.kind='delete' AND d.status!='done' AND d.item IN (SELECT id FROM items WHERE feed_id=jobs.item)))"
        query += " ORDER BY CASE kind WHEN 'delete' THEN 0 WHEN 'feed-delete' THEN 0 WHEN 'discover' THEN 1 WHEN 'filter' THEN 2 WHEN 'read' THEN 2 ELSE 3 END,id LIMIT 1"
        params = (now,) if deleting_only or ai_only else (now, *available, *dependencies) if media_only else (now, *('' if f == 'demo' else f for f in available), *available, *dependencies)
        row = self.db.execute(query, params).fetchone()
        if not row:
            return None
        with self.db:
            # SQLite may reuse an INTEGER PRIMARY KEY after feed cleanup. Keep
            # dispatch generations monotonic even when every old job was removed.
            sequence = self.db.execute("SELECT value FROM state WHERE key='job-dispatch-generation'").fetchone()
            generation = max(row['generation'], int(sequence[0]) if sequence else 0) + 1
            self.db.execute("INSERT INTO state VALUES ('job-dispatch-generation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(generation),))
            self.db.execute('UPDATE execution_settings SET generation=? WHERE job=? AND generation=?', (generation, row['id'], row['generation']))
            self.db.execute("UPDATE jobs SET status='running',attempt=attempt+1,generation=? WHERE id=?", (generation, row['id']))
        current = self.db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()
        return Job(current['id'], current['kind'], current['item'], current['generation'])

    def finish(self, job: Job) -> None:
        self.db.execute("UPDATE jobs SET status='done',error=NULL,problem_code=NULL,manual_once=0,retry_at=NULL,retry_delay=0,wait_scope=NULL WHERE id=? AND generation=? AND status='running'", (job.id, job.generation))

    def valid(self, job: Job) -> bool:
        row = self.db.execute('SELECT status,generation,kind,item FROM jobs WHERE id=?', (job.id,)).fetchone()
        return bool(row and row['status'] == 'running' and row['generation'] == job.generation and row['kind'] == job.kind and row['item'] == job.item)

    def download_stage(self, job: Job, admitted: Callable[[], bool] | None = None) -> DownloadStage:
        if not self.valid(job):
            raise ValueError('expired download job')
        directory = self.root/'staging'/'_downloads'/(str(job.id)+'-'+str(job.generation)+'-'+secrets.token_hex(8))
        directory.mkdir(parents=True,mode=0o700)
        return DownloadStage(directory,job.id,job.generation,admitted or (lambda:self.valid(job)))

    def _retained_payload(self, path: Path, staging: DownloadStage | None) -> bytes | StagedPayload:
        if staging is None:
            return path.read_bytes()
        retained = staging.path/('retained-'+path.name)
        os.link(path,retained)
        return StagedPayload(retained,retained.stat().st_size,self.versions.file_digest(retained))

    def publication(self, item: str, staging: DownloadStage | None = None) -> Publication | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        if row and row['platform'] is not None:
            header, text = saved_document(self.path(item)) if row['complete'] else ({},'')
            files = self.db.execute("SELECT * FROM media_files WHERE publication_id=? AND status='ready'", (item,)).fetchall()
            kinds = {a['media_id']:a['kind'] for a in header.get('attachments', []) if 'media_id' in a}
            retained = tuple(Attachment(self.media.projection_name(media), self._retained_payload(self.root/media['storage_key'], staging),
                kinds.get(media['id'],'file'), media['mime_type'] or 'application/octet-stream') for media in files if (self.root/media['storage_key']).is_file())
            refs = tuple(MediaReference(m['resource_key'],m['source_locator'],m['name'],m['mime_type'],m['size_bytes'],m['resource_identity'])
                for m in self.db.execute('SELECT * FROM media_files WHERE publication_id=?',(item,)) if m['source_locator'])
            last_description = PublicationContent(PublicationMetadata(ExternalReference(ExternalKey(Platform(row['platform']),row['external_id']),row['url']),
                header.get('title',''),datetime.fromtimestamp(row['published'],timezone.utc) if row['published_known'] else None),media=refs,
                linked_publications=tuple(ExternalReference(ExternalKey(Platform(target['platform']),target['external_id']),target['url']) for target in self.db.execute('SELECT i.* FROM publication_links l JOIN items i ON i.id=l.target_publication_id WHERE l.source_publication_id=?',(item,))))
            teaser = Material(header.get('title',''),text,header.get('full',False),header.get('reason',''),attachments=retained,
                members=tuple(json.loads(row['members'])),description=header.get('description',''),
                source_manifest=SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else None,
                reply_preview=ReplyPreview.model_validate(header['reply_preview']) if header.get('reply_preview') else None,adapter_content=last_description) if row['complete'] else None
            return Publication(row['source_id'].split(':',1)[1],row['url'],row['published'],teaser,tuple(json.loads(row['members'])),bool(row['published_known']),adapter_reference=last_description.metadata.reference)
        fallback = self.material(item) if row else None
        teaser = Material(fallback['title'], fallback['text'], fallback.get('full', False), images=tuple(Image(Path(url).name, (self.path(item).parent / Path(url).name).read_bytes()) for url in fallback.get('images', []) if (self.path(item).parent / Path(url).name).is_file()), captions=fallback.get('captions'), description=fallback.get('description', ''), reply_preview=ReplyPreview.model_validate(fallback['reply_preview']) if fallback.get('reply_preview') else None, attachments=tuple(Attachment(Path(a['url']).name, self._retained_payload(self.path(item).parent / Path(a['url']).name, staging), a['kind'], a['mime']) for a in fallback.get('attachments', []) if (self.path(item).parent / Path(a['url']).name).is_file())) if fallback else None
        if teaser and fallback:
            header = json.loads(self.path(item).read_text().split('\n\n',1)[0]) if self.path(item).is_file() else {}
            manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if 'source_manifest' in header else None
            teaser = replace(teaser, reason=fallback.get('reason', ''), source_manifest=manifest)
        return Publication(row['source_id'] if row['feed_id'] == 'demo' else row['source_id'].split(':', 1)[1], row['url'], row['published'], teaser, tuple(json.loads(row['members'])), bool(row['published_known'])) if row else None

    def save(self, job: Job, material: Material, now: float | None = None, staging: DownloadStage | None = None) -> None:
        item = job.item
        if not self.valid(job) or not self._has_unviewed(item):
            return
        previous = self.material(item)
        if material.source_manifest and material.source_manifest.post_web_unavailable and previous and (previous['text'].strip() or previous['attachments'] or previous['images']):
            header, _ = self.original(item)
            names = [a['name'] for a in header.get('attachments', [])]
            manifest = SourceManifest.model_validate_json(json.dumps(header['source_manifest'])) if header.get('source_manifest') else material.source_manifest
            names.extend(r.name for r in manifest.resources if r.status == 'loaded')
            if any(not (self.path(item).parent / name).is_file() for name in names):
                with self.db:
                    self._finish_fetch(job, replace(material, collection_error=SourceError('Локальный файл вложения недоступен', code='media-unavailable')), self.clock() if now is None else now)
                return
            retained = self.publication(item)
            accept_preview = (material.source_manifest.preview_fragment is not None
                and (manifest.preview_fragment is not None or
                     manifest.post.get('text') == material.source_manifest.post.get('text')
                     and not manifest.articles and all(r.status == 'unsupported' and r.sha256 is None
                         and r.reason == 'Пост не поддерживается публичным предпросмотром' for r in manifest.resources)))
            if retained and retained.teaser and not accept_preview:
                material = replace(retained.teaser, full=False, members=material.members,
                    source_manifest=manifest.model_copy(update={'post_web_unavailable':True}), collection_error=material.collection_error)
        if self.publications.owns(item):
            prepared = self.versions.prepare(item, material, job, staging)
            canonical = self.publications.prepare(item, material, self.versions.path(item, prepared[0]).parent)
            self.fault('after_file_move')
            with self.db:
                if not self.valid(job) or not self._has_unviewed(item):
                    return
                if material.adapter_content and material.adapter_content.metadata.reference.key.platform==Platform.telegram:
                    self.publications.identify(item,material.adapter_content.metadata.reference)
                self.versions.publish(item, prepared, True)
                self.publications.publish(item, canonical, prepared[1])
                self.db.execute('UPDATE items SET complete=1,members=? WHERE id=?', (json.dumps(material.members), item))
                self._finish_fetch(job, material, self.clock() if now is None else now)
            self.publications.recover(item)
            self.processing.sync()
            with self.db:
                self.publications.status(item)
            return
        if previous and previous.get('full', False) and not material.full and not material.collection_error and material.source_manifest and telegraph_partial(material.source_manifest) and not material.source_manifest.download_partial:
            with self.db:
                self.finish(job)
                self.subscriptions.history.saved(job.item, True)
            return
        if material.members and previous and previous.get('full', False) and (not material.full or material.collection_error) and not (expected_incomplete(material.source_manifest) and not material.collection_error):
            error = material.collection_error or SourceError(material.reason or 'Повторная загрузка не завершена',
                retryable=not bool(material.source_manifest and any(r.status == 'unsupported' for r in material.source_manifest.resources)))
            if error.retryable and material.source_manifest and any(r.status == 'unsupported' for r in material.source_manifest.resources):
                error = SourceError(material.reason or error.label, retryable=False)
            with self.db:
                self._finish_fetch(job, replace(material, collection_error=error), self.clock() if now is None else now)
            return
        prepared = self.versions.prepare(item, material, job, staging)
        self.fault('after_file_move')
        with self.db:
            self.versions.publish(item, prepared, True)
            self.db.execute('UPDATE items SET complete=1 WHERE id=? AND viewed=0', (item,))
            if material.source_manifest and material.source_manifest.source_url:
                self.db.execute('UPDATE items SET url=? WHERE id=?', (material.source_manifest.source_url,item))
            if material.members:
                self.db.execute('UPDATE items SET members=? WHERE id=? AND viewed=0', (json.dumps(material.members), item))
            self._finish_fetch(job, material, self.clock() if now is None else now)
        self.processing.sync()

    def _finish_fetch(self, job: Job, material: Material, now: float) -> None:
        error = material.collection_error
        if not error or not self.subscriptions.history.member(job.item):
            self.subscriptions.history.saved(job.item, material.full)
        if not error:
            self.db.execute('DELETE FROM source_deleted WHERE item=?', (job.item,))
            self.finish(job)
        elif self.publications.owns(job.item):
            collection_jobs.fail(self,job,error.adapter_issue or AdapterIssue(error.code or 'fetch-failed',error.label,
                AdapterErrorKind.temporary if error.retryable else AdapterErrorKind.permanent))
        elif isinstance(error, SourceDeleted):
            self._record_fetch_removal(job, error.label)
        else:
            self._record_fetch_failure(job, error.label,
                error.code or job.kind+':'+(error.remote_type or type(error).__name__), now+error.delay,
                terminal=not error.retryable, forced_wait=error.remote_type=='FloodWaitError')

    def _record_fetch_removal(self, job: Job, label: str) -> None:
        self.db.execute('INSERT OR IGNORE INTO source_deleted VALUES (?)', (job.item,))
        self.subscriptions.history.failed(job.item, True)
        self.finish(job)
        self.db.execute("UPDATE jobs SET error=?,problem_code='source-deleted' WHERE id=?", (label, job.id))

    def _record_fetch_failure(self, job: Job, label: str, code: str, retry_at: float, *, terminal: bool, forced_wait: bool) -> None:
        # Participate in the caller's transaction: a saved copy, history outcome
        # and retry intent must commit together. Only success/removal clears intent.
        row = self.db.execute('SELECT manual_once FROM jobs WHERE id=?', (job.id,)).fetchone()
        once = bool(row and row[0])
        self.subscriptions.history.failed(job.item)
        self.db.execute("""UPDATE jobs SET status=?,next=?,error=?,problem_code=?
            WHERE id=? AND generation=? AND status='running'""",
            ('done' if terminal or (once and not forced_wait) else 'pending', retry_at, label, code, job.id, job.generation))

    def folder_contents(self, folder: str) -> FolderContents:
        return self.reading.folder_contents(folder)

    def viewed(self, item: str) -> bool:
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
                row = self.db.execute('SELECT feed_id,members FROM items WHERE id=?', (member,)).fetchone()
                for feed in memberships[member]:
                    self.read_sync.eligible(feed,json.loads(row['members']),'viewed')
                if self.publications.owns(member):
                    self.db.execute('DELETE FROM feed_publications WHERE publication_id=?',(member,))
                    publication_links.prune(self,{member})
                    self.db.execute('UPDATE items SET feed_id=NULL WHERE id=? AND viewed=0',(member,))
                    continue
                self.subscriptions.history.viewed(member)
                self.db.execute('DELETE FROM thesis_inputs WHERE item=?', (member,))
                self.db.execute('DELETE FROM retained_theses WHERE item=?', (member,))
                self.filters.forget(member)
                self.youtube.reset(member)
                self.versions.forget(member)
                self.db.execute('UPDATE items SET viewed=1,complete=0,published=NULL WHERE id=?', (member,))
                self.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE item=? AND kind!='delete'", (member,))
                self.db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('delete',?)", (member,))
        self.read_sync.sync()
        self.processing.sync()
        self.fault('after_viewed_commit')
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
                if remaining:
                    self.db.execute("UPDATE jobs SET status=CASE WHEN status='running' THEN 'pending' ELSE status END,generation=generation+1 WHERE item=? AND kind IN ('fetch','filter','theses')",(item,))
                    self.db.execute("UPDATE jobs SET status='pending',generation=generation+1 WHERE kind='download_media' AND status='running' AND item IN (SELECT id FROM media_files WHERE publication_id=?)",(item,))
            publication_links.prune(self,set(shared))
            for item in shared:
                self.db.execute('UPDATE items SET feed_id=NULL WHERE id=? AND feed_id=? AND viewed=0',(item,feed))
            items = [r[0] for r in self.db.execute('SELECT id FROM items WHERE feed_id=?', (feed,))]
            self.problems.forget_materials(set(items), self.wall_clock())
            self.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1 WHERE (item=? OR item IN (SELECT id FROM items WHERE feed_id=?)) AND kind NOT IN ('delete','feed-delete')", (feed, feed))
            for item in items:
                self.filters.forget(item)
                self.youtube.reset(item)
                self.versions.forget(item)
                if self.publications.owns(item):
                    self.publications.forget(item)
                self.db.execute('DELETE FROM thesis_inputs WHERE item=?', (item,))
                self.db.execute('DELETE FROM retained_theses WHERE item=?', (item,))
                # This is local cleanup, not viewed(): never create read eligibility.
                self.db.execute('UPDATE items SET viewed=1,complete=0 WHERE id=?', (item,))
                self.db.execute("INSERT INTO jobs(kind,item) VALUES ('delete',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,generation=generation+1", (item,))
            self.db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('feed-delete',?)", (feed,))
        self.fault('after_feed_delete_commit')

    def finish_feed_deletion(self, job: Job) -> None:
        feed = job.item
        if self.db.execute("SELECT 1 FROM jobs WHERE kind='delete' AND status!='done' AND item IN (SELECT id FROM items WHERE feed_id=?)", (feed,)).fetchone():
            self.retry(job, 0, 'Ожидание очистки файлов', .1)
            return
        self.fault('before_feed_claim_release')
        with self.db:
            self.db.execute('DELETE FROM execution_settings WHERE job IN (SELECT id FROM jobs WHERE item=? OR item IN (SELECT id FROM items WHERE feed_id=?))', (feed, feed))
            self.db.execute('DELETE FROM jobs WHERE item=? OR item IN (SELECT id FROM items WHERE feed_id=?)', (feed, feed))
            self.db.execute('DELETE FROM items WHERE feed_id=?', (feed,))
            for table in ('history_members', 'initial_history', 'telegram_read_members', 'telegram_read_state', 'telegram_read_topics'):
                self.db.execute(f'DELETE FROM {table} WHERE feed=?', (feed,))
            self.db.execute("DELETE FROM settings WHERE scope='feed' AND owner=?", (feed,))
            self.db.execute('DELETE FROM state WHERE key IN (?,?,?)', ('cursor:'+feed, 'cursor:telegram:'+feed, 'feed-status:'+feed))
            self.subscriptions.telegram.release(feed)
            self.db.execute('DELETE FROM feed_details WHERE feed=?', (feed,))
            self.db.execute('DELETE FROM feeds WHERE id=?', (feed,))

    def delete(self, job: Job) -> None:
        self.fault('before_delete')
        for path in (self.root / 'content' / job.item, self.root / 'staging' / job.item, self.root / 'media' / job.item):
            if any(path==active or path in active.parents for active in self.media.active_paths):
                self.retry(job,self.clock(),'Ожидание завершения сохранения файла',.1)
                return
            if path.exists():
                shutil.rmtree(path)
        self.fault('after_delete_files')
        with self.db:
            self.db.execute('DELETE FROM execution_settings WHERE job IN (SELECT id FROM jobs WHERE item=?)', (job.item,))
            self.finish(job)
            if self.publications.active:
                self.db.execute("DELETE FROM items WHERE id=? AND viewed=1 AND platform IS NOT NULL ",(job.item,))

    def retry(self, job: Job, now: float, error: str, delay: float = 1, *, code: str | None = None, terminal: bool = False, issue: AdapterIssue | None = None) -> None:
        if job.kind in ('fetch','filter','theses') and self.publications.owns(job.item):
            with self.db:
                collection_jobs.fail(self, job, issue or AdapterIssue(code or 'fetch-failed', error,
                    AdapterErrorKind.permanent if terminal else AdapterErrorKind.temporary))
            return
        with self.db:
            if job.kind == 'fetch':
                self._record_fetch_failure(job, error, code or job.kind, 0 if code == 'ai-permission' else now+delay,
                    terminal=terminal, forced_wait=(code or '').endswith('FloodWaitError'))
                return
            row = self.db.execute('SELECT manual_once FROM jobs WHERE id=?',(job.id,)).fetchone()
            once = row and row[0] and not (code or '').endswith('FloodWaitError')
            self.db.execute("UPDATE jobs SET status=?,next=?,error=?,problem_code=? WHERE id=? AND generation=? AND status='running'",
                            ('done' if once or terminal else 'pending', 0 if code == 'ai-permission' else now + delay, error, code or job.kind, job.id, job.generation))

    def restore_telegram_wait(self, feed: str, deadline: float) -> None:
        with self.db:
            self.db.execute("""UPDATE jobs SET next=? WHERE status='pending' AND problem_code LIKE '%:FloodWaitError'
              AND (item=? OR item IN (SELECT id FROM items WHERE feed_id=?))""", (deadline, feed, feed))

    def interrupted(self, feeds: set[str] | None = None) -> None:
        selection = "status='running'"
        params: tuple[str, ...] = ()
        if feeds is not None:
            placeholders = ','.join('?' for _ in feeds) or "''"
            selection += f" AND (item IN ({placeholders}) OR item IN (SELECT id FROM items WHERE feed_id IN ({placeholders})) OR item IN (SELECT m.id FROM media_files m JOIN items i ON i.id=m.publication_id WHERE i.feed_id IN ({placeholders})))"
            params = (*feeds, *feeds, *feeds)
        with self.db:
            collection_jobs.interrupt(self,feeds)
            self.db.execute(f"UPDATE execution_settings SET generation=generation+1 WHERE job IN (SELECT id FROM jobs WHERE {selection})", params)
            self.db.execute(f"UPDATE jobs SET status='pending',generation=generation+1 WHERE {selection}", params)

    @reading_value
    def original(self, item: str) -> tuple[dict[str, Any], str]:
        header,text=saved_document(self.path(item))
        if self.publications.active:
            own=self.db.execute('SELECT content FROM items WHERE id=?',(item,)).fetchone()
            if own and 'youtube' in json.loads(own[0]).get('extra',{}):
                projected=youtube_media.add_input(self,json.loads(own[0]))
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
            if filtered['status'] == 'done' and not filtered['restored']:
                for fragment in filtered['removed']:
                    display = display.replace(fragment, '')
            material['display_text'] = display
        return material

    def saved_publication(self, row: sqlite3.Row) -> dict[str, Any] | None:
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
            previous = self.versions.previous(item)
            base.update(changed=bool(previous), previous=previous, source_deleted=self.versions.deleted(item),
                filter=filtered, title=header['title'], theses=self.processing.theses.read(item),
                provenance=self.processing.theses.provenance(item), ai=self.processing.theses.status(item),
                version=self.content_version(item), loading=bool(self.db.execute(
                    "SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND (status='running' OR (status='pending' AND next<=?))",
                    (item, reader_now(self.clock()))).fetchone()),
                full=header.get('full', True) and (not header.get('members') or set(header['members']) == set(base['members'])),
                reason=header.get('reason', ''), captions=header.get('captions'),
                attachments=[dict(url=header.get('_media_urls',{}).get(a['name'],f"/api/v3/materials/{item}/media/{a.get('media_id',a['name'])}"), kind=a['kind'], mime=a['mime']) for a in header.get('attachments', [])],
                images=[] if row['platform'] is not None else [f'/api/v3/materials/{item}/images/{p.name}' for p in sorted(self.path(item).parent.glob('image-*'))])
            base.update(saved_external(header, f'/api/v3/materials/{item}'))
            base['loading'] = base['loading'] or row['platform'] is not None and self.media.loading(item)
            linked=saved_telegraph(header, f'/api/v3/materials/{item}')
            if row['platform']=='telegram':
                linked=publication_links.reading(self,item,saved_document(self.path(item))[1]) or linked
            base.update(text=text, description=header.get('description', ''), reply_preview=header.get('reply_preview'), linked_content=linked)
            return base
        except (OSError, ValueError, KeyError):
            record_caught('store.material')
            return None

    def cards(self, show_hidden: bool = False) -> list[dict[str, Any]]:
        return self.reading.cards(show_hidden)

    def work_summary(self) -> list[WorkSummary]:
        rows = self.db.execute("""SELECT jobs.kind,jobs.status,jobs.problem_code,count(*) AS count,
            CASE WHEN jobs.kind IN ('discover','read','feed-delete') THEN NULLIF(jobs.item,'')
                WHEN jobs.kind='download_media' THEN (SELECT i.feed_id FROM media_files m JOIN items i ON i.id=m.publication_id WHERE m.id=jobs.item)
                ELSE items.feed_id END AS feed,
            min(jobs.next) AS next,sum(jobs.error IS NOT NULL) AS errors
            FROM jobs LEFT JOIN items ON items.id=jobs.item
            WHERE jobs.status IN ('pending','running')
            GROUP BY jobs.kind,jobs.status,jobs.problem_code,feed ORDER BY min(jobs.id)""").fetchall()
        now, wall = self.clock(), self.wall_clock()
        return [WorkSummary(row['kind'],row['status'],row['feed'],row['count'],
                    row['problem_code'] or ('legacy-'+row['kind'] if row['errors'] else None),
                    retry_time(row['next'],now,wall) if row['status']=='pending' and row['errors'] and row['next'] else None)
                for row in rows]

    def status(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT kind,status,error,count(*) AS count FROM jobs WHERE status IN ('pending','running') OR error IS NOT NULL GROUP BY kind,status,error").fetchall()
        # The durable feed sweep is deletion work, not a new public job kind.
        jobs = [dict(r, kind='delete' if r['kind'] == 'feed-delete' else r['kind']) for r in rows]
        errors = sorted({r['error'] for r in rows if r['error']})
        pending_deletes = any(r['kind'] in ('delete', 'feed-delete') and r['status'] != 'done' for r in rows)
        return dict(jobs=jobs, errors=errors, deleting=pending_deletes)


    def saved_execution(self, job: Job, signature: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM execution_settings WHERE job=? AND generation=? AND signature=?',
                              (job.id, job.generation, signature)).fetchone()
        return json.loads(row['parameters']) if row else None

    def save_execution(self, job: Job, signature: str, origin: dict[str, Any]) -> None:
        with self.db:
            self.db.execute('INSERT INTO execution_settings VALUES (?,?,?,?) ON CONFLICT(job) DO UPDATE SET generation=excluded.generation,signature=excluded.signature,parameters=excluded.parameters',
                            (job.id, job.generation, signature, json.dumps(origin)))

    def forget_execution(self, kind: str, item: str) -> None:
        with self.db:
            self.db.execute('DELETE FROM execution_settings WHERE job IN (SELECT id FROM jobs WHERE kind=? AND item=?)', (kind, item))

    def execution_provenance(self, job: Job) -> dict[str, Any] | None:
        row = self.db.execute('SELECT parameters FROM execution_settings WHERE job=? AND generation=?', (job.id, job.generation)).fetchone()
        return json.loads(row[0]) if row else None

    @reading_value
    def derivative_provenance(self, path: Path, signature: str | None) -> dict[str, Any] | None:
        if signature is None:
            return None
        try:
            header, _ = saved_document(path)
            if header['signature'] == signature:
                return header.get('provenance') or dict(model=None)
        except (OSError, ValueError, KeyError):
            record_caught('store.derivative_provenance')
        return None

    def work_enabled(self) -> bool:
        row = self.db.execute("SELECT value FROM state WHERE key='work-enabled'").fetchone()
        return row is None or row['value'] != 'false'

    def set_work_enabled(self, enabled: bool) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES ('work-enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('true' if enabled else 'false',))

    def codex_config(self) -> CodexSettings | None:
        row = self.db.execute("SELECT value FROM state WHERE key='processor:codex'").fetchone()
        return CodexSettings.model_validate_json(row[0]) if row else None

    def configure_codex(self, config: CodexSettings) -> None:
        # Future execution choice does not invalidate inputs or cancel work.
        with self.db:
            self.db.execute("INSERT INTO state VALUES ('processor:codex',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (config.model_dump_json(),))

    @reading_value
    def processor_config(self) -> ProcessorConfig | None:
        value = material_read.state(self.db,'processor:llamacpp')
        return ProcessorConfig.model_validate_json(value) if value else None

    def configure_processor(self, config: ProcessorConfig) -> bool:
        if self.processor_config() == config:
            return False
        with self.db:
            self.db.execute("INSERT INTO state VALUES ('processor:llamacpp',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (config.model_dump_json(),))
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

    def content_version(self, item: str) -> str:
        version=self.versions.version(item)
        if self.publications.active:
            youtube=self.db.execute("SELECT json_extract(content,'$.extra.youtube') FROM items WHERE id=? AND platform='youtube'",(item,)).fetchone()
            if youtube and youtube[0]:
                data=json.loads(youtube[0])
                version=hashlib.sha256(json.dumps([version,data.get('captions'),data.get('captions_digest')]).encode()).hexdigest()
            linked=self.db.execute('''SELECT i.id,i.complete,v.fingerprint FROM publication_links l JOIN items i ON i.id=l.target_publication_id
                LEFT JOIN content_current c ON c.item=i.id LEFT JOIN content_versions v ON v.item=i.id AND v.number=c.number
                WHERE l.source_publication_id=? AND i.viewed=0 ORDER BY i.discovered_at,i.id''',(item,)).fetchall()
            if linked:
                return hashlib.sha256(json.dumps([version,[tuple(row) for row in linked]]).encode()).hexdigest()
        return version

    def prepare_youtube(self, feed: str, want_captions: bool) -> None:
        if not want_captions:
            return
        # This runs in the reader loop. Admission needs only the saved caption
        # state, not a reader material with filters, theses and provenance.
        rows = self.db.execute("""SELECT id FROM items WHERE feed_id=? AND viewed=0 AND complete=1
            AND NOT EXISTS (SELECT 1 FROM jobs WHERE kind='fetch' AND item=items.id AND status IN ('pending','running'))""", (feed,)).fetchall()
        for row in rows:
            try:
                with self.path(row['id']).open() as source:
                    header = json.loads(source.readline())
            except (OSError, ValueError):
                record_caught('store.prepare_youtube')
                continue
            if header.get('captions') == 'not_requested':
                self.retry_material(row['id'])

    def save_derivative(self, job: Job, signature: str, result: dict[str, Any], text: str, finish: bool = True, *, provenance: dict[str, Any] | None | Literal['execution'] = 'execution') -> None:
        """Commit a validated AI result; callers own admission and input signature."""
        stage = None
        if job.kind in ('filter', 'theses'):
            stage = self.root / 'staging' / job.item
            stage.mkdir(exist_ok=True)
            filename = job.kind + '.md'
            destination = self.path(job.item).parent / filename
            pending = stage / filename
        else:
            raise ValueError('Unknown derivative')
        provenance = self.execution_provenance(job) if provenance == 'execution' else provenance
        header=dict(signature=signature,result=result,provenance=provenance)
        if self.publications.owns(job.item) and isinstance(provenance,dict):
            header['execution']=provenance
            header['provenance']={key:value for key,value in provenance.items() if key!='parameters'}
        with pending.open('w', encoding='utf-8') as output:
            output.write(json.dumps(header, ensure_ascii=False) + '\n\n' + text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, destination)
        if stage is not None:
            stage.rmdir()
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self.fault('after_' + job.kind + '_move')
        if finish:
            with self.db:
                if self.publications.owns(job.item):
                    self.publications.results(job.item, {job.kind: dict(file=destination.read_text())})
                self.finish(job)
                if job.kind == 'filter':
                    self.read_versions.sync_filter(job.item, bool(result['hidden']) and not self.filters.read(job.item).get('restored', False))
