"""Immutable content versions; SQLite selects only a durably published directory."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING, Any

from oko.collection.contracts import Material

if TYPE_CHECKING:
    from oko.storage.store import Store


class Versions:
    def __init__(self, store: 'Store') -> None:
        self.store = store
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS content_versions (
          item TEXT, number INTEGER, fingerprint TEXT, visible INTEGER,
          PRIMARY KEY(item,number));
        CREATE TABLE IF NOT EXISTS content_current (item TEXT PRIMARY KEY, number INTEGER);
        CREATE TABLE IF NOT EXISTS source_deleted (item TEXT PRIMARY KEY);
        ''')
        # Existing v1 files remain at their original path. No content in SQLite.
        with store.db:
            for row in store.db.execute('SELECT id FROM items WHERE complete=1 AND viewed=0').fetchall():
                path = store.root / 'content' / row['id'] / '1'
                if (path / 'original.md').is_file() and not self.number(row['id']):
                    self._register(row['id'], 1, self.fingerprint(path), True)

    def number(self, item: str) -> int:
        row = self.store.db.execute('SELECT number FROM content_current WHERE item=?', (item,)).fetchone()
        return int(row[0]) if row else 0

    def path(self, item: str, number: int | None = None) -> Path:
        return self.store.root / 'content' / item / str(number if number is not None else self.number(item) or 1) / 'original.md'

    @staticmethod
    def fingerprint(directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(directory.iterdir()):
            if path.name != 'original.md' and not path.name.startswith(('image-', 'media-')):
                continue
            digest.update(path.name.encode() + b'\0')
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def _register(self, item: str, number: int, fingerprint: str, visible: bool) -> None:
        self.store.db.execute('INSERT INTO content_versions VALUES (?,?,?,?) ON CONFLICT(item,number) DO UPDATE SET fingerprint=excluded.fingerprint,visible=MAX(visible,excluded.visible)', (item, number, fingerprint, int(visible)))
        self.store.db.execute('INSERT INTO content_current VALUES (?,?) ON CONFLICT(item) DO UPDATE SET number=excluded.number', (item, number))

    def prepare(self, item: str, material: Material) -> tuple[int, str]:
        stage = self.store.root / 'staging' / item
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        header = dict(title=material.title, full=material.full, reason=material.reason, members=list(material.members), captions=material.captions,
                      attachments=[dict(name=a.name, kind=a.kind, mime=a.mime) for a in material.attachments])
        payload = json.dumps(header, ensure_ascii=False) + '\n\n' + material.text
        self._write(stage / 'original.md', payload.encode())
        for image in material.images:
            if not re.fullmatch(r'image-[0-9]+\.(png|jpg|gif|webp)', image.name):
                raise ValueError('image name')
            self._write(stage / image.name, image.data)
        for attachment in material.attachments:
            if not re.fullmatch(r'media-[0-9]+\.[a-z0-9]+', attachment.name):
                raise ValueError('attachment name')
            self._write(stage / attachment.name, attachment.data)
        fingerprint = self.fingerprint(stage)
        number = self.number(item)
        if number and self.path(item).is_file() and self.fingerprint(self.path(item).parent) == fingerprint:
            shutil.rmtree(stage)
            return number, fingerprint
        # Include orphan directories from an interrupted write; never overwrite
        # the published version or promote that orphan as a completed result.
        parent = self.store.root / 'content' / item
        parent.mkdir(exist_ok=True)
        number = max([0] + [int(p.name) for p in parent.iterdir() if p.name.isdecimal()]) + 1
        destination = parent / str(number)
        os.replace(stage, destination)
        for directory in (destination, parent, parent.parent, self.store.root / 'staging'):
            self._fsync(directory)
        return number, fingerprint

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        with path.open('wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def _fsync(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def publish(self, item: str, prepared: tuple[int, str], visible: bool) -> None:
        # Called inside Store's discovery/save transaction.
        self._register(item, *prepared, visible)
        self.store.db.execute('DELETE FROM source_deleted WHERE item=?', (item,))

    def version(self, item: str) -> str:
        row = self.store.db.execute('SELECT fingerprint FROM content_versions WHERE item=? AND number=?', (item, self.number(item))).fetchone()
        return str(row[0]) if row else self.fingerprint(self.path(item).parent)

    def previous(self, item: str) -> list[dict[str, Any]]:
        rows = self.store.db.execute('SELECT number,fingerprint FROM content_versions WHERE item=? AND visible=1 AND number!=? ORDER BY number DESC', (item, self.number(item))).fetchall()
        return [dict(number=r['number'], version=r['fingerprint'], url=f"/api/materials/{item}/versions/{r['number']}") for r in rows]

    def read(self, item: str, number: int) -> dict[str, Any] | None:
        if not self.store.db.execute('SELECT 1 FROM items WHERE id=? AND viewed=0', (item,)).fetchone():
            return None
        if not self.store.db.execute('SELECT 1 FROM content_versions WHERE item=? AND number=? AND visible=1', (item, number)).fetchone():
            return None
        try:
            path = self.path(item, number)
            raw, text = path.read_text().split('\n\n', 1)
            header = json.loads(raw)
            base = f'/api/materials/{item}/versions/{number}'
            return dict(title=header['title'], text=text, full=header['full'], reason=header['reason'],
                        images=[base + '/images/' + p.name for p in sorted(path.parent.glob('image-*'))],
                        attachments=[dict(url=base+'/media/'+a['name'], kind=a['kind'], mime=a['mime']) for a in header['attachments']])
        except (OSError, ValueError, KeyError):
            return None

    def deleted(self, item: str) -> bool:
        return bool(self.store.db.execute('SELECT 1 FROM source_deleted WHERE item=?', (item,)).fetchone())

    def forget(self, item: str) -> None:
        for table in ('content_versions', 'content_current', 'source_deleted'):
            self.store.db.execute(f'DELETE FROM {table} WHERE item=?', (item,))

    def recover(self) -> None:
        for parent in (self.store.root / 'content').iterdir():
            row = self.store.db.execute('SELECT viewed FROM items WHERE id=?', (parent.name,)).fetchone()
            if not row or row['viewed']:
                continue
            known = {str(r[0]) for r in self.store.db.execute('SELECT number FROM content_versions WHERE item=?', (parent.name,))}
            for directory in parent.iterdir():
                if directory.is_dir() and directory.name not in known:
                    shutil.rmtree(directory)
