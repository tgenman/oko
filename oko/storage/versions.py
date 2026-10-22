from oko.collection.external_media import saved_external, expected_incomplete
from oko.collection.telegram.reading import saved_telegraph
"""Immutable content versions; SQLite selects only a durably published directory."""
from oko.storage import material_read
from oko.storage.reading import reading_value
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING, Any

from oko.collection.contracts import Material, SourceError, StagedPayload, DownloadStage, SourceManifest

if TYPE_CHECKING:
    from oko.storage.store import Store, Job


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

    @reading_value
    def number(self, item: str) -> int:
        snapshot=material_read.current(self.store.db)
        if snapshot and item in snapshot.items:
            return int(snapshot.items[item]['current_number'] or 0)
        row=self.store.db.execute('SELECT number FROM content_current WHERE item=?',(item,)).fetchone()
        return int(row[0]) if row else 0

    def path(self, item: str, number: int | None = None) -> Path:
        return self.store.root / 'content' / item / str(number if number is not None else self.number(item) or 1) / 'original.md'

    @staticmethod
    def file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as source:
            while part := source.read(1024*1024):
                digest.update(part)
        return digest.hexdigest()

    @staticmethod
    def fingerprint(directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(directory.iterdir()):
            if path.name != 'original.md' and not path.name.startswith(('image-', 'media-')):
                continue
            digest.update(path.name.encode() + b'\0')
            if path.name == 'original.md':
                raw,text = path.read_text().split('\n\n',1)
                header = json.loads(raw)
                if 'source_manifest' in header:
                    # Attempts and local completeness are metadata, not new
                    # processing inputs when available text/bytes are unchanged.
                    header.pop('full',None)
                    header.pop('reason',None)
                    manifest = header['source_manifest']
                    manifest.pop('source_url',None)
                    manifest.pop('download_partial',None)
                    for resource in manifest.get('resources',[]):
                        resource.pop('source_url',None)
                        resource.pop('reason',None)
                        resource.pop('status',None)
                    for article in manifest.get('articles',[]):
                        article.pop('reason',None)
                    digest.update(hashlib.sha256((json.dumps(header,sort_keys=True)+'\n\n'+text).encode()).digest())
                    continue
            digest.update(bytes.fromhex(Versions.file_digest(path)))
        return digest.hexdigest()

    def _register(self, item: str, number: int, fingerprint: str, visible: bool) -> None:
        self.store.db.execute('INSERT INTO content_versions VALUES (?,?,?,?) ON CONFLICT(item,number) DO UPDATE SET fingerprint=excluded.fingerprint,visible=MAX(visible,excluded.visible)', (item, number, fingerprint, int(visible)))
        self.store.db.execute('INSERT INTO content_current VALUES (?,?) ON CONFLICT(item) DO UPDATE SET number=excluded.number', (item, number))

    @staticmethod
    def payload_fingerprint(directory: Path) -> str:
        raw, text = (directory / 'original.md').read_text().split('\n\n', 1)
        header = json.loads(raw)
        for key in ('full', 'reason', 'captions'):
            header.pop(key, None)
        digest = hashlib.sha256((json.dumps(header, sort_keys=True) + '\n\n' + text).encode())
        for path in sorted(directory.iterdir()):
            if path.name.startswith(('image-', 'media-')):
                digest.update(path.name.encode() + b'\0' + bytes.fromhex(Versions.file_digest(path)))
        return digest.hexdigest()

    @staticmethod
    def source_progress(previous: Path, current: Path) -> bool | None:
        old_raw,old_text = (previous/'original.md').read_text().split('\n\n',1)
        new_raw,new_text = (current/'original.md').read_text().split('\n\n',1)
        old_header,new_header = json.loads(old_raw),json.loads(new_raw)
        if 'source_manifest' not in new_header:
            return None
        new = SourceManifest.model_validate_json(json.dumps(new_header['source_manifest']))
        if 'source_manifest' not in old_header:
            # First reliable manifest is a baseline only when previously known
            # text and common local bytes remain unchanged.
            if old_text not in (new.post.get('text'),new.post.get('baseline_text')):
                return False
            common = {p.name for p in previous.iterdir() if p.name.startswith(('media-','image-'))} & {p.name for p in current.iterdir()}
            return all(Versions.file_digest(previous/name)==Versions.file_digest(current/name) for name in common)
        old = SourceManifest.model_validate_json(json.dumps(old_header['source_manifest']))
        if (old_text == new_text and old.resources and not new.resources
                and not old.articles and not new.articles
                and all(r.status == 'unsupported' and r.kind == 'file' and r.sha256 is None and r.external is None
                        and r.reason == 'Пост не поддерживается публичным предпросмотром'
                        and r.key.startswith('telegram-web:') and r.key.endswith(':unsupported') for r in old.resources)
                and {k:v for k,v in old.post.items() if k != 'resources'} == {k:v for k,v in new.post.items() if k != 'resources'}):
            # Removing an inferred fallback attachment corrects our knowledge;
            # no source text or previously available media has changed.
            return True
        if new.post_web_unavailable and not old_text and not new_text and not new.resources and all(r.status=='unsupported' and r.kind=='file' for r in old.resources):
            # Replacing an old unknown placeholder with an explicit access
            # restriction changes our knowledge, not the publication's content.
            if {k:v for k,v in old.post.items() if k!='resources'}=={k:v for k,v in new.post.items() if k!='resources'}:
                return True
        if old.post!=new.post:
            return False
        resources = {resource.key:resource for resource in old.resources}
        for resource in new.resources:
            known = resources.get(resource.key)
            if known and known.sha256 and resource.sha256 and known.sha256!=resource.sha256:
                return False
        articles = {article.url:article for article in old.articles}
        for article in new.articles:
            known_article = articles.get(article.url)
            if known_article and known_article.text is not None and article.text is not None:
                if (known_article.title,known_article.text,known_article.resource_keys)!=(article.title,article.text,article.resource_keys):
                    return False
        return True

    def prepare(self, item: str, material: Material, job: 'Job | None' = None, staging: DownloadStage | None = None) -> tuple[int, str, bool]:
        stage = self.store.root / 'staging' / item
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        header = dict(title=material.title, full=material.full, reason=material.reason, members=list(material.members), captions=material.captions,
                      attachments=[dict(name=a.name, kind=a.kind, mime=a.mime) for a in material.attachments])
        if material.reply_preview is not None:
            header['reply_preview'] = material.reply_preview.model_dump(mode='json')
        if material.description:
            header['description'] = material.description
        if material.source_manifest:
            header['source_manifest'] = material.source_manifest.model_dump(mode='json')
        payload = json.dumps(header, ensure_ascii=False) + '\n\n' + material.text
        self._write(stage / 'original.md', payload.encode())
        for image in material.images:
            if not re.fullmatch(r'image-[0-9]+\.(png|jpg|gif|webp)', image.name):
                raise ValueError('image name')
            self._write(stage / image.name, image.data)
        for attachment in material.attachments:
            if not re.fullmatch(r'media-[0-9]+\.[a-z0-9]+', attachment.name):
                raise ValueError('attachment name')
            if isinstance(attachment.data, StagedPayload):
                self._write_staged(stage / attachment.name, attachment.data, job, staging)
            else:
                self._write(stage / attachment.name, attachment.data)
        fingerprint = self.fingerprint(stage)
        number = self.number(item)
        if number and self.path(item).is_file() and self.fingerprint(self.path(item).parent) == fingerprint and (stage/'original.md').read_bytes()==self.path(item).read_bytes():
            shutil.rmtree(stage)
            return number, fingerprint, False
        progress_only = False
        if number and self.path(item).is_file():
            source_progress = self.source_progress(self.path(item).parent,stage)
            progress_only = source_progress if source_progress is not None else self.payload_fingerprint(stage)==self.payload_fingerprint(self.path(item).parent)
        if number and self.version(item) == fingerprint:
            # Operational metadata can need a new immutable directory while the
            # processing input stays identical. Carry only committed results;
            # files moved by an interrupted attempt are not accepted results.
            for kind in ('filter', 'theses'):
                result = self.path(item).parent / (kind + '.md')
                done = self.store.db.execute("SELECT 1 FROM jobs WHERE item=? AND kind=? AND status='done'", (item,kind)).fetchone()
                if done and result.is_file():
                    self._write(stage / result.name, result.read_bytes())
        accepted_full = bool(progress_only and json.loads(self.path(item).read_text().split('\n\n', 1)[0]).get('full'))
        if accepted_full and not material.full and not (expected_incomplete(material.source_manifest) and not material.collection_error):
            shutil.rmtree(stage)
            raise SourceError(material.reason or 'Повторная загрузка материала не завершена')
        # Include orphan directories from an interrupted write; never overwrite
        # the published version or promote that orphan as a completed result.
        parent = self.store.root / 'content' / item
        parent.mkdir(exist_ok=True)
        number = max([0] + [int(p.name) for p in parent.iterdir() if p.name.isdecimal()]) + 1
        destination = parent / str(number)
        os.replace(stage, destination)
        for directory in (destination, parent, parent.parent, self.store.root / 'staging'):
            self._fsync(directory)
        return number, fingerprint, progress_only

    def _write_staged(self, target: Path, payload: StagedPayload, job: 'Job | None', staging: DownloadStage | None) -> None:
        allowed = self.store.root/'staging'/'_downloads'
        if not job or not staging or (staging.job,staging.generation)!=(job.id,job.generation) or staging.path.parent.resolve()!=allowed.resolve() or not self.store.valid(job):
            raise ValueError('unowned download staging')
        if payload.path.is_symlink() or payload.path.resolve().parent!=staging.path.resolve() or not payload.path.is_file():
            raise ValueError('unowned staged file')
        if not 0<payload.size<=200*1024*1024 or payload.path.stat().st_size!=payload.size:
            raise ValueError('staged file size')
        digest,size = hashlib.sha256(),0
        with payload.path.open('rb') as source, target.open('wb') as output:
            while part := source.read(1024*1024):
                size += len(part)
                if size>200*1024*1024:
                    raise ValueError('staged file too large')
                digest.update(part)
                output.write(part)
            output.flush()
            os.fsync(output.fileno())
        if size!=payload.size or digest.hexdigest()!=payload.sha256:
            raise ValueError('staged file changed')

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

    def publish(self, item: str, prepared: tuple[int, str, bool], visible: bool) -> None:
        # Called inside Store's discovery/save transaction.
        number, fingerprint, progress_only = prepared
        if progress_only:
            self.store.db.execute('UPDATE content_versions SET visible=0 WHERE item=? AND number=?', (item, self.number(item)))
        self._register(item, number, fingerprint, visible)
        self.store.db.execute('DELETE FROM source_deleted WHERE item=?', (item,))

    @reading_value
    def version(self, item: str) -> str:
        snapshot=material_read.current(self.store.db)
        if snapshot and item in snapshot.items and snapshot.items[item]['current_fingerprint']:
            return str(snapshot.items[item]['current_fingerprint'])
        row=self.store.db.execute('SELECT fingerprint FROM content_versions WHERE item=? AND number=?',(item,self.number(item))).fetchone()
        return str(row[0]) if row else self.fingerprint(self.path(item).parent)

    @reading_value
    def previous(self, item: str) -> list[dict[str, Any]]:
        current_number=self.number(item)
        snapshot=material_read.current(self.store.db)
        rows = ([r for r in snapshot.versions.get(item,[]) if r['visible'] and r['number'] != current_number]
                if snapshot and item in snapshot.items else self.store.db.execute(
                    'SELECT number,fingerprint FROM content_versions WHERE item=? AND visible=1 AND number!=? ORDER BY number DESC',
                    (item,current_number)).fetchall())
        return [dict(number=r['number'], version=r['fingerprint'], url=f"/api/v3/materials/{item}/versions/{r['number']}") for r in rows]

    def read(self, item: str, number: int) -> dict[str, Any] | None:
        if not self.store.db.execute('SELECT 1 FROM items WHERE id=? AND viewed=0', (item,)).fetchone():
            return None
        if not self.store.db.execute('SELECT 1 FROM content_versions WHERE item=? AND number=? AND visible=1', (item, number)).fetchone():
            return None
        try:
            path = self.path(item, number)
            raw, text = path.read_text().split('\n\n', 1)
            header = json.loads(raw)
            base = f'/api/v3/materials/{item}/versions/{number}'
            return dict(**saved_external(header, base), linked_content=saved_telegraph(header, base), title=header['title'], text=text, full=header['full'], reason=header['reason'], reply_preview=header.get('reply_preview'),
                        images=[base + '/images/' + p.name for p in sorted(path.parent.glob('image-*'))],
                        attachments=[dict(url=base+'/media/'+a['name'], kind=a['kind'], mime=a['mime']) for a in header['attachments']])
        except (OSError, ValueError, KeyError):
            return None

    @reading_value
    def deleted(self, item: str) -> bool:
        snapshot=material_read.current(self.store.db)
        if snapshot and item in snapshot.items:
            return bool(snapshot.items[item]['source_deleted'])
        return bool(self.store.db.execute('SELECT 1 FROM source_deleted WHERE item=?',(item,)).fetchone())

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
