from dataclasses import dataclass
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
from collections.abc import Callable
from typing import Any

from oko.reader.composition import FolderContents, ReadingComposition
from oko.collection.telegram.read_sync import ReadSync
from oko.reviews.events import Events
from oko.reviews.digests import Digests
from oko.reviews.filtering import Filters
from oko.collection.contracts import Attachment, Material, Publication
from oko.subscriptions.subscriptions import Subscriptions
from oko.processing.contracts import InputSource, ProcessingRequest, Theses
from oko.processing.llamacpp import ProcessorConfig


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    item: str
    generation: int


class Store:
    """SQLite owns identity/lifecycle; files own all publication content."""
    def __init__(self, root: Path, fault: Callable[[str], None]) -> None:
        self.root, self.fault = root, fault
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(root / 'state.sqlite')
        self.db.row_factory = sqlite3.Row
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
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS thesis_inputs (item TEXT PRIMARY KEY, signature TEXT);
        ''')
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(items)')}
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
        self.subscriptions = Subscriptions(self.db, self.fault)
        self.filters = Filters(self)
        self.events = Events(self)
        self.digests = Digests(self)
        self.reading = ReadingComposition(self)
        self.read_sync = ReadSync(self)
        for name in ('content', 'staging'):
            (root / name).mkdir(exist_ok=True)
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',generation=generation+1 WHERE status='running'")
            self.db.execute("UPDATE jobs SET next=0 WHERE status='pending'")
        self.recover_files()

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
        return self.root / 'content' / item / '1' / 'original.md'

    def enqueue_discovery(self, feed: str = 'demo') -> None:
        with self.db:
            self.db.execute("INSERT INTO jobs(kind,item) VALUES ('discover',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,error=NULL WHERE jobs.status='done'", ('' if feed == 'demo' else feed,))

    def cursor_key(self, feed: str, namespace: str) -> str:
        return 'cursor:' + namespace + ':' + feed if namespace else 'cursor' if feed == 'demo' else 'cursor:' + feed

    def cursor(self, feed: str = 'demo', namespace: str = '') -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (self.cursor_key(feed, namespace),)).fetchone()
        return row[0] if row else None

    def discovered(self, publications: list[Publication], cursor: str, job: Job, feed: str = 'demo', namespace: str = '') -> None:
        with self.db:
            for p in publications:
                item = hashlib.sha256((feed + ':' + p.id).encode()).hexdigest()[:32]
                source_id = p.id if feed == 'demo' else feed + ':' + p.id
                existing = self.db.execute('SELECT url FROM items WHERE source_id=?', (source_id,)).fetchone()
                if existing and existing['url'] != p.url and not (p.members and p.id.startswith('album:')):
                    source_id = feed + ':' + p.url
                    item = hashlib.sha256(source_id.encode()).hexdigest()[:32]
                inserted = self.db.execute('INSERT OR IGNORE INTO items(id,source_id,url,published,feed_id,members) VALUES (?,?,?,?,?,?)',
                                (item, source_id, p.url, p.published, feed, json.dumps(p.members)))
                if inserted.rowcount and p.teaser:
                    self.write_material(item, p.teaser)
                    self.db.execute('UPDATE items SET complete=1 WHERE id=?', (item,))
                row = self.db.execute('SELECT * FROM items WHERE source_id=?', (source_id,)).fetchone()
                if row and not row['viewed']:
                    members = sorted(set(json.loads(row['members'])) | set(p.members))
                    if members != json.loads(row['members']):
                        self.db.execute('UPDATE items SET members=? WHERE id=?', (json.dumps(members), row['id']))
                        self.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='fetch' AND item=?", (row['id'],))
                    self.db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('fetch',?)", (row['id'],))
            if namespace == 'telegram':
                self.read_sync.observed(feed, tuple(m for p in publications for m in p.members), cursor)
            self.fault('before_discovery_commit')
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.cursor_key(feed, namespace), cursor))
            self.finish(job)

    def retry_material(self, item: str) -> bool:
        if self.publication(item) is None:
            return False
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='fetch' AND item=?", (item,))
        return True

    def item_feed(self, item: str) -> str:
        return str(self.db.execute('SELECT feed_id FROM items WHERE id=?', (item,)).fetchone()[0])

    def feed_status(self, feed: str, **state: Any) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('feed-status:' + feed, json.dumps(state)))

    def source_statuses(self) -> list[dict[str, Any]]:
        return [dict(feed_id=row['key'].split(':', 1)[1], **json.loads(row['value'])) for row in self.db.execute("SELECT * FROM state WHERE key LIKE 'feed-status:%'")]

    def ready_sources(self, feeds: list[str]) -> int:
        return sum(1 for feed in feeds if not self.db.execute(
            "SELECT 1 FROM jobs WHERE status IN ('pending','running') AND kind!='read' AND (item=? OR item IN (SELECT id FROM items WHERE feed_id=?))",
            ('' if feed == 'demo' else feed, feed)).fetchone())

    def next_job(self, now: float, deleting_only: bool = False, source_feed: str = 'demo', feeds: list[str] | None = None, ai_only: bool = False) -> Job | None:
        query = "SELECT * FROM jobs WHERE status='pending' AND next<=?"
        if ai_only:
            query += " AND kind IN ('filter','theses','events','digest')"
        elif deleting_only:
            query += " AND kind='delete'"
        else:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('?' for _ in available) or "''"
            query += f" AND (kind='delete' OR (kind IN ('discover','read') AND item IN ({placeholders})) OR (kind='fetch' AND item IN (SELECT id FROM items WHERE feed_id IN ({placeholders}))))"
        query += " ORDER BY CASE kind WHEN 'delete' THEN 0 WHEN 'discover' THEN 1 WHEN 'filter' THEN 2 WHEN 'read' THEN 2 ELSE 3 END,id LIMIT 1"
        params = (now,) if deleting_only or ai_only else (now, *('' if f == 'demo' else f for f in available), *available)
        row = self.db.execute(query, params).fetchone()
        if not row:
            return None
        with self.db:
            self.db.execute("UPDATE jobs SET status='running',attempt=attempt+1,generation=generation+1 WHERE id=?", (row['id'],))
        current = self.db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()
        return Job(current['id'], current['kind'], current['item'], current['generation'])

    def finish(self, job: Job) -> None:
        self.db.execute("UPDATE jobs SET status='done',error=NULL WHERE id=? AND generation=? AND status='running'", (job.id, job.generation))

    def valid(self, job: Job) -> bool:
        row = self.db.execute('SELECT status,generation FROM jobs WHERE id=?', (job.id,)).fetchone()
        return bool(row and row['status'] == 'running' and row['generation'] == job.generation)

    def publication(self, item: str) -> Publication | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        fallback = self.material(item) if row else None
        teaser = Material(fallback['title'], fallback['text'], False, attachments=tuple(Attachment(Path(a['url']).name, (self.path(item).parent / Path(a['url']).name).read_bytes(), a['kind'], a['mime']) for a in fallback.get('attachments', []))) if fallback else None
        return Publication(row['source_id'] if row['feed_id'] == 'demo' else row['source_id'].split(':', 1)[1], row['url'], row['published'], teaser, tuple(json.loads(row['members']))) if row else None

    def save(self, job: Job, material: Material) -> None:
        item = job.item
        if not self.valid(job) or self.publication(item) is None:
            return
        self.write_material(item, material)
        self.fault('after_file_move')
        with self.db:
            self.db.execute('UPDATE items SET complete=1 WHERE id=? AND viewed=0', (item,))
            if material.members:
                self.db.execute('UPDATE items SET members=? WHERE id=? AND viewed=0', (json.dumps(material.members), item))
            self.finish(job)
        self.sync_theses()

    def write_material(self, item: str, material: Material) -> None:
        stage = self.root / 'staging' / item
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        # Header is content too, and deliberately lives outside SQLite.
        with (stage / 'original.md').open('w', encoding='utf-8') as f:
            f.write(json.dumps({'title': material.title, 'full': material.full, 'reason': material.reason, 'members': list(material.members), 'captions': material.captions, 'attachments': [dict(name=a.name, kind=a.kind, mime=a.mime) for a in material.attachments]}, ensure_ascii=False) + '\n\n' + material.text)
            f.flush()
            os.fsync(f.fileno())
        for image in material.images:
            if Path(image.name).name != image.name or not image.name.startswith('image-'):
                raise ValueError('image name')
            with (stage / image.name).open('wb') as output:
                output.write(image.data)
                output.flush()
                os.fsync(output.fileno())
        for attachment in material.attachments:
            if not re.fullmatch(r'media-[0-9]+\.[a-z0-9]+', attachment.name):
                raise ValueError('attachment name')
            with (stage / attachment.name).open('wb') as output:
                output.write(attachment.data)
                output.flush()
                os.fsync(output.fileno())
        destination = self.path(item).parent
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(stage, destination)
        for directory in (destination, destination.parent, destination.parent.parent, self.root / 'staging'):
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def folder_contents(self, folder: str) -> FolderContents:
        return self.reading.folder_contents(folder)

    def viewed(self, item: str) -> bool:
        return self.viewed_set(self.reading.members(item))

    def viewed_set(self, members: list[str]) -> bool:
        members = sorted(set(members))
        with self.db:
            if any(not self.db.execute('SELECT 1 FROM items WHERE id=?', (member,)).fetchone() for member in members):
                return False
            for member in members:
                row = self.db.execute('SELECT feed_id,members FROM items WHERE id=?', (member,)).fetchone()
                self.read_sync.eligible(row['feed_id'], json.loads(row['members']), 'viewed')
                self.db.execute('DELETE FROM thesis_inputs WHERE item=?', (member,))
                self.filters.forget(member)
                self.db.execute('UPDATE items SET viewed=1,complete=0,published=NULL WHERE id=?', (member,))
                self.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE item=? AND kind!='delete'", (member,))
                self.db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('delete',?)", (member,))
        self.read_sync.sync()
        self.digests.sync()
        self.events.sync()
        self.fault('after_viewed_commit')
        return True

    def delete(self, job: Job) -> None:
        self.fault('before_delete')
        if job.item.startswith('event-'):
            self.events.path(job.item).unlink(missing_ok=True)
            self.events.path(job.item).with_suffix('.tmp').unlink(missing_ok=True)
        if job.item.startswith('digest-'):
            self.digests.path(job.item).unlink(missing_ok=True)
            self.digests.path(job.item).with_suffix('.tmp').unlink(missing_ok=True)
        for path in (self.root / 'content' / job.item, self.root / 'staging' / job.item):
            if path.exists():
                shutil.rmtree(path)
        self.fault('after_delete_files')
        with self.db:
            self.finish(job)

    def retry(self, job: Job, now: float, error: str, delay: float = 1) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',next=?,error=? WHERE id=? AND generation=? AND status='running'",
                            (now + delay, error, job.id, job.generation))

    def interrupted(self) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',generation=generation+1 WHERE status='running'")

    def material(self, item: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        if not row:
            return None
        if not row['complete']:
            return dict(id=item, feed_id=row['feed_id'], url=row['url'], published=row['published'], title='Материал загружается', text='', complete=False)
        try:
            header, text = self.path(item).read_text().split('\n\n', 1)
            filtered = self.filters.read(item)
            display_text = text
            if filtered['status'] == 'done' and not filtered['restored']:
                for fragment in filtered['removed']:
                    display_text = display_text.replace(fragment, '')
            return dict(originals=self.subscriptions.effective('feed', row['feed_id'])['effective']['originals']['value'], filter=filtered, display_text=display_text, id=item, feed_id=row['feed_id'], url=row['url'], published=row['published'], title=json.loads(header)['title'], text=text, complete=True, theses=self.theses(item), ai=self.thesis_status(item), version=self.content_version(item), loading=bool(self.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status IN ('pending','running')", (item,)).fetchone()), full=json.loads(header).get('full', True) and (not json.loads(header).get('members') or set(json.loads(header)['members']) == set(json.loads(row['members']))), reason=json.loads(header).get('reason', ''), captions=json.loads(header).get('captions'), members=json.loads(row['members']), attachments=[dict(url=f"/api/materials/{item}/media/{a['name']}", kind=a['kind'], mime=a['mime']) for a in json.loads(header).get('attachments', [])], images=[f'/api/materials/{item}/images/{p.name}' for p in sorted(self.path(item).parent.glob('image-*'))])
        except (OSError, ValueError, KeyError):
            return None

    def cards(self, show_hidden: bool = False) -> list[dict[str, Any]]:
        return self.reading.cards(show_hidden)

    def status(self) -> dict[str, Any]:
        rows = self.db.execute('SELECT kind,status,error,count(*) AS count FROM jobs GROUP BY kind,status,error').fetchall()
        jobs = [dict(r) for r in rows]
        errors = sorted({r['error'] for r in rows if r['error']})
        pending_deletes = any(r['kind'] == 'delete' and r['status'] != 'done' for r in rows)
        return dict(jobs=jobs, errors=errors, deleting=pending_deletes)


    def processor_config(self) -> ProcessorConfig | None:
        row = self.db.execute("SELECT value FROM state WHERE key='processor:llamacpp'").fetchone()
        return ProcessorConfig.model_validate_json(row[0]) if row else None

    def configure_processor(self, config: ProcessorConfig) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES ('processor:llamacpp',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (config.model_dump_json(),))
        self.settings_changed()

    def settings_version(self) -> int:
        row = self.db.execute("SELECT value FROM state WHERE key='settings-version'").fetchone()
        return int(row[0]) if row else 0

    def settings_changed(self) -> None:
        with self.db:
            self.db.execute("INSERT INTO state VALUES ('settings-version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(self.settings_version() + 1),))
        self.sync_theses()

    def content_version(self, item: str) -> str:
        return hashlib.sha256(self.path(item).read_bytes()).hexdigest()

    def prepare_youtube(self, feed: str, want_captions: bool) -> None:
        if not want_captions:
            return
        for row in self.db.execute('SELECT id FROM items WHERE feed_id=? AND viewed=0 AND complete=1', (feed,)).fetchall():
            material = self.material(row['id'])
            if material and material.get('captions') == 'not_requested' and not material.get('loading'):
                self.retry_material(row['id'])

    def thesis_signature(self, item: str) -> str | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0 AND complete=1', (item,)).fetchone()
        if not row or not self.path(item).is_file():
            return None
        filtered = self.filters.read(item)
        if filtered['status'] not in ('off', 'done') or filtered['hidden']:
            return None
        settings = self.subscriptions.effective('feed', row['feed_id'])
        values = {k: v['value'] for k, v in settings['effective'].items()}
        if settings['feed']['kind'] == 'youtube':
            header = json.loads(self.path(item).read_text().split('\n\n', 1)[0])
            if not self.subscriptions.youtube_theses(row['feed_id']) or header.get('captions') != 'available':
                return None
        elif not values['theses']:
            return None
        config = self.processor_config()
        payload = [self.content_version(item), filtered, row['members'], self.settings_version(), values, settings['feed']['public_ai'], config.model_dump() if config else None]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def sync_theses(self) -> None:
        self.filters.sync()
        self.digests.sync()
        self.events.sync()
        for row in self.db.execute('SELECT id FROM items WHERE viewed=0').fetchall():
            item = row['id']
            signature = self.thesis_signature(item)
            old = self.db.execute('SELECT signature FROM thesis_inputs WHERE item=?', (item,)).fetchone()
            if old and old[0] == signature:
                state = self.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=?", (item,)).fetchone()
                if state and (state[0] != 'done' or self.theses(item) is not None):
                    continue
            path = self.path(item).parent / 'theses.md'
            path.unlink(missing_ok=True)
            with self.db:
                self.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE kind='theses' AND item=?", (item,))
                if signature:
                    self.db.execute('INSERT INTO thesis_inputs VALUES (?,?) ON CONFLICT(item) DO UPDATE SET signature=excluded.signature', (item, signature))
                    self.db.execute("INSERT INTO jobs(kind,item) VALUES ('theses',?) ON CONFLICT(kind,item) DO UPDATE SET status='pending',generation=generation+1,next=0,error=NULL", (item,))
                else:
                    self.db.execute('DELETE FROM thesis_inputs WHERE item=?', (item,))

    def processing_policy(self, item: str) -> tuple[str, bool] | None:
        row = self.db.execute('SELECT feed_id FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        if not row:
            return None
        settings = self.subscriptions.effective('feed', row['feed_id'])
        return settings['effective']['processor']['value'], settings['feed']['public_ai']

    def processing_request(self, job: Job) -> tuple[ProcessingRequest, str, bool] | None:
        signature = self.thesis_signature(job.item)
        row = self.db.execute('SELECT signature FROM thesis_inputs WHERE item=?', (job.item,)).fetchone()
        material = self.material(job.item)
        if not signature or not row or row[0] != signature or not material:
            return None
        settings = self.subscriptions.effective('feed', material['feed_id'])
        values = settings['effective']
        request = ProcessingRequest(sources=(InputSource(id=job.item, version=self.content_version(job.item), url=material['url'], title=material['title'], text=material['display_text']),), settings_version=self.settings_version(), prompt=values['prompt_theses']['value'], processor=values['processor']['value'], generation=job.generation)
        return request, signature, settings['feed']['public_ai']

    def retry_theses(self, item: str) -> bool:
        if not self.material(item) or not self.thesis_signature(item):
            return False
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',next=0,error=NULL,generation=generation+1 WHERE kind='theses' AND item=?", (item,))
        return True

    def thesis_status(self, item: str) -> dict[str, Any] | None:
        if not self.thesis_signature(item):
            return None
        row = self.db.execute("SELECT status,error FROM jobs WHERE kind='theses' AND item=?", (item,)).fetchone()
        return dict(row) if row else dict(status='pending', error=None)

    def theses(self, item: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT status FROM jobs WHERE kind='theses' AND item=?", (item,)).fetchone()
        if not row or row[0] != 'done':
            return None
        try:
            header = json.loads((self.path(item).parent / 'theses.md').read_text().split('\n\n', 1)[0])
            if header['signature'] != self.thesis_signature(item):
                return None
            return Theses.model_validate(header['result']).model_dump()
        except (OSError, ValueError, KeyError):
            return None

    def save_theses(self, job: Job, signature: str, theses: Theses) -> None:
        if not self.valid(job) or signature != self.thesis_signature(job.item):
            return
        self.save_derivative(job, signature, theses.model_dump(), '\n'.join('- ' + p.text for p in theses.points))

    def save_derivative(self, job: Job, signature: str, result: dict[str, Any], text: str, finish: bool = True) -> None:
        """Commit a validated AI result; callers own admission and input signature."""
        stage = None
        if job.kind in ('events', 'digest'):
            destination = (self.events if job.kind == 'events' else self.digests).path(job.item)
            pending = destination.with_suffix('.tmp')
        elif job.kind in ('filter', 'theses'):
            stage = self.root / 'staging' / job.item
            stage.mkdir(exist_ok=True)
            filename = job.kind + '.md'
            destination = self.path(job.item).parent / filename
            pending = stage / filename
        else:
            raise ValueError('Unknown derivative')
        with pending.open('w', encoding='utf-8') as output:
            output.write(json.dumps(dict(signature=signature, result=result), ensure_ascii=False) + '\n\n' + text)
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
                self.finish(job)
