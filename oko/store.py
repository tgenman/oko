import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from collections.abc import Callable
from typing import Any

from .sources import Material, Publication


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
          id TEXT PRIMARY KEY, source_id TEXT UNIQUE, url TEXT UNIQUE,
          published REAL, viewed INTEGER DEFAULT 0, complete INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, kind TEXT, item TEXT DEFAULT '',
          status TEXT DEFAULT 'pending', attempt INTEGER DEFAULT 0,
          next REAL DEFAULT 0, generation INTEGER DEFAULT 0, error TEXT,
          UNIQUE(kind,item));
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        ''')
        for name in ('content', 'staging'):
            (root / name).mkdir(exist_ok=True)
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',generation=generation+1 WHERE status='running'")
            self.db.execute("UPDATE jobs SET next=0 WHERE status='pending'")
        self.recover_files()

    def recover_files(self) -> None:
        # Never promote orphaned output: the pending fetch must produce a fresh result.
        for p in (self.root / 'staging').iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        for p in (self.root / 'content').iterdir():
            row = self.db.execute('SELECT * FROM items WHERE id=?', (p.name,)).fetchone()
            if row is None or not row['complete']:
                shutil.rmtree(p)
        with self.db:
            for row in self.db.execute('SELECT * FROM items WHERE complete=1 AND viewed=0').fetchall():
                if not self.path(row['id']).is_file():
                    self.db.execute('UPDATE items SET complete=0 WHERE id=?', (row['id'],))
                    self.db.execute("UPDATE jobs SET status='pending' WHERE kind='fetch' AND item=?", (row['id'],))

    def path(self, item: str) -> Path:
        return self.root / 'content' / item / '1' / 'original.md'

    def enqueue_discovery(self) -> None:
        with self.db:
            self.db.execute("INSERT INTO jobs(kind) VALUES ('discover') ON CONFLICT(kind,item) DO UPDATE SET status='pending',next=0,error=NULL WHERE jobs.status='done'")

    def cursor(self) -> str | None:
        row = self.db.execute("SELECT value FROM state WHERE key='cursor'").fetchone()
        return row[0] if row else None

    def discovered(self, publications: list[Publication], cursor: str, job: dict[str, Any]) -> None:
        with self.db:
            for p in publications:
                item = hashlib.sha256(('demo:' + p.id).encode()).hexdigest()[:32]
                self.db.execute('INSERT OR IGNORE INTO items(id,source_id,url,published) VALUES (?,?,?,?)',
                                (item, p.id, p.url, p.published))
                row = self.db.execute('SELECT * FROM items WHERE source_id=?', (p.id,)).fetchone()
                if row and not row['viewed']:
                    self.db.execute("INSERT OR IGNORE INTO jobs(kind,item) VALUES ('fetch',?)", (row['id'],))
            self.fault('before_discovery_commit')
            self.db.execute("INSERT INTO state VALUES ('cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (cursor,))
            self.finish(job)

    def next_job(self, now: float, deleting_only: bool = False) -> dict[str, Any] | None:
        query = "SELECT * FROM jobs WHERE status='pending' AND next<=?"
        if deleting_only:
            query += " AND kind='delete'"
        query += " ORDER BY CASE kind WHEN 'delete' THEN 0 WHEN 'discover' THEN 1 ELSE 2 END,id LIMIT 1"
        row = self.db.execute(query, (now,)).fetchone()
        if not row:
            return None
        with self.db:
            self.db.execute("UPDATE jobs SET status='running',attempt=attempt+1,generation=generation+1 WHERE id=?", (row['id'],))
        return dict(self.db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())

    def finish(self, job: dict[str, Any]) -> None:
        self.db.execute("UPDATE jobs SET status='done',error=NULL WHERE id=? AND generation=? AND status='running'", (job['id'], job['generation']))

    def valid(self, job: dict[str, Any]) -> bool:
        row = self.db.execute('SELECT status,generation FROM jobs WHERE id=?', (job['id'],)).fetchone()
        return bool(row and row['status'] == 'running' and row['generation'] == job['generation'])

    def publication(self, item: str) -> Publication | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        return Publication(row['source_id'], row['url'], row['published']) if row else None

    def save(self, job: dict[str, Any], material: Material) -> None:
        item = job['item']
        if not self.valid(job) or self.publication(item) is None:
            return
        stage = self.root / 'staging' / item
        stage.mkdir(exist_ok=True)
        # Header is content too, and deliberately lives outside SQLite.
        with (stage / 'original.md').open('w', encoding='utf-8') as f:
            f.write(json.dumps({'title': material.title}, ensure_ascii=False) + '\n\n' + material.text)
            f.flush()
            os.fsync(f.fileno())
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
        self.fault('after_file_move')
        with self.db:
            self.db.execute('UPDATE items SET complete=1 WHERE id=? AND viewed=0', (item,))
            self.finish(job)

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

    def delete(self, job: dict[str, Any]) -> None:
        self.fault('before_delete')
        for path in (self.root / 'content' / job['item'], self.root / 'staging' / job['item']):
            if path.exists():
                shutil.rmtree(path)
        with self.db:
            self.finish(job)

    def retry(self, job: dict[str, Any], now: float, error: str) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',next=?,error=? WHERE id=? AND generation=? AND status='running'",
                            (now + 1, error, job['id'], job['generation']))

    def interrupted(self) -> None:
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',generation=generation+1 WHERE status='running'")

    def material(self, item: str) -> dict[str, Any] | None:
        row = self.db.execute('SELECT * FROM items WHERE id=? AND viewed=0', (item,)).fetchone()
        if not row:
            return None
        if not row['complete']:
            return dict(id=item, url=row['url'], published=row['published'], title='Материал загружается', text='', complete=False)
        try:
            header, text = self.path(item).read_text().split('\n\n', 1)
            return dict(id=item, url=row['url'], published=row['published'], title=json.loads(header)['title'], text=text, complete=True)
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
