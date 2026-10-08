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

from .sources import Attachment, Material, Publication
from .subscriptions import Subscriptions


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
        self.subscriptions = Subscriptions(self.db)
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
            "SELECT 1 FROM jobs WHERE status IN ('pending','running') AND (item=? OR item IN (SELECT id FROM items WHERE feed_id=?))",
            ('' if feed == 'demo' else feed, feed)).fetchone())

    def next_job(self, now: float, deleting_only: bool = False, source_feed: str = 'demo', feeds: list[str] | None = None) -> Job | None:
        query = "SELECT * FROM jobs WHERE status='pending' AND next<=?"
        if deleting_only:
            query += " AND kind='delete'"
        else:
            available = feeds if feeds is not None else [source_feed]
            placeholders = ','.join('?' for _ in available) or "''"
            query += f" AND (kind='delete' OR (kind='discover' AND item IN ({placeholders})) OR (kind='fetch' AND item IN (SELECT id FROM items WHERE feed_id IN ({placeholders}))))"
        query += " ORDER BY CASE kind WHEN 'delete' THEN 0 WHEN 'discover' THEN 1 ELSE 2 END,id LIMIT 1"
        params = (now,) if deleting_only else (now, *('' if f == 'demo' else f for f in available), *available)
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

    def write_material(self, item: str, material: Material) -> None:
        stage = self.root / 'staging' / item
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        # Header is content too, and deliberately lives outside SQLite.
        with (stage / 'original.md').open('w', encoding='utf-8') as f:
            f.write(json.dumps({'title': material.title, 'full': material.full, 'reason': material.reason, 'members': list(material.members), 'attachments': [dict(name=a.name, kind=a.kind, mime=a.mime) for a in material.attachments]}, ensure_ascii=False) + '\n\n' + material.text)
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

    def viewed(self, item: str) -> bool:
        with self.db:
            row = self.db.execute('SELECT id FROM items WHERE id=?', (item,)).fetchone()
            if not row:
                return False
            self.db.execute('UPDATE items SET viewed=1,complete=0,published=NULL WHERE id=?', (item,))
            self.db.execute("UPDATE jobs SET status='cancelled',generation=generation+1,error=NULL WHERE item=? AND kind!='delete'", (item,))
            self.db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('delete',?)", (item,))
        self.fault('after_viewed_commit')
        return True

    def delete(self, job: Job) -> None:
        self.fault('before_delete')
        for path in (self.root / 'content' / job.item, self.root / 'staging' / job.item):
            if path.exists():
                shutil.rmtree(path)
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
            return dict(id=item, feed_id=row['feed_id'], url=row['url'], published=row['published'], title=json.loads(header)['title'], text=text, complete=True, loading=bool(self.db.execute("SELECT 1 FROM jobs WHERE kind='fetch' AND item=? AND status IN ('pending','running')", (item,)).fetchone()), full=json.loads(header).get('full', True) and (not json.loads(header).get('members') or set(json.loads(header)['members']) == set(json.loads(row['members']))), reason=json.loads(header).get('reason', ''), members=json.loads(row['members']), attachments=[dict(url=f"/api/materials/{item}/media/{a['name']}", kind=a['kind'], mime=a['mime']) for a in json.loads(header).get('attachments', [])], images=[f'/api/materials/{item}/images/{p.name}' for p in sorted(self.path(item).parent.glob('image-*'))])
        except (OSError, ValueError, KeyError):
            return None

    def cards(self) -> list[dict[str, Any]]:
        cards = []
        for row in self.db.execute('SELECT id FROM items WHERE viewed=0 ORDER BY published DESC').fetchall():
            material = self.material(row['id'])
            if material:
                del material['text']
                cards.append(material)
        return cards

    def status(self) -> dict[str, Any]:
        rows = self.db.execute('SELECT kind,status,error,count(*) AS count FROM jobs GROUP BY kind,status,error').fetchall()
        jobs = [dict(r) for r in rows]
        errors = sorted({r['error'] for r in rows if r['error']})
        pending_deletes = any(r['kind'] == 'delete' and r['status'] != 'done' for r in rows)
        return dict(jobs=jobs, errors=errors, deleting=pending_deletes)
